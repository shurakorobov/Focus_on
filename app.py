"""FastAPI сервер: роздає Mini App + REST API для сесій фокусу.

Запуск:
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncio
import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import storage
from config import settings
from telegram_auth import authenticate, verify_login_widget
from jwt_auth import create_token, verify_token

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
LANDING_FILE = BASE_DIR / "landing.html"
PRIVACY_FILE = BASE_DIR / "privacy.html"
ROBOTS_FILE = BASE_DIR / "robots.txt"
SITEMAP_FILE = BASE_DIR / "sitemap.xml"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Життєвий цикл: запускаємо фонові задачі при старті."""
    import asyncio
    import logging
    from keepalive import keepalive_loop

    logging.basicConfig(level=logging.INFO)
    logging.info("🚀 Focus ON стартує... PORT=%s WEBAPP_URL=%s",
                 os.getenv("PORT"), os.getenv("WEBAPP_URL", "(не задано)"))
    task = asyncio.create_task(keepalive_loop())
    # Реєструємо webhook для прийому Telegram updates (платежі Stars, /start тощо)
    await _setup_webhook()
    # Реєструємо callback для SSE real-time сповіщень
    db.set_change_callback(_notify_admin_subscribers)
    yield
    task.cancel()


async def _setup_webhook() -> None:
    """Встановлює Telegram webhook на /api/telegram/webhook.
    Потрібно для Telegram Stars (pre_checkout_query + successful_payment)."""
    if not settings.has_token or not settings.WEBAPP_URL:
        return
    webhook_url = settings.WEBAPP_URL.rstrip("/") + "/api/telegram/webhook"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/setWebhook",
                json={"url": webhook_url, "allowed_updates": [
                    "message", "pre_checkout_query"]},
            )
            data = r.json()
            if data.get("ok"):
                import logging
                logging.getLogger("webhook").info("Webhook встановлено: %s", webhook_url)
            else:
                import logging
                logging.getLogger("webhook").warning("setWebhook помилка: %s", data)
    except Exception as e:
        import logging
        logging.getLogger("webhook").warning("setWebhook виключення: %s", e)


app = FastAPI(title="Focus ON API", lifespan=lifespan)

# Ініціалізуємо БД при старті
db.init_db()


@app.middleware("http")
async def disable_caching(request: Request, call_next):
    """Вимикає кешування для Mini App, лендінгу та статичних файлів.
    Telegram WebView і браузери інакше можуть тримати стару версію JS/CSS
    навіть після нового деплою."""
    response = await call_next(request)
    path = request.url.path
    if path in {"/", "/landing", "/privacy"} or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# Роздаємо статичні ассети (js/css/img)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------- SSE pub/sub для real-time адмінки ----------------------
_admin_subscribers: set = set()

# In-memory трекер онлайн-користувачів: {tg_id: last_seen_monotonic}
# Не ліземо в БД на кожен heartbeat (Neon scale-to-zero засинає).
# TTL має бути > кількох інтервалів heartbeat (8с), щоб випадковий джиттер
# не «дропав» активного юзера. 25с ≈ 3× heartbeat — надійно ловить вихід.
_ONLINE_TTL = 25  # секунд: користувач вважається онлайн якщо heartbeat < 25с тому
_online_users: dict = {}  # tg_id (int) -> time.time() (float)


def _notify_admin_subscribers():
    """Сповіщає всіх SSE-підписаних адмінів про зміну даних."""
    for queue in list(_admin_subscribers):
        try:
            queue.put_nowait(True)
        except asyncio.QueueFull:
            pass


def _touch_online(tg_id: int) -> None:
    """Оновлює timestamp онлайн-користувача (in-memory)."""
    _online_users[int(tg_id)] = time.time()


def _prune_online() -> None:
    """Видаляє «застарілі» записи (користувачі, що не давали про себе знати > TTL)."""
    cutoff = time.time() - _ONLINE_TTL
    stale = [uid for uid, ts in _online_users.items() if ts < cutoff]
    for uid in stale:
        _online_users.pop(uid, None)


# Кеш імен/планів онлайн-користувачів. Імена змінюються рідко, тож запит до БД
# робимо лише коли з'являється новий tg_id, якого ще немає в кеші.
_online_name_cache: dict = {}  # tg_id -> {name, username, plan}


def online_count() -> int:
    """Кількість користувачів онлайн прямо зараз."""
    _prune_online()
    return len(_online_users)


def online_users() -> list[dict]:
    """Список онлайн-користувачів з іменами (для дашборду).
    Імена кешуються — БД-запит лише для нових tg_id."""
    _prune_online()
    ids = list(_online_users.keys())
    if not ids:
        return []
    # яких tg_id ще немає в кеші — тягнемо з БД
    missing = [uid for uid in ids if uid not in _online_name_cache]
    if missing:
        placeholders = ",".join(["?"] * len(missing))  # _translate_sql → %s під PG
        try:
            with db.get_conn() as conn:
                rows = conn.execute(
                    f"SELECT tg_id, first_name, username, plan FROM users WHERE tg_id IN ({placeholders})",
                    tuple(missing),
                ).fetchall()
            for r in rows:
                _online_name_cache[int(r["tg_id"])] = {
                    "tg_id": int(r["tg_id"]),
                    "name": r["first_name"] or r["username"] or ("ID:" + str(r["tg_id"])),
                    "username": r["username"],
                    "plan": r["plan"],
                }
        except Exception:
            pass
    return [_online_name_cache[uid] for uid in ids if uid in _online_name_cache]


# ---------------------- Глобальний обробник помилок -------------------------
import logging
import traceback as _tb

_err_logger = logging.getLogger("focus.errors")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Логує будь-яку непійману помилку з повним traceback → діагностика 500."""
    tb = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
    path = request.url.path
    _err_logger.error("UNHANDLED on %s: %s\n%s", path, exc, tb)
    # також шлємо адміну коротке повідомлення (не спамити — лише 5xx)
    try:
        await _notify_admin(f"🔥 <b>Помилка 500</b> на <code>{path}</code>\n<pre>{str(exc)[:500]}</pre>")
    except Exception:
        pass
    return JSONResponse(
        {"detail": str(exc), "type": type(exc).__name__},
        status_code=500,
    )


# ------------------------------ авторизація ---------------------------------


def _log_init_data_failure(init_data: str, request: Request) -> None:
    """Логує причину невдалої перевірки initData (для діагностики вебапп)."""
    try:
        import logging
        log = logging.getLogger("focuson")
        ua = request.headers.get("user-agent", "")[:80]
        # розбір
        parsed = {}
        try:
            parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        except Exception:
            pass
        has_hash = "hash" in parsed
        has_user = "user" in parsed
        auth_date = parsed.get("auth_date", "")
        log.warning(
            "initData invalid: len=%d has_hash=%s has_user=%s auth_date=%s ua=%s",
            len(init_data), has_hash, has_user, auth_date, ua,
        )
    except Exception:
        pass


def current_user(request: Request) -> dict:
    """DI-залежність: автентифікує запит.

    Підтримує два механізми:
      1. Telegram WebApp initData (Mini App) — заголовок Bearer <initData>,
         query ?init_data= або поле init_data у тілі.
      2. JWT-токен (Android-клієнт) — заголовок Bearer <jwt> після входу
         через /api/auth/telegram-login або /api/auth/google (Google Sign-In).
    """
    auth = request.headers.get("authorization", "")
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()

    user = None
    # спершу пробуємо JWT (формат: три частини через крапку)
    if token and token.count(".") == 2:
        user = verify_token(token)
        if not user:
            raise HTTPException(status_code=401, detail="invalid jwt")
    else:
        # інакше — Telegram initData
        init_data = token
        if not init_data and "init_data" in request.query_params:
            init_data = request.query_params["init_data"]
        if not init_data:
            try:
                body = request.scope.get("_body_json") or {}
            except Exception:
                body = {}
            init_data = body.get("init_data", "") if isinstance(body, dict) else ""
        if init_data:
            user = authenticate(init_data)
            if not user:
                # Невірний initData — логуємо причину для діагностики.
                _log_init_data_failure(init_data, request)
                raise HTTPException(status_code=401, detail="invalid telegram auth")
        else:
            raise HTTPException(status_code=401, detail="invalid telegram auth")

    # Доповнюємо профільними даними з БД (email, avatar_url — для Google-юзерів),
    # щоб перевірки admin/premium мали повний контекст.
    row = db.get_user(user.get("id") or user.get("tg_id") or 0)
    if row:
        user["email"] = row.get("email", "") or ""
        user["avatar_url"] = row.get("avatar_url", "") or ""
        user["google_id"] = row.get("google_id", "") or ""
    else:
        user.setdefault("email", "")
        user.setdefault("avatar_url", "")
        user.setdefault("google_id", "")
    return user


def _authenticate_token(token: str) -> dict | None:
    """Універсальна авторизація для endpoint-ів без Depends (напр. SSE):
    приймає і JWT (з крапками), і initData. Повертає user-словник або None."""
    if not token:
        return None
    if token.count(".") == 2:
        u = verify_token(token)
    else:
        u = authenticate(token)
    if not u:
        return None
    row = db.get_user(u.get("id") or u.get("tg_id") or 0)
    if row:
        u["email"] = row.get("email", "") or ""
        u["avatar_url"] = row.get("avatar_url", "") or ""
    return u


def _ensure_user_exists(user: dict) -> None:
    db.upsert_user(
        tg_id=user["id"],
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
        username=user.get("username", ""),
        photo_url=user.get("photo_url", ""),
    )


def _client_ip(request: Request) -> str:
    """Реальний IP клієнта (Render шле X-Forwarded-For)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _with_query_param(url: str, key: str, value: str) -> str:
    """Безпечно додає query-параметр, не стираючи наявні параметри URL."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[key] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


async def _notify_admin(message: str) -> None:
    """Шле повідомлення адміну через Bot API (якщо задано токен і chat_id)."""
    if not settings.BOT_TOKEN or not settings.ADMIN_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": settings.ADMIN_CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
    except Exception:
        pass


# ------------------------------ схеми ---------------------------------------


class StartSession(BaseModel):
    mode: str = Field(..., description="deep_work | focus | short | break")
    duration: int = Field(..., gt=0, le=4 * 3600, description="тривалість у секундах")


class FinishSession(BaseModel):
    mode: str
    planned: int = Field(..., gt=0)
    actual: int = Field(..., ge=0)
    completed: bool = False
    started_at: str
    category: str = Field("other", pattern="^(deep_work|creative|learning|reading|training|other)$")


class AddTrackByURL(BaseModel):
    url: str = Field(..., min_length=3)
    title: str = ""
    author: str = ""
    scope: str = Field("user", pattern="^(demo|admin|user)$")
    category: str = Field("other", pattern="^(deep_work|creative|learning|reading|training|other)$")


class RenameTrack(BaseModel):
    title: str = Field(..., min_length=0, max_length=200)


class TrackKeyReq(BaseModel):
    track_key: str = Field(..., min_length=1)


class BugReport(BaseModel):
    message: str = Field(..., max_length=4000)
    platform: str = ""
    screen: str = ""


class GrantPremium(BaseModel):
    tg_id: int
    days: int = Field(settings.PREMIUM_DURATION_DAYS, gt=0, le=3650)


class RedeemCode(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)


class PlayVerify(BaseModel):
    """Google Play purchase verification (Android BillingClient)."""
    purchase_token: str = Field(..., min_length=1, max_length=2048)
    product_id: str = Field(settings.GOOGLE_PLAY_PREMIUM_SKU, max_length=128)
    order_id: str = Field("", max_length=512)


class PromoCodeReq(BaseModel):
    code: str = Field(..., min_length=3, max_length=50)
    days: int = Field(30, gt=0, le=3650)
    max_uses: int = Field(0, ge=0)


class AttributionClickReq(BaseModel):
    source: str = Field("direct", max_length=80)
    medium: str = Field("none", max_length=80)
    campaign: str = Field("landing", max_length=160)
    content: str = Field("", max_length=160)
    term: str = Field("", max_length=160)
    gclid: str = Field("", max_length=255)
    landing_path: str = Field("/landing", max_length=255)


class AttributionClaimReq(BaseModel):
    start_param: str = Field(..., min_length=1, max_length=64)


class ProductEventReq(BaseModel):
    event_name: str = Field(..., min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)


# ------------------------------ API -----------------------------------------


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok", "configured": settings.is_configured}


class TelegramLoginReq(BaseModel):
    """Тіло запиту /api/auth/telegram-login.
    auth_data — повний query-string від Telegram Login Widget (з хешем)."""
    auth_data: str


@app.post("/api/auth/telegram-login")
async def api_telegram_login(payload: TelegramLoginReq):
    """Обмін Telegram Login Widget payload на наш JWT.
    Android-клієнт викликає цей endpoint після входу через Telegram,
    отримує JWT і надалі шле його у заголовку Authorization: Bearer <jwt>."""
    user = verify_login_widget(payload.auth_data, settings.BOT_TOKEN)
    if not user:
        raise HTTPException(status_code=401, detail="invalid telegram login")
    # переконуємось що користувач існує в БД
    _ensure_user_exists(user)
    token = create_token(user)
    return {"ok": True, "token": token, "user": user}


class GoogleAuthReq(BaseModel):
    """ID-токен Google Sign-In від Android Credential Manager."""
    id_token: str = Field(..., min_length=10)


async def _verify_google_id_token(id_token: str) -> dict | None:
    """Верифікує Google ID token через tokeninfo endpoint (без нових залежностей).

    Перевіряє audience (aud) проти settings.GOOGLE_OAUTH_CLIENT_ID, щоб
    чужі токени (видані іншим клієнтам) не приймались.
    Повертає {sub, email, email_verified, name, picture} або None.
    """
    if not settings.GOOGLE_OAUTH_CLIENT_ID:
        logging.getLogger("google_auth").warning(
            "GOOGLE_OAUTH_CLIENT_ID не налаштований — Google вхід вимкнений"
        )
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
            )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None
    # audience має бути наш Web Client ID
    if data.get("aud") != settings.GOOGLE_OAUTH_CLIENT_ID:
        return None
    # email має бути верифікований
    if str(data.get("email_verified", "")).lower() != "true":
        return None
    if not data.get("sub") or not data.get("email"):
        return None
    return {
        "sub": data["sub"],
        "email": data["email"],
        "name": data.get("name", ""),
        "picture": data.get("picture", ""),
    }


@app.post("/api/auth/google")
async def api_google_login(payload: GoogleAuthReq):
    """Обмін Google ID token на наш JWT (Android Google Sign-In).

    Верифікує токен через Google tokeninfo, створює/оновлює Google-юзера
    в БД (синтетичний негативний tg_id), повертає JWT.
    """
    verified = await _verify_google_id_token(payload.id_token)
    if not verified:
        raise HTTPException(status_code=401, detail="invalid google token")
    user = db.upsert_user_google(
        google_id=verified["sub"],
        email=verified["email"],
        name=verified["name"],
        avatar_url=verified["picture"],
    )
    token = create_token(user)
    return {"ok": True, "token": token, "user": user}


class ExchangeCodeReq(BaseModel):
    """Одноразовий код входу від бота (bot-code auth)."""
    code: str = Field(..., min_length=4, max_length=10)


@app.post("/api/auth/exchange-code")
async def api_exchange_code(payload: ExchangeCodeReq):
    """Обмін одноразового коду (з бота) на JWT.

    Використовується Android-клієнтом коли Google Sign-In недоступний.
    Юзер шле /login боту @focuson_on_bot → отримує 6-значний код → вводить тут.
    Endpoint публічний (код сам є секретом, діє 5 хв, одноразовий).
    """
    user = db.consume_login_code(payload.code)
    if not user:
        raise HTTPException(status_code=401, detail="invalid or expired code")
    _ensure_user_exists(user)
    token = create_token(user)
    return {"ok": True, "token": token, "user": user}


@app.post("/api/attribution/click")
async def api_attribution_click(payload: AttributionClickReq):
    """Створює короткий токен для переходу landing → Telegram.

    Endpoint публічний: персональних даних не приймає, лише UTM/gclid.
    """
    token = db.create_attribution_click(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
    return {"ok": True, "start_param": token}


@app.post("/api/attribution/claim")
async def api_attribution_claim(
    payload: AttributionClaimReq, user: dict = Depends(current_user)
):
    """Прив'язує рекламну мітку до авторизованого Telegram-користувача."""
    _ensure_user_exists(user)
    return db.claim_attribution(user["id"], payload.start_param)


@app.post("/api/events")
async def api_product_event(
    payload: ProductEventReq, user: dict = Depends(current_user)
):
    """Власна продуктова аналітика паралельно з GA4."""
    _ensure_user_exists(user)
    event_id = db.record_product_event(user["id"], payload.event_name, payload.params)
    return {"ok": True, "id": event_id}


@app.get("/api/categories")
async def api_categories():
    """Перелік категорій діяльності."""
    return {"categories": db.CATEGORIES, "modes": db.FOCUS_MODES}


# Звуки, що доступні безкоштовно. Решта — premium (url віддається тільки premium-юзерам).
_FREE_SOUND_IDS = {"rain", "cafe", "fire", "ocean"}


@app.get("/api/sounds")
async def api_sounds(request: Request):
    """Перелік ambient-звуків з R2 (URL + метадані) + premium-gating.

    Без авторизації: віддаються всі звуки, але premium-звукам url="" (фронт покаже lock).
    Premium-юзери (JWT/initData) отримують повні URL для всіх 16 звуків.
    """
    base = settings.R2_PUBLIC_URL
    # id має збігатися з SOUND_CHANNELS у app.js
    all_sounds = [
        # Free (4)
        {"id": "rain", "emoji": "🌧", "name": "Дощ", "premium": False},
        {"id": "cafe", "emoji": "☕", "name": "Кафе", "premium": False},
        {"id": "fire", "emoji": "🔥", "name": "Вогнище", "premium": False},
        {"id": "ocean", "emoji": "🌊", "name": "Океан", "premium": False},
        # Premium (12): 4 існуючі + 8 нових
        {"id": "forest", "emoji": "🌲", "name": "Ліс", "premium": True},
        {"id": "wind", "emoji": "🌬", "name": "Вітер", "premium": True},
        {"id": "white", "emoji": "🤍", "name": "White noise", "premium": True},
        {"id": "brown", "emoji": "🟤", "name": "Brown noise", "premium": True},
        {"id": "clock", "emoji": "🕰️", "name": "Годинник", "premium": True},
        {"id": "soft-rain", "emoji": "🌦", "name": "Ніжний дощ", "premium": True},
        {"id": "thunder", "emoji": "⛈", "name": "Гроза", "premium": True},
        {"id": "stream", "emoji": "💧", "name": "Струмок", "premium": True},
        {"id": "night", "emoji": "🦗", "name": "Нічні звуки", "premium": True},
        {"id": "traffic", "emoji": "🚗", "name": "Місто", "premium": True},
        {"id": "piano", "emoji": "🎹", "name": "Піаніно", "premium": True},
        {"id": "bowl", "emoji": "🛕", "name": "Співаючі чаші", "premium": True},
    ]
    # Визначаємо premium-статус юзера (мяко, без помилок для анонімів)
    is_prem = False
    try:
        user = current_user(request)
        if user:
            is_prem = db.is_premium(user["id"])
    except Exception:
        pass

    sounds = []
    for s in all_sounds:
        # URL віддаємо: для free-звуків завжди; для premium — тільки premium-юзерам
        give_url = (not s["premium"]) or is_prem
        sounds.append({
            "id": s["id"],
            "emoji": s["emoji"],
            "name": s["name"],
            "premium": s["premium"],
            "url": f"{base}/{s['id']}.mp3" if give_url else "",
        })
    return {"sounds": sounds}


@app.get("/api/me")
async def api_me(request: Request, user: dict = Depends(current_user)):
    """Повертає профіль користувача + тариф + мережева інформація."""
    _ensure_user_exists(user)
    _touch_online(user["id"])  # користувач відкрив застосунок — він онлайн
    profile = db.get_user(user["id"]) or {}
    plan = db.get_user_plan(user["id"])
    recommendations = _recommendations(profile, plan, user)

    # мережева інформація користувача
    ip = _client_ip(request)
    geo = await _geo_info(ip)

    return {
        "user": user,
        "profile": profile,
        "plan": plan["plan"],
        "is_premium": plan["is_premium"],
        "plan_expires_at": plan["plan_expires_at"],
        "recommendations": recommendations,
        "is_admin": settings.is_admin(tg_id=user["id"], email=user.get("email","")),
        "premium_price_stars": settings.PREMIUM_PRICE_STARS,
        "version": settings.APP_VERSION,
        "network": geo,
        "attribution": db.get_user_attribution(user["id"]),
    }


async def _geo_info(ip: str) -> dict:
    """Мережева інформація: IP, країна, місто, провайдер."""
    if not ip:
        return {"ip": "", "country": "", "city": "", "region": "", "isp": ""}
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(
                f"http://ip-api.com/json/{ip}?fields=country,regionName,city,isp,query&lang=uk"
            )
            d = r.json()
            return {
                "ip": ip,
                "country": d.get("country", ""),
                "region": d.get("regionName", ""),
                "city": d.get("city", ""),
                "isp": d.get("isp", ""),
            }
    except Exception:
        return {"ip": ip, "country": "", "city": "", "region": "", "isp": ""}


def _recommendations(profile: dict, plan: dict, user: dict) -> list[str]:
    """Розширені рекомендації на основі активності (4-5 порад)."""
    recs = []
    total = profile.get("total_focus_seconds", 0) or 0
    upload_count = profile.get("upload_count", 0) or 0
    hours = total / 3600
    name = (user.get("first_name") or "друг").strip()

    if total == 0:
        # новачок
        recs.append(f"Привіт, {name}! Почни з короткої сесії на 15 хвилин — так легше ввійти в ритм.")
        recs.append("Обери категорію «Над чим працюємо?» перед стартом таймера.")
        recs.append("Додай свій трек на вкладці Музика — посилання на YouTube або пряме URL.")
        recs.append("Тапни по таймеру, щоб виставити власний час фокусу.")
    else:
        if hours < 5:
            recs.append("Спробуй режим Deep Work (50 хв) для глибокої концентрації.")
        elif hours < 20:
            recs.append("Чудовий прогрес! Постав ціль — 5 сесій поспіль для серії.")
        else:
            recs.append(f"Ти вже набрав {hours:.1f} годин фокусу. Вражаюча дисципліна! 🏆")

        recs.append("Закріпи улюблений трек 📌, щоб він завжди був під рукою.")
        if upload_count == 0:
            recs.append("Створи свій плейліст — додай треки в категорію «Креатив» чи «DeepWork».")
        if not plan.get("is_premium"):
            recs.append("Преміум відкриває статистику за категоріями, графіки прогресу та серії 🔥.")

    return recs[:5]


@app.get("/api/modes")
async def api_modes():
    """Доступні режими фокусу."""
    return {"modes": db.FOCUS_MODES}


@app.get("/api/stats")
async def api_stats(user: dict = Depends(current_user)):
    """Базова (безкоштовна) статистика."""
    return db.get_stats(user["id"])


@app.get("/api/stats/today")
async def api_stats_today(user: dict = Depends(current_user)):
    """Прогрес на сьогодні + серія для головного екрана (безкоштовно).
    Легкий запит — лише сума за сьогодні + серія, без повної агрегації."""
    _ensure_user_exists(user)
    return db.get_daily_progress(user["id"])


class DailyGoalReq(BaseModel):
    seconds: int = Field(..., ge=300, le=86400, description="ціль у секундах (5хв..24год)")


@app.put("/api/daily-goal")
async def api_set_daily_goal(payload: DailyGoalReq, user: dict = Depends(current_user)):
    """Встановлює щоденну ціль фокусу (у секундах)."""
    _ensure_user_exists(user)
    val = db.set_daily_goal(user["id"], payload.seconds)
    return {"ok": True, "daily_goal_seconds": val}


@app.get("/api/network/payload")
async def api_network_payload(request: Request):
    """Повертає фіксований payload (~256КБ) для вимірювання швидкості завантаження клієнтом.
    Без авторизації — це просто фіксовані дані."""
    # 256 KB випадкових даних (64 * 4096 байт)
    import secrets
    data = secrets.token_bytes(256 * 1024)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Payload-Bytes": str(len(data)),
        },
    )


@app.get("/api/stats/premium")
async def api_stats_premium(
    request: Request,
    range: str = "month",
    category: str | None = None,
    user: dict = Depends(current_user),
):
    """Преміум-статистика: за категоріями, по днях, серії."""
    if range not in ("week", "month", "year", "all"):
        range = "month"
    if not db.is_premium(user["id"]):
        raise HTTPException(status_code=403, detail="premium only")
    return db.get_stats_premium(user["id"], range_key=range, category=category)


@app.post("/api/session/finish")
async def api_finish_session(
    payload: FinishSession, user: dict = Depends(current_user)
):
    """Зберігає результат сесії фокусу."""
    if payload.mode not in db.FOCUS_MODES:
        raise HTTPException(status_code=400, detail="unknown mode")
    _ensure_user_exists(user)
    db.save_session(
        tg_id=user["id"],
        mode=payload.mode,
        planned=payload.planned,
        actual=payload.actual,
        completed=payload.completed,
        started_at=payload.started_at,
        category=payload.category,
    )
    db.record_product_event(
        user["id"],
        "focus_session_complete" if payload.completed else "focus_session_partial",
        {
            "mode": payload.mode,
            "planned_seconds": payload.planned,
            "actual_seconds": payload.actual,
            "category": payload.category,
        },
    )
    stats = db.get_stats(user["id"])
    return {"ok": True, "stats": stats}


# ------------------------------ музика --------------------------------------


@app.get("/api/tracks")
async def api_list_tracks(
    category: str | None = None,
    user: dict = Depends(current_user),
):
    """Усі треки користувача: демо + адмінські + особисті (з фільтром за категорією)."""
    _ensure_user_exists(user)
    _ensure_user_exists(user)
    saved = db.list_tracks(user["id"], category=category)
    # embed-URL для iframe; відносні URL → абсолютні
    base = settings.WEBAPP_URL
    for t in saved:
        if t["kind"] == "youtube":
            t["embed_url"] = storage.youtube_embed_url(t["url"])
        elif t["kind"] == "soundcloud":
            t["embed_url"] = storage.soundcloud_embed_url(t["url"])
        elif t["kind"] == "spotify":
            t["embed_url"] = storage.spotify_embed_url(t["url"])
        elif t["kind"] == "apple_music":
            t["embed_url"] = storage.apple_music_embed_url(t["url"])
        elif base and t["url"].startswith("/"):
            t["url"] = base + t["url"]
    is_admin = settings.is_admin(tg_id=user["id"], email=user.get("email",""))
    upload_count = db.get_upload_count(user["id"])
    return {
        "demo": [],
        "tracks": saved,
        "is_admin": is_admin,
        "upload_enabled": settings.supabase_enabled,
        "upload_count": upload_count,
        "upload_limit": settings.FREE_UPLOAD_LIMIT,
        "is_premium": db.is_premium(user["id"]),
    }


@app.post("/api/tracks/url")
async def api_add_track_url(
    payload: AddTrackByURL, user: dict = Depends(current_user)
):
    """Додає трек за прямим посиланням або YouTube."""
    _ensure_user_exists(user)
    scope = payload.scope
    # demo/admin — лише адмін
    if scope in ("demo", "admin") and not settings.is_admin(tg_id=user["id"], email=user.get("email","")):
        raise HTTPException(status_code=403, detail="admin only")
    # ліміт для безкоштовних
    if scope == "user" and not db.is_premium(user["id"]):
        used = db.get_upload_count(user["id"])
        if used >= settings.FREE_UPLOAD_LIMIT:
            raise HTTPException(
                status_code=402,
                detail=f"Ліміт безкоштовних треків ({settings.FREE_UPLOAD_LIMIT}). Преміум знімає обмеження.",
            )

    kind = storage.classify_url(payload.url)
    title = payload.title
    author = payload.author
    # якщо назва не вказана — підтягуємо з джерела (YouTube/SoundCloud/Spotify)
    if not title:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if kind == "youtube":
                    info = await storage.fetch_youtube_info(client, payload.url)
                elif kind == "soundcloud":
                    info = await storage.fetch_soundcloud_info(client, payload.url)
                elif kind == "spotify":
                    info = await storage.fetch_spotify_info(client, payload.url)
                elif kind == "apple_music":
                    info = await storage.fetch_apple_music_info(client, payload.url)
                else:
                    info = {"title": "", "author": ""}
                if info.get("title"):
                    title = info["title"]
                if not author and info.get("author"):
                    author = info["author"]
        except Exception:
            pass
    tid = db.add_track(
        tg_id=user["id"],
        scope=scope,
        kind=kind,
        url=payload.url,
        title=title or "Без назви",
        author=author or "",
        category=payload.category,
    )
    return {"ok": True, "id": tid, "kind": kind, "title": title or "Без назви"}


@app.post("/api/tracks/upload")
async def api_upload_track(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    scope: str = Form("user"),
    category: str = Form("other"),
    user: dict = Depends(current_user),
):
    """Завантажує файл музики у Supabase Storage."""
    _ensure_user_exists(user)
    if scope == "admin" and not settings.is_admin(tg_id=user["id"], email=user.get("email","")):
        raise HTTPException(status_code=403, detail="admin only")
    if scope == "user" and not db.is_premium(user["id"]):
        used = db.get_upload_count(user["id"])
        if used >= settings.FREE_UPLOAD_LIMIT:
            raise HTTPException(
                status_code=402,
                detail=f"Ліміт безкоштовних треків ({settings.FREE_UPLOAD_LIMIT}).",
            )
    if category not in db.CATEGORIES:
        category = "other"
    if not settings.supabase_enabled:
        raise HTTPException(
            status_code=400,
            detail="Завантаження у хмару не налаштоване (SUPABASE_*).",
        )

    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл задовгий (макс 25 МБ)")

    import time

    suffix = Path(file.filename or "track.mp3").suffix.lower()[:6] or ".mp3"
    filename = f"{int(time.time() * 1000)}_{user['id']}{suffix}"
    content_type = file.content_type or "audio/mpeg"

    async with httpx.AsyncClient(timeout=60) as client:
        url = await storage.upload_to_supabase(
            client, data, filename, content_type=content_type
        )
    if not url:
        raise HTTPException(status_code=502, detail="Не вдалося завантажити у хмару")

    tid = db.add_track(
        tg_id=user["id"],
        scope=scope,
        kind="audio",
        url=url,
        title=title or (file.filename or "Без назви"),
        author=author,
        category=category,
    )
    return {"ok": True, "id": tid, "url": url}


@app.delete("/api/tracks/{track_id}")
async def api_delete_track(
    track_id: int, user: dict = Depends(current_user)
):
    """Видаляє трек. Адмін — будь-який; користувач — лише свій особистий."""
    is_admin = settings.is_admin(tg_id=user["id"], email=user.get("email",""))
    ok = db.delete_track(user["id"], track_id, is_admin)
    if not ok:
        raise HTTPException(status_code=404, detail="не знайдено або немає доступу")
    return {"ok": True}


@app.patch("/api/tracks/{track_id}")
async def api_rename_track(
    track_id: int,
    payload: RenameTrack,
    user: dict = Depends(current_user),
):
    """Перейменовує трек. Адмін — будь-який; користувач — лише свій особистий."""
    is_admin = settings.is_admin(tg_id=user["id"], email=user.get("email",""))
    ok = db.rename_track(user["id"], track_id, payload.title, is_admin)
    if not ok:
        raise HTTPException(status_code=404, detail="не знайдено або немає доступу")
    return {"ok": True}


@app.post("/api/tracks/favorite")
async def api_toggle_favorite(
    payload: TrackKeyReq, user: dict = Depends(current_user)
):
    """Перемикає «бажане» для треку."""
    _ensure_user_exists(user)
    state = db.toggle_favorite(user["id"], payload.track_key)
    return {"ok": True, "is_favorite": state}


@app.post("/api/tracks/pin")
async def api_toggle_pin(
    payload: TrackKeyReq, user: dict = Depends(current_user)
):
    """Перемикає «закріпити» для треку."""
    _ensure_user_exists(user)
    state = db.toggle_pin(user["id"], payload.track_key)
    return {"ok": True, "is_pinned": state}


# ------------------------------ підписка / оплата ----------------------------


@app.post("/api/subscribe")
async def api_subscribe(user: dict = Depends(current_user)):
    """Створює інвойс Telegram Stars для місячної підписки.
    Повертає invoice_link (slug) — клієнт відкриває через tg.openInvoice(slug)."""
    _ensure_user_exists(user)
    if not settings.stars_enabled:
        return {
            "available": False,
            "message": "Оплата тимчасово недоступна.",
            "price_stars": settings.PREMIUM_PRICE_STARS,
        }
    order_id = f"focus-{user['id']}-{int(time.time())}"
    invoice = await _send_stars_invoice(
        chat_id=user["id"],
        title=f"Focus ON Premium — {settings.PREMIUM_DURATION_DAYS} днів",
        description="Детальна статистика, графіки прогресу, серії, безліміт треків.",
        payload=order_id,
        stars=settings.PREMIUM_PRICE_STARS,
        subscription_period=settings.PREMIUM_DURATION_DAYS * 86400,
    )
    if not invoice:
        return {
            "available": False,
            "message": "Не вдалося створити інвойс. Спробуйте пізніше.",
            "price_stars": settings.PREMIUM_PRICE_STARS,
        }
    return {
        "available": True,
        "invoice_link": invoice,  # slug для tg.openInvoice()
        "order_id": order_id,
        "price_stars": settings.PREMIUM_PRICE_STARS,
        "duration_days": settings.PREMIUM_DURATION_DAYS,
    }


async def _send_stars_invoice(chat_id: int, title: str, description: str,
                              payload: str, stars: int, subscription_period: int) -> str | None:
    """Створює інвойс через sendInvoice (Telegram Stars, currency=XTR).
    Повертає invoice link (slug) або None."""
    if not settings.has_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendInvoice",
                json={
                    "chat_id": chat_id,
                    "title": title,
                    "description": description,
                    "payload": payload,
                    "currency": "XTR",  # Telegram Stars
                    "prices": [{"label": title, "amount": stars}],
                    "subscription_period": subscription_period,
                    "provider_token": "",  # порожньо = Stars (не платіжний провайдер)
                },
            )
            data = r.json()
            if data.get("ok"):
                # Telegram повертає повідомлення з інвойсом; потрібен slug для openInvoice
                # sendInvoice не дає slug напряму — витягуємо з createInvoiceLink краще
                pass
            # краще використати createInvoiceLink — дає URL-slug для openInvoice
            r2 = await c.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/createInvoiceLink",
                json={
                    "title": title,
                    "description": description,
                    "payload": payload,
                    "currency": "XTR",
                    "prices": [{"label": title, "amount": stars}],
                    "subscription_period": subscription_period,
                    "provider_token": "",
                },
            )
            d2 = r2.json()
            if d2.get("ok"):
                return d2["result"]  # invoice URL
    except Exception as e:
        import logging
        logging.getLogger("invoice").warning("sendInvoice помилка: %s", e)
    return None


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    """Приймає Telegram updates (платежі Stars, /start).
    Telegram шле сюди updates після setWebhook."""
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    await _handle_update(update)
    return JSONResponse({"ok": True}, status_code=200)


async def _handle_update(update: dict) -> None:
    """Обробляє Telegram update: платежі Stars + /start."""
    # pre_checkout_query — Telegram чекає відповідь за 10с
    pcq = update.get("pre_checkout_query")
    if pcq:
        await _answer_pre_checkout(pcq["id"], ok=True)
        return

    message = update.get("message")
    if not message:
        return
    chat_id = message["chat"]["id"]

    # успішний платіж Stars
    sp = message.get("successful_payment")
    if sp:
        await _process_stars_payment(sp, chat_id)
        return

    # текстові команди. Підтримуємо також /start <payload> із лендінгу.
    text = (message.get("text") or "").strip()
    command, _, command_payload = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    if command in ("/start", "/help", "/app"):
        start_param = command_payload.strip()[:64] if command == "/start" else ""
        # /start login → одразу генеруємо код (без окремої команди)
        if start_param == "login":
            await _send_login_code(message)
        else:
            await _send_welcome(chat_id, start_param=start_param)
    elif command == "/login":
        await _send_login_code(message)


async def _answer_pre_checkout(query_id: int, ok: bool, error_message: str = "") -> None:
    """Відповідає на pre_checkout_query (обов'язково за 10с, інакше платіж відхиляється)."""
    if not settings.has_token:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/answerPreCheckoutQuery",
                json={"pre_checkout_query_id": query_id, "ok": ok,
                      **({"error_message": error_message} if error_message else {})},
            )
    except Exception:
        pass


async def _process_stars_payment(sp: dict, chat_id: int) -> None:
    """Обробляє успішний платіж: активує преміум + лог + адміну."""
    order_id = str(sp.get("invoice_payload", ""))
    stars = int(sp.get("total_amount", 0) or 0)  # в XTR
    currency = sp.get("currency", "XTR")
    tg_id = chat_id
    from_user = sp.get("from") or {}
    first_name = from_user.get("first_name", "")
    username = from_user.get("username")

    # ім'я + клікабельне посилання на профіль користувача
    name_line = first_name or "Без імені"
    if username:
        name_line += f' · <a href="https://t.me/{username}">@{username}</a>'
    profile_link = f'<a href="tg://user?id={tg_id}">Профіль</a>'

    db.record_payment(tg_id, order_id, stars, "success", raw=str(sp))
    db.record_product_event(tg_id, "purchase", {
        "transaction_id": order_id,
        "currency": "XTR",
        "value": stars,
        "item_id": "focus_on_premium_month",
    })
    expires = db.set_user_plan(tg_id, "premium", days=settings.PREMIUM_DURATION_DAYS)
    await _notify_admin(
        f"⭐ <b>Преміум активовано (Stars)</b>\n\n"
        f"<b>Користувач:</b> {name_line} ({profile_link})\n"
        f"<b>ID:</b> <code>{tg_id}</code>\n"
        f"<b>Сума:</b> {stars} Stars\n"
        f"<b>Замовлення:</b> <code>{order_id}</code>\n"
        f"<b>Діє до:</b> {expires[:10] if expires else '—'}"
    )


async def _send_login_code(message: dict) -> None:
    """Створює одноразовий код входу і надсилає його юзеру в Telegram.

    Використовується для bot-code auth в Android-застосунку (де Google Sign-In
    недоступний). Код діє 5 хвилин, одноразовий.
    """
    if not settings.has_token:
        return
    from_user = message.get("from") or message.get("chat") or {}
    chat_id = message["chat"]["id"]
    tg_id = int(from_user.get("id", chat_id))
    first_name = from_user.get("first_name", "")
    last_name = from_user.get("last_name", "")
    username = from_user.get("username", "")
    photo_url = from_user.get("photo_url", "")
    code = db.create_login_code(
        tg_id=tg_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
        photo_url=photo_url,
    )
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "parse_mode": "HTML",
                    "text": (
                        f"🔑 <b>Код для входу в Focus ON</b>\n\n"
                        f"<code>{code}</code>\n\n"
                        f"Діє 5 хвилин. Введіть його у застосунку.\n"
                        f"Якщо ви не запитували код — проігноруйте."
                    ),
                },
            )
    except Exception:
        pass


async def _send_welcome(chat_id: int, start_param: str = "") -> None:
    """Вітальне повідомлення з кнопкою відкриття Mini App.

    Якщо користувач прийшов із рекламного deep link ``?start=...``, коротка
    атрибуційна мітка передається у URL Mini App як ``start_param``.
    """
    if not settings.has_token or not settings.WEBAPP_URL:
        return

    web_app_url = settings.WEBAPP_URL
    if start_param:
        web_app_url = _with_query_param(web_app_url, "start_param", start_param)

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "🎯 Focus ON — таймер фокусу з музикою та статистикою.\nНатисни кнопку, щоб відкрити застосунок.",
                    "reply_markup": {
                        "inline_keyboard": [[{
                            "text": "🚀 Відкрити Focus ON",
                            "web_app": {"url": web_app_url},
                        }]]
                    },
                },
            )
    except Exception:
        pass


@app.post("/api/admin/grant-premium")
async def api_grant_premium(
    payload: GrantPremium, user: dict = Depends(current_user)
):
    """Ручне вмикання преміуму (тільки адмін)."""
    if not settings.is_admin(tg_id=user["id"], email=user.get("email","")):
        raise HTTPException(status_code=403, detail="admin only")
    expires = db.set_user_plan(payload.tg_id, "premium", days=payload.days)
    return {"ok": True, "tg_id": payload.tg_id, "plan": "premium", "plan_expires_at": expires}


@app.post("/api/redeem")
async def api_redeem(payload: RedeemCode, user: dict = Depends(current_user)):
    """Активує промокод для користувача."""
    _ensure_user_exists(user)
    result = db.redeem_promo_code(user["id"], payload.code)
    if result.get("ok"):
        db.record_product_event(user["id"], "promo_activate", {
            "days": result.get("days", 0),
        })
    return result


@app.post("/api/play/verify")
async def api_play_verify(payload: PlayVerify, user: dict = Depends(current_user)):
    """Підтверджує покупку Google Play Billing та активує premium.

    Android BillingClient після успішної покупки шле сюди purchase_token.
    Дедуплікація: якщо токен вже оброблений — повертаємо поточний план без повторної активації.

    NOTE: MVP-перевірка — довіряємо клієнтському токену. Повна криптоперевірка
    через Play Developer API (purchases.subscriptions.get) — у v2, після
    створення Service Account у Play Console.
    """
    _ensure_user_exists(user)
    tg_id = user["id"]

    # Дедуплікація: один purchase_token = одна активація
    if db.is_payment_processed(payload.purchase_token, status="google_verified"):
        plan = db.get_user_plan(tg_id)
        return {
            "ok": True,
            "already_processed": True,
            "premium": plan["is_premium"],
            "plan_expires_at": plan["plan_expires_at"],
        }

    # Лог платежу (purchase_token як унікальний order_id)
    payload_log = {
        "platform": "google_play",
        "product_id": payload.product_id,
        "order_id": payload.order_id,
        "purchase_token": payload.purchase_token[:64] + "…",
    }
    db.record_payment(
        tg_id, payload.purchase_token, 0.0, "google_verified", raw=json.dumps(payload_log)
    )
    db.record_product_event(tg_id, "purchase", {
        "platform": "google_play",
        "product_id": payload.product_id,
        "transaction_id": payload.order_id or payload.purchase_token[:32],
        "currency": "USD",
    })
    expires = db.set_user_plan(tg_id, "premium", days=settings.PREMIUM_DURATION_DAYS)

    # Сповіщення адміністру (формат як у Stars flow)
    name_line = user.get("first_name") or "Без імені"
    username = user.get("username")
    if username:
        name_line += f' · <a href="https://t.me/{username}">@{username}</a>'
    profile_link = f'<a href="tg://user?id={tg_id}">Профіль</a>'
    await _notify_admin(
        f"🤖 <b>Преміум активовано (Google Play)</b>\n\n"
        f"<b>Користувач:</b> {name_line} ({profile_link})\n"
        f"<b>ID:</b> <code>{tg_id}</code>\n"
        f"<b>Продукт:</b> <code>{payload.product_id}</code>\n"
        f"<b>Замовлення:</b> <code>{payload.order_id or '—'}</code>\n"
        f"<b>Діє до:</b> {expires[:10] if expires else '—'}"
    )

    return {"ok": True, "premium": True, "plan_expires_at": expires}


@app.get("/api/admin/promo-codes")
async def api_list_promo(user: dict = Depends(current_user)):
    """Список промокодів (тільки адмін)."""
    if not settings.is_admin(tg_id=user["id"], email=user.get("email","")):
        raise HTTPException(status_code=403, detail="admin only")
    return {"codes": db.list_promo_codes()}


@app.post("/api/admin/promo-codes")
async def api_create_promo(payload: PromoCodeReq, user: dict = Depends(current_user)):
    """Створити промокод (тільки адмін)."""
    if not settings.is_admin(tg_id=user["id"], email=user.get("email","")):
        raise HTTPException(status_code=403, detail="admin only")
    result = db.add_promo_code(payload.code, payload.days, payload.max_uses, user["id"])
    return result


@app.delete("/api/admin/promo-codes/{code}")
async def api_delete_promo(code: str, user: dict = Depends(current_user)):
    """Видалити промокод (тільки адмін)."""
    if not settings.is_admin(tg_id=user["id"], email=user.get("email","")):
        raise HTTPException(status_code=403, detail="admin only")
    ok = db.delete_promo_code(code)
    return {"ok": ok}


@app.get("/api/admin/stats")
async def api_admin_stats(user: dict = Depends(current_user)):
    """Повна статистика використання застосунку (тільки адмін)."""
    if not settings.is_admin(tg_id=user["id"], email=user.get("email","")):
        raise HTTPException(status_code=403, detail="admin only")
    stats = db.get_admin_stats()
    stats["online"] = {"count": online_count(), "users": online_users()}
    return stats


@app.get("/api/admin/stats/stream")
async def api_admin_stats_stream(request: Request):
    """SSE stream — real-time оновлення адмін-статистики.
    Auth через query param: ?jwt=<JWT> (Android/Google) або ?init_data=<initData> (Telegram Mini App).
    EventSource не підтримує заголовки, тому токен у query."""
    # Приймаємо і JWT (Android Google Sign-In), і initData (Telegram Mini App)
    token = request.query_params.get("jwt", "") or request.query_params.get("init_data", "")
    user = _authenticate_token(token)
    if not user or not settings.is_admin(tg_id=user.get("id", 0), email=user.get("email", "")):
        raise HTTPException(status_code=403, detail="admin only")

    from fastapi.encoders import jsonable_encoder
    import json

    async def event_generator():
        queue = asyncio.Queue(maxsize=1)
        _admin_subscribers.add(queue)
        try:
            # перший пуш — одразу повна статистика
            yield f"data: {json.dumps(jsonable_encoder(db.get_admin_stats()))}\n\n"
            # ...і одразу поточний онлайн
            yield f"event: online\ndata: {json.dumps({'count': online_count(), 'users': online_users()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                # чекаємо або подію зміни даних, або таймаут для онлайн-пушу (2с)
                triggered = False
                try:
                    await asyncio.wait_for(queue.get(), timeout=2)
                    triggered = True
                except asyncio.TimeoutError:
                    pass
                # онлайн-статистику шлемо кожні 2с (дешево — in-memory, без БД)
                # це ж і keep-alive для SSE-з'єднання
                yield f"event: online\ndata: {json.dumps({'count': online_count(), 'users': online_users()})}\n\n"
                # якщо були реальні зміни даних — повний пуш статистики
                if triggered:
                    yield f"data: {json.dumps(jsonable_encoder(db.get_admin_stats()))}\n\n"
        finally:
            _admin_subscribers.discard(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------ онлайн-трекер (heartbeat) -------------------


@app.post("/api/heartbeat")
async def api_heartbeat(user: dict = Depends(current_user)):
    """Heartbeat від клієнта: позначає користувача онлайн (in-memory).
    Клієнт шле кожні ~30с + при відновленні видимості вікна."""
    _touch_online(user["id"])
    return {"ok": True, "online": online_count()}


# ------------------------------ музика у фоні + статистика ------------------


class PlayTrackReq(BaseModel):
    track_key: str
    title: str = ""
    source: str = "webview"
    duration: int = 0


@app.post("/api/play")
async def api_play(payload: PlayTrackReq, user: dict = Depends(current_user)):
    """Записує факт прослуховування треку (статистика)."""
    _ensure_user_exists(user)
    db.record_play(user["id"], payload.track_key, payload.title, payload.source, payload.duration)
    return {"ok": True}


@app.post("/api/play-in-background")
async def api_play_in_background(payload: PlayTrackReq, user: dict = Depends(current_user)):
    """Відправляє аудіо в чат через sendAudio для фонового відтворення.
    Працює лише для прямих аудіо-URL (mp3/wav). Для embed-джерел клієнт
    використовує deep link до нативного плеєра."""
    _ensure_user_exists(user)
    db.record_play(user["id"], payload.track_key, payload.title, "background", payload.duration)
    # шукаємо URL треку за track_key
    # track_key формат: 'demo:<id>' або 'db:<id>'
    try:
        track_id = int(payload.track_key.split(":")[1])
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="невірний track_key")
    # отримуємо URL з БД
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT url, kind, title FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="трек не знайдено")
    if row["kind"] != "audio":
        raise HTTPException(status_code=400, detail="фонове відтворення лише для прямих аудіо-URL")
    url = row["url"]
    title = row["title"] or payload.title or "Focus ON"
    # sendAudio через Bot API — Telegram сам завантажить і відправить в чат
    if not settings.has_token:
        raise HTTPException(status_code=400, detail="бот не налаштований")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendAudio",
                json={
                    "chat_id": user["id"],
                    "audio": url,
                    "title": title[:100],
                    "caption": "🎵 " + title + " — Focus ON",
                },
            )
            data = r.json()
            if data.get("ok"):
                return {"ok": True, "sent": True}
            else:
                return {"ok": True, "sent": False, "error": data.get("description", "")}
    except Exception as e:
        return {"ok": True, "sent": False, "error": str(e)}


# ------------------------------ баг-репорти ---------------------------------


@app.post("/api/bug-report")
async def api_bug_report(
    payload: BugReport, request: Request, user: dict = Depends(current_user)
):
    """Зберігає звіт про баг і шле повідомлення адміну з мережевою інформацією."""
    _ensure_user_exists(user)
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")
    geo = await _geo_info(ip)
    region = ", ".join(p for p in [geo.get("country"), geo.get("region"), geo.get("city")] if p)

    report_id = db.save_bug_report(
        tg_id=user["id"],
        message=payload.message,
        user_agent=ua,
        ip=ip,
        region=region,
        platform=payload.platform,
        screen=payload.screen,
    )

    name = (user.get("first_name", "") + " " + user.get("last_name", "")).strip() or user.get("username", "")
    uname = f"@{user['username']}" if user.get("username") else "—"
    msg = (
        f"🐞 <b>Новий баг-репорт</b> #{report_id}\n\n"
        f"<b>Від:</b> {name} (<code>{user['id']}</code>)\n"
        f"<b>Username:</b> {uname}\n\n"
        f"<b>Текст:</b>\n{payload.message}\n\n"
        f"<b>Платформа:</b> {payload.platform or '—'}\n"
        f"<b>Екран:</b> {payload.screen or '—'}\n"
        f"<b>IP:</b> <code>{ip}</code>\n"
        f"<b>Країна:</b> {geo.get('country', '—')}\n"
        f"<b>Місто:</b> {geo.get('city', '—')}\n"
        f"<b>Провайдер:</b> {geo.get('isp', '—')}\n"
        f"<b>UA:</b> <code>{ua[:120]}</code>"
    )
    await _notify_admin(msg)
    return {"ok": True, "id": report_id}


# ------------------------------ Публічні сторінки ---------------------------


def _public_file(path: Path, media_type: str, *, cache_seconds: int = 0):
    """Повертає публічний файл із зрозумілою помилкою, якщо його не додано."""
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path.name} не знайдено")
    cache_control = (
        f"public, max-age={cache_seconds}"
        if cache_seconds > 0
        else "no-store, no-cache, must-revalidate, max-age=0"
    )
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": cache_control},
    )


@app.get("/landing", include_in_schema=False)
async def landing_page():
    """Маркетинговий лендінг для Google Ads та інших рекламних джерел."""
    return _public_file(LANDING_FILE, "text/html; charset=utf-8")


@app.get("/privacy", include_in_schema=False)
async def privacy_page():
    """Політика конфіденційності Focus ON."""
    return _public_file(PRIVACY_FILE, "text/html; charset=utf-8")


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    return _public_file(ROBOTS_FILE, "text/plain; charset=utf-8", cache_seconds=3600)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    return _public_file(SITEMAP_FILE, "application/xml; charset=utf-8", cache_seconds=3600)


# ------------------------------ Mini App сторінка ---------------------------


@app.get("/")
async def index(request: Request):
    """Головна сторінка Mini App (SPA). Завжди свежа (no-cache)."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse(
            {"error": "static/index.html не знайдено. Створіть фронтенд."},
            status_code=500,
        )
    content = index_file.read_text(encoding="utf-8")
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/privacy")
async def privacy_policy():
    """Privacy Policy — обов'язково для Google Play Console."""
    html = """<!DOCTYPE html>
<html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Focus ON — Політика конфіденційності</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #07030f; color: #e8e2ff; font-family: -apple-system, system-ui, sans-serif;
         line-height: 1.7; padding: 24px; max-width: 720px; margin: 0 auto; }
  h1 { color: #bf5af2; font-size: 28px; margin-bottom: 8px; }
  h2 { color: #5ac8fa; font-size: 20px; margin: 24px 0 8px; }
  p, li { color: rgba(232,226,255,0.85); font-size: 15px; margin-bottom: 8px; }
  ul { padding-left: 20px; }
  .meta { color: rgba(210,200,255,0.5); font-size: 13px; margin-bottom: 32px; }
  a { color: #5ac8fa; }
</style></head><body>
  <h1>Політика конфіденційності</h1>
  <p class="meta">Focus ON · Останнє оновлення: 18 липня 2026</p>

  <h2>1. Які дані ми збираємо</h2>
  <ul>
    <li><b>Telegram User ID</b> та <b>username</b> — для ідентифікації вашого акаунта при вході через Telegram.</li>
    <li><b>Статистика фокус-сесій</b> — тривалість, дата завершення, серії (streaks).</li>
    <li><b>Налаштування</b> — щоденна ціль фокусу, збережені пресети звукових міксів.</li>
  </ul>

  <h2>2. Чому ми збираємо ці дані</h2>
  <ul>
    <li>Для синхронізації статистики між пристроями.</li>
    <li>Для розрахунку серій та щоденних цілей.</li>
    <li>Для відображення вашого прогресу.</li>
  </ul>

  <h2>3. Де зберігаються дані</h2>
  <ul>
    <li>Хмарна база даних PostgreSQL (Neon, AWS us-east-2).</li>
    <li>Аудіофайли та кеш — на вашому пристрої (IndexedDB / локальне сховище).</li>
    <li>JWT-токен авторизації — на пристрої, термін дії 30 днів.</li>
  </ul>

  <h2>4. Чи передаємо ми дані третім сторонам</h2>
  <p><b>Ні.</b> Ми не продаємо, не орендуємо та не передаємо ваші дані жодним рекламним мережам,
  аналітичним сервісам чи брокерам даних.</p>

  <h2>5. Шифрування</h2>
  <ul>
    <li>Уся передача даних відбувається через <b>HTTPS</b> (TLS 1.2+).</li>
    <li>Авторизація — через підписаний <b>JWT (HMAC-SHA256)</b>.</li>
  </ul>

  <h2>6. Видалення даних</h2>
  <p>Ви можете видалити свій акаунт та всі пов'язані дані, надіславши запит через
  бота <b>@focuson_on_bot</b> у Telegram. Дані будуть видалені протягом 72 годин.</p>

  <h2>7. Діти</h2>
  <p>Застосунок не орієнтований на дітей молодше 13 років. Ми не свідомо збираємо
  дані від дітей.</p>

  <h2>8. Зміни політики</h2>
  <p>Ми можемо оновлювати цю політику. Про значні зміни повідомимо через застосунок або бота.</p>

  <h2>9. Контакти</h2>
  <p>Питання щодо конфіденційності: <b>@focuson_on_bot</b> у Telegram.</p>
</body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/android-login")
async def android_login(request: Request):
    """Сторінка логіну для Android-клієнта.
    Показує Telegram Login Widget усередині WebView.
    Після успішного входу відправляє auth_data на /api/auth/telegram-login,
    отримує JWT і передає його через AndroidNative.onLogin(token) JS-міст."""
    bot_id = settings.BOT_TOKEN.split(":")[0] if settings.BOT_TOKEN else ""
    bot_username = os.getenv("BOT_USERNAME", "focuson_on_bot")
    bot_domain = settings.WEBAPP_URL.rstrip("/").replace("https://", "").replace("http://", "") if settings.WEBAPP_URL else request.url.hostname
    html = f"""<!DOCTYPE html>
<html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Focus ON — Вхід</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #07030f; color: #fff; font-family: -apple-system, system-ui, sans-serif;
          min-height: 100vh; display: flex; flex-direction: column; align-items: center;
          justify-content: center; padding: 24px; }}
  h1 {{ color: #bf5af2; font-size: 32px; font-weight: 800; margin-bottom: 8px; }}
  p {{ color: rgba(210,200,255,0.7); font-size: 15px; text-align: center; line-height: 1.5; margin-bottom: 32px; }}
  #widget {{ margin-bottom: 24px; }}
  #status {{ color: rgba(210,200,255,0.5); font-size: 14px; min-height: 20px; }}
  .err {{ color: #ff453a; }}
  .ok {{ color: #30d158; }}
</style></head><body>
  <h1>🎯 Focus ON</h1>
  <p>Увійдіть через Telegram,<br>щоб синхронізувати прогрес</p>
  <div id="widget">
    <script async src="https://telegram.org/js/telegram-widget.js?22"
      data-telegram-login="{bot_username}"
      data-size="large"
      data-onauth="onTelegramAuth(user)"
      data-request-access="write"></script>
  </div>
  <div id="status"></div>
<script>
  // Вигін після успішного входу: обмін auth_data → JWT → AndroidNative.onLogin
  function exchangeAuth(params) {{
    document.getElementById('status').innerHTML = '<span class="ok">✅ Вхід виконано, обмін на токен…</span>';
    fetch('/api/auth/telegram-login', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ auth_data: params }})
    }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
      if (d.token) {{
        document.getElementById('status').innerHTML = '<span class="ok">Токен отримано ✓</span>';
        if (window.AndroidNative && window.AndroidNative.onLogin) {{
          window.AndroidNative.onLogin(d.token);
        }} else {{
          localStorage.setItem('focus_jwt', d.token);
          document.getElementById('status').innerHTML = '<span class="ok">Успіх! Можна закрити сторінку.</span>';
        }}
      }} else {{
        document.getElementById('status').innerHTML = '<span class="err">Помилка: ' + (d.detail || 'невідомо') + '</span>';
      }}
    }}).catch(function(e) {{
      document.getElementById('status').innerHTML = '<span class="err">Мережева помилка</span>';
    }});
  }}

  // Спосіб 1: Telegram Widget викличе onTelegramAuth(user) напряму
  function onTelegramAuth(user) {{
    var params = new URLSearchParams();
    Object.keys(user).forEach(function(k) {{ params.append(k, user[k]); }});
    exchangeAuth(params.toString());
  }}

  // Спосіб 2: widget повертає результат через URL hash #tgAuthResult=<base64>
  // (стосується, коли Telegram-апп недоступний / WebView). base64 → JSON user.
  function checkHashResult() {{
    var m = (location.hash || '').match(/tgAuthResult=([^&]+)/);
    if (!m) return false;
    try {{
      var json = atob(decodeURIComponent(m[1]));
      var user = JSON.parse(json);
      var params = new URLSearchParams();
      Object.keys(user).forEach(function(k) {{ params.append(k, user[k]); }});
      exchangeAuth(params.toString());
      return true;
    }} catch (e) {{
      document.getElementById('status').innerHTML = '<span class="err">Не вдалось розібрати результат входу</span>';
      return false;
    }}
  }}

  // перевіряємо hash при завантаженні та при зміні (redirect після входу)
  if (!checkHashResult()) {{
    window.addEventListener('hashchange', checkHashResult);
  }}
</script>
</body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# Запуск через python app.py
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=settings.PORT, reload=True)

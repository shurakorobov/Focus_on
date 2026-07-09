"""FastAPI сервер: роздає Mini App + REST API для сесій фокусу.

Запуск:
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import storage
from config import settings
from telegram_auth import authenticate

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Життєвий цикл: запускаємо фонові задачі при старті."""
    import asyncio
    import logging
    from keepalive import keepalive_loop

    logging.basicConfig(level=logging.INFO)
    logging.info("🚀 Focus OS стартує... PORT=%s WEBAPP_URL=%s",
                 os.getenv("PORT"), os.getenv("WEBAPP_URL", "(не задано)"))
    task = asyncio.create_task(keepalive_loop())
    yield
    task.cancel()


app = FastAPI(title="Focus OS API", lifespan=lifespan)

# Ініціалізуємо БД при старті
db.init_db()


@app.middleware("http")
async def disable_caching(request: Request, call_next):
    """Вимикає кешування для /static/* і / — Telegram WebView інакше тримає
    стару версію JS/CSS навіть зі зміною ?v=. no-cache дозволяє 304-валідацію,
    але примусово перечитує при зміні."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# Роздаємо статичні ассети (js/css/img)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ------------------------------ авторизація ---------------------------------


def current_user(request: Request) -> dict:
    """DI-залежність: витягує та перевіряє Telegram initData з запиту.

    initData може приходити:
      - у заголовку Authorization: Bearer <initData...>
      - у тілі запиту як поле init_data
      - у query-параметрі ?init_data=...
    """
    init_data = ""

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        init_data = auth[7:].strip()
    elif "init_data" in request.query_params:
        init_data = request.query_params["init_data"]

    if not init_data:
        try:
            body = request.scope.get("_body_json") or {}
        except Exception:
            body = {}
        init_data = body.get("init_data", "") if isinstance(body, dict) else ""

    user = authenticate(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="invalid telegram auth")
    return user


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


# ------------------------------ API -----------------------------------------


@app.get("/api/health")
async def health():
    return {"status": "ok", "configured": settings.is_configured}


@app.get("/api/categories")
async def api_categories():
    """Перелік категорій діяльності."""
    return {"categories": db.CATEGORIES, "modes": db.FOCUS_MODES}


@app.get("/api/me")
async def api_me(request: Request, user: dict = Depends(current_user)):
    """Повертає профіль користувача + тариф + мережева інформація."""
    _ensure_user_exists(user)
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
        "is_admin": settings.is_admin(user["id"]),
        "premium_price_uah": settings.PREMIUM_PRICE_UAH,
        "network": geo,
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
    # youtube-треки отримують embed-URL для iframe; відносні URL → абсолютні
    base = settings.WEBAPP_URL
    for t in saved:
        if t["kind"] == "youtube":
            t["embed_url"] = storage.youtube_embed_url(t["url"])
        elif base and t["url"].startswith("/"):
            t["url"] = base + t["url"]
    is_admin = settings.is_admin(user["id"])
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
    if scope in ("demo", "admin") and not settings.is_admin(user["id"]):
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
    # якщо назва не вказана — намагаємось дістати її автоматично
    if not title:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if kind == "youtube":
                    title = await storage.fetch_youtube_title(client, payload.url)
                    if title and " - " in title:
                        author, title = title.split(" - ", 1)
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
    if scope == "admin" and not settings.is_admin(user["id"]):
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
    is_admin = settings.is_admin(user["id"])
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
    is_admin = settings.is_admin(user["id"])
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
    """Генерує LiqPay checkout для преміум-підписки."""
    _ensure_user_exists(user)
    if not settings.liqpay_enabled:
        return {
            "available": False,
            "message": "Онлайн-оплата налаштовується. Преміум можна активувати вручну — зверніться до адміністратора.",
            "price_uah": settings.PREMIUM_PRICE_UAH,
        }
    import time

    from liqpay import build_checkout_url

    order_id = f"focus-{user['id']}-{int(time.time())}"
    server_url = f"{settings.WEBAPP_URL}/api/payments/liqpay-webhook"
    result_url = settings.WEBAPP_URL
    checkout_url = build_checkout_url(
        public_key=settings.LIQPAY_PUBLIC_KEY,
        private_key=settings.LIQPAY_PRIVATE_KEY,
        amount_uah=settings.PREMIUM_PRICE_UAH,
        order_id=order_id,
        description=f"Focus OS Premium — {settings.PREMIUM_DURATION_DAYS} днів",
        server_url=server_url,
        result_url=result_url,
    )
    return {
        "available": True,
        "checkout_url": checkout_url,
        "order_id": order_id,
        "price_uah": settings.PREMIUM_PRICE_UAH,
        "duration_days": settings.PREMIUM_DURATION_DAYS,
    }


@app.post("/api/payments/liqpay-webhook")
async def api_liqpay_webhook(request: Request):
    """Вебхук від LiqPay (без auth — перевіряється підписом)."""
    if not settings.liqpay_enabled:
        return JSONResponse({"status": "ignored"}, status_code=200)
    from liqpay import verify_webhook, is_payment_success

    form = await request.form()
    data_b64 = form.get("data", "")
    signature = form.get("signature", "")
    payload = verify_webhook(settings.LIQPAY_PRIVATE_KEY, data_b64, signature)
    if not payload:
        return JSONResponse({"status": "bad signature"}, status_code=400)

    order_id = str(payload.get("order_id", ""))
    amount = float(payload.get("amount", 0) or 0)
    status = str(payload.get("status", ""))
    tg_id = _parse_tg_id_from_order(order_id)

    db.record_payment(tg_id, order_id, amount, status, raw=str(payload))
    if is_payment_success(payload) and tg_id:
        db.set_user_plan(tg_id, "premium", days=settings.PREMIUM_DURATION_DAYS)
        await _notify_admin(f"💳 <b>Преміум активовано</b>\nКористувач: <code>{tg_id}</code>\nСума: {amount} UAH\nЗамовлення: {order_id}")
    return JSONResponse({"status": "ok"}, status_code=200)


def _parse_tg_id_from_order(order_id: str) -> int:
    """order_id має формат 'focus-<tg_id>-<ts>'."""
    try:
        parts = order_id.split("-")
        return int(parts[1])
    except Exception:
        return 0


@app.post("/api/admin/grant-premium")
async def api_grant_premium(
    payload: GrantPremium, user: dict = Depends(current_user)
):
    """Ручне вмикання преміуму (тільки адмін). Fallback, коли LiqPay не налаштований."""
    if not settings.is_admin(user["id"]):
        raise HTTPException(status_code=403, detail="admin only")
    expires = db.set_user_plan(payload.tg_id, "premium", days=payload.days)
    return {"ok": True, "tg_id": payload.tg_id, "plan": "premium", "plan_expires_at": expires}


@app.get("/api/admin/stats")
async def api_admin_stats(user: dict = Depends(current_user)):
    """Повна статистика використання застосунку (тільки адмін)."""
    if not settings.is_admin(user["id"]):
        raise HTTPException(status_code=403, detail="admin only")
    return db.get_admin_stats()


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


# Запуск через python app.py
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=settings.PORT, reload=True)

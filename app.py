"""FastAPI сервер: роздає Mini App + REST API для сесій фокусу.

Запуск:
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
import time

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
    # Реєструємо webhook для прийому Telegram updates (платежі Stars, /start тощо)
    await _setup_webhook()
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


@app.api_route("/api/health", methods=["GET", "HEAD"])
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
        "premium_price_stars": settings.PREMIUM_PRICE_STARS,
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
    # якщо назва не вказана — підтягуємо з YouTube (назва + виконавець)
    if not title:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if kind == "youtube":
                    info = await storage.fetch_youtube_info(client, payload.url)
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
        title=f"Focus OS Premium — {settings.PREMIUM_DURATION_DAYS} днів",
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

    # текстові команди
    text = (message.get("text") or "").strip()
    if text in ("/start", "/help", "/app"):
        await _send_welcome(chat_id)


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

    db.record_payment(tg_id, order_id, stars, "success", raw=str(sp))
    expires = db.set_user_plan(tg_id, "premium", days=settings.PREMIUM_DURATION_DAYS)
    await _notify_admin(
        f"⭐ <b>Преміум активовано (Stars)</b>\n"
        f"Користувач: <code>{tg_id}</code>\n"
        f"Сума: {stars} Stars\n"
        f"Замовлення: <code>{order_id}</code>\n"
        f"Діє до: {expires[:10] if expires else '—'}"
    )


async def _send_welcome(chat_id: int) -> None:
    """Вітальне повідомлення з кнопкою відкриття Mini App."""
    if not settings.has_token or not settings.WEBAPP_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "🎯 Focus OS — таймер фокусу з музикою та статистикою.\nНатисни кнопку, щоб відкрити застосунок.",
                    "reply_markup": {
                        "inline_keyboard": [[{
                            "text": "🚀 Відкрити Focus OS",
                            "web_app": {"url": settings.WEBAPP_URL},
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

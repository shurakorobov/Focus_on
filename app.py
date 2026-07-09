"""FastAPI сервер: роздає Mini App + REST API для сесій фокусу.

Запуск:
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
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


class AddTrackByURL(BaseModel):
    url: str = Field(..., min_length=3)
    title: str = ""
    author: str = ""
    scope: str = Field("user", pattern="^(admin|user)$")  # admin лише перевіряється окремо


class RenameTrack(BaseModel):
    title: str = Field(..., min_length=0, max_length=200)


# ------------------------------ API -----------------------------------------


@app.get("/api/health")
async def health():
    return {"status": "ok", "configured": settings.is_configured}


@app.get("/api/me")
async def api_me(user: dict = Depends(current_user)):
    """Повертає профіль користувача (автоматично створюється при першому запиті)."""
    tg_id = user["id"]
    db.upsert_user(
        tg_id=tg_id,
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
        username=user.get("username", ""),
        photo_url=user.get("photo_url", ""),
    )
    profile = db.get_user(tg_id) or {}
    return {"user": user, "profile": profile}


@app.get("/api/modes")
async def api_modes():
    """Доступні режими фокусу."""
    return {"modes": db.FOCUS_MODES}


@app.get("/api/stats")
async def api_stats(user: dict = Depends(current_user)):
    return db.get_stats(user["id"])


@app.post("/api/session/finish")
async def api_finish_session(
    payload: FinishSession, user: dict = Depends(current_user)
):
    """Зберігає результат сесії фокусу."""
    if payload.mode not in db.FOCUS_MODES:
        raise HTTPException(status_code=400, detail="unknown mode")
    db.upsert_user(
        tg_id=user["id"],
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
        username=user.get("username", ""),
        photo_url=user.get("photo_url", ""),
    )
    db.save_session(
        tg_id=user["id"],
        mode=payload.mode,
        planned=payload.planned,
        actual=payload.actual,
        completed=payload.completed,
        started_at=payload.started_at,
    )
    stats = db.get_stats(user["id"])
    return {"ok": True, "stats": stats}


# ------------------------------ музика --------------------------------------


def _ensure_user_exists(user: dict) -> None:
    db.upsert_user(
        tg_id=user["id"],
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
        username=user.get("username", ""),
        photo_url=user.get("photo_url", ""),
    )


@app.get("/api/tracks")
async def api_list_tracks(user: dict = Depends(current_user)):
    """Усі треки користувача: демо + адмінські + особисті."""
    _ensure_user_exists(user)
    demo = storage.list_demo_tracks(settings.WEBAPP_URL)
    saved = db.list_tracks(user["id"])
    # youtube-треки отримують embed-URL для iframe
    for t in saved:
        if t["kind"] == "youtube":
            t["embed_url"] = storage.youtube_embed_url(t["url"])
    is_admin = settings.is_admin(user["id"])
    return {
        "demo": demo,
        "tracks": saved,
        "is_admin": is_admin,
        "upload_enabled": settings.supabase_enabled,
    }


@app.post("/api/tracks/url")
async def api_add_track_url(
    payload: AddTrackByURL, user: dict = Depends(current_user)
):
    """Додає трек за прямим посиланням або YouTube."""
    _ensure_user_exists(user)
    scope = payload.scope
    # адмін-трек може додати лише адмін
    if scope == "admin" and not settings.is_admin(user["id"]):
        raise HTTPException(status_code=403, detail="admin only")

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
    )
    return {"ok": True, "id": tid, "kind": kind, "title": title or "Без назви"}


@app.post("/api/tracks/upload")
async def api_upload_track(
    request: Request,
    file: UploadFile = File(...),
    title: str = "",
    author: str = "",
    scope: str = "user",
    user: dict = Depends(current_user),
):
    """Завантажує файл музики у Supabase Storage (адмін або особистий).

    Доступ: адмін — для scope=admin; будь-хто — для scope=user.
    """
    _ensure_user_exists(user)
    if scope == "admin" and not settings.is_admin(user["id"]):
        raise HTTPException(status_code=403, detail="admin only")
    if not settings.supabase_enabled:
        raise HTTPException(
            status_code=400,
            detail="Завантаження у хмару не налаштоване (SUPABASE_*).",
        )

    data = await file.read()
    # обмеження ~25 МБ
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл задовгий (макс 25 МБ)")

    # безпечне ім'я файлу
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


# ------------------------------ Mini App сторінка ---------------------------


@app.get("/")
async def index():
    """Головна сторінка Mini App (SPA)."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse(
            {"error": "static/index.html не знайдено. Створіть фронтенд."},
            status_code=500,
        )
    return FileResponse(index_file)


# Запуск через python app.py
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=settings.PORT, reload=True)

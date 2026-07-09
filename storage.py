"""Абстракція файлового сховища для музики.

Підтримує:
- локальні демо-треки (завжди доступні)
- Supabase Storage (для завантажених адміном файлів у хмарі)

Якщо Supabase не налаштований — завантаження повертає помилку,
але демо-треки та прямі посилання продовжують працювати.
"""
from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Optional

import httpx

from config import settings

STATIC_TRACKS_DIR = Path(__file__).parent / "static" / "tracks"

# Демо-треки, що постачаються з репозиторію
DEMO_TRACKS = [
    {
        "id": "demo_deep_calm",
        "scope": "demo",
        "kind": "audio",
        "url": "/static/tracks/deep_calm.wav",
        "title": "Deep Calm",
        "author": "Focus OS Demo",
    },
    {
        "id": "demo_pulse_focus",
        "scope": "demo",
        "kind": "audio",
        "url": "/static/tracks/pulse_focus.wav",
        "title": "Pulse Focus",
        "author": "Focus OS Demo",
    },
]


def list_demo_tracks(webapp_url: str = "") -> list[dict]:
    """Демо-треки з абсолютними URL (для відтворення в WebView)."""
    base = webapp_url.rstrip("/")
    out = []
    for t in DEMO_TRACKS:
        item = dict(t)
        if base and item["url"].startswith("/"):
            item["url"] = base + item["url"]
        out.append(item)
    return out


def _supabase_headers(upload: bool = False) -> dict:
    h = {
        "apikey": settings.SUPABASE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_KEY}",
    }
    if upload:
        # при завантаженні не вказуємо Content-Type — сервер визначить сам
        h["x-upsert"] = "true"
    return h


async def upload_to_supabase(
    client: httpx.AsyncClient,
    file_bytes: bytes,
    filename: str,
    content_type: str = "audio/mpeg",
) -> Optional[str]:
    """Завантажує файл у Supabase Storage, повертає публічний URL.

    Повертає None, якщо Supabase не налаштований або сталась помилка.
    """
    if not settings.supabase_enabled:
        return None

    base = settings.SUPABASE_URL
    bucket = settings.SUPABASE_BUCKET
    url = f"{base}/storage/v1/object/{bucket}/{filename}"
    headers = _supabase_headers(upload=True)
    headers["Content-Type"] = content_type

    r = await client.post(url, content=file_bytes, headers=headers)
    if r.status_code >= 300:
        return None

    # Публічний URL (бакет має бути public)
    public_url = f"{base}/storage/v1/object/public/{bucket}/{filename}"
    return public_url


async def delete_from_supabase(
    client: httpx.AsyncClient, filename: str
) -> bool:
    if not settings.supabase_enabled:
        return False
    base = settings.SUPABASE_URL
    bucket = settings.SUPABASE_BUCKET
    url = f"{base}/storage/v1/object/{bucket}/{filename}"
    headers = _supabase_headers()
    r = await client.delete(url, headers=headers)
    return r.status_code < 300


def classify_url(url: str) -> str:
    """Визначає тип треку за URL: 'youtube' або 'audio'."""
    u = url.lower()
    youtube_hosts = ("youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com")
    if any(h in u for h in youtube_hosts):
        return "youtube"
    return "audio"


def youtube_embed_url(url: str) -> str:
    """Перетворює різні формати YouTube-посилань на embed-URL для iframe."""
    import urllib.parse as up

    parsed = up.urlparse(url)
    q = up.parse_qs(parsed.query)
    host = (parsed.netloc or "").lower()

    # youtu.be/<id>
    if host.endswith("youtu.be") and parsed.path:
        vid = parsed.path.strip("/")
    # youtube.com/watch?v=<id>
    elif "v" in q:
        vid = q["v"][0]
    # youtube.com/embed/<id>
    elif "/embed/" in parsed.path:
        vid = parsed.path.split("/embed/")[1].split("/")[0]
    else:
        vid = ""
    if not vid:
        return url
    return f"https://www.youtube.com/embed/{vid}?playsinline=1"


def youtube_video_id(url: str) -> str:
    """Витягує video id з будь-якого YouTube-формату (порожньо, якщо не вдалося)."""
    import urllib.parse as up

    parsed = up.urlparse(url)
    q = up.parse_qs(parsed.query)
    host = (parsed.netloc or "").lower()
    if host.endswith("youtu.be") and parsed.path:
        return parsed.path.strip("/")
    if "v" in q:
        return q["v"][0]
    if "/embed/" in parsed.path:
        return parsed.path.split("/embed/")[1].split("/")[0]
    return ""


async def fetch_youtube_title(client: "httpx.AsyncClient", url: str) -> str:
    """Отримує назву відео через YouTube oEmbed (без ключа API).

    Повертає порожній рядок, якщо не вдалося.
    """
    vid = youtube_video_id(url)
    if not vid:
        return ""
    oembed_url = "https://www.youtube.com/oembed"
    try:
        r = await client.get(
            oembed_url,
            params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            return (data.get("title") or "").strip()
    except Exception:
        pass
    return ""

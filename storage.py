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

# Демо-треки, що постачаються з репозиторію + вбудований робочий плейлист
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
    {
        "id": "demo_focus_playlist",
        "scope": "demo",
        "kind": "youtube",
        "url": "https://music.youtube.com/playlist?list=OLAK5uy_ld7f9jlp-xLjPLIt9Q_UtzzFLwKMCETXs",
        "title": "Робоча збірка",
        "author": "Netpeak Group",
        "category": "deep_work",
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
    """Визначає тип треку за URL: 'youtube' (відео/плейлист) або 'audio'."""
    u = url.lower()
    youtube_hosts = (
        "youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com",
        "music.youtube.com",
    )
    if any(h in u for h in youtube_hosts):
        return "youtube"
    return "audio"


def youtube_playlist_id(url: str) -> str:
    """Витягує ID плейлиста з YouTube/YouTube Music URL (порожньо, якщо немає)."""
    import urllib.parse as up

    parsed = up.urlparse(url)
    q = up.parse_qs(parsed.query)
    return (q.get("list") or [""])[0]


def youtube_embed_url(url: str) -> str:
    """Перетворює різні формати YouTube-посилань на embed-URL для iframe.

    Підтримує: окреме відео, відео+плейлист, плейлист (вкл. YouTube Music).
    """
    import urllib.parse as up

    parsed = up.urlparse(url)
    q = up.parse_qs(parsed.query)
    host = (parsed.netloc or "").lower()
    playlist_id = (q.get("list") or [""])[0]

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

    # Плейлист без конкретного відео → videoseries з listType=playlist
    if not vid and playlist_id:
        return (
            f"https://www.youtube.com/embed/videoseries"
            f"?listType=playlist&list={playlist_id}&playsinline=1"
        )

    if not vid:
        return url
    embed = f"https://www.youtube.com/embed/{vid}?playsinline=1"
    if playlist_id:
        embed += f"&list={playlist_id}"
    return embed


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


async def fetch_youtube_info(client: "httpx.AsyncClient", url: str) -> dict:
    """Отримує назву та виконавця відео/плейлиста через YouTube oEmbed.

    Повертає {"title": str, "author": str} (можуть бути порожні).
    """
    import urllib.parse as up

    # плейлист (вкл. YouTube Music) → oembed з URL плейлиста
    pid = youtube_playlist_id(url)
    if pid:
        try:
            r = await client.get(
                "https://www.youtube.com/oembed",
                params={
                    "url": f"https://www.youtube.com/playlist?list={pid}",
                    "format": "json",
                },
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                title = (data.get("title") or "").strip()
                author = (data.get("author_name") or "").strip()
                # YouTube Music плейлисти: title часто "Album - X" або "Single - X",
                # author порожній. "Album"/"Single"/"EP" — це тип-префікси, не виконавці.
                if title and not author and " - " in title:
                    parts = title.split(" - ", 1)
                    first = parts[0].strip().lower()
                    type_prefixes = ("album", "single", "ep", "playlist", "mix", "compilation")
                    if first in type_prefixes:
                        # "Album - Netpeak Group" → title=Netpeak Group, author залишаємо порожнім
                        title = parts[1].strip()
                    else:
                        # "Artist - Title" → author=Artist
                        author = parts[0].strip()
                        title = parts[1].strip()
                return {"title": title, "author": author}
        except Exception:
            pass
        return {"title": "", "author": ""}

    # окреме відео
    vid = youtube_video_id(url)
    if not vid:
        return {"title": "", "author": ""}
    try:
        r = await client.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            title = (data.get("title") or "").strip()
            author = (data.get("author_name") or "").strip()
            # відео: title часто "Artist - Song", author_name вже дає артиста.
            # Якщо title починається з "Author - " → прибрати дублювання.
            if title and author and title.lower().startswith(author.lower() + " - "):
                title = title[len(author) + 3:].strip()
            # якщо author порожній, але є " - " — спробуємо розщепити
            if title and not author and " - " in title:
                parts = title.split(" - ", 1)
                author = parts[0].strip()
                title = parts[1].strip()
            return {"title": title, "author": author}
    except Exception:
        pass
    return {"title": "", "author": ""}


# Зворотна сумісність: стара функція повертає лише title
async def fetch_youtube_title(client: "httpx.AsyncClient", url: str) -> str:
    info = await fetch_youtube_info(client, url)
    return info.get("title", "")

"""Stateless JWT-автентифікація для Android-клієнта.

Реалізація JWT (RFC 7519) без зовнішніх бібліотек: HMAC-SHA256.
Використовується для самостійного Android-застосунку, де немає контексту
Telegram WebApp initData. Android-клієнт логіниться через Telegram Login Widget,
отримує підписаний Telegram payload, обмінює його на наш JWT через
/api/auth/telegram-login, і далі шле JWT у заголовку Authorization: Bearer <jwt>.

Структура payload: { tg_id, first_name, last_name, username, photo_url, iat, exp }
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from config import settings

# Термін дії токена: 30 днів (Android-клієнт зберігає в EncryptedSharedPreferences)
TOKEN_TTL = 30 * 24 * 3600
_ALG = "HS256"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_token(user: dict) -> str:
    """Створює підписаний JWT для користувача.
    user має містити: id (або tg_id), first_name, last_name, username, photo_url."""
    tg_id = int(user.get("id") or user.get("tg_id") or 0)
    header = {"alg": _ALG, "typ": "JWT"}
    now = int(time.time())
    payload = {
        "tg_id": tg_id,
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "username": user.get("username", ""),
        "photo_url": user.get("photo_url", ""),
        "iat": now,
        "exp": now + TOKEN_TTL,
    }
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = (h + "." + p).encode()
    sig = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
    return h + "." + p + "." + _b64url_encode(sig)


def verify_token(token: str) -> Optional[dict]:
    """Перевіряє підпис та термін дії JWT. Повертає словник користувача або None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    h, p, sig = parts
    try:
        signing_input = (h + "." + p).encode()
        expected = hmac.new(
            settings.jwt_secret.encode(), signing_input, hashlib.sha256
        ).digest()
        given = _b64url_decode(sig)
        if not hmac.compare_digest(expected, given):
            return None
        payload = json.loads(_b64url_decode(p))
    except Exception:
        return None
    # перевірка терміну дії
    if payload.get("exp", 0) < time.time():
        return None
    # повертаємо у форматі, сумісному з Telegram user dict
    return {
        "id": int(payload.get("tg_id", 0)),
        "tg_id": int(payload.get("tg_id", 0)),
        "first_name": payload.get("first_name", ""),
        "last_name": payload.get("last_name", ""),
        "username": payload.get("username", ""),
        "photo_url": payload.get("photo_url", ""),
    }

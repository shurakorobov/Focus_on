"""Перевірка та розбір Telegram WebApp initData.

Це дозволяє переконатись, що запит дійсно прийшов від Telegram
(а не підмінений користувачем), і витягнути дані про користувача.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
from typing import Optional

from config import settings


def verify_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    """Перевіряє підпис initData від Telegram WebApp.

    Алгоритм описаний у документації Telegram:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Повертає словник з полями користувача або None, якщо підпис невірний.
    """
    if not init_data or not bot_token:
        return None

    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        # дублювання ключів
        return None

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    # 1. data-check-string — рядок сортированих пар key=value через \n
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

    # 2. secret_key = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    # 3. hash = HMAC-SHA256(secret_key, data_check_string) у hex
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    # Витягуємо user (рядок JSON)
    user_raw = parsed.get("user")
    if not user_raw:
        return None
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        return None

    # додаємо auth_date для перевірки «свіжості»
    auth_date_raw = parsed.get("auth_date")
    if auth_date_raw and auth_date_raw.isdigit():
        user["auth_date"] = int(auth_date_raw)
    return user


def authenticate(init_data: str) -> Optional[dict]:
    """Зручна обгортка: перевіряє initData з налаштувань проєкту."""
    return verify_init_data(init_data, settings.BOT_TOKEN)

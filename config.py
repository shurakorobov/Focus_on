"""Конфігурація застосунку.

Значення беруться з переменних оточення (файл .env).
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

# Завантажуємо .env з кореня проєкту
load_dotenv()


@lru_cache
class Settings:
    """Налаштування, що читаються один раз і кешуються."""

    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
    PORT: int = int(os.getenv("PORT", "8000"))
    DB_PATH: str = os.getenv("DB_PATH", "focus.db")

    # Список Telegram ID адміністраторів (через кому): хто може додавати
    # загальну музику та завантажувати файли в хмару.
    ADMIN_IDS: tuple[int, ...] = tuple(
        int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", ",").split(",") if x.strip().isdigit()
    )

    # Supabase (для збереження завантажених треків у хмарі).
    # Якщо порожні — завантаження у хмару вимкнене, доступні лише
    # демо-треки та прямі посилання.
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    SUPABASE_BUCKET: str = os.getenv("SUPABASE_BUCKET", "tracks")

    # Telegram Stars — нативна валюта Telegram для платежів.
    # PREMIUM_PRICE_STARS — ціна місячної підписки в Stars (1 Star ≈ $0.013).
    PREMIUM_PRICE_STARS: int = int(os.getenv("PREMIUM_PRICE_STARS", "100"))
    PREMIUM_DURATION_DAYS: int = int(os.getenv("PREMIUM_DURATION_DAYS", "30"))
    # Ліміт завантажень треків для безкоштовних користувачів.
    FREE_UPLOAD_LIMIT: int = int(os.getenv("FREE_UPLOAD_LIMIT", "5"))

    # Куди слати баг-репорти (Telegram chat_id). За замовч. — перший адмін.
    ADMIN_CHAT_ID: int = int(os.getenv("ADMIN_CHAT_ID", "0")) or (
        ADMIN_IDS[0] if ADMIN_IDS else 0
    )

    @property
    def is_configured(self) -> bool:
        """Чи достатньо налаштувань для повноцінної роботи бота."""
        return bool(self.BOT_TOKEN) and bool(self.WEBAPP_URL)

    @property
    def has_token(self) -> bool:
        return bool(self.BOT_TOKEN)

    @property
    def supabase_enabled(self) -> bool:
        return bool(self.SUPABASE_URL) and bool(self.SUPABASE_KEY)

    @property
    def stars_enabled(self) -> bool:
        """Telegram Stars доступні, коли є BOT_TOKEN (бо це нативні платежі TG)."""
        return bool(self.BOT_TOKEN)

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.ADMIN_IDS


settings = Settings()

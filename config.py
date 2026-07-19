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
    # Постійна PostgreSQL БД (Neon). Якщо задано — використовуємо замість SQLite.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip()
    # Версія застосунку (SemVer). Bump вручну при релізі.
    APP_VERSION: str = os.getenv("APP_VERSION", "0.1.0-beta")
    # Секрет для підпису JWT-токенів (автентифікація Android-клієнта).
    # За замовч. деривується з BOT_TOKEN, щоб не вимагати додаткового налаштування.
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")

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

    # Cloudflare R2 — публічне сховище для ambient-звуків та демо-треків.
    # URL бакета з увімкненим публічним доступом (r2.dev або кастомний домен).
    R2_PUBLIC_URL: str = os.getenv(
        "R2_PUBLIC_URL", "https://pub-a1d59554b57543038a1128541bbdb32e.r2.dev"
    ).strip().rstrip("/")

    # Telegram Stars — нативна валюта Telegram для платежів.
    # PREMIUM_PRICE_STARS — ціна місячної підписки в Stars (1 Star ≈ $0.013).
    PREMIUM_PRICE_STARS: int = int(os.getenv("PREMIUM_PRICE_STARS", "100"))
    PREMIUM_DURATION_DAYS: int = int(os.getenv("PREMIUM_DURATION_DAYS", "30"))
    # Ліміт завантажень треків для безкоштовних користувачів.
    FREE_UPLOAD_LIMIT: int = int(os.getenv("FREE_UPLOAD_LIMIT", "5"))
    # Google Play Billing: SKU місячної підписки (використовується в Android-клієнті
    # та як дефолт product_id для /api/play/verify). Ідентифікатор має збігатись
    # з продуктом, створеним у Play Console → Products → Subscriptions.
    GOOGLE_PLAY_PREMIUM_SKU: str = os.getenv("GOOGLE_PLAY_PREMIUM_SKU", "focus_on_premium_month")

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
    def use_postgres(self) -> bool:
        """Чи використовуємо PostgreSQL (продакшн) замість SQLite (локально)."""
        return bool(self.DATABASE_URL)

    @property
    def jwt_secret(self) -> str:
        """Секрет для JWT: явний JWT_SECRET або деривація з BOT_TOKEN.
        Деривація — щоб не ламати існуючі деплої без нового env."""
        if self.JWT_SECRET:
            return self.JWT_SECRET
        if self.BOT_TOKEN:
            import hashlib
            return "fx_" + hashlib.sha256(("jwt|" + self.BOT_TOKEN).encode()).hexdigest()
        return "fx_dev_insecure_secret"

    @property
    def stars_enabled(self) -> bool:
        """Telegram Stars доступні, коли є BOT_TOKEN (бо це нативні платежі TG)."""
        return bool(self.BOT_TOKEN)

    # Google Sign-In (Android): список email-ів, які є адмінами.
    # Окремо від ADMIN_IDS (Telegram tg_id), бо Google-юзери не мають tg_id.
    ADMIN_GOOGLE_EMAILS: tuple[str, ...] = tuple(
        e.strip().lower()
        for e in os.getenv("ADMIN_GOOGLE_EMAILS", "").replace(" ", ",").split(",")
        if e.strip() and "@" in e
    )
    # Web Client ID OAuth-клієнта Google (з Google Cloud Console).
    # Потрібен для верифікації audience ID-токена на бекенді.
    GOOGLE_OAUTH_CLIENT_ID: str = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")

    def is_admin(self, tg_id: int = 0, email: str = "") -> bool:
        """Адмін за tg_id (Telegram) АБО за email (Google Sign-In)."""
        if tg_id and tg_id in self.ADMIN_IDS:
            return True
        if email and email.strip().lower() in self.ADMIN_GOOGLE_EMAILS:
            return True
        return False


settings = Settings()

"""Telegram-бот для Focus OS.

Реєструє команду /start, що показує кнопку для відкриття Mini App.

Запуск (окремо від сервера):
    python bot.py
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from config import settings

logger = logging.getLogger("focus_bot")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)

API = f"https://api.telegram.org/bot{settings.BOT_TOKEN}"


async def set_menu_button() -> None:
    """Встановлює кнопку меню бота, що відкриває Mini App."""
    if not settings.WEBAPP_URL:
        logger.warning(
            "WEBAPP_URL порожній — кнопка меню Mini App не буде встановлена. "
            "Заповніть WEBAPP_URL у .env і перезапустіть бота."
        )
        return

    payload = {
        "menu_button": {
            "type": "web_app",
            "text": "Focus OS",
            "web_app": {"url": settings.WEBAPP_URL},
        }
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{API}/setChatMenuButton", json=payload)
        data = r.json()
        if data.get("ok"):
            logger.info("✅ Кнопку меню 'Focus OS' встановлено → %s", settings.WEBAPP_URL)
        else:
            logger.error("Не вдалося встановити меню: %s", data)


async def handle_update(update: dict) -> None:
    """Обробляє одне оновлення від Telegram."""
    message = update.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    if text == "/start" or text == "/help":
        await send_welcome(chat_id)
    elif text == "/app":
        await send_app_button(chat_id)
    else:
        # на будь-яке повідомлення — підказка
        await send_welcome(chat_id)


async def send_welcome(chat_id: int) -> None:
    """Привітання з інлайн-кнопкою для відкриття застосунку."""
    if settings.WEBAPP_URL:
        payload = {
            "chat_id": chat_id,
            "text": (
                "👋 Привіт! Це **Focus OS** — твій простір для глибокої роботи.\n\n"
                "Натисни кнопку нижче, щоб відкрити застосунок 👇"
            ),
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "🚀 Открыть приложение",
                            "web_app": {"url": settings.WEBAPP_URL},
                        }
                    ]
                ]
            },
        }
    else:
        payload = {
            "chat_id": chat_id,
            "text": (
                "👋 Привіт! Це **Focus OS**.\n\n"
                "⚠️ Щоб відкрити застосунок, адміністратор має вказати "
                "WEBAPP_URL у налаштуваннях (.env)."
            ),
            "parse_mode": "Markdown",
        }

    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{API}/sendMessage", json=payload)


async def send_app_button(chat_id: int) -> None:
    await send_welcome(chat_id)


async def poll() -> None:
    """Long-polling цикл отримання оновлень."""
    offset = None
    logger.info("🤖 Бот запущено. Long polling...")
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            try:
                params: dict = {"timeout": 30}
                if offset:
                    params["offset"] = offset
                r = await client.get(f"{API}/getUpdates", params=params)
                data = r.json()
                if not data.get("ok"):
                    logger.error("getUpdates помилка: %s", data)
                    await asyncio.sleep(3)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    try:
                        await handle_update(update)
                    except Exception:
                        logger.exception("Помилка обробки update")
            except httpx.RequestError:
                logger.warning("Мережева помилка, повтор через 5с")
                await asyncio.sleep(5)
            except Exception:
                logger.exception("Неочікувана помилка в poll()")
                await asyncio.sleep(5)


async def main() -> None:
    if not settings.has_token:
        logger.error(
            "BOT_TOKEN не задано. Створіть бота через @BotFather і впишіть токен у .env"
        )
        return

    await set_menu_button()
    await poll()


if __name__ == "__main__":
    asyncio.run(main())

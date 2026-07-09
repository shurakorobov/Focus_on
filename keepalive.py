"""Фоновий автопінг, щоб Render не засинав через 15 хв без активності.

Працює лише у продакшені (коли задано WEBAPP_URL) — локально не потрібен.
Раз на 10 хв робить запит до власного /api/health.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger("keepalive")

PING_INTERVAL = 600  # 10 хвилин


async def keepalive_loop() -> None:
    """Періодично пінгує власний сервер, щоб уникнути засинання."""
    # визначаємо власний URL
    # у продакшені WEBAPP_URL або Render-URL через змінні
    target = os.getenv("WEBAPP_URL", "").rstrip("/")
    if not target:
        # Render дає URL через змінну оточення Render-сервісу
        # або можна скласти з хосту запиту — але у фоні немає request,
        # тому використовуємо localhost як запас
        port = os.getenv("PORT", "8000")
        target = f"http://127.0.0.1:{port}"

    health_url = target + "/api/health"
    logger.info("Keepalive запущено → %s кожні %dc", health_url, PING_INTERVAL)

    while True:
        await asyncio.sleep(PING_INTERVAL)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(health_url)
                logger.debug("keepalive ping: %s", r.status_code)
        except Exception as e:
            logger.debug("keepalive помилка: %s", e)


def start_keepalive() -> None:
    """Запускає keepalive у фоні (як asyncio-задачу поточного loop-у)."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(keepalive_loop())
    except RuntimeError:
        # немає активного event loop — FastAPI/uvicorn ще не стартував;
        # тоді залишимо запуск на lifespan
        pass

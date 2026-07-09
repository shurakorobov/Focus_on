"""Фоновий автопінг, щоб Render не засинав через 15 хв без активності.

Render на free-плані присипляє сервіс після ~15 хв без вхідних запитів.
Цей модуль раз на 4 хв робить запит до власного /api/health — цього
достатньо, щоб тримати сервіс активним. Додатково до само-пінгу через
PUBLIC URL (щоб «справжній» зовнішній запит активував сервіс, якщо він
вже почав засинати), робиться повторна спроба при помилці.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger("keepalive")

PING_INTERVAL = 240   # 4 хвилини — з запасом проти 15-хв таймера Render
MAX_RETRIES = 3


async def _ping(client: httpx.AsyncClient, url: str) -> bool:
    try:
        r = await client.get(url)
        ok = r.status_code < 500
        logger.debug("keepalive %s → %s", url, r.status_code)
        return ok
    except Exception as e:
        logger.debug("keepalive %s помилка: %s", url, e)
        return False


async def keepalive_loop() -> None:
    """Періодично пінгує власний сервер, щоб уникнути засинання Render.

    Стратегія: спершу пробуємо ПУБЛІЧНИЙ URL (WEBAPP_URL) — зовнішній запит
    «розбудить» Render навіть якщо він уже заснув. Запит до localhost лише
    підтримує активність усередині вже живого процесу.
    """
    # пріоритет публічних URL: WEBAPP_URL, потім RENDER_EXTERNAL_URL (авто від Render)
    public_url = (
        os.getenv("WEBAPP_URL", "").rstrip("/")
        or os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    )
    port = os.getenv("PORT", "8000")
    local_url = f"http://127.0.0.1:{port}"

    public_health = (public_url + "/api/health") if public_url else None
    local_health = local_url + "/api/health"

    targets = []
    if public_health:
        targets.append(public_health)
    targets.append(local_health)

    logger.info(
        "Keepalive запущено: %s кожні %dc",
        " + ".join(targets), PING_INTERVAL,
    )

    while True:
        await asyncio.sleep(PING_INTERVAL)
        # пінг усіх цілей з повторами при помилці
        async with httpx.AsyncClient(timeout=20) as client:
            for url in targets:
                for attempt in range(MAX_RETRIES):
                    if await _ping(client, url):
                        break
                    await asyncio.sleep(5)
                else:
                    logger.warning("keepalive: не вдалося пропінгувати %s за %d спроб", url, MAX_RETRIES)


def start_keepalive() -> None:
    """Запускає keepalive у фоні (як asyncio-задачу поточного loop-у)."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(keepalive_loop())
    except RuntimeError:
        # немає активного event loop — FastAPI/uvicorn ще не стартував;
        # тоді залишимо запуск на lifespan
        pass

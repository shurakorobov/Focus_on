"""SQLite сховище для сесій фокусу.

Схема:
- users:    профілі користувачів (tg id, ім'я, аватар, налаштування)
- sessions: завершені сесії фокусу (режим, тривалість, дата)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from config import settings

# Режими фокусу та їхні тривалості за замовчуванням (у секундах)
FOCUS_MODES = {
    "deep_work": {"label": "Deep Work", "duration": 50 * 60, "color": "#7C5CFC"},
    "focus": {"label": "Focus", "duration": 25 * 60, "color": "#3DDC97"},
    "short": {"label": "Short Focus", "duration": 15 * 60, "color": "#FFB454"},
    "break": {"label": "Break", "duration": 5 * 60, "color": "#FF6B6B"},
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Контекстний менеджер для з'єднання з БД."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Створює таблиці, якщо їх ще немає."""
    Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id        INTEGER PRIMARY KEY,
                first_name   TEXT NOT NULL DEFAULT '',
                last_name    TEXT NOT NULL DEFAULT '',
                username     TEXT NOT NULL DEFAULT '',
                photo_url    TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                total_focus_seconds INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id        INTEGER NOT NULL,
                mode         TEXT NOT NULL,
                planned      INTEGER NOT NULL,   -- запланована тривалість, сек
                actual       INTEGER NOT NULL,   -- фактична тривалість, сек
                completed    INTEGER NOT NULL DEFAULT 0,  -- 1 = доведено до кінця
                started_at   TEXT NOT NULL,
                finished_at  TEXT NOT NULL,
                FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_tg ON sessions(tg_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);

            -- Музика. scope: 'admin' (доступна всім) або 'user' (особиста).
            -- kind:  'audio' (пряме посилання .mp3/.m4a/.ogg) або 'youtube' (embed).
            CREATE TABLE IF NOT EXISTS tracks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id        INTEGER NOT NULL,          -- хто додав
                scope        TEXT NOT NULL,             -- 'admin' | 'user'
                kind         TEXT NOT NULL,             -- 'audio' | 'youtube'
                url          TEXT NOT NULL,
                title        TEXT NOT NULL DEFAULT '',
                author       TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tracks_scope ON tracks(scope);
            CREATE INDEX IF NOT EXISTS idx_tracks_tg ON tracks(tg_id);
            """
        )


# ----------------------------- користувачі ----------------------------------


def upsert_user(
    tg_id: int,
    first_name: str = "",
    last_name: str = "",
    username: str = "",
    photo_url: str = "",
) -> None:
    """Створює або оновлює профіль користувача."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (tg_id, first_name, last_name, username, photo_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name  = excluded.last_name,
                username   = excluded.username,
                photo_url  = excluded.photo_url
            """,
            (
                tg_id,
                first_name,
                last_name,
                username,
                photo_url,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_user(tg_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        return dict(row) if row else None


# ------------------------------ сесії ---------------------------------------


def save_session(
    tg_id: int,
    mode: str,
    planned: int,
    actual: int,
    completed: bool,
    started_at: str,
) -> None:
    """Зберігає завершену сесію фокусу та оновлює підсумки користувача."""
    finished_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sessions (tg_id, mode, planned, actual, completed, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tg_id, mode, planned, actual, int(completed), started_at, finished_at),
        )
        # рахуємо в загальний час лише успішно завершені сесії
        if completed:
            conn.execute(
                "UPDATE users SET total_focus_seconds = total_focus_seconds + ? WHERE tg_id = ?",
                (actual, tg_id),
            )


def get_stats(tg_id: int, limit: int = 50) -> dict:
    """Повертає статистику користувача для екрана Stats."""
    with get_conn() as conn:
        total_row = conn.execute(
            "SELECT COALESCE(SUM(actual), 0) AS s, COUNT(*) AS c FROM sessions WHERE tg_id = ? AND completed = 1",
            (tg_id,),
        ).fetchone()
        total_seconds = int(total_row["s"])
        total_sessions = int(total_row["c"])

        today = datetime.now(timezone.utc).date().isoformat()
        today_row = conn.execute(
            "SELECT COALESCE(SUM(actual), 0) AS s FROM sessions WHERE tg_id = ? AND completed = 1 AND date(finished_at) = ?",
            (tg_id, today),
        ).fetchone()
        today_seconds = int(today_row["s"])

        by_mode = conn.execute(
            """
            SELECT mode, COUNT(*) AS c, COALESCE(SUM(actual), 0) AS s
            FROM sessions
            WHERE tg_id = ? AND completed = 1
            GROUP BY mode
            ORDER BY s DESC
            """,
            (tg_id,),
        ).fetchall()

        recent = conn.execute(
            """
            SELECT mode, actual, completed, finished_at
            FROM sessions
            WHERE tg_id = ?
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            (tg_id, limit),
        ).fetchall()

    return {
        "total_seconds": total_seconds,
        "total_sessions": total_sessions,
        "today_seconds": today_seconds,
        "by_mode": [dict(r) for r in by_mode],
        "recent": [dict(r) for r in recent],
        "modes": FOCUS_MODES,
    }


# ------------------------------ музика --------------------------------------


def add_track(
    tg_id: int,
    scope: str,
    kind: str,
    url: str,
    title: str = "",
    author: str = "",
) -> int:
    """Додає трек (адмінський або особистий). Повертає id нового запису."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tracks (tg_id, scope, kind, url, title, author, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tg_id, scope, kind, url, title, author, datetime.now(timezone.utc).isoformat()),
        )
        return int(cur.lastrowid)


def list_tracks(tg_id: int) -> list[dict]:
    """Повертає треки, доступні користувачу: усі адмінські + його особисті."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, tg_id, scope, kind, url, title, author, created_at
            FROM tracks
            WHERE scope = 'admin' OR tg_id = ?
            ORDER BY scope DESC, created_at DESC
            """,
            (tg_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_track(tg_id: int, track_id: int, is_admin: bool) -> bool:
    """Видаляє трек. Адмін може видаляти будь-які; юзер — лише свої."""
    with get_conn() as conn:
        if is_admin:
            cur = conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        else:
            cur = conn.execute(
                "DELETE FROM tracks WHERE id = ? AND tg_id = ? AND scope = 'user'",
                (track_id, tg_id),
            )
        return cur.rowcount > 0


def rename_track(
    tg_id: int, track_id: int, title: str, is_admin: bool
) -> bool:
    """Перейменовує трек. Адмін — будь-який; юзер — лише свій особистий."""
    title = (title or "").strip()
    with get_conn() as conn:
        if is_admin:
            cur = conn.execute(
                "UPDATE tracks SET title = ? WHERE id = ?", (title, track_id)
            )
        else:
            cur = conn.execute(
                "UPDATE tracks SET title = ? WHERE id = ? AND tg_id = ? AND scope = 'user'",
                (title, track_id, tg_id),
            )
        return cur.rowcount > 0


def is_track_owner_or_admin(tg_id: int, track_id: int, is_admin: bool) -> bool:
    if is_admin:
        return True
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM tracks WHERE id = ? AND tg_id = ?", (track_id, tg_id)
        ).fetchone()
        return row is not None

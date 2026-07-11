"""Сховище для сесій фокусу (SQLite локально / PostgreSQL продакшн).

Шар сумісності: код пише SQL з ?-плейсхолдерами (SQLite-style), а під
капотом — автоматичний переклад під обраний backend через DATABASE_URL.
Підтримує: INSERT OR IGNORE, date(...), INTEGER PRIMARY KEY AUTOINCREMENT.

Схема:
- users:           профілі користувачів (tg id, ім'я, аватар, тариф)
- sessions:        завершені сесії фокусу (режим, тривалість, категорія, дата)
- tracks:          музичні треки (scope demo/admin/user, kind audio/youtube)
- track_user_meta: бажане/закріплення треків на користувача
- bug_reports:     звіти про баги
- payments:        лог платежів (Telegram Stars)
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

# Callback-хук для real-time сповіщень (SSE). app.py реєструє його при старті.
_on_data_changed = None


def set_change_callback(fn):
    """Реєструє callback який викликається при зміні даних (для SSE pub/sub)."""
    global _on_data_changed
    _on_data_changed = fn


def _notify_changed():
    if _on_data_changed:
        try:
            _on_data_changed()
        except Exception:
            pass

from config import settings

# Режими фокусу та їхні тривалості за замовчуванням (у секундах)
FOCUS_MODES = {
    "deep_work": {"label": "Deep Work", "duration": 50 * 60, "color": "#7C5CFC"},
    "focus": {"label": "Focus", "duration": 25 * 60, "color": "#3DDC97"},
    "short": {"label": "Short Focus", "duration": 15 * 60, "color": "#FFB454"},
    "break": {"label": "Break", "duration": 5 * 60, "color": "#FF6B6B"},
}

# Категорії діяльності ("над чим працюємо?")
CATEGORIES = {
    "deep_work": {"label": "DeepWork", "emoji": "🧠", "color": "#bf5af2"},
    "creative": {"label": "Креатив", "emoji": "🎨", "color": "#ff9f0a"},
    "learning": {"label": "Навчання", "emoji": "📚", "color": "#0a84ff"},
    "reading": {"label": "Читання", "emoji": "📖", "color": "#64d2ff"},
    "training": {"label": "Тренування", "emoji": "💪", "color": "#30d158"},
    "other": {"label": "Інше", "emoji": "✨", "color": "#8e8e93"},
}


# ---------------------- Шар сумісності SQLite/PostgreSQL ----------------------

# Прапорець: True = PostgreSQL (psycopg2), False = SQLite (sqlite3)
_IS_PG = settings.use_postgres


def _translate_sql(sql: str) -> str:
    """Перекладає SQL з SQLite-діалекту під поточний backend.
    Для PostgreSQL: ? → %s, INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING,
    date(x) → (x)::date, AUTOINCREMENT→SERIAL, tg_id INTEGER→tg_id BIGINT."""
    if not _IS_PG:
        return sql  # SQLite — як є
    out = sql.replace("?", "%s")
    # INSERT OR IGNORE INTO → INSERT INTO
    out = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", out, flags=re.IGNORECASE)
    # date(col) → (col)::date
    out = re.sub(r"\bdate\((\w+)\)", r"(\1)::date", out)
    # AUTOINCREMENT не підтримується PG — SERIAL зробить автоінкремент
    out = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", out, flags=re.IGNORECASE)
    # tg_id INTEGER → tg_id BIGINT (Telegram ID > 2.1 млрд, не вміщується в INTEGER)
    out = re.sub(r"\btg_id\s+INTEGER\b", "tg_id BIGINT", out, flags=re.IGNORECASE)
    return out


class _PGWrapper:
    """Обгортка psycopg2-курсора/з'єднання під інтерфейс, сумісний зі sqlite3.
    Підтримує: execute(sql, params), fetchone/fetchall (dict-like row), executescript."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()):
        cur = self._conn.cursor()
        cur.execute(_translate_sql(sql), params)
        self._last_cur = cur
        return cur

    def executescript(self, script: str):
        # PG не підтримує executescript — розділяємо по ';' та виконуємо по черзі.
        # Розділяємо обережно (простий спліт, бо наші DDL не містять ';' у рядках).
        cur = self._conn.cursor()
        for stmt in _split_sql_script(script):
            stmt = stmt.strip()
            if stmt:
                cur.execute(_translate_sql(stmt))
        self._last_cur = cur
        return cur

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, val):
        # ігноруємо — PG завжди повертає dict через RealDictCursor
        pass

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _split_sql_script(script: str) -> list[str]:
    """Розділяє SQL-скрипт на окремі стейтменти по ';'.
    Ігнорує порожні та коментарі (--)."""
    stmts = []
    for raw in script.split(";"):
        s = "\n".join(
            line for line in raw.splitlines()
            if not line.strip().startswith("--")
        ).strip()
        if s:
            stmts.append(s)
    return stmts


def _connect_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _connect_pg():
    """Підключення до PostgreSQL через DATABASE_URL (Neon).
    connect_timeout=30 — Neon scale-to-zero: перший запит ~5-15с на пробудження."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(settings.DATABASE_URL, cursor_factory=RealDictCursor,
                            connect_timeout=30)
    conn.autocommit = False
    return _PGWrapper(conn)


@contextmanager
def get_conn():
    """Контекстний менеджер для з'єднання з БД (SQLite або PostgreSQL)."""
    conn = _connect_pg() if _IS_PG else _connect_sqlite()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _last_id(conn, cur, table: str) -> int:
    """Повертає id щойно вставленого запису.
    SQLite: cur.lastrowid. PostgreSQL: lastval() поточної сесії."""
    if not _IS_PG:
        return int(cur.lastrowid)
    # PG: lastval() повертає останнє значення будь-якої послідовності в цій сесії
    seq_cur = conn.execute("SELECT lastval() AS id")
    row = seq_cur.fetchone()
    return int(row["id"]) if row and row["id"] else 0


def _add_column(conn, table: str, column: str, decl: str) -> None:
    """Додає колонку, якщо її ще немає (ідемпотентно). SQLite: PRAGMA, PG: info_schema."""
    if _IS_PG:
        # PG: для NOT NULL DEFAULT колонок спершу вставляємо з DEFAULT, потім NOT NULL
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        cols = {row["column_name"] for row in cur.fetchall()}
        if column not in cols:
            # PG не дозволяє ADD COLUMN ... NOT NULL без DEFAULT для непорожньої таблиці,
            # але з DEFAULT це OK. Прибираємо "NOT NULL" з decl для безпечності.
            safe_decl = re.sub(r"\bNOT\s+NULL\s+", "", decl, flags=re.IGNORECASE)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {safe_decl}")
    else:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _pg_alter_to_bigint(conn) -> None:
    """PostgreSQL: конвертує tg_id з INTEGER у BIGINT у всіх таблицях.
    Telegram user IDs > 2.1 млрд не вміщаються в INTEGER."""
    # знаходимо всі таблиці з колонкою tg_id типу integer
    cur = conn.execute(
        """
        SELECT table_name FROM information_schema.columns
        WHERE column_name = 'tg_id' AND data_type = 'integer'
        """
    )
    tables = [row["table_name"] for row in cur.fetchall()]
    for table in tables:
        try:
            conn.execute(f"ALTER TABLE {table} ALTER COLUMN tg_id TYPE BIGINT")
        except Exception:
            pass  # вже BIGINT або інша помилка — ідемпотентно


def migrate() -> None:
    """Безпечна міграція схеми під існуючу базу."""
    with get_conn() as conn:
        _add_column(conn, "tracks", "category", "TEXT NOT NULL DEFAULT 'other'")
        _add_column(conn, "sessions", "category", "TEXT NOT NULL DEFAULT 'other'")
        _add_column(conn, "users", "plan", "TEXT NOT NULL DEFAULT 'free'")
        _add_column(conn, "users", "plan_expires_at", "TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "users", "upload_count", "INTEGER NOT NULL DEFAULT 0")
        # Щоденна ціль фокусу (за замовч. 2 години), для відображення на головному екрані
        _add_column(conn, "users", "daily_goal_seconds", "INTEGER NOT NULL DEFAULT 7200")
        # PostgreSQL: tg_id INTEGER → BIGINT (Telegram ID > 2.1 млрд)
        if _IS_PG:
            _pg_alter_to_bigint(conn)

        conn.executescript(
            """
            -- Бажане / закріплені треки + призначення категорії на користувача.
            CREATE TABLE IF NOT EXISTS track_user_meta (
                tg_id        INTEGER NOT NULL,
                track_key    TEXT NOT NULL,        -- 'demo:<id>' | 'db:<id>'
                favorite     INTEGER NOT NULL DEFAULT 0,
                pinned       INTEGER NOT NULL DEFAULT 0,
                category     TEXT NOT NULL DEFAULT 'other',
                created_at   TEXT NOT NULL,
                PRIMARY KEY (tg_id, track_key),
                FOREIGN KEY (tg_id) REFERENCES users(tg_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS bug_reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id        INTEGER,
                message      TEXT NOT NULL DEFAULT '',
                user_agent   TEXT NOT NULL DEFAULT '',
                ip           TEXT NOT NULL DEFAULT '',
                region       TEXT NOT NULL DEFAULT '',
                platform     TEXT NOT NULL DEFAULT '',
                screen       TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id        INTEGER NOT NULL,
                order_id     TEXT NOT NULL,
                amount       REAL NOT NULL DEFAULT 0,
                status       TEXT NOT NULL DEFAULT '',
                raw          TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL
            );

            -- Статистика прослуховувань треків
            CREATE TABLE IF NOT EXISTS plays (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id        INTEGER NOT NULL,
                track_key    TEXT NOT NULL,
                title        TEXT NOT NULL DEFAULT '',
                source       TEXT NOT NULL DEFAULT '',
                duration     INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL
            );

            -- Промокоди
            CREATE TABLE IF NOT EXISTS promo_codes (
                code         TEXT PRIMARY KEY,
                days         INTEGER NOT NULL DEFAULT 30,
                max_uses     INTEGER NOT NULL DEFAULT 0,    -- 0 = безліміт
                used_count   INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                created_by   BIGINT NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS promo_uses (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                code         TEXT NOT NULL,
                tg_id        BIGINT NOT NULL,
                used_at      TEXT NOT NULL,
                UNIQUE(code, tg_id)
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_category ON sessions(category);
            CREATE INDEX IF NOT EXISTS idx_sessions_finished ON sessions(finished_at);
            CREATE INDEX IF NOT EXISTS idx_tracks_category ON tracks(category);
            CREATE INDEX IF NOT EXISTS idx_track_meta_tg ON track_user_meta(tg_id);
            CREATE INDEX IF NOT EXISTS idx_payments_tg ON payments(tg_id);
            CREATE INDEX IF NOT EXISTS idx_plays_tg ON plays(tg_id);
            CREATE INDEX IF NOT EXISTS idx_plays_track ON plays(track_key);
            """
        )


def init_db() -> None:
    """Створює таблиці, якщо їх ще немає, і виконує міграції."""
    if not _IS_PG:
        # SQLite: створити директорію для файлу БД
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
    migrate()
    seed_demo_tracks()


def seed_demo_tracks() -> None:
    """Вставляє вбудовані демо-треки (scope='demo'), якщо їх ще немає.
    Демо-треки тепер живуть у БД — адмін може їх додавати/видаляти."""
    demos = [
        ("demo_deep_calm", "audio", "/static/tracks/deep_calm.wav",
         "Deep Calm", "Focus OS Demo", "other"),
        ("demo_pulse_focus", "audio", "/static/tracks/pulse_focus.wav",
         "Pulse Focus", "Focus OS Demo", "other"),
        ("demo_focus_playlist", "youtube",
         "https://music.youtube.com/playlist?list=OLAK5uy_ld7f9jlp-xLjPLIt9Q_UtzzFLwKMCETXs",
         "Робоча збірка", "Netpeak Group", "deep_work"),
    ]
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        # системний користувач tg_id=0 для демо/адмін-треків (щоб FK спрацював)
        exists_user = conn.execute("SELECT 1 FROM users WHERE tg_id = ?", (0,)).fetchone()
        if not exists_user:
            conn.execute(
                "INSERT INTO users (tg_id, first_name, created_at) VALUES (0, 'Focus OS', ?)",
                (now,),
            )
        # вставляємо лише якщо такого demo-треку ще немає (по url)
        for demo_id, kind, url, title, author, category in demos:
            exists = conn.execute(
                "SELECT 1 FROM tracks WHERE scope = 'demo' AND url = ?", (url,)
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO tracks (tg_id, scope, kind, url, title, author, created_at, category)
                    VALUES (0, 'demo', ?, ?, ?, ?, ?, ?)
                    """,
                    (kind, url, title, author, now, category),
                )


def track_key(t: dict) -> str:
    """Уніфікований ключ треку: 'demo:<id>' для демо, 'db:<id>' для БД-треку."""
    scope = t.get("scope", "")
    if scope == "demo":
        return f"demo:{t.get('id')}"
    return f"db:{t.get('id')}"


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
    _notify_changed()


def get_user(tg_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        return dict(row) if row else None


# ------------------------------ тариф ---------------------------------------


def get_user_plan(tg_id: int) -> dict:
    """Повертає {plan, is_premium, plan_expires_at}."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT plan, plan_expires_at FROM users WHERE tg_id = ?", (tg_id,)
        ).fetchone()
    if not row:
        return {"plan": "free", "is_premium": False, "plan_expires_at": ""}
    return {"plan": row["plan"], "is_premium": _is_premium_row(row), "plan_expires_at": row["plan_expires_at"] or ""}


def _is_premium_row(row) -> bool:
    if row["plan"] != "premium":
        return False
    expires = row["plan_expires_at"] or ""
    if not expires:
        return True  # без обмеження в часі
    try:
        return datetime.fromisoformat(expires) > datetime.now(timezone.utc)
    except ValueError:
        return True


def is_premium(tg_id: int) -> bool:
    return get_user_plan(tg_id)["is_premium"]


def set_user_plan(tg_id: int, plan: str, days: int = 0) -> str:
    """Встановлює тариф. Якщо plan='premium' і days>0 — виставляє термін дії.
    Повертає рядок дати завершення ISO (або '')."""
    expires = ""
    if plan == "premium" and days > 0:
        # подовжуємо з поточного моменту (або з кінця попередньої підписки)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT plan_expires_at FROM users WHERE tg_id = ?", (tg_id,)
            ).fetchone()
            prev = row["plan_expires_at"] if row else ""
        base = datetime.now(timezone.utc)
        if prev:
            try:
                prev_dt = datetime.fromisoformat(prev)
                if prev_dt > base:
                    base = prev_dt
            except ValueError:
                pass
        expires = (base + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET plan = ?, plan_expires_at = ? WHERE tg_id = ?",
            (plan, expires, tg_id),
        )
    return expires


# ------------------------------ сесії ---------------------------------------


def save_session(
    tg_id: int,
    mode: str,
    planned: int,
    actual: int,
    completed: bool,
    started_at: str,
    category: str = "other",
) -> None:
    """Зберігає завершену сесію фокусу та оновлює підсумки користувача."""
    category = category if category in CATEGORIES else "other"
    finished_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sessions (tg_id, mode, planned, actual, completed, started_at, finished_at, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tg_id, mode, planned, actual, int(completed), started_at, finished_at, category),
        )
        # рахуємо в загальний час лише успішно завершені сесії
        if completed:
            conn.execute(
                "UPDATE users SET total_focus_seconds = total_focus_seconds + ? WHERE tg_id = ?",
                (actual, tg_id),
            )
    _notify_changed()


def get_stats(tg_id: int, limit: int = 50) -> dict:
    """Повертає базову статистику користувача (безкоштовний рівень)."""
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
            SELECT mode, actual, completed, finished_at, category
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


def _compute_streak(conn, tg_id: int) -> tuple[int, int]:
    """Обчислює поточну та найкращу серію (streak) по днях із завершеними
    сесіями. Повертає (current_streak, best_streak). Спільна логіка для
    безкоштовного (головний екран) та преміум (деталі) шляхів."""
    days = conn.execute(
        """
        SELECT DISTINCT date(finished_at) AS d
        FROM sessions WHERE tg_id = ? AND completed = 1
        ORDER BY d DESC
        """,
        (tg_id,),
    ).fetchall()

    # поточна серія: дозволяємо «сьогодні ще немає сесії» → починаємо з учора
    streak = 0
    today_date = datetime.now(timezone.utc).date()
    day_set = {row["d"] for row in days}
    cursor = today_date
    if today_date.isoformat() not in day_set:
        cursor = today_date - timedelta(days=1)
    while cursor.isoformat() in day_set:
        streak += 1
        cursor = cursor - timedelta(days=1)

    # найкраща серія: найдовший ряд послідовних днів
    best_streak = 0
    if days:
        sorted_days = sorted({row["d"] for row in days})
        cur = 1
        best_streak = 1
        for i in range(1, len(sorted_days)):
            prev = datetime.fromisoformat(sorted_days[i - 1]).date()
            this = datetime.fromisoformat(sorted_days[i]).date()
            if (this - prev).days == 1:
                cur += 1
                best_streak = max(best_streak, cur)
            else:
                cur = 1

    return streak, best_streak


def get_daily_progress(tg_id: int) -> dict:
    """Прогрес на сьогодні + серія для головного екрана (безкоштовно).
    Повертає {today_seconds, daily_goal_seconds, streak, best_streak}."""
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        today_row = conn.execute(
            "SELECT COALESCE(SUM(actual), 0) AS s FROM sessions WHERE tg_id = ? AND completed = 1 AND date(finished_at) = ?",
            (tg_id, today),
        ).fetchone()
        goal_row = conn.execute(
            "SELECT daily_goal_seconds FROM users WHERE tg_id = ?", (tg_id,)
        ).fetchone()
        streak, best_streak = _compute_streak(conn, tg_id)

    goal = int(goal_row["daily_goal_seconds"]) if goal_row and goal_row["daily_goal_seconds"] else 7200
    return {
        "today_seconds": int(today_row["s"]),
        "daily_goal_seconds": goal,
        "streak": streak,
        "best_streak": best_streak,
    }


def set_daily_goal(tg_id: int, seconds: int) -> int:
    """Встановлює щоденну ціль фокусу (у секундах). Повертає нове значення."""
    seconds = max(300, min(seconds, 24 * 3600))  # обмеження 5хв..24год
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET daily_goal_seconds = ? WHERE tg_id = ?",
            (seconds, tg_id),
        )
    return seconds


def _range_start(range_key: str) -> datetime | None:
    """Повертає початок періоду (UTC) або None = весь час."""
    now = datetime.now(timezone.utc)
    if range_key == "week":
        return now - timedelta(days=7)
    if range_key == "month":
        return now - timedelta(days=30)
    if range_key == "year":
        return now - timedelta(days=365)
    return None  # all


def get_stats_premium(tg_id: int, range_key: str = "month", category: str | None = None) -> dict:
    """Преміум-статистика: за категоріями, по днях (для графіка), серії."""
    start = _range_start(range_key)

    def time_clause() -> tuple[str, tuple]:
        params: tuple = (tg_id,)
        sql = "tg_id = ? AND completed = 1"
        if start is not None:
            sql += " AND finished_at >= ?"
            params = (tg_id, start.isoformat())
        if category:
            sql += " AND category = ?"
            params = params + (category,)
        return sql, params

    with get_conn() as conn:
        sql, params = time_clause()
        by_category = conn.execute(
            f"""
            SELECT category, COUNT(*) AS c, COALESCE(SUM(actual), 0) AS s
            FROM sessions WHERE {sql}
            GROUP BY category ORDER BY s DESC
            """,
            params,
        ).fetchall()

        sql2, params2 = time_clause()
        by_day = conn.execute(
            f"""
            SELECT date(finished_at) AS d, COALESCE(SUM(actual), 0) AS s, COUNT(*) AS c
            FROM sessions WHERE {sql2}
            GROUP BY d ORDER BY d ASC
            """,
            params2,
        ).fetchall()

        sql3, params3 = time_clause()
        total_row = conn.execute(
            f"SELECT COALESCE(SUM(actual),0) AS s, COUNT(*) AS c FROM sessions WHERE {sql3}",
            params3,
        ).fetchone()

        # повна історія (преміум)
        sql4, params4 = time_clause()
        history = conn.execute(
            f"""
            SELECT mode, actual, completed, finished_at, category, started_at
            FROM sessions WHERE tg_id = ?
            ORDER BY finished_at DESC LIMIT 500
            """,
            (tg_id,),
        ).fetchall()

        # серія (streak) — спільна логіка з безкоштовним шляхом
        streak, best_streak = _compute_streak(conn, tg_id)

    return {
        "range": range_key,
        "category": category,
        "total_seconds": int(total_row["s"]),
        "total_sessions": int(total_row["c"]),
        "by_category": [dict(r) for r in by_category],
        "by_day": [dict(r) for r in by_day],
        "history": [dict(r) for r in history],
        "current_streak": streak,
        "best_streak": best_streak,
        "categories": CATEGORIES,
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
    category: str = "other",
) -> int:
    """Додає трек (адмінський або особистий). Повертає id нового запису."""
    category = category if category in CATEGORIES else "other"
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tracks (tg_id, scope, kind, url, title, author, created_at, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tg_id, scope, kind, url, title, author, datetime.now(timezone.utc).isoformat(), category),
        )
        if scope == "user":
            conn.execute(
                "UPDATE users SET upload_count = upload_count + 1 WHERE tg_id = ?",
                (tg_id,),
            )
        return _last_id(conn, cur, "tracks")


def _load_meta(conn: sqlite3.Connection, tg_id: int) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT track_key, favorite, pinned, category FROM track_user_meta WHERE tg_id = ?",
        (tg_id,),
    ).fetchall()
    return {row["track_key"]: dict(row) for row in rows}


def _ensure_meta(conn, tg_id: int, key: str) -> None:
    conn.execute(
        """
        INSERT INTO track_user_meta (tg_id, track_key, favorite, pinned, category, created_at)
        VALUES (?, ?, 0, 0, 'other', ?)
        ON CONFLICT (tg_id, track_key) DO NOTHING
        """,
        (tg_id, key, datetime.now(timezone.utc).isoformat()),
    )


def list_tracks(tg_id: int, category: str | None = None) -> list[dict]:
    """Повертає треки, доступні користувачу: демо + усі адмінські + його особисті.
    Додає поля is_favorite, is_pinned, track_key з track_user_meta."""
    cat_sql = ""
    params: tuple = ()
    if category and category != "all":
        cat_sql = " AND category = ?"
        params = (category,)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, tg_id, scope, kind, url, title, author, created_at, category
            FROM tracks
            WHERE (scope IN ('demo','admin') OR tg_id = ?){cat_sql}
            ORDER BY
              CASE scope WHEN 'demo' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
              created_at DESC
            """,
            (tg_id,) + params,
        ).fetchall()
        meta = _load_meta(conn, tg_id)
    result = []
    for r in rows:
        d = dict(r)
        key = track_key(d)
        m = meta.get(key, {})
        d["track_key"] = key
        d["is_favorite"] = bool(m.get("favorite", 0))
        d["is_pinned"] = bool(m.get("pinned", 0))
        result.append(d)
    return result


def set_track_category_for_user(tg_id: int, key: str, category: str) -> None:
    """Призначає категорію треку в межах користувача (для демо/адмінських треків)."""
    category = category if category in CATEGORIES else "other"
    with get_conn() as conn:
        _ensure_meta(conn, tg_id, key)
        conn.execute(
            "UPDATE track_user_meta SET category = ? WHERE tg_id = ? AND track_key = ?",
            (category, tg_id, key),
        )


def toggle_favorite(tg_id: int, key: str) -> bool:
    """Перемикає «бажане». Повертає новий стан."""
    with get_conn() as conn:
        _ensure_meta(conn, tg_id, key)
        conn.execute(
            """
            UPDATE track_user_meta SET favorite = CASE favorite WHEN 1 THEN 0 ELSE 1 END
            WHERE tg_id = ? AND track_key = ?
            """,
            (tg_id, key),
        )
        row = conn.execute(
            "SELECT favorite FROM track_user_meta WHERE tg_id = ? AND track_key = ?",
            (tg_id, key),
        ).fetchone()
    return bool(row["favorite"]) if row else False


def toggle_pin(tg_id: int, key: str) -> bool:
    """Перемикає «закріпити». Повертає новий стан."""
    with get_conn() as conn:
        _ensure_meta(conn, tg_id, key)
        conn.execute(
            """
            UPDATE track_user_meta SET pinned = CASE pinned WHEN 1 THEN 0 ELSE 1 END
            WHERE tg_id = ? AND track_key = ?
            """,
            (tg_id, key),
        )
        row = conn.execute(
            "SELECT pinned FROM track_user_meta WHERE tg_id = ? AND track_key = ?",
            (tg_id, key),
        ).fetchone()
    return bool(row["pinned"]) if row else False


def list_favorites(tg_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT track_key FROM track_user_meta WHERE tg_id = ? AND favorite = 1",
            (tg_id,),
        ).fetchall()
    return [r["track_key"] for r in rows]


def list_pinned(tg_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT track_key FROM track_user_meta WHERE tg_id = ? AND pinned = 1",
            (tg_id,),
        ).fetchall()
    return [r["track_key"] for r in rows]


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


def get_upload_count(tg_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT upload_count FROM users WHERE tg_id = ?", (tg_id,)
        ).fetchone()
    return int(row["upload_count"]) if row else 0


# ------------------------------ баг-репорти ---------------------------------


def save_bug_report(
    tg_id: int,
    message: str,
    user_agent: str = "",
    ip: str = "",
    region: str = "",
    platform: str = "",
    screen: str = "",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO bug_reports (tg_id, message, user_agent, ip, region, platform, screen, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tg_id,
                message,
                user_agent,
                ip,
                region,
                platform,
                screen,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return _last_id(conn, cur, "bug_reports")


# ------------------------------ платежі -------------------------------------


def record_payment(
    tg_id: int, order_id: str, amount: float, status: str, raw: str = ""
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO payments (tg_id, order_id, amount, status, raw, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tg_id, order_id, amount, status, raw, datetime.now(timezone.utc).isoformat()),
        )
        return _last_id(conn, cur, "payments")
    # NOTE: _notify_changed() викликається у app.py після обробки платежу


# ------------------------------ статистика прослуховувань -------------------


def record_play(
    tg_id: int, track_key: str, title: str = "", source: str = "webview", duration: int = 0
) -> int:
    """Записує факт прослуховування треку."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO plays (tg_id, track_key, title, source, duration, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tg_id, track_key, title, source, duration, datetime.now(timezone.utc).isoformat()),
        )
        return _last_id(conn, cur, "plays")
    # не сповіщаємо — це занадто часто (кожен плей)


def get_play_stats(tg_id: int | None = None, limit: int = 20) -> dict:
    """Статистика прослуховувань для користувача або загальна (tg_id=None)."""
    with get_conn() as conn:
        if tg_id:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM plays WHERE tg_id = ?", (tg_id,)
            ).fetchone()["c"]
            recent = conn.execute(
                "SELECT track_key, title, source, created_at FROM plays WHERE tg_id = ? ORDER BY created_at DESC LIMIT ?",
                (tg_id, limit),
            ).fetchall()
            by_source = conn.execute(
                "SELECT source, COUNT(*) AS c FROM plays WHERE tg_id = ? GROUP BY source",
                (tg_id,),
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) AS c FROM plays").fetchone()["c"]
            recent = conn.execute(
                "SELECT track_key, title, source, created_at FROM plays ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            by_source = conn.execute(
                "SELECT source, COUNT(*) AS c FROM plays GROUP BY source"
            ).fetchall()
    return {
        "total": int(total),
        "recent": [dict(r) for r in recent],
        "by_source": [dict(r) for r in by_source],
    }


# ------------------------------ промокоди ----------------------------------


def redeem_promo_code(tg_id: int, code: str) -> dict:
    """Активує промокод для користувача. Повертає {ok, days, error?}."""
    code = (code or "").strip().upper()
    if not code:
        return {"ok": False, "error": "Введи код"}
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        promo = conn.execute(
            "SELECT code, days, max_uses, used_count FROM promo_codes WHERE code = ?",
            (code,),
        ).fetchone()
        if not promo:
            return {"ok": False, "error": "Невірний код"}
        if promo["max_uses"] > 0 and promo["used_count"] >= promo["max_uses"]:
            return {"ok": False, "error": "Код вичерпано"}
        already = conn.execute(
            "SELECT 1 FROM promo_uses WHERE code = ? AND tg_id = ?", (code, tg_id)
        ).fetchone()
        if already:
            return {"ok": False, "error": "Вже активовано"}
        # активуємо
        conn.execute(
            "INSERT INTO promo_uses (code, tg_id, used_at) VALUES (?, ?, ?)",
            (code, tg_id, now),
        )
        conn.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
            (code,),
        )
    expires = set_user_plan(tg_id, "premium", days=promo["days"])
    return {"ok": True, "days": promo["days"], "expires": expires}


def add_promo_code(code: str, days: int, max_uses: int, created_by: int) -> dict:
    """Створює промокод (адмін). Повертає {ok, error?}."""
    code = (code or "").strip().upper()
    if not code or len(code) < 3:
        return {"ok": False, "error": "Код закороткий (мін 3 символи)"}
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT 1 FROM promo_codes WHERE code = ?", (code,)
        ).fetchone()
        if exists:
            return {"ok": False, "error": "Код вже існує"}
        conn.execute(
            "INSERT INTO promo_codes (code, days, max_uses, used_count, created_at, created_by) VALUES (?, ?, ?, 0, ?, ?)",
            (code, days, max_uses, now, created_by),
        )
    return {"ok": True}


def delete_promo_code(code: str) -> bool:
    code = (code or "").strip().upper()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM promo_codes WHERE code = ?", (code,))
        return cur.rowcount > 0


def list_promo_codes() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT code, days, max_uses, used_count, created_at FROM promo_codes ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------ адмін-статистика ----------------------------


def get_admin_stats() -> dict:
    """Загальна статистика використання застосунку (для адміна)."""
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()
    month_ago = (now - timedelta(days=30)).isoformat()

    with get_conn() as conn:
        # користувачі
        users_row = conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(total_focus_seconds),0) AS s FROM users"
        ).fetchone()
        total_users = int(users_row["c"])
        total_focus_all = int(users_row["s"])

        premium_row = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE plan = 'premium'"
        ).fetchone()
        premium_users = int(premium_row["c"])

        # активні за періоди
        dau = conn.execute(
            "SELECT COUNT(DISTINCT tg_id) AS c FROM sessions WHERE date(finished_at) = ?",
            (today,),
        ).fetchone()["c"]
        wau = conn.execute(
            "SELECT COUNT(DISTINCT tg_id) AS c FROM sessions WHERE finished_at >= ?",
            (week_ago,),
        ).fetchone()["c"]
        mau = conn.execute(
            "SELECT COUNT(DISTINCT tg_id) AS c FROM sessions WHERE finished_at >= ?",
            (month_ago,),
        ).fetchone()["c"]

        # сесії
        sessions_row = conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(actual),0) AS s FROM sessions"
        ).fetchone()
        total_sessions = int(sessions_row["c"])
        total_session_seconds = int(sessions_row["s"])

        today_sessions = conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(actual),0) AS s FROM sessions WHERE date(finished_at) = ?",
            (today,),
        ).fetchone()
        completed_row = conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE completed = 1"
        ).fetchone()["c"]

        # за категоріями
        by_category = conn.execute(
            """
            SELECT category, COUNT(*) AS c, COALESCE(SUM(actual),0) AS s,
                   COUNT(DISTINCT tg_id) AS u
            FROM sessions WHERE completed = 1
            GROUP BY category ORDER BY s DESC
            """
        ).fetchall()

        # за режимами
        by_mode = conn.execute(
            """
            SELECT mode, COUNT(*) AS c, COALESCE(SUM(actual),0) AS s
            FROM sessions WHERE completed = 1
            GROUP BY mode ORDER BY s DESC
            """
        ).fetchall()

        # треки
        tracks_row = conn.execute(
            "SELECT COUNT(*) AS c FROM tracks"
        ).fetchone()
        total_tracks = int(tracks_row["c"])
        admin_tracks = conn.execute(
            "SELECT COUNT(*) AS c FROM tracks WHERE scope = 'admin'"
        ).fetchone()["c"]
        fav_count = conn.execute(
            "SELECT COUNT(*) AS c FROM track_user_meta WHERE favorite = 1"
        ).fetchone()["c"]
        pin_count = conn.execute(
            "SELECT COUNT(*) AS c FROM track_user_meta WHERE pinned = 1"
        ).fetchone()["c"]

        # активність по днях (останні 14)
        by_day = conn.execute(
            """
            SELECT date(finished_at) AS d, COUNT(*) AS c,
                   COUNT(DISTINCT tg_id) AS u, COALESCE(SUM(actual),0) AS s
            FROM sessions WHERE finished_at >= ?
            GROUP BY d ORDER BY d ASC
            """,
            (week_ago,),
        ).fetchall()

        # топ користувачі
        top_users = conn.execute(
            """
            SELECT u.tg_id, u.first_name, u.username,
                   u.total_focus_seconds, COUNT(s.id) AS sessions, u.plan
            FROM users u
            LEFT JOIN sessions s ON s.tg_id = u.tg_id AND s.completed = 1
            GROUP BY u.tg_id
            ORDER BY u.total_focus_seconds DESC
            LIMIT 10
            """
        ).fetchall()

        # платежі
        payments_row = conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(amount),0) AS s FROM payments WHERE status IN ('success','sandbox')"
        ).fetchone()
        revenue_uah = float(payments_row["s"])

        # баг-репорти
        bugs_row = conn.execute(
            "SELECT COUNT(*) AS c FROM bug_reports"
        ).fetchone()["c"]

        # прослуховування
        plays_row = conn.execute(
            "SELECT COUNT(*) AS c FROM plays"
        ).fetchone()["c"]
        plays_by_source = conn.execute(
            "SELECT source, COUNT(*) AS c FROM plays GROUP BY source"
        ).fetchall()
        top_tracks = conn.execute(
            "SELECT MAX(title) AS title, COUNT(*) AS c FROM plays GROUP BY track_key ORDER BY c DESC LIMIT 5"
        ).fetchall()

    return {
        "users": {
            "total": total_users,
            "premium": premium_users,
            "dau": dau, "wau": wau, "mau": mau,
            "total_focus_seconds": total_focus_all,
        },
        "sessions": {
            "total": total_sessions,
            "completed": completed_row,
            "today": int(today_sessions["c"]),
            "today_seconds": int(today_sessions["s"]),
            "total_seconds": total_session_seconds,
        },
        "by_category": [dict(r) for r in by_category],
        "by_mode": [dict(r) for r in by_mode],
        "by_day": [dict(r) for r in by_day],
        "tracks": {
            "total": total_tracks,
            "admin": admin_tracks,
            "favorites": fav_count,
            "pinned": pin_count,
        },
        "top_users": [dict(r) for r in top_users],
        "payments": {
            "count": int(payments_row["c"]),
            "revenue_uah": revenue_uah,
        },
        "bug_reports": bugs_row,
        "plays": {
            "total": int(plays_row),
            "by_source": [dict(r) for r in plays_by_source],
            "top_tracks": [dict(r) for r in top_tracks],
        },
        "categories": CATEGORIES,
        "modes": FOCUS_MODES,
    }

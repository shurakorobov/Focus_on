-- Focus OS — схема для Supabase (PostgreSQL)
--
-- Виконайте у SQL Editor вашого Supabase-проєкту.

-- ===== Користувачі =====
CREATE TABLE IF NOT EXISTS users (
    tg_id              BIGINT PRIMARY KEY,
    first_name         TEXT NOT NULL DEFAULT '',
    last_name          TEXT NOT NULL DEFAULT '',
    username           TEXT NOT NULL DEFAULT '',
    photo_url          TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_focus_seconds INTEGER NOT NULL DEFAULT 0
);

-- ===== Сесії фокусу =====
CREATE TABLE IF NOT EXISTS sessions (
    id          BIGSERIAL PRIMARY KEY,
    tg_id       BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    mode        TEXT NOT NULL,
    planned     INTEGER NOT NULL,
    actual      INTEGER NOT NULL,
    completed   SMALLINT NOT NULL DEFAULT 0,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_tg     ON sessions(tg_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);

-- ===== Музика =====
CREATE TABLE IF NOT EXISTS tracks (
    id         BIGSERIAL PRIMARY KEY,
    tg_id      BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    scope      TEXT NOT NULL CHECK (scope IN ('admin','user')),
    kind       TEXT NOT NULL CHECK (kind IN ('audio','youtube')),
    url        TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    author     TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tracks_scope ON tracks(scope);
CREATE INDEX IF NOT EXISTS idx_tracks_tg    ON tracks(tg_id);

-- Row Level Security: користувач бачить адмінські треки + свої особисті
ALTER TABLE tracks ENABLE ROW LEVEL SECURITY;

-- можна читати: scope='admin' OR власний запис
CREATE POLICY "tracks_select" ON tracks
    FOR SELECT USING (
        scope = 'admin'
        OR tg_id = (auth.jwt() ->> 'sub')::BIGINT
    );

-- заборонити пряме редагування через клієнт Supabase:
-- усе写入 відбувається через бекенд (service role key, обходить RLS)

-- ===== Допоміжні функції =====

-- Оновити загальний час фокусу після завершення сесії (тригер)
CREATE OR REPLACE FUNCTION update_total_focus()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.completed = 1 THEN
        UPDATE users
        SET total_focus_seconds = total_focus_seconds + NEW.actual
        WHERE tg_id = NEW.tg_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_total ON sessions;
CREATE TRIGGER trg_update_total
    AFTER INSERT ON sessions
    FOR EACH ROW EXECUTE FUNCTION update_total_focus();

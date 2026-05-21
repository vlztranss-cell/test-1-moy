-- =============================================
-- Миграция: таблица support_tickets для формы поддержки на лендинге
-- БД: photo_bot
-- =============================================

BEGIN;

CREATE TABLE IF NOT EXISTS support_tickets (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(255) NOT NULL,
    name        VARCHAR(255),
    subject     VARCHAR(255),
    message     TEXT NOT NULL,
    source      VARCHAR(32) DEFAULT 'landing',  -- landing / bot / dashboard
    user_agent  TEXT,
    ip          INET,
    status      VARCHAR(16) NOT NULL DEFAULT 'open',  -- open / in_progress / closed
    admin_note  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON support_tickets (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_tickets_email ON support_tickets (email);

COMMIT;

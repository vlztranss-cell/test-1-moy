-- =============================================
-- Таблица для веб-платежей VideoAI
-- Запустить на PostgreSQL (72.56.96.64)
-- =============================================

CREATE TABLE IF NOT EXISTS web_payments (
    id SERIAL PRIMARY KEY,
    payment_id VARCHAR(64) UNIQUE NOT NULL,   -- ID платежа от ЮKassa
    email VARCHAR(255) NOT NULL,
    session_id VARCHAR(64),
    plan VARCHAR(20) NOT NULL,                 -- starter / pro / business
    amount INTEGER NOT NULL,                   -- сумма в рублях
    status VARCHAR(20) DEFAULT 'pending',      -- pending / succeeded / canceled
    credits_added INTEGER DEFAULT 0,           -- сколько кредитов начислено
    created_at TIMESTAMP DEFAULT NOW(),
    paid_at TIMESTAMP
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_web_payments_email ON web_payments(email);
CREATE INDEX IF NOT EXISTS idx_web_payments_session ON web_payments(session_id);
CREATE INDEX IF NOT EXISTS idx_web_payments_status ON web_payments(status);

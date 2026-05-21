-- =============================================
-- Миграция: партнёрская программа (cash payouts для арбитражников)
-- БД: photo_bot
-- =============================================
-- Идемпотентна.
-- =============================================

BEGIN;

CREATE TABLE IF NOT EXISTS partner_applications (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) NOT NULL,
    name            VARCHAR(255),
    telegram        VARCHAR(100),                  -- @username
    phone           VARCHAR(30),
    traffic_source  TEXT NOT NULL,                 -- описание источника (TG-канал, сайт и т.д.)
    monthly_volume_estimate VARCHAR(50),           -- '10-50' / '50-200' / '200+'
    legal_status    VARCHAR(20),                   -- 'self_employed' / 'ip' / 'individual'
    inn             VARCHAR(20),                   -- ИНН партнёра (для выплат самозанятого/ИП)
    bank_card       VARCHAR(50),                   -- маска карты или счёта (опц.)
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending / approved / rejected
    ref_code        VARCHAR(16) UNIQUE,            -- присваивается при approve
    -- Индивидуальные ставки выплат (можем поднимать для топов)
    payout_starter  INTEGER NOT NULL DEFAULT 70,
    payout_pro      INTEGER NOT NULL DEFAULT 150,
    payout_business INTEGER NOT NULL DEFAULT 250,
    balance_rub     NUMERIC(10,2) NOT NULL DEFAULT 0,  -- невыплаченный остаток
    total_paid_out  NUMERIC(10,2) NOT NULL DEFAULT 0,  -- всего выплачено за всё время
    admin_note      TEXT,
    user_agent      TEXT,
    ip              INET,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at     TIMESTAMPTZ,
    rejected_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_partner_applications_status ON partner_applications (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_partner_applications_ref_code ON partner_applications (ref_code) WHERE ref_code IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_partner_applications_email ON partner_applications (LOWER(email));

CREATE TABLE IF NOT EXISTS partner_conversions (
    id              SERIAL PRIMARY KEY,
    partner_id      INTEGER NOT NULL REFERENCES partner_applications(id) ON DELETE CASCADE,
    web_order_id    INTEGER REFERENCES web_orders(id) ON DELETE SET NULL,
    tariff_code     VARCHAR(32),                   -- starter / pro / business
    amount_rub      NUMERIC(10,2) NOT NULL,        -- начисленная партнёру сумма
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending / paid / cancelled
    payout_id       INTEGER,                       -- ссылка на partner_payouts.id когда выплачено
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_partner_conversions_partner ON partner_conversions (partner_id, status);

CREATE TABLE IF NOT EXISTS partner_payouts (
    id              SERIAL PRIMARY KEY,
    partner_id      INTEGER NOT NULL REFERENCES partner_applications(id) ON DELETE CASCADE,
    amount_rub      NUMERIC(10,2) NOT NULL,
    method          VARCHAR(32),                   -- 'card' / 'account' / 'cash'
    receipt_url     TEXT,                          -- ссылка на чек самозанятого
    admin_note      TEXT,
    paid_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_partner_payouts_partner ON partner_payouts (partner_id);

COMMIT;

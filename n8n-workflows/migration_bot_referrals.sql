-- =============================================
-- Миграция: реферальная программа в Telegram-боте (Phase 4)
-- БД: photo_bot
-- =============================================
-- Идемпотентна.
-- =============================================

BEGIN;

-- 1) user_state: ref_by — кто пригласил этого бот-юзера (хранится между сессиями)
ALTER TABLE user_state
    ADD COLUMN IF NOT EXISTS ref_by VARCHAR(16),
    ADD COLUMN IF NOT EXISTS ref_captured_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_user_state_ref_by ON user_state (ref_by) WHERE ref_by IS NOT NULL;

-- 2) orders: ref_by + referral_bonus_paid для идемпотентного начисления при оплате в боте
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS ref_by VARCHAR(16),
    ADD COLUMN IF NOT EXISTS referral_bonus_paid BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_orders_ref_by ON orders (ref_by) WHERE ref_by IS NOT NULL;

-- 3) web_users: telegram_user_id для связи бот-юзера ↔ web_users
ALTER TABLE web_users
    ADD COLUMN IF NOT EXISTS telegram_user_id TEXT,
    ADD COLUMN IF NOT EXISTS telegram_username TEXT;

CREATE INDEX IF NOT EXISTS idx_web_users_tg_user_id ON web_users (telegram_user_id) WHERE telegram_user_id IS NOT NULL;

-- 4) Функция связи бот-юзера с web_users по email + сохранение tg_user_id.
--    Возвращает ref_code (создаёт web_users если ещё нет).
CREATE OR REPLACE FUNCTION ensure_web_user_for_bot(
    p_email VARCHAR(255),
    p_tg_user_id TEXT,
    p_tg_username TEXT DEFAULT NULL
) RETURNS VARCHAR(16) AS $$
DECLARE
    v_email VARCHAR(255);
    v_code VARCHAR(16);
BEGIN
    v_email := LOWER(TRIM(p_email));
    IF v_email IS NULL OR v_email = '' THEN
        RETURN NULL;
    END IF;

    INSERT INTO web_users (email, ref_code, telegram_user_id, telegram_username, last_seen)
    VALUES (v_email, generate_ref_code(), NULLIF(p_tg_user_id, ''), NULLIF(p_tg_username, ''), NOW())
    ON CONFLICT (email) DO UPDATE
    SET ref_code = COALESCE(web_users.ref_code, EXCLUDED.ref_code),
        telegram_user_id = COALESCE(web_users.telegram_user_id, EXCLUDED.telegram_user_id),
        telegram_username = COALESCE(EXCLUDED.telegram_username, web_users.telegram_username),
        last_seen = NOW()
    RETURNING ref_code INTO v_code;

    RETURN v_code;
END;
$$ LANGUAGE plpgsql;

COMMIT;

-- =============================================
-- Миграция: реферальная программа (cross-platform: лендинг + Telegram-бот)
-- БД: photo_bot @ host-postgres
-- =============================================
-- Идемпотентна.
-- =============================================

BEGIN;

-- 1) Расширяем web_users реферальными полями
ALTER TABLE web_users
    ADD COLUMN IF NOT EXISTS ref_code VARCHAR(16) UNIQUE,
    ADD COLUMN IF NOT EXISTS ref_by VARCHAR(16),
    ADD COLUMN IF NOT EXISTS bonus_credits_earned INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS referred_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS paid_referred_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_web_users_ref_code ON web_users (ref_code) WHERE ref_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_web_users_ref_by ON web_users (ref_by) WHERE ref_by IS NOT NULL;

-- 2) Расширяем web_orders — храним ref_by на момент оплаты + флаг что бонус уже начислен
ALTER TABLE web_orders
    ADD COLUMN IF NOT EXISTS ref_by VARCHAR(16),
    ADD COLUMN IF NOT EXISTS referral_bonus_paid BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_web_orders_ref_by ON web_orders (ref_by) WHERE ref_by IS NOT NULL;

-- 3) Расширяем существующую referrals (она для бота) — добавляем платформу и бонус в кредитах.
--    bonus_paid_credits — сколько кредитов получил рефер за этого друга.
ALTER TABLE referrals
    ADD COLUMN IF NOT EXISTS platform VARCHAR(16) DEFAULT 'bot',
    ADD COLUMN IF NOT EXISTS bonus_paid_credits INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS web_user_id INTEGER,
    ADD COLUMN IF NOT EXISTS friend_web_user_id INTEGER,
    ADD COLUMN IF NOT EXISTS first_paid_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_referrals_platform ON referrals (platform);
CREATE INDEX IF NOT EXISTS idx_referrals_web_user_id ON referrals (web_user_id) WHERE web_user_id IS NOT NULL;

-- 4) Функция генерации короткого ref_code (6 символов, без неоднозначных I/O/0/1/l).
CREATE OR REPLACE FUNCTION generate_ref_code() RETURNS VARCHAR(16) AS $$
DECLARE
    alphabet TEXT := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';   -- без I, O, 0, 1
    code VARCHAR(16);
    i INT;
    attempts INT := 0;
BEGIN
    LOOP
        code := '';
        FOR i IN 1..6 LOOP
            code := code || substr(alphabet, 1 + floor(random() * length(alphabet))::int, 1);
        END LOOP;
        -- Проверяем уникальность
        EXIT WHEN NOT EXISTS (SELECT 1 FROM web_users WHERE ref_code = code);
        attempts := attempts + 1;
        IF attempts > 50 THEN
            RAISE EXCEPTION 'Не удалось сгенерировать уникальный ref_code за 50 попыток';
        END IF;
    END LOOP;
    RETURN code;
END;
$$ LANGUAGE plpgsql;

-- 5) Бэкфилл — у уже оплативших юзеров генерируем ref_code, чтобы они могли приглашать
UPDATE web_users
SET ref_code = generate_ref_code()
WHERE ref_code IS NULL
  AND paid_credits > 0
  AND email IS NOT NULL
  AND email <> '';

COMMIT;

-- Контрольные запросы:
-- SELECT email, ref_code, ref_by, bonus_credits_earned FROM web_users WHERE ref_code IS NOT NULL;
-- SELECT generate_ref_code(); SELECT generate_ref_code();

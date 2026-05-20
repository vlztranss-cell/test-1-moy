-- =============================================
-- Миграция: добавляем web_orders.charge_type для различения
-- free/paid генераций (нужно для выбора URL с водяным знаком в Parse Status).
-- БД: photo_bot @ host-postgres
-- =============================================
-- Идемпотентна.
-- =============================================

BEGIN;

ALTER TABLE web_orders
    ADD COLUMN IF NOT EXISTS charge_type VARCHAR(8);

-- Бэкфилл: существующие записи считаем 'paid' (безопасный дефолт —
-- они получат clean URL даже без вотермарка, на которых клиент уже
-- мог скачать).
UPDATE web_orders SET charge_type = 'paid'
WHERE charge_type IS NULL;

CREATE INDEX IF NOT EXISTS idx_web_orders_charge_type ON web_orders (charge_type);

COMMIT;

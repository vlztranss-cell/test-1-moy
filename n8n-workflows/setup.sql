-- =============================================
-- Миграция web_orders для веб-платежей VideoAI
-- База: photo_bot @ 72.56.96.64
-- =============================================

-- Таблица web_orders уже существует.
-- Добавляем только колонку email (если нет):
ALTER TABLE web_orders ADD COLUMN IF NOT EXISTS email VARCHAR(255);
CREATE INDEX IF NOT EXISTS idx_web_orders_email ON web_orders(email);

-- Готово! Таблица уже содержит все нужные поля:
-- payment_id, tariff_code, amount_rub, is_paid,
-- generations_limit, generations_left, paid_at, session_id

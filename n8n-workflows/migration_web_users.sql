-- =============================================
-- Миграция: таблица web_users для серверной защиты кредитов на лендинге botisk.ru
-- БД: photo_bot @ host-postgres (port 5432)
-- =============================================
-- Идемпотентна: безопасно прогонять повторно.
-- =============================================

BEGIN;

-- 1) Основная таблица: один email = один пользователь лендинга
CREATE TABLE IF NOT EXISTS web_users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    free_used       BOOLEAN     NOT NULL DEFAULT FALSE,
    paid_credits    INTEGER     NOT NULL DEFAULT 0,
    total_generated INTEGER     NOT NULL DEFAULT 0,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_ip         INET
);

-- Индекс по lower(email) — на случай если кто-то введёт email в смешанном регистре
CREATE INDEX IF NOT EXISTS idx_web_users_email_lower ON web_users (LOWER(email));

-- 2) Бэкфилл: переносим уже оплаченных пользователей из web_orders в web_users.
--    Суммируем generations_left (несработавшие кредиты) по email — мы за это получили деньги.
INSERT INTO web_users (email, paid_credits, first_seen, last_seen)
SELECT
    LOWER(email)            AS email,
    SUM(generations_left)   AS paid_credits,
    MIN(created_at)         AS first_seen,
    COALESCE(MAX(paid_at), MAX(created_at)) AS last_seen
FROM web_orders
WHERE is_paid = 'yes'
  AND email IS NOT NULL
  AND email <> ''
GROUP BY LOWER(email)
ON CONFLICT (email) DO NOTHING;

-- 3) Связь web_orders → web_users (опционально, для отчётности).
--    Не делаем FK жёстким (web_orders может быть без email), просто индексируем.
ALTER TABLE web_orders
    ADD COLUMN IF NOT EXISTS web_user_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_web_orders_web_user_id ON web_orders (web_user_id);

-- Бэкфилл web_user_id для тех заказов, у которых email уже есть в web_users
UPDATE web_orders wo
SET web_user_id = wu.id
FROM web_users wu
WHERE wo.web_user_id IS NULL
  AND wo.email IS NOT NULL
  AND wo.email <> ''
  AND LOWER(wo.email) = wu.email;

COMMIT;

-- =============================================
-- Контрольные запросы (выполнить отдельно для проверки)
-- =============================================
-- SELECT COUNT(*) AS users_total, SUM(paid_credits) AS credits_total FROM web_users;
-- SELECT id, email, free_used, paid_credits, total_generated, first_seen FROM web_users ORDER BY id;
-- SELECT COUNT(*) FROM web_orders WHERE web_user_id IS NOT NULL;

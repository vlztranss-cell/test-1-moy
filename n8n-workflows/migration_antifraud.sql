-- =============================================
-- Миграция: антифрод для бесплатных генераций
-- Защита от накрутки free-видео через множественные email с одного IP
-- =============================================

BEGIN;

-- Индекс для быстрого подсчёта free-юзеров по IP
CREATE INDEX IF NOT EXISTS idx_web_users_last_ip_free
    ON web_users (last_ip, first_seen)
    WHERE last_ip IS NOT NULL AND free_used = TRUE;

-- Колонка для отметки заблокированных IP (на случай ручного бана)
ALTER TABLE web_users
    ADD COLUMN IF NOT EXISTS ip_abuse_blocked BOOLEAN NOT NULL DEFAULT FALSE;

-- Лог попыток обхода — для аналитики/разбора
CREATE TABLE IF NOT EXISTS web_abuse_log (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(255),
    ip          INET,
    reason      VARCHAR(64),         -- 'ip_too_many_free' / 'manual_block' / etc
    free_users_from_ip INTEGER,      -- сколько уже было с этого IP
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_web_abuse_log_ip ON web_abuse_log (ip, created_at DESC);

COMMIT;

-- Контрольный запрос — посмотреть IP-абузеров
-- SELECT last_ip, COUNT(*) AS free_users, array_agg(email) AS emails
-- FROM web_users
-- WHERE last_ip IS NOT NULL AND free_used = TRUE
-- GROUP BY last_ip
-- HAVING COUNT(*) > 1
-- ORDER BY free_users DESC;

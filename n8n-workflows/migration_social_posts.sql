-- =============================================
-- Миграция: таблица social_posts для очереди автопостинга в YouTube/Telegram/VK
-- =============================================

BEGIN;

CREATE TABLE IF NOT EXISTS social_posts (
    id              SERIAL PRIMARY KEY,
    -- Источник креатива (файл в /srv/creatives/raw или processed)
    creative_file   VARCHAR(255) NOT NULL,
    title           VARCHAR(255),
    caption         TEXT,                       -- описание/подпись
    hashtags        TEXT,                       -- #тег1 #тег2 ...
    -- Расписание
    scheduled_at    TIMESTAMPTZ NOT NULL,
    -- Целевые платформы (любая комбинация)
    target_youtube  BOOLEAN NOT NULL DEFAULT TRUE,
    target_telegram BOOLEAN NOT NULL DEFAULT TRUE,
    target_vk       BOOLEAN NOT NULL DEFAULT TRUE,
    -- Статусы по платформам: pending / posted / failed
    youtube_status  VARCHAR(16) DEFAULT 'pending',
    youtube_video_id VARCHAR(64),
    youtube_url     TEXT,
    youtube_error   TEXT,
    youtube_posted_at TIMESTAMPTZ,
    telegram_status VARCHAR(16) DEFAULT 'pending',
    telegram_msg_id INTEGER,
    telegram_url    TEXT,
    telegram_error  TEXT,
    telegram_posted_at TIMESTAMPTZ,
    vk_status       VARCHAR(16) DEFAULT 'pending',
    vk_video_id     VARCHAR(64),
    vk_url          TEXT,
    vk_error        TEXT,
    vk_posted_at    TIMESTAMPTZ,
    -- Метаданные
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_social_posts_scheduled ON social_posts (scheduled_at)
    WHERE youtube_status = 'pending' OR telegram_status = 'pending' OR vk_status = 'pending';

-- Аналитика просмотров по постам (заполняется отдельным cron'ом)
CREATE TABLE IF NOT EXISTS social_posts_metrics (
    id              SERIAL PRIMARY KEY,
    post_id         INTEGER NOT NULL REFERENCES social_posts(id) ON DELETE CASCADE,
    platform        VARCHAR(16) NOT NULL,  -- youtube / telegram / vk
    views           INTEGER DEFAULT 0,
    likes           INTEGER DEFAULT 0,
    comments        INTEGER DEFAULT 0,
    shares          INTEGER DEFAULT 0,
    avg_view_duration_sec NUMERIC(10,2),    -- только YouTube Analytics
    ctr_to_botisk   NUMERIC(5,2),           -- кликов на botisk.ru через UTM / показов
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_social_posts_metrics_post ON social_posts_metrics (post_id, platform, fetched_at DESC);

-- Логи всех YouTube API вызовов с request_id (для разбора квот/ошибок)
CREATE TABLE IF NOT EXISTS api_call_log (
    id              SERIAL PRIMARY KEY,
    service         VARCHAR(32) NOT NULL,   -- 'youtube' / 'youtube_analytics' / 'direct' / 'metrika'
    endpoint        VARCHAR(255),
    method          VARCHAR(8),
    status_code     INTEGER,
    request_id      VARCHAR(128),           -- X-Request-Id из ответа
    quota_cost      INTEGER,                -- сколько квоты съел этот вызов
    duration_ms     INTEGER,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_call_log_service_created ON api_call_log (service, created_at DESC);

COMMIT;

-- Контрольные запросы:
-- SELECT service, DATE(created_at), COUNT(*), SUM(quota_cost) FROM api_call_log GROUP BY 1, 2;
-- SELECT * FROM social_posts WHERE youtube_status='pending' AND scheduled_at < NOW() ORDER BY scheduled_at LIMIT 5;

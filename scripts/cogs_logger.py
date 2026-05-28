#!/usr/bin/env python3
"""
COGS Logger — наполняет ai_spend_log реальными расходами на генерацию.

Проблема: ai_spend_log был пустой → мы не знали себестоимость и считали воронку
«на глаз». Этот скрипт восстанавливает COGS из таблиц, где КАЖДАЯ генерация уже
записана (web_orders / orders / auto_creative_log), и пишет по строке на расход.

Идемпотентность: уникальный ключ ref ('web:<id>', 'bot:<id>', 'auto:<id>',
'auto-nano:<id>') + ON CONFLICT DO NOTHING. Можно гонять хоть каждый час — добавит
только новые генерации.

Стоимости (USD) — единственное место, где их менять при смене тарифов PiAPI:
    Kling 2.5/2.6 std 5с ......... $0.20  (https://piapi.ai/kling-2-5)
    Nano Banana 2 (2K) ........... $0.08  (https://piapi.ai/nano-banana-2)
    Flux Schnell (картинка) ...... $0.003

ВАЖНО: это ВЕРХНЯЯ оценка COGS — логируем все генерации с task_id, включая
failed (Kling-модерация иногда не списывает). Лучше переоценить расход, чем
недооценить. Поле metadata->>'status' позволяет потом уточнить.

Запуск на VPS:
    python3 /srv/creatives/cogs_logger.py
Cron (ежедневно в 5:00 МСК = 02:00 UTC):
    0 2 * * *  /usr/bin/python3 /srv/creatives/cogs_logger.py >> /var/log/cogs.log 2>&1
"""
from __future__ import annotations

import subprocess

# --- Тарифы PiAPI (USD за единицу). Меняем тут при смене цен. ---
COST_KLING_VIDEO = 0.16  # подтверждённая реальная цена Kling 5с std (было 0.20 list-price)
COST_NANO_BANANA = 0.08
COST_FLUX_IMAGE = 0.003


def psql(sql: str) -> str:
    r = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        print(f"  ⚠️ psql error: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def ensure_schema() -> None:
    """ref-колонка для идемпотентности (UNIQUE). Безопасно гонять повторно."""
    psql("ALTER TABLE ai_spend_log ADD COLUMN IF NOT EXISTS ref TEXT;")
    # UNIQUE-индекс отдельно (ADD COLUMN ... UNIQUE не поддерживает IF NOT EXISTS)
    psql("CREATE UNIQUE INDEX IF NOT EXISTS ai_spend_log_ref_uidx "
         "ON ai_spend_log (ref) WHERE ref IS NOT NULL;")


# Каждый INSERT...SELECT...ON CONFLICT добавляет только ещё не залогированные генерации.
INSERTS = [
    # --- Веб-лендинг: каждый web_order с piapi_task_id = 1 Kling-видео ---
    f"""
    INSERT INTO ai_spend_log (service, operation, cost_usd, quantity, metadata, created_at, ref)
    SELECT 'piapi', 'kling_video', {COST_KLING_VIDEO}, 1,
           jsonb_build_object('source','web','order_id',order_id,
                              'status',status,'charge_type',charge_type),
           created_at, 'web:'||order_id
    FROM web_orders
    WHERE piapi_task_id IS NOT NULL AND piapi_task_id <> ''
    ON CONFLICT (ref) WHERE ref IS NOT NULL DO NOTHING;
    """,
    # --- Telegram-бот: каждый заказ с результатом/job = 1 Kling-видео (без тестов) ---
    f"""
    INSERT INTO ai_spend_log (service, operation, cost_usd, quantity, metadata, created_at, ref)
    SELECT 'piapi', 'kling_video', {COST_KLING_VIDEO}, 1,
           jsonb_build_object('source','bot','order_id',id,'status',status),
           created_at, 'bot:'||id
    FROM orders
    WHERE (is_test IS NULL OR is_test NOT IN ('t','true','yes','1'))
      AND (ai_job_id IS NOT NULL OR result_video_url IS NOT NULL)
    ON CONFLICT (ref) WHERE ref IS NOT NULL DO NOTHING;
    """,
    # --- Автоген креативов: Kling-видео ---
    f"""
    INSERT INTO ai_spend_log (service, operation, cost_usd, quantity, metadata, created_at, ref)
    SELECT 'piapi', 'kling_video', {COST_KLING_VIDEO}, 1,
           jsonb_build_object('source','auto_creative','log_id',id,
                              'category',category,'status',status),
           created_at, 'auto:'||id
    FROM auto_creative_log
    WHERE piapi_task_id IS NOT NULL AND piapi_task_id <> ''
    ON CONFLICT (ref) WHERE ref IS NOT NULL DO NOTHING;
    """,
    # --- Автоген: доп. расход на исходник Nano Banana 2 (когда источник = nano-banana) ---
    f"""
    INSERT INTO ai_spend_log (service, operation, cost_usd, quantity, metadata, created_at, ref)
    SELECT 'piapi', 'nano_banana_image', {COST_NANO_BANANA}, 1,
           jsonb_build_object('source','auto_creative','log_id',id,'category',category),
           created_at, 'auto-nano:'||id
    FROM auto_creative_log
    WHERE source_photo_url LIKE 'nano-banana:%'
    ON CONFLICT (ref) WHERE ref IS NOT NULL DO NOTHING;
    """,
]


def main() -> None:
    ensure_schema()
    before = psql("SELECT COUNT(*), COALESCE(ROUND(SUM(cost_usd),2),0) FROM ai_spend_log;")
    for sql in INSERTS:
        psql(sql)
    after = psql("SELECT COUNT(*), COALESCE(ROUND(SUM(cost_usd),2),0) FROM ai_spend_log;")

    print(f"ai_spend_log: было [{before}] → стало [{after}]")
    # Сводка по источникам
    summary = psql("""
        SELECT metadata->>'source' AS src,
               COUNT(*) AS n,
               ROUND(SUM(cost_usd),2) AS usd
        FROM ai_spend_log
        WHERE ref IS NOT NULL
        GROUP BY 1 ORDER BY 3 DESC;
    """)
    print("Расход по источникам (src | gens | USD):")
    print(summary if summary else "  (пусто)")


if __name__ == "__main__":
    main()

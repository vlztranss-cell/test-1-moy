"""
Mark_Daily_Digest — n8n workflow собирает каждое утро в 9:00 МСК
сводку за вчерашний день и сохраняет в admin_daily_digest.

Что считает:
- revenue_rub: сумма за вчера из web_orders + orders (TG-бот)
- new_users: записи в web_users за день
- free_attempts: COUNT по web_orders charge_type='free'
- paid_orders: COUNT is_paid='yes'
- failed_orders: status='failed'
- top_videos: топ-3 по приросту views за вчера vs позавчера
- anomalies[]: автогенерация (revenue падение >50%, failed > 30% и т.д.)
- recommendations[]: автогенерация исходя из аномалий

Дашборд читает последнюю запись и показывает.
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "6JRfp0UMBDBhhghL", "name": "Postgres account"}

# SQL: одним заходом собираем сводку за yesterday (МСК)
COLLECT_SQL = """
WITH d AS (
    SELECT (NOW() AT TIME ZONE 'Europe/Moscow')::date - 1 AS day_start
),
web_rev AS (
    SELECT
        COALESCE(SUM(amount_rub::numeric) FILTER (WHERE is_paid='yes'), 0)::int AS rev,
        COUNT(*) FILTER (WHERE is_paid='yes') AS paid,
        COUNT(*) FILTER (WHERE charge_type='free') AS free_attempts,
        COUNT(*) FILTER (WHERE status='failed') AS failed
    FROM web_orders, d
    WHERE created_at AT TIME ZONE 'Europe/Moscow' >= d.day_start
      AND created_at AT TIME ZONE 'Europe/Moscow' < d.day_start + 1
),
bot_rev AS (
    SELECT COALESCE(SUM(amount_rub::numeric) FILTER (WHERE is_paid='yes'), 0)::int AS rev,
        COUNT(*) FILTER (WHERE is_paid='yes') AS paid
    FROM orders, d
    WHERE created_at AT TIME ZONE 'Europe/Moscow' >= d.day_start
      AND created_at AT TIME ZONE 'Europe/Moscow' < d.day_start + 1
),
new_u AS (
    SELECT COUNT(*) AS n FROM web_users, d
    WHERE first_seen AT TIME ZONE 'Europe/Moscow' >= d.day_start
      AND first_seen AT TIME ZONE 'Europe/Moscow' < d.day_start + 1
),
prev_day AS (
    SELECT
        COALESCE(SUM(amount_rub::numeric) FILTER (WHERE is_paid='yes'), 0)::int AS rev_prev
    FROM web_orders, d
    WHERE created_at AT TIME ZONE 'Europe/Moscow' >= d.day_start - 1
      AND created_at AT TIME ZONE 'Europe/Moscow' < d.day_start
),
top_vids AS (
    SELECT jsonb_agg(jsonb_build_object(
        'video_id', video_id, 'title', title, 'views', views, 'category', category
    ) ORDER BY views DESC) AS top
    FROM (SELECT * FROM youtube_video_stats ORDER BY views DESC LIMIT 5) t
)
SELECT
    (SELECT day_start FROM d)::text AS digest_date,
    (SELECT rev FROM web_rev) + (SELECT rev FROM bot_rev) AS revenue_rub,
    (SELECT n FROM new_u) AS new_users,
    (SELECT free_attempts FROM web_rev) AS free_attempts,
    (SELECT paid FROM web_rev) + (SELECT paid FROM bot_rev) AS paid_orders,
    (SELECT failed FROM web_rev) AS failed_orders,
    (SELECT rev_prev FROM prev_day) AS prev_revenue,
    (SELECT top FROM top_vids) AS top_videos;
""".strip()

# JS — генерирует anomalies + recommendations
ANALYZE_JS = """
const r = $input.first().json;
const anomalies = [];
const recs = [];

const rev = r.revenue_rub || 0;
const prev = r.prev_revenue || 0;
const free = r.free_attempts || 0;
const paid = r.paid_orders || 0;
const failed = r.failed_orders || 0;
const newU = r.new_users || 0;

// Аномалии
if (prev > 0 && rev < prev * 0.5) {
    anomalies.push(`Выручка упала на ${Math.round((1 - rev/prev) * 100)}% vs позавчера (${prev}₽ → ${rev}₽)`);
}
if (free > 0 && failed / free > 0.3) {
    anomalies.push(`Высокий % failed-генераций: ${Math.round(failed/free*100)}% (${failed}/${free}). Возможно Kling content policy.`);
}
if (free > 5 && paid === 0) {
    anomalies.push(`${free} бесплатных генераций → 0 платежей. Конверсия лендинга = 0.`);
}
if (newU === 0 && free === 0) {
    anomalies.push('Нет новых юзеров и активности. Возможно сервер был недоступен или нет рекламы.');
}

// Рекомендации
if (free > 10 && paid === 0) {
    recs.push('🎯 H1 email recovery должен сработать — проверить recovery_email_log за день.');
    recs.push('💡 Возможно стоит запустить H6 TG-посевы — органика не конвертит.');
}
if (failed > 0 && failed === free) {
    recs.push('⚠️ Все генерации failed — срочно проверить PiAPI Kling статус и баланс.');
}
if (rev > 0 && rev > prev * 1.5 && prev > 0) {
    recs.push(`📈 Рост выручки в ${Math.round(rev/prev * 10)/10}x — выявить источник в utm/ref и удвоить ставки.`);
}
if (anomalies.length === 0 && rev === 0) {
    recs.push('Тишина в сутках. Рассмотреть: усиление Direct, новый креатив, посев.');
}

return [{ json: {
    digest_date: r.digest_date,
    revenue_rub: rev,
    new_users: newU,
    free_attempts: free,
    paid_orders: paid,
    failed_orders: failed,
    top_videos: r.top_videos || [],
    anomalies: anomalies,
    recommendations: recs.length ? recs : ['Все стабильно. Поддерживайте текущие активности.']
}}];
""".strip()

INSERT_SQL = """
INSERT INTO admin_daily_digest
    (digest_date, revenue_rub, new_users, free_attempts, paid_orders, failed_orders,
     top_videos, anomalies, recommendations)
VALUES
    ('{{ $json.digest_date }}',
     {{ $json.revenue_rub }}, {{ $json.new_users }},
     {{ $json.free_attempts }}, {{ $json.paid_orders }}, {{ $json.failed_orders }},
     '{{ JSON.stringify($json.top_videos).replace(/'/g, "''") }}'::jsonb,
     ARRAY[{{ ($json.anomalies || []).map(a => "'" + a.replace(/'/g, "''") + "'").join(',') }}]::text[],
     ARRAY[{{ ($json.recommendations || []).map(a => "'" + a.replace(/'/g, "''") + "'").join(',') }}]::text[])
ON CONFLICT (digest_date) DO UPDATE SET
    revenue_rub = EXCLUDED.revenue_rub,
    new_users = EXCLUDED.new_users,
    free_attempts = EXCLUDED.free_attempts,
    paid_orders = EXCLUDED.paid_orders,
    failed_orders = EXCLUDED.failed_orders,
    top_videos = EXCLUDED.top_videos,
    anomalies = EXCLUDED.anomalies,
    recommendations = EXCLUDED.recommendations,
    created_at = NOW();
""".strip()


def build_workflow():
    return {
        "name": "Mark_Daily_Digest",
        "nodes": [
            {"parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "0 6 * * *"}]}},
             "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
             "position": [0, 0], "id": "tr", "name": "Daily 9 MSK"},
            {"parameters": {"operation": "executeQuery", "query": COLLECT_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [220, 0], "id": "col", "name": "Collect Stats",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {"jsCode": ANALYZE_JS},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [440, 0], "id": "an", "name": "Analyze"},
            {"parameters": {"operation": "executeQuery", "query": INSERT_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [660, 0], "id": "sv", "name": "Save Digest",
             "credentials": {"postgres": PG_CRED}},
        ],
        "connections": {
            "Daily 9 MSK":     {"main": [[{"node": "Collect Stats", "type": "main", "index": 0}]]},
            "Collect Stats": {"main": [[{"node": "Analyze", "type": "main", "index": 0}]]},
            "Analyze":       {"main": [[{"node": "Save Digest", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base+"/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Mark_Daily_Digest"), None)
    body = build_workflow()
    if found:
        urllib.request.urlopen(urllib.request.Request(
            base+f"/api/v1/workflows/{found['id']}", data=json.dumps(body).encode(),
            method="PUT", headers={**h, "Content-Type": "application/json"}), timeout=30)
        wid = found["id"]; print(f"[OK] Updated {wid}")
    else:
        wid = json.loads(urllib.request.urlopen(urllib.request.Request(
            base+"/api/v1/workflows", data=json.dumps(body).encode(),
            method="POST", headers={**h, "Content-Type": "application/json"}), timeout=30).read())["id"]
        print(f"[OK] Created {wid}")
    try:
        urllib.request.urlopen(urllib.request.Request(
            base+f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15)
        print("[OK] Activated (cron 0 6 * * * = 9:00 МСК ежедневно)")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise


if __name__ == "__main__":
    main()

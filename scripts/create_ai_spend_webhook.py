"""Web_AI_Spend — webhook /webhook/ai-spend для дашборда: PiAPI + OpenAI расход."""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

SPEND_SQL = """
WITH baselines AS (
    SELECT service, known_balance_usd, known_at FROM ai_balance_baseline
),
-- Видео-генерации (Kling/Hailuo) — дорогие. Реальная цена ~$0.18 (калибровка $55/309).
piapi_video AS (
    SELECT COUNT(*)::int AS n, MAX(created_at) AS last_call_at
    FROM (
        SELECT created_at FROM web_orders WHERE piapi_task_id IS NOT NULL
            AND created_at > (SELECT known_at FROM baselines WHERE service='piapi')
        UNION ALL
        SELECT created_at FROM orders WHERE ai_job_id IS NOT NULL
            AND created_at > (SELECT known_at FROM baselines WHERE service='piapi')
    ) v
),
-- Обложки ТЗ на Flux Schnell — копейки (~$0.003). Раньше НЕ учитывались вообще.
piapi_img AS (
    SELECT COUNT(*)::int AS n
    FROM workzilla_tz_drafts
    WHERE cover_url IS NOT NULL
        AND generated_at > (SELECT known_at FROM baselines WHERE service='piapi')
),
piapi_spend AS (
    SELECT
        (SELECT n FROM piapi_video) AS video_calls,
        (SELECT n FROM piapi_img)   AS img_calls,
        ((SELECT n FROM piapi_video) * 0.18
         + (SELECT n FROM piapi_img) * 0.003)::numeric(10, 2) AS cost_usd,
        (SELECT last_call_at FROM piapi_video) AS last_call_at
),
openai_spend AS (
    SELECT
        (SELECT COUNT(*) FROM workzilla_tz_drafts WHERE generated_at > (SELECT known_at FROM baselines WHERE service='openai')) AS workzilla,
        (SELECT COUNT(*) FROM seo_article_drafts WHERE generated_at > (SELECT known_at FROM baselines WHERE service='openai')) AS seo,
        (SELECT COUNT(*) FROM direct_ab_suggestions WHERE generated_at > (SELECT known_at FROM baselines WHERE service='openai')) AS direct_ab
)
SELECT json_build_object(
    'piapi', json_build_object(
        'baseline_usd', (SELECT known_balance_usd FROM baselines WHERE service='piapi'),
        'baseline_at', (SELECT known_at FROM baselines WHERE service='piapi'),
        'video_calls', (SELECT video_calls FROM piapi_spend),
        'img_calls', (SELECT img_calls FROM piapi_spend),
        'calls_since', ((SELECT video_calls FROM piapi_spend) + (SELECT img_calls FROM piapi_spend)),
        'video_price_usd', 0.18,
        'spent_usd', (SELECT cost_usd FROM piapi_spend),
        'last_call_at', (SELECT last_call_at FROM piapi_spend),
        -- Для дашборда (не пугаем минусом): не ниже 0
        'estimated_balance_usd', GREATEST(0::numeric,
            (SELECT known_balance_usd FROM baselines WHERE service='piapi') - (SELECT cost_usd FROM piapi_spend)
        )::numeric(10, 2),
        -- Честный баланс: может быть отрицательным → сигнал, что baseline пора обновить/пополнить
        'real_balance_usd', (
            (SELECT known_balance_usd FROM baselines WHERE service='piapi') - (SELECT cost_usd FROM piapi_spend)
        )::numeric(10, 2)
    ),
    'openai', json_build_object(
        'baseline_usd', (SELECT known_balance_usd FROM baselines WHERE service='openai'),
        'baseline_at', (SELECT known_at FROM baselines WHERE service='openai'),
        'workzilla_calls', (SELECT workzilla FROM openai_spend),
        'seo_calls', (SELECT seo FROM openai_spend),
        'direct_ab_calls', (SELECT direct_ab FROM openai_spend),
        'spent_usd', (
            (SELECT workzilla FROM openai_spend) * 0.06 +
            (SELECT seo FROM openai_spend) * 0.005 +
            (SELECT direct_ab FROM openai_spend) * 0.001
        )::numeric(10, 4),
        'estimated_balance_usd', GREATEST(0::numeric,
            (SELECT known_balance_usd FROM baselines WHERE service='openai') - (
                (SELECT workzilla FROM openai_spend) * 0.06 +
                (SELECT seo FROM openai_spend) * 0.005 +
                (SELECT direct_ab FROM openai_spend) * 0.001
            )
        )::numeric(10, 2)
    ),
    'checked_at', NOW()
) AS payload;
""".strip()


def build_workflow():
    return {
        "name": "Web_AI_Spend",
        "nodes": [
            {"parameters": {"path": "ai-spend", "httpMethod": "GET",
                "responseMode": "responseNode", "options": {}},
             "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
             "position": [0, 0], "id": "wh", "name": "Webhook",
             "webhookId": str(uuid.uuid4())},
            {"parameters": {"operation": "executeQuery", "query": SPEND_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [220, 0], "id": "q", "name": "Calc",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {"respondWith": "json",
                "responseBody": "={{ JSON.stringify($json.payload) }}",
                "options": {"responseHeaders": {"entries": [
                    {"name": "Access-Control-Allow-Origin", "value": "*"}]}}},
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
             "position": [440, 0], "id": "r", "name": "Respond"},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Calc", "type": "main", "index": 0}]]},
            "Calc":    {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base+"/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Web_AI_Spend"), None)
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
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise


if __name__ == "__main__":
    main()

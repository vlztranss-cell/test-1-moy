"""
Вебхук /webhook/economics для дашборда — единый P&L на РЕАЛЬНЫХ данных:
  выручка = yukassa_payments (succeeded), НЕ is_paid (там фантомы);
  COGS    = последний снимок piapi_spend_snapshots (факт-расход PiAPI);
  реклама = константа (Директ не в Метрике; пока вручную);
  + качество данных (фантомы/потери) и разбивка выручки по месяцам.

Запуск: python scripts/create_economics_webhook.py
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

# ad_spend_rub и usd_rub — вручную (Директ не слинкован с Метрикой, реклама сейчас OFF).
# Меняются правкой этих констант + повторным запуском скрипта.
AD_SPEND_RUB = 5000
USD_RUB = 95

QUERY = f"""
SELECT
  (SELECT COALESCE(SUM(amount_rub),0)::int FROM yukassa_payments WHERE status='succeeded') AS revenue_rub,
  (SELECT COUNT(*)::int FROM yukassa_payments WHERE status='succeeded') AS paid_count,
  (SELECT COALESCE(SUM(amount_rub),0)::int FROM yukassa_payments WHERE status='succeeded'
     AND (COALESCE(captured_at,created_at) AT TIME ZONE 'Europe/Moscow')::date = (now() AT TIME ZONE 'Europe/Moscow')::date) AS rev_today_rub,
  (SELECT COALESCE(SUM(amount_rub),0)::int FROM yukassa_payments WHERE status='succeeded'
     AND (COALESCE(captured_at,created_at) AT TIME ZONE 'Europe/Moscow')::date = ((now() AT TIME ZONE 'Europe/Moscow')::date - 1)) AS rev_yesterday_rub,
  (SELECT COALESCE(SUM(amount_rub),0)::int FROM yukassa_payments WHERE status='succeeded'
     AND COALESCE(captured_at,created_at) >= now() - interval '7 days') AS rev_week_rub,
  COALESCE((SELECT spent_usd FROM piapi_spend_snapshots ORDER BY id DESC LIMIT 1),0)::numeric AS cogs_usd,
  COALESCE((SELECT remain_usd FROM piapi_spend_snapshots ORDER BY id DESC LIMIT 1),0)::numeric AS piapi_remain_usd,
  {AD_SPEND_RUB} AS ad_spend_rub,
  {USD_RUB} AS usd_rub,
  (SELECT COUNT(*)::int FROM (
     SELECT payment_id FROM web_orders WHERE is_paid IN ('t','true','yes','1') AND payment_id<>''
     UNION ALL
     SELECT payment_id FROM orders WHERE is_paid IN ('t','true','yes','1') AND payment_id<>''
   ) p WHERE NOT EXISTS (SELECT 1 FROM yukassa_payments y WHERE y.id=p.payment_id AND y.status='succeeded')
  ) AS phantoms,
  (SELECT COUNT(*)::int FROM yukassa_payments y WHERE y.status='succeeded'
     AND NOT EXISTS (SELECT 1 FROM web_orders w WHERE w.payment_id=y.id)
     AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.payment_id=y.id)
  ) AS missing,
  (SELECT json_agg(t) FROM (
     SELECT to_char(COALESCE(captured_at,created_at) AT TIME ZONE 'Europe/Moscow','YYYY-MM') AS m,
            COUNT(*)::int AS n, SUM(amount_rub)::int AS rub
     FROM yukassa_payments WHERE status='succeeded' GROUP BY 1 ORDER BY 1
   ) t) AS by_month,
  (SELECT diagnosis FROM funnel_health ORDER BY id DESC LIMIT 1) AS funnel_diagnosis,
  (SELECT MAX(synced_at)::text FROM yukassa_payments) AS updated_at
"""


def workflow():
    return {
        "name": "Web_Economics",
        "nodes": [
            {"parameters": {"path": "economics", "httpMethod": "GET",
                "responseMode": "responseNode", "options": {}},
             "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
             "position": [0, 0], "id": "wh", "name": "Webhook", "webhookId": str(uuid.uuid4())},
            {"parameters": {"operation": "executeQuery", "query": QUERY, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [220, 0], "id": "q", "name": "Query",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {"respondWith": "json",
                "responseBody": "={{ JSON.stringify($json) }}",
                "options": {"responseHeaders": {"entries": [
                    {"name": "Access-Control-Allow-Origin", "value": "*"}]}}},
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
             "position": [440, 0], "id": "r", "name": "Respond"},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Query", "type": "main", "index": 0}]]},
            "Query": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def deploy(wf):
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base+"/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == wf["name"]), None)
    if found:
        urllib.request.urlopen(urllib.request.Request(
            base+f"/api/v1/workflows/{found['id']}", data=json.dumps(wf).encode(),
            method="PUT", headers={**h, "Content-Type": "application/json"}), timeout=30)
        wid = found["id"]
    else:
        wid = json.loads(urllib.request.urlopen(urllib.request.Request(
            base+"/api/v1/workflows", data=json.dumps(wf).encode(),
            method="POST", headers={**h, "Content-Type": "application/json"}), timeout=30).read())["id"]
    try:
        urllib.request.urlopen(urllib.request.Request(
            base+f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15)
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise
    print(f"  ✓ {wf['name']} → {wid}")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    deploy(workflow())

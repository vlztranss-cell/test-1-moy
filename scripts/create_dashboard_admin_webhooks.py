"""
2 webhook'а для дашборда:
  /webhook/admin-digest  — последний admin_daily_digest
  /webhook/seeding-list  — список tg_seeding_channels (top 30 по score)
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "6JRfp0UMBDBhhghL", "name": "Postgres account"}


def webhook_workflow(name, path, query):
    return {
        "name": name,
        "nodes": [
            {"parameters": {"path": path, "httpMethod": "GET",
                "responseMode": "responseNode", "options": {}},
             "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
             "position": [0, 0], "id": "wh", "name": "Webhook",
             "webhookId": str(uuid.uuid4())},
            {"parameters": {"operation": "executeQuery", "query": query, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [220, 0], "id": "q", "name": "Query",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {"jsCode": "return [{ json: { items: $input.all().map(i => i.json) } }];"},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [440, 0], "id": "agg", "name": "Aggregate"},
            {"parameters": {"respondWith": "json",
                "responseBody": "={{ JSON.stringify($json) }}",
                "options": {"responseHeaders": {"entries": [
                    {"name": "Access-Control-Allow-Origin", "value": "*"}]}}},
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
             "position": [660, 0], "id": "r", "name": "Respond"},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Query", "type": "main", "index": 0}]]},
            "Query":   {"main": [[{"node": "Aggregate", "type": "main", "index": 0}]]},
            "Aggregate": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
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


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    deploy(webhook_workflow(
        "Web_Admin_Digest", "admin-digest",
        "SELECT digest_date::text, revenue_rub, new_users, free_attempts, "
        "paid_orders, failed_orders, top_videos, anomalies, recommendations, "
        "created_at::text FROM admin_daily_digest ORDER BY digest_date DESC LIMIT 1"
    ))
    deploy(webhook_workflow(
        "Web_Seeding_List", "seeding-list",
        "SELECT channel_username, title, category, subscribers, er_percent, "
        "avg_views, ad_price_rub, contact, score, rationale, "
        "contacted_at::text, purchased_at::text, result_clicks, result_payments "
        "FROM tg_seeding_channels ORDER BY score DESC LIMIT 30"
    ))


if __name__ == "__main__":
    main()

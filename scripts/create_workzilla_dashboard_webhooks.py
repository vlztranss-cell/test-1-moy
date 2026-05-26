"""
2 webhook'а для секции «Workzilla TZ» в дашборде:
  GET /webhook/workzilla-tz-list — свежие ТЗ status='ready_to_post' + pending_publication + published
  POST /webhook/workzilla-tz-update — обновить статус (interactive из дашборда)
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

LIST_QUERY = """
SELECT id::text AS id, platform, tz_title, tz_markdown, article_text,
       suggested_price_rub, utm_content, status,
       generated_at::text, given_to_executor_at::text,
       published_at::text, publication_url, executor_name,
       cover_url, video_url
FROM workzilla_tz_drafts
WHERE generated_at > NOW() - INTERVAL '90 days'
ORDER BY generated_at DESC LIMIT 30
""".strip()


def list_workflow():
    return {
        "name": "Web_Workzilla_TZ_List",
        "nodes": [
            {"parameters": {"path": "workzilla-tz-list", "httpMethod": "GET",
                "responseMode": "responseNode", "options": {}},
             "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
             "position": [0, 0], "id": "wh", "name": "Webhook",
             "webhookId": str(uuid.uuid4())},
            {"parameters": {"operation": "executeQuery", "query": LIST_QUERY, "options": {}},
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
            "Webhook":   {"main": [[{"node": "Query", "type": "main", "index": 0}]]},
            "Query":     {"main": [[{"node": "Aggregate", "type": "main", "index": 0}]]},
            "Aggregate": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


# Update endpoint — переход по статусам + сохранение url исполнителя
UPDATE_SQL = """
UPDATE workzilla_tz_drafts SET
    status = COALESCE('{{ ($json.body.status || '').replace(/'/g, "''") }}', status),
    given_to_executor_at = CASE WHEN '{{ $json.body.status }}' = 'pending_publication' THEN NOW() ELSE given_to_executor_at END,
    published_at = CASE WHEN '{{ $json.body.status }}' = 'published' THEN NOW() ELSE published_at END,
    publication_url = COALESCE(NULLIF('{{ ($json.body.publication_url || '').replace(/'/g, "''") }}', ''), publication_url),
    executor_name = COALESCE(NULLIF('{{ ($json.body.executor_name || '').replace(/'/g, "''") }}', ''), executor_name),
    actually_paid_rub = COALESCE(NULLIF('{{ $json.body.actually_paid_rub }}', '')::int, actually_paid_rub),
    notes = COALESCE(NULLIF('{{ ($json.body.notes || '').replace(/'/g, "''") }}', ''), notes)
WHERE id = {{ $json.body.id }}::bigint
RETURNING id, status;
""".strip()


def update_workflow():
    return {
        "name": "Web_Workzilla_TZ_Update",
        "nodes": [
            {"parameters": {"path": "workzilla-tz-update", "httpMethod": "POST",
                "responseMode": "responseNode", "options": {}},
             "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
             "position": [0, 0], "id": "wh", "name": "Webhook",
             "webhookId": str(uuid.uuid4())},
            {"parameters": {"operation": "executeQuery", "query": UPDATE_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [220, 0], "id": "q", "name": "Update",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {"respondWith": "json",
                "responseBody": "={{ JSON.stringify({ok:true, result:$json}) }}",
                "options": {"responseHeaders": {"entries": [
                    {"name": "Access-Control-Allow-Origin", "value": "*"}]}}},
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
             "position": [440, 0], "id": "r", "name": "Respond"},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Update", "type": "main", "index": 0}]]},
            "Update":  {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
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
    deploy(list_workflow())
    deploy(update_workflow())


if __name__ == "__main__":
    main()

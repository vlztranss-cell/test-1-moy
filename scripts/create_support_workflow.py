"""
Создаёт n8n workflow Web_Support_Form:
- POST /webhook/support — принимает форму с лендинга, валидирует, пишет в support_tickets
- GET  /webhook/support-list — возвращает open-тикеты для дашборда
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
import urllib.error
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

PG_CREDENTIAL = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

VALIDATE_JS = r"""
const wh = $('Form Webhook').first().json;
const b = wh.body || {};
const headers = wh.headers || {};
const rawIp = (headers['x-forwarded-for'] || headers['x-real-ip'] || '').toString().split(',')[0].trim();
const ipOk = /^(\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]+$/.test(rawIp);
const ip = ipOk ? rawIp : null;
const email = (b.email || '').toString().trim().toLowerCase();
const emailValid = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(email);
const message = (b.message || '').toString().trim();
const name = (b.name || '').toString().trim().slice(0, 100);
const subject = (b.subject || '').toString().trim().slice(0, 200);
const userAgent = (headers['user-agent'] || '').toString().slice(0, 300);

if (!emailValid) return [{json: {ok: false, error: 'email_invalid'}}];
if (message.length < 5) return [{json: {ok: false, error: 'message_too_short'}}];
if (message.length > 5000) return [{json: {ok: false, error: 'message_too_long'}}];

return [{json: {ok: true, email, name, subject, message, ip, user_agent: userAgent}}];
""".strip()

INSERT_SQL = """INSERT INTO support_tickets (email, name, subject, message, source, user_agent, ip)
VALUES (
    '{{$json.email}}',
    NULLIF('{{ ($json.name || '').replace(/'/g, "''") }}', ''),
    NULLIF('{{ ($json.subject || '').replace(/'/g, "''") }}', ''),
    '{{ ($json.message || '').replace(/'/g, "''") }}',
    'landing',
    NULLIF('{{ ($json.user_agent || '').replace(/'/g, "''") }}', ''),
    {{$json.ip ? "'" + $json.ip + "'::inet" : 'NULL'}}
)
RETURNING id, created_at;"""

LIST_SQL = """SELECT json_agg(t.*) AS payload FROM (
    SELECT id, email, name, subject,
           SUBSTRING(message FROM 1 FOR 200) AS message_preview,
           LENGTH(message) AS message_length,
           status, source, created_at, closed_at, admin_note
    FROM support_tickets
    ORDER BY
        CASE status WHEN 'open' THEN 1 WHEN 'in_progress' THEN 2 ELSE 3 END,
        created_at DESC
    LIMIT 50
) t;"""


def build_workflow():
    return {
        "name": "Web_Support_Form",
        "nodes": [
            # ============ FORM SUBMIT ============
            {
                "parameters": {
                    "path": "support",
                    "httpMethod": "POST",
                    "responseMode": "responseNode",
                    "options": {"allowedOrigins": "https://botisk.ru"},
                },
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2.1,
                "position": [0, 0],
                "id": "support-form-webhook-2026",
                "name": "Form Webhook",
                "webhookId": str(uuid.uuid4()),
            },
            {
                "parameters": {"jsCode": VALIDATE_JS},
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [220, 0],
                "id": "support-validate-2026",
                "name": "Validate",
            },
            {
                "parameters": {
                    "conditions": {
                        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                        "conditions": [{
                            "leftValue": "={{$json.ok}}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                        }],
                        "combinator": "and",
                    },
                },
                "type": "n8n-nodes-base.if",
                "typeVersion": 2,
                "position": [440, 0],
                "id": "support-validated-if-2026",
                "name": "Valid?",
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": INSERT_SQL,
                    "options": {},
                },
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.5,
                "position": [660, 0],
                "id": "support-insert-2026",
                "name": "Insert Ticket",
                "credentials": {"postgres": PG_CREDENTIAL},
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": '={{ JSON.stringify({ok: true, ticket_id: $json.id, created_at: $json.created_at}) }}',
                    "options": {
                        "responseHeaders": {
                            "entries": [{"name": "Access-Control-Allow-Origin", "value": "https://botisk.ru"}]
                        },
                    },
                },
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.5,
                "position": [880, -100],
                "id": "support-respond-ok-2026",
                "name": "Respond OK",
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": '={{ JSON.stringify({ok: false, error: $json.error || "invalid_input"}) }}',
                    "options": {
                        "responseCode": 400,
                        "responseHeaders": {
                            "entries": [{"name": "Access-Control-Allow-Origin", "value": "https://botisk.ru"}]
                        },
                    },
                },
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.5,
                "position": [660, 200],
                "id": "support-respond-400-2026",
                "name": "Respond 400",
            },
            # ============ LIST FOR DASHBOARD ============
            {
                "parameters": {
                    "path": "support-list",
                    "httpMethod": "GET",
                    "responseMode": "responseNode",
                    "options": {},
                },
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2.1,
                "position": [0, 400],
                "id": "support-list-webhook-2026",
                "name": "List Webhook",
                "webhookId": str(uuid.uuid4()),
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": LIST_SQL,
                    "options": {},
                },
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.5,
                "position": [220, 400],
                "id": "support-list-sql-2026",
                "name": "List SQL",
                "credentials": {"postgres": PG_CREDENTIAL},
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": "={{ $json.payload || [] }}",
                    "options": {},
                },
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.5,
                "position": [440, 400],
                "id": "support-list-respond-2026",
                "name": "List Respond",
            },
        ],
        "connections": {
            "Form Webhook": {"main": [[{"node": "Validate", "type": "main", "index": 0}]]},
            "Validate": {"main": [[{"node": "Valid?", "type": "main", "index": 0}]]},
            "Valid?": {
                "main": [
                    [{"node": "Insert Ticket", "type": "main", "index": 0}],
                    [{"node": "Respond 400", "type": "main", "index": 0}],
                ]
            },
            "Insert Ticket": {"main": [[{"node": "Respond OK", "type": "main", "index": 0}]]},
            "List Webhook": {"main": [[{"node": "List SQL", "type": "main", "index": 0}]]},
            "List SQL": {"main": [[{"node": "List Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}

    with urllib.request.urlopen(urllib.request.Request(base + "/api/v1/workflows", headers=h), timeout=30) as r:
        existing = json.loads(r.read())["data"]
    found = next((w for w in existing if w["name"] == "Web_Support_Form"), None)

    body = build_workflow()
    if found:
        url = base + f"/api/v1/workflows/{found['id']}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT",
                                     headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        wid = found["id"]
        print(f"[OK] Обновлён Web_Support_Form: id={wid}, версия={res.get('versionId','?')[:8]}")
    else:
        url = base + "/api/v1/workflows"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={**h, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
            raise
        wid = res["id"]
        print(f"[OK] Создан Web_Support_Form: id={wid}")

    try:
        with urllib.request.urlopen(urllib.request.Request(base + f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15) as r:
            print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code in (200, 400):
            print("  (уже активен)")
        else:
            raise

    print(f"\nForm webhook: {base}/webhook/support  (POST)")
    print(f"List webhook: {base}/webhook/support-list  (GET)")


if __name__ == "__main__":
    main()

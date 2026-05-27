"""
Web_AI_Set_Baseline — вебхук /webhook/set-baseline для обновления baseline баланса AI-сервисов.

PiAPI/OpenAI не отдают баланс по API, поэтому baseline вносится вручную из кабинета.
После пополнения дёрнуть:
    GET /webhook/set-baseline?service=piapi&balance=50&token=<TOKEN>
→ UPDATE ai_balance_baseline: known_balance_usd=50, known_at=NOW().
Дальше трекер /ai-spend считает расход уже от этой свежей отметки.
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

# Секрет, чтобы случайный/чужой GET не сбросил baseline. Хранится в .env (не в публичном репо).
TOKEN = env.get("SET_BASELINE_TOKEN", "").strip()

VALIDATE_JS = f"""
const q = ($('Webhook').first().json.query) || {{}};
if (q.token !== '{TOKEN}') return [{{json: {{ok: false, error: 'forbidden'}}}}];
const service = (q.service || '').toString().toLowerCase();
if (!['piapi', 'openai'].includes(service)) return [{{json: {{ok: false, error: 'bad_service'}}}}];
const balance = parseFloat(q.balance);
if (!(balance >= 0) || balance > 100000) return [{{json: {{ok: false, error: 'bad_balance'}}}}];
return [{{json: {{ok: true, service, balance}}}}];
""".strip()

# service и balance уже провалидированы в Code (whitelist + Number) — безопасно интерполируем.
UPDATE_SQL = """UPDATE ai_balance_baseline
SET known_balance_usd = {{$json.balance}}, known_at = NOW()
WHERE service = '{{$json.service}}'
RETURNING service, known_balance_usd, known_at::text AS known_at;"""


def build_workflow():
    return {
        "name": "Web_AI_Set_Baseline",
        "nodes": [
            {"parameters": {"path": "set-baseline", "httpMethod": "GET",
                "responseMode": "responseNode", "options": {}},
             "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
             "position": [0, 0], "id": "wh", "name": "Webhook",
             "webhookId": str(uuid.uuid4())},
            {"parameters": {"jsCode": VALIDATE_JS},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [220, 0], "id": "val", "name": "Validate"},
            {"parameters": {"conditions": {"options": {"caseSensitive": True,
                "leftValue": "", "typeValidation": "loose"},
                "conditions": [{"leftValue": "={{$json.ok}}", "rightValue": True,
                    "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                "combinator": "and"}},
             "type": "n8n-nodes-base.if", "typeVersion": 2,
             "position": [440, 0], "id": "iff", "name": "OK?"},
            {"parameters": {"operation": "executeQuery", "query": UPDATE_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [660, 0], "id": "upd", "name": "Update Baseline",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {"respondWith": "json",
                "responseBody": "={{ JSON.stringify({ok: true, baseline: $json}) }}",
                "options": {"responseHeaders": {"entries": [
                    {"name": "Access-Control-Allow-Origin", "value": "*"}]}}},
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
             "position": [880, 0], "id": "rok", "name": "Respond OK"},
            {"parameters": {"respondWith": "json",
                "responseBody": "={{ JSON.stringify({ok: false, error: $json.error}) }}",
                "options": {"responseCode": 400, "responseHeaders": {"entries": [
                    {"name": "Access-Control-Allow-Origin", "value": "*"}]}}},
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
             "position": [660, 200], "id": "rerr", "name": "Respond Error"},
        ],
        "connections": {
            "Webhook":         {"main": [[{"node": "Validate", "type": "main", "index": 0}]]},
            "Validate":        {"main": [[{"node": "OK?", "type": "main", "index": 0}]]},
            "OK?":             {"main": [
                [{"node": "Update Baseline", "type": "main", "index": 0}],
                [{"node": "Respond Error", "type": "main", "index": 0}]]},
            "Update Baseline": {"main": [[{"node": "Respond OK", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if not TOKEN:
        print("[STOP] Не задан SET_BASELINE_TOKEN в .env"); return
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base + "/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Web_AI_Set_Baseline"), None)
    body = build_workflow()
    if found:
        urllib.request.urlopen(urllib.request.Request(
            base + f"/api/v1/workflows/{found['id']}", data=json.dumps(body).encode(),
            method="PUT", headers={**h, "Content-Type": "application/json"}), timeout=30)
        wid = found["id"]; print(f"[OK] Updated {wid}")
    else:
        wid = json.loads(urllib.request.urlopen(urllib.request.Request(
            base + "/api/v1/workflows", data=json.dumps(body).encode(),
            method="POST", headers={**h, "Content-Type": "application/json"}), timeout=30).read())["id"]
        print(f"[OK] Created {wid}")
    try:
        urllib.request.urlopen(urllib.request.Request(
            base + f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15)
        print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise
    print(f"\nGET {base}/webhook/set-baseline?service=piapi&balance=50&token={TOKEN}")


if __name__ == "__main__":
    main()

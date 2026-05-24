"""
Web_TG_Auth — endpoint /webhook/tg-auth для Telegram Login Widget.

Виджет на лендинге шлёт сюда GET с параметрами:
  id, first_name, last_name, username, photo_url, auth_date, hash

Workflow:
1. Получает данные виджета
2. Проверяет HMAC: hash должен совпадать с SHA256(secret_key, data_check_string),
   где secret_key = SHA256(BOT_TOKEN), data_check_string = sorted "k=v" по '\\n'
3. При успехе — UPSERT в web_users (telegram_id, telegram_username, ...)
4. Возвращает {ok: true, web_user_id, ref_code} → лендинг сохраняет email-like ID

Документация Telegram: https://core.telegram.org/widgets/login#checking-authorization
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()

# Bot token нужен для HMAC проверки. Берём из credentials store n8n
# или передаём напрямую (мы знаем токен @VideoAI_24isk_bot, но это не наш бот).
# ВАЖНО: для Login Widget нужен ИМЕННО тот бот, для которого делали /setdomain.
# Используем @VideoAI_24isk_bot (он public face сервиса).
BOT_TOKEN = env["TELEGRAM_BOT_PHOTO2VIDEO"]   # @VideoAI_24isk_bot

PG_CRED = {"id": "6JRfp0UMBDBhhghL", "name": "Postgres account"}

# JS код для верификации HMAC + UPSERT
VERIFY_JS = f"""
const crypto = require('crypto');
const BOT_TOKEN = '{BOT_TOKEN}';

// Получили данные от widget — могут быть в body (POST) или query (GET)
const raw = $input.first().json;
const data = {{}};
for (const k of ['id','first_name','last_name','username','photo_url','auth_date','hash']) {{
    data[k] = raw.body?.[k] ?? raw.query?.[k] ?? raw[k] ?? null;
}}

if (!data.hash || !data.id || !data.auth_date) {{
    return [{{ json: {{ ok: false, error: 'missing required fields' }} }}];
}}

// Проверка свежести (не старше 1 часа)
const ageSec = Math.floor(Date.now()/1000) - parseInt(data.auth_date);
if (ageSec > 3600) {{
    return [{{ json: {{ ok: false, error: 'auth_date too old' }} }}];
}}

// Сборка data_check_string
const fields = ['auth_date','first_name','id','last_name','photo_url','username']
    .filter(k => data[k] != null && data[k] !== '')
    .map(k => `${{k}}=${{data[k]}}`)
    .sort()
    .join('\\n');

const secretKey = crypto.createHash('sha256').update(BOT_TOKEN).digest();
const calcHash = crypto.createHmac('sha256', secretKey).update(fields).digest('hex');

if (calcHash !== data.hash) {{
    return [{{ json: {{ ok: false, error: 'hash mismatch' }} }}];
}}

return [{{ json: {{
    ok: true,
    telegram_id: parseInt(data.id),
    telegram_username: data.username || null,
    telegram_first_name: data.first_name || null,
    telegram_photo_url: data.photo_url || null,
}} }}];
""".strip()

UPSERT_SQL = """
INSERT INTO web_users (telegram_id, telegram_username, telegram_first_name,
                       telegram_photo_url, telegram_verified_at, ref_code, last_seen)
VALUES ({{ $json.telegram_id }},
        {{ $json.telegram_username ? "'" + $json.telegram_username.replace(/'/g, "''") + "'" : 'NULL' }},
        {{ $json.telegram_first_name ? "'" + $json.telegram_first_name.replace(/'/g, "''") + "'" : 'NULL' }},
        {{ $json.telegram_photo_url ? "'" + $json.telegram_photo_url.replace(/'/g, "''") + "'" : 'NULL' }},
        NOW(), generate_ref_code(), NOW())
ON CONFLICT (telegram_id) WHERE telegram_id IS NOT NULL DO UPDATE SET
    telegram_username = EXCLUDED.telegram_username,
    telegram_first_name = EXCLUDED.telegram_first_name,
    telegram_photo_url = EXCLUDED.telegram_photo_url,
    last_seen = NOW()
RETURNING id, email, ref_code, free_used, paid_credits, telegram_id;
""".strip()


def build_workflow():
    return {
        "name": "Web_TG_Auth",
        "nodes": [
            {"parameters": {"path": "tg-auth", "httpMethod": "POST",
                "responseMode": "responseNode",
                "options": {"allowedOrigins": "https://botisk.ru"}},
             "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
             "position": [0, 0], "id": "wh", "name": "Webhook",
             "webhookId": str(uuid.uuid4())},
            {"parameters": {"jsCode": VERIFY_JS},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [220, 0], "id": "vrf", "name": "Verify HMAC"},
            {"parameters": {
                "conditions": {"conditions": [{
                    "leftValue": "={{ $json.ok }}", "rightValue": True,
                    "operator": {"type": "boolean", "operation": "true"}
                }]}
             },
             "type": "n8n-nodes-base.if", "typeVersion": 2,
             "position": [440, 0], "id": "isok", "name": "Verified?"},
            {"parameters": {"operation": "executeQuery", "query": UPSERT_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [660, -100], "id": "up", "name": "Upsert User",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {"respondWith": "json",
                "responseBody": "={{ JSON.stringify({ok:true, user:$json}) }}",
                "options": {"responseHeaders": {"entries": [
                    {"name": "Access-Control-Allow-Origin", "value": "https://botisk.ru"}]}}},
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
             "position": [880, -100], "id": "respok", "name": "Respond OK"},
            {"parameters": {"respondWith": "json",
                "responseBody": "={{ JSON.stringify({ok:false, error:$json.error}) }}",
                "options": {"responseCode": 400,
                    "responseHeaders": {"entries": [
                        {"name": "Access-Control-Allow-Origin", "value": "https://botisk.ru"}]}}},
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
             "position": [660, 100], "id": "resperr", "name": "Respond Error"},
        ],
        "connections": {
            "Webhook":     {"main": [[{"node": "Verify HMAC", "type": "main", "index": 0}]]},
            "Verify HMAC": {"main": [[{"node": "Verified?", "type": "main", "index": 0}]]},
            "Verified?":   {"main": [[{"node": "Upsert User", "type": "main", "index": 0}],
                                      [{"node": "Respond Error", "type": "main", "index": 0}]]},
            "Upsert User": {"main": [[{"node": "Respond OK", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base+"/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Web_TG_Auth"), None)
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
        print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise
    print(f"\nPOST {base}/webhook/tg-auth")


if __name__ == "__main__":
    main()

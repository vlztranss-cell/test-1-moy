"""
Mark_TG_Followup — раз в час пишет в TG юзерам, которые:
1) Через TG Login авторизовались (есть telegram_id)
2) НЕ купили (paid_credits = 0)
3) Прошло 1+ часов с авторизации, но НЕ более 7 дней
4) Им ещё не отправляли follow-up

Сообщение: персонализированное, с промокодом MICRO99 (на 1 видео за 99₽).
Использует @VideoAI_24isk_bot.
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "6JRfp0UMBDBhhghL", "name": "Postgres account"}
TOKEN = env["TELEGRAM_BOT_PHOTO2VIDEO"]

SELECT_SQL = """
SELECT
    wu.telegram_id::text AS tg_id,
    COALESCE(wu.telegram_first_name, wu.telegram_username, 'друг') AS name,
    wu.telegram_username,
    wu.id::text AS user_id
FROM web_users wu
LEFT JOIN tg_followup_log fl ON fl.web_user_id = wu.id
WHERE wu.telegram_id IS NOT NULL
  AND wu.telegram_verified_at < NOW() - INTERVAL '1 hour'
  AND wu.telegram_verified_at > NOW() - INTERVAL '7 days'
  AND COALESCE(wu.paid_credits, 0) = 0
  AND fl.id IS NULL
LIMIT 20;
""".strip()

# Сообщение шлём через Telegram Bot API напрямую (не через n8n Telegram node, чтобы был полный контроль)
# Используем HTTP Request с предзаполненными параметрами
SEND_JS = f"""
const TOKEN = '{TOKEN}';
const u = $input.first().json;
const text =
  `Привет, ${{u.name}}!\\n\\n` +
  `Видел что ты заходил на botisk.ru — спасибо за интерес 🙂\\n\\n` +
  `Если ещё не сделал видео — у меня для тебя подарок:\\n` +
  `*промокод MICRO99* — одно видео без водяного знака за 99₽ (вместо 290₽).\\n\\n` +
  `Используй на https://botisk.ru/?promo=MICRO99 или в боте @VideoAI_24isk_bot\\n\\n` +
  `Лучше всего работает фото бабушек/дедушек или детские портреты.`;

const body = new URLSearchParams({{
    chat_id: u.tg_id,
    text: text,
    parse_mode: 'Markdown',
    disable_web_page_preview: 'false'
}}).toString();

try {{
    const r = await this.helpers.httpRequest({{
        method: 'POST',
        url: `https://api.telegram.org/bot${{TOKEN}}/sendMessage`,
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: body,
        json: true,
        timeout: 15000,
    }});
    return [{{ json: {{
        web_user_id: parseInt(u.user_id),
        tg_id: u.tg_id,
        sent: true,
        message_id: r.result ? r.result.message_id : null
    }} }}];
}} catch (e) {{
    return [{{ json: {{
        web_user_id: parseInt(u.user_id),
        tg_id: u.tg_id,
        sent: false,
        error: String(e).substring(0, 200)
    }} }}];
}}
""".strip()

INSERT_SQL = """
INSERT INTO tg_followup_log (web_user_id, tg_id, message_id, sent_ok, error)
VALUES ({{ $json.web_user_id }},
        '{{ $json.tg_id }}',
        {{ $json.message_id || 'NULL' }},
        {{ $json.sent }},
        {{ $json.error ? "'" + $json.error.replace(/'/g, "''") + "'" : 'NULL' }});
""".strip()


def build_workflow():
    return {
        "name": "Mark_TG_Followup",
        "nodes": [
            {"parameters": {"rule": {"interval": [{"field": "hours"}]}},
             "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
             "position": [0, 0], "id": "tr", "name": "Hourly"},
            {"parameters": {"operation": "executeQuery", "query": SELECT_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [220, 0], "id": "sel", "name": "Find Users",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {"jsCode": SEND_JS},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [440, 0], "id": "snd", "name": "Send via Bot API"},
            {"parameters": {"operation": "executeQuery", "query": INSERT_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [660, 0], "id": "log", "name": "Log Sent",
             "credentials": {"postgres": PG_CRED}},
        ],
        "connections": {
            "Hourly":      {"main": [[{"node": "Find Users", "type": "main", "index": 0}]]},
            "Find Users":  {"main": [[{"node": "Send via Bot API", "type": "main", "index": 0}]]},
            "Send via Bot API": {"main": [[{"node": "Log Sent", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base+"/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Mark_TG_Followup"), None)
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
        print("[OK] Activated (hourly)")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise


if __name__ == "__main__":
    main()

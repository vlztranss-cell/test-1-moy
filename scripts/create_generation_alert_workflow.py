"""
Web_Generation_Alert — cron-воркфлоу мониторинга простоя генерации.

Каждые 30 мин проверяет:
  1. Свежие error-выполнения Web_Photo2Video_MVP (особ. "failed to freeze credit").
  2. Метрику: GENERATION_STARTED >= 5 при FREE_GEN_COMPLETED == 0 (старты есть, видео нет).
При тревоге шлёт сообщение в личку админу через бота @iskPhotoAlive (PHOTO2VIDEO token).

Перед деплоем заполнить ADMIN_CHAT_ID (узнать через @userinfobot в Telegram).
Запуск: py scripts/create_generation_alert_workflow.py
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
N8N = env["N8N_URL"].rstrip("/")
N8N_KEY = env["N8N_API_KEY"]
TG_TOKEN = env["TELEGRAM_BOT_PHOTO2VIDEO"]
# Личный chat_id админа (узнать через @userinfobot). Хранится в .env как ADMIN_TG_CHAT_ID.
ADMIN_CHAT_ID = env.get("ADMIN_TG_CHAT_ID", "").strip()

GEN_WF_ID = "9LfeKp4vBn5ZTV2D"

CHECK_JS = f"""
const N8N = '{N8N}';
const KEY = '{N8N_KEY}';
const WF = '{GEN_WF_ID}';
const WINDOW_MS = 35 * 60 * 1000;  // окно ~30 мин + запас

// 1. Свежие ошибки выполнения
let recentErrors = [];
let freezeErr = false;
try {{
    const ex = await this.helpers.httpRequest({{
        method: 'GET',
        url: N8N + '/api/v1/executions?workflowId=' + WF + '&status=error&limit=20',
        headers: {{'X-N8N-API-KEY': KEY}}, json: true, timeout: 15000,
    }});
    const now = Date.now();
    recentErrors = (ex.data || []).filter(e => now - new Date(e.startedAt).getTime() < WINDOW_MS);
    freezeErr = recentErrors.some(e => JSON.stringify(e).toLowerCase().includes('freeze'));
}} catch (e) {{}}

// 2. Метрика: старты vs завершения
let started = 0, completed = 0;
try {{
    const m = await this.helpers.httpRequest({{
        method: 'GET', url: N8N + '/webhook/metrika-stats', json: true, timeout: 20000,
    }});
    const g = m.goals || {{}};
    started = (g.GENERATION_STARTED || {{}}).reaches || 0;
    completed = (g.FREE_GEN_COMPLETED || {{}}).reaches || 0;
}} catch (e) {{}}

const errCount = recentErrors.length;
const metricAlarm = started >= 5 && completed === 0;
const alarm = errCount > 0 || metricAlarm;

let msg = '';
if (alarm) {{
    msg = '⚠️ Генерация видео — проблема\\n\\n';
    if (errCount > 0) {{
        msg += 'Ошибок за ~30 мин: ' + errCount;
        if (freezeErr) msg += ' — failed to freeze credit (пополни баланс PiAPI: app.piapi.ai/billing)';
        msg += '\\n';
    }}
    if (metricAlarm) msg += 'Сегодня стартов: ' + started + ', завершено видео: ' + completed + '\\n';
    msg += '\\nWorkflow Web_Photo2Video_MVP.';
}}
return [{{json: {{alarm, msg, errCount, started, completed}}}}];
""".strip()


def build_workflow():
    return {
        "name": "Web_Generation_Alert",
        "nodes": [
            {"parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}},
             "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
             "position": [0, 0], "id": "cron", "name": "Every 30m"},
            {"parameters": {"jsCode": CHECK_JS},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [220, 0], "id": "chk", "name": "Check"},
            {"parameters": {"conditions": {"options": {"caseSensitive": True,
                "leftValue": "", "typeValidation": "loose"},
                "conditions": [{"leftValue": "={{$json.alarm}}", "rightValue": True,
                    "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                "combinator": "and"}},
             "type": "n8n-nodes-base.if", "typeVersion": 2,
             "position": [440, 0], "id": "iff", "name": "Alarm?"},
            {"parameters": {"method": "POST",
                "url": f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                "sendBody": True, "specifyBody": "json",
                "jsonBody": '={{ JSON.stringify({chat_id: "' + ADMIN_CHAT_ID + '", text: $json.msg}) }}',
                "options": {}},
             "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
             "position": [660, 0], "id": "tg", "name": "Send Alert"},
        ],
        "connections": {
            "Every 30m": {"main": [[{"node": "Check", "type": "main", "index": 0}]]},
            "Check":     {"main": [[{"node": "Alarm?", "type": "main", "index": 0}]]},
            "Alarm?":    {"main": [[{"node": "Send Alert", "type": "main", "index": 0}], []]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if not ADMIN_CHAT_ID:
        print("[STOP] Не задан ADMIN_CHAT_ID. Узнай chat_id через @userinfobot в Telegram")
        print("       и добавь в .env строку: ADMIN_TG_CHAT_ID=<число>")
        return
    h = {"X-N8N-API-KEY": N8N_KEY}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(N8N + "/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Web_Generation_Alert"), None)
    body = build_workflow()
    if found:
        urllib.request.urlopen(urllib.request.Request(
            N8N + f"/api/v1/workflows/{found['id']}", data=json.dumps(body).encode(),
            method="PUT", headers={**h, "Content-Type": "application/json"}), timeout=30)
        wid = found["id"]; print(f"[OK] Updated {wid}")
    else:
        wid = json.loads(urllib.request.urlopen(urllib.request.Request(
            N8N + "/api/v1/workflows", data=json.dumps(body).encode(),
            method="POST", headers={**h, "Content-Type": "application/json"}), timeout=30).read())["id"]
        print(f"[OK] Created {wid}")
    try:
        urllib.request.urlopen(urllib.request.Request(
            N8N + f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15)
        print("[OK] Activated — алёрт каждые 30 мин")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise


if __name__ == "__main__":
    main()

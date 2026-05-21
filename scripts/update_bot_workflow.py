"""
Минимальные точечные правки в AI_Photo2Video_Bot для интеграции реф-программы.

ИЗМЕНЕНИЯ:
1. YooKassa: fresh idempotence key1 — async fetch ref_by + положить в output
2. HTTP Request – YooKassa: Create payment — добавить ref_by в metadata
3. PG: Save payment — добавить RETURNING id
4. Новая нода "Call bot-referral-bonus" — между PG: Save payment и PG: Get total balance

ОТКАТ: бэкап есть в n8n-workflows/AI_Photo2Video_Bot.backup_*.json.
Можно перезалить через `python scripts/restore_bot_workflow.py BACKUP_FILE`.
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

WORKFLOW_ID = "jy1vZlmAgELvecJ8"

# ── 1. Новый код для YooKassa: fresh idempotence key1 ─────────────
# Async — делает httpRequest на bot-get-ref-by и приклеивает ref_by к output
NEW_IDEMPOTENCE_JS = """
const item = items[0];
const j = item.json || {};
const freshKey = [
  String(j.user_id || 'user'),
  String(j.tariff_code || 'tariff'),
  String(Date.now()),
  String(Math.floor(Math.random() * 1000000000)),
  String(Math.floor(Math.random() * 1000000000))
].join('-');

// Подтягиваем ref_by для этого юзера (если он пришёл через /start ref_)
let ref_by = '';
try {
  const r = await this.helpers.httpRequest({
    method: 'GET',
    url: 'https://n8n.24isk.ru/webhook/bot-get-ref-by?user_id=' + encodeURIComponent(j.user_id || ''),
    json: true,
    timeout: 5000,
  });
  if (r && r.ref_by) ref_by = String(r.ref_by);
} catch (e) {
  // Силент — если endpoint недоступен, продолжаем без реф-бонуса
}

return [{
  json: {
    ...j,
    idempotence_key: freshKey,
    ref_by,
  }
}];
""".strip()

# ── 2. Новый body для HTTP YooKassa Create payment ────────────────
# Добавлен ref_by в metadata. Остальное сохранено как было.
NEW_YOOKASSA_BODY = """={{ JSON.stringify({
  amount: {
    value: String($json.amount_value),
    currency: String($json.amount_currency)
  },
  capture: true,
  description: String($json.description || 'Оплата генерации фото'),
  confirmation: {
    type: 'redirect',
    return_url: 'https://t.me/isku_ai_bot'
  },
  receipt: {
    tax_system_code: 1,
    customer: {
      full_name: String($json.username || 'Telegram user'),
      email: 'vlz.trans@yandex.ru'
    },
    items: [
      {
        description: String($json.description || 'Оплата генерации фото'),
        quantity: '1.0',
        amount: {
          value: String($json.amount_value),
          currency: String($json.amount_currency)
        },
        vat_code: 1,
        payment_subject: 'service',
        payment_mode: 'full_payment'
      }
    ]
  },
  metadata: {
    user_id: String($json.user_id || ''),
    chat_id: String($json.chat_id || ''),
    tariff_code: String($json.tariff_code || ''),
    generations_limit: String($json.metadata?.generations_limit ?? ''),
    ref_by: String($json.ref_by || '')
  }
}) }}"""

# ── 3. Новый SQL для PG: Save payment (только добавлен RETURNING id) ────
# Текст изменяем минимально — только add RETURNING в конец
def patch_save_payment_sql(old_sql: str) -> str:
    # ON CONFLICT (order_id) DO UPDATE SET ... paid_at = EXCLUDED.paid_at;
    # Меняем последний `;` на ` RETURNING id;`
    if "RETURNING id" in old_sql:
        return old_sql
    # Убираем последний `;` и добавляем RETURNING
    return old_sql.rstrip().rstrip(";") + "\nRETURNING id;"


# ── 4. Новая нода: HTTP Call bot-referral-bonus ────────────────────
def make_bonus_call_node():
    return {
        "parameters": {
            "method": "POST",
            "url": "https://n8n.24isk.ru/webhook/bot-referral-bonus",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [{"name": "Content-Type", "value": "application/json"}],
            },
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": '={{ JSON.stringify({ order_id: $json.id }) }}',
            "options": {"timeout": 10000, "ignoreHttpStatusErrors": True},
        },
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [46000, 22000],
        "id": "call-bot-referral-bonus-2026",
        "name": "Call bot-referral-bonus",
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}

    # GET текущий workflow
    with urllib.request.urlopen(urllib.request.Request(base + f"/api/v1/workflows/{WORKFLOW_ID}", headers=h), timeout=30) as r:
        wf = json.loads(r.read())

    changes = []

    # 1. YooKassa: fresh idempotence key1
    for n in wf["nodes"]:
        if n["name"] == "YooKassa: fresh idempotence key1":
            if n["parameters"].get("jsCode") != NEW_IDEMPOTENCE_JS:
                n["parameters"]["jsCode"] = NEW_IDEMPOTENCE_JS
                changes.append("YooKassa: fresh idempotence key1 jsCode")
            break
    else:
        print("[WARN] Не найден узел 'YooKassa: fresh idempotence key1'")

    # 2. HTTP Request – YooKassa: Create payment
    for n in wf["nodes"]:
        if n["name"] == "HTTP Request – YooKassa: Create payment":
            if n["parameters"].get("jsonBody") != NEW_YOOKASSA_BODY:
                n["parameters"]["jsonBody"] = NEW_YOOKASSA_BODY
                changes.append("HTTP YooKassa Create payment body (ref_by в metadata)")
            break

    # 3. PG: Save payment — add RETURNING id
    for n in wf["nodes"]:
        if n["name"] == "PG: Save payment":
            old_q = n["parameters"].get("query", "")
            new_q = patch_save_payment_sql(old_q)
            if old_q != new_q:
                n["parameters"]["query"] = new_q
                changes.append("PG: Save payment — RETURNING id")
            break

    # 4a. Если нода Call bot-referral-bonus уже есть — обновим URL (на случай рефакторов)
    for n in wf["nodes"]:
        if n["name"] == "Call bot-referral-bonus":
            current = n["parameters"].get("url", "")
            target = "https://n8n.24isk.ru/webhook/bot-referral-bonus"
            if current != target:
                n["parameters"]["url"] = target
                changes.append("Call bot-referral-bonus URL → " + target)
            break

    # 4. Add new node "Call bot-referral-bonus" + reroute
    if not any(n["name"] == "Call bot-referral-bonus" for n in wf["nodes"]):
        new_node = make_bonus_call_node()
        wf["nodes"].append(new_node)
        # Найдём что было следующим после PG: Save payment
        cur = wf["connections"].get("PG: Save payment", {})
        next_targets = []
        if cur.get("main"):
            for branch in cur["main"]:
                for c in branch:
                    next_targets.append(c["node"])
        # PG: Save payment → Call bonus
        wf["connections"]["PG: Save payment"] = {
            "main": [[{"node": "Call bot-referral-bonus", "type": "main", "index": 0}]]
        }
        # Call bonus → original next targets
        wf["connections"]["Call bot-referral-bonus"] = {
            "main": [[{"node": t, "type": "main", "index": 0} for t in next_targets]]
        }
        changes.append(f"Add Call bot-referral-bonus, rerouted to {next_targets}")

    if not changes:
        print("[OK] Никаких изменений не требуется — workflow уже актуальный.")
        return

    print("Изменения:")
    for c in changes:
        print(f"  - {c}")

    # PUT — фильтруем settings (public API не принимает доп.поля типа availableInMCP)
    raw_settings = wf.get("settings", {})
    allowed = {"callerPolicy", "executionOrder", "errorWorkflow",
               "saveDataErrorExecution", "saveDataSuccessExecution",
               "saveExecutionProgress", "saveManualExecutions", "timezone"}
    settings = {k: v for k, v in raw_settings.items() if k in allowed}
    if "executionOrder" not in settings:
        settings["executionOrder"] = "v1"

    body = {
        "name": wf["name"],
        "nodes": wf["nodes"],
        "connections": wf["connections"],
        "settings": settings,
    }
    req = urllib.request.Request(
        base + f"/api/v1/workflows/{WORKFLOW_ID}",
        data=json.dumps(body).encode(),
        method="PUT",
        headers={**h, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        print(f"\n[OK] PUT успех. версия={res.get('versionId','?')[:8]}, active={res.get('active')}")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()

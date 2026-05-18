"""
Модифицирует n8n workflow Web_Photo2Video_MVP:
добавляет серверную проверку кредитов (web_users) ПЕРЕД генерацией.

Логика:
1. Validate (Code) — валидирует email + image_base64, готовит ip.
2. Charge Credit (Postgres) — атомарный UPSERT в web_users + списание кредита
   (paid если paid_credits>0, free если ещё не использовал).
3. Allow? (IF) — если списать не удалось (кредитов нет) → 402.
4. Существующий flow продолжается, в PG Save добавляется email/web_user_id,
   в Respond — credits_left.

Запуск:
    python scripts/update_web_video_create.py [--dry-run]

--dry-run: только собрать JSON в n8n-workflows/web-video-create.modified.json,
           без PUT на сервер.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

WORKFLOW_ID = "9LfeKp4vBn5ZTV2D"
EXPORT_PATH = Path(__file__).parent.parent / "n8n-workflows" / "web-video-create.exported.json"
MODIFIED_PATH = Path(__file__).parent.parent / "n8n-workflows" / "web-video-create.modified.json"

# n8n Postgres credential — берём существующий, который уже используется в PG Save
PG_CREDENTIAL = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

# JS для validate-ноды (компактно, чтобы влезло в строку)
VALIDATE_JS = r"""
const wh = $('Create Webhook').first().json;
const b = wh.body || {};
const headers = wh.headers || {};
const rawIp = (headers['x-forwarded-for'] || headers['x-real-ip'] || '').toString().split(',')[0].trim();
const ipOk = /^(\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]+$/.test(rawIp);
const ip = ipOk ? rawIp : null;
const email = (b.email || '').toString().trim().toLowerCase();
const emailValid = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(email);
if (!emailValid) {
    return [{json: {ok: false, validation_error: 'email_invalid'}}];
}
if (!b.image_base64) {
    return [{json: {ok: false, validation_error: 'image_required'}}];
}
return [{json: {
    ok: true,
    email,
    ip,
    image_base64: b.image_base64,
    prompt: b.prompt || 'Bring this photo to life with natural, gentle motion',
    session_id: b.session_id || 'anon'
}}];
""".strip()

# SQL для charge credit. Email и IP уже провалидированы регекспом в JS,
# поэтому безопасно интерполируем через n8n {{ }}.
CHARGE_SQL = """WITH upserted AS (
    INSERT INTO web_users (email, last_seen, last_ip)
    VALUES ('{{$json.email}}', NOW(), {{$json.ip ? "'" + $json.ip + "'::inet" : 'NULL'}})
    ON CONFLICT (email) DO UPDATE SET last_seen = NOW(), last_ip = EXCLUDED.last_ip
    RETURNING id, paid_credits, free_used, email
),
charged AS (
    UPDATE web_users wu
    SET
        paid_credits = CASE WHEN u.paid_credits > 0 THEN wu.paid_credits - 1 ELSE wu.paid_credits END,
        free_used    = CASE WHEN u.paid_credits = 0 AND NOT u.free_used THEN TRUE ELSE wu.free_used END,
        total_generated = wu.total_generated + 1
    FROM upserted u
    WHERE wu.id = u.id AND (u.paid_credits > 0 OR NOT u.free_used)
    RETURNING wu.id,
        u.paid_credits AS prev_paid,
        u.free_used    AS prev_free,
        wu.paid_credits AS new_paid,
        CASE WHEN u.paid_credits > 0 THEN 'paid' ELSE 'free' END AS charge_type
)
SELECT
    (SELECT id              FROM upserted) AS user_id,
    (SELECT email           FROM upserted) AS user_email,
    (SELECT paid_credits    FROM upserted) AS available_paid,
    (SELECT free_used       FROM upserted) AS available_free,
    (SELECT id              FROM charged)  AS charged_id,
    (SELECT charge_type     FROM charged)  AS charge_type,
    (SELECT new_paid        FROM charged)  AS credits_left;"""

# Обновлённый PG Save: добавляем email и web_user_id
PG_SAVE_SQL = """INSERT INTO web_orders (
    session_id, piapi_task_id, source_photo_url, prompt, status,
    email, web_user_id
) VALUES (
    '{{$json.session_id}}',
    '{{$json.task_id}}',
    '{{$json.image_url}}',
    '{{ ($json.prompt || "").replace(/'/g, "''") }}',
    '{{$json.status}}',
    '{{$('Validate Input').first().json.email}}',
    {{$('Charge Credit').first().json.user_id}}
) RETURNING order_id;"""

# Респонсы
RESPOND_SUCCESS_BODY = (
    '={{ JSON.stringify({task_id: $item(0).$node["Parse"].json.task_id, '
    'status: $item(0).$node["Parse"].json.status, '
    'order_id: $json.order_id, '
    'credits_left: $item(0).$node["Charge Credit"].json.credits_left, '
    'charge_type: $item(0).$node["Charge Credit"].json.charge_type}) }}'
)

RESPOND_400_BODY = (
    '={{ JSON.stringify({'
    'error: $json.validation_error || "invalid_input", '
    'status: "error"}) }}'
)

RESPOND_402_BODY = (
    '={{ JSON.stringify({'
    'error: "no_credits", '
    'need_payment: true, '
    'credits_left: 0, '
    'available_paid: $json.available_paid || 0, '
    'available_free: $json.available_free === false}) }}'
)


def build_modified_workflow(exported: dict) -> dict:
    """Берёт экспортированный workflow и возвращает модифицированную копию."""
    nodes = list(exported["nodes"])
    # Найдём опорные ноды (по name) — не по id, чтобы было читабельно
    by_name = {n["name"]: n for n in nodes}

    # Новые ноды размещаем между Create Webhook (0,0) и Prepare.
    # 5 новых нод в верхнем ряду: Validate, Validated?, Charge, Allow?, + 2 respond на y=200
    # Сдвигаем существующие ноды на 1100px правее (5 столбцов × 220px)
    SHIFT_X = 1100
    for n in nodes:
        x, y = n["position"]
        if y < 200 and n["name"] != "Create Webhook":
            n["position"] = [x + SHIFT_X, y]

    # 1. Validate Input
    validate_node = {
        "parameters": {"jsCode": VALIDATE_JS},
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [220, 0],
        "id": "validate-input-2026",
        "name": "Validate Input",
    }

    # 2. Validated? (IF: $json.ok === true)
    validated_node = {
        "parameters": {
            "conditions": {
                "options": {
                    "caseSensitive": True,
                    "leftValue": "",
                    "typeValidation": "loose",
                },
                "conditions": [
                    {
                        "leftValue": "={{$json.ok}}",
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    }
                ],
                "combinator": "and",
            },
        },
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": [440, 0],
        "id": "validated-if-2026",
        "name": "Validated?",
    }

    # 3. Charge Credit (Postgres)
    charge_node = {
        "parameters": {
            "operation": "executeQuery",
            "query": CHARGE_SQL,
            "options": {},
        },
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.5,
        "position": [660, 0],
        "id": "charge-credit-2026",
        "name": "Charge Credit",
        "credentials": {"postgres": PG_CREDENTIAL},
    }

    # 4. Allow? (IF: charged_id NOT NULL — кредит списался)
    allow_node = {
        "parameters": {
            "conditions": {
                "options": {
                    "caseSensitive": True,
                    "leftValue": "",
                    "typeValidation": "loose",
                },
                "conditions": [
                    {
                        "leftValue": "={{$json.charged_id}}",
                        "rightValue": "",
                        "operator": {"type": "string", "operation": "notEmpty"},
                    }
                ],
                "combinator": "and",
            },
        },
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": [880, 0],
        "id": "allow-if-2026",
        "name": "Allow?",
    }

    # 5. Respond 400 (ошибка валидации, false-ветка Validated?)
    respond_400 = {
        "parameters": {
            "respondWith": "json",
            "responseBody": RESPOND_400_BODY,
            "options": {
                "responseCode": 400,
                "responseHeaders": {
                    "entries": [
                        {"name": "Access-Control-Allow-Origin", "value": "*"},
                    ]
                },
            },
        },
        "type": "n8n-nodes-base.respondToWebhook",
        "typeVersion": 1.5,
        "position": [660, 200],
        "id": "respond-400-2026",
        "name": "Respond 400",
    }

    # 6. Respond 402 (нет кредитов, false-ветка Allow?)
    respond_402 = {
        "parameters": {
            "respondWith": "json",
            "responseBody": RESPOND_402_BODY,
            "options": {
                "responseCode": 402,
                "responseHeaders": {
                    "entries": [
                        {"name": "Access-Control-Allow-Origin", "value": "*"},
                    ]
                },
            },
        },
        "type": "n8n-nodes-base.respondToWebhook",
        "typeVersion": 1.5,
        "position": [1100, 200],
        "id": "respond-402-2026",
        "name": "Respond 402",
    }

    # Модифицируем PG Save — обновляем query
    pg_save = by_name["PG Save"]
    pg_save["parameters"]["query"] = PG_SAVE_SQL

    # Модифицируем Respond (success) — добавляем credits_left
    respond_ok = by_name["Respond"]
    respond_ok["parameters"]["responseBody"] = RESPOND_SUCCESS_BODY

    # Вставляем новые ноды
    nodes.extend([validate_node, validated_node, charge_node, allow_node, respond_400, respond_402])

    # Переписываем connections
    # Create Webhook → Validate → Validated? ─ true ─→ Charge → Allow? ─ true ─→ Prepare → ...
    #                                       └ false ─→ Respond 400          └ false ─→ Respond 402
    old_conn = exported["connections"]
    new_conn = dict(old_conn)
    new_conn["Create Webhook"] = {
        "main": [[{"node": "Validate Input", "type": "main", "index": 0}]]
    }
    new_conn["Validate Input"] = {
        "main": [[{"node": "Validated?", "type": "main", "index": 0}]]
    }
    new_conn["Validated?"] = {
        "main": [
            [{"node": "Charge Credit", "type": "main", "index": 0}],  # true
            [{"node": "Respond 400", "type": "main", "index": 0}],     # false
        ]
    }
    new_conn["Charge Credit"] = {
        "main": [[{"node": "Allow?", "type": "main", "index": 0}]]
    }
    new_conn["Allow?"] = {
        "main": [
            [{"node": "Prepare", "type": "main", "index": 0}],         # true
            [{"node": "Respond 402", "type": "main", "index": 0}],     # false
        ]
    }

    modified = {
        "name": exported["name"],
        "nodes": nodes,
        "connections": new_conn,
        "settings": exported.get("settings", {"executionOrder": "v1"}),
    }
    return modified


def put_workflow(env: dict, workflow_id: str, body: dict) -> dict:
    url = env["N8N_URL"].rstrip("/") + f"/api/v1/workflows/{workflow_id}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={
            "X-N8N-API-KEY": env["N8N_API_KEY"],
            "Content-Type": "application/json",
            "accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        raise


def activate_workflow(env: dict, workflow_id: str) -> dict:
    url = env["N8N_URL"].rstrip("/") + f"/api/v1/workflows/{workflow_id}/activate"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"X-N8N-API-KEY": env["N8N_API_KEY"], "accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Только собрать modified.json, без PUT")
    args = ap.parse_args()

    env = load_env()
    exported = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    modified = build_modified_workflow(exported)

    MODIFIED_PATH.write_text(json.dumps(modified, ensure_ascii=False, indent=2), encoding="utf-8")
    # Перекодируем stdout в UTF-8 для печати юникода на Windows cp1251
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print(f"[OK] Собран modified workflow: {MODIFIED_PATH}")
    print(f"     Нод было: {len(exported['nodes'])} -> стало: {len(modified['nodes'])}")
    print(f"     Новые ноды: Validate Input, Charge Credit, Allow?, Respond 402")
    print(f"     Модифицированные: PG Save (+ email/web_user_id), Respond (+ credits_left)")

    if args.dry_run:
        print("\n[--dry-run] PUT пропущен.")
        return

    print(f"\nPUT https://n8n.24isk.ru/api/v1/workflows/{WORKFLOW_ID} ...")
    result = put_workflow(env, WORKFLOW_ID, modified)
    print(f"[OK] PUT: версия={result.get('versionId', 'n/a')[:8]}, active={result.get('active')}")

    if not result.get("active"):
        print(f"Активирую workflow...")
        activate_workflow(env, WORKFLOW_ID)
        print(f"[OK] Activated.")


if __name__ == "__main__":
    main()

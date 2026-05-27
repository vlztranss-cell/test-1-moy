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

# SQL для charge credit. Атомарно: один INSERT ... ON CONFLICT DO UPDATE WHERE.
# Email и IP провалидированы регекспом в JS, безопасно интерполируем.
#
# Логика:
# - Новый email → INSERT с free_used=TRUE, total_generated=1 (использовали бесплатное)
# - Существующий с paid_credits > 0 → ON CONFLICT WHERE matched, paid_credits -= 1
# - Существующий с free_used = FALSE и paid = 0 → free_used = TRUE
# - Существующий с использованным free и paid = 0 → WHERE не matched, RETURNING пуст → 402
#
# Почему НЕ два CTE (как было): WITH-подзапросы видят снимок таблицы ДО запроса,
# поэтому второй CTE не найдёт строку, только что вставленную первым.
CHARGE_SQL = """WITH
ip_check AS (
    -- Сколько free-юзеров уже было с этого IP за последние 24 часа?
    -- Считаем ТОЛЬКО free_used = TRUE (платных не блокируем).
    -- Исключаем собственно текущий email (если он уже есть и refresh-запрос).
    SELECT COUNT(*) AS free_from_ip
    FROM web_users
    WHERE last_ip = {{$json.ip ? "'" + $json.ip + "'::inet" : 'NULL'}}
      AND last_ip IS NOT NULL
      AND free_used = TRUE
      AND first_seen > NOW() - INTERVAL '24 hours'
      AND LOWER(email) <> '{{$json.email}}'
),
abuse_log AS (
    -- Логируем попытку при превышении (но не блокируем существующих пэйд-юзеров)
    INSERT INTO web_abuse_log (email, ip, reason, free_users_from_ip)
    SELECT '{{$json.email}}',
           {{$json.ip ? "'" + $json.ip + "'::inet" : 'NULL'}},
           'ip_too_many_free',
           (SELECT free_from_ip FROM ip_check)
    WHERE (SELECT free_from_ip FROM ip_check) >= 1
      AND NOT EXISTS (
          SELECT 1 FROM web_users
          WHERE LOWER(email) = '{{$json.email}}' AND (paid_credits > 0 OR free_used = FALSE)
      )
    RETURNING id
),
chg AS (
    -- Перед INSERT проверяем: если новый email + грязный IP — НЕ создаём,
    -- блок происходит через SELECT в VALUES. Существующий email пройдёт ON CONFLICT
    -- путь и там сработает WHERE (paid > 0 или новый free + чистый IP).
    INSERT INTO web_users (email, free_used, total_generated, last_seen, last_ip)
    SELECT '{{$json.email}}', TRUE, 1, NOW(),
           {{$json.ip ? "'" + $json.ip + "'::inet" : 'NULL'}}
    WHERE
        -- Юзер уже есть в БД — INSERT провалится через ON CONFLICT (норма)
        EXISTS (SELECT 1 FROM web_users WHERE LOWER(email) = '{{$json.email}}')
        OR
        -- Юзера нет, но IP чистый — создаём
        (SELECT free_from_ip FROM ip_check) < 1
    ON CONFLICT (email) DO UPDATE SET
        paid_credits = CASE
            WHEN web_users.paid_credits > 0 THEN web_users.paid_credits - 1
            ELSE web_users.paid_credits
        END,
        free_used = CASE
            WHEN web_users.paid_credits = 0 AND NOT web_users.free_used THEN TRUE
            ELSE web_users.free_used
        END,
        total_generated = web_users.total_generated + 1,
        last_seen = NOW(),
        last_ip = EXCLUDED.last_ip
    -- БЛОКИРОВКА: новому юзеру (INSERT) запрещаем если с IP уже было >= 1 free.
    -- Существующим paid-юзерам (paid_credits > 0) НЕ блокируем.
    -- Существующих free-юзеров не пускаем повторно (это и так работало).
    WHERE
        -- условие 1: есть paid-кредиты — пропускаем всегда
        web_users.paid_credits > 0
        OR
        -- условие 2: ещё не использовал free И на этом IP < 1 другого free
        (NOT web_users.free_used AND (SELECT free_from_ip FROM ip_check) < 1)
    RETURNING id, paid_credits, free_used, (xmax = 0) AS was_insert
)
SELECT
    (SELECT id FROM chg)                                                    AS user_id,
    '{{$json.email}}'                                                       AS user_email,
    COALESCE((SELECT paid_credits FROM chg), 0)                             AS credits_left,
    COALESCE((SELECT free_used FROM chg),
             (SELECT free_used FROM web_users WHERE email = '{{$json.email}}'),
             FALSE)                                                         AS free_used,
    COALESCE((SELECT paid_credits FROM web_users WHERE email = '{{$json.email}}'),
             0)                                                             AS available_paid,
    COALESCE((SELECT free_used FROM web_users WHERE email = '{{$json.email}}'),
             FALSE)                                                         AS available_free,
    CASE
        WHEN (SELECT was_insert FROM chg) THEN 'free'
        WHEN (SELECT id FROM chg) IS NULL THEN NULL
        WHEN (SELECT paid_credits FROM web_users WHERE email = '{{$json.email}}') > 0 THEN 'paid'
        ELSE 'free'
    END                                                                     AS charge_type,
    -- Маркер ip-фрода: TRUE если на IP уже > 0 free-юзеров за 24ч И chg.id NULL
    CASE
        WHEN (SELECT id FROM chg) IS NULL AND (SELECT free_from_ip FROM ip_check) >= 1 THEN TRUE
        ELSE FALSE
    END                                                                     AS ip_abuse;
-- Подзапросы к web_users в SELECT видят снимок ТАБЛИЦЫ ДО запроса, поэтому
-- available_paid/available_free показывают состояние ПЕРЕД списанием —
-- именно это нужно для 402-ответа и определения charge_type."""

# Обновлённый PG Save: добавляем email, web_user_id, charge_type
PG_SAVE_SQL = """INSERT INTO web_orders (
    session_id, piapi_task_id, source_photo_url, prompt, status,
    email, web_user_id, charge_type
) VALUES (
    '{{$json.session_id}}',
    '{{$json.task_id}}',
    '{{$json.image_url}}',
    '{{ ($json.prompt || "").replace(/'/g, "''") }}',
    '{{$json.status}}',
    '{{$('Validate Input').first().json.email}}',
    {{$('Charge Credit').first().json.user_id}},
    '{{$('Charge Credit').first().json.charge_type || "paid"}}'
) RETURNING order_id;"""

# Status flow: SQL для определения типа списания по task_id
GET_CHARGE_TYPE_SQL = """SELECT
    COALESCE(charge_type, 'paid') AS charge_type
FROM web_orders
WHERE piapi_task_id = '{{$json.query.task_id}}'
LIMIT 1;"""

# Code-нода: если status=completed AND charge_type=free → POST на watermark-сервис,
# подменяем video_url на наш с водяным знаком. Иначе пропускаем.
# Возврат кредита при status=failed. Идемпотентно через WHERE NOT IN ('failed','refunded').
# Всегда выполняется (на каждый poll), но эффект — только при первом detect'е failure.
REFUND_IF_FAILED_SQL = """WITH failed_order AS (
    UPDATE web_orders
    SET status = 'failed'
    WHERE piapi_task_id = '{{$('Status Webhook').first().json.query.task_id}}'
      AND status NOT IN ('failed', 'refunded')
      AND '{{$json.status}}' = 'failed'
    RETURNING id, web_user_id, charge_type, email
),
refunded AS (
    UPDATE web_users wu
    SET
        paid_credits = wu.paid_credits + CASE WHEN fo.charge_type = 'paid' THEN 1 ELSE 0 END,
        free_used    = CASE WHEN fo.charge_type = 'free' THEN FALSE ELSE wu.free_used END,
        total_generated = GREATEST(wu.total_generated - 1, 0)
    FROM failed_order fo
    WHERE wu.id = fo.web_user_id
    RETURNING wu.id, wu.email, fo.charge_type
)
SELECT
    (SELECT COUNT(*)::int FROM refunded) AS refunds,
    (SELECT charge_type FROM refunded LIMIT 1) AS refunded_type,
    (SELECT email FROM refunded LIMIT 1) AS refunded_email;"""

WATERMARK_CODE_JS = r"""
const ps = $input.first().json;
// Gating-модель: free-видео отдаём ЧИСТЫМ (без watermark). Смотреть можно,
// а скачать на лендинге нельзя без оплаты → watermark-сервис больше не нужен.
return [{json: {
    status: ps.status,
    video_url: ps.video_url,
    error: ps.error,
    charge_type: ps.charge_type,
    watermarked: false,
}}];
""".strip()

# Новый JS для Parse Status: выбираем URL в зависимости от charge_type
PARSE_STATUS_JS = r"""
const d = ($('PiAPI Get Status').first().json).data || {};
const st = (d.status || 'unknown').toLowerCase();
const chargeType = (($('PG Get Charge Type').first() || {json:{}}).json.charge_type || 'paid').toLowerCase();
let url = null;
if (st === 'completed') {
    const o = d.output || {};
    if (o.works && o.works[0]) {
        const v = o.works[0].video || {};
        // И free, и paid — чистое видео без watermark Kling.
        // Free защищён gating'ом скачивания на лендинге: смотреть можно, скачать — после оплаты.
        url = v.resource_without_watermark || v.resource || v.url || o.video_url || null;
    } else {
        // Старый формат (нет works[]): только один url доступен
        url = o.video_url || o.video || null;
    }
}
let s = 'processing';
if (st === 'completed') s = 'completed';
else if (st === 'failed') s = 'failed';
return [{json: {
    status: s,
    video_url: url,
    error: d.error && d.error.message ? d.error.message : null,
    charge_type: chargeType
}}];
""".strip()

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
    'error: $json.ip_abuse ? "ip_abuse_detected" : "no_credits", '
    'need_payment: !$json.ip_abuse, '
    'message: $json.ip_abuse '
        '? "С этого устройства уже было создано бесплатное видео. Для продолжения купите тариф." '
        ': "Кредиты закончились. Купите тариф для продолжения.", '
    'credits_left: 0, '
    'available_paid: $json.available_paid || 0, '
    'available_free: $json.available_free === false}) }}'
)

# Возврат кредита, если PiAPI Create Task упал (напр. "failed to freeze credit").
# Кредит списывается в Charge Credit ДО вызова PiAPI, а штатный refund привязан к
# task_id в status-flow. При сбое создания task_id нет → без этого кредит терялся.
REFUND_ON_FAIL_SQL = """UPDATE web_users SET
    paid_credits = paid_credits + CASE WHEN '{{$('Charge Credit').first().json.charge_type}}' = 'paid' THEN 1 ELSE 0 END,
    free_used = CASE WHEN '{{$('Charge Credit').first().json.charge_type}}' = 'free' THEN FALSE ELSE free_used END,
    total_generated = GREATEST(total_generated - 1, 0)
WHERE id = {{$('Charge Credit').first().json.user_id}}
RETURNING id, paid_credits, free_used;"""

# Внятный ответ вместо «Сервер не вернул task_id». 503 + явное «попытка не списана».
RESPOND_503_BODY = (
    '={{ JSON.stringify({'
    'error: "generation_unavailable", '
    'status: "error", '
    'message: "Сервис генерации временно перегружен. Попробуйте через пару минут — '
        'попытка не списана.", '
    'retry: true}) }}'
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

    # 4. Allow? (IF: user_id NOT NULL — кредит списался)
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
                        "leftValue": "={{$json.user_id}}",
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

    # Модифицируем PG Save — обновляем query (добавили charge_type)
    pg_save = by_name["PG Save"]
    pg_save["parameters"]["query"] = PG_SAVE_SQL

    # Модифицируем Respond (success) — добавляем credits_left
    respond_ok = by_name["Respond"]
    respond_ok["parameters"]["responseBody"] = RESPOND_SUCCESS_BODY

    # === ФАЗА 2: Status flow с выбором URL по charge_type ===
    # Сдвигаем status-row ноды на +220 по X, чтобы вписать новую ноду перед PiAPI Get Status
    for n in nodes:
        if n["position"][1] == 300 and n["name"] != "Status Webhook":
            n["position"] = [n["position"][0] + 220, 300]

    # Новая нода: PG Get Charge Type — между Status Webhook и PiAPI Get Status
    pg_get_charge_node = {
        "parameters": {
            "operation": "executeQuery",
            "query": GET_CHARGE_TYPE_SQL,
            "options": {},
        },
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.5,
        "position": [220, 300],
        "id": "pg-get-charge-2026",
        "name": "PG Get Charge Type",
        "credentials": {"postgres": PG_CREDENTIAL},
    }

    # Меняем JS в Parse Status — теперь выбирает URL по charge_type
    parse_status = by_name["Parse Status"]
    parse_status["parameters"]["jsCode"] = PARSE_STATUS_JS

    # Фикс: после вставки PG Get Charge Type, $json в PiAPI Get Status больше
    # не содержит query.task_id (это вывод PG). Явно ссылаемся на Status Webhook.
    piapi_status = by_name["PiAPI Get Status"]
    piapi_status["parameters"]["url"] = (
        "=https://api.piapi.ai/api/v1/task/{{$('Status Webhook').first().json.query.task_id}}"
    )

    # === ФАЗА 2.5: ставим watermark на free-видео через локальный сервис ===
    # Делаем одну Code-ноду с inline httpRequest — это проще, чем IF+HTTP+Build,
    # т.к. ветка с пропуском Apply Watermark ломала ссылки в Respond Status.
    # Добавлен Refund If Needed после Watermark — авто-возврат кредита при status=failed.
    respond_status = by_name["Respond Status"]
    respond_status["position"] = [respond_status["position"][0] + 440, 300]

    watermark_code_node = {
        "parameters": {"jsCode": WATERMARK_CODE_JS},
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [880, 300],
        "id": "watermark-if-free-2026",
        "name": "Watermark If Free",
    }

    # Refund If Needed — Postgres-нода, идемпотентно возвращает кредит при PiAPI failed.
    # Если $json.status != 'failed' или web_orders уже refunded — SQL no-op.
    refund_node = {
        "parameters": {
            "operation": "executeQuery",
            "query": REFUND_IF_FAILED_SQL,
            "options": {},
        },
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.5,
        "position": [1100, 300],
        "id": "refund-if-failed-2026",
        "name": "Refund If Needed",
        "credentials": {"postgres": PG_CREDENTIAL},
    }

    # Respond Status берёт данные ИЗ Watermark If Free (не из Postgres-ноды,
    # т.к. её $json — это {refunds: N})
    respond_status["parameters"]["responseBody"] = (
        '={{ JSON.stringify($(\'Watermark If Free\').first().json) }}'
    )

    # === ФАЗА 3: возврат кредита при сбое PiAPI Create Task ===
    # onError=continueErrorOutput даёт ноде 2-й выход «при ошибке».
    create_task = by_name["PiAPI Create Task"]
    create_task["onError"] = "continueErrorOutput"
    ctx, cty = create_task["position"]
    refund_on_fail_node = {
        "parameters": {"operation": "executeQuery", "query": REFUND_ON_FAIL_SQL, "options": {}},
        "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
        "position": [ctx, cty + 200],
        "id": "refund-on-create-fail-2026", "name": "Refund On Fail",
        "credentials": {"postgres": PG_CREDENTIAL},
    }
    respond_503_node = {
        "parameters": {"respondWith": "json", "responseBody": RESPOND_503_BODY,
            "options": {"responseCode": 503, "responseHeaders": {"entries": [
                {"name": "Access-Control-Allow-Origin", "value": "*"}]}}},
        "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
        "position": [ctx + 220, cty + 200],
        "id": "respond-503-2026", "name": "Respond 503",
    }

    # Вставляем новые ноды
    nodes.extend([validate_node, validated_node, charge_node, allow_node,
                  respond_400, respond_402, pg_get_charge_node,
                  watermark_code_node, refund_node,
                  refund_on_fail_node, respond_503_node])

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
    # Status flow: Status Webhook → PG Get Charge Type → PiAPI Get Status → Parse Status
    new_conn["Status Webhook"] = {
        "main": [[{"node": "PG Get Charge Type", "type": "main", "index": 0}]]
    }
    new_conn["PG Get Charge Type"] = {
        "main": [[{"node": "PiAPI Get Status", "type": "main", "index": 0}]]
    }
    # Parse Status → Watermark If Free → Refund If Needed → Respond Status
    new_conn["Parse Status"] = {
        "main": [[{"node": "Watermark If Free", "type": "main", "index": 0}]]
    }
    new_conn["Watermark If Free"] = {
        "main": [[{"node": "Refund If Needed", "type": "main", "index": 0}]]
    }
    new_conn["Refund If Needed"] = {
        "main": [[{"node": "Respond Status", "type": "main", "index": 0}]]
    }
    # Create Task: успех (выход 0) → как было (Parse); ошибка (выход 1) → Refund On Fail → Respond 503
    new_conn["PiAPI Create Task"] = {
        "main": [
            old_conn.get("PiAPI Create Task", {}).get("main", [[]])[0],
            [{"node": "Refund On Fail", "type": "main", "index": 0}],
        ]
    }
    new_conn["Refund On Fail"] = {
        "main": [[{"node": "Respond 503", "type": "main", "index": 0}]]
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

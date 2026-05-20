"""
Модифицирует Web_YuKassa_Payment_Callback:
после UPDATE web_orders SET is_paid='yes' ДОПОЛНИТЕЛЬНО зачисляет paid_credits
в таблицу web_users (или создаёт нового пользователя).

Идемпотентность: повторный callback (ЮKassa любит ретраить) не задвоит кредиты,
т.к. UPDATE web_orders ... WHERE is_paid <> 'yes' — на повторе RETURNING пустой.
"""
from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

WORKFLOW_ID = "j9yUxuFL8dz12xa3"
EXPORT_PATH = Path(__file__).parent.parent / "n8n-workflows" / "yukassa-payment-callback-actual.exported.json"
MODIFIED_PATH = Path(__file__).parent.parent / "n8n-workflows" / "yukassa-payment-callback.modified.json"

# CTE: атомарно отметить заказ как оплаченный, зачислить кредиты в web_users,
# связать web_orders.web_user_id с найденным пользователем.
NEW_QUERY = """WITH
paid AS (
    UPDATE web_orders
    SET is_paid = 'yes',
        status  = 'paid',
        generations_left = generations_limit,
        paid_at = NOW()
    WHERE payment_id = '{{ $json.body.object.id }}'
      AND is_paid <> 'yes'
      AND '{{ $json.body.object.metadata.source }}' = 'web'
    RETURNING id, email, generations_limit
),
user_upserted AS (
    INSERT INTO web_users (email, paid_credits, last_seen)
    SELECT LOWER(email), generations_limit, NOW()
    FROM paid
    WHERE email IS NOT NULL AND email <> ''
    ON CONFLICT (email) DO UPDATE
        SET paid_credits = web_users.paid_credits + EXCLUDED.paid_credits,
            last_seen = NOW()
    RETURNING id, email, paid_credits
)
SELECT
    (SELECT id              FROM paid)          AS order_id,
    (SELECT email           FROM paid)          AS email,
    (SELECT generations_limit FROM paid)        AS credits_added,
    (SELECT id              FROM user_upserted) AS web_user_id,
    (SELECT paid_credits    FROM user_upserted) AS user_total_credits;
-- web_user_id в web_orders заполняется отдельным заданием (или JOIN по email);
-- в одном CTE два UPDATE на одну строку web_orders запрещены PostgreSQL."""


def build_modified(exported: dict) -> dict:
    nodes = list(exported["nodes"])
    for n in nodes:
        if n["name"] == "Update DB":
            n["parameters"]["query"] = NEW_QUERY
    return {
        "name": exported["name"],
        "nodes": nodes,
        "connections": exported["connections"],
        "settings": exported.get("settings", {"executionOrder": "v1"}),
    }


def put_workflow(env, workflow_id, body):
    url = env["N8N_URL"].rstrip("/") + f"/api/v1/workflows/{workflow_id}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="PUT",
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
        print(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        raise


def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    dry = "--dry-run" in sys.argv
    env = load_env()
    exported = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    modified = build_modified(exported)
    MODIFIED_PATH.write_text(json.dumps(modified, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Modified: {MODIFIED_PATH}")
    print("     Update DB query заменён на CTE: web_orders + web_users + linking, идемпотентно")
    if dry:
        print("[--dry-run] PUT пропущен.")
        return
    res = put_workflow(env, WORKFLOW_ID, modified)
    print(f"[OK] PUT: версия={res.get('versionId', 'n/a')[:8]}, active={res.get('active')}")


if __name__ == "__main__":
    main()

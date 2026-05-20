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
    RETURNING id, email, generations_limit, tariff_code, ref_by
),
tariff_calc AS (
    -- Размер бонусов по тарифу: referrer +10/30/80, friend +3/5/10
    SELECT
        p.id, LOWER(p.email) AS email, p.generations_limit, p.tariff_code, p.ref_by,
        CASE p.tariff_code
            WHEN 'starter'  THEN 10
            WHEN 'pro'      THEN 30
            WHEN 'business' THEN 80
            ELSE 0
        END AS referrer_bonus,
        CASE p.tariff_code
            WHEN 'starter'  THEN 3
            WHEN 'pro'      THEN 5
            WHEN 'business' THEN 10
            ELSE 0
        END AS friend_bonus
    FROM paid p
),
friend_upserted AS (
    -- Друг (тот кто оплатил): +paid_credits + friend_bonus (если по реферальной ссылке).
    -- Также генерим ref_code чтобы он сам мог приглашать.
    -- ref_by фиксируется ТОЛЬКО при первой привязке (COALESCE).
    INSERT INTO web_users (email, paid_credits, ref_by, ref_code, last_seen)
    SELECT
        t.email,
        t.generations_limit + CASE WHEN t.ref_by IS NOT NULL THEN t.friend_bonus ELSE 0 END,
        t.ref_by,
        generate_ref_code(),
        NOW()
    FROM tariff_calc t
    WHERE t.email IS NOT NULL AND t.email <> ''
    ON CONFLICT (email) DO UPDATE SET
        paid_credits = web_users.paid_credits + EXCLUDED.paid_credits,
        ref_code     = COALESCE(web_users.ref_code, EXCLUDED.ref_code),
        ref_by       = COALESCE(web_users.ref_by,   EXCLUDED.ref_by),
        last_seen    = NOW()
    RETURNING id, email, paid_credits, ref_code, ref_by
),
referrer_paid AS (
    -- Реферер получает +referrer_bonus в paid_credits.
    -- Защита от self-referral: wu.email <> friend.email
    UPDATE web_users wu
    SET
        paid_credits         = wu.paid_credits + tc.referrer_bonus,
        bonus_credits_earned = wu.bonus_credits_earned + tc.referrer_bonus,
        paid_referred_count  = wu.paid_referred_count + 1
    FROM tariff_calc tc
    WHERE wu.ref_code = tc.ref_by
      AND tc.ref_by IS NOT NULL
      AND tc.referrer_bonus > 0
      AND LOWER(wu.email) <> tc.email
    RETURNING wu.id AS referrer_user_id, wu.email AS referrer_email, tc.referrer_bonus AS credited
),
referral_log AS (
    -- Записываем в referrals (если ON CONFLICT — пропускаем, идемпотентно)
    INSERT INTO referrals (
        referrer_user_id, referred_user_id, referred_username,
        platform, bonus_paid_credits, web_user_id, friend_web_user_id,
        commission_rub, status, first_paid_at
    )
    SELECT
        rp.referrer_user_id::text,
        f.id::text,
        f.email,
        'web',
        rp.credited,
        rp.referrer_user_id,
        f.id,
        0,
        'paid',
        NOW()
    FROM referrer_paid rp, friend_upserted f
    ON CONFLICT (referred_user_id) DO NOTHING
    RETURNING id
)
SELECT
    (SELECT id              FROM paid)              AS order_id,
    (SELECT email           FROM paid)              AS email,
    (SELECT generations_limit FROM paid)            AS credits_added,
    (SELECT friend_bonus    FROM tariff_calc)       AS friend_bonus,
    (SELECT id              FROM friend_upserted)   AS web_user_id,
    (SELECT ref_code        FROM friend_upserted)   AS friend_ref_code,
    (SELECT paid_credits    FROM friend_upserted)   AS user_total_credits,
    (SELECT referrer_email  FROM referrer_paid)     AS referrer_email,
    (SELECT credited        FROM referrer_paid)     AS referrer_credited;"""


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

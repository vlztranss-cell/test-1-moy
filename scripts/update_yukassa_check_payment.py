"""
Патч Web_YuKassa_Check_Payment: JOIN с web_users чтобы вернуть фронту
ref_code пользователя + статистику реферера. Используется для показа
блока «Пригласи друга» после оплаты.
"""
from __future__ import annotations
import io
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

WORKFLOW_ID = "8WFoMJ9GqrrcC20a"

NEW_QUERY = (
    "SELECT "
    "  wo.payment_id, wo.tariff_code, wo.amount_rub, wo.is_paid, "
    "  wo.generations_limit, wo.generations_left, "
    "  wu.ref_code, "
    "  COALESCE(wu.bonus_credits_earned, 0) AS bonus_credits_earned, "
    "  COALESCE(wu.paid_referred_count, 0) AS paid_referred_count "
    "FROM web_orders wo "
    "LEFT JOIN web_users wu ON LOWER(wu.email) = LOWER(wo.email) "
    "WHERE (wo.email = '{{ $json.body.email }}' OR wo.session_id = '{{ $json.body.session_id }}') "
    "  AND wo.payment_id IS NOT NULL "
    "ORDER BY wo.created_at DESC LIMIT 1"
)

NEW_RESPOND_PAID_BODY = (
    '={ '
    '"status": "succeeded", '
    '"credits": {{ $(\'Check DB\').item.json.generations_limit || 0 }}, '
    '"plan": "{{ $(\'Check DB\').item.json.tariff_code }}", '
    '"ref_code": "{{ $(\'Check DB\').item.json.ref_code || \'\' }}", '
    '"bonus_credits_earned": {{ $(\'Check DB\').item.json.bonus_credits_earned || 0 }}, '
    '"paid_referred_count": {{ $(\'Check DB\').item.json.paid_referred_count || 0 }} '
    '}'
)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    url = env["N8N_URL"].rstrip("/") + f"/api/v1/workflows/{WORKFLOW_ID}"
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}

    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        wf = json.loads(r.read())

    changed = []
    for n in wf["nodes"]:
        if n["name"] == "Check DB":
            n["parameters"]["query"] = NEW_QUERY
            changed.append("Check DB query")
        elif n["name"] == "Respond Paid":
            n["parameters"]["responseBody"] = NEW_RESPOND_PAID_BODY
            changed.append("Respond Paid body")

    body = {
        "name": wf["name"],
        "nodes": wf["nodes"],
        "connections": wf["connections"],
        "settings": wf.get("settings", {"executionOrder": "v1"}),
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="PUT",
        headers={**h, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read())
    print(f"[OK] изменения: {changed}")
    print(f"     версия={res.get('versionId','?')[:8]}")


if __name__ == "__main__":
    main()

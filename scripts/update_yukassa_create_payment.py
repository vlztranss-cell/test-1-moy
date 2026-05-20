"""
Патч Web_YuKassa_Create_Payment: добавляем ref_by в INSERT web_orders.
Фронт передаёт ref_by из localStorage.
"""
from __future__ import annotations
import io
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

WORKFLOW_ID = "mZlY9BXpl8KNGkWO"

NEW_QUERY = (
    "INSERT INTO web_orders ("
    "session_id, email, tariff_code, amount_rub, is_paid, payment_id, "
    "generations_limit, status, ref_by"
    ") VALUES ("
    "'{{ $('Webhook').item.json.body.session_id }}', "
    "'{{ $('Webhook').item.json.body.email }}', "
    "'{{ $('Webhook').item.json.body.plan }}', "
    "{{ $('Webhook').item.json.body.amount }}, "
    "'pending', "
    "'{{ $json.id }}', "
    "CASE WHEN '{{ $('Webhook').item.json.body.plan }}' = 'starter' THEN 10 "
    "     WHEN '{{ $('Webhook').item.json.body.plan }}' = 'pro' THEN 50 "
    "     WHEN '{{ $('Webhook').item.json.body.plan }}' = 'business' THEN 200 "
    "     ELSE 0 END, "
    "'payment_created', "
    "NULLIF('{{ $('Webhook').item.json.body.ref_by || '' }}', '')"
    ")"
)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    url = env["N8N_URL"].rstrip("/") + f"/api/v1/workflows/{WORKFLOW_ID}"
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}

    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        wf = json.loads(r.read())

    for n in wf["nodes"]:
        if n["name"] == "Save to DB":
            n["parameters"]["query"] = NEW_QUERY
            break

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
    print(f"[OK] Save to DB updated (ref_by added). версия={res.get('versionId','?')[:8]}")


if __name__ == "__main__":
    main()

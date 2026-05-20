"""
Обновляет CORS-настройки в YuKassa-workflow'ах на n8n:
allowedOrigins и Access-Control-Allow-Origin → https://botisk.ru

Скрипт идемпотентен — повторный запуск ничего не ломает.
"""
from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

WORKFLOWS = {
    "Web_YuKassa_Create_Payment": "mZlY9BXpl8KNGkWO",
    "Web_YuKassa_Check_Payment":  "8WFoMJ9GqrrcC20a",
    "Web_Photo2Video_MVP":        "9LfeKp4vBn5ZTV2D",  # тоже вызывается из браузера
}

OLD_ORIGIN = "https://vlztranss-cell.github.io"
NEW_ORIGIN = "https://botisk.ru"


def n8n_get(env, workflow_id):
    url = env["N8N_URL"].rstrip("/") + f"/api/v1/workflows/{workflow_id}"
    req = urllib.request.Request(url, headers={"X-N8N-API-KEY": env["N8N_API_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def n8n_put(env, workflow_id, body):
    url = env["N8N_URL"].rstrip("/") + f"/api/v1/workflows/{workflow_id}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={
            "X-N8N-API-KEY": env["N8N_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        raise


def fix_cors_in_node(node):
    """Меняет allowedOrigins и Access-Control-Allow-Origin внутри одной ноды."""
    changed = False
    params = node.get("parameters", {})

    # 1) Webhook-нода: parameters.options.allowedOrigins
    opts = params.get("options", {})
    if opts.get("allowedOrigins") in (OLD_ORIGIN, "*"):
        opts["allowedOrigins"] = NEW_ORIGIN
        params["options"] = opts
        changed = True
    elif opts.get("allowedOrigins") == OLD_ORIGIN:
        opts["allowedOrigins"] = NEW_ORIGIN
        params["options"] = opts
        changed = True

    # 2) respondToWebhook-нода: parameters.options.responseHeaders.entries[*]
    headers = (params.get("options") or {}).get("responseHeaders", {}).get("entries", [])
    for entry in headers:
        if entry.get("name") == "Access-Control-Allow-Origin":
            if entry.get("value") in (OLD_ORIGIN, "*"):
                entry["value"] = NEW_ORIGIN
                changed = True

    return changed


def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()

    for name, wid in WORKFLOWS.items():
        wf = n8n_get(env, wid)
        nodes = wf.get("nodes", [])
        changes = 0
        for n in nodes:
            if fix_cors_in_node(n):
                changes += 1
        if not changes:
            print(f"[skip] {name}: CORS уже {NEW_ORIGIN} (или не настроен)")
            continue

        body = {
            "name": wf["name"],
            "nodes": nodes,
            "connections": wf["connections"],
            "settings": wf.get("settings", {"executionOrder": "v1"}),
        }
        res = n8n_put(env, wid, body)
        print(f"[OK] {name}: исправлено нод={changes}, версия={res.get('versionId', 'n/a')[:8]}")


if __name__ == "__main__":
    main()

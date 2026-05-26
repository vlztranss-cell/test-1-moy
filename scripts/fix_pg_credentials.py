"""
Массовое исправление: меняем неправильный PG credential `6JRfp0UMBDBhhghL`
на правильный `VHwQR0NCUn28HZPP` (тот что реально подключён к photo_bot)
во всех затронутых workflow.

Также для будущих скриптов меняем все .py файлы.
"""
import sys, urllib.request, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
base = env["N8N_URL"].rstrip("/")

WRONG = "6JRfp0UMBDBhhghL"
WRONG_NAME = "Postgres account"
RIGHT = "VHwQR0NCUn28HZPP"
RIGHT_NAME = "ssh root@72.56.96.64"

# 1) Через n8n API — найти все workflows и заменить credential
wfs = json.loads(urllib.request.urlopen(
    urllib.request.Request(base + "/api/v1/workflows?limit=200", headers=h), timeout=20
).read())["data"]

fixed_count = 0
for w in wfs:
    if w.get("isArchived"):
        continue   # архивированные не редактируются через API
    try:
        wf = json.loads(urllib.request.urlopen(
            urllib.request.Request(base + f"/api/v1/workflows/{w['id']}", headers=h), timeout=15
        ).read())
    except Exception as e:
        print(f"  ✗ get {w['name']}: {e}")
        continue
    nodes_changed = False
    for n in wf.get("nodes", []):
        creds = n.get("credentials", {}) or {}
        for ct, cinfo in list(creds.items()):
            if cinfo and cinfo.get("id") == WRONG:
                creds[ct] = {"id": RIGHT, "name": RIGHT_NAME}
                nodes_changed = True
    if not nodes_changed:
        continue
    # PUT принимает только белый список keys. settings бывает с extra-полями
    # которые ломают PUT — оставляем только executionOrder.
    body = {
        "name": wf["name"],
        "nodes": wf["nodes"],
        "connections": wf["connections"],
        "settings": {"executionOrder": "v1"},
    }
    try:
        urllib.request.urlopen(urllib.request.Request(
            base + f"/api/v1/workflows/{w['id']}",
            data=json.dumps(body).encode(), method="PUT",
            headers={**h, "Content-Type": "application/json"}), timeout=30
        )
        print(f"  ✓ fixed: {w['name']}")
        fixed_count += 1
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:200]
        print(f"  ✗ {w['name']}: HTTP {e.code} {err_body}")
    except Exception as e:
        print(f"  ✗ {w['name']}: {e}")

print(f"\nИтого: исправлено {fixed_count} workflows")

# 2) Чиним исходники .py чтобы будущие deploy не использовали неправильный
import re
scripts_dir = Path(__file__).resolve().parent
for py in scripts_dir.glob("create_*.py"):
    text = py.read_text(encoding="utf-8")
    if WRONG in text:
        new_text = text.replace(WRONG, RIGHT).replace(f'"name": "{WRONG_NAME}"', f'"name": "{RIGHT_NAME}"')
        py.write_text(new_text, encoding="utf-8")
        print(f"  ✓ {py.name} — обновлён source")

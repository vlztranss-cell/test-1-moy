#!/usr/bin/env python3
"""
Ротация n8n API-ключа во ВСЕХ местах разом.

Что делает:
  1. Берёт СТАРЫЙ ключ из локального .env (N8N_API_KEY).
  2. Спрашивает НОВЫЙ ключ через getpass (скрытый ввод — НЕ в чат, НЕ в историю).
  3. Меняет старый→новый: локальный .env, VPS /srv/markirovka2d/.env,
     и 3 воркфлоу n8n (Autopilot - Monitor & Restart, Dashboard API,
     Web_Generation_Alert) через n8n API.
  4. Напоминает обновить 2 credentials в n8n UI (через API их данные не правятся).

ПОРЯДОК (чтобы без простоя):
  - В n8n UI создай НОВЫЙ API-ключ, НЕ отзывая старый.
  - Запусти этот скрипт, вставь новый ключ.
  - Проверь, что всё работает → потом отзови старый ключ в UI.

Запуск:  py -3 scripts/rotate_n8n_key.py
"""
import sys, json, urllib.request, getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from ssh import vps_run

N8N = "https://n8n.24isk.ru"
WORKFLOWS = [
    "Autopilot - Monitor & Restart",
    "Dashboard API — Project Control Panel",
    "Web_Generation_Alert",
]
LOCAL_ENV = Path(__file__).resolve().parent.parent / ".env"
VPS_ENV = "/srv/markirovka2d/.env"


def main():
    env = load_env()
    old = env.get("N8N_API_KEY", "").strip()
    if not old:
        print("❌ В локальном .env нет N8N_API_KEY — нечего заменять.")
        return
    print(f"Старый ключ из .env: …{old[-12:]} (len {len(old)})")
    new = getpass.getpass("Вставь НОВЫЙ n8n API key (ввод скрыт): ").strip()
    if not new:
        print("Пусто — отмена."); return
    if new == old:
        print("Новый совпадает со старым — отмена."); return
    if "|" in new or "|" in old:
        print("❌ В ключе есть '|' — sed сломается, обнови VPS .env вручную.");

    # 1) локальный .env
    t = LOCAL_ENV.read_text(encoding="utf-8")
    if old in t:
        LOCAL_ENV.write_text(t.replace(old, new), encoding="utf-8")
        print("✓ локальный .env обновлён")
    else:
        print("• в локальном .env старого значения нет (пропуск)")

    # 2) VPS .env
    out, err = vps_run(f"sed -i 's|{old}|{new}|g' {VPS_ENV} && grep -c '^N8N_API_KEY' {VPS_ENV}")
    print("✓ VPS .env:", (out.strip() or err.strip()[:120]))

    # 3) n8n воркфлоу через API (авторизуемся НОВЫМ ключом)
    H = {"X-N8N-API-KEY": new, "Content-Type": "application/json", "Accept": "application/json"}

    def api(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{N8N}/api/v1{path}", data=data, method=method, headers=H)
        return json.loads(urllib.request.urlopen(req, timeout=25).read())

    try:
        wfs = api("GET", "/workflows?limit=250").get("data", [])
    except Exception as e:
        print(f"❌ n8n API не отвечает новым ключом ({str(e)[:80]}). "
              f"Проверь, что новый ключ создан и активен. Воркфлоу не тронуты.")
        return
    byname = {w["name"]: w["id"] for w in wfs}
    for name in WORKFLOWS:
        wid = byname.get(name)
        if not wid:
            print(f"  ⚠ воркфлоу не найден: {name}"); continue
        wf = api("GET", f"/workflows/{wid}")
        dumped = json.dumps(wf["nodes"], ensure_ascii=False)
        if old not in dumped:
            print(f"  • {name}: старого ключа в нодах нет (ок)"); continue
        wf["nodes"] = json.loads(dumped.replace(old, new))
        api("PUT", f"/workflows/{wid}", {
            "name": wf["name"], "nodes": wf["nodes"],
            "connections": wf["connections"], "settings": wf.get("settings", {}),
        })
        print(f"  ✓ {name}: ключ заменён")

    print("\n=== ВРУЧНУЮ в n8n UI (через API данные credentials не правятся): ===")
    print("  Credentials → N8N_CONTENT_READ и N8N_CONTENT_RW (httpHeaderAuth)")
    print("  → значение заголовка X-N8N-API-KEY = новый ключ.")
    print("После проверки — отзови старый ключ в n8n UI.")


if __name__ == "__main__":
    main()

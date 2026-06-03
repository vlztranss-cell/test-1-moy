#!/usr/bin/env python3
"""
Ротация секретов разом: НОВЫЙ n8n API-ключ + НОВЫЙ bot-токен @iskdenisAI_bot.

Запускать в СВОЁМ терминале (ввод ключей скрытый, в чат не попадает):
    py -3 scripts/rotate_n8n_key.py
или прямо из этой сессии префиксом «!»:
    ! py -3 scripts/rotate_n8n_key.py

Что делает:
  n8n-ключ (старый берётся из .env):
    - локальный .env  (N8N_API_KEY)
    - VPS /srv/markirovka2d/.env
    - 3 воркфлоу n8n через API (Autopilot - Monitor & Restart, Dashboard API,
      Web_Generation_Alert)
  bot-токен:
    - локальный .env  (CONTROL_TG_TOKEN)  — для control.py
  Вручную (через API не правится): credentials N8N_CONTENT_READ / N8N_CONTENT_RW.

ПОРЯДОК без простоя: в n8n UI создай НОВЫЙ ключ (старый не отзывай) → запусти скрипт →
проверь → отзови старый. Бот: @BotFather → /revoke @iskdenisAI_bot → новый токен.
"""
import sys, json, urllib.request, getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from ssh import vps_run

N8N = "https://n8n.24isk.ru"
WORKFLOWS = ["Autopilot - Monitor & Restart", "Dashboard API — Project Control Panel", "Web_Generation_Alert"]
LOCAL_ENV = Path(__file__).resolve().parent.parent / ".env"
VPS_ENV = "/srv/markirovka2d/.env"


def ask(prompt):
    """Скрытый ввод; если терминал не поддерживает — обычный (значение видно только в твоём терминале)."""
    try:
        v = getpass.getpass(prompt)
    except Exception:
        v = input(prompt)
    return v.strip()


def set_env_var(text, key, value):
    """Заменить или добавить строку KEY=value в содержимом .env."""
    lines = text.splitlines()
    done = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith(key + "="):
            lines[i] = f"{key}={value}"
            done = True
            break
    if not done:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def main():
    env = load_env()
    old = env.get("N8N_API_KEY", "").strip()

    print("=== Ротация секретов ===")
    new_n8n = ask("НОВЫЙ n8n API key (Enter чтобы пропустить): ")
    new_bot = ask("НОВЫЙ bot-токен @iskdenisAI_bot (Enter чтобы пропустить): ")
    if not new_n8n and not new_bot:
        print("Оба пустые — отмена."); return

    text = LOCAL_ENV.read_text(encoding="utf-8")

    # --- n8n ключ ---
    if new_n8n:
        if not old:
            print("⚠ В .env нет старого N8N_API_KEY — впишу новый, но в VPS/воркфлоу заменять нечего.")
        if "|" in new_n8n or "|" in (old or ""):
            print("⚠ В ключе символ '|' — VPS .env обнови вручную.")
        text = set_env_var(text, "N8N_API_KEY", new_n8n)
        print("✓ локальный .env: N8N_API_KEY")
        if old:
            out, err = vps_run(f"sed -i 's|{old}|{new_n8n}|g' {VPS_ENV} && grep -c '^N8N_API_KEY' {VPS_ENV}")
            print("✓ VPS .env:", (out.strip() or err.strip()[:120]))
        # воркфлоу через API (авторизация НОВЫМ ключом)
        H = {"X-N8N-API-KEY": new_n8n, "Content-Type": "application/json", "Accept": "application/json"}

        def api(method, path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(f"{N8N}/api/v1{path}", data=data, method=method, headers=H)
            return json.loads(urllib.request.urlopen(req, timeout=25).read())

        try:
            wfs = api("GET", "/workflows?limit=250").get("data", [])
            byname = {w["name"]: w["id"] for w in wfs}
            for name in WORKFLOWS:
                wid = byname.get(name)
                if not wid:
                    print(f"  ⚠ воркфлоу не найден: {name}"); continue
                wf = api("GET", f"/workflows/{wid}")
                dumped = json.dumps(wf["nodes"], ensure_ascii=False)
                if old and old in dumped:
                    wf["nodes"] = json.loads(dumped.replace(old, new_n8n))
                    api("PUT", f"/workflows/{wid}", {"name": wf["name"], "nodes": wf["nodes"],
                        "connections": wf["connections"], "settings": wf.get("settings", {})})
                    print(f"  ✓ {name}: ключ заменён")
                else:
                    print(f"  • {name}: старого ключа в нодах нет")
        except Exception as e:
            print(f"  ⚠ n8n API не ответил новым ключом ({str(e)[:80]}) — воркфлоу не тронуты, проверь ключ.")

    # --- bot токен ---
    if new_bot:
        text = set_env_var(text, "CONTROL_TG_TOKEN", new_bot)
        print("✓ локальный .env: CONTROL_TG_TOKEN")

    LOCAL_ENV.write_text(text, encoding="utf-8")

    print("\n=== ВРУЧНУЮ в n8n UI: credentials N8N_CONTENT_READ и N8N_CONTENT_RW → X-N8N-API-KEY = новый ключ.")
    print("После проверки — отзови старый n8n-ключ в UI. Готово.")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Control Panel - Claude управляет n8n напрямую через API.
Использование: python control.py <command> [target]

Команды:
  status          - показать активные workflows
  errors          - последние ошибки
  activate <имя>  - включить workflow
  deactivate <имя> - выключить workflow
  notify <текст>  - отправить сообщение в Telegram (автоудаление 60с)
  update_status   - обновить статусное сообщение в Telegram
  online          - пометить Claude как онлайн
  offline         - пометить Claude как офлайн
"""
import json, sys, io, urllib.request, urllib.parse, time, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

N8N_URL = "https://n8n.24isk.ru/api/v1"
N8N_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI5YzAwMmRkNC1mMDI2LTQ1MWQtYWZmMC0wYzNlNmU2MmE0MjgiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwianRpIjoiMTM3M2VhYTctYjBlNC00ZjM0LWJkYjQtYzg0OWFlMDBlNzU1IiwiaWF0IjoxNzc5MDU0NTAzLCJleHAiOjE3ODY4Mjc2MDB9.n9QAgjQ_6Jags29Rb4qE8okSOVw_386xgMNYnIFru1A"
TG_TOKEN = "8255552951:AAES5Z0OfbxaZyWd4H9nZMVovDwKr1AePds"
CHAT_ID = "411823087"
PINNED_MSG_ID = 6  # закреплённое сообщение с командами
STATUS_FILE = "C:/AI moy/test 1 moy/.status_msg_id"


def api_get(path):
    req = urllib.request.Request(f"{N8N_URL}{path}", headers={"X-N8N-API-KEY": N8N_KEY})
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def api_post(path):
    req = urllib.request.Request(f"{N8N_URL}{path}", method="POST", headers={"X-N8N-API-KEY": N8N_KEY})
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def tg_send(text):
    """Отправить сообщение, вернуть message_id."""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    return result["result"]["message_id"]


def tg_edit(msg_id, text):
    """Редактировать существующее сообщение."""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/editMessageText"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "message_id": msg_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except Exception:
        return None


def tg_delete(msg_id):
    """Удалить сообщение."""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/deleteMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "message_id": msg_id}).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def tg_notify(text, auto_delete_sec=60):
    """Отправить временное уведомление (удалится автоматически)."""
    msg_id = tg_send(text)
    print(f"Отправлено (удалится через {auto_delete_sec}с): {text[:50]}")
    # Не можем ждать в основном потоке, но n8n autopilot будет чистить
    # Запишем ID для удаления
    cleanup_file = "C:/AI moy/test 1 moy/.tg_cleanup"
    entry = {"msg_id": msg_id, "delete_at": int(time.time()) + auto_delete_sec}
    entries = []
    if os.path.exists(cleanup_file):
        with open(cleanup_file, "r") as f:
            entries = json.load(f)
    entries.append(entry)
    with open(cleanup_file, "w") as f:
        json.dump(entries, f)
    return msg_id


def tg_cleanup():
    """Удалить сообщения, у которых истёк таймер."""
    cleanup_file = "C:/AI moy/test 1 moy/.tg_cleanup"
    if not os.path.exists(cleanup_file):
        return
    with open(cleanup_file, "r") as f:
        entries = json.load(f)
    remaining = []
    now = int(time.time())
    for e in entries:
        if now >= e["delete_at"]:
            tg_delete(e["msg_id"])
        else:
            remaining.append(e)
    with open(cleanup_file, "w") as f:
        json.dump(remaining, f)


def get_status_msg_id():
    """Получить ID статусного сообщения."""
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, "r") as f:
            return int(f.read().strip())
    return None


def save_status_msg_id(msg_id):
    with open(STATUS_FILE, "w") as f:
        f.write(str(msg_id))


def build_status_text(claude_online=True):
    """Сформировать текст статусного сообщения."""
    try:
        data = api_get("/workflows")
        active = [w for w in data["data"] if w["active"] and not w.get("isArchived")]
        active_count = len(active)
    except Exception:
        active = []
        active_count = "?"

    try:
        err_data = api_get("/executions?status=error&limit=3")
        errors = err_data.get("data", [])
        last_error_time = errors[0].get("stoppedAt", "")[:16] if errors else "нет"
    except Exception:
        last_error_time = "?"

    now = time.strftime("%d.%m.%Y %H:%M", time.localtime())

    if claude_online:
        claude_status = "🟢 ОНЛАЙН"
    else:
        claude_status = "🔴 ОФЛАЙН (сервер выключен)"

    text = f"""<b>📊 ТЕКУЩИЙ СТАТУС</b>
━━━━━━━━━━━━━━━━━━━━━
🤖 Claude: {claude_status}
⚙️ Активных workflows: {active_count}
❌ Последняя ошибка: {last_error_time}
🕐 Обновлено: {now}
━━━━━━━━━━━━━━━━━━━━━
<b>Автопилот:</b> мониторинг каждые 5 мин
<b>Авто-перезапуск:</b> включён"""

    if not claude_online:
        text += "\n\n⚠️ Claude не может выполнять команды.\nАвтопилот n8n продолжает работать."

    return text


def cmd_status():
    data = api_get("/workflows")
    active = [w for w in data["data"] if w["active"] and not w.get("isArchived")]
    print(f"Активных workflows: {len(active)}")
    for w in active:
        print(f"  [ON] {w['name']}")


def cmd_errors():
    data = api_get("/executions?status=error&limit=5")
    errors = data.get("data", [])
    if not errors:
        print("Ошибок нет!")
        return
    print(f"Последние ошибки ({len(errors)}):")
    for e in errors:
        name = e.get("workflowData", {}).get("name", e.get("workflowId", "?"))
        t = e.get("stoppedAt", "?")[:19]
        print(f"  [{t}] {name}")


def cmd_activate(target):
    data = api_get("/workflows")
    wf = next((w for w in data["data"] if target.lower() in w["name"].lower() and not w.get("isArchived")), None)
    if not wf:
        print(f"Не найден: {target}")
        return
    api_post(f"/workflows/{wf['id']}/activate")
    print(f"Включён: {wf['name']}")
    tg_notify(f"✅ Включён: {wf['name']}", auto_delete_sec=30)


def cmd_deactivate(target):
    data = api_get("/workflows")
    wf = next((w for w in data["data"] if target.lower() in w["name"].lower() and not w.get("isArchived")), None)
    if not wf:
        print(f"Не найден: {target}")
        return
    api_post(f"/workflows/{wf['id']}/deactivate")
    print(f"Выключен: {wf['name']}")
    tg_notify(f"⏹ Выключен: {wf['name']}", auto_delete_sec=30)


def cmd_notify(text):
    tg_notify(text, auto_delete_sec=60)


def cmd_update_status(online=True):
    """Обновить или создать статусное сообщение."""
    tg_cleanup()  # сначала почистим старые
    text = build_status_text(claude_online=online)
    msg_id = get_status_msg_id()
    if msg_id:
        result = tg_edit(msg_id, text)
        if result:
            print(f"Статус обновлён (msg {msg_id})")
            return
    # Если не удалось отредактировать — отправить новое
    new_id = tg_send(text)
    save_status_msg_id(new_id)
    print(f"Статус отправлен (msg {new_id})")


def cmd_online():
    cmd_update_status(online=True)


def cmd_offline():
    cmd_update_status(online=False)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    # Always cleanup expired messages
    tg_cleanup()

    cmd = args[0]
    target = " ".join(args[1:]) if len(args) > 1 else ""

    if cmd == "status":
        cmd_status()
    elif cmd == "errors":
        cmd_errors()
    elif cmd == "activate":
        cmd_activate(target)
    elif cmd == "deactivate":
        cmd_deactivate(target)
    elif cmd == "notify":
        cmd_notify(target)
    elif cmd == "update_status":
        cmd_update_status()
    elif cmd == "online":
        cmd_online()
    elif cmd == "offline":
        cmd_offline()
    else:
        print(f"Неизвестная команда: {cmd}")
        print(__doc__)

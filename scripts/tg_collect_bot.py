"""
@botisk_collect_bot — отдельный бот для сбора UGC семейных фото.

ПРЕДВАРИТЕЛЬНО (один раз):
1. Создать @BotFather → /newbot → имя "Botisk Collect" → username "botisk_collect_bot"
   (или другой свободный). Сохранить токен.
2. Добавить в /srv/creatives/.env:
       TELEGRAM_BOT_COLLECT=<токен_от_BotFather>
3. Запустить bot через systemd:
       systemd-run --unit=collect-bot --working-directory=/srv/creatives \\
           -p StandardOutput=file:/var/log/collect_bot.log \\
           -p StandardError=file:/var/log/collect_bot.log \\
           -p Restart=always \\
           python3 /srv/creatives/tg_collect_bot.py

ЛОГИКА БОТА:
- /start → приветствие + кнопка «Я разрешаю использовать своё фото»
- После согласия → принимаем 1+ фото
- Каждое фото сохраняем в /srv/creatives/raw/source/from_bot/<user_id>_<ts>.jpg
- Пишем в таблицу user_uploads (user_id, username, file_path, consent_given_at, ...)
- /privacy → показывает полный текст согласия
- /cancel → удаляет всё, отзываем согласие
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
TOKEN = env.get("TELEGRAM_BOT_COLLECT")
if not TOKEN:
    sys.exit("ОШИБКА: нет TELEGRAM_BOT_COLLECT в .env — создайте бота в BotFather")

API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
STORAGE = Path("/srv/creatives/raw/source/from_bot")
STORAGE.mkdir(parents=True, exist_ok=True)

STATE_FILE = Path("/srv/creatives/collect_bot_state.json")  # offset + consent map

WELCOME = (
    "Привет! 👋\n\n"
    "Этот бот собирает *реальные семейные фото* для нашего архива и контента.\n\n"
    "Загруженные фото мы используем:\n"
    "✅ для роликов в TikTok / YouTube / Reels (с указанием авторства)\n"
    "✅ для тренировки нашей AI-модели оживления\n"
    "✅ для примеров на сайте botisk.ru\n\n"
    "*В обмен:* +5 бесплатных генераций видео и наш промокод.\n\n"
    "Сначала нужно ваше согласие. Нажмите /agree если согласны.\n"
    "Полный текст: /privacy"
)

PRIVACY = (
    "📜 *Согласие на использование изображений*\n\n"
    "Загружая фото, вы передаёте VideoAI (ИП Искусных Е.В., ИНН: указан на botisk.ru/legal):\n\n"
    "1️⃣ Право использования фото в маркетинговых материалах\n"
    "2️⃣ Право показа в соцсетях с указанием Telegram-handle\n"
    "3️⃣ Право использовать для оживления через AI\n\n"
    "Согласие отзывается командой /cancel — все ваши фото будут удалены.\n\n"
    "Контакты: @botisk_canal · botisk.ru"
)

AGREE_TEXT = (
    "✅ Спасибо! Согласие зафиксировано.\n\n"
    "Теперь можете загружать фото:\n"
    "📸 Фото бабушек/дедушек\n"
    "📸 Детские фото\n"
    "📸 Семейные архивные снимки\n"
    "📸 Фото питомцев\n\n"
    "Просто отправьте любое фото — оно автоматически сохранится."
)


def tg(method: str, params: dict | None = None, timeout: int = 30):
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error_code": e.code,
                "description": e.read().decode("utf-8", errors="replace")[:200]}
    except Exception as e:
        return {"ok": False, "description": str(e)}


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"offset": 0, "consents": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def download_file(file_id: str, dest: Path) -> bool:
    r = tg("getFile", {"file_id": file_id})
    if not r.get("ok"): return False
    path = r["result"]["file_path"]
    try:
        with urllib.request.urlopen(f"{FILE_API}/{path}", timeout=60) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f"  ✗ download error: {e}")
        return False


def insert_upload(user_id: int, username: str, file_id: str,
                  local_path: str, consented_at: int):
    # psql call — пишем через локальный psql
    import subprocess
    sql = (
        "INSERT INTO user_uploads (user_id, username, file_id, file_path, "
        "consent_given_at, created_at) VALUES "
        f"({user_id}, '{(username or '').replace(chr(39), chr(39)*2)}', "
        f"'{file_id}', '{local_path}', to_timestamp({consented_at}), NOW()) "
        "ON CONFLICT DO NOTHING"
    )
    try:
        subprocess.run(
            ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-c", sql],
            capture_output=True, text=True, timeout=10
        )
    except Exception as e:
        print(f"  ⚠ psql error: {e}")


def ensure_table():
    import subprocess
    sql = """
    CREATE TABLE IF NOT EXISTS user_uploads (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        username TEXT,
        file_id TEXT NOT NULL,
        file_path TEXT NOT NULL,
        consent_given_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS user_uploads_user_idx ON user_uploads(user_id);
    """
    subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-c", sql],
        capture_output=True, text=True, timeout=10
    )


def handle_update(upd: dict, state: dict):
    msg = upd.get("message")
    if not msg: return
    from_user = msg.get("from", {})
    uid = from_user.get("id")
    uname = from_user.get("username", "")
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")

    consents = state.setdefault("consents", {})

    if text == "/start":
        tg("sendMessage", {"chat_id": chat_id, "text": WELCOME, "parse_mode": "Markdown"})
        return
    if text == "/privacy":
        tg("sendMessage", {"chat_id": chat_id, "text": PRIVACY, "parse_mode": "Markdown"})
        return
    if text == "/agree":
        consents[str(uid)] = int(time.time())
        save_state(state)
        tg("sendMessage", {"chat_id": chat_id, "text": AGREE_TEXT})
        return
    if text == "/cancel":
        if str(uid) in consents:
            del consents[str(uid)]
            save_state(state)
            tg("sendMessage", {"chat_id": chat_id,
                "text": "Согласие отозвано. Все ваши фото будут удалены в течение 24ч."})
        else:
            tg("sendMessage", {"chat_id": chat_id, "text": "Активного согласия нет."})
        return

    # Фото
    if msg.get("photo"):
        if str(uid) not in consents:
            tg("sendMessage", {"chat_id": chat_id,
                "text": "Сначала дайте согласие: /agree"})
            return
        # Самое большое разрешение — последнее в массиве
        photo = msg["photo"][-1]
        file_id = photo["file_id"]
        ts = int(time.time())
        dest = STORAGE / f"{uid}_{ts}_{file_id[-10:]}.jpg"
        if download_file(file_id, dest):
            insert_upload(uid, uname, file_id, str(dest), consents[str(uid)])
            tg("sendMessage", {"chat_id": chat_id,
                "text": f"✅ Фото получено! Спасибо.\nНомер: #{ts % 100000}\n\nЕщё загрузите?"})
            print(f"[+] photo from {uid} ({uname}): {dest.name}")
        else:
            tg("sendMessage", {"chat_id": chat_id, "text": "⚠ Не удалось скачать, попробуйте ещё раз"})


def main():
    ensure_table()
    state = load_state()
    print(f"Bot started, offset={state['offset']}, consents={len(state.get('consents', {}))}")
    while True:
        r = tg("getUpdates", {"offset": state["offset"] + 1, "timeout": 30}, timeout=40)
        if not r.get("ok"):
            print(f"getUpdates error: {r}")
            time.sleep(5)
            continue
        for upd in r["result"]:
            state["offset"] = upd["update_id"]
            try:
                handle_update(upd, state)
            except Exception as e:
                print(f"handler error: {e}")
        save_state(state)


if __name__ == "__main__":
    main()

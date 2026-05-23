"""
Упаковка Telegram-канала @botisk_canal через Bot API.

Бот «Оживить фото» (TELEGRAM_BOT_PHOTO2VIDEO) уже добавлен админом канала
с правом «Изменение профиля канала».

Что делает:
1. setChatTitle — название канала
2. setChatDescription — описание канала
3. setChatPhoto — аватар (из /srv/admin/og-image.png)
4. sendMessage + pinChatMessage — закреплённое приветствие

Запуск:
    python tg_channel_packaging.py
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

CHANNEL_TITLE = "VideoAI — оживить фото"

CHANNEL_DESCRIPTION = (
    "🎬 Оживляем ваши фото с помощью AI\n"
    "✅ Бабушки и дедушки снова улыбаются\n"
    "✅ Питомцы — снова рядом\n"
    "✅ Семейный архив оживает\n\n"
    "🎁 Первое видео бесплатно: @VideoAI_24isk_bot\n"
    "🌐 botisk.ru"
)

PINNED_MESSAGE = """🎬 *Добро пожаловать в VideoAI\\!*

Здесь мы превращаем *статичные фото* в живые видео с помощью искусственного интеллекта 🤖

✨ *Что мы делаем:*
🔹 Оживляем фото бабушек и дедушек
🔹 Возвращаем движение детским снимкам
🔹 Сохраняем память о любимых питомцах
🔹 Превращаем свадебные фото в кино

💎 *Как попробовать бесплатно:*
1️⃣ Открой бота: [@VideoAI\\_24isk\\_bot](https://t.me/VideoAI_24isk_bot)
2️⃣ Загрузи фото
3️⃣ Получи живое видео через 1 минуту 🎁

🌐 Сайт: [botisk\\.ru](https://botisk.ru)

_Подписывайся, чтобы первым узнавать о новых возможностях AI и видеть наши лучшие работы\\!_"""


def tg_api(method: str, token: str, params: dict = None, files: dict = None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:
        # multipart upload
        boundary = "----VideoAIBoundary7MA4YWxkTrZu0gW"
        body = b""
        for k, v in (params or {}).items():
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
            body += str(v).encode("utf-8") + b"\r\n"
        for k, (fname, fdata, ctype) in files.items():
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'.encode()
            body += f"Content-Type: {ctype}\r\n\r\n".encode()
            body += fdata + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        data = json.dumps(params or {}).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                      headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error_code": e.code, "description": body_err[:500]}


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    token = env["TELEGRAM_BOT_PHOTO2VIDEO"]
    chat_id = env["TG_CHANNEL_CHAT_ID"]

    print(f"→ канал: {env.get('TG_CHANNEL_HANDLE', chat_id)}")

    # 1. setChatTitle
    r = tg_api("setChatTitle", token, {"chat_id": chat_id, "title": CHANNEL_TITLE})
    print(f"  setChatTitle: {'✓' if r.get('ok') else '✗ ' + str(r.get('description', ''))}")

    # 2. setChatDescription
    r = tg_api("setChatDescription", token,
               {"chat_id": chat_id, "description": CHANNEL_DESCRIPTION})
    print(f"  setChatDescription: {'✓' if r.get('ok') else '✗ ' + str(r.get('description', ''))}")

    # 3. setChatPhoto — берём og-image с сервера если есть
    photo_path = Path("/srv/admin/og-image.png")
    if not photo_path.exists():
        # Локально на Windows — попробуем найти любой подходящий
        local = Path(__file__).parent.parent / "landing" / "og-image.png"
        if local.exists():
            photo_path = local
    if photo_path.exists():
        with open(photo_path, "rb") as f:
            photo_data = f.read()
        r = tg_api("setChatPhoto", token, {"chat_id": chat_id},
                   files={"photo": ("avatar.png", photo_data, "image/png")})
        print(f"  setChatPhoto: {'✓' if r.get('ok') else '✗ ' + str(r.get('description', ''))}")
    else:
        print(f"  setChatPhoto: ⊘ нет файла {photo_path}")

    # 4. Закреплённое сообщение
    r = tg_api("sendMessage", token, {
        "chat_id": chat_id,
        "text": PINNED_MESSAGE,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": False,
    })
    if r.get("ok"):
        msg_id = r["result"]["message_id"]
        print(f"  sendMessage: ✓ id={msg_id}")
        r2 = tg_api("pinChatMessage", token,
                    {"chat_id": chat_id, "message_id": msg_id, "disable_notification": True})
        print(f"  pinChatMessage: {'✓' if r2.get('ok') else '✗ ' + str(r2.get('description', ''))}")
    else:
        print(f"  sendMessage: ✗ {r.get('description', '')}")

    print("\n✅ Упаковка TG-канала завершена")


if __name__ == "__main__":
    main()

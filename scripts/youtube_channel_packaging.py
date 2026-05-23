"""
Упаковка YouTube канала VideoAI: описание, ключевые слова, 4 плейлиста.

ВАЖНО: требует scope https://www.googleapis.com/auth/youtube (read+write),
которого нет у текущего refresh_token (только upload+readonly).

Перед запуском пользователь должен:
1. Открыть myaccount.google.com/permissions → revoke "VideoAI Uploader"
2. Запустить scripts/youtube_oauth_setup.py с обновлёнными SCOPES
   (auto: добавь 'https://www.googleapis.com/auth/youtube' к SCOPES)
3. Авторизоваться выбрав ИМЕННО канал VideoAI (UC6wfhx42PKNBW4ISk5Ai8ZA)

После этого этот скрипт можно запускать в авто-режиме.
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from youtube_uploader import _get_access_token, API_BASE

CHANNEL_DESCRIPTION = """🎬 VideoAI — оживляем ваши фото с помощью искусственного интеллекта.

Превращаем статичные фотографии в живые 5-секундные видео:
✅ Бабушки и дедушки снова улыбаются
✅ Детские фото оживают в семейном архиве
✅ Питомцы, которых уже нет рядом — снова рядом
✅ Свадебные и любовные истории в движении

🎁 Первая генерация — БЕСПЛАТНО на botisk.ru
💎 Платные тарифы от 99₽ за видео

👉 Сайт: https://botisk.ru
👉 Telegram-бот: https://t.me/VideoAI_24isk_bot
👉 Поддержка: https://t.me/botisk_canal

#оживитьфото #aiвидео #нейросеть #семейноевидео #подарок"""

CHANNEL_KEYWORDS = (
    "оживить фото, AI видео, оживление фотографий, нейросеть для фото, "
    "семейное видео, подарок маме, бабушка молодая, питомец оживить, "
    "Kling AI, photo to video, animate photo"
)

PLAYLISTS = [
    {
        "title": "🎞 Память — оживление архивных фото",
        "description": "Бабушки, дедушки, родители молодыми. AI оживляет старые фотографии.",
        "tag": "memory",
    },
    {
        "title": "👶 Детство — детские фото в движении",
        "description": "Подарок маме от внуков. Первые шаги, школьные фото оживают.",
        "tag": "babies",
    },
    {
        "title": "🐶 Питомцы — в память о любимцах",
        "description": "Когда питомца больше нет рядом — он остаётся в живом видео.",
        "tag": "pets",
    },
    {
        "title": "💍 Love Story — свадьбы и годовщины",
        "description": "Свадебные фото в движении. Подарок-сюрприз на годовщину.",
        "tag": "love",
    },
]


def api_call(method: str, endpoint: str, access_token: str, body: dict = None):
    url = API_BASE + endpoint
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {method} {endpoint}: {body_err[:500]}")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    channel_id = env.get("YOUTUBE_CHANNEL_ID")
    if not channel_id:
        raise RuntimeError("Нет YOUTUBE_CHANNEL_ID в .env")

    token = _get_access_token(env)
    print(f"✓ access_token получен")

    # Текущее состояние канала
    cur = api_call("GET", f"/channels?part=brandingSettings,snippet&id={channel_id}",
                   token)
    if not cur.get("items"):
        raise RuntimeError(f"Канал {channel_id} не найден")
    channel = cur["items"][0]
    print(f"✓ канал: {channel['snippet']['title']}")

    # Обновление branding
    branding = channel.get("brandingSettings", {})
    branding.setdefault("channel", {})
    branding["channel"]["description"] = CHANNEL_DESCRIPTION
    branding["channel"]["keywords"] = CHANNEL_KEYWORDS
    branding["channel"]["country"] = "RU"
    branding["channel"]["defaultLanguage"] = "ru"

    update_body = {"id": channel_id, "brandingSettings": branding}
    res = api_call("PUT", "/channels?part=brandingSettings", token, update_body)
    print(f"✓ branding обновлён")

    # Плейлисты
    existing = api_call("GET", f"/playlists?part=snippet&channelId={channel_id}&maxResults=50",
                        token)
    existing_titles = {p["snippet"]["title"]: p["id"] for p in existing.get("items", [])}

    for pl in PLAYLISTS:
        if pl["title"] in existing_titles:
            print(f"  — '{pl['title']}' уже существует ({existing_titles[pl['title']]})")
            continue
        body = {
            "snippet": {
                "title": pl["title"],
                "description": pl["description"],
                "defaultLanguage": "ru",
                "tags": [pl["tag"], "VideoAI", "оживить фото"],
            },
            "status": {"privacyStatus": "public"},
        }
        try:
            res = api_call("POST", "/playlists?part=snippet,status", token, body)
            print(f"  ✓ создан '{pl['title']}' → {res['id']}")
        except RuntimeError as e:
            print(f"  ✗ {pl['title']}: {e}")

    print("\n✅ Упаковка YouTube канала завершена")


if __name__ == "__main__":
    main()

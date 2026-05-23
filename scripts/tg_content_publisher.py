"""
Публикатор контент-постов в @botisk_canal.

Берёт tg_content_posts.json и публикует посты с интервалом.

Запуск:
    python tg_content_publisher.py             # все посты с интервалом 1 час
    python tg_content_publisher.py --first 5   # только первые 5
    python tg_content_publisher.py --dry-run   # печать без отправки
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env


def tg_api(method: str, token: str, params: dict):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error_code": e.code, "description": body_err[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=0, help="публиковать только первые N постов")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interval", type=int, default=10, help="интервал между постами, сек (для batch публикации)")
    args = ap.parse_args()

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    token = env["TELEGRAM_BOT_PHOTO2VIDEO"]
    chat_id = env["TG_CHANNEL_CHAT_ID"]

    posts_file = Path(__file__).parent / "tg_content_posts.json"
    with open(posts_file, encoding="utf-8") as f:
        posts = json.load(f)

    if args.first > 0:
        posts = posts[:args.first]

    print(f"К публикации: {len(posts)} постов")
    if args.dry_run:
        for i, p in enumerate(posts, 1):
            print(f"\n--- {i}. {p['title']} [{p.get('tags','')}]")
            print(p["text"][:200] + "...")
        return

    published = 0
    for i, p in enumerate(posts, 1):
        title = p["title"]
        text = p["text"]
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
        r = tg_api("sendMessage", token, params)
        if r.get("ok"):
            msg_id = r["result"]["message_id"]
            print(f"  [{i}/{len(posts)}] ✓ id={msg_id} «{title}»")
            published += 1
        else:
            print(f"  [{i}/{len(posts)}] ✗ «{title}»: {r.get('description', 'unknown')}")

        # Опрос, если есть
        if p.get("poll"):
            poll_params = {
                "chat_id": chat_id,
                "question": "Что бы ты выбрал первой?",
                "options": json.dumps(p["poll"], ensure_ascii=False),
                "is_anonymous": True,
                "allows_multiple_answers": False,
            }
            r2 = tg_api("sendPoll", token, poll_params)
            print(f"     poll: {'✓' if r2.get('ok') else '✗ ' + str(r2.get('description', ''))}")

        if i < len(posts):
            time.sleep(args.interval)

    print(f"\n✅ Опубликовано {published}/{len(posts)}")


if __name__ == "__main__":
    main()

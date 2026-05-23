"""
Публикует ОДИН следующий пост из tg_content_posts.json в @botisk_canal.
Хранит offset в /srv/creatives/tg_content_state.json.

Cron (раз в день в 14:00):
    0 14 * * *  python3 /srv/creatives/tg_content_cron.py >> /tmp/tg_content_cron.log 2>&1
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

STATE_FILE = Path("/srv/creatives/tg_content_state.json")
POSTS_FILE = Path("/srv/creatives/tg_content_posts.json")


def tg(method: str, token: str, params: dict):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "description": e.read().decode("utf-8", errors="replace")[:200]}


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    token = env["TELEGRAM_BOT_PHOTO2VIDEO"]
    chat_id = env["TG_CHANNEL_CHAT_ID"]

    posts = json.loads(POSTS_FILE.read_text(encoding="utf-8"))
    state = {"next_index": 0}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    # Стартовый offset для cron — 5 (первые 5 уже опубликованы вручную)
    idx = state.get("next_index", 5)
    if idx >= len(posts):
        print(f"[{time.strftime('%Y-%m-%d %H:%M')}] Все посты опубликованы (idx={idx}/{len(posts)})")
        return

    p = posts[idx]
    r = tg("sendMessage", token, {
        "chat_id": chat_id,
        "text": p["text"],
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    })
    ts = time.strftime("%Y-%m-%d %H:%M")
    if r.get("ok"):
        print(f"[{ts}] ✓ [{idx+1}/{len(posts)}] «{p['title']}» msg={r['result']['message_id']}")
        if p.get("poll"):
            r2 = tg("sendPoll", token, {
                "chat_id": chat_id,
                "question": "Что бы ты выбрал первой?",
                "options": json.dumps(p["poll"], ensure_ascii=False),
                "is_anonymous": True,
            })
            print(f"    poll: {'✓' if r2.get('ok') else '✗'}")
        state["next_index"] = idx + 1
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    else:
        print(f"[{ts}] ✗ [{idx+1}] «{p['title']}»: {r.get('description', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    main()

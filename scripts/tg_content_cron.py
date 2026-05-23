"""
Публикует ОДИН контент-пост из tg_content_posts.json в @botisk_canal
с обязательным медиа (видео из /srv/creatives/processed_v2/).

Правила (см. memory/feedback_tg_media_only.md, feedback_creative_variety.md):
- Только sendVideo (или sendPhoto fallback), не чистый текст
- Не использовать подряд одно и то же исходное фото (kling_task_id)

Хранит offset + последний task_id в /srv/creatives/tg_content_state.json.

Cron (раз в день в 13:00 МСК = 10:00 UTC):
    0 10 * * * python3 /srv/creatives/tg_content_cron.py >> /tmp/tg_content_cron.log 2>&1
"""
from __future__ import annotations

import io
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

STATE_FILE = Path("/srv/creatives/tg_content_state.json")
POSTS_FILE = Path("/srv/creatives/tg_content_posts.json")
VIDEOS_DIR = Path("/srv/creatives/processed_v2")
OG_IMAGE = Path("/srv/admin/og-image.png")


def tg_multipart(method: str, token: str, params: dict, file_path: Path, file_field: str):
    """sendVideo / sendPhoto multipart upload."""
    boundary = "----TGContentBoundary7MA4YWxkTrZu"
    body = b""
    for k, v in params.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += str(v).encode("utf-8") + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'.encode()
    mime = "video/mp4" if file_path.suffix == ".mp4" else "image/png"
    body += f"Content-Type: {mime}\r\n\r\n".encode()
    body += file_path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    url = f"https://api.telegram.org/bot{token}/{method}"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "description": e.read().decode("utf-8", errors="replace")[:300]}


def extract_task_id(filename: str) -> str | None:
    m = re.match(r"kling_([0-9a-f-]{36})_", filename)
    return m.group(1) if m else None


def pick_media(used_task_ids: list[str]) -> tuple[Path, str, str | None]:
    """
    Возвращает (file_path, kind, task_id) где kind = 'video' или 'photo'.
    Старается избежать task_id из недавнего списка used_task_ids (последние 5).
    Если все task_id уже в недавних — берёт случайное.
    Если v2-папка пустая — fallback на og-image.png (photo).
    """
    candidates = sorted(VIDEOS_DIR.glob("kling_*_v2_*.mp4"))
    if not candidates:
        return OG_IMAGE, "photo", None

    fresh = [p for p in candidates if extract_task_id(p.name) not in used_task_ids]
    pool = fresh or candidates
    chosen = random.choice(pool)
    return chosen, "video", extract_task_id(chosen.name)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    token = env["TELEGRAM_BOT_PHOTO2VIDEO"]
    chat_id = env["TG_CHANNEL_CHAT_ID"]

    posts = json.loads(POSTS_FILE.read_text(encoding="utf-8"))
    state = {"next_index": 5, "recent_task_ids": []}
    if STATE_FILE.exists():
        state = {**state, **json.loads(STATE_FILE.read_text(encoding="utf-8"))}

    idx = state.get("next_index", 5)
    ts = time.strftime("%Y-%m-%d %H:%M")
    if idx >= len(posts):
        print(f"[{ts}] Все посты опубликованы (idx={idx}/{len(posts)})")
        return

    p = posts[idx]
    media, kind, task_id = pick_media(state.get("recent_task_ids", []))
    method = "sendVideo" if kind == "video" else "sendPhoto"
    file_field = "video" if kind == "video" else "photo"
    params = {
        "chat_id": chat_id,
        "caption": p["text"],
        "parse_mode": "MarkdownV2",
    }

    r = tg_multipart(method, token, params, media, file_field)
    if r.get("ok"):
        msg_id = r["result"]["message_id"]
        print(f"[{ts}] ✓ [{idx+1}/{len(posts)}] «{p['title']}» {kind}={media.name} msg={msg_id}")

        if p.get("poll"):
            poll_url = f"https://api.telegram.org/bot{token}/sendPoll"
            poll_body = json.dumps({
                "chat_id": chat_id,
                "question": "Что бы ты выбрал первой?",
                "options": p["poll"],
                "is_anonymous": True,
            }).encode()
            poll_req = urllib.request.Request(poll_url, data=poll_body, method="POST",
                                               headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(poll_req, timeout=20)
                print("     poll: ✓")
            except Exception as e:
                print(f"     poll: ✗ {e}")

        state["next_index"] = idx + 1
        # храним 5 последних task_id для antispam
        recent = state.get("recent_task_ids", [])
        if task_id:
            recent = ([task_id] + recent)[:5]
        state["recent_task_ids"] = recent
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    else:
        print(f"[{ts}] ✗ [{idx+1}] «{p['title']}»: {r.get('description', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    main()

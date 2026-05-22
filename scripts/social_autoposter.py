#!/usr/bin/env python3
"""
Автопостер social_posts → YouTube/Telegram/VK (запускается cron'ом каждые 30 мин).

Логика:
1. Берёт самый ранний post в social_posts с pending-статусом по хотя бы одной платформе
   и scheduled_at <= NOW()
2. Для каждой target-платформы с pending-статусом — постит, обновляет _status и _url
3. На YouTube — через youtube_uploader.upload_short()
4. TG/VK пока заглушки (платформы будут когда юзер создаст)

Запуск:
    /usr/bin/python3 /srv/creatives/social_autoposter.py

Cron:
    */30 * * * * /usr/bin/python3 /srv/creatives/social_autoposter.py >> /var/log/autoposter.log 2>&1
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

CREATIVES_DIR = Path("/srv/creatives/processed")


# ─── PostgreSQL через psql CLI ───
import subprocess

def psql_fetch(sql: str) -> str:
    """Локальный psql на VPS (мы уже на сервере)."""
    result = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql: {result.stderr}")
    return result.stdout.strip()


# ─── YouTube uploader ───
def upload_to_youtube(env: dict, video_path: Path, title: str, description: str, tags: list[str]) -> dict:
    """Загружает в YouTube. Возвращает {'video_id', 'url', 'error'}."""
    refresh_token = env.get("YOUTUBE_REFRESH_TOKEN")
    if not refresh_token:
        return {"error": "no_refresh_token"}

    # refresh → access
    body = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": env["YOUTUBE_CLIENT_ID"],
        "client_secret": env["YOUTUBE_CLIENT_SECRET"],
        "grant_type": "refresh_token",
    }).encode()
    try:
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body, method="POST",
                                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            access_token = json.loads(r.read())["access_token"]
    except urllib.error.HTTPError as e:
        return {"error": f"oauth refresh failed: {e.code}"}

    # Multipart upload
    import mimetypes
    boundary = f"===videoai_{int(time.time())}"
    mime = mimetypes.guess_type(str(video_path))[0] or "video/mp4"
    metadata = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": (tags or [])[:30],
            "categoryId": "22",  # People & Blogs
            "defaultLanguage": "ru",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    body_parts = []
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(b"Content-Type: application/json; charset=UTF-8\r\n\r\n")
    body_parts.append(json.dumps(metadata).encode("utf-8"))
    body_parts.append(b"\r\n")
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    body_parts.append(video_path.read_bytes())
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(body_parts)

    url = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status"
    try:
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(body)),
        })
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.loads(r.read())
        vid = resp.get("id")
        return {"video_id": vid, "url": f"https://youtu.be/{vid}" if vid else None}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"upload {e.code}: {body[:300]}"}


# ─── Main loop ───
def get_next_pending_post() -> dict | None:
    """Возвращает следующий пост готовый к публикации (хотя бы 1 платформа = pending)."""
    sql = (
        "SELECT id, creative_file, title, caption, hashtags, "
        "target_youtube, target_telegram, target_vk, "
        "youtube_status, telegram_status, vk_status "
        "FROM social_posts "
        "WHERE scheduled_at <= NOW() "
        "  AND ((target_youtube AND youtube_status = 'pending') "
        "    OR (target_telegram AND telegram_status = 'pending') "
        "    OR (target_vk AND vk_status = 'pending')) "
        "ORDER BY scheduled_at "
        "LIMIT 1"
    )
    out = psql_fetch(sql)
    if not out:
        return None
    cols = ["id", "creative_file", "title", "caption", "hashtags",
            "target_youtube", "target_telegram", "target_vk",
            "youtube_status", "telegram_status", "vk_status"]
    vals = out.split("|")
    return dict(zip(cols, vals))


def update_youtube_status(post_id: int, status: str, video_id: str = "", url: str = "", error: str = "") -> None:
    error_safe = error.replace("'", "''")[:500]
    sql = (
        f"UPDATE social_posts SET "
        f"youtube_status = '{status}', "
        f"youtube_video_id = NULLIF('{video_id}', ''), "
        f"youtube_url = NULLIF('{url}', ''), "
        f"youtube_error = NULLIF('{error_safe}', ''), "
        f"youtube_posted_at = CASE WHEN '{status}' = 'posted' THEN NOW() ELSE youtube_posted_at END, "
        f"updated_at = NOW() "
        f"WHERE id = {post_id}"
    )
    psql_fetch(sql)


def main():
    env = load_env()
    post = get_next_pending_post()
    if not post:
        print(f"[{time.strftime('%Y-%m-%d %H:%M')}] нет постов готовых к публикации")
        return

    print(f"[{time.strftime('%Y-%m-%d %H:%M')}] обработка post_id={post['id']} файл={post['creative_file']}")

    video_path = CREATIVES_DIR / post["creative_file"]
    if not video_path.exists():
        msg = f"file not found: {video_path}"
        print(f"  ❌ {msg}")
        if post["target_youtube"] == "t" and post["youtube_status"] == "pending":
            update_youtube_status(post["id"], "failed", error=msg)
        return

    # YouTube
    if post["target_youtube"] == "t" and post["youtube_status"] == "pending":
        title = post["title"][:100] or "AI оживляет фотографии"
        description = (post["caption"] or "") + "\n\n#shorts"
        tags = ["AI", "оживить фото", "Kling", "VideoAI", "Shorts"]
        print(f"  → YouTube upload: {title[:50]}")
        try:
            result = upload_to_youtube(env, video_path, title, description, tags)
        except Exception as e:
            result = {"error": str(e)[:300]}
        if result.get("error"):
            print(f"  ❌ {result['error']}")
            update_youtube_status(post["id"], "failed", error=result["error"])
        else:
            print(f"  ✅ {result['url']}")
            update_youtube_status(post["id"], "posted",
                                  video_id=result.get("video_id", ""),
                                  url=result.get("url", ""))

    # Telegram / VK — пока заглушки (платформы не созданы)
    if post["target_telegram"] == "t" and post["telegram_status"] == "pending":
        print(f"  ⏭ Telegram: канал не настроен")
    if post["target_vk"] == "t" and post["vk_status"] == "pending":
        print(f"  ⏭ VK: сообщество не настроено")


if __name__ == "__main__":
    main()

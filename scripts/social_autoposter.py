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
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

CREATIVES_DIR = Path("/srv/creatives/processed")
CREATIVES_DIR_V2 = Path("/srv/creatives/processed_v2")


def resolve_video_path(filename: str) -> Path:
    """Ищет файл по имени в v2-папке (приоритет) и старой processed-папке."""
    v2 = CREATIVES_DIR_V2 / filename
    if v2.exists():
        return v2
    return CREATIVES_DIR / filename


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


# ─── Telegram channel posting ───
def upload_to_telegram(token: str, chat_id: str, video_path: Path,
                       title: str, caption: str) -> dict:
    """Постит видео в TG-канал через sendVideo. Возвращает {msg_id, url, error}."""
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    # Подпись: title + caption + ссылка на сервис
    text = f"{title}\n\n{caption[:900]}"  # TG лимит 1024 символа
    if len(text) > 1000:
        text = text[:997] + "..."

    # multipart/form-data
    import uuid as uuid_mod
    boundary = f"---tg-{uuid_mod.uuid4().hex[:16]}"
    parts = []
    # chat_id
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
    parts.append(str(chat_id).encode())
    parts.append(b"\r\n")
    # caption
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
    parts.append(text.encode("utf-8"))
    parts.append(b"\r\n")
    # supports_streaming
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="supports_streaming"\r\n\r\n')
    parts.append(b"true")
    parts.append(b"\r\n")
    # video file
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="video"; filename="{video_path.name}"\r\n'
        f'Content-Type: video/mp4\r\n\r\n'.encode()
    )
    parts.append(video_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    try:
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        })
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
        if not data.get("ok"):
            return {"error": data.get("description", "unknown")}
        result = data.get("result", {})
        msg_id = result.get("message_id")
        # Если канал публичный с username — формируем URL
        chat = result.get("chat", {})
        username = chat.get("username")
        if username:
            url_str = f"https://t.me/{username}/{msg_id}"
        else:
            # private channel: t.me/c/{id_без_-100}/{msg_id}
            cid = str(chat.get("id", "")).replace("-100", "")
            url_str = f"https://t.me/c/{cid}/{msg_id}"
        return {"msg_id": msg_id, "url": url_str}
    except urllib.error.HTTPError as e:
        return {"error": f"TG {e.code}: {e.read().decode()[:300]}"}


def update_telegram_status(post_id: int, status: str, msg_id: int = 0, url: str = "", error: str = "") -> None:
    error_safe = error.replace("'", "''")[:500]
    sql = (
        f"UPDATE social_posts SET "
        f"telegram_status = '{status}', "
        f"telegram_msg_id = NULLIF({msg_id or 0}, 0), "
        f"telegram_url = NULLIF('{url}', ''), "
        f"telegram_error = NULLIF('{error_safe}', ''), "
        f"telegram_posted_at = CASE WHEN '{status}' = 'posted' THEN NOW() ELSE telegram_posted_at END, "
        f"updated_at = NOW() "
        f"WHERE id = {post_id}"
    )
    psql_fetch(sql)


# ─── Main loop ───
def get_next_pending_post() -> dict | None:
    """Возвращает следующий пост готовый к публикации.

    Фильтр разнообразия: пропускаем посты с тем же kling-task_id что был
    опубликован за последние 48 часов (см. feedback_creative_variety.md).

    ENV-override:
      FORCE_POST_ID=<id> — берёт именно этот post без фильтра variety
        (для тестов / ручных перезаливов).
    """
    force_id = os.environ.get("FORCE_POST_ID")
    if force_id:
        sql = (
            "SELECT id, creative_file, title, caption, hashtags, "
            "target_youtube, target_telegram, target_vk, "
            "youtube_status, telegram_status, vk_status "
            f"FROM social_posts WHERE id = {int(force_id)} LIMIT 1"
        )
    else:
        sql = (
            "SELECT id, creative_file, title, caption, hashtags, "
            "target_youtube, target_telegram, target_vk, "
            "youtube_status, telegram_status, vk_status "
            "FROM social_posts sp "
            "WHERE scheduled_at <= NOW() "
            "  AND ((target_youtube AND youtube_status = 'pending') "
            "    OR (target_telegram AND telegram_status = 'pending') "
            "    OR (target_vk AND vk_status = 'pending')) "
            # Извлекаем UUID-task из creative_file и проверяем — не было ли поста
            # с этим же task_id опубликовано за последние 48 ч.
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM social_posts past "
            "    WHERE (past.youtube_posted_at > NOW() - interval '48 hours' "
            "        OR past.telegram_posted_at > NOW() - interval '48 hours') "
            "      AND substring(past.creative_file from 'kling_([0-9a-f-]+)_') "
            "        = substring(sp.creative_file from 'kling_([0-9a-f-]+)_') "
            "      AND past.id != sp.id"
            "  ) "
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

    video_path = resolve_video_path(post["creative_file"])
    if not video_path.exists():
        msg = f"file not found: {post['creative_file']} (checked processed_v2 + processed)"
        print(f"  ❌ {msg}")
        if post["target_youtube"] == "t" and post["youtube_status"] == "pending":
            update_youtube_status(post["id"], "failed", error=msg)
        return

    # YouTube
    if post["target_youtube"] == "t" and post["youtube_status"] == "pending":
        # Заголовок: hook + ясный context
        raw_title = (post["title"] or "AI оживляет фотографии").strip()
        # YouTube любит до 100 символов в title. Конверсионный паттерн:
        # «<хук> — оживите ваше фото за 60 сек | botisk.ru»
        title = (raw_title + " — оживите ваше фото за 60 сек | botisk.ru")[:100]

        # Description с явным CTA и хештегами для алгоритма
        description = (
            f"{raw_title}\n\n"
            f"🎬 Это AI оживляет любую фотографию: свадебную, детскую, фото близких. "
            f"Превращаем статичный снимок в живое видео за 60 секунд через нейросеть Kling AI.\n\n"
            f"🔗 Попробуйте бесплатно: botisk.ru\n"
            f"🤖 Или через Telegram-бот: @VideoAI_24isk_bot\n\n"
            f"❓ Как это работает:\n"
            f"1. Загружаете фото\n"
            f"2. AI генерирует 5-секундное видео\n"
            f"3. Скачиваете и делитесь с близкими\n\n"
            f"💡 Идеи: оживить фото бабушки, дедушки, детское фото малыша, "
            f"свадебный снимок, фото потерянного питомца.\n\n"
            f"#оживитьфото #нейросеть #AI #shorts #память #подарок "
            f"#семейноевидео #искусственныйинтеллект #ai_video"
        )
        tags = [
            "оживить фото", "AI", "нейросеть", "оживить старое фото",
            "видео из фото", "Kling AI", "VideoAI", "Shorts",
            "подарок маме", "подарок бабушке", "семейный архив",
            "память", "AI видео", "искусственный интеллект",
        ]
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

    # Telegram канал
    if post["target_telegram"] == "t" and post["telegram_status"] == "pending":
        token = env.get("TELEGRAM_BOT_PHOTO2VIDEO")
        chat_id = env.get("TG_CHANNEL_CHAT_ID")
        if token and chat_id:
            print(f"  → Telegram канал: {post['title'][:50]}")
            try:
                result = upload_to_telegram(token, chat_id, video_path, post["title"], post["caption"])
                if result.get("error"):
                    print(f"  ❌ TG: {result['error']}")
                    update_telegram_status(post["id"], "failed", error=result["error"])
                else:
                    print(f"  ✅ TG: msg_id={result['msg_id']}")
                    update_telegram_status(post["id"], "posted",
                                            msg_id=result["msg_id"], url=result["url"])
            except Exception as e:
                print(f"  ❌ TG exception: {e}")
                update_telegram_status(post["id"], "failed", error=str(e)[:300])
        else:
            print(f"  ⏭ Telegram: TG_CHANNEL_CHAT_ID не в .env")

    if post["target_vk"] == "t" and post["vk_status"] == "pending":
        # VK не настроен — переводим в skipped, чтобы пост не зависал в выборке
        sql = (f"UPDATE social_posts SET vk_status='skipped', "
               f"vk_error='VK not configured', updated_at=NOW() WHERE id={post['id']}")
        psql_fetch(sql)
        print(f"  ⏭ VK: сообщество не настроено → vk_status=skipped")


if __name__ == "__main__":
    main()

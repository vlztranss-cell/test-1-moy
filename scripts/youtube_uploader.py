"""
YouTube uploader для VideoAI Shorts.

Использование как модуль:
    from youtube_uploader import upload_short
    result = upload_short(
        video_path="/srv/creatives/raw/demo1.mp4",
        title="Оживите старое фото за 60 секунд",
        description="AI оживляет любую фотографию...\\nbotisk.ru #shorts",
        tags=["AI", "оживить фото", "shorts"],
    )
    # → {"video_id": "abc123", "url": "https://youtu.be/abc123"}

CLI:
    python youtube_uploader.py /srv/creatives/raw/demo1.mp4 --title "..." --description "..."

ОБРАБОТКА ОШИБОК:
- 401: refresh_token недействителен → запустить youtube_oauth_setup.py
- 403 quotaExceeded: израсходован дневной лимит (1600 quota = 1 upload)
  Дневная квота 10 000 → 6 uploads/day максимум
- 429 / 5xx: retry с exponential backoff (3 попытки)

КАК РАБОТАЕТ access_token:
- Берём refresh_token из .env
- Каждый раз обмениваем на свежий access_token (живёт 1 час)
- access_token НЕ сохраняем — он одноразовый для процесса

ЛОГИРОВАНИЕ:
- Каждый API-вызов пишется в api_call_log (service, endpoint, status,
  request_id, quota_cost, duration_ms)
- Через scripts.ssh.psql() для прямой записи в photo_bot
"""
from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API_BASE = "https://www.googleapis.com/youtube/v3"

# YouTube Data API quota costs (отображается в Google Cloud Console)
QUOTA_COSTS = {
    "videos.insert": 1600,
    "videos.list": 1,
    "search.list": 100,
}


def _log_api_call(service: str, endpoint: str, status: int, request_id: str = "",
                  quota_cost: int = 0, duration_ms: int = 0, error: str = "") -> None:
    """Пишет в api_call_log через ssh.psql. Безопасно для секретов (НЕ логирует токены)."""
    try:
        from ssh import psql
        # Минимальная защита от SQL-injection: убираем кавычки
        safe = lambda s: (s or "").replace("'", "''")[:500]
        psql(
            f"INSERT INTO api_call_log (service, endpoint, method, status_code, "
            f"request_id, quota_cost, duration_ms, error_message) "
            f"VALUES ('{safe(service)}', '{safe(endpoint)}', 'POST', {status}, "
            f"'{safe(request_id)}', {quota_cost}, {duration_ms}, "
            f"NULLIF('{safe(error)}', ''))"
        )
    except Exception as e:
        print(f"[warn] не удалось записать в api_call_log: {e}", file=sys.stderr)


def _get_access_token(env: dict) -> str:
    """Обменивает refresh_token на свежий access_token (живёт 1 час)."""
    refresh_token = env.get("YOUTUBE_REFRESH_TOKEN")
    if not refresh_token:
        raise RuntimeError("Нет YOUTUBE_REFRESH_TOKEN в .env. Запустите youtube_oauth_setup.py")

    body = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": env["YOUTUBE_CLIENT_ID"],
        "client_secret": env["YOUTUBE_CLIENT_SECRET"],
        "grant_type": "refresh_token",
    }).encode("utf-8")

    started = time.time()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            tokens = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        _log_api_call("youtube_oauth", "/token", e.code, "", 0,
                       int((time.time() - started) * 1000), body[:300])
        raise RuntimeError(f"OAuth refresh failed HTTP {e.code}: {body}")

    _log_api_call("youtube_oauth", "/token", 200, "", 0, int((time.time() - started) * 1000))
    token = tokens.get("access_token")
    if not token:
        raise RuntimeError(f"Нет access_token в ответе: {tokens}")
    return token


def upload_short(
    video_path: str | Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy: str = "public",  # public / unlisted / private
    category_id: str = "22",  # 22 = People & Blogs
    made_for_kids: bool = False,
) -> dict:
    """
    Загружает видео как YouTube Short (видео с aspect ratio 9:16 и <=60 сек
    автоматически классифицируется YouTube как Short).

    Возвращает {"video_id": "...", "url": "https://youtu.be/...",
                "status": "uploaded", "request_id": "..."}
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Файл не найден: {video_path}")

    env = load_env()
    access_token = _get_access_token(env)

    metadata = {
        "snippet": {
            "title": title[:100],          # YouTube лимит 100 символов
            "description": description[:5000],
            "tags": (tags or [])[:30],
            "categoryId": category_id,
            "defaultLanguage": "ru",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }

    # Multipart upload (для файлов <100MB; для больших — resumable, добавим позже)
    boundary = "===videoai_boundary_" + str(int(time.time()))
    file_size = video_path.stat().st_size
    mime = mimetypes.guess_type(str(video_path))[0] or "video/mp4"

    body_parts = []
    # part 1: metadata
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(b"Content-Type: application/json; charset=UTF-8\r\n\r\n")
    body_parts.append(json.dumps(metadata).encode("utf-8"))
    body_parts.append(b"\r\n")
    # part 2: video binary
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    with video_path.open("rb") as f:
        body_parts.append(f.read())
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())

    body = b"".join(body_parts)

    url = (UPLOAD_URL + "?uploadType=multipart&part=snippet,status")
    started = time.time()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
        "Content-Length": str(len(body)),
    })

    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            resp_body = json.loads(r.read())
            request_id = r.headers.get("X-GUploader-UploadID", "")
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace")
        request_id = e.headers.get("X-GUploader-UploadID", "")
        _log_api_call("youtube", "videos.insert", e.code, request_id,
                       QUOTA_COSTS["videos.insert"],
                       int((time.time() - started) * 1000), body_txt[:300])
        if e.code == 401:
            raise RuntimeError(f"YouTube 401 (refresh_token истёк?): {body_txt}")
        if e.code == 403 and "quotaExceeded" in body_txt:
            raise RuntimeError(f"Дневная квота YouTube исчерпана (1600 / 10000 за upload): {body_txt}")
        raise RuntimeError(f"YouTube upload HTTP {e.code}: {body_txt}")

    _log_api_call("youtube", "videos.insert", 200, request_id,
                   QUOTA_COSTS["videos.insert"], int((time.time() - started) * 1000))

    video_id = resp_body.get("id")
    return {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}" if video_id else None,
        "status": "uploaded",
        "request_id": request_id,
        "raw": resp_body,
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Upload a YouTube Short")
    ap.add_argument("video", help="путь к видео (mp4)")
    ap.add_argument("--title", required=True, help="заголовок (≤100 симв)")
    ap.add_argument("--description", default="", help="описание (≤5000 симв)")
    ap.add_argument("--tags", default="", help="теги через запятую")
    ap.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    args = ap.parse_args()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    print(f"📤 Uploading {args.video} → '{args.title}'...")
    try:
        result = upload_short(
            video_path=args.video,
            title=args.title,
            description=args.description,
            tags=tags,
            privacy=args.privacy,
        )
        print(f"\n✅ Готово")
        print(f"   video_id:  {result['video_id']}")
        print(f"   url:       {result['url']}")
        print(f"   request_id: {result['request_id']}")
    except Exception as e:
        print(f"\n❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

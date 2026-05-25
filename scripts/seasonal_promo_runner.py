"""
Запускается cron'ом раз в день в 10:00 МСК. Читает seasonal_promo_calendar.json,
проверяет какие сообщения сегодня должны выйти (по days_before до trigger_date),
и публикует:
  - tg_canal → в @botisk_canal с видео-обложкой
  - tg_user  → персонально всем платным пользователям с telegram_id

Идемпотентно: лог в seasonal_promo_log.

Cron на VPS:
    0 10 * * * /usr/bin/python3 /srv/creatives/seasonal_promo_runner.py >> /var/log/seasonal_promo.log 2>&1
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

CALENDAR = Path(__file__).parent / "seasonal_promo_calendar.json"

env = load_env()
TOKEN = env["TELEGRAM_BOT_PHOTO2VIDEO"]
CHAT = env["TG_CHANNEL_CHAT_ID"]


def psql_fetch(sql: str) -> str:
    r = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
        capture_output=True, text=True, timeout=20
    )
    return r.stdout.strip()


def ensure_log_table():
    psql_fetch("""
        CREATE TABLE IF NOT EXISTS seasonal_promo_log (
            id BIGSERIAL PRIMARY KEY,
            promo_name TEXT NOT NULL,
            days_before INT NOT NULL,
            channel TEXT NOT NULL,
            recipients_count INT,
            sent_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(promo_name, days_before, channel)
        );
    """)


def tg_send_message(chat_id, text, parse_mode="MarkdownV2"):
    body = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": parse_mode, "disable_web_page_preview": "false"
    }).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data=body, method="POST"
            ), timeout=15
        ).read()
        return True
    except Exception:
        return False


def tg_send_video(chat_id, video_path, caption):
    boundary = "----SeasonalPromoBoundary"
    body = b""
    for k, v in [("chat_id", chat_id), ("caption", caption), ("parse_mode", "MarkdownV2")]:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += str(v).encode() + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="video"; filename="promo.mp4"\r\n'
    body += b"Content-Type: video/mp4\r\n\r\n"
    body += Path(video_path).read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{TOKEN}/sendVideo",
                data=body, method="POST",
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
            ), timeout=180
        ).read()
        return True
    except Exception as e:
        print(f"sendVideo error: {e}")
        return False


def get_video_for_promo(promo_name: str) -> str | None:
    """Подбирает видео под тематику праздника."""
    cat = {
        "den_materi": "memory", "8marta": "memory", "9maya": "memory",
        "den_otca": "memory", "novy_god": "babies",
    }.get(promo_name, "memory")
    files = sorted(glob.glob(f"/srv/creatives/processed_v2/kling_*_v2_{cat}_*.mp4"))
    return files[-1] if files else None


def main():
    ensure_log_table()
    today = dt.date.today()
    calendar = json.loads(CALENDAR.read_text(encoding="utf-8"))
    log_lines = []

    for promo in calendar:
        trigger = dt.date.fromisoformat(promo["trigger_date"])
        days_diff = (trigger - today).days
        for msg in promo["messages"]:
            if msg["days_before"] != days_diff:
                continue
            # Проверка идемпотентности
            existing = psql_fetch(
                f"SELECT id FROM seasonal_promo_log WHERE promo_name='{promo['name']}' "
                f"AND days_before={msg['days_before']} AND channel='{msg['channel']}'"
            )
            if existing:
                log_lines.append(f"⏭ {promo['name']} D-{msg['days_before']} {msg['channel']} — уже отправлено")
                continue

            recipients = 0
            if msg["channel"] == "tg_canal":
                video = get_video_for_promo(promo["name"])
                if video:
                    ok = tg_send_video(CHAT, video, msg["text"])
                else:
                    ok = tg_send_message(CHAT, msg["text"])
                if ok: recipients = 1
                log_lines.append(f"📤 {promo['name']} D-{msg['days_before']} canal: {'✓' if ok else '✗'}")

            elif msg["channel"] == "tg_user":
                # Берём всех платных юзеров с telegram_id
                rows = psql_fetch(
                    "SELECT DISTINCT telegram_id FROM web_users "
                    "WHERE telegram_id IS NOT NULL AND COALESCE(paid_credits,0) > 0"
                )
                for tg_id in rows.split("\n"):
                    tg_id = tg_id.strip()
                    if not tg_id: continue
                    if tg_send_message(tg_id, msg["text"], parse_mode="Markdown"):
                        recipients += 1
                log_lines.append(f"📤 {promo['name']} D-{msg['days_before']} user: {recipients} получателей")

            # Запись в лог
            psql_fetch(
                f"INSERT INTO seasonal_promo_log (promo_name, days_before, channel, recipients_count) "
                f"VALUES ('{promo['name']}', {msg['days_before']}, '{msg['channel']}', {recipients}) "
                f"ON CONFLICT DO NOTHING"
            )

    print(f"[{today}] {'; '.join(log_lines) if log_lines else 'нет промо на сегодня'}")


if __name__ == "__main__":
    main()

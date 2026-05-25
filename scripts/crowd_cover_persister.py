"""
Каждые 30 минут скачивает свежие обложки из DALL-E (cover_url с openai.com domains)
в /srv/admin/crowd-covers/<id>.png и обновляет ссылку на постоянную:
    https://n8n.24isk.ru/crowd-covers/<id>.png

Cron на VPS:
    */30 * * * * /usr/bin/python3 /srv/creatives/crowd_cover_persister.py >> /var/log/cover_persist.log 2>&1
"""
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

STORAGE = Path("/srv/admin/crowd-covers")
STORAGE.mkdir(parents=True, exist_ok=True)
BASE_URL = "https://n8n.24isk.ru/crowd-covers"


def psql_fetch(sql):
    r = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
        capture_output=True, text=True, timeout=15
    )
    return r.stdout.strip()


def main():
    ts = time.strftime("%Y-%m-%d %H:%M")
    # Берём свежие записи с cover_url который ещё не на нашем домене
    rows = psql_fetch(
        "SELECT id, cover_url FROM workzilla_tz_drafts "
        "WHERE cover_url IS NOT NULL "
        "AND cover_url NOT LIKE 'https://n8n.24isk.ru/%' "
        "AND generated_at > NOW() - INTERVAL '24 hours' "
        "ORDER BY id DESC"
    )
    if not rows:
        print(f"[{ts}] нет новых обложек для скачивания")
        return

    downloaded = 0
    for line in rows.split("\n"):
        if not line: continue
        tz_id, url = line.split("|", 1)
        dest = STORAGE / f"{tz_id}.png"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=60).read()
            dest.write_bytes(data)
            # Обновляем URL в БД на постоянный
            new_url = f"{BASE_URL}/{tz_id}.png"
            psql_fetch(
                f"UPDATE workzilla_tz_drafts SET cover_url = '{new_url}' WHERE id = {tz_id}"
            )
            downloaded += 1
            print(f"[{ts}] ✓ id={tz_id} → {dest.name} ({len(data)//1024} KB)")
        except urllib.error.HTTPError as e:
            print(f"[{ts}] ✗ id={tz_id} HTTP {e.code}")
        except Exception as e:
            print(f"[{ts}] ✗ id={tz_id} {e}")

    print(f"[{ts}] всего скачано: {downloaded}")


if __name__ == "__main__":
    main()

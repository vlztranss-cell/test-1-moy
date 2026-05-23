"""
Раз в час собирает статистику просмотров для всех видео которые
опубликованы через social_autoposter (есть в social_posts.youtube_video_id).

Сохраняет в youtube_video_stats. Дёшево по квоте: 1 unit на batch до 50 видео.

Cron:
    0 * * * * /usr/bin/python3 /srv/creatives/youtube_stats_collector.py >> /var/log/youtube_stats.log 2>&1
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from youtube_uploader import _get_access_token, API_BASE


def psql_fetch(sql: str) -> str:
    r = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
        capture_output=True, text=True, timeout=20
    )
    return r.stdout.strip()


def extract_category(creative_file: str) -> str:
    for cat in ("memory", "babies", "pets", "love"):
        if re.search(rf"_{cat}_(?:\d+)", creative_file or ""):
            return cat
    return "unknown"


def iso_duration_to_seconds(iso: str) -> int:
    """PT12S → 12. PT1M30S → 90."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m: return 0
    h, mn, s = m.groups()
    return int(h or 0) * 3600 + int(mn or 0) * 60 + int(s or 0)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    ts = time.strftime("%Y-%m-%d %H:%M")

    # 1. Все видео которые мы публиковали
    out = psql_fetch(
        "SELECT youtube_video_id, creative_file, title FROM social_posts "
        "WHERE youtube_status='posted' AND youtube_video_id IS NOT NULL"
    )
    if not out:
        print(f"[{ts}] нет опубликованных видео")
        return

    rows = [line.split("|") for line in out.split("\n") if line]
    vid_map = {r[0]: {"creative_file": r[1], "title": r[2]} for r in rows}
    print(f"[{ts}] собираю статистику для {len(vid_map)} видео")

    # 2. videos.list?id=v1,v2,...&part=statistics,snippet,contentDetails (батч до 50)
    token = _get_access_token(env)
    all_ids = list(vid_map.keys())
    items = []
    for i in range(0, len(all_ids), 50):
        batch = all_ids[i:i+50]
        params = urllib.parse.urlencode({
            "id": ",".join(batch),
            "part": "statistics,snippet,contentDetails",
        })
        req = urllib.request.Request(
            f"{API_BASE}/videos?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
                items.extend(d.get("items", []))
        except urllib.error.HTTPError as e:
            print(f"[{ts}] ✗ videos.list batch {i}: HTTP {e.code} {e.read()[:200]}")
            return

    # 3. UPSERT в youtube_video_stats
    updated = 0
    for it in items:
        vid = it["id"]
        stats = it.get("statistics", {})
        snippet = it.get("snippet", {})
        details = it.get("contentDetails", {})
        url_meta = f"https://youtu.be/{vid}"
        title = snippet.get("title", vid_map.get(vid, {}).get("title", ""))
        cat = extract_category(vid_map.get(vid, {}).get("creative_file", ""))
        published_at = snippet.get("publishedAt", "")
        duration = iso_duration_to_seconds(details.get("duration", ""))
        views = int(stats.get("viewCount", 0))
        likes = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))
        favorites = int(stats.get("favoriteCount", 0))

        # Escape SQL
        title_safe = title.replace("'", "''")[:300]
        pub_clause = f"'{published_at}'" if published_at else "NULL"
        sql = (
            "INSERT INTO youtube_video_stats "
            "(video_id, title, category, views, likes, comments, favorites, "
            "published_at, duration_seconds, url, fetched_at) VALUES "
            f"('{vid}', '{title_safe}', '{cat}', {views}, {likes}, {comments}, "
            f"{favorites}, {pub_clause}, {duration}, '{url_meta}', NOW()) "
            "ON CONFLICT (video_id) DO UPDATE SET "
            "views=EXCLUDED.views, likes=EXCLUDED.likes, "
            "comments=EXCLUDED.comments, favorites=EXCLUDED.favorites, "
            "duration_seconds=EXCLUDED.duration_seconds, "
            "title=EXCLUDED.title, category=EXCLUDED.category, "
            "fetched_at=NOW()"
        )
        psql_fetch(sql)
        updated += 1

    print(f"[{ts}] ✅ обновлено {updated} записей в youtube_video_stats")


if __name__ == "__main__":
    main()

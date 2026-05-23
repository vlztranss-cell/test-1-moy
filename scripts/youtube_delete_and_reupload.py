"""
Удаляет 4 сегодняшних видео с YouTube (неправильная вёрстка v2),
сбрасывает в БД youtube_status='pending', обнуляет video_id/url.

После этого autoposter подхватит их с новыми v2.1 файлами и перезальёт.

Запуск:
    python scripts/youtube_delete_and_reupload.py
"""
from __future__ import annotations

import io
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from youtube_uploader import _get_access_token, API_BASE

# 4 видео которые перезаливаем (исключаем «Папу плакал» 1506 views и «Подарок маме» 553 views)
VIDEOS_TO_DELETE = [
    {"post_id": 7,  "video_id": "_hA20-fw-sI", "title": "Свадебный подарок-сюрприз"},
    {"post_id": 13, "video_id": "AaLj1a_nqi4", "title": "Когда питомца больше нет рядом"},
    {"post_id": 3,  "video_id": "EbII7KwmeYo", "title": "Оживи фото бабушки"},
    {"post_id": 10, "video_id": "xyX-HgXImrU", "title": "Подарок маме на 8 марта v2"},
]


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    token = _get_access_token(env)
    print(f"✓ access_token получен")

    deleted_post_ids = []
    for v in VIDEOS_TO_DELETE:
        url = f"{API_BASE}/videos?id={v['video_id']}"
        req = urllib.request.Request(
            url, method="DELETE",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            urllib.request.urlopen(req, timeout=30)
            print(f"  ✅ DELETE {v['video_id']} ({v['title']})")
            deleted_post_ids.append(v["post_id"])
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"  ❌ {v['video_id']}: HTTP {e.code} {body[:200]}")
            if e.code == 404:
                # уже удалён — считаем как успех
                deleted_post_ids.append(v["post_id"])

    if not deleted_post_ids:
        print("Ни одно видео не удалено")
        return

    # Сброс статусов в БД через ssh.psql
    sys.path.insert(0, str(Path(__file__).parent))
    from ssh import psql
    ids_csv = ",".join(str(i) for i in deleted_post_ids)
    sql = (
        "UPDATE social_posts SET "
        "youtube_status='pending', "
        "youtube_video_id=NULL, "
        "youtube_url=NULL, "
        "youtube_posted_at=NULL, "
        "youtube_error=NULL, "
        "scheduled_at=NOW() - interval '1 minute', "
        "updated_at=NOW() "
        f"WHERE id IN ({ids_csv})"
    )
    out, err = psql(sql)
    print(f"\n✓ БД обновлена: {out.strip()} (post ids {ids_csv})")

    # Также удалим из youtube_video_stats чтобы не светились старые counters
    delete_stats = f"DELETE FROM youtube_video_stats WHERE video_id IN (" + \
                    ",".join(f"'{v['video_id']}'" for v in VIDEOS_TO_DELETE) + ")"
    out2, _ = psql(delete_stats)
    print(f"✓ youtube_video_stats очищен: {out2.strip()}")

    print(f"\nПостов в очереди: {len(deleted_post_ids)}. Запускаю autoposter…")


if __name__ == "__main__":
    main()

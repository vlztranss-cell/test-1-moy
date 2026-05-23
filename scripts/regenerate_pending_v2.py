#!/usr/bin/env python3
"""
Перегенерирует все pending видео из social_posts в новый формат v2
(до→после→CTA). Обновляет creative_file в БД на новый v2-файл.

Не создаёт новых PiAPI запросов — использует уже скачанные Kling-видео
из /srv/creatives/raw/kling/

Запуск:
    python3 regenerate_pending_v2.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/srv/creatives")
from creative_variator_v2 import variate_v2

# Хуки (берём из creative_generator, чтобы не дублировать)
HOOKS = {
    "memory": [
        "Оживи фото бабушки",
        "AI вернул её улыбку",
        "Подарок маме на 8 марта",
        "Семейный архив снова живой",
        "Бабушке 92 — она увидела себя молодой",
        "Папа плакал, увидев маму на видео",
    ],
    "babies": [
        "Оживи детство ребёнка",
        "Первые шаги в живом видео",
        "Подарок маме от внуков",
        "Семейная история продолжается",
        "Детские фото снова двигаются",
    ],
    "pets": [
        "В память о любимом питомце",
        "Когда питомца больше нет рядом",
        "Видео из фото вашего котика",
        "Сохраните память о собаке навсегда",
    ],
    "love": [
        "Свадебный подарок-сюрприз",
        "Оживи свадебное фото",
        "Подарок на годовщину свадьбы",
        "Love story в живом видео",
        "Сюрприз для жены",
    ],
}


def psql_fetch(sql: str) -> str:
    result = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
        capture_output=True, text=True, timeout=15
    )
    return result.stdout.strip()


def main():
    # Список pending
    rows = psql_fetch(
        "SELECT id, creative_file, title FROM social_posts WHERE youtube_status='pending' ORDER BY scheduled_at"
    )

    # Группируем по kling_task_id и category
    # Имя может быть зашумлено повторными v2-проходами:
    #   kling_<task_id>_v2_..._<category>_<idx>_..._<timestamp>.mp4
    # Достаём task_id (36-символьный UUID), потом ищем категорию (известный список),
    # затем номер варианта сразу после категории.
    KNOWN_CATS = ("memory", "babies", "pets", "love")
    posts = []
    for line in rows.split("\n"):
        if not line: continue
        fields = line.split("|")
        post_id, fname, title = fields[0], fields[1], fields[2]
        m_task = re.match(r"kling_([0-9a-f-]{36})_", fname)
        if not m_task:
            print(f"⚠️ не нашёл task_id в {fname}")
            continue
        task_id = m_task.group(1)
        # Ищем categort: последнее вхождение известной категории
        category = None
        idx = 0
        for cat in KNOWN_CATS:
            m_cat = re.search(rf"_{cat}_(\d+)", fname)
            if m_cat:
                category = cat
                idx = int(m_cat.group(1))
                break
        if not category:
            print(f"⚠️ не нашёл категорию в {fname}")
            continue
        posts.append({"id": post_id, "fname": fname, "title": title,
                       "task_id": task_id, "category": category, "idx": idx})

    if not posts:
        print("Нет pending постов")
        return

    print(f"Перегенерирую {len(posts)} постов")

    # Группируем по task_id+category, сортируем по idx
    by_task = {}
    for p in posts:
        by_task.setdefault((p["task_id"], p["category"]), []).append(p)
    for k in by_task:
        by_task[k].sort(key=lambda p: p["idx"])

    total_done = 0
    for (task_id, category), group in by_task.items():
        source = Path(f"/srv/creatives/raw/kling/kling_{task_id}.mp4")
        if not source.exists():
            print(f"❌ нет {source}")
            continue
        hooks = [p["title"] for p in group]
        print(f"\n📁 task {task_id[:8]} ({category}, {len(hooks)} вариаций)")
        try:
            new_files = variate_v2(str(source), category, hooks)
        except Exception as e:
            print(f"  ❌ variate_v2: {e}")
            continue

        # Обновляем БД: каждому посту привязываем новый файл по индексу
        for post, new_f in zip(group, new_files):
            new_fname = new_f["filename"]
            psql_fetch(
                f"UPDATE social_posts SET creative_file = '{new_fname}', "
                f"updated_at = NOW() WHERE id = {post['id']}"
            )
            print(f"  ✓ post #{post['id']} → {new_fname}")
            total_done += 1

    print(f"\n✅ Готово: {total_done} постов перегенерены и обновлены в БД")

    # Симлинк новых файлов в /srv/videos для дашборда
    subprocess.run(
        "cd /srv/videos && for f in /srv/creatives/processed_v2/*.mp4; do "
        '[ -e "$(basename "$f")" ] || ln -sf "$f" "$(basename "$f")"; done',
        shell=True, capture_output=True
    )
    print("✓ Симлинки в /srv/videos созданы")


if __name__ == "__main__":
    main()

"""
Auto-cron каждые 3 дня: генерирует новые креативы автономно.

Pipeline:
1. Выбирает следующую категорию из ротации (memory/babies/pets/love)
2. Берёт исходное фото (ротация трёх источников):
   - Из /srv/creatives/raw/source/from_bot/ (UGC) если есть и не использовано — приоритет
   - Иначе равномерно чередует: Unsplash-сток ИЛИ генерация через PiAPI Nano Banana 2
3. Грузит на PiAPI Kling 2.5
4. После готовности (polling до 5 мин) скачивает Kling-результат
5. Делает РОВНО 1 ролик через creative_variator_v2 (правило «1 креатив = 1 видео»)
6. Сохраняет 1 пост в social_posts

Правило «1 креатив = 1 видео»: из одного исходного фото получается одно
видео и один пост — без размножения вариациями (для чистоты рекламных креативов).

Cron на VPS:
    0 4 */3 * *  /usr/bin/python3 /srv/creatives/auto_creative_generator.py >> /var/log/auto_gen.log 2>&1
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PIAPI_KEY = env["PIAPI_KEY"]

CATEGORIES = ["memory", "babies", "pets", "love"]
HOOKS = {
    "memory": [
        "Оживи фото бабушки", "Папа плакал, увидев маму", "Подарок к 80-летию",
        "Семейный архив снова живой", "AI вернул её улыбку",
    ],
    "babies": [
        "Оживи детство ребёнка", "Подарок маме от внуков",
        "Первые шаги в живом видео", "Детские фото снова двигаются",
    ],
    "pets": [
        "В память о любимом питомце", "Когда его уже нет рядом",
        "Видео из фото вашего кота", "Сохраните память о собаке навсегда",
    ],
    "love": [
        "Свадебный подарок-сюрприз", "Оживи свадебное фото",
        "Love story в живом видео", "Подарок на годовщину свадьбы",
    ],
}
UNSPLASH_QUERIES = {
    "memory": ["elderly portrait", "old grandmother", "vintage photo elderly"],
    "babies": ["happy baby portrait", "toddler family", "cute child"],
    "pets": ["cat portrait", "dog face", "elderly pet"],
    "love": ["wedding couple", "bride groom", "couple portrait vintage"],
}

# Промпты для PiAPI Nano Banana 2 (text-to-image, исходник для оживления).
# Фотореалистичные портреты «как из семейного архива» — крупный план, смотрит в кадр.
NANO_PROMPTS = {
    "memory": "Photorealistic vertical portrait of a warm elderly grandmother, gentle smile, "
              "looking at camera, soft natural window light, slight film grain, vintage family "
              "photo aesthetic, sharp face, shallow depth of field",
    "babies": "Photorealistic vertical portrait of a cute happy toddler, big curious eyes, "
              "soft natural light, looking at camera, wholesome family photo aesthetic, sharp face",
    "pets": "Photorealistic vertical close-up portrait of an adorable fluffy cat, expressive eyes "
            "looking at camera, soft natural light, shallow depth of field, sharp detailed fur",
    "love": "Photorealistic vertical portrait of a happy elderly couple together, gentle smiles, "
            "looking at camera, warm natural light, nostalgic family photo aesthetic, sharp faces",
}

# Источники исходного фото, между которыми идёт равномерная ротация (кроме UGC-приоритета)
ROTATING_SOURCES = ["unsplash", "nano-banana"]


def psql_fetch(sql: str) -> str:
    r = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
        capture_output=True, text=True, timeout=15
    )
    return r.stdout.strip()


def pick_category() -> str:
    """Категория ротации: берём ту, для которой меньше всего posted-постов
    за последние 14 дней (для равномерного покрытия)."""
    out = psql_fetch("""
        SELECT
            substring(creative_file from 'v2_(memory|babies|pets|love)_') AS cat,
            COUNT(*) FILTER (WHERE youtube_status='posted') AS cnt
        FROM social_posts
        WHERE youtube_posted_at > NOW() - INTERVAL '14 days'
          AND creative_file ~ 'v2_(memory|babies|pets|love)_'
        GROUP BY 1 ORDER BY 2 ASC LIMIT 1
    """)
    if out:
        parts = out.split("|")
        if parts[0] in CATEGORIES:
            return parts[0]
    return random.choice(CATEGORIES)


def pick_rotating_source() -> str:
    """Выбирает реже использованный за 14 дней источник из ROTATING_SOURCES
    (unsplash / nano-banana) — для равномерной ротации."""
    out = psql_fetch("""
        SELECT split_part(source_photo_url, ':', 1) AS src, COUNT(*) AS cnt
        FROM auto_creative_log
        WHERE created_at > NOW() - INTERVAL '14 days'
          AND split_part(source_photo_url, ':', 1) = ANY(ARRAY['unsplash','nano-banana'])
        GROUP BY 1
    """)
    counts = {s: 0 for s in ROTATING_SOURCES}
    for line in out.split("\n"):
        if "|" in line:
            src, cnt = line.split("|", 1)
            if src in counts:
                counts[src] = int(cnt)
    # реже всего использованный; при равенстве — случайный
    least = min(counts.values())
    candidates = [s for s, c in counts.items() if c == least]
    return random.choice(candidates)


def pick_source_photo(category: str) -> tuple[str, str] | None:
    """Возвращает (image_url, source_tag).

    Ротация трёх источников:
      1) UGC из collect-bot — приоритет, если есть свежее неиспользованное фото;
      2) иначе равномерно чередуем Unsplash-сток и генерацию Nano Banana 2.
    source_tag начинается с типа источника (ugc/unsplash/nano-banana) — по нему
    pick_rotating_source считает статистику в auto_creative_log.
    """
    # 1) UGC — берём первое неиспользованное фото (всегда приоритет)
    from_bot_dir = Path("/srv/creatives/raw/source/from_bot")
    if from_bot_dir.exists():
        used = set(psql_fetch("SELECT DISTINCT source_photo_url FROM auto_creative_log WHERE source_photo_url IS NOT NULL").split("\n"))
        for f in sorted(from_bot_dir.glob("*.jpg")):
            local_url = f"file://{f}"
            if local_url not in used and str(f) not in used:
                # Заливаем на freeimage чтобы PiAPI мог скачать
                upload_url = upload_to_freeimage(str(f))
                if upload_url:
                    return upload_url, f"ugc:{f.name}"

    # 2) Ротация синтетических источников
    source = pick_rotating_source()
    if source == "nano-banana":
        print(f"  источник: nano-banana (генерация исходника)")
        task_id = nano_banana_create_task(category)
        if task_id:
            img_url = nano_banana_wait(task_id)
            if img_url:
                return img_url, f"nano-banana:{task_id}"
        print("  ⚠️ nano-banana не дал картинку — fallback на Unsplash")

    # 3) Unsplash (выбран ротацией или fallback)
    query = random.choice(UNSPLASH_QUERIES.get(category, ["portrait"]))
    # Используем Unsplash Source (random photo by query — без API key)
    url = f"https://source.unsplash.com/1080x1080/?{urllib.parse.quote(query)}"
    return url, f"unsplash:{query}"


def upload_to_freeimage(local_path: str) -> str | None:
    """POST на freeimage.host для получения публичной ссылки."""
    try:
        boundary = "----UploadBoundary"
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="key"\r\n\r\n'
        body += b"6d207e02198a847aa98d0a2a901485a5\r\n"  # публичный freeimage key
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="action"\r\n\r\nupload\r\n'
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="format"\r\n\r\njson\r\n'
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="source"; filename="img.jpg"\r\n'
        body += b"Content-Type: image/jpeg\r\n\r\n"
        body += Path(local_path).read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            "https://freeimage.host/api/1/upload",
            data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "User-Agent": "VideoAI-AutoGen/1.0"},
        )
        d = json.loads(urllib.request.urlopen(req, timeout=60).read())
        return d.get("image", {}).get("url")
    except Exception as e:
        print(f"freeimage upload error: {e}")
        return None


def piapi_create_task(image_url: str, prompt: str = "natural movement, blink, smile") -> str | None:
    body = json.dumps({
        "model": "kling",
        "task_type": "video_generation",
        "input": {
            "prompt": prompt,
            "negative_prompt": "blurry, low quality, distorted",
            "duration": 5,
            "aspect_ratio": "9:16",
            "image_url": image_url,
            "version": "2.5",
            "cfg_scale": 0.5,
        },
        "config": {"service_mode": "public"},
    }).encode()
    req = urllib.request.Request(
        "https://api.piapi.ai/api/v1/task", data=body, method="POST",
        headers={"x-api-key": PIAPI_KEY, "Content-Type": "application/json",
                 "User-Agent": "VideoAI-AutoGen/1.0"},
    )
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return r.get("data", {}).get("task_id")
    except Exception as e:
        print(f"piapi create error: {e}")
        return None


def piapi_wait(task_id: str, timeout: int = 600) -> str | None:
    """Polling статуса. Возвращает URL Kling-видео при completed."""
    started = time.time()
    while time.time() - started < timeout:
        try:
            req = urllib.request.Request(
                f"https://api.piapi.ai/api/v1/task/{task_id}",
                headers={"x-api-key": PIAPI_KEY, "User-Agent": "VideoAI-AutoGen/1.0"},
            )
            d = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("data", {})
            st = (d.get("status") or "").lower()
            if st == "completed":
                works = (d.get("output") or {}).get("works", [])
                if works and works[0].get("video"):
                    return works[0]["video"].get("resource_without_watermark") or works[0]["video"].get("resource")
                return (d.get("output") or {}).get("video_url")
            if st == "failed":
                print(f"piapi failed: {d.get('error', {}).get('message', '')}")
                return None
            time.sleep(20)
        except Exception as e:
            print(f"piapi poll error: {e}")
            time.sleep(20)
    return None


def nano_banana_create_task(category: str) -> str | None:
    """Создаёт text-to-image задачу в PiAPI Nano Banana 2 (Gemini). Возвращает task_id."""
    prompt = NANO_PROMPTS.get(category, NANO_PROMPTS["memory"])
    body = json.dumps({
        "model": "gemini",
        "task_type": "nano-banana-2",
        "input": {
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "resolution": "2K",
            "output_format": "png",
        },
        "config": {"service_mode": "public"},
    }).encode()
    req = urllib.request.Request(
        "https://api.piapi.ai/api/v1/task", data=body, method="POST",
        headers={"x-api-key": PIAPI_KEY, "Content-Type": "application/json",
                 "User-Agent": "VideoAI-AutoGen/1.0"},
    )
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return r.get("data", {}).get("task_id")
    except Exception as e:
        print(f"nano-banana create error: {e}")
        return None


def nano_banana_wait(task_id: str, timeout: int = 180) -> str | None:
    """Polling Nano Banana 2. Возвращает публичный URL сгенерированной картинки."""
    started = time.time()
    while time.time() - started < timeout:
        try:
            req = urllib.request.Request(
                f"https://api.piapi.ai/api/v1/task/{task_id}",
                headers={"x-api-key": PIAPI_KEY, "User-Agent": "VideoAI-AutoGen/1.0"},
            )
            d = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("data", {})
            st = (d.get("status") or "").lower()
            if st == "completed":
                out = d.get("output") or {}
                # PiAPI может вернуть картинку в разных полях — обрабатываем все варианты
                if out.get("image_url"):
                    return out["image_url"]
                imgs = out.get("image_urls") or out.get("temporary_image_urls")
                if imgs:
                    return imgs[0]
                works = out.get("works") or []
                if works:
                    img = works[0].get("image") or {}
                    return img.get("resource") or img.get("url") or works[0].get("url")
                print(f"nano-banana: нет URL картинки в output: {out}")
                return None
            if st == "failed":
                print(f"nano-banana failed: {d.get('error', {}).get('message', '')}")
                return None
            time.sleep(8)
        except Exception as e:
            print(f"nano-banana poll error: {e}")
            time.sleep(8)
    return None


def download_video(url: str, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        dest.write_bytes(r.read())


def main():
    psql_fetch("""
        CREATE TABLE IF NOT EXISTS auto_creative_log (
            id BIGSERIAL PRIMARY KEY,
            category TEXT,
            source_photo_url TEXT,
            piapi_task_id TEXT,
            kling_video_path TEXT,
            posts_created INT,
            status TEXT,
            error TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    ts = time.strftime("%Y-%m-%d %H:%M")
    category = pick_category()
    print(f"[{ts}] выбрана категория: {category}")

    src = pick_source_photo(category)
    if not src:
        print("❌ нет источника фото")
        return
    image_url, source_tag = src
    print(f"  фото: {source_tag}")

    task_id = piapi_create_task(image_url)
    if not task_id:
        psql_fetch(f"INSERT INTO auto_creative_log (category, source_photo_url, status, error) "
                   f"VALUES ('{category}', '{source_tag}', 'failed', 'piapi create failed')")
        return
    print(f"  task_id: {task_id}")

    video_url = piapi_wait(task_id, timeout=600)
    if not video_url:
        psql_fetch(f"INSERT INTO auto_creative_log (category, source_photo_url, piapi_task_id, status, error) "
                   f"VALUES ('{category}', '{source_tag}', '{task_id}', 'failed', 'kling timeout/error')")
        return

    # Скачиваем Kling видео
    kling_dir = Path("/srv/creatives/raw/kling")
    kling_dir.mkdir(parents=True, exist_ok=True)
    kling_file = kling_dir / f"kling_{task_id}.mp4"
    download_video(video_url, kling_file)
    print(f"  ✓ Kling скачан: {kling_file.name}")

    # Правило «1 креатив = 1 видео»: ровно 1 ролик из 1 исходного фото
    sys.path.insert(0, "/srv/creatives")
    from creative_variator_v2 import variate_v2
    hooks_pool = HOOKS[category]
    chosen_hooks = [random.choice(hooks_pool)]
    try:
        new_files = variate_v2(str(kling_file), category, chosen_hooks)
    except Exception as e:
        psql_fetch(f"INSERT INTO auto_creative_log (category, source_photo_url, piapi_task_id, status, error) "
                   f"VALUES ('{category}', '{source_tag}', '{task_id}', 'failed', 'variator: {str(e)[:100]}')")
        return

    # Создаём 1 social_post (правило «1 креатив = 1 видео»)
    posts_created = 0
    for i, nf in enumerate(new_files):
        hook = chosen_hooks[i] if i < len(chosen_hooks) else f"AI оживление #{i}"
        caption = f"Оживи фото за 60 секунд | botisk.ru"
        offset_days = i + 1
        scheduled_at = f"NOW() + interval '{offset_days} days'"
        sql = (
            "INSERT INTO social_posts (creative_file, title, caption, hashtags, "
            "target_youtube, target_telegram, target_vk, "
            "youtube_status, telegram_status, vk_status, scheduled_at) VALUES ("
            f"'{nf['filename']}',"
            f"'{hook.replace(chr(39), chr(39)*2)}',"
            f"'{caption.replace(chr(39), chr(39)*2)}',"
            f"'#оживитьфото #aiвидео',"
            f"true,true,false,"
            f"'pending','pending','skipped',{scheduled_at})"
        )
        psql_fetch(sql)
        posts_created += 1
        print(f"  ✓ post #{posts_created}: {nf['filename']}")

    psql_fetch(
        f"INSERT INTO auto_creative_log (category, source_photo_url, piapi_task_id, "
        f"kling_video_path, posts_created, status) VALUES "
        f"('{category}', '{source_tag}', '{task_id}', '{kling_file}', {posts_created}, 'completed')"
    )
    print(f"[{ts}] ✅ создано {posts_created} постов")


if __name__ == "__main__":
    main()

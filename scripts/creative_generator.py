#!/usr/bin/env python3
"""
Полный генератор креативов из фото.

1. Загружает фото на freeimage.host (как делает бот для пользователей)
2. Отправляет в PiAPI Kling → ждёт готовое видео (~60-120 сек)
3. Скачивает видео на VPS в /srv/creatives/raw/kling/
4. Через variator.py делает 3 вариации с текст-хуками
5. Регистрирует в social_posts с расписанием (по умолчанию 1 пост/день)

Категории → текст-хуки (по 5+ на каждую). Хуки эмоциональные, проверенные.

CLI:
    python creative_generator.py /path/to/photo.jpg --category memory
    # → 3 готовых видео в processed/, регистрация в social_posts на 1 день

Batch (вся папка):
    python creative_generator.py /srv/creatives/raw/source/ --category auto
    # auto = детект категории по имени файла (memory_*.jpg etc) или GPT vision
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

FREEIMAGE_API = "https://freeimage.host/api/1/upload"
FREEIMAGE_KEY = "6d207e02198a847aa98d0a2a901485a5"   # public API key
PIAPI_BASE = "https://api.piapi.ai/api/v1/task"

# Реальные эмоциональные хуки (проверенные паттерны)
HOOKS = {
    "memory": [
        "Оживи фото бабушки",
        "AI вернул её улыбку",
        "Подарок маме на 8 марта",
        "Семейный архив снова живой",
        "Бабушке 92 — она увидела себя молодой",
        "Папа плакал, увидев маму на видео",
        "Воспоминания о тех, кого уже нет",
    ],
    "babies": [
        "Оживи детство ребёнка",
        "Первые шаги в живом видео",
        "Подарок маме от внуков",
        "Семейная история продолжается",
        "Детские фото снова двигаются",
        "Мама расплакалась, увидев своего малыша",
    ],
    "pets": [
        "В память о любимом питомце",
        "Барсик ушёл год назад. AI вернул его",
        "Видео из фото вашего котика",
        "Сохраните память о собаке навсегда",
        "Когда питомца больше нет рядом",
        "Оживи фото пушистого друга",
    ],
    "love": [
        "Свадебный подарок-сюрприз",
        "Оживи свадебное фото",
        "Подарок на годовщину свадьбы",
        "Романтика 50 лет назад снова живая",
        "Сюрприз для жены",
        "Love story в живом видео",
    ],
}

# Шаблоны caption (описаний) для платформ
CAPTIONS = {
    "memory": [
        "✨ AI оживляет старые фотографии. Загрузите фото близких — получите живое видео за 60 секунд. Первое бесплатно.\n\n🔗 botisk.ru\n\n#оживитьфото #нейросеть #ai #память #семья #shorts",
        "💔 Они уже не с нами, но AI снова показывает их живыми. Загрузите старое фото на botisk.ru — получите видео.\n\n#память #семейныйархив #нейросеть #shorts",
    ],
    "babies": [
        "👶 Каждое детское фото — момент, который уходит. AI делает их живыми. botisk.ru — попробуйте бесплатно.\n\n#детство #мама #подарок #нейросеть #shorts",
    ],
    "pets": [
        "🐾 В память о питомце. AI оживляет фотографии. botisk.ru\n\n#питомцы #кот #собака #память #нейросеть #shorts",
    ],
    "love": [
        "💑 Подарок-сюрприз на годовщину. AI оживляет свадебное фото за 60 секунд. botisk.ru\n\n#свадьба #годовщина #подарок #нейросеть #shorts",
    ],
}

# Промпты для Kling — что должно происходить в видео
KLING_PROMPTS = {
    "memory": "An elderly person in the photo smiles gently, blinks naturally, slight head movement. Warm, nostalgic, cinematic",
    "babies": "The child in the photo smiles warmly, blinks, slight gentle movements. Wholesome, heartwarming",
    "pets": "The pet looks at the camera with affection, blinks, slight head tilt. Heartwarming",
    "love": "The couple in the photo smile at each other, gentle natural movements, romantic atmosphere",
}


def upload_to_freeimage(image_path: Path) -> str:
    """Загружает фото на freeimage.host, возвращает публичный URL."""
    import base64
    img_b64 = base64.b64encode(image_path.read_bytes()).decode()
    body = urllib.parse.urlencode({
        "key": FREEIMAGE_KEY, "action": "upload", "source": img_b64, "format": "json",
    }).encode()
    req = urllib.request.Request(FREEIMAGE_API, data=body, method="POST",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    if data.get("status_code") != 200 or not data.get("image", {}).get("url"):
        raise RuntimeError(f"freeimage upload failed: {data}")
    return data["image"]["url"]


def piapi_create_kling_task(env: dict, image_url: str, prompt: str) -> str:
    """Создаёт задачу в PiAPI Kling, возвращает task_id."""
    body = json.dumps({
        "model": "kling",
        "task_type": "video_generation",
        "service_mode": "public",   # PAYG, не HYA
        "input": {
            "image_url": image_url,
            "prompt": prompt,
            "cfg_scale": 0.5,
            "duration": 5,
            "aspect_ratio": "9:16",
            "mode": "std",
            "version": "2.5",
        },
    }).encode()
    req = urllib.request.Request(PIAPI_BASE, data=body, method="POST",
                                  headers={"Content-Type": "application/json",
                                            "x-api-key": env["PIAPI_KEY"],
                                            "User-Agent": "VideoAI-Generator/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    task_id = (data.get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"PiAPI create failed: {data}")
    return task_id


def piapi_wait_for_video(env: dict, task_id: str, timeout_sec: int = 600) -> str:
    """Поллит PiAPI пока status=completed, возвращает video URL без watermark."""
    started = time.time()
    while time.time() - started < timeout_sec:
        req = urllib.request.Request(f"{PIAPI_BASE}/{task_id}",
                                      headers={"x-api-key": env["PIAPI_KEY"],
                                                "User-Agent": "VideoAI-Generator/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        d = data.get("data", {}) or {}
        status = (d.get("status") or "").lower()
        if status == "completed":
            out = d.get("output") or {}
            works = out.get("works") or []
            if works:
                v = works[0].get("video") or {}
                url = v.get("resource_without_watermark") or v.get("resource") or out.get("video_url")
                if url:
                    return url
            video_url = out.get("video_url")
            if video_url:
                return video_url
            raise RuntimeError(f"no video URL in completed task: {d}")
        if status == "failed":
            err = (d.get("error") or {}).get("message") or "unknown"
            raise RuntimeError(f"Kling failed: {err}")
        elapsed = int(time.time() - started)
        print(f"  ⏳ status={status} ({elapsed}с)")
        time.sleep(8)
    raise TimeoutError(f"Kling timeout after {timeout_sec}s")


def download_video(url: str, dest: Path) -> Path:
    """Скачивает видео с User-Agent чтобы Cloudflare не блочил."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) VideoAI/1.0",
    })
    with urllib.request.urlopen(req, timeout=120) as r, dest.open("wb") as f:
        while chunk := r.read(64 * 1024):
            f.write(chunk)
    return dest


def detect_category_by_filename(filename: str) -> str:
    name = filename.lower()
    if any(k in name for k in ["memory", "память", "babuk", "бабуш", "дед", "елдер"]):
        return "memory"
    if any(k in name for k in ["baby", "babies", "child", "детск", "ребён"]):
        return "babies"
    if any(k in name for k in ["pet", "cat", "dog", "кот", "соб", "питом"]):
        return "pets"
    if any(k in name for k in ["love", "wedding", "свадьб", "пара", "коупл"]):
        return "love"
    return "memory"   # дефолт


def register_in_social_queue(env: dict, files: list[dict], schedule_start_hours: int = 0,
                              interval_hours: int = 24) -> None:
    """Регистрирует созданные креативы в очереди social_posts (через n8n webhook)."""
    base_url = "https://n8n.24isk.ru/webhook/social-add"
    schedule = time.time() + schedule_start_hours * 3600
    for f in files:
        # caption случайный из категории
        cat = f["category"]
        caption_pool = CAPTIONS.get(cat, CAPTIONS["memory"])
        caption = random.choice(caption_pool)

        body = json.dumps({
            "creative_file": f["filename"],
            "title": f["hook"],
            "caption": caption,
            "hashtags": "",  # уже в caption
            "scheduled_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(schedule)),
            "target_youtube": True,
            "target_telegram": True,
            "target_vk": True,
        }).encode()
        try:
            req = urllib.request.Request(base_url, data=body, method="POST",
                                          headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
            print(f"  📥 в очереди: id={resp.get('id')} time={resp.get('scheduled_at','?')[:19]}")
        except Exception as e:
            print(f"  ❌ social-add failed: {e}")
        schedule += interval_hours * 3600


def process_one(photo_path: Path, category: str, env: dict, raw_dir: Path) -> list[dict]:
    """Полный цикл для 1 фото: upload → Kling → download → variator."""
    print(f"\n🎬 Обработка {photo_path.name} (категория: {category})")
    print(f"  → upload to freeimage...")
    img_url = upload_to_freeimage(photo_path)
    print(f"  → image URL: {img_url}")

    prompt = KLING_PROMPTS.get(category, KLING_PROMPTS["memory"])
    print(f"  → PiAPI Kling task...")
    task_id = piapi_create_kling_task(env, img_url, prompt)
    print(f"  → task_id: {task_id}")

    video_url = piapi_wait_for_video(env, task_id, timeout_sec=600)
    print(f"  ✓ Kling готов: {video_url[:60]}...")

    # Скачиваем на VPS через SSH (не локально — мы локально, копируем через scp/sftp в /srv)
    # Здесь скрипт ДОЛЖЕН запускаться на VPS, иначе нужны 2 пути.
    # Для упрощения: пишем в локальный raw_dir, потом variator должен быть тоже локально.
    local_video = raw_dir / f"kling_{task_id}.mp4"
    download_video(video_url, local_video)
    print(f"  ✓ скачано: {local_video} ({local_video.stat().st_size / 1e6:.1f} MB)")

    # Variator (импорт локально, должен быть запущен на VPS)
    from creative_variator import variate
    hooks = random.sample(HOOKS[category], min(3, len(HOOKS[category])))
    variations = variate(str(local_video), category, hooks)
    for v in variations:
        v["source_photo"] = photo_path.name
        v["kling_task_id"] = task_id
    return variations


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="Файл или папка с фотками")
    ap.add_argument("--category", default="auto", choices=["memory", "babies", "pets", "love", "auto"])
    ap.add_argument("--register", action="store_true", help="Зарегистрировать в social_posts queue")
    ap.add_argument("--schedule-start-hours", type=int, default=2, help="Через сколько часов первый пост")
    ap.add_argument("--interval-hours", type=int, default=24, help="Интервал между постами")
    args = ap.parse_args()

    env = load_env()
    if not env.get("PIAPI_KEY"):
        print("❌ PIAPI_KEY отсутствует в .env"); sys.exit(1)

    # Файл или папка?
    path = Path(args.path)
    if path.is_file():
        photos = [path]
    elif path.is_dir():
        photos = sorted(p for p in path.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    else:
        print(f"❌ Не найден: {path}"); sys.exit(1)

    if not photos:
        print(f"❌ Нет фоток в {path}"); sys.exit(1)

    raw_dir = Path("/srv/creatives/raw/kling")
    raw_dir.mkdir(parents=True, exist_ok=True)

    all_variations = []
    for photo in photos:
        category = args.category if args.category != "auto" else detect_category_by_filename(photo.name)
        try:
            vars_ = process_one(photo, category, env, raw_dir)
            all_variations.extend(vars_)
        except Exception as e:
            print(f"❌ {photo.name}: {e}")
            continue

    print(f"\n{'='*60}")
    print(f"✓ Создано {len(all_variations)} вариаций из {len(photos)} фото")

    if args.register and all_variations:
        print(f"\n📥 Регистрирую в social_posts queue...")
        register_in_social_queue(env, all_variations, args.schedule_start_hours, args.interval_hours)

    out_json = Path("/srv/creatives/last_batch.json")
    out_json.write_text(json.dumps(all_variations, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📄 Сохранил manifest: {out_json}")


if __name__ == "__main__":
    main()

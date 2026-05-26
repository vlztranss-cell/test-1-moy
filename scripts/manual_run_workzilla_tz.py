"""
Ручной запуск генерации ТЗ + обложки вне n8n cron.
Делает один полный цикл (GPT-4o-mini + DALL-E 3) и сохраняет в БД.

Используется для:
- Первоначального наполнения дашборда
- Внеплановой генерации (праздник, тест)
- Отладки промптов

Запуск:
    python scripts/manual_run_workzilla_tz.py [platform]
    platform = dzen | pikabu | irecommend | otzovik | vc | habr
    если не указано — берётся следующая по расписанию
"""
import json
import random
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from ssh import psql

env = load_env()
OPENAI_KEY = env["OPENAI_API_KEY"]

TOPICS = {
    "dzen": ["детское фото мамы", "свадебное фото бабушки", "фото деда-фронтовика", "фото мамы 60-х", "питомец которого больше нет"],
    "pikabu": ["оживил фото деда-фронтовика к 9 мая", "фото свадьбы родителей", "первое фото мамы"],
    "irecommend": ["подарок на 8 марта", "подарок на день матери", "подарок на годовщину свадьбы"],
    "otzovik": ["подарок на ДР маме", "сравнение с фотовидеомонтажём", "опыт после 5 видео"],
    "vc": ["unit-экономика AI-сервиса", "почему лендинг не конвертит", "B2C на эмоциональной нише"],
    "habr": ["Kling 2.5 vs Sora", "архитектура n8n+Kling", "ffmpeg overlay без drawtext"],
}

PRICES = {"dzen": 350, "pikabu": 450, "irecommend": 250, "otzovik": 250, "vc": 600, "habr": 1000}

PROMPTS = {
    "dzen": """Ты — копирайтер для Яндекс.Дзена, аудитория женщины 30-55 лет.
Напиши пост от первого лица «подружка делится открытием» про оживление фотографий через AI (сервис botisk.ru).
Тема: {topic}
Требования: 800-1100 слов, разговорный тон, эмоциональная завязка, 2-3 личных истории, раздел «Что я узнала», упоминание botisk.ru без агрессии, цены 99/290/790 руб.
Возвращай JSON: {{"title": "...", "article_text": "полный текст"}}.""",
    "pikabu": """Ты — копирайтер для Pikabu. Напиши эмоциональный пост от первого лица про оживление фотографии родственника через AI (сервис botisk.ru).
Тема: {topic}
Требования: 500-800 слов, сильная эмоциональная завязка, конкретный персонаж, короткий тех. бит, упоминание botisk.ru, цены 99-290 руб.
JSON: {{"title": "...", "article_text": "..."}}.""",
    "irecommend": """Ты пишешь честный развёрнутый отзыв на IRecommend о сервисе оживления фото botisk.ru.
Тема: {topic}
Требования: 400-600 слов, рейтинг 5/5, блоки Достоинства и Недостатки, 3-5 конкретных деталей, цены 99/290/790.
JSON: {{"title": "...", "article_text": "Достоинства:...\\n\\nНедостатки:...\\n\\nОсновной текст: ..."}}.""",
    "otzovik": """Ты пишешь честный отзыв на Otzovik про сервис оживления фото botisk.ru. Стиль разговорный, женский.
Тема: {topic}
Требования: 400-700 слов, 5-7 достоинств, 2-3 недостатка, цены 99/290/790. Текст должен отличаться от IRecommend на 40%+.
JSON: {{"title": "...", "article_text": "..."}}.""",
    "vc": """Ты пишешь авторскую статью на vc.ru для основателей/маркетологов про запуск AI-сервиса botisk.ru.
Тема: {topic}
Требования: 1500-2200 слов, профессиональный тон, конкретные цифры, ссылка только 1-2 раза.
JSON: {{"title": "...", "article_text": "..."}}.""",
    "habr": """Ты пишешь технический разбор на Habr про AI-видео-генерацию.
Тема: {topic}
Требования: 2500-3500 слов, code snippets Python+ffmpeg, не продажный тон, ссылка botisk.ru только 1 раз в конце.
JSON: {{"title": "...", "article_text": "..."}}.""",
}

COVER_STYLES = {
    "dzen": ("warm vintage photo aesthetic, sepia tones, family album feel, soft lighting", "1024x1024"),
    "pikabu": ("emotional documentary photography, intimate moment, candid black and white old photograph", "1024x1024"),
    "irecommend": ("product review hero image, clean composition, family photo on display, before-after concept", "1024x1024"),
    "otzovik": ("reviewer testimonial style, soft warm lighting, holding old photograph", "1024x1024"),
    "vc": ("modern startup pitch hero image, dashboard analytics + family photo overlay, professional", "1792x1024"),
    "habr": ("technical illustration, AI neural network meets vintage photography, abstract data visualization", "1792x1024"),
}


def openai_request(endpoint, payload, timeout=180):
    req = urllib.request.Request(
        "https://api.openai.com/v1/" + endpoint,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + OPENAI_KEY,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def pick_next_platform():
    out, _ = psql(
        "SELECT platform FROM crowd_schedule WHERE enabled = TRUE "
        "AND (last_run_at IS NULL OR last_run_at < NOW() - (interval_days || ' days')::interval) "
        "ORDER BY priority ASC LIMIT 1"
    )
    return out.strip() or "dzen"


def generate_article(platform, topic):
    prompt = PROMPTS[platform].format(topic=topic)
    print(f"  GPT-4o-mini генерит статью...")
    resp = openai_request("chat/completions", {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.8,
        "max_tokens": 4000,
    })
    content = resp["choices"][0]["message"]["content"]
    return json.loads(content)


def generate_cover(platform, title, topic):
    style, size = COVER_STYLES[platform]
    prompt = (
        f'Create a high-quality cover image for an article titled "{title}". '
        f"Theme: {topic}. Service context: AI photo animation (revive old family photos via Kling neural network). "
        f"Visual style: {style}. "
        f"IMPORTANT: NO text or words in the image. Focus on visual storytelling. "
        f"Photorealistic. High emotional impact. Warm color palette suggesting family memories."
    )
    print(f"  DALL-E 3 генерит обложку ({size})...")
    resp = openai_request("images/generations", {
        "model": "dall-e-3",
        "prompt": prompt,
        "size": size,
        "quality": "standard",
        "style": "natural",
    }, timeout=120)
    return resp["data"][0]["url"], prompt


def main():
    platform = sys.argv[1] if len(sys.argv) > 1 else pick_next_platform()
    if platform not in TOPICS:
        print(f"❌ unknown platform {platform}")
        return

    topic = random.choice(TOPICS[platform])
    price = PRICES[platform]
    print(f"=== Platform: {platform}, Topic: {topic}, Price: {price}₽ ===")

    # 1. GPT генерит статью
    article = generate_article(platform, topic)
    title = article.get("title", "")
    text = article.get("article_text", "")
    print(f"  ✓ Title: {title[:80]}")
    print(f"  ✓ Word count: ~{len(text.split())}")

    # 2. DALL-E генерит обложку
    try:
        cover_url, cover_prompt = generate_cover(platform, title, topic)
        print(f"  ✓ Cover URL получен")
    except Exception as e:
        print(f"  ✗ Cover failed: {e}")
        cover_url, cover_prompt = None, None

    # 3. Сохраняем в БД
    import time
    utm = f"tz_{int(time.time())}_{platform}"
    title_safe = title.replace("'", "''")
    text_safe = text.replace("'", "''")
    cover_url_clause = f"'{cover_url}'" if cover_url else "NULL"
    cover_prompt_safe = (cover_prompt or "").replace("'", "''")[:1500]

    tz_md = (
        f"# ТЗ для исполнителя на Workzilla\n\n"
        f"**Площадка:** {platform}\n"
        f"**Тема:** {topic}\n"
        f"**Заголовок:** {title}\n\n"
        f"## ТЕКСТ ДЛЯ ПУБЛИКАЦИИ\n\n{text}\n\n"
        f"## UTM-метка\nbotisk.ru/?utm_source={platform}&utm_medium=article&utm_content={utm}"
    )
    tz_md_safe = tz_md.replace("'", "''")

    out, err = psql(
        f"INSERT INTO workzilla_tz_drafts "
        f"(platform, tz_title, article_text, suggested_price_rub, utm_content, status, "
        f"tz_markdown, cover_url, cover_prompt) "
        f"VALUES ('{platform}', '{title_safe}', '{text_safe}', {price}, '{utm}', 'ready_to_post', "
        f"'{tz_md_safe}', {cover_url_clause}, '{cover_prompt_safe}') RETURNING id"
    )
    if err: print(f"  SQL warning: {err[:200]}")
    print(f"  ✓ Saved id={out.strip()}")

    # 4. Обновляем расписание
    psql(f"UPDATE crowd_schedule SET last_run_at = NOW() WHERE platform = '{platform}'")
    print(f"\n✅ ТЗ для {platform} готово. Открой дашборд → секция Workzilla.")


if __name__ == "__main__":
    main()

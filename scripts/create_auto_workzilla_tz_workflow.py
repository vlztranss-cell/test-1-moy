"""
Mark_Auto_Workzilla_TZ — ежедневный cron 9:00 МСК.
Проверяет crowd_schedule: какая площадка не публиковалась N дней.
Берёт первую готовую к запуску. GPT-4o-mini генерирует:
  - Свежую статью под площадку (тон/длина/структура)
  - Полное ТЗ для Workzilla с финальным текстом

Сохраняет в workzilla_tz_drafts (status=ready_to_post).
Пользователь видит в дашборде → нажимает «Скопировать» → выкладывает на Workzilla.
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "6JRfp0UMBDBhhghL", "name": "Postgres account"}
OPENAI_CRED = {"id": "Pl8RJODLbgUy5Azg", "name": "OPENAI_CRED_ID"}

# SQL: какая площадка должна запуститься сегодня
PICK_PLATFORM_SQL = """
SELECT platform, interval_days, COALESCE(last_run_at, NOW() - INTERVAL '1 year') AS last_run
FROM crowd_schedule
WHERE enabled = TRUE
  AND (last_run_at IS NULL OR last_run_at < NOW() - (interval_days || ' days')::interval)
ORDER BY priority ASC
LIMIT 1;
""".strip()

# Шаблоны промптов под платформу
PROMPTS = {
    "dzen": """Ты — копирайтер для Яндекс.Дзена, аудитория женщины 30-55 лет.
Напиши пост от первого лица «подружка делится открытием» про оживление фотографий через AI (сервис botisk.ru).

ТРЕБОВАНИЯ:
- 800-1100 слов, тон тёплый/разговорный
- Свежая тема, не повторять предыдущие. Сегодняшняя тема: {topic}
- Эмоциональная завязка в первом абзаце
- 2-3 личных «истории» (вымышленных но правдоподобных)
- Раздел «Что я узнала» с практическими советами
- В конце упоминание botisk.ru без агрессивной рекламы
- Цены 99/290/790 руб

Возвращай JSON: {{"title": "...", "article_text": "полный текст с переносами строк"}}.""",

    "pikabu": """Ты — копирайтер для Pikabu, аудитория мужчины и женщины 20-45 лет, любят эмоциональные истории.
Напиши пост от первого лица про оживление фотографии родственника через AI (сервис botisk.ru).

ТРЕБОВАНИЯ:
- 500-800 слов, разговорный тон
- Свежая тема: {topic}
- Сильная эмоциональная завязка (НЕ начинать с «Здравствуйте, я хочу рассказать...»)
- Конкретный персонаж: бабушка, мама, дед или питомец
- Технический бит про Kling AI коротко
- В конце совет с УПОМИНАНИЕМ botisk.ru, но без агрессивной рекламы
- Цены: 99-290 руб

Возвращай JSON: {{"title": "...", "article_text": "полный текст"}}.""",

    "irecommend": """Ты — пишешь честный развёрнутый отзыв на IRecommend о сервисе оживления фото botisk.ru.
Тон: личный опыт, женский, 30-50 лет.

ТРЕБОВАНИЯ:
- 400-600 слов основного текста + отдельные блоки: Достоинства, Недостатки
- Рейтинг 5/5, но честно про недостатки (это поднимает доверие)
- Тема впечатлений: {topic}
- 3-5 конкретных деталей опыта
- Цены 99/290/790
- Без агрессивной рекламы

JSON: {{"title": "заголовок отзыва", "article_text": "Достоинства:...\\n\\nНедостатки:...\\n\\nОсновной текст: ..."}}.""",

    "otzovik": """Ты — пишешь честный отзыв на Otzovik про сервис оживления фото botisk.ru.
Стиль: разговорный, женский.

ТРЕБОВАНИЯ:
- 400-700 слов
- 5-7 пунктов в достоинствах, 2-3 в недостатках
- Тема: {topic}
- Текст ДОЛЖЕН отличаться от IRecommend на 40%+ (модерация Otzovik проверяет уникальность)
- Цены 99/290/790

JSON: {{"title": "...", "article_text": "..."}}.""",

    "vc": """Ты — пишешь авторскую статью на vc.ru для аудитории основателей/маркетологов.
Тема: запуск AI-сервиса botisk.ru, кейс с метриками.

ТРЕБОВАНИЯ:
- 1500-2200 слов
- Структура: лид → проблема → подход → метрики → инсайты → выводы
- Конкретные цифры (можно из реальных: 500 пользователей, 0.5% конверсия, CPC 4₽)
- Тема: {topic}
- Тон: профессиональный, не «продажный»
- Ссылка на botisk.ru только 1-2 раза

JSON: {{"title": "...", "article_text": "..."}}.""",

    "habr": """Ты — пишешь технический разбор на Habr про AI-видео-генерацию (Kling, Sora, Runway).

ТРЕБОВАНИЯ:
- 2500-3500 слов
- Структура: введение → сравнение моделей → архитектура → пример кода → подводные камни → юнит-экономика
- Тема: {topic}
- Конкретные code snippets (Python + ffmpeg)
- НЕ продажный тон, только техническая ценность
- Ссылка botisk.ru только 1 раз в конце

JSON: {{"title": "...", "article_text": "..."}}.""",
}

# Темы (ротация — каждый запуск GPT выбирает по принципу «не повторять»)
TOPICS_BY_PLATFORM = {
    "dzen":       ["детское фото мамы", "свадебное фото бабушки", "фото деда-фронтовика", "фото мамы 60-х", "питомец которого больше нет"],
    "pikabu":     ["оживил фото деда-фронтовика к 9 мая", "фото свадьбы родителей", "первое фото мамы", "питомец до и после"],
    "irecommend": ["подарок на 8 марта", "подарок на день матери", "подарок на годовщину свадьбы"],
    "otzovik":    ["подарок на ДР маме", "сравнение с фотовидеомонтажём", "опыт после 5 видео"],
    "vc":         ["unit-экономика AI-сервиса", "почему лендинг не конвертит", "B2C на эмоциональной нише", "стек на n8n"],
    "habr":       ["Kling 2.5 vs Sora", "архитектура n8n+Kling", "ffmpeg overlay без drawtext", "PostgreSQL для AI-сервиса"],
}

SUGGESTED_PRICES = {
    "dzen": 350, "pikabu": 450, "irecommend": 250, "otzovik": 250,
    "vc": 600, "habr": 1000,
}

import random
TOPIC_PICK_JS = """
const platform = $('Pick Platform').first().json.platform;
const topics = """ + json.dumps(TOPICS_BY_PLATFORM, ensure_ascii=False) + """;
const arr = topics[platform] || ['оживление фото'];
const topic = arr[Math.floor(Math.random() * arr.length)];
return [{ json: { platform, topic } }];
""".strip()

# JS чтобы выбрать промпт по платформе перед GPT (передаём строку в node)
PROMPT_BY_PLATFORM_JS = """
const platform = $('Choose Topic').first().json.platform;
const topic = $('Choose Topic').first().json.topic;
const prompts = """ + json.dumps(PROMPTS, ensure_ascii=False) + """;
const prompt = (prompts[platform] || '').replace('{topic}', topic);
return [{ json: { platform, topic, prompt } }];
""".strip()

SAVE_SQL = """
WITH inserted AS (
    INSERT INTO workzilla_tz_drafts
        (platform, tz_title, article_text, suggested_price_rub, utm_content, status,
         tz_markdown)
    VALUES (
        '{{ $('Choose Topic').first().json.platform }}',
        '{{ ($json.title || '').replace(/'/g, "''") }}',
        '{{ ($json.article_text || '').replace(/'/g, "''") }}',
        {{ ({dzen:350,pikabu:450,irecommend:250,otzovik:250,vc:600,habr:1000})[$('Choose Topic').first().json.platform] || 300 }},
        'tz_' || EXTRACT(EPOCH FROM NOW())::int || '_' || $('Choose Topic').first().json.platform,
        'ready_to_post',
        E'# ТЗ для исполнителя на Workzilla\\n\\n' ||
        E'**Площадка:** ' || $('Choose Topic').first().json.platform || E'\\n' ||
        E'**Тема:** ' || $('Choose Topic').first().json.topic || E'\\n' ||
        E'**Заголовок:** ' || COALESCE('{{ ($json.title || '').replace(/'/g, "''") }}', '') || E'\\n\\n' ||
        E'## ТЕКСТ ДЛЯ ПУБЛИКАЦИИ\\n\\n' ||
        COALESCE('{{ ($json.article_text || '').replace(/'/g, "''") }}', '') || E'\\n\\n' ||
        E'## UTM-метка\\n\\nВ тексте использовать ссылку:\\n' ||
        E'botisk.ru/?utm_source=' || $('Choose Topic').first().json.platform ||
        '&utm_medium=article&utm_content=tz_' || EXTRACT(EPOCH FROM NOW())::int
    )
    RETURNING id
)
UPDATE crowd_schedule SET last_run_at = NOW()
WHERE platform = '{{ $('Choose Topic').first().json.platform }}';
""".strip()


def build_workflow():
    return {
        "name": "Mark_Auto_Workzilla_TZ",
        "nodes": [
            {"parameters": {"rule": {"interval": [{"field": "cronExpression",
                "expression": "0 6 * * *"}]}},  # каждый день 9:00 МСК
             "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
             "position": [0, 0], "id": "tr", "name": "Daily"},
            {"parameters": {"operation": "executeQuery", "query": PICK_PLATFORM_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [200, 0], "id": "pp", "name": "Pick Platform",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {"jsCode": TOPIC_PICK_JS},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [400, 0], "id": "ct", "name": "Choose Topic"},
            {"parameters": {"jsCode": PROMPT_BY_PLATFORM_JS},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [600, 0], "id": "cp", "name": "Choose Prompt"},
            {"parameters": {
                "resource": "text", "operation": "message",
                "modelId": {"__rl": True, "value": "gpt-4o-mini", "mode": "list"},
                "messages": {"values": [{"role": "user", "content": "={{ $json.prompt }}"}]},
                "jsonOutput": True,
                "options": {"temperature": 0.8, "maxTokens": 4000},
              },
             "type": "@n8n/n8n-nodes-langchain.openAi", "typeVersion": 1.8,
             "position": [800, 0], "id": "gpt", "name": "Generate Content",
             "credentials": {"openAiApi": OPENAI_CRED}},
            {"parameters": {"operation": "executeQuery", "query": SAVE_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [1000, 0], "id": "sv", "name": "Save TZ",
             "credentials": {"postgres": PG_CRED}},
        ],
        "connections": {
            "Daily":            {"main": [[{"node": "Pick Platform", "type": "main", "index": 0}]]},
            "Pick Platform":    {"main": [[{"node": "Choose Topic", "type": "main", "index": 0}]]},
            "Choose Topic":     {"main": [[{"node": "Choose Prompt", "type": "main", "index": 0}]]},
            "Choose Prompt":    {"main": [[{"node": "Generate Content", "type": "main", "index": 0}]]},
            "Generate Content": {"main": [[{"node": "Save TZ", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base+"/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Mark_Auto_Workzilla_TZ"), None)
    body = build_workflow()
    if found:
        urllib.request.urlopen(urllib.request.Request(
            base+f"/api/v1/workflows/{found['id']}", data=json.dumps(body).encode(),
            method="PUT", headers={**h, "Content-Type": "application/json"}), timeout=30)
        wid = found["id"]; print(f"[OK] Updated {wid}")
    else:
        wid = json.loads(urllib.request.urlopen(urllib.request.Request(
            base+"/api/v1/workflows", data=json.dumps(body).encode(),
            method="POST", headers={**h, "Content-Type": "application/json"}), timeout=30).read())["id"]
        print(f"[OK] Created {wid}")
    try:
        urllib.request.urlopen(urllib.request.Request(
            base+f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15)
        print("[OK] Activated (Daily 9:00 МСК = 6:00 UTC)")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise


if __name__ == "__main__":
    main()

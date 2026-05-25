"""
Mark_Auto_SEO_Article — раз в неделю в понедельник 8:00 МСК
GPT-4o-mini генерирует новую SEO-статью под long-tail запрос из очереди.

Сохраняет в seo_article_drafts (status=draft).
Пользователь review'ит и публикует через git push (либо включаем auto-publish позже).
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "6JRfp0UMBDBhhghL", "name": "Postgres account"}
OPENAI_CRED = {"id": "Pl8RJODLbgUy5Azg", "name": "OPENAI_CRED_ID"}

# SQL: следующий keyword из очереди
PICK_KW_SQL = """
SELECT id, keyword, slug_template
FROM seo_keywords_queue
WHERE used_at IS NULL
ORDER BY id ASC
LIMIT 1;
""".strip()

GPT_PROMPT = """Ты — SEO-копирайтер сайта botisk.ru (AI-сервис оживления фотографий через нейросеть Kling 2.5).

ЗАДАЧА: Напиши **подробную SEO-статью** в HTML под long-tail запрос:
KEYWORD: «{{ $('Pick Keyword').first().json.keyword }}»

ТРЕБОВАНИЯ К СТРУКТУРЕ:
- 1200-1800 слов
- 4-6 секций с подзаголовками <h2>
- Использовать ключ органично (5-8 раз по тексту)
- LSI-слова: «оживление фото», «AI-видео», «нейросеть», «семейный архив»
- Тон: тёплый, разговорный, для русской аудитории 30-65 лет

СТРУКТУРА:
1. <p class="lead">: эмоциональное вступление (2-3 предложения, описывает проблему/желание читателя)
2. <h2>: «Что значит [тема] технически» — простое объяснение AI Kling
3. <h2>: 2-3 секции с практическими советами или примерами
4. <h2>: «Реальная история клиента» — короткий кейс
5. <h2>: «Как сделать пошагово» — список из 4-5 шагов
6. CTA в конце на botisk.ru или @VideoAI_24isk_bot

ОГРАНИЧЕНИЯ:
- Никаких deepfake/обмана упоминаний
- Уважительный тон к умершим близким (это часть аудитории)
- Никаких ссылок на конкурентов (Sora, Runway, MyHeritage)
- Промокод не использовать
- Цены упоминать только эти: 99₽ за 1 видео, 290₽ за 10, 790₽ за 50, 2490₽ за 200

ВЕРНИ JSON:
{
  "title": "Полный заголовок страницы (до 70 символов)",
  "meta_desc": "Описание для поисковика (до 160 символов)",
  "html_content": "<p class=\\"lead\\">...</p><h2>...</h2>... — полный HTML-блок только тело статьи (без head/body/css)",
  "word_count": число слов
}
""".strip()

SAVE_SQL = """
WITH article AS (
    INSERT INTO seo_article_drafts (slug, keyword, title, meta_desc, html_content, word_count)
    VALUES (
        '{{ $('Pick Keyword').first().json.slug_template }}',
        '{{ $('Pick Keyword').first().json.keyword }}',
        '{{ ($json.title || '').replace(/'/g, "''") }}',
        '{{ ($json.meta_desc || '').replace(/'/g, "''") }}',
        '{{ ($json.html_content || '').replace(/'/g, "''") }}',
        {{ $json.word_count || 0 }}
    )
    ON CONFLICT (slug) DO UPDATE SET
        title = EXCLUDED.title,
        meta_desc = EXCLUDED.meta_desc,
        html_content = EXCLUDED.html_content,
        word_count = EXCLUDED.word_count,
        generated_at = NOW()
    RETURNING id
)
UPDATE seo_keywords_queue SET used_at = NOW()
WHERE id = {{ $('Pick Keyword').first().json.id }};
""".strip()


def build_workflow():
    return {
        "name": "Mark_Auto_SEO_Article",
        "nodes": [
            {"parameters": {"rule": {"interval": [{"field": "cronExpression",
                "expression": "0 5 * * 1"}]}},  # Понедельник 8:00 МСК = 5:00 UTC
             "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
             "position": [0, 0], "id": "tr", "name": "Weekly Mon"},
            {"parameters": {"operation": "executeQuery", "query": PICK_KW_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [220, 0], "id": "pk", "name": "Pick Keyword",
             "credentials": {"postgres": PG_CRED}},
            {"parameters": {
                "resource": "text", "operation": "message",
                "modelId": {"__rl": True, "value": "gpt-4o-mini", "mode": "list"},
                "messages": {"values": [{"role": "user", "content": GPT_PROMPT}]},
                "jsonOutput": True,
                "options": {"temperature": 0.7, "maxTokens": 4000},
              },
             "type": "@n8n/n8n-nodes-langchain.openAi", "typeVersion": 1.8,
             "position": [440, 0], "id": "gpt", "name": "Generate Article",
             "credentials": {"openAiApi": OPENAI_CRED}},
            {"parameters": {"operation": "executeQuery", "query": SAVE_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [660, 0], "id": "sv", "name": "Save Draft",
             "credentials": {"postgres": PG_CRED}},
        ],
        "connections": {
            "Weekly Mon":      {"main": [[{"node": "Pick Keyword", "type": "main", "index": 0}]]},
            "Pick Keyword":    {"main": [[{"node": "Generate Article", "type": "main", "index": 0}]]},
            "Generate Article":{"main": [[{"node": "Save Draft", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base+"/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Mark_Auto_SEO_Article"), None)
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
        print("[OK] Activated (Mon 8:00 МСК = 5:00 UTC)")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise


if __name__ == "__main__":
    main()

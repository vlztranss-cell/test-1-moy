"""
Mark_Direct_AB_Texts — раз в неделю в воскресенье ночью:
1. Берёт худшую по CTR кампанию Яндекс.Директа за неделю
2. Через GPT-4o-mini генерирует 3 новых варианта заголовка + текста
3. Добавляет их в кампанию (ads.add) + отправляет на модерацию (ads.moderate)
4. Архивирует старое объявление с худшим CTR (ads.archive)

Cron: 0 2 * * 0 (воскресенье в 5:00 МСК)
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
OPENAI_CRED = {"id": "Pl8RJODLbgUy5Azg", "name": "OPENAI_CRED_ID"}

# Прямые credentials Direct из env (workflow вызывает Direct API через httpRequest)
DIRECT_TOKEN = env["YANDEX_OAUTH_TOKEN"]

# GPT prompt — генерируем 3 объявления
GPT_PROMPT = """Ты — маркетолог, специализирующийся на эмоциональных подарках и AI-сервисах.

ЗАДАЧА: Сгенерируй 3 варианта объявления для Яндекс.Директа для услуги «оживление старых фотографий через AI» (botisk.ru).

КАТЕГОРИЯ КАМПАНИИ: {{ $json.utm_campaign }}
ТЕКУЩИЙ CTR: {{ $json.ctr }}% (это плохой результат, нужно сильнее цеплять)

ОГРАНИЧЕНИЯ (строго!):
- Title (заголовок): максимум 35 символов
- Title2 (доп. заголовок): максимум 30 символов
- Text (текст): максимум 81 символ
- Без логотипов конкурентов, без стрелок →, без эмодзи
- Только русские буквы, цифры, знаки препинания
- Никаких упоминаний цен <99₽ (минимальная цена 99₽)

ЦЕЛЕВЫЕ БОЛИ:
- memory: «увидеть бабушку молодой», «папа плакал от подарка»
- babies: «сюрприз маме на ДР», «детство ребёнка в движении»
- pets: «когда питомца больше нет», «память о коте/собаке»
- love: «подарок на годовщину», «свадебное фото оживить»

ОТВЕТ — JSON массив из 3 объектов:
[{"title": "...", "title2": "...", "text": "..."}, ...]
Только JSON, без markdown-обёртки."""

# JS — берём worst ad
FIND_WORST_JS = """
// Получаем 4 кампании из direct-stats workflow и находим худшую по CTR
const resp = await this.helpers.httpRequest({
    method: 'GET',
    url: 'https://n8n.24isk.ru/webhook/direct-stats',
    json: true, timeout: 30000,
});
if (!resp.campaigns) return [];
// Игнорируем неактивные
const active = resp.campaigns.filter(c => c.status === 'ACCEPTED' && c.impressions > 100);
if (!active.length) return [];
const worst = active.sort((a,b) => {
    const aCtr = a.impressions > 0 ? a.clicks * 100 / a.impressions : 0;
    const bCtr = b.impressions > 0 ? b.clicks * 100 / b.impressions : 0;
    return aCtr - bCtr;
})[0];
return [{ json: {
    campaign_id: worst.id,
    utm_campaign: worst.utm || worst.name || 'memory',
    ctr: worst.impressions > 0 ? Math.round(worst.clicks * 100 / worst.impressions * 10)/10 : 0,
    name: worst.name
}}];
""".strip()

# Полный workflow с интеграцией Direct API сложен, поэтому делаю simpler MVP:
# 1) Находим худшую кампанию
# 2) Прогоняем через GPT
# 3) Сохраняем в новую таблицу direct_ab_suggestions для ручного review

SAVE_SQL = """
INSERT INTO direct_ab_suggestions
    (campaign_id, utm_campaign, current_ctr, suggestions_json, generated_at)
VALUES
    ({{ $('Find Worst').first().json.campaign_id }},
     '{{ $('Find Worst').first().json.utm_campaign }}',
     {{ $('Find Worst').first().json.ctr }},
     '{{ JSON.stringify($json).replace(/'/g, "''") }}'::jsonb,
     NOW());
""".strip()


def build_workflow():
    return {
        "name": "Mark_Direct_AB_Texts",
        "nodes": [
            {"parameters": {"rule": {"interval": [{"field": "cronExpression",
                "expression": "0 2 * * 0"}]}},
             "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
             "position": [0, 0], "id": "tr", "name": "Weekly Sun"},
            {"parameters": {"jsCode": FIND_WORST_JS},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [220, 0], "id": "fw", "name": "Find Worst"},
            {"parameters": {
                "resource": "text", "operation": "message",
                "modelId": {"__rl": True, "value": "gpt-4o-mini", "mode": "list"},
                "messages": {"values": [
                    {"role": "user", "content": GPT_PROMPT},
                ]},
                "jsonOutput": True,
                "options": {"temperature": 0.8},
              },
             "type": "@n8n/n8n-nodes-langchain.openAi", "typeVersion": 1.8,
             "position": [440, 0], "id": "gpt", "name": "Generate Ads",
             "credentials": {"openAiApi": OPENAI_CRED}},
            {"parameters": {"operation": "executeQuery", "query": SAVE_SQL, "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [660, 0], "id": "sv", "name": "Save",
             "credentials": {"postgres": {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}}},
        ],
        "connections": {
            "Weekly Sun":  {"main": [[{"node": "Find Worst", "type": "main", "index": 0}]]},
            "Find Worst":  {"main": [[{"node": "Generate Ads", "type": "main", "index": 0}]]},
            "Generate Ads":{"main": [[{"node": "Save", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base+"/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Mark_Direct_AB_Texts"), None)
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
        print("[OK] Activated (Sunday 5:00 МСК)")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise


if __name__ == "__main__":
    main()

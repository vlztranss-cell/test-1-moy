"""
Mark_TGStat_Seeding_Finder — ищет TG-каналы для посевов через TGStat API.

Критерии маркетингового профи:
- Тематика: 'Психология', 'Семья', 'Воспитание', 'Воспоминания', 'Истории',
  'Ностальгия', 'Подарки', 'Рукоделие', 'Генеалогия', 'Семейный архив'
- Подписчики: 5000–100000 (sweet spot — не слишком мало, не слишком дорого)
- ER (Engagement Rate): ≥ 5% (живая аудитория)
- AvgViews / subscribers: ≥ 30% (не мёртвый канал)
- География: Россия + СНГ

Score = (er_percent × 2) + (avg_views_ratio × 100) - log10(subscribers) × 10
(чем больше — тем интереснее канал; штрафуем избыточный размер)

Запуск: ручной из дашборда или по cron раз в неделю.
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
PG_CRED = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}
TGSTAT_CRED = {"id": "dtpaFWozqmvFMB8P", "name": "api.tgstat.ru"}

KEYWORDS = [
    "оживить фото", "AI видео", "память семьи", "семейный архив",
    "подарки маме", "подарки бабушке", "фотореставрация",
    "ностальгия", "старые фото", "цифровая память"
]

# JS-код после HTTP запроса — фильтрация + scoring
PROCESS_JS = """
const results = [];
const all = $input.all();
for (const item of all) {
    const data = item.json;
    if (!data.response || !data.response.items) continue;
    for (const ch of data.response.items) {
        const subs = ch.participants_count || 0;
        const avgViews = ch.avg_post_reach || 0;
        const er = ch.er || 0;  // engagement rate %
        const adPrice = ch.ad_price || null;
        // Фильтры
        if (subs < 5000 || subs > 100000) continue;
        if (er < 5) continue;
        if (avgViews / Math.max(subs, 1) < 0.3) continue;
        // Score
        const score = er * 2 + (avgViews / Math.max(subs, 1)) * 100 - Math.log10(Math.max(subs, 1)) * 10;
        results.push({
            channel_username: ch.username || ch.link || '',
            title: ch.title || '',
            category: ch.category || '',
            subscribers: subs,
            er_percent: Math.round(er * 100) / 100,
            avg_views: avgViews,
            ad_price_rub: adPrice,
            contact: ch.contact || null,
            score: Math.round(score * 100) / 100,
            rationale: `subs=${subs} er=${er}% avgViews/subs=${Math.round(avgViews/subs*100)}%`,
        });
    }
}
return results.sort((a,b) => b.score - a.score).slice(0, 30).map(c => ({json: c}));
""".strip()

UPSERT_SQL = """
INSERT INTO tg_seeding_channels
    (channel_username, title, category, subscribers, er_percent,
     avg_views, ad_price_rub, contact, score, rationale)
VALUES
    ('{{ $json.channel_username.replace(/'/g, "''") }}',
     '{{ ($json.title || '').replace(/'/g, "''").substring(0, 200) }}',
     '{{ ($json.category || '').replace(/'/g, "''").substring(0, 50) }}',
     {{ $json.subscribers }}, {{ $json.er_percent }},
     {{ $json.avg_views }},
     {{ $json.ad_price_rub === null ? 'NULL' : $json.ad_price_rub }},
     {{ $json.contact ? "'" + $json.contact.replace(/'/g, "''") + "'" : 'NULL' }},
     {{ $json.score }},
     '{{ ($json.rationale || '').replace(/'/g, "''") }}')
ON CONFLICT (channel_username) DO UPDATE SET
    subscribers = EXCLUDED.subscribers,
    er_percent = EXCLUDED.er_percent,
    avg_views = EXCLUDED.avg_views,
    score = EXCLUDED.score,
    rationale = EXCLUDED.rationale,
    found_at = NOW();
""".strip()


def build_workflow():
    """Делает массив HTTP-запросов: один на keyword + один process+upsert на каждый."""
    nodes = [
        {"parameters": {}, "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
         "position": [0, 0], "id": "mt", "name": "Manual"},
    ]
    connections = {"Manual": {"main": [[]]}}

    # Один HTTP запрос для каждого keyword
    last = "Manual"
    for i, kw in enumerate(KEYWORDS):
        node_id = f"q{i}"
        node_name = f"Search '{kw[:20]}'"
        nodes.append({
            "parameters": {
                "url": "https://api.tgstat.ru/channels/search",
                "authentication": "predefinedCredentialType",
                "nodeCredentialType": "httpHeaderAuth",
                "sendQuery": True,
                "queryParameters": {"parameters": [
                    {"name": "q", "value": kw},
                    {"name": "country", "value": "ru"},
                    {"name": "limit", "value": "30"},
                ]},
                "options": {},
            },
            "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
            "position": [(i+1) * 200, 0], "id": node_id, "name": node_name,
            "credentials": {"httpHeaderAuth": TGSTAT_CRED},
        })
        connections.setdefault(last, {"main": [[]]})
        connections[last]["main"][0].append({"node": node_name, "type": "main", "index": 0})
        connections.setdefault(node_name, {"main": [[]]})
        last = node_name

    # Merge всех результатов, Process, Upsert
    nodes.extend([
        {"parameters": {"jsCode": PROCESS_JS},
         "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [(len(KEYWORDS)+1) * 200, 0], "id": "proc", "name": "Process & Score"},
        {"parameters": {"operation": "executeQuery", "query": UPSERT_SQL, "options": {}},
         "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
         "position": [(len(KEYWORDS)+2) * 200, 0], "id": "ups", "name": "Save to DB",
         "credentials": {"postgres": PG_CRED}},
    ])
    connections[last]["main"][0].append({"node": "Process & Score", "type": "main", "index": 0})
    connections["Process & Score"] = {"main": [[{"node": "Save to DB", "type": "main", "index": 0}]]}

    return {
        "name": "Mark_TGStat_Seeding_Finder",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    existing = json.loads(urllib.request.urlopen(
        urllib.request.Request(base+"/api/v1/workflows?limit=200", headers=h), timeout=20).read())["data"]
    found = next((w for w in existing if w["name"] == "Mark_TGStat_Seeding_Finder"), None)
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
    # Active не нужен для manual trigger, но укажу для удобства
    print(f"\n→ Запустить вручную: открыть workflow в n8n UI и нажать Execute")
    print(f"→ Или: вызвать через API workflow_id={wid}")


if __name__ == "__main__":
    main()

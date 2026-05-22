"""
n8n workflow Web_Direct_Stats: GET /webhook/direct-stats
→ через Direct API возвращает живой статус 4 VideoAI-кампаний (state, status, impressions, clicks)
Используется дашбордом для апдейта карточек кампаний.

NB: Yandex Direct OAuth токен хранится в credentials n8n (создаётся
вручную при первом запуске — см. ниже).
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

# Получим токен из .env и зашьём его в Code-ноду (НЕ через credentials — упрощённо)
env = load_env()
TOKEN = env['YANDEX_OAUTH_TOKEN']

FETCH_JS = f"""
const TOKEN = '{TOKEN}';
const CAMPAIGN_IDS = [710122418, 710122420, 710122422, 710122424];

const body = {{
    method: 'get',
    params: {{
        SelectionCriteria: {{ Ids: CAMPAIGN_IDS }},
        FieldNames: ['Id', 'Name', 'State', 'Status', 'StatusClarification', 'Statistics']
    }}
}};

const resp = await this.helpers.httpRequest({{
    method: 'POST',
    url: 'https://api.direct.yandex.com/json/v5/campaigns',
    headers: {{
        'Authorization': 'Bearer ' + TOKEN,
        'Accept-Language': 'ru',
        'Content-Type': 'application/json; charset=utf-8',
    }},
    body: JSON.stringify(body),
    json: false,
    timeout: 15000,
}});

let data;
try {{
    data = typeof resp === 'string' ? JSON.parse(resp) : resp;
}} catch (e) {{
    return [{{json: {{error: 'parse_failed', raw: String(resp).substring(0, 500)}}}}];
}}

if (data.error) {{
    return [{{json: {{error: data.error.error_string, code: data.error.error_code}}}}];
}}

const campaigns = (data.result && data.result.Campaigns) || [];
const total = campaigns.reduce((acc, c) => {{
    const s = c.Statistics || {{}};
    acc.impressions += s.Impressions || 0;
    acc.clicks      += s.Clicks      || 0;
    return acc;
}}, {{impressions: 0, clicks: 0}});

return [{{json: {{
    campaigns: campaigns.map(c => ({{
        id: c.Id,
        name: c.Name,
        state: c.State,
        status: c.Status,
        clarification: c.StatusClarification || '',
        impressions: (c.Statistics && c.Statistics.Impressions) || 0,
        clicks:      (c.Statistics && c.Statistics.Clicks)      || 0,
    }})),
    total,
    fetched_at: new Date().toISOString(),
}}}}];
""".strip()


def build_workflow():
    return {
        "name": "Web_Direct_Stats",
        "nodes": [
            {
                "parameters": {
                    "path": "direct-stats",
                    "httpMethod": "GET",
                    "responseMode": "responseNode",
                    "options": {},
                },
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2.1,
                "position": [0, 0],
                "id": "direct-stats-webhook-2026",
                "name": "Webhook",
                "webhookId": str(uuid.uuid4()),
            },
            {
                "parameters": {"jsCode": FETCH_JS},
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [220, 0],
                "id": "direct-stats-fetch-2026",
                "name": "Fetch Direct API",
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": "={{ JSON.stringify($json) }}",
                    "options": {
                        "responseHeaders": {
                            "entries": [{"name": "Access-Control-Allow-Origin", "value": "*"}]
                        },
                    },
                },
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.5,
                "position": [440, 0],
                "id": "direct-stats-respond-2026",
                "name": "Respond",
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Fetch Direct API", "type": "main", "index": 0}]]},
            "Fetch Direct API": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}

    with urllib.request.urlopen(urllib.request.Request(base + "/api/v1/workflows", headers=h), timeout=30) as r:
        existing = json.loads(r.read())["data"]
    found = next((w for w in existing if w["name"] == "Web_Direct_Stats"), None)
    body = build_workflow()

    if found:
        url = base + f"/api/v1/workflows/{found['id']}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT",
                                     headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        wid = found["id"]
        print(f"[OK] Обновлён Web_Direct_Stats: id={wid}, версия={res.get('versionId','?')[:8]}")
    else:
        req = urllib.request.Request(base + "/api/v1/workflows", data=json.dumps(body).encode(), method="POST",
                                     headers={**h, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr); raise
        wid = res["id"]
        print(f"[OK] Создан Web_Direct_Stats: id={wid}")

    try:
        with urllib.request.urlopen(urllib.request.Request(base + f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15) as r:
            print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code in (200, 400):
            print("  (уже активен)")
        else:
            raise

    print(f"\nWebhook: {base}/webhook/direct-stats")


if __name__ == "__main__":
    main()

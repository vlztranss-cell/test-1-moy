"""
n8n workflow Web_Metrika_Stats:
GET /webhook/metrika-stats → возвращает данные по 9 целям + сводку
из Yandex Metrika API за последние 24 часа.

Goals (id из .env):
  PHOTO_UPLOADED, GENERATION_STARTED, FREE_GEN_COMPLETED,
  PAID_GEN_COMPLETED, GEN_FAILED, PAYMENT_OPEN,
  PAYMENT_REDIRECT, PAYMENT_SUCCESS, VIDEO_DOWNLOAD
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
TOKEN = env['YANDEX_OAUTH_TOKEN']
COUNTER = env['YANDEX_METRIKA_COUNTER_ID']

# Все Goal IDs
GOALS = {
    'PHOTO_UPLOADED':      env.get('YANDEX_GOAL_PHOTO_UPLOADED'),
    'GENERATION_STARTED':  env.get('YANDEX_GOAL_GENERATION_STARTED'),
    'FREE_GEN_COMPLETED':  env.get('YANDEX_GOAL_FREE_GEN_COMPLETED'),
    'PAID_GEN_COMPLETED':  env.get('YANDEX_GOAL_PAID_GEN_COMPLETED'),
    'GEN_FAILED':          env.get('YANDEX_GOAL_GEN_FAILED'),
    'PAYMENT_OPEN':        env.get('YANDEX_GOAL_PAYMENT_OPEN'),
    'PAYMENT_REDIRECT':    env.get('YANDEX_GOAL_PAYMENT_REDIRECT'),
    'PAYMENT_SUCCESS':     env.get('YANDEX_GOAL_PAYMENT_SUCCESS'),
    'VIDEO_DOWNLOAD':      env.get('YANDEX_GOAL_VIDEO_DOWNLOAD'),
}

# JS-код для n8n: дёргает Metrika API.
# В sandbox n8n нет URLSearchParams — собираем query вручную.
FETCH_JS = f"""
const TOKEN = '{TOKEN}';
const COUNTER = '{COUNTER}';
const GOALS = {json.dumps({k:v for k,v in GOALS.items() if v}, ensure_ascii=False)};

function qs(params) {{
    return Object.entries(params)
        .filter(([_, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v))
        .join('&');
}}

const baseUrl = 'https://api-metrika.yandex.net/stat/v1/data';
const auth = {{'Authorization': 'OAuth ' + TOKEN}};

// ОПТИМИЗАЦИЯ: объединяем 9 целей + summary в ОДИН запрос (вместо 11).
// Yandex Metrika позволяет до 20 metrics за запрос.
// Раньше был HTTP 429 rate-limit из-за слишком частых обращений.
const goalEntries = Object.entries(GOALS).filter(([,id]) => id);
const goalMetrics = [];
for (const [, gid] of goalEntries) {{
    goalMetrics.push('ym:s:goal' + gid + 'reaches');
    goalMetrics.push('ym:s:goal' + gid + 'users');
}}
const allMetrics = 'ym:s:visits,ym:s:users,ym:s:bounceRate,ym:s:pageDepth,ym:s:avgVisitDurationSeconds,' + goalMetrics.join(',');

const combined = await this.helpers.httpRequest({{
    method: 'GET',
    url: baseUrl + '?' + qs({{
        ids: COUNTER,
        metrics: allMetrics,
        date1: 'today',
        date2: 'today',
        accuracy: 'full',
    }}),
    headers: auth,
    json: true,
    timeout: 20000,
}});

const totalsFlat = combined.totals || [];
// Первые 5 — summary
const t = totalsFlat.slice(0, 5).length ? totalsFlat.slice(0, 5) : [0,0,0,0,0];

// Далее — пары [reaches, users] для каждой цели
const goalsStats = {{}};
let idx = 5;
for (const [name, goalId] of goalEntries) {{
    goalsStats[name] = {{
        id: goalId,
        reaches: Math.round(totalsFlat[idx] || 0),
        users: Math.round(totalsFlat[idx + 1] || 0),
    }};
    idx += 2;
}}

// UTM-источники — отдельным запросом (нужны dimensions)
let traffic = [];
try {{
    const tr = await this.helpers.httpRequest({{
        method: 'GET',
        url: baseUrl + '?' + qs({{
            ids: COUNTER,
            metrics: 'ym:s:visits',
            dimensions: 'ym:s:UTMCampaign',
            date1: '7daysAgo', date2: 'today',
            filters: 'ym:s:UTMCampaign!n',
            accuracy: 'full', limit: 10,
        }}),
        headers: auth, json: true, timeout: 10000,
    }});
    traffic = (tr.data || []).map(d => ({{
        campaign: d.dimensions[0].name,
        visits: Math.round(d.metrics[0] || 0),
    }}));
}} catch (e) {{}}
return [{{json: {{
    summary: {{
        visits: Math.round(t[0] || 0),
        users: Math.round(t[1] || 0),
        bounce_rate: Math.round((t[2] || 0) * 10) / 10,
        avg_pages: Math.round((t[3] || 0) * 10) / 10,
        avg_duration_sec: Math.round(t[4] || 0),
    }},
    goals: goalsStats,
    traffic_by_campaign: traffic,
    fetched_at: new Date().toISOString(),
}}}}];
""".strip()


def build_workflow():
    return {
        "name": "Web_Metrika_Stats",
        "nodes": [
            {"parameters":{"path":"metrika-stats","httpMethod":"GET","responseMode":"responseNode","options":{}},
             "type":"n8n-nodes-base.webhook","typeVersion":2.1,"position":[0,0],
             "id":"mw","name":"Webhook","webhookId":str(uuid.uuid4())},
            {"parameters":{"jsCode":FETCH_JS},
             "type":"n8n-nodes-base.code","typeVersion":2,"position":[220,0],"id":"mf","name":"Fetch"},
            {"parameters":{"respondWith":"json","responseBody":"={{ JSON.stringify($json) }}",
                "options":{"responseHeaders":{"entries":[{"name":"Access-Control-Allow-Origin","value":"*"}]}}},
             "type":"n8n-nodes-base.respondToWebhook","typeVersion":1.5,"position":[440,0],"id":"mr","name":"Respond"},
        ],
        "connections": {
            "Webhook": {"main":[[{"node":"Fetch","type":"main","index":0}]]},
            "Fetch": {"main":[[{"node":"Respond","type":"main","index":0}]]},
        },
        "settings": {"executionOrder":"v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    with urllib.request.urlopen(urllib.request.Request(base + "/api/v1/workflows", headers=h), timeout=30) as r:
        existing = json.loads(r.read())["data"]
    found = next((w for w in existing if w["name"] == "Web_Metrika_Stats"), None)
    body = build_workflow()
    if found:
        url = base + f"/api/v1/workflows/{found['id']}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT",
                                     headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"[OK] Updated {found['id']}")
        wid = found["id"]
    else:
        req = urllib.request.Request(base + "/api/v1/workflows", data=json.dumps(body).encode(), method="POST",
                                     headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            wid = json.loads(r.read())["id"]
        print(f"[OK] Created {wid}")
    try:
        urllib.request.urlopen(urllib.request.Request(base + f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15)
        print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise
    print(f"\nGET {base}/webhook/metrika-stats")


if __name__ == "__main__":
    main()

"""
n8n workflow Web_YouTube_Stats:
GET /webhook/youtube-stats → JSON со статистикой просмотров видео из БД.

Возвращает:
  - summary: total views/likes/comments + counts
  - by_category: разбивка по категориям (memory, babies, pets, love)
  - top_videos: топ-10 по просмотрам
  - all_videos: полный список с url
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()

# Используем существующий PG-credential photo_bot (см. другие workflow)
# Достанем его id из API
PG_CRED_NAME = "photo_bot"

JS_AGGREGATE = """
const rows = $input.all().map(i => i.json);

const summary = {
    videos_count: rows.length,
    total_views: rows.reduce((s, r) => s + Number(r.views || 0), 0),
    total_likes: rows.reduce((s, r) => s + Number(r.likes || 0), 0),
    total_comments: rows.reduce((s, r) => s + Number(r.comments || 0), 0),
    avg_views: rows.length ? Math.round(rows.reduce((s, r) => s + Number(r.views || 0), 0) / rows.length) : 0,
};

const byCat = {};
for (const r of rows) {
    const c = r.category || 'unknown';
    if (!byCat[c]) byCat[c] = { videos: 0, views: 0, likes: 0, comments: 0 };
    byCat[c].videos += 1;
    byCat[c].views += Number(r.views || 0);
    byCat[c].likes += Number(r.likes || 0);
    byCat[c].comments += Number(r.comments || 0);
}
const by_category = Object.entries(byCat)
    .map(([k, v]) => ({ category: k, ...v, avg_views: v.videos ? Math.round(v.views / v.videos) : 0 }))
    .sort((a, b) => b.views - a.views);

const top_videos = [...rows]
    .sort((a, b) => Number(b.views || 0) - Number(a.views || 0))
    .slice(0, 10)
    .map(r => ({
        video_id: r.video_id,
        title: r.title,
        category: r.category,
        views: Number(r.views || 0),
        likes: Number(r.likes || 0),
        comments: Number(r.comments || 0),
        url: r.url,
        published_at: r.published_at,
        duration_seconds: r.duration_seconds,
    }));

const fetched_at = rows.length ? rows[0].fetched_at : null;

return [{ json: { summary, by_category, top_videos, fetched_at } }];
""".strip()


def build_workflow(pg_cred_id: str):
    return {
        "name": "Web_YouTube_Stats",
        "nodes": [
            {"parameters": {"path": "youtube-stats", "httpMethod": "GET",
                "responseMode": "responseNode", "options": {}},
             "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
             "position": [0, 0], "id": "yw", "name": "Webhook",
             "webhookId": str(uuid.uuid4())},
            {"parameters": {
                "operation": "executeQuery",
                "query": "SELECT video_id, title, category, views, likes, comments, url, "
                          "published_at, duration_seconds, fetched_at "
                          "FROM youtube_video_stats ORDER BY views DESC",
                "options": {}},
             "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
             "position": [220, 0], "id": "yq", "name": "Query",
             "credentials": {"postgres": {"id": pg_cred_id, "name": PG_CRED_NAME}}},
            {"parameters": {"jsCode": JS_AGGREGATE},
             "type": "n8n-nodes-base.code", "typeVersion": 2,
             "position": [440, 0], "id": "ya", "name": "Aggregate"},
            {"parameters": {"respondWith": "json",
                "responseBody": "={{ JSON.stringify($json) }}",
                "options": {"responseHeaders": {"entries": [
                    {"name": "Access-Control-Allow-Origin", "value": "*"}]}}},
             "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
             "position": [660, 0], "id": "yr", "name": "Respond"},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Query", "type": "main", "index": 0}]]},
            "Query": {"main": [[{"node": "Aggregate", "type": "main", "index": 0}]]},
            "Aggregate": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}

    # 1. Найти PG credential id
    with urllib.request.urlopen(urllib.request.Request(
        base + "/api/v1/credentials/schema/postgres", headers=h), timeout=15) as r:
        # этот endpoint только для схемы, реальные creds — через workflow:
        pass
    # Берём id из существующего workflow, который использует Postgres
    with urllib.request.urlopen(urllib.request.Request(
        base + "/api/v1/workflows?limit=200", headers=h), timeout=20) as r:
        all_wf = json.loads(r.read())["data"]
    pg_cred_id = None
    for w in all_wf:
        with urllib.request.urlopen(urllib.request.Request(
            base + f"/api/v1/workflows/{w['id']}", headers=h), timeout=15) as r:
            wf = json.loads(r.read())
        for n in wf.get("nodes", []):
            if n.get("type") == "n8n-nodes-base.postgres":
                creds = (n.get("credentials") or {}).get("postgres")
                if creds and creds.get("id"):
                    pg_cred_id = creds["id"]
                    break
        if pg_cred_id: break
    if not pg_cred_id:
        raise RuntimeError("Не нашёл postgres credentials в существующих workflow")
    print(f"PG credential id: {pg_cred_id}")

    # 2. Создать/обновить Web_YouTube_Stats
    body = build_workflow(pg_cred_id)
    found = next((w for w in all_wf if w["name"] == "Web_YouTube_Stats"), None)
    if found:
        req = urllib.request.Request(
            base + f"/api/v1/workflows/{found['id']}",
            data=json.dumps(body).encode(), method="PUT",
            headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            wid = found["id"]
        print(f"[OK] Updated {wid}")
    else:
        req = urllib.request.Request(
            base + "/api/v1/workflows",
            data=json.dumps(body).encode(), method="POST",
            headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            wid = json.loads(r.read())["id"]
        print(f"[OK] Created {wid}")

    try:
        urllib.request.urlopen(urllib.request.Request(
            base + f"/api/v1/workflows/{wid}/activate", method="POST",
            headers=h), timeout=15)
        print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400): raise

    print(f"\nGET {base}/webhook/youtube-stats")


if __name__ == "__main__":
    main()

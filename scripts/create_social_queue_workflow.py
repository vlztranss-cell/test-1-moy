"""
n8n workflow Web_Social_Queue:
- GET /webhook/social-queue        — JSON список постов из social_posts (для дашборда)
- POST /webhook/social-add         — добавить пост в очередь
- POST /webhook/social-mark        — обновить статус (после публикации Python-скриптом)

Сам автопостинг делается через CRON-задачу на VPS которая дёргает
youtube_uploader.py — n8n тут просто как удобный REST-фасад.
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

PG_CREDENTIAL = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

QUEUE_SQL = """SELECT json_agg(row_to_json(t) ORDER BY scheduled_at DESC) AS payload FROM (
    SELECT
        id, creative_file, title, scheduled_at,
        target_youtube, target_telegram, target_vk,
        youtube_status, youtube_url, youtube_video_id, youtube_posted_at,
        telegram_status, telegram_url, telegram_posted_at,
        vk_status, vk_url, vk_posted_at,
        created_at
    FROM social_posts
    ORDER BY
        CASE WHEN youtube_status = 'pending' OR telegram_status = 'pending' OR vk_status = 'pending'
             THEN 0 ELSE 1 END,
        scheduled_at DESC
    LIMIT 50
) t;"""

ADD_SQL = """INSERT INTO social_posts (
    creative_file, title, caption, hashtags, scheduled_at,
    target_youtube, target_telegram, target_vk
) VALUES (
    '{{ ($json.body.creative_file || '').replace(/'/g, "''") }}',
    '{{ ($json.body.title || '').replace(/'/g, "''") }}',
    '{{ ($json.body.caption || '').replace(/'/g, "''") }}',
    '{{ ($json.body.hashtags || '').replace(/'/g, "''") }}',
    COALESCE('{{ $json.body.scheduled_at }}'::timestamptz, NOW()),
    {{ $json.body.target_youtube !== false }},
    {{ $json.body.target_telegram !== false }},
    {{ $json.body.target_vk !== false }}
) RETURNING id, scheduled_at;"""


def build_workflow():
    queue_wh = {
        "parameters": {"path":"social-queue","httpMethod":"GET","responseMode":"responseNode","options":{}},
        "type":"n8n-nodes-base.webhook","typeVersion":2.1,"position":[0,0],
        "id":"social-queue-webhook","name":"queue webhook","webhookId":str(uuid.uuid4()),
    }
    add_wh = {
        "parameters": {"path":"social-add","httpMethod":"POST","responseMode":"responseNode","options":{}},
        "type":"n8n-nodes-base.webhook","typeVersion":2.1,"position":[0,220],
        "id":"social-add-webhook","name":"add webhook","webhookId":str(uuid.uuid4()),
    }
    return {
        "name": "Web_Social_Queue",
        "nodes": [
            queue_wh,
            {"parameters":{"operation":"executeQuery","query":QUEUE_SQL,"options":{}},
             "type":"n8n-nodes-base.postgres","typeVersion":2.5,"position":[220,0],
             "id":"queue-pg","name":"Queue SQL","credentials":{"postgres":PG_CREDENTIAL}},
            {"parameters":{"respondWith":"json","responseBody":"={{ $json.payload || [] }}","options":{}},
             "type":"n8n-nodes-base.respondToWebhook","typeVersion":1.5,"position":[440,0],
             "id":"queue-resp","name":"Queue Respond"},

            add_wh,
            {"parameters":{"operation":"executeQuery","query":ADD_SQL,"options":{}},
             "type":"n8n-nodes-base.postgres","typeVersion":2.5,"position":[220,220],
             "id":"add-pg","name":"Add SQL","credentials":{"postgres":PG_CREDENTIAL}},
            {"parameters":{"respondWith":"json","responseBody":"={{ JSON.stringify({ok:true, id: $json.id, scheduled_at: $json.scheduled_at}) }}","options":{}},
             "type":"n8n-nodes-base.respondToWebhook","typeVersion":1.5,"position":[440,220],
             "id":"add-resp","name":"Add Respond"},
        ],
        "connections": {
            "queue webhook": {"main":[[{"node":"Queue SQL","type":"main","index":0}]]},
            "Queue SQL": {"main":[[{"node":"Queue Respond","type":"main","index":0}]]},
            "add webhook": {"main":[[{"node":"Add SQL","type":"main","index":0}]]},
            "Add SQL": {"main":[[{"node":"Add Respond","type":"main","index":0}]]},
        },
        "settings": {"executionOrder":"v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    with urllib.request.urlopen(urllib.request.Request(base + "/api/v1/workflows", headers=h), timeout=30) as r:
        existing = json.loads(r.read())["data"]
    found = next((w for w in existing if w["name"] == "Web_Social_Queue"), None)
    body = build_workflow()
    if found:
        url = base + f"/api/v1/workflows/{found['id']}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT",
                                     headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        print(f"[OK] Обновлён Web_Social_Queue: id={found['id']}, версия={res.get('versionId','?')[:8]}")
        wid = found["id"]
    else:
        req = urllib.request.Request(base + "/api/v1/workflows", data=json.dumps(body).encode(), method="POST",
                                     headers={**h, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr); raise
        wid = res["id"]
        print(f"[OK] Создан Web_Social_Queue: id={wid}")
    try:
        with urllib.request.urlopen(urllib.request.Request(base + f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15) as r:
            print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code in (200, 400):
            print("  (уже активен)")
        else: raise
    print(f"\nGET {base}/webhook/social-queue — список")
    print(f"POST {base}/webhook/social-add — добавить (creative_file, title, caption, hashtags, scheduled_at)")


if __name__ == "__main__":
    main()

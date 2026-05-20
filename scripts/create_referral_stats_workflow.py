"""
Создаёт новый n8n workflow Web_Referral_Stats:
GET /webhook/referral-stats → возвращает JSON с метриками реф-программы +
топ-10 рефереров. Используется дашбордом.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
import urllib.error
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

PG_CREDENTIAL = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

SUMMARY_SQL = """SELECT json_build_object(
    'total_referrers',       (SELECT COUNT(*) FROM web_users WHERE ref_code IS NOT NULL),
    'users_invited',         (SELECT COUNT(*) FROM web_users WHERE ref_by  IS NOT NULL),
    'paid_referrals',        (SELECT COUNT(*) FROM referrals WHERE status='paid' AND platform='web'),
    'total_bonus_credits',   (SELECT COALESCE(SUM(bonus_credits_earned),0) FROM web_users),
    'top_referrers',         (
        SELECT json_agg(t.*) FROM (
            SELECT email, ref_code, bonus_credits_earned AS credits_earned, paid_referred_count
            FROM web_users
            WHERE bonus_credits_earned > 0
            ORDER BY bonus_credits_earned DESC
            LIMIT 10
        ) t
    ),
    'recent_referrals',      (
        SELECT json_agg(t.*) FROM (
            SELECT r.created_at, r.bonus_paid_credits,
                   (SELECT email FROM web_users WHERE id = r.web_user_id)        AS referrer_email,
                   (SELECT email FROM web_users WHERE id = r.friend_web_user_id) AS friend_email
            FROM referrals r
            WHERE r.platform = 'web' AND r.status = 'paid'
            ORDER BY r.created_at DESC
            LIMIT 20
        ) t
    )
) AS payload;"""


def build_workflow():
    return {
        "name": "Web_Referral_Stats",
        "nodes": [
            {
                "parameters": {
                    "path": "referral-stats",
                    "httpMethod": "GET",
                    "responseMode": "responseNode",
                    "options": {"allowedOrigins": "https://botisk.ru"},
                },
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2.1,
                "position": [0, 0],
                "id": "ref-stats-webhook-2026",
                "name": "Webhook",
                "webhookId": str(uuid.uuid4()),
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": SUMMARY_SQL,
                    "options": {},
                },
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.5,
                "position": [220, 0],
                "id": "ref-stats-pg-2026",
                "name": "Referral Stats SQL",
                "credentials": {"postgres": PG_CREDENTIAL},
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": "={{ $json.payload }}",
                    "options": {
                        "responseHeaders": {
                            "entries": [
                                {"name": "Access-Control-Allow-Origin", "value": "https://botisk.ru"},
                            ]
                        },
                    },
                },
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.5,
                "position": [440, 0],
                "id": "ref-stats-respond-2026",
                "name": "Respond",
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Referral Stats SQL", "type": "main", "index": 0}]]},
            "Referral Stats SQL": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}

    # Если уже существует — обновим PUT'ом по id; иначе создадим POST'ом
    with urllib.request.urlopen(urllib.request.Request(base + "/api/v1/workflows", headers=h), timeout=30) as r:
        existing = json.loads(r.read())["data"]
    found = next((w for w in existing if w["name"] == "Web_Referral_Stats"), None)

    body = build_workflow()
    if found:
        wid = found["id"]
        url = base + f"/api/v1/workflows/{wid}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT",
                                     headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        print(f"[OK] Обновлён существующий workflow Web_Referral_Stats: id={wid}")
    else:
        url = base + "/api/v1/workflows"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={**h, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
            raise
        wid = res["id"]
        print(f"[OK] Создан workflow Web_Referral_Stats: id={wid}")

    # Активируем
    act_url = base + f"/api/v1/workflows/{wid}/activate"
    try:
        with urllib.request.urlopen(urllib.request.Request(act_url, method="POST", headers=h), timeout=15) as r:
            print(f"[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code == 200 or e.code == 400:
            print(f"  (уже активен или конфликт — игнорируем)")
        else:
            raise

    print(f"\nWebhook URL: {env['N8N_URL'].rstrip('/')}/webhook/referral-stats")


if __name__ == "__main__":
    main()

"""
Создаёт n8n workflow Bot_Referral_API — helpers для бот-реферальной программы.

3 endpoint'а:

POST /webhook/bot-referral-save
    body: {tg_user_id, ref_code}
    → UPSERT user_state.ref_by — сохраняем кто пригласил бот-юзера
    (вызывается ботом при /start ref_<CODE>)

POST /webhook/bot-referral-link
    body: {tg_user_id, email}
    → ensure_web_user_for_bot(email, tg_user_id) → возвращает ref_code
    (вызывается ботом при команде /referral)

POST /webhook/bot-referral-bonus
    body: {order_id} (или {tg_user_id, tariff_code, generations_limit})
    → проверяем orders.ref_by, начисляем бонус рефереру в web_users.paid_credits,
      пишем запись в referrals (platform='bot'), помечаем bonus_paid=true
    (вызывается ботом после успешной оплаты, идемпотентно)
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

PG_CREDENTIAL = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

# ── /webhook/bot-referral-save: сохранить ref_by для бот-юзера ──────────────
SAVE_SQL = """INSERT INTO user_state (user_id, ref_by, ref_captured_at, updated_at)
VALUES ('{{$json.body.tg_user_id}}', '{{$json.body.ref_code}}', NOW(), NOW())
ON CONFLICT (user_id) DO UPDATE SET
    ref_by = COALESCE(user_state.ref_by, EXCLUDED.ref_by),
    ref_captured_at = COALESCE(user_state.ref_captured_at, EXCLUDED.ref_captured_at),
    updated_at = NOW()
RETURNING user_id, ref_by, ref_captured_at;"""

# ── /webhook/bot-get-ref-by: вернуть ref_by пользователя из user_state ──────
GET_REF_BY_SQL = """SELECT COALESCE(ref_by, '') AS ref_by
FROM user_state
WHERE user_id = '{{$json.query.user_id}}'
LIMIT 1;"""

# ── /webhook/bot-referral-link: дать боту ref_code этого юзера ──────────────
LINK_SQL = """SELECT
    ensure_web_user_for_bot(
        '{{$json.body.email}}',
        '{{$json.body.tg_user_id}}',
        '{{$json.body.tg_username || ''}}'
    ) AS ref_code,
    'https://t.me/VideoAI_24isk_bot?start=ref_' ||
        ensure_web_user_for_bot(
            '{{$json.body.email}}',
            '{{$json.body.tg_user_id}}',
            '{{$json.body.tg_username || ''}}'
        ) AS bot_link,
    'https://botisk.ru/?ref=' ||
        ensure_web_user_for_bot(
            '{{$json.body.email}}',
            '{{$json.body.tg_user_id}}',
            '{{$json.body.tg_username || ''}}'
        ) AS web_link,
    (SELECT bonus_credits_earned FROM web_users WHERE email = LOWER('{{$json.body.email}}')) AS bonus_earned,
    (SELECT paid_referred_count FROM web_users WHERE email = LOWER('{{$json.body.email}}')) AS paid_referred;"""

# ── /webhook/bot-referral-bonus: начисление при оплате в боте ───────────────
# Идемпотентно: бонус выдаётся только если orders.referral_bonus_paid = FALSE.
# Reward по тарифу: starter +10 / pro +30 / business +80 referrer,
#                   friend +3 / +5 / +10
BONUS_SQL = """WITH
target_order AS (
    SELECT id, user_telegram_id, username, tariff_code, generations_limit, ref_by
    FROM orders
    WHERE id = {{$json.body.order_id}}
      AND ref_by IS NOT NULL AND ref_by <> ''
      AND referral_bonus_paid = FALSE
      AND is_paid = 'yes'
),
tariff_calc AS (
    -- Бот-тарифы (pack_start, pack_pro, pack_business) и web-тарифы (starter, pro, business) — общий маппинг
    SELECT
        t.*,
        CASE
            WHEN t.tariff_code IN ('starter',  'pack_start')    THEN 10
            WHEN t.tariff_code IN ('pro',      'pack_pro')      THEN 30
            WHEN t.tariff_code IN ('business', 'pack_business') THEN 80
            ELSE 0
        END AS referrer_bonus,
        CASE
            WHEN t.tariff_code IN ('starter',  'pack_start')    THEN 3
            WHEN t.tariff_code IN ('pro',      'pack_pro')      THEN 5
            WHEN t.tariff_code IN ('business', 'pack_business') THEN 10
            ELSE 0
        END AS friend_bonus
    FROM target_order t
),
-- Если friend (платящий бот-юзер) ещё не в web_users — создаём placeholder
friend_upserted AS (
    INSERT INTO web_users (email, telegram_user_id, telegram_username, last_seen, ref_code)
    SELECT 'tg-' || t.user_telegram_id || '@bot.local',
           t.user_telegram_id,
           NULLIF(t.username, ''),
           NOW(),
           generate_ref_code()
    FROM target_order t
    WHERE t.user_telegram_id IS NOT NULL AND t.user_telegram_id <> ''
    ON CONFLICT (email) DO UPDATE SET
        telegram_user_id = COALESCE(web_users.telegram_user_id, EXCLUDED.telegram_user_id),
        ref_code = COALESCE(web_users.ref_code, EXCLUDED.ref_code),
        last_seen = NOW()
    RETURNING id, telegram_user_id, ref_code
),
referrer_paid AS (
    -- Match по ref_code (web-стиль, буквенный) ИЛИ по telegram_user_id (bot-стиль, numeric).
    -- Self-ref защита: referrer.tg_id <> friend.tg_id
    UPDATE web_users wu
    SET
        paid_credits = wu.paid_credits + tc.referrer_bonus,
        bonus_credits_earned = wu.bonus_credits_earned + tc.referrer_bonus,
        paid_referred_count = wu.paid_referred_count + 1
    FROM tariff_calc tc
    WHERE (wu.ref_code = tc.ref_by OR wu.telegram_user_id = tc.ref_by)
      AND tc.referrer_bonus > 0
      AND (wu.telegram_user_id IS NULL OR wu.telegram_user_id <> tc.user_telegram_id)
    RETURNING wu.id AS referrer_user_id, wu.email AS referrer_email, tc.referrer_bonus AS credited
),
mark_paid AS (
    UPDATE orders SET referral_bonus_paid = TRUE
    FROM target_order t WHERE orders.id = t.id
    RETURNING orders.id
),
log_referral AS (
    INSERT INTO referrals (
        referrer_user_id, referred_user_id, referred_username,
        platform, bonus_paid_credits, web_user_id, status, first_paid_at
    )
    SELECT
        rp.referrer_user_id::text,
        t.user_telegram_id,
        NULL,
        'bot',
        rp.credited,
        rp.referrer_user_id,
        'paid',
        NOW()
    FROM referrer_paid rp, target_order t
    ON CONFLICT (referred_user_id) DO NOTHING
    RETURNING id
)
SELECT
    (SELECT id FROM target_order) AS order_id,
    (SELECT ref_by FROM target_order) AS ref_by,
    (SELECT referrer_email FROM referrer_paid) AS referrer_email,
    (SELECT credited FROM referrer_paid) AS referrer_credited,
    (SELECT friend_bonus FROM tariff_calc) AS friend_bonus;"""


def w(path, http="POST"):
    return {
        "parameters": {"path": path, "httpMethod": http, "responseMode": "responseNode", "options": {}},
        "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
        "id": f"{path}-webhook-2026", "name": f"{path}-webhook",
        "webhookId": str(uuid.uuid4()),
    }


def pg(name, sql, x, y):
    return {
        "parameters": {"operation": "executeQuery", "query": sql, "options": {}},
        "type": "n8n-nodes-base.postgres", "typeVersion": 2.5,
        "position": [x, y], "id": f"{name}-pg-2026", "name": name,
        "credentials": {"postgres": PG_CREDENTIAL},
    }


def resp(name, x, y, body="={{ JSON.stringify($json) }}"):
    return {
        "parameters": {"respondWith": "json", "responseBody": body, "options": {}},
        "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
        "position": [x, y], "id": f"{name}-resp-2026", "name": name,
    }


def build_workflow():
    save_wh = w("bot-referral-save")
    save_wh["position"] = [0, 0]
    link_wh = w("bot-referral-link")
    link_wh["position"] = [0, 220]
    bonus_wh = w("bot-referral-bonus")
    bonus_wh["position"] = [0, 440]
    getref_wh = w("bot-get-ref-by", http="GET")
    getref_wh["position"] = [0, 660]

    return {
        "name": "Bot_Referral_API",
        "nodes": [
            save_wh,
            pg("Save Ref By", SAVE_SQL, 220, 0),
            resp("Save Respond", 440, 0),
            link_wh,
            pg("Get Ref Link", LINK_SQL, 220, 220),
            resp("Link Respond", 440, 220),
            bonus_wh,
            pg("Apply Bonus", BONUS_SQL, 220, 440),
            resp("Bonus Respond", 440, 440),
            getref_wh,
            pg("Lookup Ref By", GET_REF_BY_SQL, 220, 660),
            resp("Ref By Respond", 440, 660),
        ],
        "connections": {
            "bot-referral-save-webhook":  {"main": [[{"node": "Save Ref By", "type": "main", "index": 0}]]},
            "Save Ref By":                 {"main": [[{"node": "Save Respond", "type": "main", "index": 0}]]},
            "bot-referral-link-webhook":  {"main": [[{"node": "Get Ref Link", "type": "main", "index": 0}]]},
            "Get Ref Link":                {"main": [[{"node": "Link Respond", "type": "main", "index": 0}]]},
            "bot-referral-bonus-webhook": {"main": [[{"node": "Apply Bonus", "type": "main", "index": 0}]]},
            "Apply Bonus":                 {"main": [[{"node": "Bonus Respond", "type": "main", "index": 0}]]},
            "bot-get-ref-by-webhook":     {"main": [[{"node": "Lookup Ref By", "type": "main", "index": 0}]]},
            "Lookup Ref By":               {"main": [[{"node": "Ref By Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}
    with urllib.request.urlopen(urllib.request.Request(base + "/api/v1/workflows", headers=h), timeout=30) as r:
        existing = json.loads(r.read())["data"]
    found = next((w for w in existing if w["name"] == "Bot_Referral_API"), None)
    body = build_workflow()
    if found:
        url = base + f"/api/v1/workflows/{found['id']}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT", headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        wid = found["id"]
        print(f"[OK] Обновлён Bot_Referral_API: id={wid}, версия={res.get('versionId','?')[:8]}")
    else:
        req = urllib.request.Request(base + "/api/v1/workflows", data=json.dumps(body).encode(), method="POST", headers={**h, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr); raise
        wid = res["id"]
        print(f"[OK] Создан Bot_Referral_API: id={wid}")
    try:
        with urllib.request.urlopen(urllib.request.Request(base + f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15) as r:
            print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code in (200, 400):
            print("  (уже активен)")
        else:
            raise
    print(f"\n3 endpoint'а готовы:")
    print(f"  POST {base}/webhook/bot-referral-save   {{tg_user_id, ref_code}}")
    print(f"  POST {base}/webhook/bot-referral-link   {{tg_user_id, email, tg_username?}}")
    print(f"  POST {base}/webhook/bot-referral-bonus  {{order_id}}")


if __name__ == "__main__":
    main()

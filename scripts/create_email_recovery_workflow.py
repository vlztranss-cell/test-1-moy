"""
H1: n8n workflow Web_Email_Recovery — раз в час шлёт recovery-email тем,
кто 1+ часов назад делал free-генерацию и не купил.

Cron: каждый час
Запрос: web_orders WHERE created_at > 24h ago AND charge_type='free'
        AND email NOT IN (recovery_email_log)
        AND email NOT IN (paid web_users)
Шаблон: «Ваше видео готово! Попробуйте ещё одно — со скидкой» + промокод RECOVER99
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

env = load_env()

PG_CRED = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}
GMAIL_CRED = {"id": "ap4THiNHfGpAXmL6", "name": "Gmail 05/03/2026"}

SELECT_SQL = """
SELECT DISTINCT ON (wo.email)
    wo.id::text AS order_id,
    LOWER(wo.email) AS email,
    wo.status,
    wo.created_at::text AS created_at
FROM web_orders wo
WHERE wo.created_at > NOW() - INTERVAL '24 hours'
  AND wo.created_at < NOW() - INTERVAL '1 hour'
  AND wo.charge_type = 'free'
  AND wo.email IS NOT NULL
  AND wo.email <> ''
  AND wo.email NOT LIKE '%e2e_test%'
  AND wo.email NOT LIKE '%[e2e_test]%'
  AND wo.email NOT LIKE 'autotest_%'
  -- Не отправляли ранее
  AND LOWER(wo.email) NOT IN (SELECT email FROM recovery_email_log)
  -- Не купил уже
  AND NOT EXISTS (
      SELECT 1 FROM web_users wu
      WHERE wu.email = LOWER(wo.email) AND wu.paid_credits > 0
  )
ORDER BY wo.email, wo.created_at DESC
LIMIT 20;
""".strip()

EMAIL_SUBJECT = "Попробуйте ещё одно фото — со скидкой 99₽"
EMAIL_BODY_HTML = """
<div style="font-family: Inter, system-ui, sans-serif; max-width: 600px; margin: 0 auto; line-height: 1.6;">
  <h2 style="color: #7c5cfc;">Здравствуйте! 🎬</h2>
  <p>Вы недавно попробовали оживить фото на <a href="https://botisk.ru/" style="color: #7c5cfc;">botisk.ru</a> — спасибо за интерес к нашему сервису!</p>

  <p>Хотим напомнить про два важных момента:</p>

  <ul>
    <li><b>Бесплатное видео остаётся доступным</b> — если у вас в браузере остался ваш email, кредит не сгорает. Попробуйте другое фото (особенно хорошо работают чёткие портреты бабушек/дедушек, детские фото).</li>
    <li><b>Подарок к возвращению — промокод <code style="background:#f0f0f0;padding:2px 8px;border-radius:4px;">RECOVER99</code></b> даст вам видео всего за 99₽ без водяного знака. Идеально для подарка маме на день рождения или памятного видео для семейного архива.</li>
  </ul>

  <p style="margin-top: 24px;">
    <a href="https://botisk.ru/?utm_source=email&utm_medium=recovery&utm_campaign=free_followup" style="background: linear-gradient(135deg,#7c5cfc,#a855f7); color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; display: inline-block; font-weight: 700;">Попробовать ещё одно фото →</a>
  </p>

  <p style="color:#666; font-size: 13px; margin-top: 32px;">
    Если письмо вам не нужно — просто проигнорируйте его, мы больше не побеспокоим.<br>
    С уважением, команда VideoAI · <a href="https://botisk.ru/" style="color:#7c5cfc;">botisk.ru</a>
  </p>
</div>
""".strip()

INSERT_SQL = """
INSERT INTO recovery_email_log (email, order_id, promo_code, reason)
VALUES ('{{ $json.email }}', {{ $json.order_id }}, 'RECOVER99', 'free_followup_1h')
ON CONFLICT (email) DO NOTHING;
""".strip()


def build_workflow():
    return {
        "name": "Web_Email_Recovery",
        "nodes": [
            {
                "parameters": {
                    "rule": {"interval": [{"field": "hours"}]},
                },
                "type": "n8n-nodes-base.scheduleTrigger",
                "typeVersion": 1.2,
                "position": [0, 0],
                "id": "trig",
                "name": "Hourly",
            },
            {
                "parameters": {"operation": "executeQuery", "query": SELECT_SQL, "options": {}},
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.5,
                "position": [220, 0],
                "id": "sel",
                "name": "Select Candidates",
                "credentials": {"postgres": PG_CRED},
            },
            {
                "parameters": {
                    "sendTo": "={{ $json.email }}",
                    "subject": EMAIL_SUBJECT,
                    "emailType": "html",
                    "message": EMAIL_BODY_HTML,
                    "options": {},
                },
                "type": "n8n-nodes-base.gmail",
                "typeVersion": 2.1,
                "position": [440, 0],
                "id": "snd",
                "name": "Send Email",
                "credentials": {"gmailOAuth2": GMAIL_CRED},
            },
            {
                "parameters": {"operation": "executeQuery", "query": INSERT_SQL, "options": {}},
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.5,
                "position": [660, 0],
                "id": "log",
                "name": "Log Sent",
                "credentials": {"postgres": PG_CRED},
            },
        ],
        "connections": {
            "Hourly": {"main": [[{"node": "Select Candidates", "type": "main", "index": 0}]]},
            "Select Candidates": {"main": [[{"node": "Send Email", "type": "main", "index": 0}]]},
            "Send Email": {"main": [[{"node": "Log Sent", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    base = env["N8N_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}

    # Найти существующий
    with urllib.request.urlopen(urllib.request.Request(base + "/api/v1/workflows?limit=200", headers=h), timeout=20) as r:
        existing = json.loads(r.read())["data"]
    found = next((w for w in existing if w["name"] == "Web_Email_Recovery"), None)
    body = build_workflow()

    if found:
        req = urllib.request.Request(
            base + f"/api/v1/workflows/{found['id']}",
            data=json.dumps(body).encode(), method="PUT",
            headers={**h, "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=30)
        print(f"[OK] Updated {found['id']}")
        wid = found["id"]
    else:
        req = urllib.request.Request(
            base + "/api/v1/workflows",
            data=json.dumps(body).encode(), method="POST",
            headers={**h, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            wid = json.loads(r.read())["id"]
        print(f"[OK] Created {wid}")

    try:
        urllib.request.urlopen(urllib.request.Request(
            base + f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15)
        print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 400):
            raise


if __name__ == "__main__":
    main()

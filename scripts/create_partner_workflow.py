"""
Создаёт n8n workflow Web_Partner_Apply:
- POST /webhook/partner-apply — приём заявки на партнёрство
- GET  /webhook/partner-list — список заявок для дашборда
"""
from __future__ import annotations
import io, json, sys, urllib.request, urllib.error, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

PG_CREDENTIAL = {"id": "VHwQR0NCUn28HZPP", "name": "ssh root@72.56.96.64"}

VALIDATE_JS = r"""
const wh = $('Apply Webhook').first().json;
const b = wh.body || {};
const headers = wh.headers || {};
const rawIp = (headers['x-forwarded-for'] || headers['x-real-ip'] || '').toString().split(',')[0].trim();
const ipOk = /^(\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]+$/.test(rawIp);
const ip = ipOk ? rawIp : null;

const email = (b.email || '').toString().trim().toLowerCase();
const name = (b.name || '').toString().trim().slice(0, 200);
const telegram = (b.telegram || '').toString().trim().slice(0, 100);
const phone = (b.phone || '').toString().trim().slice(0, 30);
const trafficSource = (b.traffic_source || '').toString().trim().slice(0, 2000);
const volume = (b.monthly_volume_estimate || '').toString().trim().slice(0, 50);
const legalStatus = (b.legal_status || '').toString().trim().slice(0, 30);
const userAgent = (headers['user-agent'] || '').toString().slice(0, 300);

const emailValid = /^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(email);
if (!emailValid) return [{json: {ok: false, error: 'email_invalid'}}];
if (!name) return [{json: {ok: false, error: 'name_required'}}];
if (trafficSource.length < 10) return [{json: {ok: false, error: 'traffic_source_too_short'}}];

return [{json: {
    ok: true, email, name, telegram, phone,
    traffic_source: trafficSource,
    monthly_volume_estimate: volume,
    legal_status: legalStatus,
    ip, user_agent: userAgent
}}];
""".strip()

INSERT_SQL = """INSERT INTO partner_applications (
    email, name, telegram, phone, traffic_source,
    monthly_volume_estimate, legal_status, ip, user_agent
) VALUES (
    '{{$json.email}}',
    '{{ ($json.name || '').replace(/'/g, "''") }}',
    NULLIF('{{ ($json.telegram || '').replace(/'/g, "''") }}', ''),
    NULLIF('{{ ($json.phone || '').replace(/'/g, "''") }}', ''),
    '{{ ($json.traffic_source || '').replace(/'/g, "''") }}',
    NULLIF('{{$json.monthly_volume_estimate}}', ''),
    NULLIF('{{$json.legal_status}}', ''),
    {{$json.ip ? "'" + $json.ip + "'::inet" : 'NULL'}},
    NULLIF('{{ ($json.user_agent || '').replace(/'/g, "''") }}', '')
)
ON CONFLICT ON CONSTRAINT uniq_partner_applications_email DO UPDATE SET
    name = COALESCE(partner_applications.name, EXCLUDED.name),
    telegram = COALESCE(EXCLUDED.telegram, partner_applications.telegram),
    traffic_source = EXCLUDED.traffic_source,
    monthly_volume_estimate = EXCLUDED.monthly_volume_estimate,
    legal_status = EXCLUDED.legal_status,
    user_agent = EXCLUDED.user_agent,
    ip = EXCLUDED.ip
RETURNING id, status, created_at;"""

LIST_SQL = """SELECT json_agg(t.* ORDER BY t.created_at DESC) AS payload FROM (
    SELECT id, email, name, telegram, phone, traffic_source, monthly_volume_estimate,
           legal_status, status, ref_code, balance_rub, total_paid_out,
           payout_starter, payout_pro, payout_business,
           admin_note, created_at, approved_at, rejected_at
    FROM partner_applications
    ORDER BY
        CASE status WHEN 'pending' THEN 1 WHEN 'approved' THEN 2 ELSE 3 END,
        created_at DESC
    LIMIT 100
) t;"""


def build_workflow():
    return {
        "name": "Web_Partner_Apply",
        "nodes": [
            {"parameters":{"path":"partner-apply","httpMethod":"POST","responseMode":"responseNode","options":{"allowedOrigins":"https://botisk.ru"}},"type":"n8n-nodes-base.webhook","typeVersion":2.1,"position":[0,0],"id":"partner-apply-webhook-2026","name":"Apply Webhook","webhookId":str(uuid.uuid4())},
            {"parameters":{"jsCode":VALIDATE_JS},"type":"n8n-nodes-base.code","typeVersion":2,"position":[220,0],"id":"partner-validate-2026","name":"Validate"},
            {"parameters":{"conditions":{"options":{"caseSensitive":True,"leftValue":"","typeValidation":"loose"},"conditions":[{"leftValue":"={{$json.ok}}","rightValue":True,"operator":{"type":"boolean","operation":"true","singleValue":True}}],"combinator":"and"}},"type":"n8n-nodes-base.if","typeVersion":2,"position":[440,0],"id":"partner-validated-if-2026","name":"Valid?"},
            {"parameters":{"operation":"executeQuery","query":INSERT_SQL,"options":{}},"type":"n8n-nodes-base.postgres","typeVersion":2.5,"position":[660,0],"id":"partner-insert-2026","name":"Insert/Update Application","credentials":{"postgres":PG_CREDENTIAL}},
            {"parameters":{"respondWith":"json","responseBody":'={{ JSON.stringify({ok: true, application_id: $json.id, status: $json.status}) }}',"options":{"responseHeaders":{"entries":[{"name":"Access-Control-Allow-Origin","value":"https://botisk.ru"}]}}},"type":"n8n-nodes-base.respondToWebhook","typeVersion":1.5,"position":[880,-100],"id":"partner-respond-ok-2026","name":"Respond OK"},
            {"parameters":{"respondWith":"json","responseBody":'={{ JSON.stringify({ok: false, error: $json.error || "invalid_input"}) }}',"options":{"responseCode":400,"responseHeaders":{"entries":[{"name":"Access-Control-Allow-Origin","value":"https://botisk.ru"}]}}},"type":"n8n-nodes-base.respondToWebhook","typeVersion":1.5,"position":[660,200],"id":"partner-respond-400-2026","name":"Respond 400"},
            # List endpoint
            {"parameters":{"path":"partner-list","httpMethod":"GET","responseMode":"responseNode","options":{}},"type":"n8n-nodes-base.webhook","typeVersion":2.1,"position":[0,400],"id":"partner-list-webhook-2026","name":"List Webhook","webhookId":str(uuid.uuid4())},
            {"parameters":{"operation":"executeQuery","query":LIST_SQL,"options":{}},"type":"n8n-nodes-base.postgres","typeVersion":2.5,"position":[220,400],"id":"partner-list-sql-2026","name":"List SQL","credentials":{"postgres":PG_CREDENTIAL}},
            {"parameters":{"respondWith":"json","responseBody":"={{ $json.payload || [] }}","options":{}},"type":"n8n-nodes-base.respondToWebhook","typeVersion":1.5,"position":[440,400],"id":"partner-list-respond-2026","name":"List Respond"},
        ],
        "connections": {
            "Apply Webhook": {"main":[[{"node":"Validate","type":"main","index":0}]]},
            "Validate": {"main":[[{"node":"Valid?","type":"main","index":0}]]},
            "Valid?": {"main":[[{"node":"Insert/Update Application","type":"main","index":0}],[{"node":"Respond 400","type":"main","index":0}]]},
            "Insert/Update Application": {"main":[[{"node":"Respond OK","type":"main","index":0}]]},
            "List Webhook": {"main":[[{"node":"List SQL","type":"main","index":0}]]},
            "List SQL": {"main":[[{"node":"List Respond","type":"main","index":0}]]},
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
    found = next((w for w in existing if w["name"] == "Web_Partner_Apply"), None)
    body = build_workflow()
    if found:
        url = base + f"/api/v1/workflows/{found['id']}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT", headers={**h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        wid = found["id"]
        print(f"[OK] Обновлён Web_Partner_Apply: id={wid}, версия={res.get('versionId','?')[:8]}")
    else:
        req = urllib.request.Request(base + "/api/v1/workflows", data=json.dumps(body).encode(), method="POST", headers={**h, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr); raise
        wid = res["id"]
        print(f"[OK] Создан Web_Partner_Apply: id={wid}")
    try:
        with urllib.request.urlopen(urllib.request.Request(base + f"/api/v1/workflows/{wid}/activate", method="POST", headers=h), timeout=15) as r:
            print("[OK] Activated")
    except urllib.error.HTTPError as e:
        if e.code in (200, 400):
            print("  (уже активен)")
        else:
            raise
    print(f"\nApply webhook: {base}/webhook/partner-apply  (POST)")
    print(f"List webhook:  {base}/webhook/partner-list   (GET)")


if __name__ == "__main__":
    main()

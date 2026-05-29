#!/usr/bin/env python3
"""
YuKassa Reconciler — зеркало ВСЕХ платежей ЮKassa в БД = источник правды по выручке.

Зачем: is_paid в web_orders/orders врёт (683 фантома — отменённые помечены как
оплаченные, + 10 реальных потеряны из-за недолетевших callback'ов). Поэтому
выручку считаем НЕ по is_paid, а по этому зеркалу, синхронизированному с API.

Что делает:
1. Тянет ВСЕ платежи из API ЮKassa (с пагинацией).
2. Upsert в yukassa_payments (id PK) — статус, сумма, даты, метод.
3. Реальная выручка = SUM(amount_rub) WHERE status='succeeded'.
4. Печатает сверку с is_paid (фантомы/потери) — мониторинг качества данных.

Запуск на VPS (нужны YUKASSA_SHOP_ID/SECRET в /srv/.env):
    python3 /srv/creatives/yukassa_reconciler.py
Cron (ежедневно 03:00 UTC):
    0 3 * * *  /usr/bin/python3 /srv/creatives/yukassa_reconciler.py >> /var/log/yukassa_recon.log 2>&1
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env


def psql(sql: str) -> str:
    r = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-F|", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        print(f"  ⚠️ psql: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def fetch_all_payments(env: dict) -> list[dict]:
    auth = base64.b64encode(
        f"{env['YUKASSA_SHOP_ID']}:{env['YUKASSA_SECRET_KEY']}".encode()
    ).decode()
    hdr = {"Authorization": f"Basic {auth}"}
    items, cursor = [], None
    for _ in range(100):  # защита от бесконечного цикла
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        url = "https://api.yookassa.ru/v3/payments?" + urllib.parse.urlencode(params)
        d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30).read())
        items.extend(d.get("items", []))
        cursor = d.get("next_cursor")
        if not cursor:
            break
    return items


def esc(s) -> str:
    """Экранирование строки для SQL-литерала."""
    return str(s or "").replace("'", "''")


def main() -> None:
    env = load_env()
    psql("""
        CREATE TABLE IF NOT EXISTS yukassa_payments (
            id TEXT PRIMARY KEY,
            status TEXT,
            amount_rub NUMERIC,
            currency TEXT,
            paid BOOLEAN,
            refunded BOOLEAN,
            description TEXT,
            payment_method TEXT,
            created_at TIMESTAMPTZ,
            captured_at TIMESTAMPTZ,
            synced_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    payments = fetch_all_payments(env)
    if not payments:
        print("ЮKassa вернула 0 платежей — проверь ключи."); return

    rows = []
    for p in payments:
        amt = p.get("amount", {}).get("value", 0)
        cur = p.get("amount", {}).get("currency", "RUB")
        method = (p.get("payment_method") or {}).get("type", "")
        ca = f"'{p['created_at']}'" if p.get("created_at") else "NULL"
        cp = f"'{p['captured_at']}'" if p.get("captured_at") else "NULL"
        paid = str(p.get("paid", False)).lower()
        refunded = str(p.get("refunded", False)).lower()
        rows.append(
            f"('{esc(p.get('id'))}','{esc(p.get('status'))}',{amt},'{esc(cur)}',"
            f"{paid},{refunded},'{esc(p.get('description'))}','{esc(method)}',{ca},{cp},NOW())"
        )
    psql(
        "INSERT INTO yukassa_payments "
        "(id,status,amount_rub,currency,paid,refunded,description,payment_method,created_at,captured_at,synced_at) "
        "VALUES " + ",".join(rows) +
        " ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, amount_rub=EXCLUDED.amount_rub, "
        "paid=EXCLUDED.paid, refunded=EXCLUDED.refunded, captured_at=EXCLUDED.captured_at, synced_at=NOW();"
    )

    # --- Отчёт + сверка качества данных ---
    rev = psql("SELECT COUNT(*), COALESCE(SUM(amount_rub),0) FROM yukassa_payments WHERE status='succeeded';")
    by_month = psql("""
        SELECT to_char(COALESCE(captured_at,created_at) AT TIME ZONE 'Europe/Moscow','YYYY-MM') m,
               COUNT(*), SUM(amount_rub)
        FROM yukassa_payments WHERE status='succeeded' GROUP BY 1 ORDER BY 1;
    """)
    # Фантомы: is_paid в БД, но НЕ succeeded в зеркале
    phantom = psql("""
        WITH paid_db AS (
            SELECT payment_id FROM web_orders WHERE is_paid IN ('t','true','yes','1') AND payment_id<>''
            UNION ALL
            SELECT payment_id FROM orders WHERE is_paid IN ('t','true','yes','1') AND payment_id<>''
        )
        SELECT COUNT(*) FROM paid_db p
        WHERE NOT EXISTS (SELECT 1 FROM yukassa_payments y WHERE y.id=p.payment_id AND y.status='succeeded');
    """)
    # Потери: succeeded в зеркале, но нет ни в web_orders, ни в orders
    missing = psql("""
        SELECT COUNT(*) FROM yukassa_payments y
        WHERE y.status='succeeded'
          AND NOT EXISTS (SELECT 1 FROM web_orders w WHERE w.payment_id=y.id)
          AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.payment_id=y.id);
    """)
    print(f"Синхронизировано платежей: {len(payments)}")
    print(f"💰 РЕАЛЬНАЯ ВЫРУЧКА (succeeded): {rev}")
    print("По месяцам (МСК):")
    print(by_month or "  (пусто)")
    print(f"🔎 Качество данных: фантомов в is_paid={phantom} | потерянных оплат={missing}")


if __name__ == "__main__":
    main()

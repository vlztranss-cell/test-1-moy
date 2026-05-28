#!/usr/bin/env python3
"""
PiAPI Spend Logger — точный расход PiAPI из кошелька аккаунта (не оценка!).

Раньше COGS оценивали по числу генераций × прайс. Теперь берём ФАКТ из
PiAPI account/info → wallet.point_used. Курс выведен из реальной задачи:
  Kling 2.5 std 5с = $0.16 = 2 000 000 поинтов  →  1 USD = 12 500 000 поинтов.

Снимок пишется в piapi_spend_snapshots на каждый запуск → дельта между
снимками = расход за период (видно дневной burn rate и когда нужен топап).

Запуск на VPS:
    python3 /srv/creatives/piapi_spend_logger.py
Cron (ежедневно 02:10 UTC, после cogs_logger):
    10 2 * * *  /usr/bin/python3 /srv/creatives/piapi_spend_logger.py >> /var/log/piapi_spend.log 2>&1
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

# Курс: 2 000 000 поинтов = $0.16 (Kling 5с std) → 12 500 000 поинтов за $1.
POINTS_PER_USD = 12_500_000


def psql(sql: str) -> str:
    r = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        print(f"  ⚠️ psql: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def fetch_wallet() -> dict:
    env = load_env()
    req = urllib.request.Request(
        "https://api.piapi.ai/account/info",
        headers={"x-api-key": env["PIAPI_KEY"], "User-Agent": "VideoAI-SpendLogger/1.0"},
    )
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return (data.get("data") or {}).get("wallet", {})


def main() -> None:
    psql("""
        CREATE TABLE IF NOT EXISTS piapi_spend_snapshots (
            id BIGSERIAL PRIMARY KEY,
            captured_at TIMESTAMPTZ DEFAULT NOW(),
            point_used BIGINT,
            point_remain BIGINT,
            point_frozen BIGINT,
            llm_used BIGINT,
            spent_usd NUMERIC,
            remain_usd NUMERIC
        );
    """)

    w = fetch_wallet()
    point_used = int(w.get("point_used", 0))
    point_remain = int(w.get("point_remain", 0))
    point_frozen = int(w.get("point_frozen", 0))
    llm_used = int(w.get("llm_used", 0))

    spent_usd = round(point_used / POINTS_PER_USD, 2)
    remain_usd = round(point_remain / POINTS_PER_USD, 2)

    psql(
        "INSERT INTO piapi_spend_snapshots "
        "(point_used, point_remain, point_frozen, llm_used, spent_usd, remain_usd) "
        f"VALUES ({point_used}, {point_remain}, {point_frozen}, {llm_used}, {spent_usd}, {remain_usd});"
    )

    # Дельта с предыдущим снимком = расход за период
    prev = psql("SELECT spent_usd, captured_at::date FROM piapi_spend_snapshots "
                "ORDER BY id DESC OFFSET 1 LIMIT 1;")
    delta_line = ""
    if prev and "|" in prev:
        prev_usd, prev_date = prev.split("|")
        delta = round(spent_usd - float(prev_usd), 2)
        delta_line = f"  Δ с {prev_date}: +${delta}"

    print(f"PiAPI spend: ВСЕГО потрачено ${spent_usd} | остаток ${remain_usd} "
          f"(point_used={point_used:,}){delta_line}")
    if remain_usd < 5:
        print(f"  ⚠️ ОСТАТОК НИЗКИЙ (${remain_usd}) — нужен топап PiAPI, иначе генерация встанет.")


if __name__ == "__main__":
    main()

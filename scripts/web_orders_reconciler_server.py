#!/usr/bin/env python3
"""
Web Orders Reconciler (СЕРВЕРНАЯ версия — запускается НА VPS).

Закрывает web_orders, которые n8n не закрывает (в веб-воркфлоу нет write-back):
опрашивает PiAPI по piapi_task_id и проставляет фактический статус.
  completed -> status='done', result_video_url=<url>, completed_at=now()
  failed    -> status='failed', error_message=<piapi error>

Отличия от локальной версии: psql через subprocess (sudo -u postgres), ретрай с
backoff на rate-limit PiAPI, пауза между запросами. Никакого SSH.

    python3 web_orders_reconciler_server.py            # DRY-RUN
    python3 web_orders_reconciler_server.py --apply     # применить

Cron (постоянный фикс), каждые 5 мин:
    */5 * * * *  /usr/bin/python3 /srv/creatives/web_orders_reconciler_server.py --apply >> /var/log/web_reconciler.log 2>&1
"""
from __future__ import annotations
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

ENV = load_env()
APPLY = "--apply" in sys.argv


def psql(sql: str) -> str:
    r = subprocess.run(["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print("  PSQL ERR:", r.stderr.strip()[:200])
    return r.stdout.strip()


def piapi_status(tid: str, retries: int = 1) -> dict:
    # retries=1: rate-limit'нутые остаются на следующий cron-цикл (PiAPI к тому времени отпускает).
    last = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"https://api.piapi.ai/api/v1/task/{tid}",
                headers={"x-api-key": ENV["PIAPI_KEY"], "User-Agent": "WebReconciler/1.0"})
            d = (json.loads(urllib.request.urlopen(req, timeout=12).read()).get("data") or {})
            st = (d.get("status") or "unknown").lower()
            url = None
            if st == "completed":
                o = d.get("output") or {}
                works = o.get("works") or []
                if works and works[0]:
                    v = works[0].get("video") or {}
                    url = v.get("resource_without_watermark") or v.get("resource") or v.get("url") or o.get("video_url")
                else:
                    url = o.get("video_url") or o.get("video")
            err = ""
            if st == "failed":
                e = d.get("error") or {}
                err = (e.get("raw_message") or e.get("message") or "piapi failed")[:200]
            return {"status": st, "url": url, "error": err}
        except urllib.error.HTTPError as e:
            # 400/404 с пустой задачей = PiAPI её уже удалил (старые заказы) → не вернуть
            if e.code in (400, 404):
                return {"status": "_gone", "url": None, "error": f"HTTP {e.code} task purged"}
            last = f"HTTP {e.code}"
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last = str(e)[:120]
            time.sleep(1.5 * (attempt + 1))  # backoff на rate-limit
    return {"status": "_apierr", "url": None, "error": last}


def esc(s: str) -> str:
    return (s or "").replace("'", "''")


def main() -> None:
    print(f"=== Reconciler ({'APPLY' if APPLY else 'DRY-RUN'}) {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    rows = psql("SELECT id||'|'||piapi_task_id||'|'||coalesce(charge_type,'free') "
                "FROM web_orders WHERE status='processing' AND piapi_task_id IS NOT NULL AND piapi_task_id<>'' "
                "ORDER BY id DESC LIMIT 25;")  # маленькая порция — бережём token-bucket PiAPI, остаток добьют след. cron-циклы
    items = [r for r in rows.splitlines() if r.strip()]
    print(f"processing к проверке: {len(items)}")

    to_done, to_failed, to_expired = [], [], []
    still = apierr = nourl = 0
    for i, line in enumerate(items, 1):
        oid, tid, charge = (line.split("|") + ["", "", "free"])[:3]
        r = piapi_status(tid)
        st = r["status"]
        if st == "completed":
            (to_done.append((oid, r["url"], charge)) if r["url"] else None)
            if not r["url"]:
                nourl += 1
        elif st == "failed":
            to_failed.append((oid, r["error"], charge))
        elif st == "_gone":
            to_expired.append((oid, r["error"], charge))  # задача удалена в PiAPI — не вернуть
        elif st == "_apierr":
            apierr += 1
        else:
            still += 1
        time.sleep(2.0)  # бережём rate-limit PiAPI (token-bucket)
        if i % 50 == 0:
            print(f"  ...{i}/{len(items)}  done={len(to_done)} failed={len(to_failed)} expired={len(to_expired)} apierr={apierr}")

    print(f"\nИТОГ: done={len(to_done)} (paid {sum(1 for x in to_done if x[2]!='free')}) | "
          f"failed={len(to_failed)} (paid {sum(1 for x in to_failed if x[2]!='free')}) | "
          f"expired={len(to_expired)} | nourl={nourl} | still={still} | apierr={apierr}")

    if not APPLY:
        print("[DRY-RUN] без записи")
        return

    def chunks(lst, n=80):
        for k in range(0, len(lst), n):
            yield lst[k:k + n]

    n = 0
    for batch in chunks(to_done):
        vals = ",".join(f"({oid},'{esc(url)}')" for oid, url, _ in batch)
        psql(f"UPDATE web_orders AS w SET status='done', result_video_url=v.url, completed_at=now() "
             f"FROM (VALUES {vals}) AS v(id,url) WHERE w.id=v.id AND w.status='processing';")
        n += len(batch)
    print(f"применено done: {n}")

    m = 0
    for batch in chunks(to_failed):
        vals = ",".join(f"({oid},'{esc(err)}')" for oid, err, _ in batch)
        psql(f"UPDATE web_orders AS w SET status='failed', error_message=v.err "
             f"FROM (VALUES {vals}) AS v(id,err) WHERE w.id=v.id AND w.status='processing';")
        m += len(batch)
    print(f"применено failed: {m}")

    k = 0
    for batch in chunks(to_expired):
        vals = ",".join(f"({oid},'{esc(err)}')" for oid, err, _ in batch)
        psql(f"UPDATE web_orders AS w SET status='expired', error_message=v.err "
             f"FROM (VALUES {vals}) AS v(id,err) WHERE w.id=v.id AND w.status='processing';")
        k += len(batch)
    print(f"применено expired: {k}")
    print("ГОТОВО")


if __name__ == "__main__":
    main()

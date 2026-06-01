#!/usr/bin/env python3
"""
Web Orders Reconciler — закрывает web_orders, которые n8n не закрывает.

Веб-воркфлоу (Web_Photo2Video_MVP) отдаёт готовое видео в браузер, но НЕ пишет
результат обратно в БД → заказы навсегда висят в status='processing'. Этот скрипт
опрашивает PiAPI по piapi_task_id и проставляет фактический статус:
  completed -> status='done', result_video_url=<url>, completed_at=now()
  failed    -> status='failed', error_message=<piapi error>
  прочее    -> не трогаем (ещё генерится / неизвестно)

Запуск:
    py -3 scripts/web_orders_reconciler.py            # DRY-RUN (ничего не пишет)
    py -3 scripts/web_orders_reconciler.py --apply    # применить

Cron (на VPS, каждые 5 мин) — постоянный фикс:
    */5 * * * *  /usr/bin/python3 /srv/creatives/web_orders_reconciler.py --apply >> /var/log/web_reconciler.log 2>&1
"""
from __future__ import annotations
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from ssh import psql

ENV = load_env()
APPLY = "--apply" in sys.argv


def q(sql: str) -> str:
    out, err = psql(sql)
    if err.strip():
        print("  PSQL ERR:", err.strip()[:200])
    return out.strip()


def piapi_status(tid: str) -> dict:
    """Вернуть {status, url, error} по задаче PiAPI. Логика url — как в ноде Parse Status."""
    try:
        req = urllib.request.Request(
            f"https://api.piapi.ai/api/v1/task/{tid}",
            headers={"x-api-key": ENV["PIAPI_KEY"], "User-Agent": "WebReconciler/1.0"})
        d = (json.loads(urllib.request.urlopen(req, timeout=20).read()).get("data") or {})
    except Exception as e:
        return {"status": "_apierr", "url": None, "error": str(e)[:120]}
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


def sql_escape(s: str) -> str:
    return (s or "").replace("'", "''")


def main() -> None:
    print(f"=== Web Orders Reconciler ({'APPLY' if APPLY else 'DRY-RUN'}) ===")
    rows = q("SELECT id||'|'||piapi_task_id||'|'||coalesce(charge_type,'free') "
             "FROM web_orders WHERE status='processing' AND piapi_task_id IS NOT NULL AND piapi_task_id<>'' "
             "ORDER BY id;")
    items = [r for r in rows.splitlines() if r.strip()]
    print(f"К проверке: {len(items)} заказов в processing\n")

    to_done, to_failed = [], []          # (id, url|err, charge)
    still, apierr, nourl = 0, 0, 0
    for i, line in enumerate(items, 1):
        oid, tid, charge = (line.split("|") + ["", "", "free"])[:3]
        r = piapi_status(tid)
        st = r["status"]
        if st == "completed":
            if r["url"]:
                to_done.append((oid, r["url"], charge))
            else:
                nourl += 1  # completed, но url не достали — не трогаем, разберём
        elif st == "failed":
            to_failed.append((oid, r["error"], charge))
        elif st == "_apierr":
            apierr += 1
        else:
            still += 1
        if i % 50 == 0:
            print(f"  ...{i}/{len(items)}")

    done_free = sum(1 for _ in to_done if _[2] == "free")
    done_paid = sum(1 for x in to_done if x[2] != "free")
    fail_paid = sum(1 for x in to_failed if x[2] != "free")
    print("\n=== ИТОГ АНАЛИЗА ===")
    print(f"  -> done   : {len(to_done)}  (free {done_free} / paid {done_paid})")
    print(f"  -> failed : {len(to_failed)}  (из них платных {fail_paid} — БЕЗ авторефанда, на ручной разбор)")
    print(f"  completed без url: {nourl}")
    print(f"  ещё генерится/unknown: {still}")
    print(f"  ошибка PiAPI API: {apierr}")

    if to_failed:
        sample = ", ".join(f"#{x[0]}" for x in to_failed[:10])
        print(f"  failed sample: {sample}")
    if any(x[2] != 'free' for x in to_failed):
        print("  ⚠️ ПЛАТНЫЕ с failed: " + ", ".join(f"#{x[0]}" for x in to_failed if x[2] != 'free'))

    if not APPLY:
        print("\n[DRY-RUN] записи в БД не делались. Для применения: --apply")
        return

    # --- APPLY: батчами по 100 ---
    def chunks(lst, n=100):
        for k in range(0, len(lst), n):
            yield lst[k:k + n]

    upd_done = 0
    for batch in chunks(to_done):
        vals = ",".join(f"({oid},'{sql_escape(url)}')" for oid, url, _ in batch)
        sql = (f"UPDATE web_orders AS w SET status='done', result_video_url=v.url, "
               f"completed_at=now() FROM (VALUES {vals}) AS v(id,url) "
               f"WHERE w.id=v.id AND w.status='processing';")
        q(sql)
        upd_done += len(batch)
    print(f"  применено done: {upd_done}")

    upd_fail = 0
    for batch in chunks(to_failed):
        vals = ",".join(f"({oid},'{sql_escape(err)}')" for oid, err, _ in batch)
        sql = (f"UPDATE web_orders AS w SET status='failed', error_message=v.err "
               f"FROM (VALUES {vals}) AS v(id,err) WHERE w.id=v.id AND w.status='processing';")
        q(sql)
        upd_fail += len(batch)
    print(f"  применено failed: {upd_fail}")
    print("\n=== ГОТОВО ===")


if __name__ == "__main__":
    main()

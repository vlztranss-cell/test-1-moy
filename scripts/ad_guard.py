#!/usr/bin/env python3
"""
Ad Guard — самозащита рекламного бюджета (self-healing).

Каждые ~20 минут проверяет здоровье генерации. Если генерация реально легла
(высокая доля фейлов при объёме ИЛИ баланс PiAPI почти ноль) — САМ ставит
кампании Я.Директа на паузу, чтобы не лить бюджет в поломку. Когда генерация
восстановилась — САМ снимает с паузы (но только те, что ставил сам: ручную
паузу пользователя не трогает, маркер в .ad_guard_state).

Запуск на VPS:
    python3 /srv/creatives/ad_guard.py
Cron (каждые 20 мин, off-minute):
    13,33,53 * * * *  /usr/bin/python3 /srv/creatives/ad_guard.py >> /var/log/ad_guard.log 2>&1

Требует в .env: YANDEX_OAUTH_TOKEN, PIAPI_KEY (+ опц. бот-токен для уведомления).
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

ENV = load_env()
CAMPAIGNS = [710122418, 710122420, 710122422, 710122424]
STATE_FILE = Path("/srv/creatives/.ad_guard_state")
PPU = 12_500_000

# Пороги (консервативные, чтобы не дёргать рекламу зря)
OUTAGE_MIN_STARTS = 6      # минимум стартов за 2ч, чтобы судить
OUTAGE_FAILRATE = 0.7      # >=70% фейлов = системный сбой
OUTAGE_BALANCE_USD = 1.5   # баланс ниже = вот-вот freeze
RECOVER_MIN_STARTS = 3
RECOVER_FAILRATE = 0.34    # <=34% фейлов = норма


def out(s: str) -> None:
    sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))


def psql(sql: str) -> str:
    r = subprocess.run(["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-F", "|", "-c", sql],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def gen_health() -> tuple[int, int, float]:
    """Старты и фейлы за последние 2 часа → (starts, fails, failrate)."""
    row = psql("SELECT COUNT(*), COUNT(*) FILTER (WHERE status='failed') "
               "FROM web_orders WHERE created_at >= NOW() - INTERVAL '2 hours';")
    starts, fails = (row.split("|") + ["0", "0"])[:2]
    starts, fails = int(starts or 0), int(fails or 0)
    return starts, fails, (fails / starts if starts else 0.0)


def piapi_balance() -> float:
    try:
        req = urllib.request.Request("https://api.piapi.ai/account/info",
            headers={"x-api-key": ENV["PIAPI_KEY"], "User-Agent": "AdGuard/1.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read()).get("data", {})
        return round(float(d.get("equivalent_in_usd", 0) or 0), 2)
    except Exception:
        return 999.0  # при ошибке API не трогаем рекламу из-за баланса


def direct(method: str, params: dict) -> dict:
    body = json.dumps({"method": method, "params": params}).encode()
    req = urllib.request.Request("https://api.direct.yandex.com/json/v5/campaigns", data=body,
        headers={"Authorization": f"Bearer {ENV['YANDEX_OAUTH_TOKEN']}", "Accept-Language": "ru",
                 "Content-Type": "application/json; charset=utf-8"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:200]}


def campaigns_on() -> int:
    r = direct("get", {"SelectionCriteria": {"Ids": CAMPAIGNS}, "FieldNames": ["Id", "State"]})
    return sum(1 for c in r.get("result", {}).get("Campaigns", []) if c.get("State") == "ON")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"paused_by_guard": False}


def save_state(d: dict) -> None:
    STATE_FILE.write_text(json.dumps(d))


def notify(text: str) -> None:
    """Best-effort уведомление админу в Telegram (если есть токен/чат в .env)."""
    tok = next((ENV[k] for k in ENV if "BOT_TOKEN" in k.upper() and ENV[k]), None)
    chat = next((ENV[k] for k in ENV if "ADMIN" in k.upper() and k.upper().endswith("ID") and ENV[k]), None)
    if not tok or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=15)
    except Exception:
        pass


def main() -> None:
    starts, fails, rate = gen_health()
    bal = piapi_balance()
    state = load_state()
    on = campaigns_on()
    out(f"health: старты(2ч)={starts} фейлы={fails} доля={round(rate*100)}% | баланс=${bal} | "
        f"кампаний ON={on} | guard_paused={state.get('paused_by_guard')}")

    outage = (starts >= OUTAGE_MIN_STARTS and rate >= OUTAGE_FAILRATE) or (bal < OUTAGE_BALANCE_USD)
    recovered = bal >= OUTAGE_BALANCE_USD and (starts >= RECOVER_MIN_STARTS and rate <= RECOVER_FAILRATE)

    if outage and on > 0 and not state.get("paused_by_guard"):
        r = direct("suspend", {"SelectionCriteria": {"Ids": CAMPAIGNS}})
        ok = "error" not in r
        save_state({"paused_by_guard": True})
        reason = f"баланс ${bal}" if bal < OUTAGE_BALANCE_USD else f"фейлы {round(rate*100)}% ({fails}/{starts})"
        msg = f"AD GUARD: реклама ПРИОСТАНОВЛЕНА — сбой генерации ({reason}). Бюджет не жжём."
        out("  -> " + msg + (" [ok]" if ok else f" [err {r.get('error')}]"))
        notify("⏸ " + msg)

    elif recovered and state.get("paused_by_guard"):
        r = direct("resume", {"SelectionCriteria": {"Ids": CAMPAIGNS}})
        ok = "error" not in r
        save_state({"paused_by_guard": False})
        msg = f"AD GUARD: реклама ВОЗОБНОВЛЕНА — генерация восстановилась (фейлы {round(rate*100)}%, баланс ${bal})."
        out("  -> " + msg + (" [ok]" if ok else f" [err {r.get('error')}]"))
        notify("▶️ " + msg)
    else:
        out("  -> действий не требуется")


if __name__ == "__main__":
    import urllib.parse  # для notify
    main()

#!/usr/bin/env python3
"""
Funnel Doctor — авто-разбор воронки: где провал и ПОЧЕМУ.

Тянет воронку из Метрики (визиты → загрузка фото → генерация → оплата) за вчера
и базу за предыдущие 7 дней. Находит шаг с наибольшим относительным провалом
против базы и выдаёт вердикт о причине. Выручку берёт из yukassa_payments (правда).

Каждый запуск пишет снимок в funnel_health (для истории и дашборда).

Запуск на VPS (нужны YANDEX_OAUTH_TOKEN, YANDEX_METRIKA_COUNTER_ID, YANDEX_GOAL_* в /srv/creatives/.env):
    python3 /srv/creatives/funnel_doctor.py
Cron (ежедневно 06:00 UTC = 09:00 МСК, после сбора суток):
    0 6 * * *  /usr/bin/python3 /srv/creatives/funnel_doctor.py >> /var/log/funnel_doctor.log 2>&1
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

env = load_env()
TOKEN = env["YANDEX_OAUTH_TOKEN"]
COUNTER = env["YANDEX_METRIKA_COUNTER_ID"]
GOALS = {
    "PHOTO_UPLOADED": env["YANDEX_GOAL_PHOTO_UPLOADED"],
    "GENERATION_STARTED": env["YANDEX_GOAL_GENERATION_STARTED"],
    "GEN_COMPLETED": env["YANDEX_GOAL_FREE_GEN_COMPLETED"],   # free доминирует
    "PAYMENT_OPEN": env["YANDEX_GOAL_PAYMENT_OPEN"],
    "PAYMENT_REDIRECT": env["YANDEX_GOAL_PAYMENT_REDIRECT"],
    "PAYMENT_SUCCESS": env["YANDEX_GOAL_PAYMENT_SUCCESS"],
}

# Шаги воронки: (ключ, числитель, знаменатель, вердикт при провале)
STAGES = [
    ("визит→загрузка",   "PHOTO_UPLOADED",    "visits",            "Трафик/лендинг: заходят, но не грузят фото (нерелевантный трафик или непонятный первый экран)."),
    ("загрузка→старт",   "GENERATION_STARTED","PHOTO_UPLOADED",    "Барьер перед генерацией: грузят фото, но не запускают (email-гейт/непонятная кнопка)."),
    ("старт→готово",     "GEN_COMPLETED",     "GENERATION_STARTED","Генерация падает (PiAPI/модерация фото/таймауты). Проверь баланс PiAPI и ошибки."),
    ("готово→оплата",    "PAYMENT_OPEN",      "GEN_COMPLETED",     "Не доводим до оплаты: видео получили, но пейволл не цепляет (gating/оффер после wow)."),
    ("оплата→редирект",  "PAYMENT_REDIRECT",  "PAYMENT_OPEN",      "Оффер/цена: открыли оплату, но не пошли платить (дорого/нет доверия/выбор тарифа)."),
    ("редирект→успех",   "PAYMENT_SUCCESS",   "PAYMENT_REDIRECT",  "🚨 ОПЛАТА ЛОМАЕТСЯ на стороне ЮKassa/возврата (тех.сбой платежа!). Проверь ключ/коллбэк СРОЧНО."),
]


def metrika(date1: str, date2: str) -> dict:
    metrics = ["ym:s:visits"] + [f"ym:s:goal{g}reaches" for g in GOALS.values()]
    params = {"ids": COUNTER, "date1": date1, "date2": date2,
              "metrics": ",".join(metrics), "accuracy": "full"}
    url = "https://api-metrika.yandex.net/stat/v1/data?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"OAuth {TOKEN}"})
    totals = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("totals", [])
    keys = ["visits"] + list(GOALS.keys())
    return dict(zip(keys, totals))


def psql(sql: str) -> str:
    r = subprocess.run(["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-c", sql],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def ratio(num, den):
    return (num / den) if den else 0.0


def main() -> None:
    yest = date.today() - timedelta(days=1)
    base_from = yest - timedelta(days=7)
    base_to = yest - timedelta(days=1)

    y = metrika(yest.isoformat(), yest.isoformat())                 # вчера
    b = metrika(base_from.isoformat(), base_to.isoformat())          # база (7 дней)
    b_days = 7

    # Выручка из ЮKassa-зеркала
    rev_yest = psql(f"SELECT COALESCE(SUM(amount_rub),0)::int FROM yukassa_payments "
                    f"WHERE status='succeeded' AND (COALESCE(captured_at,created_at) AT TIME ZONE 'Europe/Moscow')::date='{yest}'")

    print(f"=== Funnel Doctor — {yest} (база {base_from}…{base_to}) ===")
    print(f"Визиты вчера: {y['visits']} | выручка вчера: {rev_yest} ₽")

    findings = []
    for label, num_k, den_k, verdict in STAGES:
        r_y = ratio(y[num_k], y[den_k])
        r_b = ratio(b[num_k], b[den_k])  # суммарный за 7д = средняя конверсия
        drop = (r_b - r_y) / r_b if r_b > 0 else 0.0
        flag = "❗" if (drop >= 0.4 and y[den_k] >= 5) else "  "
        print(f"  {flag} {label}: вчера {r_y*100:.1f}% vs база {r_b*100:.1f}% "
              f"({y[num_k]}/{y[den_k]})" + (f"  ↓{drop*100:.0f}%" if drop > 0 else ""))
        if drop >= 0.4 and y[den_k] >= 5:
            findings.append((drop, label, verdict))

    findings.sort(reverse=True)
    if findings:
        drop, label, verdict = findings[0]
        diagnosis = f"СЛАБОЕ ЗВЕНО: «{label}» (провал {drop*100:.0f}% против базы). {verdict}"
    elif y["visits"] < (b["visits"] / b_days) * 0.5:
        diagnosis = "Мало трафика: визитов вдвое ниже базы. Воронка по конверсиям ок — вопрос в притоке."
    else:
        diagnosis = "Аномалий в воронке нет — конверсии на уровне базы."
    print(f"\n🩺 ВЕРДИКТ: {diagnosis}")

    # Снимок в БД
    psql("""CREATE TABLE IF NOT EXISTS funnel_health (
        id BIGSERIAL PRIMARY KEY, day DATE, visits INT, revenue_rub INT,
        diagnosis TEXT, metrics JSONB, created_at TIMESTAMPTZ DEFAULT NOW());""")
    metrics_json = json.dumps({"yesterday": y, "baseline_7d": b}).replace("'", "''")
    diag_esc = diagnosis.replace("'", "''")
    psql(f"INSERT INTO funnel_health (day, visits, revenue_rub, diagnosis, metrics) "
         f"VALUES ('{yest}', {int(y['visits'])}, {rev_yest or 0}, '{diag_esc}', '{metrics_json}'::jsonb)")


if __name__ == "__main__":
    main()

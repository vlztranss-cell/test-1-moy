"""
Прямой запрос Метрика API по 9 целям за пост-gating окно (с 27.05 11:30 +03).
Сравнить с baseline 1119 визитов → 756 free → 13 PAYMENT_OPEN → 3 paid за неделю до.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env  # noqa
import urllib.parse
import urllib.request
import json

env = load_env()
TOKEN = env["YANDEX_OAUTH_TOKEN"]
COUNTER = env["YANDEX_METRIKA_COUNTER_ID"]

GOALS = {
    "PHOTO_UPLOADED":      env["YANDEX_GOAL_PHOTO_UPLOADED"],
    "GENERATION_STARTED":  env["YANDEX_GOAL_GENERATION_STARTED"],
    "FREE_GEN_COMPLETED":  env["YANDEX_GOAL_FREE_GEN_COMPLETED"],
    "PAID_GEN_COMPLETED":  env["YANDEX_GOAL_PAID_GEN_COMPLETED"],
    "GEN_FAILED":          env["YANDEX_GOAL_GEN_FAILED"],
    "PAYMENT_OPEN":        env["YANDEX_GOAL_PAYMENT_OPEN"],
    "PAYMENT_REDIRECT":    env["YANDEX_GOAL_PAYMENT_REDIRECT"],
    "PAYMENT_SUCCESS":     env["YANDEX_GOAL_PAYMENT_SUCCESS"],
    "VIDEO_DOWNLOAD":      env["YANDEX_GOAL_VIDEO_DOWNLOAD"],
}

def fetch(date1: str, date2: str, metrics: list[str], label: str) -> dict:
    params = {
        "ids": COUNTER,
        "date1": date1, "date2": date2,
        "metrics": ",".join(metrics),
        "accuracy": "full",
    }
    url = "https://api-metrika.yandex.net/stat/v1/data?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"OAuth {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    totals = data.get("totals", [])
    print(f"\n=== {label} ({date1}…{date2}) ===")
    for m, v in zip(metrics, totals):
        # imm-имя цели → короткий лейбл
        name = m.replace("ym:s:goal", "g").replace("reaches", "").replace("visits", "VISITS").replace("users", "USERS")
        print(f"  {name:>30s} = {v}")
    return dict(zip(metrics, totals))


def goal_metric(goal_id: str) -> str:
    return f"ym:s:goal{goal_id}reaches"


# Базовая метрика — визиты и юзеры
base_metrics = ["ym:s:visits", "ym:s:users"]
goal_metrics = [goal_metric(gid) for gid in GOALS.values()]
# Yandex limits us to ≤20 metrics. У нас 2 + 9 = 11 → ок.

# Окно gating: с момента деплоя (27.05 11:30 +03) — берём с 27.05 и сегодня
fetch("2026-05-27", "2026-05-28", base_metrics + goal_metrics, "Пост-gating (27-28.05)")

# Baseline: неделя ДО (20-26.05)
fetch("2026-05-20", "2026-05-26", base_metrics + goal_metrics, "Baseline (20-26.05, неделя до)")

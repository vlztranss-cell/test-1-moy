"""
Проверка гипотезы i18n: какая доля визитов из не-РФ за прошедшую неделю?
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env  # noqa
import urllib.parse, urllib.request, json

env = load_env()
TOKEN = env["YANDEX_OAUTH_TOKEN"]
COUNTER = env["YANDEX_METRIKA_COUNTER_ID"]

params = {
    "ids": COUNTER,
    "date1": "2026-05-21", "date2": "2026-05-28",
    "metrics": "ym:s:visits,ym:s:users",
    "dimensions": "ym:s:regionCountry",
    "limit": 50,
    "accuracy": "full",
}
url = "https://api-metrika.yandex.net/stat/v1/data?" + urllib.parse.urlencode(params)
req = urllib.request.Request(url, headers={"Authorization": f"OAuth {TOKEN}"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.loads(r.read().decode())

rows = data.get("data", [])
total_v = sum(row["metrics"][0] for row in rows)
total_u = sum(row["metrics"][1] for row in rows)
print(f"Всего за неделю: {int(total_v)} визитов / {int(total_u)} юзеров\n")
print(f"{'Страна':<35s} {'visits':>8s} {'%v':>6s} {'users':>8s}")
print("-" * 62)
for row in rows[:20]:
    name = row["dimensions"][0].get("name", "?")
    v = int(row["metrics"][0]); u = int(row["metrics"][1])
    pct = 100 * v / total_v if total_v else 0
    print(f"{name:<35s} {v:>8d} {pct:>5.1f}% {u:>8d}")

# Не-РФ доля
ru = next((row for row in rows if row["dimensions"][0].get("name") == "Россия"), None)
ru_v = int(ru["metrics"][0]) if ru else 0
non_ru = total_v - ru_v
print(f"\nРФ: {ru_v} визитов ({100*ru_v/total_v:.1f}%)")
print(f"Не-РФ: {int(non_ru)} визитов ({100*non_ru/total_v:.1f}%)")

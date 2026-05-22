"""
Отправляет объявления всех 4 VideoAI-кампаний на модерацию.
Кампания переходит DRAFT → MODERATION → (после проверки яндексом) → ACCEPTED.

Запуск:
    python scripts/direct_send_to_moderation.py
"""
from __future__ import annotations
import io, json, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

# Ad IDs для 4 VideoAI-кампаний (фиксированные ids из direct_populate_campaigns)
AD_IDS = [17726262823, 17726263124, 17726263154, 17726263157]
CAMPAIGN_IDS = [710122418, 710122420, 710122422, 710122424]


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    H = {
        "Authorization": f"Bearer {env['YANDEX_OAUTH_TOKEN']}",
        "Accept-Language": "ru",
        "Content-Type": "application/json; charset=utf-8",
    }

    # 1. ads.moderate
    body = {"method": "moderate", "params": {"SelectionCriteria": {"Ids": AD_IDS}}}
    req = urllib.request.Request(
        "https://api.direct.yandex.com/json/v5/ads",
        data=json.dumps(body).encode(), method="POST", headers=H,
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    results = data["result"]["ModerateResults"]
    print("=== ads.moderate ===")
    for ad_id, res in zip(AD_IDS, results):
        if res.get("Errors"):
            err = res["Errors"][0]
            note = "уже не в DRAFT" if err.get("Code") == 8300 else err.get("Details", "")
            print(f"  ⏭  ad_id={ad_id}: {note}")
        else:
            print(f"  ✅ ad_id={ad_id} отправлено на модерацию")

    # 2. Проверка статусов
    body2 = {"method": "get", "params": {
        "SelectionCriteria": {"Ids": CAMPAIGN_IDS},
        "FieldNames": ["Id", "Name", "State", "Status", "StatusClarification"],
    }}
    req2 = urllib.request.Request(
        "https://api.direct.yandex.com/json/v5/campaigns",
        data=json.dumps(body2).encode(), method="POST", headers=H,
    )
    with urllib.request.urlopen(req2, timeout=15) as r:
        data = json.loads(r.read())
    print("\n=== Статус кампаний после ===")
    for c in data["result"]["Campaigns"]:
        print(f"  {c['Name']:30} State={c['State']:6} Status={c['Status']:12} {c.get('StatusClarification','')}")
    print("\n📋 Модерация обычно занимает 30 минут — 24 часа.")
    print("   После прохождения Status станет 'ACCEPTED', можно запускать:")
    print("   python scripts/direct_resume_campaigns.py --apply")


if __name__ == "__main__":
    main()

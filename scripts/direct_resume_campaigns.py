"""
Запускает все 4 VideoAI-кампании в Yandex Direct (campaigns.resume).
Перед запуском убедитесь что:
- Баланс кампаний пополнен (от 100₽ на кампанию)
- Объявления прошли модерацию (Status='ACCEPTED' или хотя бы не REJECTED)

Запуск:
    python scripts/direct_resume_campaigns.py --apply
"""
from __future__ import annotations
import argparse, io, json, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from direct_create_campaigns import DirectClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    env = load_env()
    client = DirectClient(env["YANDEX_OAUTH_TOKEN"])

    # Проверим статусы перед запуском
    data, _ = client.call("campaigns", "get", {
        "SelectionCriteria": {"Ids": [710122418, 710122420, 710122422, 710122424]},
        "FieldNames": ["Id", "Name", "State", "Status", "StatusPayment", "Funds"],
    })
    campaigns = data.get("result", {}).get("Campaigns", [])
    print("=== Текущее состояние ===")
    for c in campaigns:
        funds = c.get("Funds", {})
        amount = 0
        if funds.get("Mode") == "SHARED_ACCOUNT_FUNDS":
            amount = funds.get("SharedAccountFunds", {}).get("Amount", 0) / 1_000_000
        elif funds.get("Mode") == "CAMPAIGN_FUNDS":
            amount = funds.get("CampaignFunds", {}).get("Amount", 0) / 1_000_000
        print(f"  {c['Name']:30} | State={c['State']:8} Status={c.get('Status','?'):10} баланс={amount}₽")

    if not args.apply:
        print("\n[dry-run] --apply не передан, кампании не запускаются.")
        return

    ids = [c["Id"] for c in campaigns if c["State"] != "ON"]
    if not ids:
        print("\nВсе кампании уже включены.")
        return

    print(f"\nВключаю кампании: {ids}")
    data, _ = client.call("campaigns", "resume", {"SelectionCriteria": {"Ids": ids}})
    results = data.get("result", {}).get("ResumeResults", [])
    for r in results:
        if r.get("Errors"):
            print(f"  ❌ id={r.get('Id','?')}: {r['Errors']}")
        else:
            print(f"  ✅ id={r.get('Id')} запущена")


if __name__ == "__main__":
    main()

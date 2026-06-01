#!/usr/bin/env python3
"""Остановить (suspend) все активные кампании Яндекс.Директа. По явному запросу пользователя."""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

API = "https://api.direct.yandex.com/json/v5/campaigns"


def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept-Language": "ru",
        "Content-Type": "application/json; charset=utf-8",
    }


def call(token, payload):
    req = urllib.request.Request(API, data=json.dumps(payload).encode("utf-8"), headers=headers(token))
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get_campaigns(token):
    r = call(token, {"method": "get", "params": {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "State", "Status", "StatusPayment"],
    }})
    return r


def main():
    env = load_env()
    token = env.get("YANDEX_DIRECT_OAUTH_TOKEN") or env.get("YANDEX_OAUTH_TOKEN")
    if not token:
        print("NO TOKEN")
        sys.exit(1)

    before = get_campaigns(token)
    camps = before.get("result", {}).get("Campaigns", [])
    if not camps:
        print("Кампаний нет или ошибка:")
        print(json.dumps(before, ensure_ascii=False)[:600])
        return

    print("=== ДО ===")
    for c in camps:
        print(f"  {c['Id']} | {c.get('State')} | {c.get('Status')} | {c.get('Name')}")

    # Останавливаем те, что сейчас работают/могут показываться
    active = [c["Id"] for c in camps if c.get("State") in ("ON", "SERVING", "OFF") and c.get("State") != "SUSPENDED"]
    active = [c["Id"] for c in camps if c.get("State") in ("ON", "SERVING")]
    if not active:
        print("\nНет активных (ON/SERVING) кампаний — останавливать нечего.")
        return

    print(f"\nОстанавливаю (suspend): {active}")
    res = call(token, {"method": "suspend", "params": {"SelectionCriteria": {"Ids": active}}})
    print(json.dumps(res, ensure_ascii=False, indent=2))

    after = get_campaigns(token)
    print("\n=== ПОСЛЕ ===")
    for c in after.get("result", {}).get("Campaigns", []):
        print(f"  {c['Id']} | {c.get('State')} | {c.get('Status')} | {c.get('Name')}")


if __name__ == "__main__":
    main()

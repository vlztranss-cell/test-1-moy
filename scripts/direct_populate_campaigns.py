"""
Заполняет уже существующие 4 VideoAI-кампании: группы → объявления → ключи.
Идемпотентно: пропускает группы/ключи, которые уже есть с тем же Name/Keyword.

Запуск:
    python direct_populate_campaigns.py --apply
"""
from __future__ import annotations
import argparse, io, json, sys, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from direct_create_campaigns import (
    DirectClient, CAMPAIGNS, utm_url, GEO_RU
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    env = load_env()
    client = DirectClient(env["YANDEX_OAUTH_TOKEN"])

    # Map Name → campaign_id
    existing = client.get_existing_campaigns()
    name_to_id = {c["Name"]: c["Id"] for c in existing.values() if c["Name"].startswith("VideoAI")}
    print(f"Найдено наших кампаний: {len(name_to_id)}")
    for n, cid in name_to_id.items():
        print(f"  - {n}: id={cid}")

    for spec in CAMPAIGNS:
        if spec["name"] not in name_to_id:
            print(f"❌ '{spec['name']}' не найдена. Сначала запустите direct_create_campaigns.py --apply")
            continue
        cid = name_to_id[spec["name"]]
        print(f"\n=== {spec['name']} (cid={cid}) ===")

        # Получаем существующие группы этой кампании
        data, _ = client.call("adgroups", "get", {
            "SelectionCriteria": {"CampaignIds": [cid]},
            "FieldNames": ["Id", "Name", "CampaignId"],
        })
        existing_ags = {g["Name"]: g["Id"] for g in data.get("result", {}).get("AdGroups", [])}
        print(f"  существующих групп: {len(existing_ags)}")

        for ag_spec in spec["ad_groups"]:
            if ag_spec["name"] in existing_ags:
                ag_id = existing_ags[ag_spec["name"]]
                print(f"  ⏭  AdGroup '{ag_spec['name']}' уже есть, id={ag_id}")
            else:
                if not args.apply:
                    print(f"  🆕 [dry] AdGroup '{ag_spec['name']}'")
                    continue
                ag_payload = {
                    "Name": ag_spec["name"],
                    "CampaignId": cid,
                    "RegionIds": [GEO_RU],
                }
                data, units = client.call("adgroups", "add", {"AdGroups": [ag_payload]})
                if "error" in data:
                    print(f"  ❌ AdGroup '{ag_spec['name']}': {data['error']}")
                    continue
                ag_res = data["result"]["AddResults"][0]
                if ag_res.get("Errors"):
                    print(f"  ❌ AdGroup: {ag_res['Errors']}")
                    continue
                ag_id = ag_res["Id"]
                print(f"  ✅ AdGroup '{ag_spec['name']}' создана id={ag_id} (Units {units})")

            # Объявления
            ad_data, _ = client.call("ads", "get", {
                "SelectionCriteria": {"AdGroupIds": [ag_id]},
                "FieldNames": ["Id", "AdGroupId"],
                "TextAdFieldNames": ["Title", "Title2", "Text", "Href"],
            })
            existing_ads = ad_data.get("result", {}).get("Ads", [])
            print(f"    объявлений в группе: {len(existing_ads)}")
            if not existing_ads:
                if args.apply:
                    ads_payload = [
                        {"AdGroupId": ag_id, "TextAd": {
                            "Title": a["title"], "Title2": a["title2"], "Text": a["text"],
                            "Href": utm_url(spec["utm_campaign"]), "Mobile": "NO",
                        }} for a in ag_spec["ads"]
                    ]
                    data, units = client.call("ads", "add", {"Ads": ads_payload})
                    for ad_res in data["result"]["AddResults"]:
                        if ad_res.get("Errors"):
                            print(f"    ❌ Ad: {ad_res['Errors']}")
                        else:
                            print(f"    ✅ Объявление id={ad_res['Id']}")

            # Ключевые слова
            kw_data, _ = client.call("keywords", "get", {
                "SelectionCriteria": {"AdGroupIds": [ag_id]},
                "FieldNames": ["Id", "Keyword", "AdGroupId"],
            })
            existing_kw = {k["Keyword"] for k in kw_data.get("result", {}).get("Keywords", [])}
            new_kw = [kw for kw in ag_spec["keywords"] if kw not in existing_kw]
            print(f"    ключей в группе: {len(existing_kw)}, новых нужно: {len(new_kw)}")
            if new_kw:
                if args.apply:
                    kw_payload = [{"AdGroupId": ag_id, "Keyword": kw} for kw in new_kw]
                    data, units = client.call("keywords", "add", {"Keywords": kw_payload})
                    ok = sum(1 for k in data["result"]["AddResults"] if not k.get("Errors"))
                    print(f"    ✅ Ключей добавлено: {ok}/{len(new_kw)}")
                    for i, k in enumerate(data["result"]["AddResults"]):
                        if k.get("Errors"):
                            print(f"       ❌ '{new_kw[i]}': {k['Errors']}")

    print("\nГотово.")


if __name__ == "__main__":
    main()

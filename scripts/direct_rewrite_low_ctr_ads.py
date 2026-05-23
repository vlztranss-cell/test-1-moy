"""
Переписывает тексты объявлений в кампаниях с низким CTR:
  - Love Story (id 710122424, CTR 0.9%)
  - Питомцы   (id 710122422, CTR 2.2%)

Стратегия:
  - Любой Yandex Direct text-ad НЕ редактируется через ads.update —
    PUT по существующему объявлению не разрешён.
  - Поэтому делаем: archive старые объявления + add новые с новыми текстами
    в те же AdGroups → проходит модерацию → перезаливает CTR с чистого листа.

Запуск:
    python direct_rewrite_low_ctr_ads.py --dry-run    # показать план
    python direct_rewrite_low_ctr_ads.py --apply      # применить
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env
from direct_create_campaigns import DirectClient, utm_url

# Тексты — рабочие гипотезы конкретно для болевых аудиторий
NEW_ADS = {
    710122424: {  # Love Story
        "utm": "love",
        "ads": [
            {
                "title": "Оживить свадебное фото",
                "title2": "AI-видео на годовщину свадьбы",
                "text": "Превратим фото в живое видео за 60 сек. Удивите супруга — 1-е видео бесплатно!",
            },
            {
                "title": "Подарок-сюрприз на годовщину",
                "title2": "Свадебное фото в движении",
                "text": "Любимое свадебное фото оживает на 5 секунд. Реакция будет навсегда!",
            },
            {
                "title": "Love story из вашего архива",
                "title2": "Старое фото в живом видео",
                "text": "AI добавит улыбку, поворот головы. Сохраните момент любви. Демо на botisk.ru",
            },
        ],
    },
    710122422: {  # Питомцы
        "utm": "pets",
        "ads": [
            {
                "title": "В память о питомце",
                "title2": "Оживить фото за 60 секунд",
                "text": "Когда кошки или собаки уже нет рядом — AI вернёт её на 5 секунд.",
            },
            {
                "title": "Видео из фото вашего кота",
                "title2": "AI оживляет любимца",
                "text": "Подарите себе тёплое воспоминание. Загрузите фото, получите живое видео.",
            },
            {
                "title": "Сохраните память о собаке",
                "title2": "Оживить фото за 1 минуту",
                "text": "Любимый питомец снова двигается на видео. Бесплатная демо-генерация на botisk.ru",
            },
        ],
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    env = load_env()
    client = DirectClient(env["YANDEX_OAUTH_TOKEN"])

    for cid, plan in NEW_ADS.items():
        print(f"\n=== Campaign {cid} ({plan['utm']}) ===")

        # Все AdGroups в кампании
        data, _ = client.call("adgroups", "get", {
            "SelectionCriteria": {"CampaignIds": [cid]},
            "FieldNames": ["Id", "Name"],
        })
        ags = data.get("result", {}).get("AdGroups", [])
        if not ags:
            print("  ❌ нет AdGroups")
            continue

        # Существующие объявления (для архивации + дедупа по Title)
        ag_ids = [g["Id"] for g in ags]
        data, _ = client.call("ads", "get", {
            "SelectionCriteria": {"AdGroupIds": ag_ids, "States": ["ON"]},
            "FieldNames": ["Id", "AdGroupId", "State", "Status"],
            "TextAdFieldNames": ["Title", "Title2", "Text"],
        })
        existing_ads = data.get("result", {}).get("Ads", [])
        existing_titles = {a.get("TextAd", {}).get("Title") for a in existing_ads}
        new_titles = {a["title"] for a in plan["ads"]}
        # Объявления, которых ещё нет в боевых, — для добавления
        ads_to_add = [a for a in plan["ads"] if a["title"] not in existing_titles]
        # Объявления для архивации — те, чьи Title НЕТ в новом списке
        ads_to_archive = [a for a in existing_ads if a.get("TextAd", {}).get("Title") not in new_titles]
        print(f"  Текущих active объявлений: {len(existing_ads)}")
        for a in existing_ads:
            t = (a.get("TextAd", {}).get("Title") or "")[:50]
            print(f"    ad {a['Id']} (AdGroup {a['AdGroupId']}) — \"{t}\"")

        print(f"  → к добавлению: {len(ads_to_add)}, к архивации: {len(ads_to_archive)}")
        if not args.apply:
            for ag in ags:
                for new in ads_to_add:
                    print(f"    + ag {ag['Id']}: \"{new['title']}\"")
            print(f"  [dry-run] архивировал бы: {[a['Id'] for a in ads_to_archive]}")
            continue

        # 1. Add только недостающие
        ads_payload = []
        href = utm_url(plan["utm"])
        for ag in ags:
            for new in ads_to_add:
                ads_payload.append({
                    "AdGroupId": ag["Id"],
                    "TextAd": {
                        "Title": new["title"],
                        "Title2": new["title2"],
                        "Text": new["text"],
                        "Href": href,
                        "Mobile": "NO",
                    },
                })
        if not ads_payload:
            print("  ⊘ все нужные объявления уже добавлены, пропускаю add")
            # архивации тоже не делаем — если ничего не добавлено новое, безопаснее не трогать старое
            continue
        data, units = client.call("ads", "add", {"Ads": ads_payload})
        new_ad_ids = []
        for r in data.get("result", {}).get("AddResults", []):
            if r.get("Errors"):
                print(f"  ❌ add: {r['Errors']}")
            else:
                new_ad_ids.append(r["Id"])
        print(f"  ✅ Добавлено новых: {len(new_ad_ids)} (Units {units})")

        # 2. Отправить на модерацию
        if new_ad_ids:
            data, _ = client.call("ads", "moderate", {
                "SelectionCriteria": {"Ids": new_ad_ids},
            })
            mr = data.get("result", {}).get("ModerateResults", [])
            ok = sum(1 for r in mr if not r.get("Errors"))
            print(f"  ✅ На модерацию: {ok}/{len(new_ad_ids)}")

        # 3. Архивируем только те старые, чьи Title не в новом плане
        if ads_to_archive:
            # Сначала останавливаем (Archive требует чтобы объявление было OFF)
            client.call("ads", "suspend", {
                "SelectionCriteria": {"Ids": [a["Id"] for a in ads_to_archive]},
            })
            data, _ = client.call("ads", "archive", {
                "SelectionCriteria": {"Ids": [a["Id"] for a in ads_to_archive]},
            })
            ar = data.get("result", {}).get("ArchiveResults", [])
            ok = sum(1 for r in ar if not r.get("Errors"))
            err_msgs = [r.get("Errors") for r in ar if r.get("Errors")]
            print(f"  ✅ Архивировано старых: {ok}/{len(ads_to_archive)}")
            if err_msgs: print(f"     errors: {err_msgs}")

    print("\nГотово.")


if __name__ == "__main__":
    main()

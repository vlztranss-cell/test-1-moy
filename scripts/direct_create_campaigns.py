"""
Создание 4 кампаний в Яндекс.Директе через API v5.

Все объекты создаются в режиме PAUSED/OFF (не открутка):
- Кампании: State='SUSPENDED'
- Группы: автоматом за кампанией
- Объявления: State не управляется, идёт на модерацию
- Ключевые слова: State='SUSPENDED' пока модерация не пройдёт

Запуск (только создание): python direct_create_campaigns.py --apply
Без --apply: только показать что будет создаваться, ничего не записать.

idempotency: скрипт проверяет существующие кампании по Name. Если уже есть —
пропускает. Если нужно пересоздать — удалить руками в UI или сменить Name.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

BASE = "https://api.direct.yandex.com/json/v5"
GEO_RU = 225  # Россия

# Stratagy: ручные ставки. После 50+ конверсий переключим на
# WB_DEFAULT/OPTIMIZE_CONVERSIONS на PAYMENT_SUCCESS — отдельный скрипт.
def bidding_strategy(daily_budget_rub: int):
    """Поиск через автоматическую WB_MAXIMUM_CLICKS с недельным лимитом,
    РСЯ выключаем (SERVING_OFF) — пилотируем сначала только осознанный intent.
    Включим РСЯ отдельным апдейтом когда соберём данные с Поиска."""
    weekly_limit_micros = daily_budget_rub * 7 * 1_000_000
    return {
        "Search": {
            "BiddingStrategyType": "WB_MAXIMUM_CLICKS",
            "WbMaximumClicks": {"WeeklySpendLimit": weekly_limit_micros},
        },
        "Network": {"BiddingStrategyType": "SERVING_OFF"},
    }


# Базовая структура кампаний
CAMPAIGNS = [
    {
        "name": "VideoAI — Память",
        "utm_campaign": "memory",
        "daily_budget": 200,
        "ad_groups": [
            {
                "name": "Память — основная",
                "keywords": [
                    "оживить старое фото", "старая фотография в видео",
                    "оживить фото бабушки", "оживить фото дедушки",
                    "сделать видео из старой фотографии", "вернуть к жизни фото",
                    "анимировать старое фото", "ии оживляет фото",
                    "нейросеть оживляет фото", "превратить фото в видео ИИ",
                    "оживить умершего родственника", "видео из старого снимка",
                ],
                "ads": [
                    {
                        "title": "Оживите старое фото",
                        "title2": "AI за 60 секунд, бесплатно",     # 27 ≤ 30
                        "text": "Превратите фото близких в живое видео. Загрузите снимок и получите ролик.",  # 79 ≤ 81
                    },
                ],
            },
        ],
    },
    {
        "name": "VideoAI — Детство",
        "utm_campaign": "babies",
        "daily_budget": 250,
        "ad_groups": [
            {
                "name": "Детство — основная",
                "keywords": [
                    "оживить фото ребёнка", "превратить детское фото в видео",
                    "анимация детских фотографий", "видео из фото младенца",
                    "оживить детство ии", "первые шаги фото в видео",
                    "подарок от внуков бабушке", "оживить семейные фото",
                    "видео из детских фото", "анимация фото ребенка",
                ],
                "ads": [
                    {
                        "title": "Оживите детские фото",
                        "title2": "AI делает живое видео",
                        "text": "Загрузите детское фото — получите видео за минуту. Подарок маме и бабушке.",
                    },
                ],
            },
        ],
    },
    {
        "name": "VideoAI — Питомцы",
        "utm_campaign": "pets",
        "daily_budget": 150,
        "ad_groups": [
            {
                "name": "Питомцы — основная",
                "keywords": [
                    "оживить фото кошки", "оживить фото собаки",
                    "видео из фото питомца", "воспоминания о питомце",
                    "в память о собаке", "анимация фото кота",
                    "оживить фото любимца", "видео из фото котика",
                ],
                "ads": [
                    {
                        "title": "Видео из фото питомца",
                        "title2": "Живое воспоминание, AI",
                        "text": "Превратите фото любимого питомца в живое видео. Сохраните память навсегда.",
                    },
                ],
            },
        ],
    },
    {
        "name": "VideoAI — Love Story",
        "utm_campaign": "love",
        "daily_budget": 150,
        "ad_groups": [
            {
                "name": "Love — основная",
                "keywords": [
                    "оживить свадебное фото", "подарок на годовщину",
                    "оживить фото с любимым", "подарок жене на день рождения",
                    "романтический подарок ии видео", "свадебное фото в видео",
                    "оригинальный подарок мужу", "анимированное свадебное фото",
                ],
                "ads": [
                    {
                        "title": "Оживите свадебное фото",
                        "title2": "Подарок на годовщину",
                        "text": "Превратите свадебный снимок в живое видео. Подарок на годовщину или ДР.",
                    },
                ],
            },
        ],
    },
]

# Минус-слова на уровне кампании
NEGATIVE_KEYWORDS = [
    "курсы", "обучение", "скачать", "торрент", "бесплатно",
    "фотошоп", "premiere", "after effects", "своими руками",
    "урок", "туториал", "tutorial",
]


class DirectClient:
    def __init__(self, token: str):
        self.h = {
            "Authorization": f"Bearer {token}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8",
        }

    def call(self, service: str, method: str, params: dict):
        url = f"{BASE}/{service}"
        body = json.dumps({"method": method, "params": params}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers=self.h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
                units = r.headers.get("Units")
                return data, units
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {body}")

    def get_existing_campaigns(self) -> dict:
        data, _ = self.call("campaigns", "get", {
            "SelectionCriteria": {},
            "FieldNames": ["Id", "Name", "State", "Status", "Type"],
        })
        return {c["Name"]: c for c in data.get("result", {}).get("Campaigns", [])}


def utm_url(utm_campaign: str) -> str:
    return (
        f"https://botisk.ru/?utm_source=yandex&utm_medium=cpc"
        f"&utm_campaign={utm_campaign}&utm_content={{ad_id}}&utm_term={{keyword}}"
        f"&yclid={{yclid}}"
    )


def build_campaign_payload(spec: dict) -> dict:
    # При WB_MAXIMUM_CLICKS бюджет задаётся через WeeklySpendLimit в стратегии,
    # отдельный DailyBudget указывать нельзя — конфликт.
    return {
        "Name": spec["name"],
        "StartDate": time.strftime("%Y-%m-%d"),
        "NegativeKeywords": {"Items": NEGATIVE_KEYWORDS},
        "TextCampaign": {
            "BiddingStrategy": bidding_strategy(spec["daily_budget"]),
            "CounterIds": {"Items": [109293181]},
            "Settings": [
                {"Option": "ADD_METRICA_TAG", "Value": "YES"},
                {"Option": "REQUIRE_SERVICING", "Value": "NO"},
            ],
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Реально создать кампании (без флага — dry-run)")
    args = ap.parse_args()

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    client = DirectClient(env["YANDEX_OAUTH_TOKEN"])

    existing = client.get_existing_campaigns()
    print(f"Существующих кампаний: {len(existing)}")
    for n in existing:
        print(f"  ⏭  '{n}' (id={existing[n]['Id']}, state={existing[n]['State']})")

    print(f"\nПланируем создать {len(CAMPAIGNS)} кампаний:")
    for spec in CAMPAIGNS:
        skip = spec["name"] in existing
        marker = "⏭ SKIP" if skip else "🆕 NEW "
        print(f"  {marker} '{spec['name']}' (budget {spec['daily_budget']}₽/сутки, {len(spec['ad_groups'][0]['keywords'])} keywords)")

    if not args.apply:
        print("\n[dry-run] --apply не передан, ничего не создаём.")
        return

    print("\n========= APPLY =========")
    created = []
    for spec in CAMPAIGNS:
        if spec["name"] in existing:
            print(f"⏭  '{spec['name']}' — уже есть, пропускаем")
            continue
        # 1. Создать кампанию
        payload = build_campaign_payload(spec)
        data, units = client.call("campaigns", "add", {"Campaigns": [payload]})
        if "error" in data:
            print(f"❌ Создание кампании '{spec['name']}': {data['error']}")
            continue
        results = data["result"].get("AddResults", [])
        if not results:
            print(f"❌ '{spec['name']}': пустой результат")
            continue
        # AddResults может содержать Errors=[] (пустой) — это успех. Реальная ошибка
        # это когда Errors список НЕ пустой.
        first = results[0]
        if first.get("Errors"):
            print(f"❌ '{spec['name']}': {first['Errors']}")
            continue
        cid = first["Id"]
        print(f"✅ Кампания '{spec['name']}' создана: id={cid} (Units: {units})")
        created.append({"campaign_id": cid, "spec": spec, "ad_groups": []})

        # 2. Создать группу(ы) объявлений
        for ag_spec in spec["ad_groups"]:
            ag_payload = {
                "Name": ag_spec["name"],
                "CampaignId": cid,
                "RegionIds": [GEO_RU],
            }
            data, units = client.call("adgroups", "add", {"AdGroups": [ag_payload]})
            ag_res = data["result"]["AddResults"][0]
            if ag_res.get("Errors"):
                print(f"  ❌ AdGroup '{ag_spec['name']}': {ag_res['Errors']}")
                continue
            ag_id = ag_res["Id"]
            print(f"  ✅ Группа '{ag_spec['name']}' создана: id={ag_id} (Units: {units})")

            # 3. Создать объявления
            ads_payload = []
            for ad_spec in ag_spec["ads"]:
                ads_payload.append({
                    "AdGroupId": ag_id,
                    "TextAd": {
                        "Title":  ad_spec["title"],
                        "Title2": ad_spec["title2"],
                        "Text":   ad_spec["text"],
                        "Href":   utm_url(spec["utm_campaign"]),
                        "Mobile": "NO",
                    },
                })
            data, units = client.call("ads", "add", {"Ads": ads_payload})
            for i, ad_res in enumerate(data["result"]["AddResults"]):
                if ad_res.get("Errors"):
                    print(f"    ❌ Ad: {ad_res['Errors']}")
                else:
                    print(f"    ✅ Объявление id={ad_res['Id']}")

            # 4. Ключевые слова — batch до 1000 шт.
            kw_payload = [
                {"AdGroupId": ag_id, "Keyword": kw}
                for kw in ag_spec["keywords"]
            ]
            data, units = client.call("keywords", "add", {"Keywords": kw_payload})
            ok_count = sum(1 for k in data["result"]["AddResults"] if "Id" in k)
            err_count = len(data["result"]["AddResults"]) - ok_count
            print(f"    ✅ Ключей создано: {ok_count}/{len(kw_payload)} (err: {err_count}, Units: {units})")
            for k in data["result"]["AddResults"]:
                if k.get("Errors"):
                    print(f"       ❌ {k}")

            created[-1]["ad_groups"].append(ag_id)

    print(f"\n========= ИТОГИ =========")
    print(f"Создано кампаний: {len(created)}")
    for c in created:
        spec = c["spec"]
        print(f"  - {spec['name']}: campaign_id={c['campaign_id']}, ad_groups={c['ad_groups']}")
    print("\nКампании в статусе DRAFT (не откручиваются). После модерации:")
    print("  1. Зайти в https://direct.yandex.ru/, проверить тексты/ключи/гео")
    print("  2. Пополнить баланс кампании (от 100₽ на каждую)")
    print("  3. Сменить State в SUSPENDED → ON для запуска")
    print("Можно тоже через API: campaigns.resume + проверка Status='ACCEPTED'")


if __name__ == "__main__":
    main()

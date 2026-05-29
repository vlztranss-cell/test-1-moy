#!/usr/bin/env python3
"""
Заливает 4 финальных (курируемых вручную) ТЗ в таблицу workzilla_tz_drafts,
из которой дашборд показывает раздел «Готовые ТЗ для Workzilla».

Зачем: авто-генератор Mark_Auto_Workzilla_TZ ставился на паузу 29.05 — теперь
ТЗ курируются вручную (docs/workzilla_tz/TZ_0{1..4}_..._FOR_WORKZILLA.md) с
согласованными обложками (/op/covers/<platform>.png). Этот скрипт синхронизирует
файлы → БД.

Идемпотентно: UPDATE существующих + INSERT отсутствующих по platform. Запускать
после правки любого TZ_*_FOR_WORKZILLA.md или обложки.

    python scripts/seed_workzilla_drafts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ssh import psql_file, psql

DOCS = Path(__file__).parent.parent / "docs" / "workzilla_tz"
COVER_BASE = "https://n8n.24isk.ru/op/covers"

# platform -> (файл ТЗ, заголовок, цена ₽, utm_content)
SPEC = {
    "vc":     ("TZ_01_VC_FOR_WORKZILLA.md",     "Как мы запустили AI-сервис оживления фото за неделю (VC.ru)", 600, "vc_workzilla_01"),
    "pikabu": ("TZ_02_Pikabu_FOR_WORKZILLA.md", "Я оживил фото бабушки через AI — папа плакал (Pikabu)",       400, ""),
    "habr":   ("TZ_03_Habr_FOR_WORKZILLA.md",   "Kling 2.5 vs Sora vs Runway: опыт оживления 600+ фото (Habr)", 1000, "habr_workzilla_03"),
    "dzen":   ("TZ_04_Dzen_FOR_WORKZILLA.md",   "Подарила маме видео из её детского фото (Дзен)",               300, ""),
}


def esc(s: str) -> str:
    return s.replace("'", "''")


def main() -> None:
    parts = ["BEGIN;"]
    for plat, (fname, title, price, utm) in SPEC.items():
        md = (DOCS / fname).read_text(encoding="utf-8")
        cover = f"{COVER_BASE}/{plat}.png"
        parts.append(
            f"UPDATE workzilla_tz_drafts SET tz_markdown='{esc(md)}', tz_title='{esc(title)}', "
            f"cover_url='{cover}', suggested_price_rub={price}, utm_content='{esc(utm)}', "
            f"status='ready_to_post' WHERE platform='{plat}';"
        )
        parts.append(
            f"INSERT INTO workzilla_tz_drafts (platform, tz_title, tz_markdown, cover_url, "
            f"suggested_price_rub, utm_content, status, generated_at) "
            f"SELECT '{plat}','{esc(title)}','{esc(md)}','{cover}',{price},'{esc(utm)}','ready_to_post',NOW() "
            f"WHERE NOT EXISTS (SELECT 1 FROM workzilla_tz_drafts WHERE platform='{plat}');"
        )
    parts.append("COMMIT;")

    sql_path = Path(__file__).parent.parent / "n8n-workflows" / "_seed_workzilla.sql"
    sql_path.write_text("\n".join(parts), encoding="utf-8")
    out, err = psql_file(sql_path)
    print(out or "(psql ok)")
    if err.strip():
        print("STDERR:", err[:300])
    sql_path.unlink(missing_ok=True)

    rows, _ = psql("SELECT platform, status, cover_url FROM workzilla_tz_drafts ORDER BY platform")
    print("Итог таблицы:\n" + rows)


if __name__ == "__main__":
    main()

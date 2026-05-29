"""
Выгружает готовые ТЗ (поле tz_markdown) из workzilla_tz_drafts.
Нужно, чтобы посмотреть, что уже собирает Auto_Workzilla_TZ workflow.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ssh import psql  # noqa

# Сначала список + длины
out, _ = psql("""
    SELECT id, platform,
           LEFT(tz_title, 50) AS title,
           LENGTH(tz_markdown) AS md_len,
           LENGTH(article_text) AS art_len,
           cover_url
    FROM workzilla_tz_drafts
    ORDER BY id;
""")
print("=== Список ТЗ + длины ===")
print(out)

# Дамп tz_markdown первого
out, _ = psql("""
    SELECT tz_markdown FROM workzilla_tz_drafts ORDER BY id LIMIT 1;
""")
print("\n=== tz_markdown первого ТЗ (что собирает workflow) ===")
print(out[:3000] if out else "(пусто)")

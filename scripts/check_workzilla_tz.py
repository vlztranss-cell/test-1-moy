"""
Состояние ТЗ Workzilla в БД (workzilla_tz_drafts).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ssh import psql  # noqa

print("=== Схема workzilla_tz_drafts ===")
out, _ = psql("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'workzilla_tz_drafts'
    ORDER BY ordinal_position;
""")
print(out or "(нет таблицы)")

print("\n=== Все ТЗ + cover_url ===")
out, _ = psql("""
    SELECT id, platform,
           LEFT(COALESCE(tz_title, ''), 50) AS tz_title,
           CASE WHEN cover_url IS NULL THEN 'NO_COVER'
                WHEN cover_url LIKE '%n8n.24isk.ru%' THEN 'OUR_DOMAIN_OK'
                ELSE LEFT(cover_url, 40) END AS cover_status,
           status,
           generated_at,
           given_to_executor_at,
           publication_url
    FROM workzilla_tz_drafts
    ORDER BY id;
""")
print(out or "(нет данных)")

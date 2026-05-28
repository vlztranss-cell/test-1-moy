"""
Аудит фейковости email в web_users / web_orders.
Цель: понять масштаб проблемы перед тем как добавлять верификацию.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ssh import psql  # noqa

print("=== Все домены email в web_users (top 30) ===")
out, _ = psql("""
    SELECT LOWER(SPLIT_PART(email, '@', 2)) AS domain,
           COUNT(*) AS n,
           SUM(CASE WHEN free_used THEN 1 ELSE 0 END) AS used_free,
           SUM(paid_credits) AS paid_credits
    FROM web_users
    WHERE email LIKE '%@%'
    GROUP BY 1
    ORDER BY n DESC
    LIMIT 30;
""")
print(out)

print("\n=== Подозрительные паттерны (тест-email, повтор букв, no-MX-домены) ===")
out, _ = psql("""
    SELECT id, email, first_seen, free_used, paid_credits
    FROM web_users
    WHERE
      email ~* '^(test|asdf|qwerty|aaa|bbb|ccc|123|admin|user|abc|zzz)[0-9]*@'
      OR email ~* '@(test|asdf|qwerty|fake|tempmail|mailinator|10minutemail)\\.'
      OR email ~* '@.*\\.(xyz|top|click|tk)$'
      OR email ~* '^[a-z]@'  -- одна буква
      OR LENGTH(email) < 10  -- слишком короткий
      OR email ~* '(.)\\1{4,}'  -- 5+ одинаковых подряд
    ORDER BY first_seen DESC
    LIMIT 30;
""")
print(out or "(пусто — явных фейков нет)")

print("\n=== Итог: доля «подозрительных» от всех ===")
out, _ = psql("""
    WITH all_u AS (SELECT COUNT(*) AS total FROM web_users WHERE email LIKE '%@%'),
         susp AS (SELECT COUNT(*) AS n FROM web_users
                  WHERE email LIKE '%@%' AND (
                    email ~* '^(test|asdf|qwerty|aaa|bbb|ccc|123|admin|user|abc|zzz)[0-9]*@'
                    OR email ~* '@(test|asdf|qwerty|fake|tempmail|mailinator|10minutemail)\\.'
                    OR email ~* '@.*\\.(xyz|top|click|tk)$'
                    OR LENGTH(email) < 10
                  ))
    SELECT susp.n AS suspect, all_u.total AS total,
           ROUND(100.0 * susp.n / NULLIF(all_u.total, 0), 1) AS pct
    FROM susp, all_u;
""")
print(out)

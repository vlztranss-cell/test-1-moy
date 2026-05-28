"""
Sanity-check после gating (00b9b0b, деплой 27.05 ~11:30 МСК):
- web_orders за 24ч: статусы + charge_type, считаем FREE_GEN_COMPLETED
- web_orders с момента деплоя 27.05 11:30+03 → срез по gating-окну
- error_message за 24ч (что ломалось)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ssh import psql  # noqa


QUERIES = {
    "schema_web_orders": """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'web_orders'
        ORDER BY ordinal_position;
    """,
    "recent_5_full": """
        SELECT * FROM web_orders
        WHERE created_at > NOW() - INTERVAL '24 hours'
        ORDER BY created_at DESC
        LIMIT 5;
    """,
    "by_age_bucket": """
        SELECT charge_type, status,
               CASE
                  WHEN created_at > NOW() - INTERVAL '1 hour' THEN '1_last_hour'
                  WHEN created_at > NOW() - INTERVAL '6 hours' THEN '2_last_6h'
                  WHEN created_at > NOW() - INTERVAL '24 hours' THEN '3_last_24h'
               END AS age,
               COUNT(*) AS n
        FROM web_orders
        WHERE created_at > NOW() - INTERVAL '24 hours'
        GROUP BY 1, 2, 3
        ORDER BY 1, 3;
    """,
    "payments_24h": """
        SELECT id, email, status, amount_rub, tariff_code, is_paid,
               charge_type, payment_id, paid_at, created_at
        FROM web_orders
        WHERE created_at > NOW() - INTERVAL '24 hours'
          AND (amount_rub > 0 OR status = 'paid' OR is_paid = 'yes')
        ORDER BY created_at DESC
        LIMIT 30;
    """,
    "status_all_time": """
        SELECT status, COUNT(*) AS n,
               MIN(created_at) AS first_seen, MAX(created_at) AS last_seen
        FROM web_orders
        GROUP BY status
        ORDER BY n DESC;
    """,
    "schema_web_users": """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'web_users'
        ORDER BY ordinal_position;
    """,
    "funnel_post_gating": """
        WITH w AS (
          SELECT TIMESTAMP '2026-05-27 08:30:00' AS since
        )
        SELECT
          (SELECT COUNT(DISTINCT email) FROM web_orders, w WHERE created_at > since AND email IS NOT NULL) AS uniq_emails,
          (SELECT COUNT(*) FROM web_orders, w WHERE created_at > since AND charge_type = 'free') AS free_attempts,
          (SELECT COUNT(*) FROM web_orders, w WHERE created_at > since AND charge_type = 'free' AND result_video_url IS NOT NULL AND result_video_url <> '') AS free_completed,
          (SELECT COUNT(*) FROM web_orders, w WHERE created_at > since AND status = 'paid' AND is_paid = 'yes') AS paid_count,
          (SELECT COALESCE(SUM(amount_rub), 0) FROM web_orders, w WHERE created_at > since AND status = 'paid' AND is_paid = 'yes') AS revenue_rub;
    """,
    "users_post_gating": """
        SELECT
          COUNT(*) AS new_users,
          SUM(CASE WHEN free_used THEN 1 ELSE 0 END) AS used_free,
          SUM(paid_credits) AS paid_credits_now,
          SUM(total_generated) AS total_generated
        FROM web_users
        WHERE first_seen > TIMESTAMP '2026-05-27 08:30:00';
    """,
    "completed_videos_check": """
        SELECT
          DATE(created_at) AS day,
          charge_type,
          COUNT(*) AS attempts,
          SUM(CASE WHEN result_video_url IS NOT NULL AND result_video_url <> '' THEN 1 ELSE 0 END) AS completed,
          ROUND(100.0 * SUM(CASE WHEN result_video_url IS NOT NULL AND result_video_url <> '' THEN 1 ELSE 0 END) / COUNT(*), 1) AS completion_pct
        FROM web_orders
        WHERE created_at > NOW() - INTERVAL '7 days'
          AND charge_type IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1 DESC, 2;
    """,
}


def main() -> None:
    for label, sql in QUERIES.items():
        print(f"\n=== {label} ===")
        out, err = psql(sql.strip())
        if err.strip():
            print(f"[stderr] {err.strip()}")
        print(out.strip() or "(пусто)")


if __name__ == "__main__":
    main()

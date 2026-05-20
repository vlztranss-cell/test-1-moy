"""
Деплой локального dashboard.html на VPS:/srv/admin/index.html.

dashboard.html — source-of-truth в git (НЕ публикуется на botisk.ru,
исключён через _config.yml). Реальная отдача — Caddy за basic_auth по
адресу https://n8n.24isk.ru/op/.

Запуск:
    python scripts/deploy_dashboard.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ssh import vps_run, vps_upload

LOCAL_PATH = Path(__file__).parent.parent / "dashboard.html"
REMOTE_PATH = "/srv/admin/index.html"


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if not LOCAL_PATH.exists():
        print(f"[ERR] не найден локальный {LOCAL_PATH}")
        sys.exit(1)
    size = LOCAL_PATH.stat().st_size
    print(f"Заливаю {LOCAL_PATH.name} ({size} байт) → {REMOTE_PATH}")
    vps_upload(LOCAL_PATH, REMOTE_PATH)
    out, _ = vps_run(f"ls -la {REMOTE_PATH}")
    print(out.strip())
    out, _ = vps_run(
        "curl -sI -u admin:invalid https://n8n.24isk.ru/op/ | head -1; "
        "curl -sI -o /dev/null -w '%{http_code}' https://n8n.24isk.ru/op/index.html -u admin:" + "$DASHBOARD_PASSWORD_FROM_ENV" + " 2>&1 || true"
    )
    print(f"Проверка: https://n8n.24isk.ru/op/ должен вернуть 401 без auth и 200 с правильным admin/password (см. .env DASHBOARD_PASSWORD)")
    print("Без перезагрузки Caddy — файл сразу обслуживается.")


if __name__ == "__main__":
    main()

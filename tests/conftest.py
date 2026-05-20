"""
pytest fixtures для e2e-тестов VideoAI.

Тесты бьются по живому n8n (n8n.24isk.ru) и читают/чистят БД через
scripts/ssh.py (paramiko + sudo -u postgres psql).

Запуск:
    pip install pytest requests
    python -m pytest tests/ -v

Тесты создают временных юзеров с префиксом "e2e-" в email — после каждого
теста они автоматически удаляются (см. fixture test_email).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

# scripts/ → sys.path, чтобы импортировать ssh.psql и env_loader
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ssh import psql  # noqa: E402

BASE_URL = "https://n8n.24isk.ru"
URL_CREATE   = f"{BASE_URL}/webhook/web-video-create"
URL_STATUS   = f"{BASE_URL}/webhook/web-video-status"
URL_CALLBACK = f"{BASE_URL}/webhook/yukassa-payment-callback"

EMAIL_PREFIX = "e2e-"

# Tiny 1×1 PNG — для тестов, где сам PiAPI не должен запускаться (валидация / 402).
# Если он попадает в Upload to Freeimage, тот может зафейлить, но Charge Credit
# уже отработает ДО этого шага.
TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="


@pytest.fixture
def test_email():
    """Уникальный e2e-email. Cleanup в teardown — удаляем строки в web_users/web_orders."""
    email = f"{EMAIL_PREFIX}{uuid.uuid4().hex[:8]}@e2e.test"
    yield email
    # Cleanup: удаляем по email (тесты пишут только в эти таблицы)
    psql(f"DELETE FROM web_orders WHERE email = '{email}'")
    psql(f"DELETE FROM web_users  WHERE email = '{email}'")


@pytest.fixture
def tiny_png_b64():
    """Tiny 1×1 PNG в base64 (без data:-префикса)."""
    return TINY_PNG_B64


@pytest.fixture(scope="session")
def completed_task():
    """
    task_id уже завершённой генерации из БД. Нужен для status/watermark-тестов
    без новых обращений к PiAPI. Если в БД нет ни одной завершённой записи —
    тест скипается.
    """
    out, _ = psql(
        "SELECT piapi_task_id FROM web_orders "
        "WHERE piapi_task_id IS NOT NULL AND status IN ('paid','processing') "
        "ORDER BY id DESC LIMIT 1"
    )
    tid = out.strip()
    if not tid:
        pytest.skip("Нет завершённых web_orders записей с piapi_task_id для status-тестов")
    return tid


@pytest.fixture
def session_id():
    """Уникальный session_id для запросов create."""
    return f"e2e-sess-{uuid.uuid4().hex[:8]}"


def db_row(query: str) -> dict | None:
    """Хелпер: вытащить первую строку SELECT'а в dict (по символам |)."""
    out, _ = psql(query)
    out = out.strip()
    if not out:
        return None
    return out.split("|")

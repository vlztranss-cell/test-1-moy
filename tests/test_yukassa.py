"""
Тесты ЮKassa callback /webhook/yukassa-payment-callback.
Симулируем POST с body как у ЮKassa: event=payment.succeeded, metadata.source=web.
Проверяем что web_orders отмечена is_paid='yes' и web_users получил кредиты.
Идемпотентность: повторный callback не задваивает кредиты.
"""
import uuid

import pytest
import requests

from conftest import URL_CALLBACK, psql


@pytest.fixture
def pending_order(test_email):
    """
    Создаём pending web_orders для test_email и возвращаем (payment_id, tariff, credits).
    Очищается через cleanup test_email в conftest.
    """
    payment_id = f"e2e-pid-{uuid.uuid4().hex[:12]}"
    tariff = "starter"
    credits = 10
    amount = 290
    psql(
        f"INSERT INTO web_orders (payment_id, email, tariff_code, amount_rub, is_paid, "
        f"generations_limit, generations_left, status) "
        f"VALUES ('{payment_id}', '{test_email}', '{tariff}', {amount}, 'no', "
        f"{credits}, 0, 'pending')"
    )
    return payment_id, tariff, credits


def _send_callback(payment_id: str) -> requests.Response:
    body = {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": "290.00", "currency": "RUB"},
            "metadata": {"source": "web"},
        },
    }
    return requests.post(URL_CALLBACK, json=body, timeout=20)


def test_callback_marks_order_paid_and_credits_user(pending_order, test_email):
    payment_id, tariff, credits = pending_order
    r = _send_callback(payment_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credits_added"] == credits
    assert body["user_total_credits"] == credits
    assert body["email"] == test_email

    # web_orders отмечен оплаченным
    out, _ = psql(f"SELECT is_paid, status, generations_left FROM web_orders WHERE payment_id='{payment_id}'")
    is_paid, status, left = out.strip().split("|")
    assert is_paid == "yes"
    assert status == "paid"
    assert int(left) == credits

    # web_users существует с paid_credits = credits
    out, _ = psql(f"SELECT paid_credits FROM web_users WHERE email='{test_email}'")
    assert int(out.strip()) == credits


def test_callback_is_idempotent(pending_order, test_email):
    """Повторный callback не должен задвоить кредиты."""
    payment_id, tariff, credits = pending_order

    r1 = _send_callback(payment_id)
    assert r1.json()["user_total_credits"] == credits

    # Повторный
    r2 = _send_callback(payment_id)
    assert r2.status_code == 200
    # На повторе все поля NULL (WHERE is_paid<>'yes' не matched)
    assert r2.json().get("order_id") is None
    assert r2.json().get("credits_added") is None

    out, _ = psql(f"SELECT paid_credits FROM web_users WHERE email='{test_email}'")
    assert int(out.strip()) == credits, "повторный callback задвоил кредиты"


@pytest.mark.parametrize("tariff,amount,credits", [
    ("starter", 290, 10),
    ("pro", 790, 50),
    ("business", 2490, 200),
])
def test_callback_for_each_tariff(test_email, tariff, amount, credits):
    """Все три тарифа корректно зачисляют свой лимит."""
    payment_id = f"e2e-pid-{uuid.uuid4().hex[:12]}"
    psql(
        f"INSERT INTO web_orders (payment_id, email, tariff_code, amount_rub, is_paid, "
        f"generations_limit, generations_left, status) "
        f"VALUES ('{payment_id}', '{test_email}', '{tariff}', {amount}, 'no', "
        f"{credits}, 0, 'pending')"
    )
    r = _send_callback(payment_id)
    assert r.status_code == 200
    assert r.json()["credits_added"] == credits

    out, _ = psql(f"SELECT paid_credits FROM web_users WHERE email='{test_email}'")
    assert int(out.strip()) == credits


def test_callback_non_web_source_skipped(test_email):
    """metadata.source = 'bot' → callback пропускается (бот обрабатывает сам)."""
    payment_id = f"e2e-pid-{uuid.uuid4().hex[:12]}"
    psql(
        f"INSERT INTO web_orders (payment_id, email, tariff_code, amount_rub, is_paid, "
        f"generations_limit, generations_left, status) "
        f"VALUES ('{payment_id}', '{test_email}', 'starter', 290, 'no', 10, 0, 'pending')"
    )

    body = {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "metadata": {"source": "bot"},  # ← не web
        },
    }
    requests.post(URL_CALLBACK, json=body, timeout=20)

    # web_orders НЕ должен поменяться (source=bot → обработчик пропускает)
    out, _ = psql(f"SELECT is_paid FROM web_orders WHERE payment_id='{payment_id}'")
    assert out.strip() == "no", "web-callback не должен трогать bot-платежи"

    # web_users НЕ должен быть создан
    out, _ = psql(f"SELECT email FROM web_users WHERE email='{test_email}'")
    assert not out.strip(), "web_users не должен быть создан при source=bot"

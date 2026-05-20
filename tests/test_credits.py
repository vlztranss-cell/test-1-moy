"""
Тесты логики списания кредитов. Бьём по /webhook/web-video-create
с tiny PNG — Upload to Freeimage может упасть на нём, но Charge Credit
успевает отработать ДО Upload, поэтому web_users всегда заполняется.

Проверяем эффект на БД, а не на ответ HTTP (он может быть пустой
из-за падения downstream-нод PiAPI).
"""
import requests

from conftest import URL_CREATE, psql


def test_new_user_charged_free(test_email, tiny_png_b64, session_id):
    """Новый email → INSERT в web_users с free_used=true, charge_type=free."""
    requests.post(URL_CREATE, json={
        "email": test_email,
        "image_base64": tiny_png_b64,
        "session_id": session_id,
    }, timeout=30)

    out, _ = psql(f"SELECT free_used, paid_credits, total_generated FROM web_users WHERE email='{test_email}'")
    assert out.strip(), "web_users запись не создана для нового email"
    free_used, paid, generated = out.strip().split("|")
    assert free_used == "t",                     f"ожидали free_used=t, получили {free_used}"
    assert int(paid) == 0,                       f"ожидали paid_credits=0, получили {paid}"
    assert int(generated) == 1,                  f"ожидали total_generated=1, получили {generated}"


def test_drained_user_returns_402(test_email, tiny_png_b64, session_id):
    """User с free_used=true, paid=0 → 402 + need_payment=true."""
    psql(
        f"INSERT INTO web_users (email, free_used, paid_credits) VALUES ('{test_email}', TRUE, 0) "
        f"ON CONFLICT (email) DO UPDATE SET free_used=TRUE, paid_credits=0"
    )

    r = requests.post(URL_CREATE, json={
        "email": test_email,
        "image_base64": tiny_png_b64,
        "session_id": session_id,
    }, timeout=15)

    assert r.status_code == 402, f"ожидали 402, получили {r.status_code}: {r.text}"
    body = r.json()
    assert body["error"] == "no_credits"
    assert body["need_payment"] is True
    assert body["credits_left"] == 0

    # БД не должна была измениться (попытка списания не прошла)
    out, _ = psql(f"SELECT total_generated FROM web_users WHERE email='{test_email}'")
    assert int(out.strip()) == 0, "total_generated не должен увеличиваться при 402"


def test_paid_user_decrements_credits(test_email, tiny_png_b64, session_id):
    """User с paid_credits=3 → после генерации paid_credits=2."""
    psql(
        f"INSERT INTO web_users (email, free_used, paid_credits) VALUES ('{test_email}', TRUE, 3) "
        f"ON CONFLICT (email) DO UPDATE SET free_used=TRUE, paid_credits=3"
    )

    requests.post(URL_CREATE, json={
        "email": test_email,
        "image_base64": tiny_png_b64,
        "session_id": session_id,
    }, timeout=30)

    out, _ = psql(f"SELECT free_used, paid_credits, total_generated FROM web_users WHERE email='{test_email}'")
    free_used, paid, generated = out.strip().split("|")
    assert free_used == "t",       "free_used не должен меняться"
    assert int(paid) == 2,         f"paid_credits должен быть 2, получили {paid}"
    assert int(generated) == 1


def test_user_with_only_free_uses_it_first(test_email, tiny_png_b64, session_id):
    """User с paid_credits=5 и free_used=false → СПИСЫВАЕМ paid (paid > free приоритет)."""
    psql(
        f"INSERT INTO web_users (email, free_used, paid_credits) VALUES ('{test_email}', FALSE, 5) "
        f"ON CONFLICT (email) DO UPDATE SET free_used=FALSE, paid_credits=5"
    )

    requests.post(URL_CREATE, json={
        "email": test_email,
        "image_base64": tiny_png_b64,
        "session_id": session_id,
    }, timeout=30)

    out, _ = psql(f"SELECT free_used, paid_credits FROM web_users WHERE email='{test_email}'")
    free_used, paid = out.strip().split("|")
    assert free_used == "f",       f"free_used должен остаться FALSE, получили {free_used}"
    assert int(paid) == 4,         f"paid_credits должен уменьшиться до 4, получили {paid}"


def test_two_generations_drain_free_then_block(test_email, tiny_png_b64, session_id):
    """Без paid: 1-я генерация уходит как free, 2-я → 402."""
    # 1-я: новый юзер, free
    r1 = requests.post(URL_CREATE, json={
        "email": test_email, "image_base64": tiny_png_b64, "session_id": session_id,
    }, timeout=30)
    out, _ = psql(f"SELECT free_used FROM web_users WHERE email='{test_email}'")
    assert out.strip() == "t", "после 1-й генерации free_used должен стать TRUE"

    # 2-я: уже использовал free и paid=0 → 402
    r2 = requests.post(URL_CREATE, json={
        "email": test_email, "image_base64": tiny_png_b64, "session_id": session_id,
    }, timeout=15)
    assert r2.status_code == 402
    assert r2.json()["error"] == "no_credits"

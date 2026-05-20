"""
Тесты валидации входа /webhook/web-video-create.
Charge Credit ДО них не должен срабатывать — поэтому БД не трогается.
"""
import requests

from conftest import URL_CREATE


def test_no_email_returns_400(tiny_png_b64):
    r = requests.post(URL_CREATE, json={"image_base64": tiny_png_b64}, timeout=15)
    assert r.status_code == 400, f"ожидали 400, получили {r.status_code}: {r.text}"
    body = r.json()
    assert body["error"] == "email_invalid"
    assert body["status"] == "error"


def test_invalid_email_format_returns_400(tiny_png_b64):
    r = requests.post(URL_CREATE, json={"email": "not-an-email", "image_base64": tiny_png_b64}, timeout=15)
    assert r.status_code == 400
    assert r.json()["error"] == "email_invalid"


def test_email_without_dot_returns_400(tiny_png_b64):
    r = requests.post(URL_CREATE, json={"email": "user@nodot", "image_base64": tiny_png_b64}, timeout=15)
    assert r.status_code == 400
    assert r.json()["error"] == "email_invalid"


def test_no_image_returns_400(test_email):
    r = requests.post(URL_CREATE, json={"email": test_email}, timeout=15)
    assert r.status_code == 400
    assert r.json()["error"] == "image_required"


def test_empty_image_returns_400(test_email):
    r = requests.post(URL_CREATE, json={"email": test_email, "image_base64": ""}, timeout=15)
    assert r.status_code == 400
    assert r.json()["error"] == "image_required"

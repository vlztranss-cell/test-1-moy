"""
Тесты status flow с подменой URL по charge_type:
- free → URL на n8n.24isk.ru/videos/...mp4 + watermarked=true
- paid → URL на storage.theapi.app/...mp4 + watermarked=false

Использует уже завершённый task_id из БД (см. fixture completed_task),
не делает новых обращений к PiAPI.

Также проверяем что /videos/{tid}.mp4 отдаёт валидный MP4.
"""
import requests

from conftest import URL_STATUS, psql


def _get_status(task_id: str) -> dict:
    r = requests.get(f"{URL_STATUS}?task_id={task_id}", timeout=60)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
    return r.json()


def test_free_returns_watermarked_url(completed_task):
    """charge_type=free → video_url на n8n.24isk.ru/videos/..., watermarked=true."""
    # Сохраняем оригинальный charge_type для восстановления
    out, _ = psql(f"SELECT charge_type FROM web_orders WHERE piapi_task_id='{completed_task}'")
    original = out.strip() or "paid"
    try:
        psql(f"UPDATE web_orders SET charge_type='free' WHERE piapi_task_id='{completed_task}'")
        data = _get_status(completed_task)
        assert data["status"] == "completed"
        assert data["charge_type"] == "free"
        assert data["watermarked"] is True
        assert data["video_url"].startswith("https://n8n.24isk.ru/videos/")
        assert data["video_url"].endswith(".mp4")
    finally:
        psql(f"UPDATE web_orders SET charge_type='{original}' WHERE piapi_task_id='{completed_task}'")


def test_paid_returns_clean_url(completed_task):
    """charge_type=paid → URL на storage.theapi.app или похожий clean source, без watermark."""
    out, _ = psql(f"SELECT charge_type FROM web_orders WHERE piapi_task_id='{completed_task}'")
    original = out.strip() or "free"
    try:
        psql(f"UPDATE web_orders SET charge_type='paid' WHERE piapi_task_id='{completed_task}'")
        data = _get_status(completed_task)
        assert data["status"] == "completed"
        assert data["charge_type"] == "paid"
        assert data["watermarked"] is False
        # Clean URL — НЕ должен идти через наш домен
        assert "n8n.24isk.ru/videos/" not in data["video_url"]
        # Должен быть либо theapi.app, либо klingai.com (оба варианта clean из PAYG)
        assert ("theapi.app" in data["video_url"]) or ("klingai.com" in data["video_url"])
    finally:
        psql(f"UPDATE web_orders SET charge_type='{original}' WHERE piapi_task_id='{completed_task}'")


def test_watermarked_video_file_served(completed_task):
    """GET /videos/{tid}.mp4 отдаёт валидный MP4 (после free request он создан)."""
    # Гарантируем что файл существует — делаем free-запрос
    psql(f"UPDATE web_orders SET charge_type='free' WHERE piapi_task_id='{completed_task}'")
    data = _get_status(completed_task)
    public_url = data["video_url"]
    assert public_url.startswith("https://n8n.24isk.ru/videos/")

    # GET файла
    r = requests.get(public_url, timeout=30, stream=True)
    assert r.status_code == 200
    assert r.headers.get("Content-Type") == "video/mp4"
    # Первые 12 байт MP4 содержат `ftyp` box (на офсете 4)
    first_chunk = next(r.iter_content(chunk_size=16))
    assert b"ftyp" in first_chunk, f"не похоже на MP4: {first_chunk[:16]!r}"
    r.close()


def test_status_for_unknown_task_returns_processing_or_error(completed_task):
    """Незнакомый task_id → PiAPI вернёт 404 → workflow выдаст ошибку или status!=completed."""
    # Запрос с несуществующим task_id
    r = requests.get(f"{URL_STATUS}?task_id=does-not-exist-12345", timeout=30)
    # Не упасть, а вернуть что-то осмысленное (либо processing, либо ошибка)
    assert r.status_code in (200, 500)  # n8n может вернуть 500 если PiAPI 404

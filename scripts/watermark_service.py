#!/usr/bin/env python3
"""
HTTP-сервис watermark для VideoAI.
Развёрнут на VPS 72.56.96.64, слушает 0.0.0.0:8765.

Ручки:
- POST /watermark  body={"task_id":"...", "src_url":"https://..."}
    → скачивает src_url, накладывает watermark «botisk.ru»,
      кладёт в /srv/videos/{task_id}.mp4,
      возвращает {"url":"https://n8n.24isk.ru/videos/{task_id}.mp4"}
    Идемпотентно: если файл уже есть, обрабатывать не будет.
- GET /videos/{task_id}.mp4 → отдаёт файл из /srv/videos

n8n из контейнера обращается через шлюз docker bridge: http://172.17.0.1:8765
Caddy reverse-proxy'ит /videos/* → 172.17.0.1:8765 (см. Caddyfile).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VIDEOS_DIR = Path("/srv/videos")
WATERMARK_PNG = Path("/srv/watermark/watermark.png")
PUBLIC_BASE = "https://n8n.24isk.ru/videos"

# overlay: PNG-плашка «botisk.ru» в правом нижнем углу с отступом 20px.
# Static-build johnvansickle ffmpeg не имеет drawtext, поэтому используем overlay.
FFMPEG_FILTER = "[0:v][1:v]overlay=W-w-20:H-h-20"

TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

log = logging.getLogger("watermark")


def watermark_video(src_url: str, dst_path: Path) -> None:
    """Скачивает src_url во временный файл, накладывает watermark, кладёт в dst_path."""
    tmp_path = dst_path.with_suffix(".tmp.mp4")
    try:
        urllib.request.urlretrieve(src_url, tmp_path)
        if tmp_path.stat().st_size < 1024:
            raise RuntimeError(f"скачанный файл подозрительно мал: {tmp_path.stat().st_size}")

        result = subprocess.run(
            [
                "/usr/local/bin/ffmpeg", "-y",
                "-i", str(tmp_path),
                "-i", str(WATERMARK_PNG),
                "-filter_complex", FFMPEG_FILTER,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(dst_path),
            ],
            capture_output=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg вернул {result.returncode}: {result.stderr.decode()[:500]}")
    finally:
        tmp_path.unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # CORS на всякий, хотя n8n зовёт изнутри
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/watermark":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._send_json(400, {"error": "invalid_json", "detail": str(e)})
            return

        task_id = (body.get("task_id") or "").strip()
        src_url = (body.get("src_url") or "").strip()

        if not TASK_ID_RE.match(task_id):
            self._send_json(400, {"error": "invalid_task_id"})
            return
        if not src_url.startswith("https://"):
            self._send_json(400, {"error": "src_url_must_be_https"})
            return

        dst_path = VIDEOS_DIR / f"{task_id}.mp4"

        if dst_path.exists():
            self._send_json(200, {
                "url": f"{PUBLIC_BASE}/{task_id}.mp4",
                "cached": True,
                "size": dst_path.stat().st_size,
            })
            return

        started = time.time()
        try:
            watermark_video(src_url, dst_path)
        except Exception as e:
            log.exception("watermark failed for %s", task_id)
            self._send_json(500, {"error": "ffmpeg_failed", "detail": str(e)[:500]})
            return

        elapsed = time.time() - started
        self._send_json(200, {
            "url": f"{PUBLIC_BASE}/{task_id}.mp4",
            "cached": False,
            "size": dst_path.stat().st_size,
            "elapsed_sec": round(elapsed, 2),
        })

    def do_GET(self) -> None:
        # GET-роут оставлен как fallback на случай, если Caddy упал
        # — обычно Caddy сам раздаёт /videos через file_server без обращения к этому сервису
        if self.path == "/health":
            self._send_json(200, {"ok": True, "videos_dir": str(VIDEOS_DIR)})
            return
        if not self.path.startswith("/videos/"):
            self._send_json(404, {"error": "not_found"})
            return

        fname = self.path[len("/videos/"):].split("?", 1)[0]
        if not re.match(r"^[A-Za-z0-9_-]{8,64}\.mp4$", fname):
            self._send_json(400, {"error": "bad_filename"})
            return
        path = VIDEOS_DIR / fname
        if not path.exists():
            self._send_json(404, {"error": "video_not_found"})
            return

        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "public, max-age=2592000")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with path.open("rb") as f:
            while chunk := f.read(64 * 1024):
                self.wfile.write(chunk)

    def log_message(self, fmt: str, *args) -> None:
        # Подавляем стандартный шумный лог; используем logging
        log.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", 8765), Handler)
    log.info("watermark service listening on 0.0.0.0:8765, videos dir: %s", VIDEOS_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
HTTP-сервис для загрузки и листинга креативов.
Слушает 0.0.0.0:8766. Caddy проксирует /creatives/* → этот сервис
ВНУТРИ блока с basic_auth, чтобы только админ имел доступ.

Эндпоинты:
- GET  /creatives/health           — статус
- GET  /creatives/list             — JSON список файлов
- POST /creatives/upload           — multipart upload (один файл за раз)
- DELETE /creatives/{filename}     — удалить
- GET  /creatives/file/{filename}  — скачать
- GET  /creatives/thumb/{filename} — превью первого кадра (если есть)
"""
from __future__ import annotations

import cgi
import json
import logging
import mimetypes
import re
import shutil
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CREATIVES_DIR = Path("/srv/creatives/raw")
THUMBS_DIR = Path("/srv/creatives/thumbs")
ALLOWED_EXT = {".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")

log = logging.getLogger("creatives")


def safe_filename(raw: str) -> str:
    raw = raw.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9_.\-]", "_", raw)[:120]
    if not cleaned or cleaned.startswith("."):
        cleaned = f"file_{int(time.time())}"
    return cleaned


def generate_thumb(video_path: Path) -> Path | None:
    """Генерит превью первого кадра видео через ffmpeg."""
    if video_path.suffix.lower() not in {".mp4", ".mov", ".webm"}:
        return None
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = THUMBS_DIR / (video_path.stem + ".jpg")
    if thumb_path.exists():
        return thumb_path
    try:
        subprocess.run(
            ["/usr/local/bin/ffmpeg", "-y", "-i", str(video_path),
             "-ss", "00:00:01.000", "-vframes", "1",
             "-vf", "scale=320:-1", str(thumb_path)],
            check=True, capture_output=True, timeout=30,
        )
        return thumb_path
    except Exception as e:
        log.warning("thumb fail: %s", e)
        return None


class H(BaseHTTPRequestHandler):
    def _json(self, status: int, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str | None = None):
        if not path.exists():
            self._json(404, {"error": "not_found"})
            return
        ct = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "private, max-age=600")
        self.end_headers()
        with path.open("rb") as f:
            while chunk := f.read(64 * 1024):
                self.wfile.write(chunk)

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p in ("/creatives/health", "/creatives/", "/creatives"):
            self._json(200, {"ok": True, "dir": str(CREATIVES_DIR),
                              "free_gb": round(shutil.disk_usage(CREATIVES_DIR).free / 1e9, 1)})
            return
        if p == "/creatives/list":
            items = []
            for f in sorted(CREATIVES_DIR.glob("*"), key=lambda x: -x.stat().st_mtime):
                if not f.is_file(): continue
                items.append({
                    "name": f.name, "size": f.stat().st_size,
                    "size_mb": round(f.stat().st_size / 1e6, 2),
                    "mtime": int(f.stat().st_mtime),
                    "url": f"/creatives/file/{f.name}",
                    "thumb": f"/creatives/thumb/{f.stem}.jpg" if f.suffix.lower() in (".mp4", ".mov", ".webm") else f"/creatives/file/{f.name}",
                })
            usage = shutil.disk_usage(CREATIVES_DIR)
            self._json(200, {"items": items, "count": len(items),
                              "free_gb": round(usage.free / 1e9, 1),
                              "used_mb": round(sum(f.stat().st_size for f in CREATIVES_DIR.glob("*") if f.is_file()) / 1e6, 1)})
            return
        if p.startswith("/creatives/file/"):
            name = safe_filename(p[len("/creatives/file/"):])
            self._send_file(CREATIVES_DIR / name)
            return
        if p.startswith("/creatives/thumb/"):
            name = safe_filename(p[len("/creatives/thumb/"):])
            thumb = THUMBS_DIR / name
            if not thumb.exists():
                video = CREATIVES_DIR / (Path(name).stem + ".mp4")
                if video.exists():
                    generate_thumb(video)
            self._send_file(thumb, "image/jpeg")
            return
        self._json(404, {"error": "not_found", "path": p})

    def do_POST(self):
        if self.path != "/creatives/upload":
            self._json(404, {"error": "not_found"})
            return
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._json(400, {"error": "expected_multipart"})
            return
        try:
            form = cgi.FieldStorage(
                fp=self.rfile, headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype},
                keep_blank_values=True,
            )
            file_field = form["file"] if "file" in form else None
            if not file_field or not getattr(file_field, "filename", None):
                self._json(400, {"error": "no_file"})
                return
            name = safe_filename(file_field.filename)
            ext = Path(name).suffix.lower()
            if ext not in ALLOWED_EXT:
                self._json(400, {"error": "bad_extension", "allowed": list(ALLOWED_EXT)})
                return
            dest = CREATIVES_DIR / name
            # Если файл с таким именем уже есть — добавляем timestamp
            if dest.exists():
                stem = dest.stem
                dest = CREATIVES_DIR / f"{stem}_{int(time.time())}{ext}"
            with dest.open("wb") as out:
                shutil.copyfileobj(file_field.file, out)
            # Превью для видео
            generate_thumb(dest)
            self._json(200, {
                "ok": True, "name": dest.name, "size": dest.stat().st_size,
                "size_mb": round(dest.stat().st_size / 1e6, 2),
                "url": f"/creatives/file/{dest.name}",
            })
        except Exception as e:
            log.exception("upload failed")
            self._json(500, {"error": "upload_failed", "detail": str(e)[:300]})

    def do_DELETE(self):
        m = re.match(r"^/creatives/file/(.+)$", self.path)
        if not m:
            self._json(404, {"error": "not_found"})
            return
        name = safe_filename(m.group(1))
        target = CREATIVES_DIR / name
        if not target.exists():
            self._json(404, {"error": "not_found"})
            return
        target.unlink()
        thumb = THUMBS_DIR / (target.stem + ".jpg")
        if thumb.exists():
            thumb.unlink()
        self._json(200, {"ok": True, "deleted": name})

    def log_message(self, fmt, *args):
        log.info("%s - " + fmt, self.address_string(), *args)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    CREATIVES_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("creatives service on :8766, dir=%s", CREATIVES_DIR)
    ThreadingHTTPServer(("0.0.0.0", 8766), H).serve_forever()


if __name__ == "__main__":
    main()

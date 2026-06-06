#!/usr/bin/env python3
"""
Мини-хостинг картинок для пайплайна генерации (замена забаненного freeimage).
POST /upload  {image_base64}  -> сохраняет /srv/admin/uploads/<uuid>.<ext>
                              -> {"url": "https://n8n.24isk.ru/img/<uuid>.<ext>"}
GET  /health -> ok
Слушает 172.17.0.1:8767 (только docker-bridge/host, наружу не торчит).
Файлы старше 7 дней авточистятся. Раздаёт их Caddy: /img/* -> /srv/admin/uploads (public).
systemd: imghost.service
"""
import json, base64, uuid, time, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPLOADS = Path("/srv/admin/uploads")
UPLOADS.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE = "https://n8n.24isk.ru/img"
MAX_AGE = 7 * 24 * 3600


def cleanup():
    now = time.time()
    for f in UPLOADS.glob("*"):
        try:
            if now - f.stat().st_mtime > MAX_AGE:
                f.unlink()
        except Exception:
            pass


def detect_ext(raw: bytes) -> str:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != "/upload":
            self.send_response(404); self.end_headers(); return
        try:
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(ln) or b"{}")
            b64 = (body.get("image_base64") or "").strip()
            if b64.startswith("data:") and "," in b64:
                b64 = b64.split(",", 1)[1]
            raw = base64.b64decode(b64)
            if len(raw) < 100:
                raise ValueError("image too small/empty")
            name = uuid.uuid4().hex + detect_ext(raw)
            (UPLOADS / name).write_bytes(raw)
            cleanup()
            out = json.dumps({"url": f"{PUBLIC_BASE}/{name}"}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        except Exception as e:
            out = json.dumps({"error": str(e)[:150]}).encode()
            self.send_response(400); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)


if __name__ == "__main__":
    ThreadingHTTPServer(("172.17.0.1", 8767), H).serve_forever()

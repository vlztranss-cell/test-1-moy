#!/usr/bin/env python3
"""
Мини-хостинг картинок + download-прокси для пайплайна генерации.

POST /upload  {image_base64}  -> /srv/admin/uploads/<uuid>.<ext> -> {"url": ".../img/<uuid>.<ext>"}
GET  /dl?u=<video_url>         -> тянет видео с PiAPI/Kling и отдаёт с Content-Disposition: attachment
                                  (принудительное скачивание; на iOS обычный плеер не качается)
GET  /health                  -> ok

Слушает 172.17.0.1:8767 (docker-bridge/host, наружу не торчит — снаружи только через Caddy).
Картинки раздаёт Caddy: /img/* -> /srv/admin/uploads. Скачивание: /dl* -> этот сервис.
Файлы старше 7 дней авточистятся. systemd: imghost.service
"""
import json, base64, uuid, time, re, urllib.request
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPLOADS = Path("/srv/admin/uploads")
UPLOADS.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE = "https://n8n.24isk.ru/img"
MAX_AGE = 7 * 24 * 3600
# домены, с которых разрешено проксировать скачивание (защита от open-proxy)
DL_ALLOW = re.compile(r"^https://([a-z0-9-]+\.)?(theapi\.app|klingai\.com)/", re.I)


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
        p = urlparse(self.path)
        if p.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        if p.path == "/dl":
            u = (parse_qs(p.query).get("u") or [""])[0]
            if not u or not DL_ALLOW.match(u):
                self.send_response(403); self.end_headers(); self.wfile.write(b"forbidden"); return
            try:
                up = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=40)
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Disposition", 'attachment; filename="botisk-ai-video.mp4"')
                cl = up.headers.get("Content-Length")
                if cl:
                    self.send_header("Content-Length", cl)
                self.end_headers()
                while True:
                    chunk = up.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except Exception as e:
                try:
                    self.send_response(502); self.end_headers(); self.wfile.write(str(e).encode()[:120])
                except Exception:
                    pass
            return
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

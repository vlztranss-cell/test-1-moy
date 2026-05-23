"""
ОДНОРАЗОВЫЙ скрипт: получить refresh_token для YouTube Data API.

Запуск:
    python scripts/youtube_oauth_setup.py

ТРЕБУЕТСЯ:
- В .env прописаны YOUTUBE_CLIENT_ID и YOUTUBE_CLIENT_SECRET
  (получаются на https://console.cloud.google.com/auth/clients
   при создании Desktop app)

ЧТО ДЕЛАЕТ:
1. Запускает локальный HTTP-сервер на 127.0.0.1:8090 (loopback для Desktop OAuth)
2. Открывает в браузере URL Google OAuth с нужными scopes:
     youtube.upload + youtube.readonly + yt-analytics.readonly
3. Пользователь логинится в Google, разрешает доступ
4. Google редиректит на localhost:8090 с кодом
5. Меняем код на refresh_token через POST /token
6. Пишет YOUTUBE_REFRESH_TOKEN в .env (НЕ выводит в консоль)

Refresh_token действует ~7 дней в Testing OAuth-режиме. Когда истекает —
запускать скрипт повторно.
"""
from __future__ import annotations

import io
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

REDIRECT_PORT = 8090
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    # write-доступ нужен для channels.update (описание/branding),
    # playlists.insert (создание плейлистов), videos.update (правка описания загруженных видео).
    "https://www.googleapis.com/auth/youtube",
]


class CallbackHandler(BaseHTTPRequestHandler):
    received_code: str | None = None
    received_state: str | None = None
    expected_state: str = ""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        state = qs.get("state", [None])[0]
        error = qs.get("error", [None])[0]

        if error:
            self._respond(f"<h1>OAuth error</h1><p>{error}</p>", 400)
            return
        if state != CallbackHandler.expected_state:
            self._respond("<h1>State mismatch</h1>", 400)
            return
        CallbackHandler.received_code = code
        CallbackHandler.received_state = state
        self._respond(
            "<h1>✓ Готово!</h1>"
            "<p>Возвращайтесь в консоль — refresh_token сохранён в .env.</p>"
            "<p>Можно закрыть вкладку.</p>",
            200,
        )

    def _respond(self, html: str, status: int):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        return  # silent


def update_env_value(key: str, value: str) -> None:
    """Безопасно обновить или добавить ключ в .env. Сохраняет кавычки если есть."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f'{key}="{value}"')
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f'{key}="{value}"')
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    env = load_env()
    client_id = env.get("YOUTUBE_CLIENT_ID")
    client_secret = env.get("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("❌ В .env нет YOUTUBE_CLIENT_ID или YOUTUBE_CLIENT_SECRET.")
        print("   Создайте Desktop OAuth client здесь:")
        print("   https://console.cloud.google.com/auth/clients?project=n8n-selfhost-485207")
        sys.exit(1)

    state = secrets.token_urlsafe(24)
    CallbackHandler.expected_state = state

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        # consent — чтобы выдался refresh_token (повторно);
        # select_account — Google ОБЯЗАТЕЛЬНО покажет выбор аккаунта/канала,
        # включая brand-аккаунты привязанные к личному.
        "prompt": "consent select_account",
        "state": state,
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

    print("=" * 70)
    print("🌐 OAuth flow YouTube — Desktop app")
    print("=" * 70)
    print(f"\n1. Браузер сейчас откроется. Если нет — откройте URL вручную")
    print(f"2. ⚠️ ВАЖНО: при выборе аккаунта выберите КАНАЛ «VideoAI · Оживляем фото»,")
    print(f"   а НЕ личный аккаунт vlz.transs@gmail.com")
    print(f"   (после ввода логина появится страница «Выберите канал» — там выбрать VideoAI)")
    print(f"3. На предупреждении «Google hasn't verified this app» → Advanced → Go to (unsafe)")
    print(f"4. Разрешите все 3 запрашиваемых доступа")
    print(f"5. Google перенаправит на http://127.0.0.1:{REDIRECT_PORT}")
    print(f"6. Скрипт сохранит refresh_token в .env\n")
    print(f"Если выбора канала не было — отзовите доступ в")
    print(f"https://myaccount.google.com/permissions и запустите снова.\n")

    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), CallbackHandler)
    print(f"⏳ Жду коллбэк на 127.0.0.1:{REDIRECT_PORT}...")

    webbrowser.open(auth_url)
    server.handle_request()
    server.server_close()

    if not CallbackHandler.received_code:
        print("❌ Код не получен")
        sys.exit(1)

    print("✓ Код получен, меняю на refresh_token...")

    # Обмен code → refresh_token
    token_url = "https://oauth2.googleapis.com/token"
    body = urllib.parse.urlencode({
        "code": CallbackHandler.received_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(token_url, data=body, method="POST",
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            tokens = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}")
        sys.exit(1)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(f"❌ refresh_token не получен. Ответ: {tokens}")
        print("   Возможно вы уже авторизовали приложение — отзовите доступ в")
        print("   https://myaccount.google.com/permissions и попробуйте снова.")
        sys.exit(1)

    update_env_value("YOUTUBE_REFRESH_TOKEN", refresh_token)
    print()
    print("✓ refresh_token сохранён в .env (длина: " + str(len(refresh_token)) + " символов)")
    print("✓ access_token (живёт 1 час) скрипт не сохраняет — uploader сам обновит.")
    print()
    print("Дальше:")
    print("  python scripts/youtube_uploader.py --help")


if __name__ == "__main__":
    main()

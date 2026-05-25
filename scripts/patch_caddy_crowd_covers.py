"""Идемпотентно добавляет роут /crowd-covers/* в Caddyfile."""
import re
import shutil
from pathlib import Path

src = Path("/opt/n8n/Caddyfile")
text = src.read_text(encoding="utf-8")

if "/crowd-covers/*" in text:
    print("уже есть")
else:
    shutil.copy(src, "/opt/n8n/Caddyfile.bak.crowd")
    # Вставляем перед последним handle /videos или после
    block = """
  # Crowd-marketing обложки (public, нет basic_auth)
  handle /crowd-covers/* {
    root * /srv/admin
    file_server
  }
"""
    # вставляем перед reverse_proxy n8n:5678 (это конечный default)
    new_text = re.sub(
        r"(  reverse_proxy n8n:5678)",
        block + r"\1",
        text, count=1
    )
    if new_text == text:
        print("ERROR: anchor не найден")
    else:
        src.write_text(new_text, encoding="utf-8")
        print("ОК добавлено")

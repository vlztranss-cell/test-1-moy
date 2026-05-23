"""
Идемпотентный патч Caddyfile: добавляет Cache-Control no-cache в /op/* route.
Запуск на VPS: python3 /tmp/patch_caddyfile_nocache.py
"""
import re
import shutil
from pathlib import Path

src = Path("/opt/n8n/Caddyfile")
text = src.read_text(encoding="utf-8")

if "header Cache-Control" in text:
    print("already patched")
else:
    shutil.copy(src, "/opt/n8n/Caddyfile.bak.20260523")
    # Вставляем header перед file_server внутри /op route
    pattern = re.compile(r"(    try_files \{path\} /index\.html\n)(    file_server\n  \})")
    new_text, n = pattern.subn(
        r'\1    header Cache-Control "no-cache, no-store, must-revalidate"\n\2',
        text, count=1,
    )
    if n == 0:
        print("ERROR: anchor not found")
    else:
        src.write_text(new_text, encoding="utf-8")
        print("patched")

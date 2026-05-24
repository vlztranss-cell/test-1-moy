"""
H3: одноразовый пост в @botisk_canal про реф-программу с медиа.
Запуск на VPS:
    python3 /srv/creatives/tg_referral_promo_post.py
"""
import glob, json, sys, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

e = load_env()
TOKEN = e["TELEGRAM_BOT_PHOTO2VIDEO"]
CHAT = e["TG_CHANNEL_CHAT_ID"]

# MarkdownV2 — экранируем спецсимволы
caption = (
    "🎁 *Получай бесплатные видео — за друзей\\!*\n\n"
    "У каждого нашего платного пользователя есть личный реф\\-код\\. "
    "Поделись ссылкой с друзьями — и за каждого, кто оформит платный тариф, "
    "*тебе на счёт капают \\+3\\.\\.\\.10 видео* бесплатно\\.\n\n"
    "✨ *Как использовать:*\n"
    "1\\. Купи любой тариф \\(от 99 ₽\\)\n"
    "2\\. После оплаты получи свою ссылку\n"
    "3\\. Отправь её в чат с подругами / в семейный чат — оживить бабушку хотят все\n"
    "4\\. Каждый их платёж \\= бонусные видео тебе\n\n"
    "💡 *Пример:* пригласил 5 подруг — получил 15\\-50 бесплатных видео\\. "
    "Достаточно чтобы оживить весь семейный архив\\.\n\n"
    "👉 [@VideoAI\\_24isk\\_bot](https://t.me/VideoAI_24isk_bot) \\| "
    "[botisk\\.ru](https://botisk.ru)"
)

videos = sorted(glob.glob("/srv/creatives/processed_v2/kling_*_v2_memory_*.mp4"))
if not videos:
    print("NO VIDEO available")
    sys.exit(1)
chosen = videos[-1]
print(f"video: {chosen}")

boundary = "----RefPromoBoundary7MA4YWx"
body = b""
for k, v in [("chat_id", CHAT), ("caption", caption), ("parse_mode", "MarkdownV2")]:
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
    body += str(v).encode() + b"\r\n"
body += f"--{boundary}\r\n".encode()
body += b'Content-Disposition: form-data; name="video"; filename="ref.mp4"\r\n'
body += b"Content-Type: video/mp4\r\n\r\n"
body += Path(chosen).read_bytes() + b"\r\n"
body += f"--{boundary}--\r\n".encode()

req = urllib.request.Request(
    f"https://api.telegram.org/bot{TOKEN}/sendVideo",
    data=body, method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
try:
    r = urllib.request.urlopen(req, timeout=120)
    d = json.loads(r.read())
    msg_id = d.get("result", {}).get("message_id")
    print(f"✓ posted msg_id={msg_id}")
except urllib.error.HTTPError as ex:
    print(f"✗ HTTP {ex.code}: {ex.read().decode()[:300]}")

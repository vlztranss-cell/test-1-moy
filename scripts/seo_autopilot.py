#!/usr/bin/env python3
"""
SEO Autopilot — АВТОПИЛОТ контента блога botisk.ru (вариант B, на VPS).

Раз в день (cron на VPS) берёт N свободных ключей из seo_keywords_queue,
пишет по каждому качественный лонгрид через GPT (OpenAI), генерит тематическую
hero-картинку в Nano Banana 2 (PiAPI), собирает HTML по шаблону блога,
обновляет blog/index.html, помечает ключ использованным, коммитит и пушит
на GitHub Pages. Работает БЕЗ участия человека и без открытого Claude Code.

ПРАВИЛО (память feedback-seo-image-rule): каждая статья выходит со своей
тематической картинкой Nano Banana 2 — без картинки статья НЕ публикуется.

Темп: N=3/день (feedback-seo-autonomy) — не больше, чтобы не словить Яндекс
«Баден-Баден» / Google scaled-content на молодом домене.

Запуск:
    cd /srv/seo-site && python3 scripts/seo_autopilot.py [--dry-run] [--count N]

Cron (раз в день, 04:07 UTC, off-minute):
    7 4 * * *  cd /srv/seo-site && /usr/bin/python3 scripts/seo_autopilot.py >> /var/log/seo_gen.log 2>&1

Требует в .env: OPENAI_API_KEY, PIAPI_KEY (оба уже есть). Git remote с GitHub-токеном
настраивается ОДИН раз при клонировании репо — в этом скрипте токен НЕ фигурирует.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_loader import load_env

ENV = load_env()
REPO_DIR = Path(__file__).resolve().parent.parent      # клон репо на VPS (корень сайта)
BLOG_DIR = REPO_DIR / "blog"
IMG_DIR = BLOG_DIR / "img"
SITE = "https://botisk.ru"
METRIKA_ID = "109293181"
MODEL = "gpt-4o"                                        # существующее подключение OpenAI (для SEO-качества лучше mini)
N_DEFAULT = 3

OPENAI_KEY = ENV.get("OPENAI_API_KEY", "")
PIAPI_KEY = ENV.get("PIAPI_KEY", "")


# ─────────────────────────── БД ───────────────────────────
def psql(sql: str) -> str:
    r = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", "photo_bot", "-tA", "-F", "|", "-c", sql],
        capture_output=True, text=True, timeout=40,
    )
    if r.returncode != 0:
        print(f"  psql err: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def slugify(kw: str) -> str:
    table = str.maketrans({
        "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i",
        "й":"j","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t",
        "у":"u","ф":"f","х":"h","ц":"c","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"",
        "э":"e","ю":"yu","я":"ya"," ":"-"})
    s = kw.lower().translate(table)
    return "".join(ch for ch in s if ch.isalnum() or ch == "-").strip("-")


def pick_keywords(n: int) -> list[dict]:
    rows = psql("SELECT id, keyword, COALESCE(slug_template, '') "
                "FROM seo_keywords_queue WHERE used_at IS NULL ORDER BY id LIMIT %d;" % n)
    out = []
    for ln in rows.splitlines():
        if "|" not in ln:
            continue
        kid, kw, slug = (ln.split("|") + ["", ""])[:3]
        out.append({"id": int(kid), "keyword": kw, "slug": slug or slugify(kw)})
    return out


def mark_used(kid: int) -> None:
    psql("UPDATE seo_keywords_queue SET used_at = NOW() WHERE id = %d;" % kid)


def save_draft(slug: str, kw: str, title: str, meta: str, words: int) -> None:
    t = title.replace("'", "''"); m = meta.replace("'", "''"); k = kw.replace("'", "''")
    psql(f"""INSERT INTO seo_article_drafts (slug, keyword, title, meta_desc, word_count, status, generated_at, published_at)
             VALUES ('{slug}', '{k}', '{t}', '{m}', {words}, 'published', NOW(), NOW())
             ON CONFLICT (slug) DO UPDATE SET status='published', published_at=NOW();""")


# ──────────────────── Claude API: текст статьи ────────────────────
SYSTEM_PROMPT = """Ты — опытный русскоязычный контент-маркетолог сервиса VideoAI (botisk.ru),
который оживляет старые фотографии в короткие видео через нейросеть Kling 2.5.
Пишешь честные, полезные, эмоциональные SEO-статьи для РУ-аудитории (рынок — Яндекс).
Никакой воды и переоптимизации (риск фильтра Баден-Баден). Только польза + лёгкая эмоция.
Пишешь РАЗВЁРНУТО и КОНКРЕТНО — с примерами, цифрами, реальными сценариями. Тонкий короткий
текст (<900 слов) недопустим: он не ранжируется и вредит домену. Глубина важнее краткости.
УТП: первое видео бесплатно, оплата картами РФ, на русском, без подписки, от 290р за 10 видео.
Возвращаешь СТРОГО валидный JSON без markdown-обёртки."""

USER_TMPL = """Напиши SEO-статью под ключевой запрос: «{keyword}».

Верни JSON с полями:
- "title": заголовок H1 (60-70 симв, с ключом, цепляющий, без кликбейта)
- "meta_description": meta description СТРОГО 150-160 символов (с ключом и УТП), не короче
- "meta_keywords": 5-7 ключевых фраз через запятую
- "lead": вводный абзац (2-3 предложения, эмоциональный заход, <strong> для акцентов) — HTML.
  В body НЕ повторяй lead дословно.
- "toc": массив из 5-6 строк-заголовков разделов (для оглавления)
- "body_html": ОСНОВНОЙ текст в HTML. КРИТИЧНО: НЕ МЕНЕЕ 900 слов — короткий тонкий текст
  не ранжируется и недопустим. Используй <h2 id="s1">..</h2> (id=s1,s2,..), <h3>, <p>, <ul><li>, <strong>.
  ФОРМАТ КАЖДОГО РАЗДЕЛА: 2-3 ПОЛНЫХ АБЗАЦА (каждый абзац 3-4 предложения). Раздел из одного
  абзаца ЗАПРЕЩЁН — это даёт недостаточный объём. Конкретика, примеры, цифры, без воды.
  Обязательные разделы:
  (1) польза/зачем — с реальными сценариями (подарок, память, семейный архив);
  (2) «как выбрать фото» — конкретные критерии (анфас, резкость, одно лицо, что делать со старым/повреждённым);
  (3) «пошагово через VideoAI» — детально: загрузка фото на botisk.ru БЕЗ регистрации,
      подсказка движения, ~60 сек, первое видео бесплатно (НЕ пиши «зарегистрируйтесь» — регистрация не нужна);
  (4) ОБЯЗАТЕЛЬНО HTML-таблица <table> сравнения 3-4 способов (столбцы: способ, время, цена, результат)
      с конкретными цифрами — VideoAI vs студия/фрилансер vs зарубежные сервисы;
  (5) 4-5 практических советов <ul>.
  НЕ добавляй пустых «заключений»/«заключительных мыслей» без пользы.
  Внутри текста 2-3 ВНУТРЕННИЕ ссылки на смежные статьи блога (выбери подходящие):
  <a href="kak-ozhivit-staroe-foto.html">как оживить старое фото</a>,
  <a href="ozhivit-foto-dedushki.html">оживить фото дедушки</a>,
  <a href="kak-ozhivit-foto-babushki.html">фото бабушки</a>,
  <a href="video-iz-foto-pitomtsa.html">фото питомца</a> — плюс 1 ссылка <a href="/#generator">создать видео</a>.
  id заголовков совпадают с порядком в "toc".
- "faq": массив из 3-4 объектов {{"q":"вопрос","a":"ответ 2-3 предложения, конкретно"}}
- "image_prompt": англоязычный промпт для фотореалистичной ЭМОЦИОНАЛЬНОЙ hero-картинки под тему.
  БЕЗ текста и надписей. Тёплый человечный кадр (семья/память/оживающее фото).

Тема — оживление фото/память/семья. Тон тёплый, без пафоса. Только валидный JSON."""


def gpt_write(keyword: str) -> dict | None:
    """Пишет статью через OpenAI (существующее подключение). JSON-режим гарантирует валидный JSON."""
    body = json.dumps({
        "model": MODEL, "temperature": 0.7, "max_tokens": 6000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": USER_TMPL.format(keyword=keyword)}],
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "content-type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=120).read())
        return json.loads(r["choices"][0]["message"]["content"].strip())
    except urllib.error.HTTPError as e:
        print(f"  gpt HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"  gpt err: {e}")
    return None


# ──────────────────── Nano Banana 2: hero-картинка ────────────────────
def nano_banana(prompt: str, slug: str) -> str | None:
    body = json.dumps({
        "model": "gemini", "task_type": "nano-banana-2",
        "input": {"prompt": prompt + ", no text, no watermark, no letters",
                  "aspect_ratio": "16:9", "resolution": "2K", "output_format": "png"},
        "config": {"service_mode": "public"}}).encode()
    try:
        req = urllib.request.Request("https://api.piapi.ai/api/v1/task", data=body, method="POST",
            headers={"x-api-key": PIAPI_KEY, "Content-Type": "application/json", "User-Agent": "VideoAI-SEO/1.0"})
        tid = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("data", {}).get("task_id")
    except Exception as e:
        print(f"  nano create err: {e}"); return None
    if not tid:
        return None
    url = None
    for _ in range(25):
        try:
            req = urllib.request.Request(f"https://api.piapi.ai/api/v1/task/{tid}",
                headers={"x-api-key": PIAPI_KEY, "User-Agent": "VideoAI-SEO/1.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("data", {})
            st = (d.get("status") or "").lower()
            if st == "completed":
                out = d.get("output") or {}
                url = out.get("image_url") or (out.get("image_urls") or [None])[0]
                if not url:
                    works = out.get("works") or []
                    if works:
                        img = works[0].get("image") or {}
                        url = img.get("resource") or img.get("url")
                break
            if st == "failed":
                print(f"  nano failed: {d.get('error', {})}"); return None
            time.sleep(8)
        except Exception as e:
            print(f"  nano poll err: {e}"); time.sleep(8)
    if not url:
        return None
    try:
        data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "x"}), timeout=60).read()
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        raw = IMG_DIR / f"{slug}.png"; raw.write_bytes(data)
        from PIL import Image
        im = Image.open(raw).convert("RGB")
        w, h = im.size
        if w > 1600:
            im = im.resize((1600, int(h * 1600 / w)))
        im.save(IMG_DIR / f"{slug}.jpg", "JPEG", quality=82, optimize=True)
        raw.unlink(missing_ok=True)
        return f"{slug}.jpg"
    except Exception as e:
        print(f"  image save err: {e}"); return None


# ──────────────────── Сборка HTML ────────────────────
CSS = """:root{--bg:#06080d;--bg-card:#0d1117;--border:#1e2633;--text:#e6edf3;--text-muted:#8b949e;--text-dim:#484f58;--accent:#7c5cfc;--accent-light:#9d85fd;--green:#3fb950;--red:#f85149}*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.75}.wrap{max-width:780px;margin:0 auto;padding:60px 24px 100px}.back{color:var(--accent-light);text-decoration:none;font-size:14px}h1{font-size:42px;font-weight:800;margin:32px 0 12px;line-height:1.2}.meta{font-size:13px;color:var(--text-dim);margin-bottom:24px}.hero{width:100%;border-radius:16px;overflow:hidden;margin:8px 0 32px;border:1px solid var(--border)}.hero img{width:100%;display:block;aspect-ratio:16/9;object-fit:cover}.lead{font-size:18px;color:var(--text-muted);margin-bottom:32px;border-top:1px solid var(--border);padding-top:24px}h2{font-size:28px;font-weight:700;margin:48px 0 16px}h3{font-size:20px;font-weight:600;margin:32px 0 12px;color:var(--accent-light)}p,li{font-size:16px;margin-bottom:16px}a{color:var(--accent-light)}ul,ol{padding-left:24px;margin-bottom:20px}li{margin-bottom:8px}strong{color:var(--text);font-weight:700}.toc{background:rgba(255,255,255,0.02);border-left:3px solid var(--accent);padding:18px 24px;border-radius:8px;margin-bottom:40px}.toc-title{font-size:13px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px}.toc ol{padding-left:20px;margin:0}.toc li{margin-bottom:4px;font-size:14px}.toc a{color:var(--text);text-decoration:none}.cta{background:linear-gradient(135deg,rgba(124,92,252,.15),rgba(168,85,247,.08));border:2px solid var(--accent);border-radius:16px;padding:32px;margin:48px 0 24px;text-align:center}.cta h3{font-size:24px;color:var(--text);margin-bottom:12px}.cta p{font-size:15px;color:var(--text-muted);margin-bottom:20px}.cta-btn{background:linear-gradient(135deg,#7c5cfc,#a855f7);color:#fff;padding:16px 36px;border-radius:12px;text-decoration:none;font-weight:700;font-size:16px;display:inline-block}.faq-q{font-weight:700;color:var(--text);margin:24px 0 6px}@media(max-width:600px){h1{font-size:30px}h2{font-size:22px}}"""

METRIKA = ("<script type=\"text/javascript\">(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};"
           "m[i].l=1*new Date();for(var j=0;j<document.scripts.length;j++){if(document.scripts[j].src===r){return;}}"
           "k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)})"
           f"(window,document,'script','https://mc.yandex.ru/metrika/tag.js?id={METRIKA_ID}','ym');"
           f"ym({METRIKA_ID},'init',{{ssr:true,webvisor:true,clickmap:true,accurateTrackBounce:true}});</script>")

MONTHS = ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def today_human(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(d)} {MONTHS[int(m)-1]} {y}"


def est_read(html: str) -> int:
    return max(3, round(len(html.split()) / 180))


def render_html(data: dict, slug: str, img: str, today: str) -> str:
    url = f"{SITE}/blog/{slug}.html"; img_url = f"{SITE}/blog/img/{img}"
    toc = "".join(f'<li><a href="#s{i+1}">{esc(t)}</a></li>' for i, t in enumerate(data.get("toc", [])))
    faq_html = "".join(f'<div class="faq-q">{esc(f["q"])}</div><p>{esc(f["a"])}</p>' for f in data.get("faq", []))
    faq_schema = json.dumps({"@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": f["q"],
                        "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in data.get("faq", [])]},
        ensure_ascii=False)
    art_schema = json.dumps({"@context": "https://schema.org", "@type": "Article",
        "headline": data["title"], "description": data["meta_description"], "image": img_url,
        "author": {"@type": "Organization", "name": "VideoAI"},
        "publisher": {"@type": "Organization", "name": "VideoAI", "logo": {"@type": "ImageObject", "url": f"{SITE}/og-image.png"}},
        "datePublished": today, "dateModified": today,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url}}, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(data['title'])} — VideoAI</title>
<meta name="description" content="{esc(data['meta_description'])}">
<meta name="keywords" content="{esc(data.get('meta_keywords',''))}">
<meta name="robots" content="index,follow">
<link rel="canonical" href="{url}">
<meta property="og:title" content="{esc(data['title'])}">
<meta property="og:description" content="{esc(data['meta_description'])}">
<meta property="og:type" content="article">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{img_url}">
<meta property="article:published_time" content="{today}T00:00:00+03:00">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
{METRIKA}
<script type="application/ld+json">{art_schema}</script>
<script type="application/ld+json">{faq_schema}</script>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<a href="./" class="back">← Блог VideoAI</a>
<h1>{esc(data['title'])}</h1>
<div class="meta">{today_human(today)} · {est_read(data.get('body_html',''))} мин чтения · VideoAI</div>
<figure class="hero"><img src="img/{img}" alt="{esc(data['title'])}" loading="lazy"></figure>
<p class="lead">{data['lead']}</p>
<div class="toc"><div class="toc-title">📋 В этой статье</div><ol>{toc}</ol></div>
{data['body_html']}
<div class="cta"><h3>Оживите фото бесплатно</h3><p>Без регистрации. Первое видео — бесплатно. Загрузите снимок — получите живое видео за 60 секунд.</p><a href="/#generator" class="cta-btn">Создать видео из фото →</a></div>
<h2>Частые вопросы</h2>{faq_html}
</div>
</body>
</html>"""


def update_index(slug: str, title: str, excerpt: str, today: str) -> None:
    idx = BLOG_DIR / "index.html"
    html = idx.read_text(encoding="utf-8")
    card = (f'        <div class="post-list">\n'
            f'            <a href="{slug}.html" class="post-card">\n'
            f'                <div class="post-meta">{today_human(today)} · {est_read(excerpt)} мин чтения</div>\n'
            f'                <div class="post-title">{esc(title)}</div>\n'
            f'                <div class="post-excerpt">{esc(excerpt)}</div>\n'
            f'                <div class="post-tags"><span class="post-tag">Память</span><span class="post-tag">Семья</span></div>\n'
            f'            </a>')
    html = html.replace('        <div class="post-list">', card, 1)
    idx.write_text(html, encoding="utf-8")


# ──────────────────── git publish ────────────────────
def git_publish(slugs: list[str]) -> None:
    def g(*a):
        return subprocess.run(["git", "-C", str(REPO_DIR)] + list(a), capture_output=True, text=True, timeout=90)
    g("pull", "--rebase")
    g("add", "blog/")
    r = g("commit", "-m", "feat(seo): автостатьи — " + ", ".join(slugs))
    if r.returncode != 0:
        print(f"  git commit: {r.stdout.strip() or r.stderr.strip()}"); return
    p = g("push", "origin", "master")
    print("  git push:", "OK" if p.returncode == 0 else p.stderr.strip()[:200])


# ──────────────────── main ────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--count", type=int, default=N_DEFAULT)
    args = ap.parse_args()

    if not OPENAI_KEY or not PIAPI_KEY:
        sys.exit("НЕТ OPENAI_API_KEY или PIAPI_KEY в .env — генерация невозможна.")

    today = subprocess.run(["date", "+%Y-%m-%d"], capture_output=True, text=True).stdout.strip()
    kws = pick_keywords(args.count)
    if not kws:
        print("Свободных ключей в seo_keywords_queue нет — добавь ключи. Выходим."); return
    print(f"К генерации: {len(kws)} статей ({today})")

    published = []
    for k in kws:
        print(f"\n→ «{k['keyword']}» ({k['slug']})")
        data = gpt_write(k["keyword"])
        if not data or not data.get("title") or not data.get("body_html"):
            print("  пропуск: GPT не дал валидную статью"); continue
        img = nano_banana(data.get("image_prompt", k["keyword"]), k["slug"])
        if not img:
            print("  пропуск: нет картинки Nano Banana (ПРАВИЛО: без картинки не публикуем)"); continue
        html = render_html(data, k["slug"], img, today)
        if args.dry_run:
            (REPO_DIR / f"_preview_{k['slug']}.html").write_text(html, encoding="utf-8")
            print(f"  [dry-run] превью + картинка {img}"); continue
        (BLOG_DIR / f"{k['slug']}.html").write_text(html, encoding="utf-8")
        excerpt = data["lead"].replace("<strong>", "").replace("</strong>", "")[:180]
        update_index(k["slug"], data["title"], excerpt, today)
        mark_used(k["id"])
        save_draft(k["slug"], k["keyword"], data["title"], data["meta_description"], len(data["body_html"].split()))
        published.append(k["slug"])
        print("  ✓ статья + картинка готовы")

    if published and not args.dry_run:
        git_publish(published)
    print(f"\nИтог: опубликовано {len(published)} статей.")


if __name__ == "__main__":
    main()

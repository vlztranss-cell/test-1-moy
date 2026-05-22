#!/usr/bin/env python3
"""
Генерирует вариации одного видео для соцсетей.

ВАЖНО: ffmpeg static от johnvansickle НЕ имеет drawtext-фильтра.
Поэтому текст-хук рендерим в PNG через Pillow и overlay'им через ffmpeg.

Из 1 видео делает 3 вариации с разными текст-хуками:
- Формат: 1080x1920 (9:16, Shorts/Reels/TikTok/VK Clips)
- Фон: blurred-scale исходника
- Текст-хук: PNG с прозрачным фоном, оверлей сверху
- Watermark «botisk.ru»: PNG в правом нижнем
- Целевые файлы: /srv/creatives/processed/

Использование как модуль:
    from creative_variator import variate
    files = variate(source_video="/path.mp4", category="memory",
                    hooks=["Хук 1", "Хук 2", "Хук 3"])
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FFMPEG = "/usr/local/bin/ffmpeg"
WATERMARK_PNG = "/srv/watermark/watermark.png"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
PROCESSED_DIR = Path("/srv/creatives/processed")
HOOK_TMP_DIR = Path("/tmp/variator_hooks")

TARGET_W, TARGET_H = 1080, 1920


def render_hook_png(text: str, out_path: Path,
                     width: int = TARGET_W,
                     font_size: int = 64,
                     pad_x: int = 40, pad_y: int = 28,
                     bg_opacity: int = 165,
                     line_spacing: int = 12) -> Path:
    """
    Рендерит текстовый хук в PNG: чёрная полупрозрачная плашка + белый текст.
    Поддерживает многострочный текст (перенос по словам если шире TARGET_W - 80).
    """
    HOOK_TMP_DIR.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(FONT_BOLD, font_size)
    max_text_w = width - 80  # отступы по бокам

    # Простой word-wrap
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        candidate = " ".join(cur + [w])
        bbox = font.getbbox(candidate)
        if bbox[2] - bbox[0] <= max_text_w:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))

    # Размер плашки
    line_heights = []
    line_widths = []
    for line in lines:
        bbox = font.getbbox(line)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    text_w = max(line_widths) if line_widths else 0
    text_h = sum(line_heights) + line_spacing * (len(lines) - 1) if lines else 0
    box_w = text_w + pad_x * 2
    box_h = text_h + pad_y * 2

    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, bg_opacity))
    draw = ImageDraw.Draw(img)

    # Текст по центру
    y_cursor = pad_y
    for i, line in enumerate(lines):
        bbox = font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        x = (box_w - line_w) // 2
        # ofset y по bbox.top — иначе текст слишком высоко
        draw.text((x, y_cursor - bbox[1]), line, font=font, fill=(255, 255, 255, 240))
        y_cursor += line_heights[i] + line_spacing

    img.save(out_path, "PNG")
    return out_path


def variate(source_video: str | Path, category: str, hooks: list[str]) -> list[dict]:
    """Из 1 видео + N хуков → N готовых вариаций под Shorts."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    HOOK_TMP_DIR.mkdir(parents=True, exist_ok=True)
    source = Path(source_video)
    if not source.exists():
        raise FileNotFoundError(source)

    base_id = source.stem
    results = []
    for i, hook in enumerate(hooks):
        ts = int(time.time())
        # 1. Рендерим PNG-хук
        hook_png = HOOK_TMP_DIR / f"hook_{base_id}_{i}_{ts}.png"
        render_hook_png(hook, hook_png)

        # 2. ffmpeg: blurred bg + scaled video + watermark + hook PNG
        out_file = PROCESSED_DIR / f"{base_id}_{category}_{i}_{ts}.mp4"

        # filter_complex:
        # [0] = source video
        # [1] = watermark png
        # [2] = hook png
        filter_complex = (
            f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[scaled];"
            f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},gblur=sigma=20[bg];"
            f"[bg][scaled]overlay=(W-w)/2:(H-h)/2[base];"
            f"[base][1:v]overlay=W-w-20:H-h-20[wm];"
            f"[wm][2:v]overlay=(W-w)/2:H*0.08[out]"
        )
        cmd = [
            FFMPEG, "-y",
            "-i", str(source),
            "-i", WATERMARK_PNG,
            "-i", str(hook_png),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-r", "30",
            "-t", "10",
            str(out_file),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")[-500:]
                print(f"❌ hook='{hook}': ffmpeg returncode={result.returncode}\n{err}", file=sys.stderr)
                continue
        except subprocess.TimeoutExpired:
            print(f"❌ timeout for hook='{hook}'", file=sys.stderr)
            continue
        finally:
            try: hook_png.unlink()
            except: pass

        results.append({
            "path": str(out_file),
            "filename": out_file.name,
            "size": out_file.stat().st_size,
            "hook": hook,
            "category": category,
        })
        print(f"✅ {out_file.name} ({out_file.stat().st_size/1e6:.1f} MB)")

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--category", required=True, choices=["memory", "babies", "pets", "love"])
    ap.add_argument("--hooks", nargs="+", required=True)
    args = ap.parse_args()

    results = variate(args.source, args.category, args.hooks)
    print(f"\n✓ Создано {len(results)} вариаций")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

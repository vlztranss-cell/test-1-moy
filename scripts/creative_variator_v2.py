#!/usr/bin/env python3
"""
Creative Variator v2 — формат «до-после-CTA» для понятности видео.

Структура 12-сек ролика:
  0-2.5с:  СТАТИЧНОЕ исходное фото с подписью «ВАШЕ ФОТО» (берём первый кадр Kling-видео)
  2.5-7.5с: видео оживления (5 сек) с подписью «AI ОЖИВЛЯЕТ»
  7.5-12с: CTA-экран «Попробуй сам → botisk.ru / @VideoAI_24isk_bot»

Цель: зритель должен за первые 3 секунды понять
  «Это сервис который оживляет ЛИЧНЫЕ фотографии».

Использование:
    from creative_variator_v2 import variate_v2
    files = variate_v2(source_video="...", category="memory", hooks=["...", "...", "..."])
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FFMPEG = "/usr/local/bin/ffmpeg"
WATERMARK_PNG = "/srv/watermark/watermark.png"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
PROCESSED_DIR = Path("/srv/creatives/processed_v2")
TMP_DIR = Path("/tmp/variator_v2")

TARGET_W, TARGET_H = 1080, 1920

# Время кадров (секунды)
T_BEFORE = 2.5   # длительность статичного «до»
T_AFTER = 5.0    # длительность оживления (Kling видео обычно 5 сек)
T_CTA = 4.5      # длительность CTA-экрана


def render_text_png(text: str, out_path: Path, *,
                    width: int = TARGET_W,
                    max_text_width_ratio: float = 0.85,
                    font_size: int = 72,
                    pad_x: int = 50, pad_y: int = 36,
                    bg_color: tuple = (0, 0, 0, 200),
                    text_color: tuple = (255, 255, 255, 250),
                    line_spacing: int = 14) -> Path:
    """Рендерит текст в PNG с прозрачным фоном (плашка). Word-wrap."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(FONT_BOLD, font_size)
    max_text_w = int(width * max_text_width_ratio)

    # Word-wrap
    words = text.split()
    lines = []
    cur = []
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

    # Размеры
    line_metrics = [font.getbbox(line) for line in lines]
    line_heights = [m[3] - m[1] for m in line_metrics]
    line_widths = [m[2] - m[0] for m in line_metrics]
    text_w = max(line_widths) if line_widths else 0
    text_h = sum(line_heights) + line_spacing * max(0, len(lines) - 1)
    box_w = text_w + pad_x * 2
    box_h = text_h + pad_y * 2

    img = Image.new("RGBA", (box_w, box_h), bg_color)
    draw = ImageDraw.Draw(img)

    y_cursor = pad_y
    for i, line in enumerate(lines):
        bbox = line_metrics[i]
        line_w = bbox[2] - bbox[0]
        x = (box_w - line_w) // 2
        draw.text((x, y_cursor - bbox[1]), line, font=font, fill=text_color)
        y_cursor += line_heights[i] + line_spacing

    img.save(out_path, "PNG")
    return out_path


def render_cta_png(out_path: Path) -> Path:
    """Полноэкранный CTA-плакат 1080x1920 (для финала ролика)."""
    img = Image.new("RGBA", (TARGET_W, TARGET_H), (10, 12, 20, 255))
    draw = ImageDraw.Draw(img)

    # Заголовок (большой)
    font_xl = ImageFont.truetype(FONT_BOLD, 120)
    font_l = ImageFont.truetype(FONT_BOLD, 84)
    font_m = ImageFont.truetype(FONT_BOLD, 60)

    # 1) "ОЖИВИТЕ" — большой
    title = "ОЖИВИТЕ"
    bbox = font_xl.getbbox(title)
    w = bbox[2] - bbox[0]
    draw.text(((TARGET_W - w) // 2, 320), title, font=font_xl, fill=(255, 255, 255, 255))

    # 2) "ВАШЕ ФОТО" — большой
    sub = "ВАШЕ ФОТО"
    bbox = font_xl.getbbox(sub)
    w = bbox[2] - bbox[0]
    draw.text(((TARGET_W - w) // 2, 470), sub, font=font_xl, fill=(168, 132, 252, 255))

    # 3) Разделитель
    draw.rectangle([(TARGET_W // 2 - 200, 670), (TARGET_W // 2 + 200, 678)],
                    fill=(124, 92, 252, 255))

    # 4) Бесплатно
    sub2 = "БЕСПЛАТНО"
    bbox = font_l.getbbox(sub2)
    w = bbox[2] - bbox[0]
    draw.text(((TARGET_W - w) // 2, 740), sub2, font=font_l, fill=(63, 185, 80, 255))

    # 5) URL
    url = "botisk.ru"
    bbox = font_l.getbbox(url)
    w = bbox[2] - bbox[0]
    draw.text(((TARGET_W - w) // 2, 1100), url, font=font_l, fill=(255, 255, 255, 255))

    # 6) Или Telegram
    tg = "или Telegram"
    bbox = font_m.getbbox(tg)
    w = bbox[2] - bbox[0]
    draw.text(((TARGET_W - w) // 2, 1230), tg, font=font_m, fill=(139, 148, 158, 255))

    tg_bot = "@VideoAI_24isk_bot"
    bbox = font_m.getbbox(tg_bot)
    w = bbox[2] - bbox[0]
    draw.text(((TARGET_W - w) // 2, 1310), tg_bot, font=font_m, fill=(88, 166, 255, 255))

    img.save(out_path, "PNG")
    return out_path


def extract_first_frame(video_path: Path, out_path: Path) -> Path:
    """Извлекает первый кадр видео как картинку для «до»."""
    subprocess.run([
        FFMPEG, "-y", "-i", str(video_path),
        "-ss", "00:00:00.0", "-frames:v", "1",
        str(out_path)
    ], capture_output=True, timeout=30)
    return out_path


def variate_v2(source_video: str | Path, category: str, hooks: list[str]) -> list[dict]:
    """
    Из 1 Kling-видео делает N роликов с форматом «до→после→CTA».
    Каждый ролик использует свой текст-хук в верхней плашке во время «после».
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    source = Path(source_video)
    if not source.exists():
        raise FileNotFoundError(source)

    base_id = source.stem

    # 1. Извлекаем первый кадр видео — это будет «ваше фото»
    first_frame = TMP_DIR / f"frame_{base_id}.jpg"
    extract_first_frame(source, first_frame)

    # 2. Рендерим неизменяемые элементы (общие для всех вариаций)
    label_before = TMP_DIR / f"label_before_{base_id}.png"
    render_text_png("ВАШЕ ФОТО", label_before, font_size=80,
                     bg_color=(0, 0, 0, 180),
                     text_color=(255, 255, 255, 250))

    label_after = TMP_DIR / f"label_after_{base_id}.png"
    render_text_png("AI ОЖИВЛЯЕТ", label_after, font_size=80,
                     bg_color=(124, 92, 252, 200),
                     text_color=(255, 255, 255, 250))

    cta_image = TMP_DIR / f"cta_{base_id}.png"
    render_cta_png(cta_image)

    results = []
    for i, hook in enumerate(hooks):
        ts = int(time.time())
        out_file = PROCESSED_DIR / f"{base_id}_v2_{category}_{i}_{ts}.mp4"

        # Хук рендерим персонально под каждую вариацию
        hook_png = TMP_DIR / f"hook_{base_id}_{i}_{ts}.png"
        render_text_png(hook, hook_png, font_size=64,
                         bg_color=(0, 0, 0, 200))

        # filter_complex:
        # [0:v] — source video (Kling)
        # [1:v] — first_frame.jpg
        # [2:v] — watermark.png
        # [3:v] — label_before.png
        # [4:v] — label_after.png
        # [5:v] — cta_image.png (1080x1920 full)
        # [6:v] — hook_png

        # Шаги:
        # part1: статика 2.5сек из first_frame + label_before + hook (внизу)
        # part2: видео 5сек + label_after + hook (внизу) + watermark
        # part3: CTA-экран 4.5сек
        # concat все 3 части

        # Делаем все парты отдельным проходом и склеиваем — проще

        # PART 1: «до» — статичное изображение
        part1 = TMP_DIR / f"p1_{base_id}_{i}_{ts}.mp4"
        f1 = (
            f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[fg];"
            f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},gblur=sigma=20[bg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
            f"[base][1:v]overlay=20:(H-h)/2-200[lbl];"   # label_before в левой части
            f"[lbl][2:v]overlay=(W-w)/2:H-h-60[out]"     # hook внизу по центру
        )
        subprocess.run([
            FFMPEG, "-y",
            "-loop", "1", "-t", str(T_BEFORE), "-i", str(first_frame),
            "-loop", "1", "-t", str(T_BEFORE), "-i", str(label_before),
            "-loop", "1", "-t", str(T_BEFORE), "-i", str(hook_png),
            "-filter_complex", f1,
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-t", str(T_BEFORE),
            str(part1)
        ], capture_output=True, timeout=60)

        # PART 2: видео оживления + label_after + hook + watermark
        part2 = TMP_DIR / f"p2_{base_id}_{i}_{ts}.mp4"
        f2 = (
            f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[scaled];"
            f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},gblur=sigma=20[bg];"
            f"[bg][scaled]overlay=(W-w)/2:(H-h)/2[base];"
            f"[base][1:v]overlay=20:(H-h)/2-200[lbl];"           # label_after слева
            f"[lbl][2:v]overlay=W-w-20:H-h-20[wm];"               # watermark правый нижний
            f"[wm][3:v]overlay=(W-w)/2:H-h-60[out]"               # hook внизу
        )
        subprocess.run([
            FFMPEG, "-y",
            "-i", str(source),
            "-i", str(label_after),
            "-i", str(WATERMARK_PNG),
            "-i", str(hook_png),
            "-filter_complex", f2,
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-t", str(T_AFTER),
            str(part2)
        ], capture_output=True, timeout=120)

        # PART 3: CTA-экран
        part3 = TMP_DIR / f"p3_{base_id}_{i}_{ts}.mp4"
        subprocess.run([
            FFMPEG, "-y",
            "-loop", "1", "-t", str(T_CTA), "-i", str(cta_image),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-t", str(T_CTA),
            str(part3)
        ], capture_output=True, timeout=30)

        # CONCAT 3 частей
        concat_list = TMP_DIR / f"concat_{base_id}_{i}_{ts}.txt"
        concat_list.write_text(
            f"file '{part1}'\nfile '{part2}'\nfile '{part3}'\n",
            encoding="utf-8"
        )
        result = subprocess.run([
            FFMPEG, "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-movflags", "+faststart",
            str(out_file)
        ], capture_output=True, timeout=60)

        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")[-500:]
            print(f"  ❌ concat for hook='{hook}': {err}", file=sys.stderr)
            continue

        # Cleanup временных part-файлов
        for tmp in (part1, part2, part3, concat_list, hook_png):
            try: tmp.unlink()
            except: pass

        results.append({
            "path": str(out_file),
            "filename": out_file.name,
            "size": out_file.stat().st_size,
            "hook": hook,
            "category": category,
            "format": "v2_before_after_cta",
        })
        print(f"✅ {out_file.name} ({out_file.stat().st_size / 1e6:.1f} MB, {T_BEFORE + T_AFTER + T_CTA}с)")

    # Cleanup общих файлов
    for tmp in (first_frame, label_before, label_after, cta_image):
        try: tmp.unlink()
        except: pass

    return results


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--category", required=True, choices=["memory", "babies", "pets", "love"])
    ap.add_argument("--hooks", nargs="+", required=True)
    args = ap.parse_args()
    results = variate_v2(args.source, args.category, args.hooks)
    print(json.dumps(results, ensure_ascii=False, indent=2))

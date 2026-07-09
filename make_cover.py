"""Генерація обкладинки для Telegram Mini App (640x360).

Скрипт створює стильну темну обкладинку з градієнтом у стилі посилання
(фіолетово-сині акценти, тема фокусу).

Запуск:
    python make_cover.py
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pathlib import Path

W, H = 640, 360
OUT = Path(__file__).parent / "static" / "cover.png"


def lerp(a, b, t):
    return int(a + (b - a) * t)


def gradient_vertical(d, w, h, top, bottom):
    for y in range(h):
        t = y / (h - 1)
        d.line([(0, y), (w, y)], fill=(lerp(top[0], bottom[0], t),
                                      lerp(top[1], bottom[1], t),
                                      lerp(top[2], bottom[2], t)))


def gradient_horizontal(d, w, h, left, right):
    for x in range(w):
        t = x / (w - 1)
        d.line([(x, 0), (x, h)], fill=(lerp(left[0], right[0], t),
                                       lerp(left[1], right[1], t),
                                       lerp(left[2], right[2], t)))


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H), (10, 10, 18))
    draw = ImageDraw.Draw(img)

    # Базовий діагональний градієнт: глибокий фіолетово-синій
    gradient_horizontal(draw, W, H, (20, 16, 48), (8, 10, 24))

    # Світлі плями (glow) — імітація неонових акцентів
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 220, -120, W + 120, 200], fill=(124, 92, 252))   # фіолет
    gd.ellipse([-140, H - 180, 220, H + 140], fill=(61, 220, 151))   # зелений
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    img = Image.blend(img, glow, 0.35)
    draw = ImageDraw.Draw(img)

    # Тонка сітка-лінії для «технічного» відчуття
    grid_color = (255, 255, 255)
    for x in range(0, W, 40):
        draw.line([(x, 0), (x, H)], fill=grid_color, width=1)
        # зменшуємо прозорість через overlay
    overlay = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.blend(img, overlay, 0.88)
    draw = ImageDraw.Draw(img)

    # Перерендеримо градієнт поверх, бо blend затемнив
    gradient_horizontal(draw, W, H, (20, 16, 48), (8, 10, 24))
    # glow знову
    glow2 = Image.new("RGB", (W, H), (0, 0, 0))
    gd2 = ImageDraw.Draw(glow2)
    gd2.ellipse([W - 220, -120, W + 120, 200], fill=(124, 92, 252))
    gd2.ellipse([-140, H - 180, 220, H + 140], fill=(61, 220, 151))
    glow2 = glow2.filter(ImageFilter.GaussianBlur(90))
    img = Image.blend(img, glow2, 0.4)
    draw = ImageDraw.Draw(img)

    # Коло таймера в центрі
    cx, cy, r = W // 2, H // 2 + 10, 62
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(124, 92, 252), width=4)
    # прогрес-дуга (зелена, ~70%)
    import math
    bbox = [cx - r, cy - r, cx + r, cy + r]
    # малюємо дугу через pieslice з прозорістю не вийде в RGB; використаємо overlay
    prog = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(prog)
    pd.arc(bbox, -90, -90 + 252, fill=(61, 220, 151, 255), width=5)
    img.paste(prog, (0, 0), prog)
    draw = ImageDraw.Draw(img)

    # Заголовок "FOCUS OS"
    title_font = get_font(54, bold=True)
    sub_font = get_font(20, bold=False)
    tag_font = get_font(13, bold=True)

    title = "FOCUS OS"
    tw = draw.textlength(title, font=title_font)
    draw.text(((W - tw) / 2, 30), title, font=title_font, fill=(255, 255, 255))

    subtitle = "Глибока робота. Без відволікань."
    sw = draw.textlength(subtitle, font=sub_font)
    draw.text(((W - sw) / 2, 92), subtitle, font=sub_font, fill=(180, 180, 210))

    # Плашка-тег знизу
    tag = "DEEP WORK   ·   FOCUS   ·   BREAK"
    tw2 = draw.textlength(tag, font=tag_font)
    pad_x, pad_y = 14, 7
    box_w = int(tw2) + pad_x * 2
    box_h = 13 + pad_y * 2
    bx = (W - box_w) // 2
    by = H - box_h - 26
    draw.rounded_rectangle(
        [bx, by, bx + box_w, by + box_h], radius=box_h // 2,
        fill=(124, 92, 252),
    )
    draw.text((bx + pad_x, by + pad_y - 1), tag, font=tag_font, fill=(255, 255, 255))

    img.save(OUT, "PNG")
    print(f"✅ Збережено: {OUT}  ({W}x{H})")


if __name__ == "__main__":
    main()

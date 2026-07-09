"""Генерація аватара бота Focus OS — 'Режим Бога' / gamma 40Hz вайб.

Концепція: темний нейро-кібернетичний фон, що світиться кільце-таймер
(активація мозку), пульсуючі гамма-хвилі. Мінімалістично, Apple-style.
"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import math

SIZE = 1000


def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def main():
    img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    cx, cy = SIZE // 2, SIZE // 2

    # --- Фон: радіальний градієнт від чорного до глибокого фіолетового/синього ---
    for r in range(SIZE // 2, 0, -2):
        t = 1 - (r / (SIZE // 2))
        # чорний (0,0,0) → глибокий індиго (30, 15, 60)
        col = lerp_color((8, 5, 15), (45, 20, 80), t ** 1.6)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)

    # --- Гамма-хвилі: концентричні кільця, що світяться (40Hz пульс) ---
    for i, rr in enumerate(range(180, 430, 28)):
        alpha = int(70 * (1 - i * 0.12))
        if alpha < 6:
            break
        col = (130, 90, 255, alpha)
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=col, width=2)
    # розмиття для glow
    glow = img.filter(ImageFilter.GaussianBlur(radius=6))
    img = Image.blend(img, glow, 0.35)
    draw = ImageDraw.Draw(img, "RGBA")

    # --- Основне кільце-таймер: яскравий акцент (фолетовий→синій градієнт) ---
    ring_r = 250
    # тліюче підкільце (halo)
    for w in range(14, 0, -2):
        a = int(25 * (1 - w / 14))
        draw.ellipse([cx - ring_r - w, cy - ring_r - w, cx + ring_r + w, cy + ring_r + w],
                     outline=(140, 100, 255, a), width=2)
    # прогрес-дуга (270° — "майже готово", режим бога активовано)
    arc_box = [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r]
    # фонове кільце
    draw.arc(arc_box, start=0, end=360, fill=(60, 40, 100, 180), width=20)
    # прогрес — яскравий градієнтний вигляд (малюємо секторами)
    progress_deg = 290
    for deg in range(0, int(progress_deg), 3):
        t = deg / progress_deg
        col = lerp_color((180, 120, 255), (90, 200, 255), t)
        col = (col[0], col[1], col[2], 255)
        draw.arc(arc_box, start=-90 + deg, end=-90 + deg + 4, fill=col, width=22)

    # glow на прогресі
    progress_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(progress_layer, "RGBA")
    for deg in range(0, int(progress_deg), 3):
        t = deg / progress_deg
        col = lerp_color((180, 120, 255), (90, 200, 255), t)
        pdraw.arc(arc_box, start=-90 + deg, end=-90 + deg + 4, fill=(*col, 120), width=30)
    progress_layer = progress_layer.filter(ImageFilter.GaussianBlur(radius=10))
    img.paste(Image.alpha_composite(img.convert("RGBA"), progress_layer), (0, 0))

    # --- Центр: мовний "мозок-запалення" — пульсуюча точка-ядро ---
    draw = ImageDraw.Draw(img, "RGBA")
    core_r = 70
    for w in range(core_r * 2, 0, -4):
        a = int(90 * (1 - w / (core_r * 2)) ** 2)
        draw.ellipse([cx - w, cy - w, cx + w, cy + w], fill=(160, 110, 255, a))
    # яскраве ядро
    draw.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=(220, 200, 255, 255))
    draw.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], fill=(255, 255, 255, 255))

    # --- Нейронні лінії (синапси) — тонкі промені від ядра ---
    import random
    random.seed(42)
    for ang_deg in range(0, 360, 45):
        ang = math.radians(ang_deg)
        jitter = random.uniform(-0.3, 0.3)
        ang += jitter
        r1, r2 = 80, 210
        x1, y1 = cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)
        x2, y2 = cx + r2 * math.cos(ang), cy + r2 * math.sin(ang)
        # основна лінія
        draw.line([(x1, y1), (x2, y2)], fill=(140, 180, 255, 90), width=2)
        # вузол на кінці
        draw.ellipse([x2 - 5, y2 - 5, x2 + 5, y2 + 5], fill=(160, 200, 255, 180))

    # --- Фінальний glow всього зображення ---
    final = img.convert("RGB")
    soft = final.filter(ImageFilter.GaussianBlur(radius=3))
    final = Image.blend(final, soft, 0.12)

    final.save("static/bot-avatar.png", "PNG", quality=95)
    print("✓ Аватар збережено: static/bot-avatar.png")

    # також варіант 512 для MediaSession artwork
    final.resize((512, 512), Image.LANCZOS).save("static/icon-512.png", "PNG")
    final.resize((256, 256), Image.LANCZOS).save("static/icon-256.png", "PNG")
    final.resize((192, 192), Image.LANCZOS).save("static/icon-192.png", "PNG")
    final.resize((96, 96), Image.LANCZOS).save("static/icon-96.png", "PNG")
    print("✓ Іконки MediaSession оновлено (96/192/256/512)")


if __name__ == "__main__":
    main()

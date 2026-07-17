#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор іконки "Focus ON" для Android.
Створює:
  - res/mipmap-{mdpi..xxxhdpi}/ic_launcher.png         (legacy, 48..192)
  - res/mipmap-{mdpi..xxxhdpi}/ic_launcher_round.png   (round variant)
  - res/mipmap-{mdpi..xxxhdpi}/ic_launcher_foreground.png  (adaptive fg, 108..432)
  - res/mipmap-{mdpi..xxxhdpi}/ic_launcher_background.png  (adaptive bg, 108..432)
  - res/drawable/ic_launcher_background.xml            (vector bg, fallback)
  - res/drawable/ic_launcher_foreground.xml            (vector fg, fallback)
  - res/mipmap-anydpi-v26/ic_launcher.xml              (adaptive spec)
  - res/mipmap-anydpi-v26/ic_launcher_round.xml
  - playstore-icon-512.png                             (Play Console)

Дизайн:
  - Товсте gradient-кільце (cyan #5ac8fa → purple #bf5af2) з розривом зверху (timer-look)
  - Яскраве біле ядро в центрі з м'яким glow
  - Контент у safe-zone (центральні 66%) — ніяка лаунчер-маска не обріже
  - Темний gradient background + ледь помітний starfield
"""
import math
import random
from PIL import Image, ImageDraw, ImageFilter, ImageDraw2

# ── Палітра ─────────────────────────────────────────────────────────
CYAN   = (0x5a, 0xc8, 0xfa)
PURPLE = (0xbf, 0x5a, 0xf2)
WHITE  = (0xff, 0xff, 0xff)
BG_INNER = (0x16, 0x0c, 0x2a)   # темний фіолет
BG_OUTER = (0x07, 0x03, 0x0f)   # майже чорний

RES = "android/app/src/main/res"

# dp → px для adaptive (108dp base) та legacy (48dp base)
LEGACY_SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
ADAPTIVE_SIZES = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_background(size):
    """Gradient #160c2a (центр) → #07030f (краї) + starfield."""
    img = Image.new("RGB", (size, size), BG_OUTER)
    cx = cy = size / 2
    max_r = math.hypot(cx, cy)
    # Радіальний gradient через малі кола (швидко для наших розмірів)
    steps = max(8, size // 2)
    overlay = Image.new("RGB", (size, size), 0)
    od = ImageDraw.Draw(overlay)
    for i in range(steps, 0, -1):
        t = i / steps
        r = max_r * t
        col = lerp(BG_INNER, BG_OUTER, t ** 1.3)
        od.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    img = Image.composite(overlay, img, Image.new("L", (size, size), 255))
    img.paste(overlay, (0, 0))

    # Дрібний starfield для глибини (тільки за safe-zone)
    rng = random.Random(42)
    sz_radius = size * 0.33
    for _ in range(size * size // 900):
        x = rng.randint(0, size - 1)
        y = rng.randint(0, size - 1)
        if math.hypot(x - cx, y - cy) < sz_radius * 0.95:
            continue  # не псуємо центр
        if rng.random() < 0.5:
            b = rng.randint(40, 110)
            img.putpixel((x, y), (b, b, min(255, b + 20)))
    return img


def draw_foreground(size):
    """Прозорий фон + gradient ring + glow core. Контент у safe-zone (центральні 66%)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx = cy = size / 2

    # Safe-zone діаметр = 66% від сторони → радіус safe = size*0.33
    safe_r = size * 0.33
    # Кільце: зовнішній радіус ≈ 82% від safe, товщина ≈ 14% від safe
    ring_outer = safe_r * 0.84
    ring_thickness = max(2.0, safe_r * 0.135)
    ring_inner = ring_outer - ring_thickness
    core_r = ring_inner * 0.42

    # ── 1. Glow навколо ядра (м'яке halation) ──
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    glow_radius = core_r * 3.2
    for i in range(40, 0, -1):
        t = i / 40
        r = glow_radius * t
        alpha = int(70 * (1 - t) ** 2)
        gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0xff, 0xff, 0xff, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=size / 130))
    img.alpha_composite(glow)

    # ── 2. Gradient ring з розривом зверху (timer-style arc) ──
    # Дуга: start_ang..end_ang у system PIL (0° = схід, проти годинникової)
    start_ang = 100
    end_ang = 440   # 340° довжина + 20° розрив зверху-зліва
    total = end_ang - start_ang
    segments = max(60, int(total // 3))

    # Кольоровий gradient-кільце (без маски)
    full_ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    frd = ImageDraw.Draw(full_ring)
    for i in range(segments):
        t0 = i / segments
        a0 = start_ang + total * t0
        a1 = start_ang + total * (i + 1) / segments
        if t0 < 0.5:
            col = lerp(CYAN, lerp(CYAN, WHITE, 0.4), t0 / 0.5)
        else:
            col = lerp(lerp(CYAN, WHITE, 0.4), PURPLE, (t0 - 0.5) / 0.5)
        frd.pieslice(
            [cx - ring_outer, cy - ring_outer, cx + ring_outer, cy + ring_outer],
            a0, a1, fill=col + (255,)
        )

    # Альфа-маска кільця-дуги: зовнішній диск мінус внутрішній диск мінус розрив
    arc_mask = Image.new("L", (size, size), 0)
    amd = ImageDraw.Draw(arc_mask)
    amd.pieslice([cx - ring_outer, cy - ring_outer, cx + ring_outer, cy + ring_outer],
                 start_ang, end_ang, fill=255)
    amd.pieslice([cx - ring_inner, cy - ring_inner, cx + ring_inner, cy + ring_inner],
                 start_ang, end_ang, fill=0)
    # Застосовуємо маску як альфу кольорового шару
    full_ring.putalpha(arc_mask)
    img.alpha_composite(full_ring)

    # ── 3. Вузлики на кільці (3 білі точки) ──
    nd = ImageDraw.Draw(img)
    node_r = ring_thickness * 0.55
    for frac in (0.15, 0.5, 0.85):
        ang_deg = start_ang + total * frac
        ang = math.radians(ang_deg)
        nx = cx + math.cos(ang) * (ring_outer - ring_thickness / 2)
        ny = cy - math.sin(ang) * (ring_outer - ring_thickness / 2)
        nd.ellipse([nx - node_r, ny - node_r, nx + node_r, ny + node_r], fill=WHITE + (235,))

    # ── 4. Біле ядро в центрі ──
    cd2 = ImageDraw.Draw(img)
    cd2.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=WHITE + (255,))

    return img


def make_round_mask(size, radius_ratio=0.5):
    """Маска круга для ic_launcher_round."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    r = size * radius_ratio
    d.rounded_rectangle([0, 0, size, size], radius=r * 0.92, fill=255)
    return mask


def save_legacy(density, size, fg_rgba):
    """ic_launcher.png = background + foreground, обрізаний під круг (legacy)."""
    bg = draw_background(size).convert("RGBA")
    full = Image.alpha_composite(bg, fg_rgba)
    # square variant (ic_launcher.png) — залишаємо як square, лаунчер сам маскує
    out_dir = f"{RES}/mipmap-{density}"
    import os
    os.makedirs(out_dir, exist_ok=True)
    full.convert("RGBA").save(f"{out_dir}/ic_launcher.png")
    # round variant
    rm = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rm.paste(full, (0, 0), mask=Image.new("L", (size, size), 0).__class__(
        size, size, lambda *_: 0) if False else make_round_mask(size))
    rm.save(f"{out_dir}/ic_launcher_round.png")


def main():
    import os
    os.makedirs(RES, exist_ok=True)

    # 1) Adaptive layers на всіх density
    for density, size in ADAPTIVE_SIZES.items():
        out_dir = f"{RES}/mipmap-{density}"
        os.makedirs(out_dir, exist_ok=True)
        bg = draw_background(size)
        bg.save(f"{out_dir}/ic_launcher_background.png")
        fg = draw_foreground(size)
        fg.save(f"{out_dir}/ic_launcher_foreground.png")

    # 2) Legacy ic_launcher / ic_launcher_round (повна іконка = bg + fg)
    # генеруємо на xxxhdpi-масштабі, скейлимо під кожен density
    base_size = 192
    fg_full = draw_foreground(base_size)
    for density, size in LEGACY_SIZES.items():
        save_legacy(density, size, fg_full.resize((size, size), Image.LANCZOS))

    # 3) Adaptive XML specs (anydpi-v26)
    v26 = f"{RES}/mipmap-anydpi-v26"
    os.makedirs(v26, exist_ok=True)
    with open(f"{v26}/ic_launcher.xml", "w", encoding="utf-8") as f:
        f.write(
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<adaptive-icon xmlns:android=\"http://schemas.android.com/apk/res/android\">\n"
            "    <background android:drawable=\"@drawable/ic_launcher_background\"/>\n"
            "    <foreground android:drawable=\"@drawable/ic_launcher_foreground\"/>\n"
            "</adaptive-icon>\n"
        )
    with open(f"{v26}/ic_launcher_round.xml", "w", encoding="utf-8") as f:
        f.write(
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<adaptive-icon xmlns:android=\"http://schemas.android.com/apk/res/android\">\n"
            "    <background android:drawable=\"@drawable/ic_launcher_background\"/>\n"
            "    <foreground android:drawable=\"@drawable/ic_launcher_foreground\"/>\n"
            "</adaptive-icon>\n"
        )

    # 4) Vector fallbacks у drawable/ (якщо mipmap-*-png відсутні на старих пристроях)
    drawable = f"{RES}/drawable"
    os.makedirs(drawable, exist_ok=True)
    with open(f"{drawable}/ic_launcher_background.xml", "w", encoding="utf-8") as f:
        f.write(
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<vector xmlns:android=\"http://schemas.android.com/apk/res/android\"\n"
            "    android:width=\"108dp\" android:height=\"108dp\"\n"
            "    android:viewportWidth=\"108\" android:viewportHeight=\"108\">\n"
            "    <path android:fillColor=\"#160c2a\" android:pathData=\"M0,0h108v108h-108z\"/>\n"
            "</vector>\n"
        )
    # foreground vector: approx (full-bleed, але лаунчер маскує)
    with open(f"{drawable}/ic_launcher_foreground.xml", "w", encoding="utf-8") as f:
        f.write(FOREGROUND_VECTOR)

    # 5) Play Store 512×512 (повна іконка, з padding для безпеки)
    ps_bg = draw_background(512).convert("RGBA")
    ps_fg = draw_foreground(512)
    ps_full = Image.alpha_composite(ps_bg, ps_fg)
    ps_full.convert("RGB").save("playstore-icon-512.png")
    # також круглий варіант для Play (опційно)
    rm = make_round_mask(512)
    round_ps = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    round_ps.paste(ps_full, (0, 0), mask=Image.new("L", (512, 512), 0))
    # правильна round-маска
    rm_img = Image.new("L", (512, 512), 0)
    ImageDraw.Draw(rm_img).ellipse([0, 0, 512, 512], fill=255)
    round_ps = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    round_ps.paste(ps_full, (0, 0), mask=rm_img)
    round_ps.convert("RGB").save("playstore-icon-512-round.png")

    print("✓ Іконки згенеровано:")
    print("  - android/.../res/mipmap-{mdpi..xxxhdpi}/ic_launcher[_round].png")
    print("  - android/.../res/mipmap-{mdpi..xxxhdpi}/ic_launcher_foreground.png")
    print("  - android/.../res/mipmap-{mdpi..xxxhdpi}/ic_launcher_background.png")
    print("  - android/.../res/mipmap-anydpi-v26/ic_launcher[_round].xml")
    print("  - android/.../res/drawable/ic_launcher_{background,foreground}.xml")
    print("  - playstore-icon-512.png + playstore-icon-512-round.png")


# Vector foreground: gradient ring + core, viewport 108×108, content у центрі (54,54)
FOREGROUND_VECTOR = """<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp" android:height="108dp"
    android:viewportWidth="108" android:viewportHeight="108">
    <!-- glow core -->
    <path android:fillColor="#ffffff" android:fillAlpha="0.18"
          android:pathData="M54,30 m-14,0 a14,14 0 1,0 28,0 a14,14 0 1,0 -28,0"/>
    <!-- ring approximation (gradient через group) -->
    <group>
        <path android:fillColor="#5ac8fa"
              android:pathData="M54,28 a26,26 0 1,1 -18,7"/>
        <path android:fillColor="#bf5af2"
              android:pathData="M72,73 a26,26 0 1,1 8,-18"/>
    </group>
    <!-- core -->
    <path android:fillColor="#ffffff"
          android:pathData="M54,46 a8,8 0 1,0 0.01,0 z"/>
</vector>
"""


if __name__ == "__main__":
    main()

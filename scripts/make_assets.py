"""MoneyPilot brand asset generator — "cockpit gauge" mark.

Dev tool; pip install pillow to regenerate (NOT in requirements.txt).

    .venv\\Scripts\\python.exe scripts\\make_assets.py

Renders the brand mark — a dark rounded-square tile carrying a neon-teal
arc gauge (270 degree sweep, fine tick marks) with an amber needle and the
shekel glyph at its heart — at 4x supersampling, then downsamples (Lanczos)
for crisp anti-aliased edges. Palette is lifted from app/ui/app.css :root.

Outputs (app/ui/assets/):
    icon.ico        multi-size 16/24/32/48/64/128/256 (per-size art:
                    16-24px drop ticks + glyph, thicker arc, hub needle)
    icon-256.png    full-detail mark
    favicon-32.png  mid-detail mark
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "app" / "ui" / "assets"

# --- palette (app/ui/app.css :root) -----------------------------------------
BG = (13, 17, 23)          # --bg     #0d1117
PANEL = (20, 27, 38)       # --panel  #141b26
LINE = (38, 51, 74)        # --line   #26334a
ACCENT = (78, 240, 192)    # --accent #4ef0c0
AMBER = (255, 180, 107)    # --amber  #ffb46b
TXT = (199, 214, 234)      # --txt    #c7d6ea

SS = 4  # supersample factor

# Gauge geometry: 270-degree sweep with the gap at the bottom.
# PIL angles: 0 deg = 3 o'clock, increasing clockwise.
ARC_START, ARC_END = 135.0, 405.0
NEEDLE_ANGLE = -52.0  # upper right — "healthy" reading (~70% of the sweep)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/consolab.ttf",   # Consolas Bold — the app's own mono
    "C:/Windows/Fonts/seguisb.ttf",    # Segoe UI Semibold
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
]


def _shekel_font(px: int) -> ImageFont.FreeTypeFont | None:
    """First candidate font whose shekel glyph really renders (not tofu)."""
    for path in FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, px)
        except OSError:
            continue
        mask = font.getmask("₪")
        if mask.getbbox() is None:
            continue
        ink = sum(1 for p in mask if p > 0)
        # .notdef in most fonts is an empty or hollow-box glyph; compare
        # against a guaranteed-unassigned codepoint to reject tofu.
        tofu = font.getmask("͸")
        if tofu.getbbox() is not None and list(mask) == list(tofu):
            continue
        if ink < px:  # nearly no ink — not a usable glyph
            continue
        return font
    return None


def _polar(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def _rounded_mask(s: int, radius: float, inset: float) -> Image.Image:
    m = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([inset, inset, s - 1 - inset, s - 1 - inset],
                        radius=radius, fill=255)
    return m


def _v_gradient(s: int, top: tuple, bottom: tuple) -> Image.Image:
    g = Image.linear_gradient("L").resize((s, s))  # 0 at top -> 255 at bottom
    return Image.composite(Image.new("RGB", (s, s), bottom),
                           Image.new("RGB", (s, s), top), g)


def _radial_glow(s: int, color: tuple, peak: int, radius: float,
                 center: tuple[float, float]) -> Image.Image:
    """Transparent layer with a soft radial glow of `color` at `center`."""
    grad = Image.radial_gradient("L")  # 0 at center -> 255 at edge
    grad = grad.point(lambda v: max(0, peak - v * peak // 255))
    d = int(radius * 2)
    grad = grad.resize((d, d))
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    glow = Image.new("RGBA", (d, d), color + (0,))
    glow.putalpha(grad)
    layer.alpha_composite(glow, (int(center[0] - radius), int(center[1] - radius)))
    return layer


def draw_mark(size: int) -> Image.Image:
    """Render the mark at `size` px (drawn at SS*size, downsampled)."""
    # full: arc + ticks + glyph-at-heart; mid: arc + major ticks + hub
    # needle (a 10px shekel just smears); tiny: thick arc + hub needle.
    detail = "full" if size >= 64 else ("mid" if size >= 32 else "tiny")
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    inset = 0.022 * s
    corner = 0.225 * s
    tile_mask = _rounded_mask(s, corner, inset)

    # tile: subtle vertical gradient, darker at top
    tile = _v_gradient(s, (15, 20, 28), (22, 30, 43)).convert("RGBA")
    img.paste(tile, (0, 0), tile_mask)

    cx, cy = 0.5 * s, 0.53 * s
    R = 0.315 * s

    # radial teal glow behind the gauge, clipped to the tile
    if detail != "tiny":
        glow = _radial_glow(s, ACCENT, peak=26, radius=0.44 * s, center=(cx, cy))
        glow.putalpha(Image.composite(glow.getchannel("A"),
                                      Image.new("L", (s, s), 0), tile_mask))
        img.alpha_composite(glow)

    d = ImageDraw.Draw(img)

    # arc gauge
    arc_w = (0.05 if detail != "tiny" else 0.085) * s
    box = [cx - R, cy - R, cx + R, cy + R]
    # halo pass under the arc
    d.arc([cx - R - arc_w * .55, cy - R - arc_w * .55,
           cx + R + arc_w * .55, cy + R + arc_w * .55],
          ARC_START, ARC_END, fill=ACCENT + (26,), width=int(arc_w * 2.1))
    d.arc(box, ARC_START, ARC_END, fill=ACCENT + (255,), width=int(arc_w))
    for end_deg in (ARC_START, ARC_END):  # round caps
        ex, ey = _polar(cx, cy, R, end_deg)
        r = arc_w / 2
        d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=ACCENT + (255,))

    # tick marks just inside the rim (majors every 45 deg; minors on full)
    if detail != "tiny":
        inner = R - arc_w / 2
        for k in range(13):
            deg = ARC_START + k * 22.5
            major = k % 2 == 0
            if not major and detail != "full":
                continue
            t_out = inner - 0.024 * s
            t_in = t_out - (0.044 if major else 0.026) * s
            w = (0.014 if major else 0.008) * s
            alpha = 190 if major else 100
            d.line([_polar(cx, cy, t_in, deg), _polar(cx, cy, t_out, deg)],
                   fill=ACCENT + (alpha,), width=int(w))

    # amber needle (tapered, with soft under-glow), pivot at the heart
    tip_r = R - arc_w / 2 - 0.012 * s
    tipx, tipy = _polar(cx, cy, tip_r, NEEDLE_ANGLE)
    base_half = (0.022 if detail != "tiny" else 0.05) * s
    bx1, by1 = _polar(cx, cy, base_half, NEEDLE_ANGLE + 90)
    bx2, by2 = _polar(cx, cy, base_half, NEEDLE_ANGLE - 90)
    gx1, gy1 = _polar(cx, cy, base_half * 1.9, NEEDLE_ANGLE + 90)
    gx2, gy2 = _polar(cx, cy, base_half * 1.9, NEEDLE_ANGLE - 90)
    d.polygon([(gx1, gy1), (tipx, tipy), (gx2, gy2)], fill=AMBER + (60,))
    d.polygon([(bx1, by1), (tipx, tipy), (bx2, by2)], fill=AMBER + (255,))

    if detail != "full":
        hub = (0.085 if detail == "tiny" else 0.062) * s
        d.ellipse([cx - hub, cy - hub, cx + hub, cy + hub], fill=AMBER + (255,))
        d.ellipse([cx - hub * .42, cy - hub * .42, cx + hub * .42, cy + hub * .42],
                  fill=(15, 20, 28, 255))
    else:
        # the shekel glyph at the heart, halo'd so the needle reads as
        # passing behind it
        font = _shekel_font(int(0.30 * s))
        if font is not None:
            d.text((cx, cy), "₪", font=font, anchor="mm",
                   fill=(220, 233, 250, 255),
                   stroke_width=int(0.014 * s), stroke_fill=(17, 23, 33, 255))
        else:  # stylized two-stroke fallback (never expected on Windows)
            w = int(0.045 * s)
            h, off = 0.14 * s, 0.075 * s
            d.line([(cx - off, cy - h), (cx - off, cy + h)], fill=TXT, width=w)
            d.arc([cx - off, cy - h, cx + off, cy + h * .2], 180, 360,
                  fill=TXT, width=w)
            d.line([(cx + off, cy - h * .4), (cx + off, cy + h)], fill=TXT,
                   width=w)

    # hairline tile border
    d.rounded_rectangle([inset, inset, s - 1 - inset, s - 1 - inset],
                        radius=corner, outline=LINE + (255,),
                        width=max(1, int(0.012 * s)))

    out = img.resize((size, size), Image.LANCZOS)
    # re-apply a clean mask at the final size to keep corners crisp
    final_mask = _rounded_mask(size, corner / SS, inset / SS)
    out.putalpha(Image.composite(out.getchannel("A"),
                                 Image.new("L", (size, size), 0), final_mask))
    return out


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    marks = {n: draw_mark(n) for n in sizes}

    marks[256].save(ASSETS / "icon-256.png")
    marks[32].save(ASSETS / "favicon-32.png")
    marks[256].save(ASSETS / "icon.ico", format="ICO",
                    sizes=[(n, n) for n in sizes],
                    append_images=[marks[n] for n in sizes if n != 256])

    font = _shekel_font(100)
    src = getattr(font, "path", "stylized fallback") if font else "stylized fallback"
    print(f"shekel glyph source: {src}")
    for p in ("icon.ico", "icon-256.png", "favicon-32.png"):
        print(f"wrote {ASSETS / p}")


if __name__ == "__main__":
    main()

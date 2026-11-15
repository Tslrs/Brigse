"""Draw the chip view as a PNG (for slides). Needs Pillow: uv sync --extra png"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CELL = 18
COLORS = {"C": ("#1b2330", "#6c9cff"), "I": ("#1f2d2a", "#4cc38a"), "B": ("#2e2822", "#f0a050"),
          "D": ("#2b2638", "#b48cff"), "P": ("#352424", "#f0776c")}
BACKGROUND = (0x0F, 0x14, 0x1B)
FONTS = ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf",
         "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _font(size: float):
    for path in FONTS:
        try:
            return ImageFont.truetype(path, round(size))
        except OSError:
            continue
    return ImageFont.load_default()


def render(model: dict, out: Path, scale: int = 2) -> Path:
    """Same picture as the SVG, at `scale` times its size (2 gives a sharp image for a slide)."""
    rows, cols = model["rows"], model["cols"]
    w, h = (cols * CELL + 40) * scale, (rows * CELL + 176) * scale
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=10 * scale, fill=BACKGROUND)
    d.text((20 * scale, 28 * scale), model["label"], font=_font(16 * scale), fill=_rgb("#e6e9ef"), anchor="ls")
    d.text((20 * scale, 46 * scale), "Each square is one tile of the chip. Coloured squares hold part of your design.",
           font=_font(12 * scale), fill=_rgb("#98a2b3"), anchor="ls")
    for t in model["tiles"]:
        empty, full = COLORS[t["k"]]
        x, y = (20 + t["c"] * CELL) * scale, (60 + t["r"] * CELL) * scale
        if t["used"]:
            opacity = 0.45 + 0.55 * t["occ"]
            colour = tuple(round(b + (f - b) * opacity) for b, f in zip(BACKGROUND, _rgb(full)))
        else:
            colour = _rgb(empty)
        d.rounded_rectangle([x, y, x + (CELL - 2) * scale, y + (CELL - 2) * scale], radius=3 * scale, fill=colour)
    ly = 60 + rows * CELL + 22
    for i, (k, text, count) in enumerate(model["legend"]):
        x, y = (20 + (i % 3) * 270) * scale, (ly + (i // 3) * 36) * scale
        d.rounded_rectangle([x, y, x + 14 * scale, y + 14 * scale], radius=3 * scale, fill=_rgb(COLORS[k][1]))
        d.text((x + 22 * scale, y + 11 * scale), text, font=_font(12 * scale), fill=_rgb("#e6e9ef"), anchor="ls")
        d.text((x + 22 * scale, y + 26 * scale), count, font=_font(11 * scale), fill=_rgb("#98a2b3"), anchor="ls")
    d.text((20 * scale, h - 14 * scale), model["footer"], font=_font(11 * scale), fill=_rgb("#667085"), anchor="ls")
    out = Path(out)
    img.save(out)
    return out

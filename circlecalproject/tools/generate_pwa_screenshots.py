"""Generate basic PWA screenshots for the web app manifest.

Chrome can show screenshots during the install prompt when the manifest includes them.
These are intentionally simple "brand" screenshots (not real UI captures) so they
remain stable and always available.

Outputs into: static/icons/screenshots/

Usage:
  python tools/generate_pwa_screenshots.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SRC_LOGO = ROOT / "static" / "icons" / "circlecalicon.png"
OUT_DIR = ROOT / "static" / "icons" / "screenshots"


def _contain(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize to fit inside size, preserving aspect ratio."""
    w, h = img.size
    tw, th = size
    scale = min(tw / w, th / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)


def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 1):
    # Pillow's rounded_rectangle exists; keep compatibility with older patterns.
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _make_screenshot(size: tuple[int, int], logo: Image.Image) -> Image.Image:
    w, h = size

    # Light background so the logo reads well.
    img = Image.new("RGB", (w, h), "#F8FAFC")
    draw = ImageDraw.Draw(img)

    # "Phone" card area (a subtle UI mock).
    pad = int(min(w, h) * 0.08)
    card = (pad, pad, w - pad, h - pad)
    _rounded_rect(draw, card, radius=int(min(w, h) * 0.04), fill="#FFFFFF", outline="#E5E7EB", width=3)

    # Inner content
    inner_pad = int(min(w, h) * 0.07)
    left = card[0] + inner_pad
    top = card[1] + inner_pad
    right = card[2] - inner_pad
    bottom = card[3] - inner_pad

    # Logo (centered)
    logo_target = (int((right - left) * 0.42), int((bottom - top) * 0.42))
    logo_img = _contain(logo, logo_target).convert("RGBA")
    lx = left + (right - left - logo_img.size[0]) // 2
    ly = top + int((bottom - top) * 0.18)
    img.paste(logo_img, (lx, ly), logo_img)

    # Simple UI lines under logo
    line_y = ly + logo_img.size[1] + int((bottom - top) * 0.10)
    line_w = right - left
    for i, frac in enumerate([0.78, 0.62, 0.70]):
        lw = int(line_w * frac)
        x0 = left + (line_w - lw) // 2
        y0 = line_y + i * int(min(w, h) * 0.028)
        draw.rounded_rectangle((x0, y0, x0 + lw, y0 + 10), radius=6, fill="#E5E7EB")

    # Primary button pill
    btn_w = int(line_w * 0.58)
    btn_h = int(min(w, h) * 0.06)
    bx0 = left + (line_w - btn_w) // 2
    by0 = bottom - int(min(w, h) * 0.18) - btn_h
    _rounded_rect(draw, (bx0, by0, bx0 + btn_w, by0 + btn_h), radius=btn_h // 2, fill="#3B82F6")

    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not SRC_LOGO.exists():
        raise SystemExit(f"Missing logo source: {SRC_LOGO}")

    logo = Image.open(SRC_LOGO)

    outputs = {
        "screenshot-1080x1920.png": (1080, 1920),
        "screenshot-1920x1080.png": (1920, 1080),
    }

    for name, size in outputs.items():
        out_path = OUT_DIR / name
        img = _make_screenshot(size, logo)
        img.save(out_path, format="PNG", optimize=True)
        print(f"Wrote {out_path.relative_to(ROOT)} ({size[0]}x{size[1]})")


if __name__ == "__main__":
    main()

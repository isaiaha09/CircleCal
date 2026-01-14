"""Generate CircleCal PWA/app icons.

This avoids external services and produces consistent, brand-colored icons.
Run:
  python tools/generate_circlecal_icons.py

Outputs to:
  static/icons/
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


CIRCLECAL_BLUE = (0x25, 0x63, 0xEB)  # #2563eb
CIRCLECAL_PURPLE = (0x7C, 0x3A, 0xED)  # #7c3aed
WHITE = (255, 255, 255)


def _linear_gradient(size: int, c1: tuple[int, int, int], c2: tuple[int, int, int]) -> Image.Image:
    """Fast diagonal gradient using Pillow primitives."""
    # 'L' gradient left->right, then rotate to get diagonal, then colorize.
    g = Image.linear_gradient("L").resize((size, size), resample=Image.Resampling.BICUBIC)
    g = g.rotate(45, resample=Image.Resampling.BICUBIC, expand=False)
    return ImageOps.colorize(g, black=c1, white=c2)


def _draw_icon(base: Image.Image, *, maskable: bool) -> Image.Image:
    """Draw the CircleCal mark: a circular 'C' + subtle calendar tabs."""
    size = base.size[0]
    img = base.convert("RGBA")

    # Maskable icons need padding (safe zone) so the mark isn't clipped by device masks.
    pad = int(size * (0.16 if maskable else 0.10))

    # Rounded container overlay (slight glass)
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    radius = int(size * 0.22)
    od.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=(255, 255, 255, 18))
    img.alpha_composite(overlay)

    d = ImageDraw.Draw(img)

    # Main ring arc (a 'C')
    cx = cy = size // 2
    ring_bbox = (pad, pad + int(size * 0.04), size - pad, size - pad + int(size * 0.04))
    ring_w = max(12, int(size * 0.085))

    # Soft shadow
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.arc(ring_bbox, start=35, end=325, fill=(0, 0, 0, 70), width=ring_w)
    img.alpha_composite(shadow, dest=(0, int(size * 0.01)))

    # Ring
    d.arc(ring_bbox, start=35, end=325, fill=(255, 255, 255, 235), width=ring_w)

    # Inner highlight ring
    inner_bbox = (ring_bbox[0] + ring_w * 0.55, ring_bbox[1] + ring_w * 0.55, ring_bbox[2] - ring_w * 0.55, ring_bbox[3] - ring_w * 0.55)
    d.arc(inner_bbox, start=40, end=320, fill=(255, 255, 255, 90), width=max(6, int(ring_w * 0.18)))

    # Calendar "tabs" at the top
    tab_w = int(size * 0.11)
    tab_h = int(size * 0.06)
    tab_y = pad + int(size * 0.05)
    tab_gap = int(size * 0.03)
    tab_x1 = cx - tab_w - tab_gap // 2
    tab_x2 = cx + tab_gap // 2
    tab_r = int(tab_h * 0.45)
    for x in (tab_x1, tab_x2):
        d.rounded_rectangle(
            (x, tab_y, x + tab_w, tab_y + tab_h),
            radius=tab_r,
            fill=(255, 255, 255, 210),
        )

    # Small "dot" accent
    dot_r = int(size * 0.035)
    dot_x = int(cx + size * 0.20)
    dot_y = int(cy - size * 0.12)
    d.ellipse((dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r), fill=(255, 255, 255, 235))

    return img


def _save_png(img: Image.Image, path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = img.resize((size, size), resample=Image.Resampling.LANCZOS)
    out.save(path, format="PNG", optimize=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "static" / "icons"

    base = _linear_gradient(1024, CIRCLECAL_BLUE, CIRCLECAL_PURPLE)

    standard = _draw_icon(base, maskable=False)
    maskable = _draw_icon(base, maskable=True)

    # PWA sizes
    _save_png(standard, out_dir / "icon-192.png", 192)
    _save_png(standard, out_dir / "icon-512.png", 512)
    _save_png(maskable, out_dir / "icon-512-maskable.png", 512)

    # iOS / favicon
    _save_png(standard, out_dir / "apple-touch-icon.png", 180)
    _save_png(standard, out_dir / "favicon-32.png", 32)

    print(f"Wrote icons to: {out_dir}")


if __name__ == "__main__":
    main()

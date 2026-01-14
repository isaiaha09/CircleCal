"""Generate CircleCal icon assets based on the "double C" mark.

This produces:
- SVG mark (already in static/icons/circlecal-mark.svg)
- PNG icons for PWA + iOS + Android + favicons
- favicon.ico (multi-size)
- Optional JPEGs (for marketing/preview)

Run:
  python tools/generate_circlecal_logo_icons.py

Outputs to:
  static/icons/
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


# Brand-ish gradient endpoints (approx from provided logo)
C1 = (0x05, 0x05, 0x16)  # near-black indigo
C2 = (0x2C, 0x45, 0xFF)  # bright blue
MID = (0x1F, 0x2A, 0xA6)  # mid indigo
WHITE = (255, 255, 255)


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _lerp_rgb(ca: tuple[int, int, int], cb: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (_lerp(ca[0], cb[0], t), _lerp(ca[1], cb[1], t), _lerp(ca[2], cb[2], t))


def _color_at(t: float) -> tuple[int, int, int]:
    # Piecewise gradient: C1 -> MID -> C2
    if t <= 0.45:
        return _lerp_rgb(C1, MID, t / 0.45 if 0.45 else 0)
    return _lerp_rgb(MID, C2, (t - 0.45) / 0.55 if 0.55 else 0)


def _draw_gradient_arc(
    d: ImageDraw.ImageDraw,
    bbox: tuple[int, int, int, int],
    start_deg: float,
    end_deg: float,
    width: int,
    *,
    steps: int = 240,
):
    # Draw many small arc segments with interpolated color.
    total = end_deg - start_deg
    if total == 0:
        return
    for i in range(steps):
        t0 = i / steps
        t1 = (i + 1) / steps
        a0 = start_deg + total * t0
        a1 = start_deg + total * t1
        # slight overlap to avoid gaps
        col = _color_at(t0)
        d.arc(bbox, start=a0, end=a1 + 0.5, fill=col, width=width)


def _render_base(size: int, *, maskable: bool) -> Image.Image:
    img = Image.new("RGBA", (size, size), WHITE + (255,))
    d = ImageDraw.Draw(img)

    # Padding: maskable icons need more safe-area padding.
    pad = int(size * (0.20 if maskable else 0.08))

    stroke = max(10, int(size * 0.12))

    # C arcs: we want a "C" opening on the right.
    # Pillow arc angles are degrees with 0 at 3 o'clock, increasing counter-clockwise.
    # Draw nearly-full arc but skip a chunk on right side.
    start = 40
    end = 320

    # Top-left C bbox
    b1 = (pad, pad, size - pad, size - pad)

    # Bottom-right C bbox (shifted)
    shift_x = int(size * 0.18)
    shift_y = int(size * 0.22)
    b2 = (pad + shift_x, pad + shift_y, size - pad + shift_x, size - pad + shift_y)

    # Because b2 can overflow the canvas, expand canvas a bit in drawing by clipping via bbox.
    # We'll instead draw onto a larger temp canvas and crop.
    big = int(size * 1.35)
    tmp = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)

    # Recompute boxes in tmp space centered.
    ox = (big - size) // 2
    oy = (big - size) // 2
    b1t = (b1[0] + ox, b1[1] + oy, b1[2] + ox, b1[3] + oy)
    b2t = (b2[0] + ox, b2[1] + oy, b2[2] + ox, b2[3] + oy)

    _draw_gradient_arc(td, b1t, start, end, stroke)
    _draw_gradient_arc(td, b2t, start, end, stroke)

    tmp = tmp.crop((ox, oy, ox + size, oy + size))
    img.alpha_composite(tmp)

    # Slightly round the outside corners for nicer app icon look (subtle).
    # Keep for maskable too; safe area padding prevents clipping.
    r = int(size * 0.12)
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    # Maskable should have fully-opaque background.
    if maskable:
        bg = Image.new("RGBA", (size, size), WHITE + (255,))
        bg.alpha_composite(out)
        return bg

    return out


def _save_png(img: Image.Image, path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = img.resize((size, size), resample=Image.Resampling.LANCZOS)
    out.save(path, format="PNG", optimize=True)


def _save_jpg(img: Image.Image, path: Path, size: int, *, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = img.resize((size, size), resample=Image.Resampling.LANCZOS).convert("RGB")
    out.save(path, format="JPEG", quality=quality, optimize=True, progressive=True)


def _save_ico(img: Image.Image, path: Path, sizes: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base = img.convert("RGBA")
    ico_imgs = [base.resize((s, s), resample=Image.Resampling.LANCZOS) for s in sizes]
    ico_imgs[0].save(path, format="ICO", sizes=[(s, s) for s in sizes])


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "static" / "icons"

    # Render a clean high-res base.
    base = _render_base(1024, maskable=False)
    base_maskable = _render_base(1024, maskable=True)

    # Common favicon sizes
    _save_png(base, out_dir / "favicon-16x16.png", 16)
    _save_png(base, out_dir / "favicon-32x32.png", 32)
    _save_png(base, out_dir / "favicon-48x48.png", 48)
    _save_ico(base, out_dir / "favicon.ico", [16, 32, 48])

    # PWA / Android
    _save_png(base, out_dir / "android-chrome-192x192.png", 192)
    _save_png(base, out_dir / "android-chrome-512x512.png", 512)
    _save_png(base_maskable, out_dir / "android-chrome-512x512-maskable.png", 512)

    # Generic icon set (useful for various platforms)
    for s in [72, 96, 128, 144, 152, 180, 192, 256, 384, 512]:
        _save_png(base, out_dir / f"icon-{s}x{s}.png", s)

    # Apple touch (iOS)
    _save_png(base, out_dir / "apple-touch-icon.png", 180)

    # Optional JPEG previews
    _save_jpg(base, out_dir / "icon-512.jpg", 512)
    _save_jpg(base, out_dir / "icon-192.jpg", 192)

    print(f"Wrote icon assets to: {out_dir}")


if __name__ == "__main__":
    main()

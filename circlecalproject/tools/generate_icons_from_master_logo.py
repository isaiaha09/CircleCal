"""Generate CircleCal icon assets by resizing the *exact* master logo image.

This script does NOT try to redraw the logo. It simply resizes the existing
image (including the original font/gradients) into standard icon sizes and
formats without cropping (adds white padding as needed).

Default master source:
  static/icons/circlecalicon.png

Run:
  python tools/generate_icons_from_master_logo.py

Outputs to:
  static/icons/

Notes:
- Icons are square; if the master image is not square, this centers it on a
  white square canvas (no cutoff).
- A "maskable" Android icon is also produced with extra safe-area padding.
- Also generates an SVG wrapper that embeds the exact PNG bytes (data URI).
"""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SRC_DEFAULT = ROOT / "static" / "icons" / "circlecalicon.png"
OUT_DIR = ROOT / "static" / "icons"


def _square_letterbox(src: Image.Image, size: int, *, safe_pad_ratio: float = 0.0) -> Image.Image:
    """Fit src into a size x size canvas with optional extra safe padding.

    safe_pad_ratio=0.2 means the logo is fit into 80% of the canvas.
    """
    src = src.convert("RGBA")
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    max_w = int(round(size * (1.0 - safe_pad_ratio)))
    max_h = int(round(size * (1.0 - safe_pad_ratio)))
    fitted = src.copy()
    fitted.thumbnail((max_w, max_h), resample=Image.Resampling.LANCZOS)

    x = (size - fitted.size[0]) // 2
    y = (size - fitted.size[1]) // 2
    canvas.alpha_composite(fitted, (x, y))
    return canvas


def _save_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)


def _save_jpg(img: Image.Image, path: Path, *, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, format="JPEG", quality=quality, optimize=True, progressive=True)


def _save_ico(img: Image.Image, path: Path, sizes: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base = img.convert("RGBA")
    ico_imgs = [base.resize((s, s), resample=Image.Resampling.LANCZOS) for s in sizes]
    ico_imgs[0].save(path, format="ICO", sizes=[(s, s) for s in sizes])


def _write_svg_wrapper(png_path: Path, svg_path: Path) -> None:
    png_bytes = png_path.read_bytes()
    b64 = base64.b64encode(png_bytes).decode("ascii")
    # Square viewBox; embed PNG as an <image> so it is pixel-perfect.
    svg = f"""<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"512\" height=\"512\" viewBox=\"0 0 512 512\" role=\"img\" aria-label=\"CircleCal\">
  <image href=\"data:image/png;base64,{b64}\" x=\"0\" y=\"0\" width=\"512\" height=\"512\" preserveAspectRatio=\"xMidYMid meet\"/>
</svg>
"""
    svg_path.write_text(svg, encoding="utf-8")


def main() -> None:
    src_path = SRC_DEFAULT
    if not src_path.exists():
        raise SystemExit(f"Master logo not found: {src_path}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    src = Image.open(src_path)

    # Standard icon sizes
    sizes = [16, 32, 48, 72, 96, 128, 144, 152, 180, 192, 256, 384, 512]

    # Generate standard square icons (no cropping)
    for s in sizes:
        img = _square_letterbox(src, s)
        _save_png(img, OUT_DIR / f"icon-{s}x{s}.png")

    # Platform-named outputs
    _save_png(_square_letterbox(src, 180), OUT_DIR / "apple-touch-icon.png")
    _save_png(_square_letterbox(src, 192), OUT_DIR / "android-chrome-192x192.png")
    _save_png(_square_letterbox(src, 512), OUT_DIR / "android-chrome-512x512.png")

    # Maskable: extra safe-area padding to prevent device masks cutting content.
    maskable_192 = _square_letterbox(src, 192, safe_pad_ratio=0.22)
    maskable_512 = _square_letterbox(src, 512, safe_pad_ratio=0.22)
    _save_png(maskable_192, OUT_DIR / "android-chrome-192x192-maskable.png")
    _save_png(maskable_512, OUT_DIR / "android-chrome-512x512-maskable.png")

    # Also write canonical filenames often referenced by manifests.
    _save_png(_square_letterbox(src, 192), OUT_DIR / "icon-192x192.png")
    _save_png(_square_letterbox(src, 384), OUT_DIR / "icon-384x384.png")
    _save_png(_square_letterbox(src, 512), OUT_DIR / "icon-512x512.png")
    _save_png(maskable_192, OUT_DIR / "icon-192x192-maskable.png")
    _save_png(maskable_512, OUT_DIR / "icon-512x512-maskable.png")

    # Favicons
    _save_png(_square_letterbox(src, 16), OUT_DIR / "favicon-16x16.png")
    _save_png(_square_letterbox(src, 32), OUT_DIR / "favicon-32x32.png")
    _save_png(_square_letterbox(src, 48), OUT_DIR / "favicon-48x48.png")
    _save_ico(_square_letterbox(src, 48), OUT_DIR / "favicon.ico", [16, 32, 48])

    # Optional JPEGs (exact look, just JPEG encoding)
    _save_jpg(_square_letterbox(src, 512), OUT_DIR / "icon-512.jpg")
    _save_jpg(_square_letterbox(src, 192), OUT_DIR / "icon-192.jpg")

    # Replace the primary SVG icon with an exact wrapper around the PNG.
    _write_svg_wrapper(OUT_DIR / "icon-512x512.png", OUT_DIR / "icon.svg")

    print(f"Generated icons from master: {src_path}")
    print(f"Output directory: {OUT_DIR}")


if __name__ == "__main__":
    main()

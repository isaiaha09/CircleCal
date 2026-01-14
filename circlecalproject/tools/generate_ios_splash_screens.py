"""Generate iOS launch images (splash screens) for PWA.

Creates Apple-touch-startup-image PNGs under:
  static/icons/splash/

All splash images are generated from the *exact* master logo image
(static/icons/circlecalicon.png) placed on a solid background.

Run:
  python tools/generate_ios_splash_screens.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "static" / "icons" / "circlecalicon.png"
OUT_DIR = ROOT / "static" / "icons" / "splash"


@dataclass(frozen=True)
class SplashSpec:
    filename: str
    width: int
    height: int


# Common iPhone/iPad launch image pixel sizes (portrait + iPad variants).
# We generate pixel-size images and use them with correct media queries in HTML.
SPLASH_SPECS: list[SplashSpec] = [
    # iPhone SE (1st gen)
    SplashSpec("splash-640x1136.png", 640, 1136),
    # iPhone 8, 7, 6s
    SplashSpec("splash-750x1334.png", 750, 1334),
    # iPhone XR, 11
    SplashSpec("splash-828x1792.png", 828, 1792),
    # iPhone X, XS, 11 Pro
    SplashSpec("splash-1125x2436.png", 1125, 2436),
    # iPhone 8 Plus, 7 Plus
    SplashSpec("splash-1242x2208.png", 1242, 2208),
    # iPhone XS Max, 11 Pro Max
    SplashSpec("splash-1242x2688.png", 1242, 2688),
    # iPhone 12/13/14
    SplashSpec("splash-1170x2532.png", 1170, 2532),
    # iPhone 12/13/14 Pro Max
    SplashSpec("splash-1284x2778.png", 1284, 2778),
    # iPhone 15 Pro Max
    SplashSpec("splash-1290x2796.png", 1290, 2796),
    # iPad Mini/Air
    SplashSpec("splash-1536x2048.png", 1536, 2048),
    SplashSpec("splash-1668x2224.png", 1668, 2224),
    SplashSpec("splash-1668x2388.png", 1668, 2388),
    # iPad Pro
    SplashSpec("splash-2048x2732.png", 2048, 2732),
]


def _letterbox_logo(src: Image.Image, canvas_w: int, canvas_h: int) -> Image.Image:
    """Center the logo on a canvas with white background (no cropping)."""
    src = src.convert("RGBA")
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))

    # Scale logo to a reasonable percentage of the shortest side.
    # iOS launch images look best when the mark isn't edge-to-edge.
    max_w = int(round(min(canvas_w, canvas_h) * 0.60))
    max_h = int(round(min(canvas_w, canvas_h) * 0.60))

    logo = src.copy()
    logo.thumbnail((max_w, max_h), resample=Image.Resampling.LANCZOS)

    x = (canvas_w - logo.size[0]) // 2
    y = (canvas_h - logo.size[1]) // 2
    canvas.alpha_composite(logo, (x, y))

    return canvas


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Master logo not found: {SRC}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    src = Image.open(SRC)

    for spec in SPLASH_SPECS:
        img = _letterbox_logo(src, spec.width, spec.height)
        out_path = OUT_DIR / spec.filename
        img.save(out_path, format="PNG", optimize=True)

    print(f"Generated {len(SPLASH_SPECS)} splash images in: {OUT_DIR}")


if __name__ == "__main__":
    main()

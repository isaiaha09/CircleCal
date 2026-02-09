"""Generate a Google Play Store feature graphic (1024x500) from the app icon.

Google Play requirements (high level):
- 1024 x 500 px
- PNG or JPG
- No transparency (no alpha channel)

This script creates a simple, compliant graphic: white background with the icon
centered and scaled to fit.

Usage:
  D:/CircleCal/.venv/Scripts/python.exe circlecalproject/scripts/generate_play_feature_graphic.py

Optional:
  --in  <path>   Input icon path (default: mobile/assets/appicon.png)
  --out <path>   Output path (default: mobile/assets/google-play-feature-graphic-1024x500.png)
  --bg  <hex>    Background color (default: FFFFFF)
  --margin <px>  Margin around the icon (default: 60)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image


DEFAULT_IN = Path("mobile/assets/appicon.png")
DEFAULT_OUT = Path("mobile/assets/google-play-feature-graphic-1024x500.png")


def _parse_hex_color(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) != 6:
        raise ValueError("bg must be a 6-digit hex color, e.g. FFFFFF")
    r = int(v[0:2], 16)
    g = int(v[2:4], 16)
    b = int(v[4:6], 16)
    return r, g, b


def _load_icon(path: Path) -> Image.Image:
    im = Image.open(path)

    # Some PNGs are palette-based (mode "P"); normalize early.
    if im.mode in {"P", "LA", "RGBA"}:
        im = im.convert("RGBA")
    else:
        im = im.convert("RGB")

    # If the icon has alpha, composite it on white so output has no transparency.
    if im.mode == "RGBA":
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg.convert("RGB")

    return im


def generate_feature_graphic(
    icon: Image.Image, out_path: Path, bg_rgb: tuple[int, int, int], margin: int
) -> None:
    canvas_w, canvas_h = 1024, 500

    if margin < 0 or margin >= min(canvas_w, canvas_h) // 2:
        raise ValueError("margin is out of range")

    target_w = canvas_w - 2 * margin
    target_h = canvas_h - 2 * margin

    # Scale icon to fit in the target box (preserve aspect ratio).
    scale = min(target_w / icon.width, target_h / icon.height)
    new_size = (max(1, int(icon.width * scale)), max(1, int(icon.height * scale)))
    icon_resized = icon.resize(new_size, Image.LANCZOS)

    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_rgb)
    x = (canvas_w - icon_resized.width) // 2
    y = (canvas_h - icon_resized.height) // 2
    canvas.paste(icon_resized, (x, y))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG", optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    parser.add_argument("--out", dest="out_path", default=str(DEFAULT_OUT))
    parser.add_argument("--bg", dest="bg", default="FFFFFF")
    parser.add_argument("--margin", dest="margin", type=int, default=60)
    args = parser.parse_args()

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)

    if not in_path.exists():
        raise FileNotFoundError(f"Input icon not found: {in_path}")

    # Run relative to repo root regardless of invocation cwd.
    # If invoked from elsewhere, try to resolve relative paths from the repo root.
    # (We detect repo root as the parent containing 'circlecalproject' folder.)
    cwd = Path.cwd()
    if not in_path.is_absolute() and not in_path.exists():
        pass

    bg_rgb = _parse_hex_color(args.bg)
    icon = _load_icon(in_path)

    generate_feature_graphic(icon, out_path, bg_rgb=bg_rgb, margin=args.margin)

    size_bytes = os.path.getsize(out_path)
    print(f"Wrote {out_path} ({size_bytes/1024:.1f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

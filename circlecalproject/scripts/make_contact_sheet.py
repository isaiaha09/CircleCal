"""Create a labeled contact sheet image from a folder of screenshots.

Windows-friendly helper for quickly reviewing many screenshots at once.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _iter_images(input_dir: Path) -> list[Path]:
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    return sorted([p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in exts])


def main() -> int:
    parser = argparse.ArgumentParser(description="Make a labeled contact sheet for screenshots")
    parser.add_argument("--in", dest="input_dir", required=True, help="Input folder containing screenshots")
    parser.add_argument("--out", dest="out_path", required=True, help="Output PNG path")
    parser.add_argument("--cols", type=int, default=4, help="Columns in the grid")
    parser.add_argument("--thumb-w", type=int, default=360, help="Thumbnail width")
    parser.add_argument("--thumb-h", type=int, default=780, help="Thumbnail height")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    out_path = Path(args.out_path)

    files = _iter_images(input_dir)
    if not files:
        raise SystemExit(f"No images found in: {input_dir}")

    cols = max(1, int(args.cols))
    thumb_w = max(50, int(args.thumb_w))
    thumb_h = max(50, int(args.thumb_h))

    rows = (len(files) + cols - 1) // cols
    pad = 20
    label_h = 60

    sheet_w = pad + cols * (thumb_w + pad)
    sheet_h = pad + rows * (thumb_h + label_h + pad)

    sheet = Image.new("RGB", (sheet_w, sheet_h), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)

    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()

    for i, p in enumerate(files, start=1):
        r = (i - 1) // cols
        c = (i - 1) % cols
        x0 = pad + c * (thumb_w + pad)
        y0 = pad + r * (thumb_h + label_h + pad)

        with Image.open(p) as im:
            im = im.convert("RGB")
            im.thumbnail((thumb_w, thumb_h))
            tx = x0 + (thumb_w - im.size[0]) // 2
            ty = y0 + (thumb_h - im.size[1]) // 2
            sheet.paste(im, (tx, ty))

        label = f"{i:02d} {p.name}"
        draw.text((x0, y0 + thumb_h + 10), label, fill=(0, 0, 0), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, format="PNG", optimize=True)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

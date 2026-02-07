"""Generate App Store Connect screenshots with headline/subheadline.

This script is designed for an iterative workflow:
- You keep your raw screenshots in a folder.
- For a single screenshot (by index in sorted order), you generate App Store-sized
  outputs with a white background and optional headline/subheadline.

Why index-based?
- It's easy to coordinate with a labeled contact sheet / chosen ordering.

Example:
  python scripts/appstore_screenshot_frame.py \
    --in "D:/CircleCal/mobile/CircleCal app submission" \
    --out "D:/CircleCal/mobile/CircleCal app submission/framed" \
    --device iphone --index 1 \
    --headline "Book clients in seconds" \
    --subtitle "Share your link and accept bookings instantly"
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, Tuple

try:
    from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: Pillow. Install it with: pip install pillow\n" f"Original error: {exc}"
    )


@dataclass(frozen=True)
class TargetSize:
    name: str
    width: int
    height: int


IPHONE_ACCEPTED: Sequence[TargetSize] = (
    TargetSize("1242x2688", 1242, 2688),
    TargetSize("2688x1242", 2688, 1242),
    TargetSize("1284x2778", 1284, 2778),
    TargetSize("2778x1284", 2778, 1284),
)

IPAD_ACCEPTED: Sequence[TargetSize] = (
    TargetSize("2064x2752", 2064, 2752),
    TargetSize("2752x2064", 2752, 2064),
    TargetSize("2048x2732", 2048, 2732),
    TargetSize("2732x2048", 2732, 2048),
)


def _iter_input_files(input_path: str) -> list[Path]:
    p = Path(input_path)
    if p.is_dir():
        patterns = ["*.png", "*.jpg", "*.jpeg", "*.webp"]
        files: list[Path] = []
        for pat in patterns:
            files.extend(sorted(p.glob(pat)))
        return files

    matches = glob.glob(input_path)
    if matches:
        return [Path(m) for m in sorted(matches)]

    if p.exists():
        return [p]

    return []


def _parse_rgb(value: str) -> Tuple[int, int, int]:
    rgb = ImageColor.getrgb(value)
    if isinstance(rgb, int):
        return (rgb, rgb, rgb)
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _try_load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Prefer Arial on Windows; fall back to PIL default.
    candidates = []
    if bold:
        candidates += ["arialbd.ttf", "Arial Bold.ttf"]
    candidates += ["arial.ttf", "Arial.ttf"]

    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    words = (text or "").split()
    if not words:
        return ""

    lines: list[str] = []
    current: list[str] = []
    for w in words:
        trial = (" ".join(current + [w])).strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] <= max_width or not current:
            current.append(w)
        else:
            lines.append(" ".join(current))
            current = [w]
    if current:
        lines.append(" ".join(current))

    return "\n".join(lines)


def _draw_centered_multiline(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    x_center: int,
    y_top: int,
    max_width: int,
    fill: Tuple[int, int, int],
    line_spacing: int,
) -> int:
    if not text:
        return y_top

    wrapped = _wrap_text(draw, text, font, max_width)
    lines = wrapped.split("\n")

    y = y_top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = int(x_center - w / 2)
        draw.text((x, y), line, font=font, fill=fill)
        y += h + line_spacing

    return y


def _compose(
    src_img: Image.Image,
    target: TargetSize,
    bg_rgb: Tuple[int, int, int],
    fg_rgb: Tuple[int, int, int],
    headline: str,
    subtitle: str,
    top_ratio: float,
) -> Image.Image:
    src_img = ImageOps.exif_transpose(src_img)
    if src_img.mode not in ("RGB", "RGBA"):
        src_img = src_img.convert("RGBA")

    canvas = Image.new("RGB", (target.width, target.height), bg_rgb)
    draw = ImageDraw.Draw(canvas)

    # Reserve space for text at top; keep within the required total pixel size.
    top_h = int(round(target.height * top_ratio)) if (headline or subtitle) else 0
    top_h = max(0, min(top_h, target.height // 2))

    # Font sizes scale with width; tuned for readability.
    headline_size = max(18, int(round(target.width * 0.065)))
    subtitle_size = max(14, int(round(target.width * 0.040)))

    headline_font = _try_load_font(headline_size, bold=True)
    subtitle_font = _try_load_font(subtitle_size, bold=False)

    # Text area padding
    pad_x = int(round(target.width * 0.06))
    y = int(round(target.height * 0.04))
    x_center = target.width // 2

    if top_h:
        max_text_w = target.width - 2 * pad_x
        y = _draw_centered_multiline(
            draw,
            headline.strip(),
            headline_font,
            x_center,
            y,
            max_text_w,
            fg_rgb,
            line_spacing=max(4, headline_size // 7),
        )
        if subtitle.strip():
            y += max(8, subtitle_size // 3)
            _draw_centered_multiline(
                draw,
                subtitle.strip(),
                subtitle_font,
                x_center,
                y,
                max_text_w,
                fg_rgb,
                line_spacing=max(3, subtitle_size // 7),
            )

    # Place screenshot image into remaining area.
    avail_h = target.height - top_h
    avail_w = target.width
    if avail_h <= 0:
        avail_h = target.height
        top_h = 0

    src_w, src_h = src_img.size
    scale = min(avail_w / src_w, avail_h / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = src_img.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)

    off_x = (avail_w - new_w) // 2
    off_y = top_h + (avail_h - new_h) // 2

    if resized.mode == "RGBA":
        canvas.paste(resized, (off_x, off_y), mask=resized.split()[-1])
    else:
        canvas.paste(resized, (off_x, off_y))

    return canvas


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate App Store screenshots with headlines")
    parser.add_argument("--in", dest="input_path", required=True, help="Input directory, glob, or single image file")
    parser.add_argument("--out", dest="out_dir", required=True, help="Output directory")
    parser.add_argument("--device", choices=["iphone", "ipad", "both"], required=True)
    parser.add_argument("--index", type=int, required=True, help="1-based index into sorted input screenshots")
    parser.add_argument("--headline", default="", help="Headline text (optional)")
    parser.add_argument("--subtitle", default="", help="Subtitle text (optional)")
    parser.add_argument("--bg", default="#FFFFFF", help="Background color (default white)")
    parser.add_argument("--fg", default="#111827", help="Text color (default near-black)")
    parser.add_argument(
        "--top-ratio",
        type=float,
        default=0.22,
        help="Fraction of height reserved for text when headline/subtitle present (default 0.22)",
    )

    args = parser.parse_args(argv)

    files = _iter_input_files(args.input_path)
    if not files:
        print(f"No images found for input: {args.input_path}", file=sys.stderr)
        return 2

    if args.index < 1 or args.index > len(files):
        print(f"Index out of range: {args.index}. Found {len(files)} image(s).", file=sys.stderr)
        return 2

    src_file = files[args.index - 1]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bg_rgb = _parse_rgb(args.bg)
    fg_rgb = _parse_rgb(args.fg)

    device_targets: list[tuple[str, Sequence[TargetSize]]] = []
    if args.device in ("iphone", "both"):
        device_targets.append(("iphone", IPHONE_ACCEPTED))
    if args.device in ("ipad", "both"):
        device_targets.append(("ipad", IPAD_ACCEPTED))

    with Image.open(src_file) as im:
        for device, targets in device_targets:
            for t in targets:
                device_dir = out_dir / device / t.name
                device_dir.mkdir(parents=True, exist_ok=True)

                safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", src_file.stem).strip("._-") or "screenshot"
                out_name = f"{args.index:02d}_{safe_stem}_{t.name}.png"
                out_path = device_dir / out_name

                out_img = _compose(
                    im,
                    t,
                    bg_rgb=bg_rgb,
                    fg_rgb=fg_rgb,
                    headline=args.headline,
                    subtitle=args.subtitle,
                    top_ratio=float(args.top_ratio),
                )
                out_img.save(out_path, format="PNG", optimize=True)

    print(f"OK {src_file} -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

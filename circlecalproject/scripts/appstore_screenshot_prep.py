"""Prepare App Store Connect screenshots from source screenshots.

This script DOES NOT capture screenshots; it only converts existing images
(PNG/JPG) into the exact pixel sizes App Store Connect accepts.

Typical workflow (Windows-friendly):
1) Install your build from TestFlight on an iPhone/iPad.
2) Capture screenshots on-device.
3) Copy the images to your PC (e.g., via iCloud Photos, USB, or email).
4) Run this script to output correctly-sized PNGs for upload.

Examples:
  python circlecalproject/scripts/appstore_screenshot_prep.py --in ./raw --out ./out --device iphone
  python circlecalproject/scripts/appstore_screenshot_prep.py --in ./raw --out ./out --device ipad

By default, generates ALL accepted sizes for the selected device family.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


try:
    from PIL import Image, ImageColor, ImageOps
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: Pillow. Install it with: pip install pillow\n"
        f"Original error: {exc}"
    )


@dataclass(frozen=True)
class TargetSize:
    name: str
    width: int
    height: int


IPHONE_ACCEPTED: Sequence[TargetSize] = (
    # App Store Connect groups modern iPhones under "6.5" Display" and accepts multiple sizes.
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


def _iter_input_files(input_path: str) -> List[Path]:
    p = Path(input_path)
    if p.is_dir():
        patterns = ["*.png", "*.jpg", "*.jpeg", "*.webp"]
        files: List[Path] = []
        for pat in patterns:
            files.extend(sorted(p.glob(pat)))
        return files

    # glob or single file
    matches = glob.glob(input_path)
    if matches:
        return [Path(m) for m in sorted(matches)]

    if p.exists():
        return [p]

    return []


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", stem).strip("._-")
    return stem or "screenshot"


def _parse_hex_color(value: str) -> Tuple[int, int, int]:
    try:
        rgb = ImageColor.getrgb(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid color '{value}': {exc}")

    if isinstance(rgb, int):
        # grayscale
        return (rgb, rgb, rgb)

    if len(rgb) >= 3:
        return (rgb[0], rgb[1], rgb[2])

    raise argparse.ArgumentTypeError(f"Invalid color '{value}'")


def _crop_top(img: Image.Image, *, crop_top_px: int = 0, crop_top_ratio: float = 0.0) -> Image.Image:
    if crop_top_px < 0:
        crop_top_px = 0
    if crop_top_ratio < 0:
        crop_top_ratio = 0.0

    w, h = img.size
    if w <= 0 or h <= 0:
        return img

    px = crop_top_px
    if crop_top_ratio:
        px = max(px, int(round(h * float(crop_top_ratio))))

    # Keep at least 1px height.
    px = min(max(0, int(px)), max(0, h - 1))
    if px <= 0:
        return img
    return img.crop((0, px, w, h))


def _contain_on_canvas(
    img: Image.Image,
    target_w: int,
    target_h: int,
    bg_rgb: Tuple[int, int, int],
    *,
    crop_top_px: int = 0,
    crop_top_ratio: float = 0.0,
) -> Image.Image:
    # Normalize orientation from EXIF and ensure RGB output (App Store accepts PNG/JPG; avoid alpha).
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")

    # Optional: crop out the iOS status bar / top chrome.
    img = _crop_top(img, crop_top_px=crop_top_px, crop_top_ratio=crop_top_ratio)

    # Scale to fit within target without cropping.
    src_w, src_h = img.size
    if src_w == 0 or src_h == 0:
        raise ValueError("Invalid image with zero dimension")

    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = img.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)

    # Composite onto background.
    canvas = Image.new("RGB", (target_w, target_h), bg_rgb)
    offset_x = (target_w - new_w) // 2
    offset_y = (target_h - new_h) // 2

    if resized.mode == "RGBA":
        canvas.paste(resized, (offset_x, offset_y), mask=resized.split()[-1])
    else:
        canvas.paste(resized, (offset_x, offset_y))

    return canvas


def _write_outputs(
    src_file: Path,
    out_dir: Path,
    device: str,
    targets: Sequence[TargetSize],
    index: int,
    bg_rgb: Tuple[int, int, int],
    crop_top_px: int,
    crop_top_ratio: float,
) -> None:
    stem = _safe_stem(src_file.name)

    with Image.open(src_file) as im:
        for t in targets:
            device_dir = out_dir / device / t.name
            device_dir.mkdir(parents=True, exist_ok=True)
            out_name = f"{index:02d}_{stem}_{t.name}.png"
            out_path = device_dir / out_name
            out_img = _contain_on_canvas(
                im,
                t.width,
                t.height,
                bg_rgb,
                crop_top_px=crop_top_px,
                crop_top_ratio=crop_top_ratio,
            )
            out_img.save(out_path, format="PNG", optimize=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare App Store Connect screenshots (resize/pad to accepted sizes).")
    parser.add_argument("--in", dest="input_path", required=True, help="Input directory, glob, or single image file")
    parser.add_argument("--out", dest="out_dir", required=True, help="Output directory")
    parser.add_argument(
        "--device",
        choices=["iphone", "ipad"],
        required=True,
        help="Which App Store Connect screenshot group to generate",
    )
    parser.add_argument(
        "--bg",
        type=_parse_hex_color,
        default="#000000",
        help="Background color used for padding (default: #000000)",
    )
    parser.add_argument(
        "--crop-top-px",
        type=int,
        default=0,
        help="Crop this many pixels off the top before resizing (default: 0)",
    )
    parser.add_argument(
        "--crop-top-ratio",
        type=float,
        default=0.0,
        help="Crop this fraction of the image height off the top before resizing (default: 0.0)",
    )

    args = parser.parse_args(argv)

    input_files = _iter_input_files(args.input_path)
    if not input_files:
        print(f"No images found for input: {args.input_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = IPHONE_ACCEPTED if args.device == "iphone" else IPAD_ACCEPTED

    if args.crop_top_px and args.crop_top_ratio:
        print("Use only one of --crop-top-px or --crop-top-ratio (not both)", file=sys.stderr)
        return 2

    print(f"Found {len(input_files)} image(s). Generating {len(targets)} size(s) per image...")
    for idx, f in enumerate(input_files, start=1):
        try:
            _write_outputs(
                f,
                out_dir,
                args.device,
                targets,
                idx,
                args.bg,
                int(args.crop_top_px or 0),
                float(args.crop_top_ratio or 0.0),
            )
            print(f"OK  {f}")
        except Exception as exc:
            print(f"FAIL {f} :: {exc}", file=sys.stderr)

    print(f"Done. Output written to: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

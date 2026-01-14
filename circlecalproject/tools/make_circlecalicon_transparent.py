from __future__ import annotations

from pathlib import Path

from PIL import Image


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "static" / "icons" / "circlecalicon.png"
    dst = repo_root / "static" / "icons" / "circlecalicon-transparent-v4.png"

    if not src.exists():
        raise SystemExit(f"Source icon not found: {src}")

    img = Image.open(src).convert("RGBA")
    pixels = img.load()

    # Remove near-white/gray background with a soft edge.
    #
    # Key detail: when converting a logo that was anti-aliased against white,
    # you must also handle the edge pixels ("matte") or you'll see a visible
    # band/halo when the browser scales the PNG over a non-white background.
    #
    # We also intentionally avoid the previous crop+paste step: Pillow's paste
    # compositing can premultiply partially-transparent pixels and make a
    # top-edge band look *worse*.
    lo = 220
    hi = 242
    sat_max = 20

    # If an entire row is basically "background" (e.g., a stray top border strip
    # across the whole image), force it fully transparent. This is the safest
    # way to remove the visible "lip" without risking removal of real logo art.
    row_force_lo = 230
    row_force_ratio = 0.995
    force_row_transparent: set[int] = set()
    for y in range(img.height):
        bgish = 0
        for x in range(img.width):
            r, g, b, a = pixels[x, y]
            mx = max(r, g, b)
            mn = min(r, g, b)
            sat = mx - mn
            brightness = (r + g + b) // 3
            if brightness >= row_force_lo and sat <= sat_max:
                bgish += 1
        if bgish / img.width >= row_force_ratio:
            force_row_transparent.add(y)

    def clamp_u8(v: float) -> int:
        if v < 0:
            return 0
        if v > 255:
            return 255
        return int(round(v))

    for y in range(img.height):
        for x in range(img.width):
            if y in force_row_transparent:
                pixels[x, y] = (0, 0, 0, 0)
                continue

            r, g, b, a = pixels[x, y]
            mx = max(r, g, b)
            mn = min(r, g, b)
            sat = mx - mn
            brightness = (r + g + b) // 3

            # Only key out pixels that look like background (bright AND low saturation).
            is_backgroundish = (brightness >= lo) and (sat <= sat_max)

            if is_backgroundish and brightness >= hi:
                new_a = 0
            elif not is_backgroundish or brightness <= lo:
                new_a = 255
            else:
                # Linear ramp between lo..hi (brighter => more transparent)
                t = (hi - brightness) / (hi - lo)
                new_a = clamp_u8(255 * t)

            # Defringe / un-matte from white for partially transparent pixels.
            # Observed pixel C = a*F + (1-a)*B. With B=white, solve F.
            if new_a <= 0:
                pixels[x, y] = (0, 0, 0, 0)
            elif new_a >= 255:
                pixels[x, y] = (r, g, b, 255)
            else:
                a_f = new_a / 255.0
                # Un-matte against white (255)
                rr = (r - (1.0 - a_f) * 255.0) / a_f
                gg = (g - (1.0 - a_f) * 255.0) / a_f
                bb = (b - (1.0 - a_f) * 255.0) / a_f
                pixels[x, y] = (clamp_u8(rr), clamp_u8(gg), clamp_u8(bb), new_a)

    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, format="PNG")
    print(f"Wrote: {dst}")


if __name__ == "__main__":
    main()

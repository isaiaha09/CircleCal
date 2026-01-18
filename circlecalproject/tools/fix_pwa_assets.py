from __future__ import annotations

from pathlib import Path

from PIL import Image


def ensure_splash_1179x2556(root: Path) -> Path | None:
    src = root / "splash" / "splash-1290x2796.png"
    dst = root / "splash" / "splash-1179x2556.png"

    if not src.exists():
        print("Missing source splash:", src)
        return None

    if dst.exists():
        print("Splash already exists:", dst)
        return dst

    im = Image.open(src).convert("RGBA")
    im = im.resize((1179, 2556), Image.Resampling.LANCZOS)
    im.save(dst)
    print("Wrote splash:", dst)
    return dst


def heal_screenshot_top_line(path: Path) -> int:
    """Remove the thin gray top-line artifact by copying pixels from y0+3 to y0..y0+2.

    This targets the first few rows of the top-most non-background content region.
    """

    im = Image.open(path).convert("RGBA")
    w, h = im.size
    px = im.load()

    bg = px[0, 0][:3]

    y0: int | None = None
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a != 0 and (r, g, b) != bg:
                y0 = y
                break
        if y0 is not None:
            break

    if y0 is None:
        print(path.name, "no foreground; skipped")
        return 0

    replace_from = y0 + 3
    if replace_from >= h:
        print(path.name, "unexpected geometry; skipped")
        return 0

    changed = 0
    for y in range(y0, min(y0 + 3, h)):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a != 0 and (r, g, b) != bg:
                px[x, y] = px[x, replace_from]
                changed += 1

    if changed:
        im.save(path)

    print(path.name, "healed", changed, "pixels at y0", y0)
    return changed


def main() -> None:
    icons_root = Path("D:/CircleCalBackup/circlecalproject/static/icons")

    ensure_splash_1179x2556(icons_root)

    for name in ("screenshot-1080x1920.png", "screenshot-1920x1080.png"):
        heal_screenshot_top_line(icons_root / "screenshots" / name)


if __name__ == "__main__":
    main()

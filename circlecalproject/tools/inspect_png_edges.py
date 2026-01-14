from __future__ import annotations

from pathlib import Path

from PIL import Image


def analyze(path: Path) -> None:
    if not path.exists():
        print(f"\nMISSING: {path}")
        return

    im = Image.open(path).convert("RGBA")
    w, h = im.size
    pix = im.load()

    alpha = im.getchannel("A")
    bbox_any = alpha.point(lambda a: 255 if a > 0 else 0).getbbox()
    bbox_28 = alpha.point(lambda a: 255 if a > 28 else 0).getbbox()

    print(f"\n== {path.as_posix()} ==")
    print("size", (w, h))
    print("bbox alpha>0 ", bbox_any)
    print("bbox alpha>28", bbox_28)

    def row_stats(y: int) -> tuple[int, int, int, int, int]:
        max_a = 0
        max_rgb_when_a0 = 0
        count_a0_nonzero_rgb = 0
        count_a_gt0 = 0
        count_fringe_like = 0
        for x in range(w):
            r, g, b, a = pix[x, y]
            max_a = max(max_a, a)
            if a == 0:
                mx = max(r, g, b)
                if mx:
                    count_a0_nonzero_rgb += 1
                    max_rgb_when_a0 = max(max_rgb_when_a0, mx)
            else:
                count_a_gt0 += 1
                # Heuristic for visible white fringe: fairly transparent + very bright + low saturation
                mx = max(r, g, b)
                mn = min(r, g, b)
                sat = mx - mn
                brightness = (r + g + b) // 3
                if 1 <= a <= 80 and brightness >= 240 and sat <= 18:
                    count_fringe_like += 1
        return max_a, count_a0_nonzero_rgb, max_rgb_when_a0, count_a_gt0, count_fringe_like

    for y in range(0, 12):
        max_a, count_a0_nonzero_rgb, max_rgb_when_a0, count_a_gt0, count_fringe_like = row_stats(y)
        print(
            f"row {y:02d}: max a={max_a:3d} | a>0 pixels={count_a_gt0:4d} | fringe-like={count_fringe_like:4d} | a==0&rgb!=0={count_a0_nonzero_rgb:4d} (max rgb {max_rgb_when_a0:3d})"
        )

    first_nonzero = None
    for y in range(h):
        if any(pix[x, y][3] > 0 for x in range(w)):
            first_nonzero = y
            break

    print("first row with any alpha>0:", first_nonzero)

    if first_nonzero is not None:
        start = max(0, first_nonzero - 4)
        end = min(h, first_nonzero + 14)
        print(f"top-edge window rows [{start}..{end - 1}]")
        for y in range(start, end):
            max_a, count_a0_nonzero_rgb, max_rgb_when_a0, count_a_gt0, count_fringe_like = row_stats(y)
            print(
                f"  row {y:03d}: max a={max_a:3d} | a>0 pixels={count_a_gt0:4d} | fringe-like={count_fringe_like:4d}"
            )


def main() -> None:
    root = Path("static/icons")
    files = [
        root / "circlecalicon.png",
        root / "circlecalicon-transparent.png",
        root / "circlecalicon-transparent-v2.png",
        root / "circlecalicon-transparent-v3.png",
        root / "circlecalicon-transparent-v4.png",
    ]
    for p in files:
        analyze(p)


if __name__ == "__main__":
    main()

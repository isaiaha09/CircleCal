from __future__ import annotations

from pathlib import Path

from PIL import Image


def _row_metrics(px, w: int, y: int) -> tuple[tuple[int, int, int], tuple[float, float, float]]:
    # Returns (mean_rgb, stddev_rgb) for the row, considering only opaque pixels.
    total_r = total_g = total_b = 0
    n = 0
    for x in range(w):
        r, g, b, a = px[x, y]
        if a == 0:
            continue
        total_r += r
        total_g += g
        total_b += b
        n += 1
    if n == 0:
        return (0, 0, 0), (0.0, 0.0, 0.0)

    mean_r = total_r / n
    mean_g = total_g / n
    mean_b = total_b / n

    var_r = var_g = var_b = 0.0
    for x in range(w):
        r, g, b, a = px[x, y]
        if a == 0:
            continue
        var_r += (r - mean_r) ** 2
        var_g += (g - mean_g) ** 2
        var_b += (b - mean_b) ** 2
    var_r /= n
    var_g /= n
    var_b /= n

    return (int(round(mean_r)), int(round(mean_g)), int(round(mean_b))), (
        var_r**0.5,
        var_g**0.5,
        var_b**0.5,
    )


def top_line_stats(path: Path, rows: int = 6) -> dict:
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    px = img.load()

    nontransparent: list[int] = []
    row_means: list[tuple[int, int, int]] = []
    row_stds: list[tuple[float, float, float]] = []

    for y in range(min(rows, h)):
        count = 0
        for x in range(w):
            if px[x, y][3] != 0:
                count += 1
        nontransparent.append(count)

        mean_rgb, std_rgb = _row_metrics(px, w, y)
        row_means.append(mean_rgb)
        row_stds.append(std_rgb)

    return {
        "size": (w, h),
        "top_nontransparent": nontransparent,
        "top_mean_rgb": row_means,
        "top_std_rgb": row_stds,
    }


def main() -> None:
    root = Path("D:/CircleCalBackup/circlecalproject/static/icons")
    paths: list[Path] = [
        root / "circlecalicon-transparent-v4.png",
        root / "screenshots" / "screenshot-1080x1920.png",
        root / "screenshots" / "screenshot-1920x1080.png",
        *sorted((root / "splash").glob("*.png")),
    ]

    for p in paths:
        if not p.exists():
            continue
        info = top_line_stats(p, rows=6)
        size = info["size"]
        tl = info["top_nontransparent"]
        means = info["top_mean_rgb"]
        stds = info["top_std_rgb"]
        print(f"{p.name:28} {size} top_nontransparent={tl}")
        print(f"  top_mean_rgb={means}")
        print(f"  top_std_rgb={[tuple(round(v,2) for v in s) for s in stds]}")


if __name__ == "__main__":
    main()

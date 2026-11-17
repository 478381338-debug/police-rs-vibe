"""Bake a local Esri World Imagery basemap for a terrain bbox.

The FastAPI tile proxy is blocked by the upstream for Python clients on some
networks, and direct browser requests are rejected by CORS.  This script uses
curl (which the CDN accepts) to fetch the tiles once, stitches them, crops to
the terrain bbox and writes a same-origin JPEG for the 3D engine.
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "esri"
OUT_DIR = ROOT / "web" / "assets" / "basemap"
TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

REGIONS = {
    "dagujia": {
        "bbox": [121.108, 37.086, 121.192, 37.154],
        "zoom": 15,
        "output": "dagujia_sat.jpg",
    },
    "p07_site": {
        "bbox": [120.185, 37.125, 120.245, 37.175],
        "zoom": 16,
        "output": "p07_site_sat.jpg",
    },
    "p01": {
        "bbox": [120.07, 37.41, 120.12, 37.45],
        "zoom": 16,
        "output": "p01_sat.jpg",
    },
}


def lon2px(lon: float, z: int) -> float:
    return (lon + 180.0) / 360.0 * 256.0 * (1 << z)


def lat2px(lat: float, z: int) -> float:
    s = math.sin(math.radians(lat))
    return (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * 256.0 * (1 << z)


def fetch_tile(z: int, x: int, y: int) -> Path:
    path = CACHE / str(z) / str(x) / f"{y}.jpg"
    if path.is_file() and path.stat().st_size > 512:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    url = TILE_URL.format(z=z, x=x, y=y)
    for attempt in range(3):
        tmp = path.with_suffix(".part")
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", "25", "-o", str(tmp), url],
            capture_output=True,
        )
        if proc.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 512:
            tmp.replace(path)
            return path
        if tmp.exists():
            tmp.unlink()
        print(f"  retry {attempt + 1}: {url}", file=sys.stderr)
    raise RuntimeError(f"tile failed: {url}")


def bake(name: str) -> None:
    cfg = REGIONS[name]
    west, south, east, north = cfg["bbox"]
    z = cfg["zoom"]
    px0, py0 = lon2px(west, z), lat2px(north, z)
    px1, py1 = lon2px(east, z), lat2px(south, z)
    tx0, tx1 = int(px0 // 256), int((px1 - 0.001) // 256)
    ty0, ty1 = int(py0 // 256), int((py1 - 0.001) // 256)
    cols, rows = tx1 - tx0 + 1, ty1 - ty0 + 1
    print(f"{name}: z{z} tiles {cols}x{rows} -> {cols * rows}")

    canvas = Image.new("RGB", (cols * 256, rows * 256), (18, 32, 42))
    total = cols * rows
    done = 0
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = Image.open(fetch_tile(z, tx, ty)).convert("RGB")
            canvas.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))
            done += 1
            if done % 10 == 0 or done == total:
                print(f"  {done}/{total}")

    left = int(round(px0 - tx0 * 256))
    top = int(round(py0 - ty0 * 256))
    right = int(round(px1 - tx0 * 256))
    bottom = int(round(py1 - ty0 * 256))
    cropped = canvas.crop((left, top, right, bottom))
    target = max(cropped.width, cropped.height)
    if target > 2600:
        ratio = 2600 / target
        cropped = cropped.resize(
            (round(cropped.width * ratio), round(cropped.height * ratio)),
            Image.LANCZOS,
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / cfg["output"]
    cropped.save(out, "JPEG", quality=88, optimize=True, progressive=True)
    print(f"{out}  {cropped.width}x{cropped.height}  {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    names = sys.argv[1:] or list(REGIONS)
    for region in names:
        bake(region)
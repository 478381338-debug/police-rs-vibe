"""把 Terrarium 高程瓦片烘焙成前端可直接加载的小型高程网格。

用法：
    python scripts/bake_terrain.py

输出 web/assets/terrain/<name>.json：
    {name, source, zoom, bbox:[w,s,e,n], width, height, elev_min, elev_max, grid:[[m,...],...]}

只用于三维示意底模；不做测绘用途，也不替代正式 DEM。
"""
from __future__ import annotations

import io
import json
import math
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "terrain"
OUT = ROOT / "web" / "assets" / "terrain"
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
UA = {"User-Agent": "yantai-rs-terrain-bake/1.0"}

def around(name: str, lon: float, lat: float, span_m: float, zoom: int = 15, grid: int = 129) -> dict:
    """以事件中心取局部窗口：块体尺度与图斑同级，图斑与采坑才看得见。"""
    dlat = span_m / 2.0 / 110540.0
    dlon = span_m / 2.0 / (111320.0 * math.cos(math.radians(lat)))
    return {
        "name": name,
        "bbox": [round(lon - dlon, 6), round(lat - dlat, 6), round(lon + dlon, 6), round(lat + dlat, 6)],
        "zoom": zoom,
        "grid": grid,
    }


REGIONS = [
    # P07 试点样区，略向外扩以保留坑缘周边地形背景
    {"name": "p07", "bbox": [120.176, 37.116, 120.254, 37.184], "zoom": 15, "grid": 129},
    # 三维面板的样区视图：底模范围与 Sentinel 影像范围严格一致，影像才能无缝贴地
    {"name": "p07_site", "bbox": [120.185, 37.125, 120.245, 37.175], "zoom": 15, "grid": 512},
    # P01 招远北部样区：底模与 p01 影像同范围（西北角含海）
    {"name": "p01", "bbox": [120.07, 37.41, 120.12, 37.45], "zoom": 15, "grid": 512},
    # 大沽夹河试点段（只有中心点，取近似河谷范围）
    {"name": "dagujia", "bbox": [121.108, 37.086, 121.192, 37.154], "zoom": 15, "grid": 512},
    # 事件级局部底模：1.1 km / 1.6 km 窗口（Terrarium 最高 z15）
    around("yt001", 120.23434, 37.13838, 1100, grid=512),
    around("yt002", 120.22200, 37.13200, 1100, grid=512),
    around("yt003", 121.15000, 37.12000, 1600, grid=512),
]


def lonlat_to_px(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    n = 2.0 ** zoom * 256.0
    x = (lon + 180.0) / 360.0 * n
    siny = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * n
    return x, y


def fetch_tile(z: int, x: int, y: int) -> Image.Image:
    path = CACHE / str(z) / str(x) / f"{y}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers=UA)
        with urllib.request.urlopen(req, timeout=30) as resp:
            path.write_bytes(resp.read())
    return Image.open(io.BytesIO(path.read_bytes())).convert("RGB")


def terrarium_to_m(arr: np.ndarray) -> np.ndarray:
    a = arr.astype(np.float32)
    return a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0


def bake(region: dict) -> dict:
    west, south, east, north = region["bbox"]
    zoom = region["zoom"]
    gw = gh = region["grid"]

    tx0, ty0 = lonlat_to_px(west, north, zoom)
    tx1, ty1 = lonlat_to_px(east, south, zoom)
    x0, y0 = int(tx0 // 256), int(ty0 // 256)
    x1, y1 = int(tx1 // 256), int(ty1 // 256)

    tiles = {}
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            tiles[(tx, ty)] = fetch_tile(zoom, tx, ty)

    lons = np.linspace(west, east, gw)
    lats = np.linspace(north, south, gh)
    grid = np.zeros((gh, gw), dtype=np.float32)

    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            px, py = lonlat_to_px(float(lon), float(lat), zoom)
            tx, ty = int(px // 256), int(py // 256)
            img = np.asarray(tiles[(tx, ty)], dtype=np.float32)
            dem = terrarium_to_m(img)
            fx, fy = px - tx * 256.0, py - ty * 256.0
            ix, iy = int(fx), int(fy)
            dx, dy = fx - ix, fy - iy
            ix1, iy1 = min(ix + 1, 255), min(iy + 1, 255)
            v = (dem[iy, ix] * (1 - dx) * (1 - dy) + dem[iy, ix1] * dx * (1 - dy)
                 + dem[iy1, ix] * (1 - dx) * dy + dem[iy1, ix1] * dx * dy)
            grid[i, j] = v

    # 轻度平滑，去掉瓦片缝和孤立噪声；保持真实起伏
    k = np.array([1, 2, 3, 2, 1], dtype=np.float32)
    k /= k.sum()
    for _ in range(2):
        grid = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, grid)
        grid = np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, grid)

    return {
        "name": region["name"],
        "source": "AWS Terrain Tiles / Mapzen Terrarium (z%d)" % zoom,
        "zoom": zoom,
        "bbox": [west, south, east, north],
        "width": gw,
        "height": gh,
        "elev_min": round(float(grid.min()), 1),
        "elev_max": round(float(grid.max()), 1),
        "grid": [[int(round(float(v))) for v in row] for row in grid],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for region in REGIONS:
        data = bake(region)
        path = OUT / f"{data['name']}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{path}  {data['width']}x{data['height']}  {data['elev_min']}..{data['elev_max']} m  {path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
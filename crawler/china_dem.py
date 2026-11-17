"""Build a China-wide DEM from AWS Terrarium terrain tiles.

This is deliberately independent of GMT's remote-data downloader because the
installed GMT build has remote downloads disabled.  The output is a normal
GeoTIFF that GMT can read locally.
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import calculate_default_transform, reproject
from PIL import Image
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "external"
CACHE = OUT / "terrarium_china_z6"
BOUNDS = (70.0, 15.0, 140.0, 55.0)  # west, south, east, north
ZOOM = 6
TILE = 256
URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(max(min(lat, 85.05112878), -85.05112878))
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tile_bounds(x: int, y: int, z: int):
    n = 2 ** z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west, south, east, north


def fetch_tile(z: int, x: int, y: int) -> tuple[int, int, np.ndarray]:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{z}_{x}_{y}.png"
    if not f.exists():
        r = requests.get(URL.format(z=z, x=x, y=y), timeout=45)
        r.raise_for_status()
        f.write_bytes(r.content)
    rgb = np.asarray(Image.open(f).convert("RGB"), dtype=np.float32)
    elev = rgb[:, :, 0] * 256.0 + rgb[:, :, 1] + rgb[:, :, 2] / 256.0 - 32768.0
    return x, y, elev


def main() -> None:
    w, s, e, n = BOUNDS
    x0, y0 = lonlat_to_tile(w, n, ZOOM)
    x1, y1 = lonlat_to_tile(e, s, ZOOM)
    xs = list(range(x0, x1 + 1))
    ys = list(range(y0, y1 + 1))
    print(f"tiles z{ZOOM} x={x0}..{x1} y={y0}..{y1} ({len(xs) * len(ys)} tiles)")
    mosaic = np.full((len(ys) * TILE, len(xs) * TILE), np.nan, dtype=np.float32)
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(fetch_tile, ZOOM, x, y) for x in xs for y in ys]
        for i, fut in enumerate(as_completed(futs), 1):
            x, y, arr = fut.result()
            row = (y - y0) * TILE
            col = (x - x0) * TILE
            mosaic[row:row + TILE, col:col + TILE] = arr
            if i % 25 == 0:
                print(f"  {i}/{len(futs)}")
    west, _, _, north = tile_bounds(x0, y0, ZOOM)
    _, south, east, _ = tile_bounds(x1, y1, ZOOM)
    R = 6378137.0
    def merc_x(lon): return math.radians(lon) * R
    def merc_y(lat): return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    left, right = merc_x(west), merc_x(east)
    top, bottom = merc_y(north), merc_y(south)
    merc = from_origin(left, top, (right - left) / mosaic.shape[1], (top - bottom) / mosaic.shape[0])
    src = OUT / "china_dem_3857.tif"
    with rasterio.open(
        src, "w", driver="GTiff", height=mosaic.shape[0], width=mosaic.shape[1],
        count=1, dtype="float32", crs="EPSG:3857", transform=merc, nodata=np.nan,
        compress="deflate", predictor=3,
    ) as ds:
        ds.write(mosaic, 1)
    print("wrote", src, src.stat().st_size)

    # Reproject to EPSG:4326 for GMT's grdimage/grdview.
    with rasterio.open(src) as ds:
        dst_crs = "EPSG:4326"
        width, height = 2800, 1600
        transform = from_bounds(*BOUNDS, width, height)
        dst = OUT / "china_dem.tif"
        src_arr = ds.read(1)
        src_arr = np.where(np.isfinite(src_arr), src_arr, -9999).astype("float32")
        dst_arr = np.full((height, width), -9999, dtype="float32")
        reproject(
            source=src_arr, destination=dst_arr,
            src_transform=ds.transform, src_crs=ds.crs,
            dst_transform=transform, dst_crs=dst_crs,
            src_nodata=-9999, dst_nodata=-9999,
            resampling=Resampling.bilinear,
        )
        with rasterio.open(
            dst, "w", driver="GTiff", height=height, width=width, count=1,
            dtype="float32", crs=dst_crs, transform=transform, nodata=-9999,
            compress="deflate", predictor=3,
        ) as out:
            out.write(dst_arr, 1)
    print("wrote", dst, dst.stat().st_size)


if __name__ == "__main__":
    main()

"""ESA WorldCover 10 m 地类先验。

只做一件事：把 WorldCover 瓦片按 AOI 网格窗口读出类别码，供矿区对象做
农田/园地软抑制。数据是 2021 年静态产品，不能代替目标年动态地类；它的作用
是给“这个图斑大概率是耕地/园地”一个先验，最终仍由夏季物候和变化证据决定。

瓦片命名：ESA_WorldCover_10m_2021_v200_N36E120_Map.tif
下载：
  https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N36E120_Map.tif
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds as window_from_bounds

# WorldCover v200 类别
TREE = 10
SHRUB = 20
GRASS = 30
CROPLAND = 40
BUILT = 50
BARE = 60
WATER = 80
WETLAND = 90

VEGETATED = {TREE, SHRUB, GRASS, CROPLAND}
CROPLAND_LIKE = {TREE, SHRUB, GRASS, CROPLAND}


def tile_name(bbox: list[float]) -> str:
    """返回覆盖 bbox 左下角的 3°×3° WorldCover 瓦片名。"""
    lon, lat = bbox[0], bbox[1]
    lat0 = int(math.floor(lat / 3.0) * 3)
    lon0 = int(math.floor(lon / 3.0) * 3)
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return f"{ns}{abs(lat0):02d}{ew}{abs(lon0):03d}"


def default_tile_path(root: Path, bbox: list[float]) -> Path:
    return root / f"ESA_WorldCover_10m_2021_v200_{tile_name(bbox)}_Map.tif"


def load_worldcover_grid(
    bbox: list[float],
    w: int,
    h: int,
    transform,
    root: Path = Path("data/landcover"),
    path: Path | None = None,
) -> np.ndarray | None:
    """把 WorldCover 类别重采样到 AOI 网格；瓦片不存在时返回 None。"""
    src = Path(path) if path else default_tile_path(Path(root), bbox)
    if not src.exists():
        return None
    with rasterio.open(src) as ds:
        wb = transform_bounds("EPSG:4326", ds.crs, *bbox)
        win = window_from_bounds(*wb, ds.transform).round_offsets().round_lengths()
        arr = ds.read(
            1,
            window=win,
            out_shape=(h, w),
            boundless=True,
            fill_value=0,
            resampling=Resampling.nearest,
        )
    return arr.astype(np.uint8)


def landcover_fractions(classes: np.ndarray, mask: np.ndarray) -> dict:
    """对象内 WorldCover 类别占比；mask 是对象像元布尔数组。"""
    lc = classes[mask]
    lc = lc[lc > 0]
    if lc.size == 0:
        return {
            "cropland_frac": None,
            "vegetated_frac": None,
            "bare_landcover_frac": None,
            "built_frac": None,
            "water_frac": None,
            "landcover_n": 0,
        }
    return {
        "cropland_frac": round(float(np.mean(lc == CROPLAND)), 3),
        "vegetated_frac": round(float(np.mean(np.isin(lc, list(VEGETATED)))), 3),
        "bare_landcover_frac": round(float(np.mean(lc == BARE)), 3),
        "built_frac": round(float(np.mean(lc == BUILT)), 3),
        "water_frac": round(float(np.mean(lc == WATER)), 3),
        "landcover_n": int(lc.size),
    }
"""光谱指数计算。

输入为 (n_dates, H, W) 的反射率数组（0~1）。
Sentinel-2 波段对应：
    blue=B02  green=B03  red=B04  nir=B08  swir16=B11(1.6µm)  swir22=B12(2.2µm)

指数选择依据（针对采矿/采石/采砂）：
    BSI    裸地指数 —— 采坑、碎石堆、砂堆最敏感
    NDVI   植被 —— 用来确认“原来有植被、现在没了”
    NDBI   建成区 —— 区分工业场地与自然裸地
    MNDWI  水体 —— 圈坑塘/河道水面，也可做水体掩膜
    iron   铁染 —— 金矿/金属矿氧化带，B04/B02
    clay   羟基蚀变 —— 黏土化蚀变，B11/B12
"""
from __future__ import annotations

import numpy as np


def _safe_ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    out = np.full(num.shape, np.nan, dtype="float32")
    np.divide(num, den, out=out, where=np.abs(den) > 1e-6)
    return out


def ndvi(b: dict[str, np.ndarray]) -> np.ndarray:
    nir, red = b["nir"], b["red"]
    return _safe_ratio(nir - red, nir + red)


def bsi(b: dict[str, np.ndarray]) -> np.ndarray:
    """Bare Soil Index: ((SWIR1+Red) - (NIR+Blue)) / ((SWIR1+Red) + (NIR+Blue))"""
    s1, red, nir, blue = b["swir16"], b["red"], b["nir"], b["blue"]
    return _safe_ratio((s1 + red) - (nir + blue), (s1 + red) + (nir + blue))


def ndbi(b: dict[str, np.ndarray]) -> np.ndarray:
    s1, nir = b["swir16"], b["nir"]
    return _safe_ratio(s1 - nir, s1 + nir)


def mndwi(b: dict[str, np.ndarray]) -> np.ndarray:
    green, s1 = b["green"], b["swir16"]
    return _safe_ratio(green - s1, green + s1)


def iron_oxide(b: dict[str, np.ndarray]) -> np.ndarray:
    return _safe_ratio(b["red"], b["blue"])


def clay_hydroxyl(b: dict[str, np.ndarray]) -> np.ndarray:
    return _safe_ratio(b["swir16"], b["swir22"])


def valid_mask(scl: np.ndarray) -> np.ndarray:
    """由 SCL 场景分类生成有效像元掩膜。

    剔除：0 无数据 / 1 饱和 / 3 云影 / 8,9 云 / 10 薄云 / 11 雪
    保留：2 暗区 / 4 植被 / 5 非植被 / 6 水体 / 7 未分类
    """
    bad = np.isin(scl, [0, 1, 3, 8, 9, 10, 11])
    return (~bad) & np.isfinite(scl)


def compute_all(bands: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """一次算全部指数，形状均为 (n_dates, H, W)。"""
    return {
        "bsi": bsi(bands),
        "ndvi": ndvi(bands),
        "ndbi": ndbi(bands),
        "mndwi": mndwi(bands),
        "iron": iron_oxide(bands),
        "clay": clay_hydroxyl(bands),
        "valid": valid_mask(bands["scl"]),
    }

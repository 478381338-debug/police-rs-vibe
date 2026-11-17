"""基线合成、时序变化检测、图斑矢量化。

判定逻辑（四要素交叉，避免单一指标假阳性）：
    1) 裸地指数相对上年同季显著上升      delta_bsi
    2) 植被指数相对上年同季显著下降      ndvi_drop
    3) 当期本身就是裸的（排除农地翻耕）  bsi_abs
    4) 不是水体                          mndwi
    5) 偏离全体像元分布足够远            robust z
最后按面积阈值过滤，输出矢量图斑。
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
from rasterio import features
from scipy import ndimage
from shapely.geometry import shape as shp_shape

from .settings import Thresholds


def nanmedian(stack: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """逐像元中值合成，忽略无效像元。"""
    a = np.where(valid, stack, np.nan).astype("float32")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out = np.nanmedian(a, axis=0)
    return out.astype("float32")


def robust_z(x: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """稳健 z 分数：用中位数与 MAD，抗异常值。"""
    v = x[valid & np.isfinite(x)]
    if v.size < 100:
        return np.zeros_like(x, dtype="float32")
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    scale = 1.4826 * mad if mad > 1e-6 else float(np.std(v)) or 1.0
    return ((x - med) / scale).astype("float32")


@dataclass
class Patch:
    patch_id: int
    geometry: Any
    area_m2: float
    mean_delta_bsi: float
    max_delta_bsi: float
    mean_ndvi_drop: float
    mean_ndbi: float
    mean_iron: float
    centroid_lon: float | None = None
    centroid_lat: float | None = None

    def as_properties(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "area_m2": round(self.area_m2, 1),
            "area_mu": round(self.area_m2 / 666.6667, 2),  # 亩，便于基层阅读
            "mean_delta_bsi": round(float(self.mean_delta_bsi), 4),
            "max_delta_bsi": round(float(self.max_delta_bsi), 4),
            "mean_ndvi_drop": round(float(self.mean_ndvi_drop), 4),
            "mean_ndbi": round(float(self.mean_ndbi), 4),
            "mean_iron": round(float(self.mean_iron), 3),
            "centroid_lon": self.centroid_lon,
            "centroid_lat": self.centroid_lat,
        }


def detect_patches(
    idx_now: dict[str, np.ndarray],
    idx_base: dict[str, np.ndarray],
    transform,
    crs,
    thr: Thresholds,
) -> list[Patch]:
    """对单期“当期指数”与“基线指数”做变化检测。"""
    bsi_now, bsi_base = idx_now["bsi"], idx_base["bsi"]
    ndvi_now, ndvi_base = idx_now["ndvi"], idx_base["ndvi"]
    valid = (
        idx_now["valid"]
        & idx_base["valid"]
        & np.isfinite(bsi_now)
        & np.isfinite(bsi_base)
        & np.isfinite(ndvi_now)
        & np.isfinite(ndvi_base)
    )

    delta = bsi_now - bsi_base
    drop = ndvi_base - ndvi_now
    z = robust_z(delta, valid)

    cand = (
        valid
        & (delta > thr.delta_bsi)
        & (drop > thr.ndvi_drop)
        & (bsi_now > thr.bsi_abs)
        & (idx_now["mndwi"] <= thr.max_mndwi)
        & (z > thr.z)
    )
    if not cand.any():
        return []

    px_area = abs(transform.a * transform.e)  # 像元面积 m²（UTM 投影）

    # 连通域标记，一次性算面积与统计量，避免逐图斑栅格化
    lab, n = ndimage.label(cand, structure=np.ones((3, 3), dtype=int))
    if n == 0:
        return []

    flat = lab.ravel()
    counts = np.bincount(flat, minlength=n + 1)

    def _sum(values: np.ndarray) -> np.ndarray:
        v = np.where(cand, values, 0.0).astype("float64").ravel()
        return np.bincount(flat, weights=v, minlength=n + 1)

    sum_delta = _sum(delta)
    sum_drop = _sum(drop)
    sum_ndbi = _sum(np.nan_to_num(idx_now["ndbi"]))
    sum_iron = _sum(np.nan_to_num(idx_now["iron"]))

    patches: list[Patch] = []
    pid = 0
    for label_id in range(1, n + 1):
        c = counts[label_id]
        if c == 0:
            continue
        area = c * px_area
        if area < thr.min_area_m2:
            continue
        pid += 1
        mask_i = lab == label_id
        geom = None
        for gj, _val in features.shapes(
            mask_i.astype("uint8"), mask=mask_i, transform=transform, connectivity=8
        ):
            geom = shp_shape(gj)
            if geom is not None:
                break
        if geom is None:
            continue

        cen = geom.centroid
        lon = lat = None
        try:
            from rasterio.warp import transform as warp_transform

            xs, ys = warp_transform(crs, "EPSG:4326", [cen.x], [cen.y])
            lon, lat = round(xs[0], 6), round(ys[0], 6)
        except Exception:
            pass

        patches.append(
            Patch(
                patch_id=pid,
                geometry=geom,
                area_m2=area,
                mean_delta_bsi=sum_delta[label_id] / c,
                max_delta_bsi=float(np.nanmax(delta[mask_i])),
                mean_ndvi_drop=sum_drop[label_id] / c,
                mean_ndbi=sum_ndbi[label_id] / c,
                mean_iron=sum_iron[label_id] / c,
                centroid_lon=lon,
                centroid_lat=lat,
            )
        )

    patches.sort(key=lambda p: p.area_m2, reverse=True)
    for i, p in enumerate(patches, 1):
        p.patch_id = i
    return patches

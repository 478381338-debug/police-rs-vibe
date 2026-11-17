"""结果输出：GeoTIFF、GeoJSON、CSV、PNG 快视图。"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.warp import transform_geom

from .anomalies import Patch


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_geotiff(path: Path, arr: np.ndarray, transform, crs) -> None:
    data = np.where(np.isfinite(arr), arr, -9999).astype("float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=-9999,
        compress="deflate",
    ) as dst:
        dst.write(data, 1)


def write_geojson(path: Path, patches: list[Patch], crs) -> None:
    """按 RFC7946 输出 WGS84 GeoJSON。"""
    feats = []
    for p in patches:
        geom = transform_geom(crs, "EPSG:4326", p.geometry.__geo_interface__)
        feats.append(
            {
                "type": "Feature",
                "geometry": geom,
                "properties": p.as_properties(),
            }
        )
    fc = {
        "type": "FeatureCollection",
        "name": "mining_suspects",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": feats,
    }
    path.write_text(json.dumps(fc, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, patches: list[Patch]) -> None:
    fields = list(patches[0].as_properties()) if patches else [
        "patch_id", "area_m2", "area_mu", "mean_delta_bsi", "max_delta_bsi",
        "mean_ndvi_drop", "mean_ndbi", "mean_iron", "centroid_lon", "centroid_lat",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for p in patches:
            w.writerow(p.as_properties())


def plot_quicklook(
    path: Path,
    bsi_now: np.ndarray,
    bsi_base: np.ndarray,
    delta: np.ndarray,
    patches: list[Patch],
    meta: dict[str, Any],
    transform=None,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2))
    panels = [
        (bsi_base, "BSI baseline (previous year)", "YlOrBr"),
        (bsi_now, "BSI current", "YlOrBr"),
        (delta, "BSI change (current - baseline)", "RdBu_r"),
    ]
    for ax, (arr, title, cmap) in zip(axes, panels):
        if "change" in title:
            im = ax.imshow(arr, cmap=cmap, vmin=-0.4, vmax=0.4)
        else:
            im = ax.imshow(arr, cmap=cmap, vmin=-0.2, vmax=0.7)
        ax.set_title(title, fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        plt.colorbar(im, ax=ax, fraction=0.035, pad=0.02)

    # 在变化图上画出图斑轮廓（地理坐标 -> 像元坐标）
    if transform is not None and patches:
        inv = ~transform
        ax = axes[2]
        for p in patches:
            geoms = getattr(p.geometry, "geoms", [p.geometry])
            for g in geoms:
                coords = list(g.exterior.coords)
                cols, rows = [], []
                for x, y in coords:
                    col, row = inv * (x, y)
                    cols.append(col)
                    rows.append(row)
                ax.plot(cols, rows, color="black", lw=0.9)

    fig.suptitle(
        f"{meta.get('aoi_name','')}  |  baseline {meta.get('baseline_dates','')}"
        f"  ->  target {meta.get('target_dates','')}  |  patches={len(patches)}",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def write_summary(path: Path, meta: dict[str, Any], patches: list[Patch]) -> None:
    total = sum(p.area_m2 for p in patches)
    lines = [
        f"# 疑似图斑摘要 — {meta.get('aoi_name','')}",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 区域范围：{meta.get('bbox')}",
        f"- 基线期：{meta.get('baseline_range')}（{meta.get('baseline_dates')}）",
        f"- 目标期：{meta.get('target_range')}（{meta.get('target_dates')}）",
        f"- 输出分辨率：{meta.get('resolution')} m",
        f"- 影像：{', '.join(meta.get('scene_ids', []))}",
        "",
        f"## 结果：{len(patches)} 个疑似图斑，合计 {total/10000:.2f} 公顷（{total/666.6667:.1f} 亩）",
        "",
    ]
    if patches:
        lines += [
            "| 序号 | 面积(亩) | ΔBSI | NDVI跌幅 | 铁染 | 经度 | 纬度 |",
            "|---|---|---|---|---|---|---|",
        ]
        for p in patches[:50]:
            pr = p.as_properties()
            lines.append(
                f"| {pr['patch_id']} | {pr['area_mu']} | {pr['mean_delta_bsi']} | "
                f"{pr['mean_ndvi_drop']} | {pr['mean_iron']} | "
                f"{pr['centroid_lon']} | {pr['centroid_lat']} |"
            )
        if len(patches) > 50:
            lines.append(f"| ... | 其余 {len(patches)-50} 个见图斑清单 CSV | | | | | |")
    else:
        lines.append("本期未检出满足阈值的图斑。若预期有活动，可适当放宽 `thresholds.delta_bsi`。")

    lines += [
        "",
        "## 判读提示",
        "",
        "- 本结果是**线索**，不是违法结论。合法复垦、道路施工、水库清淤在指数上高度相似。",
        "- 建议按四要素复核：持续存在 / 有运输道路 / 有作业设备 / 有 InSAR 沉降。",
        "- 图斑面积按 20m 像元统计，边界精度约 ±20m，定损需现场测绘。",
    ]

    bg = meta.get("background_delta")
    if bg is not None:
        lines.append(f"- 全图 ΔBSI 中位数 {bg:+.3f}（用于判断两期辐射是否可比）。")
        if abs(bg) > 0.05:
            lines += [
                "",
                "> ⚠ 两期影像存在整体亮度偏差，绝对阈值可靠性下降。"
                "常见原因是季节/物候、太阳高度角或大气差异（如 8 月农田与 6 月裸土）。"
                "建议改用同一物候窗口的多景中值合成（把 `max_scenes` 提到 3–5），"
                "或只做同区域内的相对排序、不做跨期绝对比较。",
            ]
    path.write_text("\n".join(lines), encoding="utf-8")

"""把本地 DEM 渲染成可核查的三维地形态势图（网页展示用）。

说明：ArcGIS Pro 的 Scene 相机在无界面 ArcPy 会话中不可写；这里使用同一份
DEM 做真实高程透视渲染，不冒充 ArcGIS Scene 截图。2D 专题图由 ArcGIS Pro 导出。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LightSource, Normalize

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "data" / "external"
DATA = ROOT / "data"
OUT = ROOT / "web" / "assets" / "arcgis_terrain_3d.png"
FONT = r"C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = r"C:/Windows/Fonts/msyhbd.ttc"


def font(size, bold=False):
    p = FONT_BOLD if bold and Path(FONT_BOLD).exists() else FONT
    return font_manager.FontProperties(fname=p, size=size)


def main():
    with rasterio.open(EXT / "dem_aoi.tif") as ds:
        arr = ds.read(1).astype("float64")
        arr[arr < -1000] = np.nan
        bounds = ds.bounds
        crs = ds.crs

    # 控制网格规模；按块平均而不是抽样，避免山脊出现锯齿/尖刺。
    step = max(1, arr.shape[1] // 240)
    h = (arr.shape[0] // step) * step
    w = (arr.shape[1] // step) * step
    z = arr[:h, :w].reshape(h // step, step, w // step, step).mean(axis=(1, 3))

    # 轻量平滑：不引入 scipy 依赖，用箱式滤波压掉 DEM 高频噪声。
    z = np.where(np.isfinite(z), z, np.nanmedian(z))

    def box_blur(a, k=5):
        pad = k // 2
        ap = np.pad(a, pad, mode="edge")
        cs = np.cumsum(np.cumsum(ap, axis=0), axis=1)
        cs = np.pad(cs, ((1, 0), (1, 0)))
        return (cs[k:, k:] - cs[:-k, k:] - cs[k:, :-k] + cs[:-k, :-k]) / (k * k)

    z = box_blur(z, 5)
    z = box_blur(z, 5)
    z = box_blur(z, 3)
    rows, cols = z.shape
    x = np.linspace(0, 1, cols)
    y = np.linspace(0, 1, rows)
    X, Y = np.meshgrid(x, y)

    lo, hi = np.nanpercentile(z, 2), np.nanpercentile(z, 98)
    norm = Normalize(vmin=lo, vmax=hi)
    ls = LightSource(azdeg=315, altdeg=42)
    rgb = ls.shade(z, cmap=plt.get_cmap("terrain"), norm=norm, blend_mode="soft")

    fig = plt.figure(figsize=(16, 10.2), dpi=120, facecolor="#070c12")
    ax = fig.add_subplot(111, projection="3d", facecolor="#070c12")
    ax.plot_surface(X, Y, z, facecolors=rgb, rstride=1, cstride=1,
                    linewidth=0, antialiased=False, shade=False, edgecolor="none")
    ax.view_init(elev=34, azim=-62)
    ax.set_box_aspect((1.0, 0.72, 0.16))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_zlim(lo, hi + (hi - lo) * 0.10)
    ax.set_axis_off()

    # 高发点（多边形取质心），按危险等级着色并投影到 DEM 表面。
    hot = json.loads((DATA / "hotspots.geojson").read_text(encoding="utf-8"))
    wb = json.loads((EXT / "dem_bounds.json").read_text(encoding="utf-8"))["bounds"]
    px, py, pz, colors, labels = [], [], [], [], []
    for f in hot.get("features", []):
        p = f.get("properties") or {}
        geom = f.get("geometry") or {}
        if geom.get("type") == "Polygon" and geom.get("coordinates"):
            ring = geom["coordinates"][0]
            lon = sum(pt[0] for pt in ring) / len(ring)
            lat = sum(pt[1] for pt in ring) / len(ring)
        else:
            continue
        xx = (lon - wb["west"]) / (wb["east"] - wb["west"])
        yy = (wb["north"] - lat) / (wb["north"] - wb["south"])
        if not (0 <= xx <= 1 and 0 <= yy <= 1):
            continue
        ix = min(cols - 1, int(xx * (cols - 1)))
        iy = min(rows - 1, int(yy * (rows - 1)))
        zz = z[iy, ix]
        if not np.isfinite(zz):
            continue
        px.append(xx)
        py.append(yy)
        pz.append(zz + (hi - lo) * 0.055)
        colors.append({"red": "#ff3b30", "orange": "#ff9500", "yellow": "#ffd60a",
                       "blue": "#0a84ff"}.get(p.get("danger_level"), "#38bdf8"))
        if p.get("rank", 99) <= 4:
            labels.append((xx, yy, zz + (hi - lo) * 0.10, p.get("name", "")[:10]))

    ax.scatter(px, py, pz, c=colors, s=110, depthshade=False, edgecolors="white",
               linewidths=1.0, zorder=1000)
    for xx, yy, zz, text in labels:
        ax.text(xx, yy, zz, text, color="#f2f7ff", fontsize=7,
                fontproperties=font(7), zorder=2000,
                bbox=dict(facecolor="#0b1622", alpha=0.62, edgecolor="none", pad=1.5))

    ax.set_title("警务遥感 · 三维地形态势（DEM）", color="#e7eef7", fontsize=26,
                 pad=6, fontproperties=font(26, True))
    fig.text(0.032, 0.935, "大青山研究区 · 高程着色 / 山体阴影 · 红/橙/黄/蓝为高发点危险等级",
             color="#8496a8", fontsize=13, fontproperties=font(13))
    fig.text(0.032, 0.055,
             "DEM: AWS Terrain Tiles / Mapzen Terrarium · 高发点: 本项目风险模型 · 2D 专题图: ArcGIS Pro 3.5",
             color="#8496a8", fontsize=10, fontproperties=font(10))
    base = json.loads((EXT / "dem_meta.json").read_text(encoding="utf-8"))
    fig.text(0.032, 0.028, f"DEM {base.get('width')}×{base.get('height')} px · {base.get('pixel_m')} m/px · EPSG:{crs.to_epsg() if crs else '3857'}",
             color="#5f7488", fontsize=9, fontproperties=font(9))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    print(f"[ok] {OUT} ({OUT.stat().st_size} B)", flush=True)


if __name__ == "__main__":
    main()

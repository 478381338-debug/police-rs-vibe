"""HyP3 INSAR_GAMMA 产品裁剪与统计（p07 采石场）。

HyP3 每个 job 会下载一个 zip 并解压到以 job 命名的目录，常见文件：
  *_los_disp.tif           LOS 向形变（m），正负号依视线方向
  *_vert_disp.tif          垂直向形变（m）
  *_unw_phase.tif          解缠相位（rad）
  *_corr.tif               相干性（0–1）
  *_dem.tif / *_inc_map.tif

用法：
  python scripts/hyp3_insar_analysis.py --root data/yantai_mvp/insar
  python scripts/hyp3_insar_analysis.py --root data/yantai_mvp/insar --aoi 120.185,37.125,120.245,37.175
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

DEFAULT_AOI = "120.185,37.125,120.245,37.175"


def window_for(src, aoi):
    b = transform_bounds("EPSG:4326", src.crs, *aoi)
    return from_bounds(*b, src.transform)


def stats(path: Path, aoi: tuple[float, float, float, float]):
    with rasterio.open(path) as src:
        a = src.read(1, window=window_for(src, aoi), masked=True)
    v = a.compressed()
    if v.size == 0:
        return None
    return {
        "file": str(path),
        "n": int(v.size),
        "min": float(v.min()),
        "max": float(v.max()),
        "mean": float(v.mean()),
        "median": float(np.median(v)),
        "p05": float(np.percentile(v, 5)),
        "p95": float(np.percentile(v, 95)),
        "std": float(v.std()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/yantai_mvp/insar")
    ap.add_argument("--aoi", default=DEFAULT_AOI, help="minx,miny,maxx,maxy (WGS84)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    aoi = tuple(float(x) for x in args.aoi.split(","))
    root = Path(args.root)
    out_dir = Path(args.out) if args.out else root
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for pattern in ("*los_disp.tif", "*vert_disp.tif", "*unw_phase.tif", "*corr.tif", "*inc_map.tif"):
        for p in sorted(root.rglob(pattern)):
            s = stats(p, aoi)
            if s:
                s["kind"] = pattern.strip("*").lstrip("_").replace(".tif", "")
                rows.append(s)
                print(s["kind"], p.name, "mean %.4f" % s["mean"], "n", s["n"])

    (out_dir / "hyp3_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 为每个 los_displacement 画一张裁剪图
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return

    for p in sorted(root.rglob("*los_disp.tif")):
        with rasterio.open(p) as src:
            a = src.read(1, window=window_for(src, aoi), masked=True)
        fig, ax = plt.subplots(figsize=(6, 5))
        vmax = max(abs(float(a.min())), abs(float(a.max())), 0.01)
        im = ax.imshow(a * 1000, cmap="RdBu_r", vmin=-vmax * 1000, vmax=vmax * 1000)
        ax.set_title(p.stem + " (mm)")
        fig.colorbar(im, ax=ax, label="LOS mm")
        fig.tight_layout()
        fig.savefig(out_dir / (p.stem + "_aoi.png"), dpi=150)
        print("saved", out_dir / (p.stem + "_aoi.png"))


if __name__ == "__main__":
    main()
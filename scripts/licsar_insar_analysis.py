"""LiCSAR 开放 InSAR 产品分析：下载 sltd.geo.tif 并做 AOI 时序/差分。

背景：烟台 p07 采石场（120.185–120.245E, 37.125–37.175N）落在
LiCSAR 帧 076D_05217_140715（降轨）内。该帧 epoch 覆盖 2016-10 至
2021-12，之后未再更新；2023–2026 的 SLC 需走 ASF HyP3（见
scripts/insar_hyp3_submit.py）。

LiCSAR 的 sltd.geo.tif 是 LOS 向累积位移（mm），绝对值和参考点/解缠
基准有关，跨 epoch 比较应看同一像素的差值，而不是绝对值本身。

用法：
  python scripts/licsar_insar_analysis.py --dates 20161005,20171024,20181019,20191014,20201020,20211015
  python scripts/licsar_insar_analysis.py --frame 069A_05428_181818 --aoi 119.8,37.1,119.95,37.25 --dates 20231030,20240526
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

BASE = "https://gws-access.jasmin.ac.uk/public/nceo_geohazards/LiCSAR_products.public"
DEFAULT_FRAME = "076D_05217_140715"
DEFAULT_DATES = "20161005,20171024,20181019,20191014,20201020,20211015"
DEFAULT_AOI = "120.185,37.125,120.245,37.175"


def path_dir(frame: str) -> str:
    # 帧 ID 前三位是 path 号，目录名不带前导零（003 -> 3，171 -> 171）
    return str(int(frame[:3]))


def download(frame: str, date: str, out_dir: Path) -> Path:
    import requests

    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{date}.sltd.geo.tif"
    if dst.exists() and dst.stat().st_size > 1_000_000:
        return dst
    url = f"{BASE}/{path_dir(frame)}/{frame}/epochs/{date}/{date}.sltd.geo.tif"
    print("download", url)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        tmp = dst.with_suffix(".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
        tmp.replace(dst)
    return dst


def read_aoi(path: Path, aoi: tuple[float, float, float, float]):
    with rasterio.open(path) as src:
        w = from_bounds(*aoi, src.transform)
        return src.read(1, window=w, masked=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", default=DEFAULT_FRAME)
    ap.add_argument("--dates", default=DEFAULT_DATES)
    ap.add_argument("--aoi", default=DEFAULT_AOI, help="minx,miny,maxx,maxy (WGS84)")
    ap.add_argument("--out", default="data/yantai_insar/076D")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    aoi = tuple(float(x) for x in args.aoi.split(","))
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    first = last = None
    for date in dates:
        p = download(args.frame, date, out_dir)
        a = read_aoi(p, aoi)
        v = a.compressed()
        row = {
            "date": date,
            "mean_mm": float(v.mean()),
            "median_mm": float(np.median(v)),
            "std_mm": float(v.std()),
            "min_mm": float(v.min()),
            "max_mm": float(v.max()),
            "n_valid": int(v.size),
        }
        rows.append(row)
        print(date, "mean %.2f mm  std %.2f  n=%d" % (row["mean_mm"], row["std_mm"], row["n_valid"]))
        if first is None:
            first, first_date = a, date
        last, last_date = a, date

    diff = last - first
    v = diff.compressed()
    summary = {
        "frame": args.frame,
        "aoi": aoi,
        "dates": dates,
        "time_series": rows,
        "difference": {
            "first": first_date,
            "last": last_date,
            "mean_mm": float(v.mean()),
            "std_mm": float(v.std()),
            "min_mm": float(v.min()),
            "max_mm": float(v.max()),
            "p05_mm": float(np.percentile(v, 5)),
            "p95_mm": float(np.percentile(v, 95)),
        },
    }
    (out_dir / "insar_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary["difference"], ensure_ascii=False, indent=2))

    if not args.no_plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        xs = [dt.datetime.strptime(r["date"], "%Y%m%d") for r in rows]
        ys = [r["mean_mm"] for r in rows]
        es = [r["std_mm"] for r in rows]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.errorbar(xs, ys, yerr=es, fmt="o-", capsize=3)
        ax.set_ylabel("LOS displacement (mm)")
        ax.set_title(f"LiCSAR {args.frame} AOI mean LOS")
        ax.grid(alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(out_dir / "insar_timeseries.png", dpi=150)

        fig, ax = plt.subplots(figsize=(6, 5))
        im = ax.imshow(diff, cmap="RdBu_r", vmin=-40, vmax=40)
        ax.set_title(f"LOS diff {first_date} -> {last_date} (mm)")
        fig.colorbar(im, ax=ax, label="mm")
        fig.tight_layout()
        fig.savefig(out_dir / "insar_diff.png", dpi=150)
        print("saved", out_dir / "insar_timeseries.png", out_dir / "insar_diff.png")


if __name__ == "__main__":
    main()
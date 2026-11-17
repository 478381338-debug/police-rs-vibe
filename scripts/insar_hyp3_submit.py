"""Sentinel-1 InSAR 形变：ASF HyP3 提交脚本（本机未运行，需 Earthdata 凭据）。

用途：把“最小可行组合”里缺的 InSAR 这一段跑起来。
本机没有 SNAP/MintPy/ISCE2，也没有 Earthdata 账号，所以只做到“数据可用性核查”：
ASF 检索到 p07 区域 2023-01 至 2026-11 有 89 景 IW SLC、升轨、相对轨道 171、约 12 天重访。

用法：
  pip install asf_search hyp3_sdk
  # 先干跑，只列出可处理的短基线对：
  python scripts/insar_hyp3_submit.py --dry-run
  # 设置 Earthdata 凭据后提交前 2 对做试验：
  set EARTHDATA_USERNAME=...
  set EARTHDATA_PASSWORD=...
  python scripts/insar_hyp3_submit.py --submit --max-pairs 2 --out data/yantai_mvp/insar

说明：
- HyP3 的 INSAR_GAMMA 产品是 LOS 向形变（米），不是垂直向；需要入射角/几何改正才能转垂直。
- 单对干涉图只能看 12 天内的形变；要判断“持续开采”，需要 20–30 景的 SBAS/PS 时序（MintPy）。
- 植被区 C 波段失相干严重；金矿硐采区若为林地，优先考虑 L 波段（陆探一号）或 DS-InSAR。
- 本脚本只提交、下载、统计，不做相位解缠之外的高级时序反演。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

BBOX = "120.185,37.125,120.245,37.175"  # p07 已知采石场试点
AOI_WKT = "POLYGON((120.185 37.125,120.245 37.125,120.245 37.175,120.185 37.175,120.185 37.125))"
REL_ORBIT = 171
FLIGHT_DIRECTION = "ASCENDING"


def search_slc(start: str, end: str):
    import asf_search as asf

    return asf.search(
        platform="Sentinel-1",
        processingLevel="SLC",
        beamMode="IW",
        flightDirection=FLIGHT_DIRECTION,
        relativeOrbit=REL_ORBIT,
        intersectsWith=AOI_WKT,
        start=start,
        end=end,
    )


def build_pairs(results, max_temporal_days: int = 24):
    rows = []
    for r in results:
        p = r.properties
        rows.append({
            "scene": p["sceneName"],
            "date": datetime.fromisoformat(p["startTime"].replace("Z", "+00:00")),
            "path": p.get("pathNumber"),
            "frame": p.get("frameNumber"),
        })
    rows.sort(key=lambda x: x["date"])
    pairs = []
    for a, b in zip(rows, rows[1:]):
        dt = (b["date"] - a["date"]).days
        if 6 <= dt <= max_temporal_days:
            pairs.append((a, b, dt))
    return rows, pairs


def submit(pairs, out_dir: Path, max_pairs: int):
    import hyp3_sdk as sdk

    hyp3 = sdk.HyP3()  # 读取 EARTHDATA_USERNAME / EARTHDATA_PASSWORD 或 .netrc
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for a, b, dt in pairs[:max_pairs]:
        name = f"YT_{a['date']:%Y%m%d}_{b['date']:%Y%m%d}"
        print("submit", name, a["scene"], b["scene"])
        job = hyp3.submit_insar_job(
            a["scene"], b["scene"], name=name,
            include_displacement_maps=True,
            include_dem=True,
            include_inc_map=True,
        )
        jobs.append(job)
    for job in jobs:
        job = hyp3.watch(job)
        print(job)
        if job.succeeded():
            files = job.download_files(location=str(out_dir))
            print("downloaded", len(files), "files")
    return jobs


def summarize(out_dir: Path):
    """统计 HyP3 产品中的 LOS 形变栅格范围；不做相位解缠。"""
    import numpy as np
    import rasterio

    rows = []
    for p in sorted(out_dir.rglob("*displacement*.tif")):
        with rasterio.open(p) as ds:
            a = ds.read(1, masked=True)
            rows.append({
                "file": str(p),
                "min_m": float(a.min()),
                "max_m": float(a.max()),
                "mean_m": float(a.mean()),
                "p05_m": float(np.percentile(a.compressed(), 5)),
                "p95_m": float(np.percentile(a.compressed(), 95)),
            })
    (out_dir / "insar_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01T00:00:00Z")
    ap.add_argument("--end", default="2026-11-13T00:00:00Z")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--max-pairs", type=int, default=2)
    ap.add_argument("--out", default="data/yantai_mvp/insar")
    args = ap.parse_args()

    results = search_slc(args.start, args.end)
    rows, pairs = build_pairs(results)
    print(f"SLC scenes: {len(rows)}; candidate 12-day pairs: {len(pairs)}")
    for a, b, dt in pairs[:20]:
        print(f"  {a['date']:%Y-%m-%d} -> {b['date']:%Y-%m-%d}  dt={dt}d  "
              f"{a['scene']} / {b['scene']}")
    if args.submit:
        if not (os.environ.get("EARTHDATA_USERNAME") or Path.home().joinpath(".netrc").exists()):
            raise SystemExit("需要 EARTHDATA_USERNAME/EARTHDATA_PASSWORD 或 ~/.netrc")
        submit(pairs, Path(args.out), args.max_pairs)
        summarize(Path(args.out))
    else:
        print("\n干跑结束。设置凭据后加 --submit 提交 HyP3 任务。")


if __name__ == "__main__":
    main()
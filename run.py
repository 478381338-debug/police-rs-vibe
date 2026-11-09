#!/usr/bin/env python
"""烟台非法采矿/采砂/采石 遥感预警 —— 一键入口。

用法：
    python run.py --aoi zhaoyuan                  # 全流程
    python run.py --aoi all                       # 所有重点区域
    python run.py --aoi zhaoyuan --stage fetch     # 只取数（落缓存）
    python run.py --aoi zhaoyuan --stage detect    # 只用缓存重跑判定
    python run.py --aoi zhaoyuan --stage report    # 只重出报告
    python run.py --check                          # 检查可选阶段环境

输出的 suspects.geojson 可直接拖进 QGIS / ArcGIS / Google Earth 核验。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from affine import Affine

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mining_watch import anomalies, climate, fetch, indices, report, stages  # noqa: E402
from mining_watch.settings import load_config  # noqa: E402

BANDS = ("blue", "green", "red", "nir", "swir16", "swir22", "scl")


# ------------------------------------------------------------------ 缓存读写
def save_cache(path: Path, base: dict, tgt: dict) -> None:
    np.savez_compressed(
        path,
        **{f"base_{k}": v for k, v in base["bands"].items()},
        **{f"tgt_{k}": v for k, v in tgt["bands"].items()},
        base_dates=np.array(base["dates"]),
        tgt_dates=np.array(tgt["dates"]),
        base_ids=np.array(base["ids"]),
        tgt_ids=np.array(tgt["ids"]),
        base_clouds=np.array(base["clouds"], dtype="float32"),
        tgt_clouds=np.array(tgt["clouds"], dtype="float32"),
        transform=np.array(list(base["transform"])[:6], dtype="float64"),
        crs=np.array(str(base["crs"])),
        resolution=np.array(base["resolution"]),
    )


def load_cache(path: Path) -> tuple[dict, dict]:
    z = np.load(path, allow_pickle=False)
    grid = tuple(z["transform"].tolist())
    crs = str(z["crs"])
    res = int(z["resolution"])

    def _side(prefix: str) -> dict:
        blue = z[f"{prefix}_blue"]
        return {
            "bands": {b: z[f"{prefix}_{b}"] for b in BANDS},
            "dates": [str(d) for d in z[f"{prefix}_dates"]],
            "ids": [str(i) for i in z[f"{prefix}_ids"]],
            "clouds": z[f"{prefix}_clouds"].tolist(),
            "transform": Affine(*grid),
            "crs": crs,
            "shape": (int(blue.shape[-2]), int(blue.shape[-1])),
            "resolution": res,
        }

    return _side("base"), _side("tgt")


# ------------------------------------------------------------------ 合成
def composite(idx: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """把 (n,H,W) 的指数序列合成单张 (H,W)，只统计有效像元。"""
    v = idx["valid"]
    out = {k: anomalies.nanmedian(idx[k], v) for k in idx if k != "valid"}
    out["valid"] = v.any(axis=0)
    return out


# ------------------------------------------------------------------ 气候阶段
def _range_bounds(spec: str) -> tuple[str, str]:
    parts = str(spec).split("/")
    if len(parts) != 2:
        raise ValueError(f"日期区间应形如 YYYY-MM-DD/YYYY-MM-DD：{spec!r}")
    return parts[0].strip(), parts[1].strip()


def run_climate(cfg, key: str) -> None:
    """按区域取历史气候，评估云/雨/雪对观测能力的影响。

    这一阶段不依赖卫星缓存，可直接运行；它给出该区域在目标期的
    可观测窗口、分月气候统计和按采矿类型的探测能力估计。
    """
    aoi = cfg.aoi(key)
    ccfg = cfg.climate or {}
    profile = climate.profile_for(cfg.aoi_types.get(key) or climate.AOI_TYPE_HINTS.get(key))
    outdir = report.ensure_dir(cfg.out_dir / key)
    lon = (aoi.bbox[0] + aoi.bbox[2]) / 2.0
    lat = (aoi.bbox[1] + aoi.bbox[3]) / 2.0
    start = _range_bounds(cfg.baseline)[0]
    end = _range_bounds(cfg.target)[1]
    cache_dir = Path(ccfg.get("cache_dir", "data/climate"))
    cache_path = (Path(__file__).resolve().parent / cache_dir / key / "daily.json") if not cache_dir.is_absolute() else cache_dir / key / "daily.json"

    print(f"[{key}] 气候取数 {start}..{end} @ ({lat:.4f}, {lon:.4f})")
    doc = climate.fetch_open_meteo(lat, lon, start, end, cache_path=cache_path, endpoint=str(ccfg.get("endpoint", climate.ARCHIVE_URL)))
    rows = climate.build_daily_features(doc)
    target_start, target_end = _range_bounds(cfg.target)
    target_rows = [r for r in rows if target_start <= str(r.get("date")) <= target_end]
    revisit = max(1, int(ccfg.get("revisit_days", 5)))
    sampled = target_rows[::revisit]
    usable_dates = []
    for row in sampled:
        q = climate.build_quality(
            valid_frac=None,
            cloud_pct=row.get("cloud_cover"),
            snow_depth_cm=row.get("snow_depth_cm"),
        )
        if climate.scene_observation(profile, row, q)["usable_optical"]:
            usable_dates.append(row["date"])
    gaps = [
        (np.datetime64(b) - np.datetime64(a)).astype("timedelta64[D]").astype(int)
        for a, b in zip(usable_dates, usable_dates[1:])
    ]
    max_gap = int(max(gaps)) if gaps else revisit
    power = climate.detection_power(profile, len(usable_dates), max_gap)

    monthly: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        monthly[str(row.get("date"))[:7]].append(row)
    month_rows = []
    for month in sorted(monthly):
        items = monthly[month]
        precip = sum(float(r.get("precip_1d") or 0.0) for r in items)
        snowfall = sum(float(r.get("snowfall_1d") or 0.0) for r in items)
        snow_depth = max(float(r.get("snow_depth_cm") or 0.0) for r in items)
        cloud = float(np.mean([float(r.get("cloud_cover") or 0.0) for r in items]))
        temp = float(np.mean([float(r.get("temp_mean") or 0.0) for r in items]))
        usable_days = 0
        for row in items:
            q = climate.build_quality(valid_frac=None, cloud_pct=row.get("cloud_cover"), snow_depth_cm=row.get("snow_depth_cm"))
            if climate.scene_observation(profile, row, q)["usable_optical"]:
                usable_days += 1
        month_rows.append(
            {
                "month": month,
                "precip_mm": round(precip, 1),
                "snowfall_cm": round(snowfall, 1),
                "snow_depth_max_cm": round(snow_depth, 1),
                "cloud_cover_mean_pct": round(cloud, 1),
                "temp_mean_c": round(temp, 1),
                "optical_usable_days": usable_days,
                "n_days": len(items),
            }
        )

    payload = {
        "aoi": key,
        "aoi_name": aoi.name,
        "bbox": list(aoi.bbox),
        "center": [lon, lat],
        "period": {"start": start, "end": end},
        "target": cfg.target,
        "profile": {
            "key": profile["key"],
            "label": profile["label"],
            "primary": profile["primary"],
            "secondary": profile["secondary"],
            "primary_modality": profile["primary_modality"],
            "clear_min": profile["clear_min"],
            "max_gap_days": profile["max_gap_days"],
        },
        "monthly": month_rows,
        "target_observation": {
            "sampled_days": len(sampled),
            "revisit_days": revisit,
            "usable_optical_samples": len(usable_dates),
            "max_gap_days": max_gap if gaps else None,
            "detection_power": power,
        },
        "note": "天气代理来自 Open-Meteo 网格日值；探测能力是观测机会估计，不是精度指标",
    }
    out_json = outdir / "climate.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (outdir / "climate_monthly.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(month_rows[0]) if month_rows else ["month"])
        writer.writeheader()
        writer.writerows(month_rows)
    print(
        f"[{key}] 气候阶段完成：可用光学抽样 {len(usable_dates)}/{len(sampled)}，"
        f"探测能力 {power['level']}（power={power['power']}）"
    )
    print(f"[{key}] 输出：{out_json.name} / climate_monthly.csv")


# ------------------------------------------------------------------ 主流程
def run_aoi(cfg, key: str, stage: str) -> None:
    if stage == "climate":
        run_climate(cfg, key)
        return
    aoi = cfg.aoi(key)
    outdir = report.ensure_dir(cfg.out_dir / key)
    cache = outdir / "cache.npz"
    t0 = time.time()

    need_fetch = stage in ("fetch", "all") or not cache.exists()
    if need_fetch:
        fetch.configure_transport()
        print(f"[{key}] 检索基线期 {cfg.baseline} ...")
        base_items = fetch.pick_scenes(
            fetch.search_items(
                cfg.stac_endpoint, cfg.collection, aoi.bbox,
                cfg.baseline, cfg.max_cloud,
            ),
            cfg.max_scenes,
        )
        print(f"[{key}] 检索目标期 {cfg.target} ...")
        tgt_items = fetch.pick_scenes(
            fetch.search_items(
                cfg.stac_endpoint, cfg.collection, aoi.bbox,
                cfg.target, cfg.max_cloud,
            ),
            cfg.max_scenes,
        )
        print(f"[{key}] 基线 {len(base_items)} 景 / 目标 {len(tgt_items)} 景，开始读取 ...")

        base = fetch.read_stack(base_items, aoi.bbox, cfg.resolution)
        grid = (base["crs"], base["transform"], *base["shape"])
        # 目标期强制沿用基线网格，跨瓦片也能逐像元比对
        tgt = fetch.read_stack(tgt_items, aoi.bbox, cfg.resolution, grid=grid)
        save_cache(cache, base, tgt)
        print(f"[{key}] 缓存已写入 {cache.name}（{time.time()-t0:.0f}s）")
    else:
        print(f"[{key}] 复用缓存 {cache.name}")
        base, tgt = load_cache(cache)

    if stage == "fetch":
        print(f"[{key}] 完成取数：{base['dates']} -> {tgt['dates']}")
        return

    # 指数
    idx_base = composite(indices.compute_all(base["bands"]))
    idx_now = composite(indices.compute_all(tgt["bands"]))
    print(f"[{key}] 指数计算完成，网格 {base['shape']}")

    # 检测
    patches = anomalies.detect_patches(
        idx_now, idx_base, base["transform"], base["crs"], cfg.thresholds
    )
    total = sum(p.area_m2 for p in patches)
    print(f"[{key}] 检出 {len(patches)} 个疑似图斑，合计 {total/10000:.2f} 公顷")

    if stage == "detect":
        # 仅落矢量，便于反复调阈值
        report.write_geojson(outdir / "suspects.geojson", patches, base["crs"])
        report.write_csv(outdir / "suspects.csv", patches)
        return

    # 报告
    delta = idx_now["bsi"] - idx_base["bsi"]
    report.save_geotiff(outdir / "bsi_current.tif", idx_now["bsi"], base["transform"], base["crs"])
    report.save_geotiff(outdir / "bsi_baseline.tif", idx_base["bsi"], base["transform"], base["crs"])
    report.save_geotiff(outdir / "bsi_delta.tif", delta, base["transform"], base["crs"])
    report.write_geojson(outdir / "suspects.geojson", patches, base["crs"])
    report.write_csv(outdir / "suspects.csv", patches)

    meta = {
        "aoi_name": aoi.name,
        "bbox": list(aoi.bbox),
        "baseline_range": cfg.baseline,
        "target_range": cfg.target,
        "baseline_dates": " / ".join(base["dates"]),
        "target_dates": " / ".join(tgt["dates"]),
        "resolution": cfg.resolution,
        "scene_ids": base["ids"] + tgt["ids"],
        # 全图中位数用于判断两期辐射是否可比（物候/大气差异会整体平移）
        "background_delta": float(np.nanmedian(delta)),
    }
    print(f"[{key}] 全图 ΔBSI 中位数 {meta['background_delta']:+.3f}")
    report.plot_quicklook(
        outdir / "quicklook.png", idx_now["bsi"], idx_base["bsi"], delta, patches, meta
    )
    report.write_summary(outdir / "summary.md", meta, patches)
    print(f"[{key}] 输出：{outdir}（{time.time()-t0:.0f}s）")


def main() -> int:
    ap = argparse.ArgumentParser(description="烟台非法采矿遥感预警流水线")
    ap.add_argument("--aoi", default="zhaoyuan", help="区域 key，或 all")
    ap.add_argument(
        "--stage",
        default="all",
        choices=["all", "fetch", "detect", "report", "climate", "yolo", "insar"],
    )
    ap.add_argument("--config", default=None)
    ap.add_argument("--check", action="store_true", help="检查可选阶段环境")
    args = ap.parse_args()

    if args.check:
        for name, fn in (("YOLO", stages.check_yolo), ("InSAR", stages.check_insar)):
            ok, msg = fn()
            print(f"=== {name}: {'可用' if ok else '不可用'} ===\n{msg}\n")
        return 0

    cfg = load_config(args.config)
    keys = list(cfg.aois) if args.aoi == "all" else [args.aoi]

    only_optional = args.stage in ("yolo", "insar")
    failures: list[tuple[str, Exception]] = []
    for k in keys:
        try:
            if args.stage == "yolo":
                stages.run_yolo(cfg.aoi(k).bbox, cfg.out_dir / k)
            elif args.stage == "insar":
                stages.run_insar(cfg.aoi(k).bbox, cfg.out_dir / k)
            else:
                run_aoi(cfg, k, args.stage)
        except Exception as exc:
            # 单个区域失败不该拖垮整批
            failures.append((k, exc))
            print(f"[{k}] 失败：{type(exc).__name__}: {exc}")

    if failures:
        print("失败区域：" + "、".join(k for k, _ in failures))
        return 1
    return 0 if only_optional or keys else 1


if __name__ == "__main__":
    raise SystemExit(main())

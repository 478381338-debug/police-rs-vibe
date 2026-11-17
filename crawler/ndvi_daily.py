"""逐格网 MODIS 每日 NBAR NDVI（500 m）。

真实数据源：Microsoft Planetary Computer `modis-43A4-061`。
MCD43A4 是每日 BRDF 校正后的 Nadir 反射率产品；虽然反演使用 16 天滚动
观测窗口，但产品按日更新，不等同于 16 天合成 NDVI。这里用红波段
(Nadir_Reflectance_Band1) 和近红外波段 (Band2) 计算 NDVI，并读取
BRDF 质量波段；无效像元不伪造，回退到有效中位数并标记。

输出与 16 天版本相同结构，供 `app/current_risk.py` 直接读取。
"""
from __future__ import annotations

import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import planetary_computer as pc
import rasterio
import requests
from rasterio.warp import transform

from app import config

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "modis-43A4-061"
CACHE = config.DATA / "external" / "ndvi_daily_cache"
OUT_JSON = config.DATA / "external" / "ndvi_grid.json"
OUT_GEOJSON = config.DATA / "external" / "ndvi_grid.geojson"
BACKUP = config.DATA / "external" / "ndvi_grid_16day.json"
TILES = ("h26v04", "h26v05")


def _start(item: dict) -> str:
    p = item.get("properties") or {}
    return str(p.get("datetime") or p.get("start_datetime") or "")


def _search_items() -> list[dict]:
    r = requests.post(
        f"{STAC}/search",
        json={
            "collections": [COLLECTION],
            "bbox": list(config.AOI_BBOX),
            "limit": 80,
            "sortby": [{"field": "datetime", "direction": "desc"}],
        },
        timeout=60,
    )
    r.raise_for_status()
    picked: dict[str, dict] = {}
    for it in r.json().get("features") or []:
        parts = (it.get("id") or "").split(".")
        if len(parts) < 3:
            continue
        tile = parts[2]
        if tile not in TILES:
            continue
        prev = picked.get(tile)
        if prev is None or _start(it) > _start(prev):
            picked[tile] = it
    return [picked[t] for t in TILES if t in picked]


def _download(item: dict, key: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 100_000:
        return
    signed = pc.sign(item)
    href = signed["assets"][key]["href"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with requests.get(href, stream=True, timeout=180) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                if chunk:
                    f.write(chunk)
    tmp.replace(path)


def _grid_points() -> list[dict]:
    raw = json.loads((config.DATA / "risk_grid.geojson").read_text(encoding="utf-8"))
    out = []
    for f in raw.get("features") or []:
        p = f.get("properties") or {}
        coords = ((f.get("geometry") or {}).get("coordinates") or [])
        ring = coords[0] if coords and isinstance(coords[0], list) else []
        if not ring:
            continue
        out.append({
            "cell_id": p.get("cell_id"),
            "lon": sum(pt[0] for pt in ring) / len(ring),
            "lat": sum(pt[1] for pt in ring) / len(ring),
        })
    return out


def _sample_tile(red_path: Path, nir_path: Path, qa_path: Path, points: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with rasterio.open(red_path) as red, rasterio.open(nir_path) as nir, rasterio.open(qa_path) as qa:
        xs, ys = transform("EPSG:4326", red.crs, [p["lon"] for p in points], [p["lat"] for p in points])
        red_v = [int(v[0]) for v in red.sample(zip(xs, ys))]
        nir_v = [int(v[0]) for v in nir.sample(zip(xs, ys))]
        qa_v = [int(v[0]) for v in qa.sample(zip(xs, ys))]
    for p, r, n, q in zip(points, red_v, nir_v, qa_v):
        # QA: 0 full BRDF inversion, 1 magnitude inversion, 255 fill.
        if q not in (0, 1) or r <= 0 or n <= 0 or r >= 10000 or n >= 10000:
            continue
        ndvi = (n - r) / float(n + r)
        if not math.isfinite(ndvi) or ndvi < -0.2 or ndvi > 1.0:
            continue
        out[p["cell_id"]] = {
            "ndvi": round(ndvi, 4),
            "qa": q,
            "quality": "valid_high" if q == 0 else "valid_magnitude",
            "raw_red": r,
            "raw_nir": n,
        }
    return out


def refresh() -> dict:
    items = _search_items()
    if not items:
        raise RuntimeError("No MODIS MCD43A4 items over AOI")
    points = _grid_points()
    if not points:
        raise RuntimeError("risk_grid.geojson has no polygon cells")

    # 保留旧 16 天版本，便于追溯和回退。
    if OUT_JSON.exists() and not BACKUP.exists():
        BACKUP.write_bytes(OUT_JSON.read_bytes())

    per_tile: list[tuple[dict, dict[str, dict]]] = []
    for item in items:
        tile = (item.get("id") or "").split(".")[2]
        base = CACHE / tile
        base.mkdir(parents=True, exist_ok=True)
        red = base / "red.tif"
        nir = base / "nir.tif"
        qa = base / "qa.tif"
        _download(item, "Nadir_Reflectance_Band1", red)
        _download(item, "Nadir_Reflectance_Band2", nir)
        _download(item, "BRDF_Albedo_Band_Mandatory_Quality_Band1", qa)
        per_tile.append((item, _sample_tile(red, nir, qa, points)))

    merged: dict[str, dict] = {}
    for item, vals in per_tile:
        for cid, v in vals.items():
            if cid not in merged:
                v = dict(v)
                v.update({
                    "item_id": item.get("id"),
                    "tile": (item.get("id") or "").split(".")[2],
                    "date": _start(item),
                })
                merged[cid] = v

    all_valid = [v["ndvi"] for v in merged.values()]
    fallback = round(statistics.median(all_valid), 4) if all_valid else 0.45
    cells = []
    for p in points:
        v = merged.get(p["cell_id"])
        if v:
            cells.append({
                "cell_id": p["cell_id"], "lon": round(p["lon"], 5), "lat": round(p["lat"], 5),
                "ndvi": v["ndvi"], "quality": v["quality"], "qa": v["qa"],
                "item_id": v["item_id"], "tile": v["tile"], "date": v["date"],
            })
        else:
            cells.append({
                "cell_id": p["cell_id"], "lon": round(p["lon"], 5), "lat": round(p["lat"], 5),
                "ndvi": fallback, "quality": "fallback_median", "qa": None,
                "item_id": None, "tile": None, "date": None,
            })

    dates = sorted({_start(it) for it, _ in per_tile if _start(it)})
    payload = {
        "source": "MODIS MCD43A4 061 · 500m 每日 NBAR NDVI（16 天滚动反演窗口）",
        "provider": "Microsoft Planetary Computer / NASA MODIS",
        "license": "NASA MODIS open data; see NASA LP DAAC citation",
        "product_type": "daily_nbar",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": list(config.AOI_BBOX),
        "items": [
            {"id": it.get("id"), "tile": (it.get("id") or "").split(".")[2],
             "start": _start(it), "end": (it.get("properties") or {}).get("end_datetime")}
            for it, _ in per_tile
        ],
        "observation_dates": dates,
        "composite_dates": dates,  # 兼容前端旧字段
        "grid_count": len(cells),
        "valid_count": sum(1 for c in cells if c["quality"].startswith("valid")),
        "fallback_count": sum(1 for c in cells if not c["quality"].startswith("valid")),
        "fallback_ndvi": fallback,
        "note": "每日 NBAR 产品，按日更新；反演使用 16 天滚动观测窗口，不是瞬时单日观测。无有效像元时用中位数回退并标记 quality。",
        "cells": cells,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
        "properties": {k: c[k] for k in ("cell_id", "ndvi", "quality", "qa", "date", "tile")},
    } for c in cells]
    OUT_GEOJSON.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "MODIS MCD43A4 daily NBAR NDVI 500m grid samples",
        "features": features,
    }, ensure_ascii=False), encoding="utf-8")
    return payload


def ensure(max_age_days: int = 2, force: bool = False) -> dict:
    if not force and OUT_JSON.exists():
        try:
            d = json.loads(OUT_JSON.read_text(encoding="utf-8"))
            gen = datetime.fromisoformat((d.get("generated_at") or "").replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - gen).total_seconds() / 86400
            if d.get("product_type") == "daily_nbar" and age < max_age_days and d.get("valid_count", 0) > 0:
                return d
        except Exception:
            pass
    return refresh()


if __name__ == "__main__":
    t = time.time()
    d = refresh()
    print(json.dumps({k: d[k] for k in ("grid_count", "valid_count", "fallback_count", "observation_dates")}, ensure_ascii=False))
    print(f"done in {time.time() - t:.1f}s -> {OUT_JSON}")

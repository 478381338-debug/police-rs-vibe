"""逐格网 MODIS NDVI（500m 16-day）提取。

真实数据源：Microsoft Planetary Computer `modis-13A1-061`。
AOI 跨 h26v04/h26v05 两景，分别采样后按质量合并；结果写到
`data/external/ndvi_grid.json` 和 `ndvi_grid.geojson`。

设计原则：
- 只使用带 QA 的有效像元；没有有效像元时回退到本地中位数，并明确记录；
- 不把固定 0.45 伪装成遥感值；
- NDVI 是 16 天合成，不是逐日观测，页面上必须显示合成期。
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
COLLECTION = "modis-13A1-061"
CACHE = config.DATA / "external" / "ndvi_cache"
OUT_JSON = config.DATA / "external" / "ndvi_grid.json"
OUT_GEOJSON = config.DATA / "external" / "ndvi_grid.geojson"
TILES = ("h26v04", "h26v05")


def _search_items() -> list[dict]:
    r = requests.post(
        f"{STAC}/search",
        json={
            "collections": [COLLECTION],
            "bbox": list(config.AOI_BBOX),
            "limit": 40,
            "sortby": [{"field": "datetime", "direction": "desc"}],
        },
        timeout=60,
    )
    r.raise_for_status()
    items = r.json().get("features") or []
    # 每景取一个最新 item；h26v04 覆盖 40–50N，h26v05 覆盖 30–40N。
    picked: dict[str, dict] = {}
    for it in items:
        tid = (it.get("id") or "").split(".")[2] if len((it.get("id") or "").split(".")) > 2 else ""
        if tid not in TILES:
            continue
        # 优先 Aqua 最新，其次任何最新；按开始日期比较。
        dt = (it.get("properties") or {}).get("start_datetime") or ""
        cur = picked.get(tid)
        if cur is None or dt > ((cur.get("properties") or {}).get("start_datetime") or ""):
            picked[tid] = it
    return [picked[t] for t in TILES if t in picked]


def _download(item: dict, key: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 1_000_000:
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
        lon = sum(pt[0] for pt in ring) / len(ring)
        lat = sum(pt[1] for pt in ring) / len(ring)
        out.append({"cell_id": p.get("cell_id"), "lon": lon, "lat": lat})
    return out


def _qa_ok(qa: int) -> bool:
    # MODLAND QA bits 0-1: 0 good, 1 marginal, 2 cloudy, 3 not produced.
    return (int(qa) & 0b11) <= 1


def _sample_tile(path: Path, points: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with rasterio.open(path) as ds:
        xs, ys = transform(
            "EPSG:4326", ds.crs,
            [p["lon"] for p in points],
            [p["lat"] for p in points],
        )
        ndvi_vals = [v[0] for v in ds.sample(zip(xs, ys))]
    qa_path = path.with_name("qa.tif")
    with rasterio.open(qa_path) as ds:
        xs, ys = transform(
            "EPSG:4326", ds.crs,
            [p["lon"] for p in points],
            [p["lat"] for p in points],
        )
        qa_vals = [int(v[0]) for v in ds.sample(zip(xs, ys))]
    for p, raw, qa in zip(points, ndvi_vals, qa_vals):
        raw = int(raw)
        if raw < -2000 or raw > 10000 or not _qa_ok(qa):
            continue
        out[p["cell_id"]] = {
            "ndvi": round(raw / 10000.0, 4),
            "raw": raw,
            "qa": qa,
        }
    return out


def refresh() -> dict:
    items = _search_items()
    if not items:
        raise RuntimeError("No MODIS 13A1 items over AOI")
    points = _grid_points()
    if not points:
        raise RuntimeError("risk_grid.geojson has no polygon cells")
    per_tile: list[tuple[dict, dict[str, dict]]] = []
    for item in items:
        tile = (item.get("id") or "").split(".")[2]
        base = CACHE / tile
        base.mkdir(parents=True, exist_ok=True)
        ndvi_path = base / "ndvi.tif"
        qa_path = base / "qa.tif"
        _download(item, "500m_16_days_NDVI", ndvi_path)
        _download(item, "500m_16_days_VI_Quality", qa_path)
        per_tile.append((item, _sample_tile(ndvi_path, points)))

    merged: dict[str, dict] = {}
    for item, vals in per_tile:
        for cid, v in vals.items():
            if cid not in merged:
                v = dict(v)
                v.update({
                    "item_id": item.get("id"),
                    "tile": (item.get("id") or "").split(".")[2],
                    "date": (item.get("properties") or {}).get("start_datetime"),
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
                "ndvi": v["ndvi"], "quality": "valid", "qa": v["qa"],
                "item_id": v["item_id"], "tile": v["tile"], "date": v["date"],
            })
        else:
            cells.append({
                "cell_id": p["cell_id"], "lon": round(p["lon"], 5), "lat": round(p["lat"], 5),
                "ndvi": fallback, "quality": "fallback_median", "qa": None,
                "item_id": None, "tile": None, "date": None,
            })

    dates = sorted({(it.get("properties") or {}).get("start_datetime") for it, _ in per_tile if (it.get("properties") or {}).get("start_datetime")})
    payload = {
        "source": "MODIS 13A1 061 · 500m 16-day NDVI",
        "provider": "Microsoft Planetary Computer / NASA MODIS",
        "license": "NASA MODIS open data; see NASA LP DAAC citation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": list(config.AOI_BBOX),
        "items": [
            {
                "id": it.get("id"), "tile": (it.get("id") or "").split(".")[2],
                "start": (it.get("properties") or {}).get("start_datetime"),
                "end": (it.get("properties") or {}).get("end_datetime"),
            }
            for it, _ in per_tile
        ],
        "composite_dates": dates,
        "grid_count": len(cells),
        "valid_count": sum(1 for c in cells if c["quality"] == "valid"),
        "fallback_count": sum(1 for c in cells if c["quality"] != "valid"),
        "fallback_ndvi": fallback,
        "note": "16 天合成 NDVI；不是逐日观测。无有效像元的格网用中位数回退，并在 quality 标记。",
        "cells": cells,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    features = []
    for c in cells:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
            "properties": {k: c[k] for k in ("cell_id", "ndvi", "quality", "qa", "date", "tile")},
        })
    OUT_GEOJSON.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "MODIS NDVI 500m 16-day grid samples",
        "features": features,
    }, ensure_ascii=False), encoding="utf-8")
    return payload


def ensure(max_age_days: int = 7, force: bool = False) -> dict:
    if not force and OUT_JSON.exists():
        try:
            d = json.loads(OUT_JSON.read_text(encoding="utf-8"))
            gen = datetime.fromisoformat((d.get("generated_at") or "").replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - gen).total_seconds() / 86400
            if age < max_age_days and d.get("valid_count", 0) > 0:
                return d
        except Exception:
            pass
    return refresh()


if __name__ == "__main__":
    t = time.time()
    d = refresh()
    print(json.dumps({k: d[k] for k in ("grid_count", "valid_count", "fallback_count", "composite_dates")}, ensure_ascii=False))
    print(f"done in {time.time() - t:.1f}s -> {OUT_JSON}")

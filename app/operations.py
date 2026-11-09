"""五类扩展场景的研判逻辑。

这里只做“桌面推演/辅助研判”，不接入真实警务数据，也不替代空域、
消防、应急或法制部门的正式审批。所有结果都带边界说明。
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import requests

from app import case_store, config

DATA = config.DATA
CACHE_LOCK = __import__("threading").Lock()
_CACHE: dict[str, tuple[float, Any]] = {}

HAZARDS = {
    "fire_risk": {"name": "森林火险", "color": "#ff5b52"},
    "flood_risk": {"name": "洪涝风险", "color": "#5aa9ff"},
    "drought_risk": {"name": "干旱风险", "color": "#ffa726"},
    "geohazard_risk": {"name": "地质灾害风险", "color": "#c084fc"},
}
LEVEL_ORDER = ["一级", "二级", "三级", "常态"]
ADVICE = {
    "fire_risk": "核查入山火源、防火隔离带和可燃物；关注大风时段和临近坟地/农事用火。",
    "flood_risk": "关注低洼路段、河道、桥涵和沟口；提前核对转移路线与警戒水位。",
    "drought_risk": "关注水源地、干枯植被和农事用火；核查应急供水与火险叠加。",
    "geohazard_risk": "关注边坡、沟口、切坡建房和施工扰动；雨前雨后加密巡查。",
}
DISCLAIMER = (
    "结果为桌面推演，使用公开遥感/气象/路网数据生成；不构成官方预警、"
    "安保方案、飞行计划或执法依据，现场必须由主管部门复核。"
)


def _load_json(name: str) -> Any:
    path = DATA / name
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with CACHE_LOCK:
        hit = _CACHE.get(name)
        if hit and hit[0] == mtime:
            return hit[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    with CACHE_LOCK:
        _CACHE[name] = (mtime, data)
    return data


def _features(name: str) -> list[dict]:
    data = _load_json(name)
    return data.get("features", []) if isinstance(data, dict) else []


def _center(feature: dict) -> tuple[float, float] | None:
    geom = feature.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype == "Point" and coords:
        return float(coords[0]), float(coords[1])
    ring = None
    if gtype == "Polygon" and coords:
        ring = coords[0]
    elif gtype == "MultiPolygon" and coords:
        ring = coords[0][0] if coords[0] else None
    if not ring:
        return None
    xs = [float(p[0]) for p in ring]
    ys = [float(p[1]) for p in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _destination(lon: float, lat: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    r = 6371.0088
    brng = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    dr = distance_km / r
    lat2 = math.asin(math.sin(lat1) * math.cos(dr) + math.cos(lat1) * math.sin(dr) * math.cos(brng))
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(dr) * math.cos(lat1),
        math.cos(dr) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lon2), math.degrees(lat2)


def _circle(lon: float, lat: float, radius_km: float, steps: int = 72) -> dict:
    ring = [_destination(lon, lat, i * 360 / steps, radius_km) for i in range(steps)]
    ring.append(ring[0])
    return {
        "type": "Feature",
        "properties": {"radius_km": radius_km},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def _police() -> list[dict]:
    out = []
    for f in _features("police.geojson"):
        c = _center(f)
        if not c:
            continue
        p = f.get("properties") or {}
        out.append({"name": p.get("name") or "警务点", "lon": c[0], "lat": c[1], "type": p.get("type")})
    return out


def _hotspots() -> list[dict]:
    out = []
    for f in _features("hotspots.geojson"):
        c = _center(f)
        if not c:
            continue
        p = f.get("properties") or {}
        out.append({
            "id": p.get("id"), "name": p.get("name"), "lon": c[0], "lat": c[1],
            "priority_score": p.get("priority_score"), "priority_label": p.get("priority_label"),
            "dominant_class": p.get("dominant_class"), "danger_label": p.get("danger_label"),
            "current_danger": p.get("current_danger"), "nearest_police": p.get("nearest_police"),
            "dist_police_km": p.get("dist_police_km"),
        })
    return out


def _grid() -> list[dict]:
    out = []
    for f in _features("risk_grid.geojson"):
        c = _center(f)
        if not c:
            continue
        p = f.get("properties") or {}
        hazard = p.get("current_hazard") or p.get("dominant_hazard")
        if hazard not in HAZARDS:
            continue
        score = p.get("current_danger") or p.get("current_composite_score") or p.get("natural_risk") or 0
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        label = p.get("current_danger_label") or ""
        level = "一级" if score >= 70 else "二级" if score >= 60 else "三级" if score >= 50 else "常态"
        out.append({
            "cell_id": p.get("cell_id"),
            "lon": c[0], "lat": c[1], "hazard": hazard,
            "hazard_name": HAZARDS[hazard]["name"],
            "score": round(score, 1), "level": level,
            "label": label or p.get("current_danger_label") or "",
            "dominant": p.get("current_hazard_name") or HAZARDS[hazard]["name"],
            "weather_point": p.get("current_weather_point") or p.get("weather_point"),
        })
    return out


def _nearest(lon: float, lat: float, rows: list[dict], key: str = "name") -> tuple[dict | None, float | None]:
    best, best_km = None, None
    for row in rows:
        km = _haversine_km(lon, lat, row["lon"], row["lat"])
        if best_km is None or km < best_km:
            best, best_km = row, km
    return best, best_km


def eco_overview(user: dict) -> dict:
    stats = case_store.case_stats(user)
    verification = case_store.verification_stats(user)
    cases = case_store.list_cases(user, limit=6)
    hotspots = sorted(_hotspots(), key=lambda x: float(x.get("priority_score") or 0), reverse=True)[:8]
    return {
        "stats": stats,
        "verification": verification,
        "recent_cases": [
            {
                "id": c["id"], "case_no": c["case_no"], "title": c["title"],
                "status": c["status"], "assignee_name": c.get("assignee_name"),
                "priority_score": c.get("priority_score"), "priority_label": c.get("priority_label"),
                "updated_at": c.get("updated_at"), "lon": c.get("lon"), "lat": c.get("lat"),
            }
            for c in cases
        ],
        "top_hotspots": hotspots,
        "disclaimer": DISCLAIMER,
    }


def emergency_overview(hazard: str | None = None) -> dict:
    cells = _grid()
    if hazard:
        if hazard not in HAZARDS:
            raise ValueError("灾种只能是：" + "、".join(HAZARDS))
        cells = [c for c in cells if c["hazard"] == hazard]
    cells.sort(key=lambda x: x["score"], reverse=True)
    counts = {level: 0 for level in LEVEL_ORDER}
    for c in cells:
        counts[c["level"]] = counts.get(c["level"], 0) + 1
    by_hazard = {}
    for key, meta in HAZARDS.items():
        subset = [c for c in cells if c["hazard"] == key]
        if not subset:
            continue
        by_hazard[key] = {
            "name": meta["name"], "color": meta["color"], "count": len(subset),
            "max_score": max(c["score"] for c in subset),
            "mean_score": round(sum(c["score"] for c in subset) / len(subset), 1),
            "top_cell": subset[0]["cell_id"],
        }
    alerts = []
    for c in cells[:16]:
        police, km = _nearest(c["lon"], c["lat"], _police())
        alerts.append({
            **c,
            "advice": ADVICE.get(c["hazard"], "按主管部门要求核查。"),
            "nearest_police": (police or {}).get("name"),
            "dist_police_km": round(km, 1) if km is not None else None,
            "color": HAZARDS[c["hazard"]]["color"],
        })
    weather = _load_json("weather.json") if isinstance(_load_json("weather.json"), dict) else {}
    return {
        "summary": {
            "cells": len(cells), "by_level": counts,
            "max_score": cells[0]["score"] if cells else 0,
            "hazard_filter": hazard,
        },
        "hazards": list(by_hazard.values()),
        "alerts": alerts,
        "weather": {
            "fetched_at": weather.get("fetched_at"),
            "source": weather.get("source"),
            "points": (weather.get("points") or [])[:5],
        },
        "disclaimer": DISCLAIMER,
    }


def event_security(
    name: str,
    lon: float,
    lat: float,
    radius_km: float = 3.0,
    people: int = 0,
    level: str = "三级",
) -> dict:
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("经纬度超出范围")
    if not 0.2 <= radius_km <= 10:
        raise ValueError("管控半径需在 0.2–10 km 之间")
    if not 0 <= people <= 1_000_000:
        raise ValueError("预计人数需在 0–1,000,000 之间")
    if level not in ("一级", "二级", "三级"):
        raise ValueError("安保等级只能是一级/二级/三级")
    outer = radius_km
    core = min(0.5, max(0.2, outer * 0.2))
    control = min(1.5, max(core + 0.3, outer * 0.5))
    if control >= outer:
        control = max(core + 0.2, outer * 0.7)
    rings = [
        {"name": "核心圈", "radius_km": round(core, 2), "color": "#ff5b52", "geojson": _circle(lon, lat, core)},
        {"name": "管控圈", "radius_km": round(control, 2), "color": "#ffa726", "geojson": _circle(lon, lat, control)},
        {"name": "外围圈", "radius_km": round(outer, 2), "color": "#38bdf8", "geojson": _circle(lon, lat, outer)},
    ]
    police = []
    for row in _police():
        km = _haversine_km(lon, lat, row["lon"], row["lat"])
        if km <= outer * 1.5:
            police.append({**row, "dist_km": round(km, 2)})
    police.sort(key=lambda x: x["dist_km"])
    hot = []
    for row in _hotspots():
        km = _haversine_km(lon, lat, row["lon"], row["lat"])
        if km <= outer:
            hot.append({**row, "dist_km": round(km, 2)})
    hot.sort(key=lambda x: x["dist_km"])
    risk = []
    for row in _grid():
        km = _haversine_km(lon, lat, row["lon"], row["lat"])
        if km <= outer and row["score"] >= 50:
            risk.append({**row, "dist_km": round(km, 2)})
    risk.sort(key=lambda x: x["score"], reverse=True)
    base_posts = max(2, math.ceil(people / 500)) if people else 4
    traffic_posts = max(2, math.ceil(people / 2000)) if people else 2
    return {
        "event": {"name": name.strip()[:80] or "未命名活动", "lon": lon, "lat": lat,
                  "radius_km": radius_km, "people": people, "level": level},
        "rings": rings,
        "police": police[:8],
        "hotspots": hot[:8],
        "risk_cells": risk[:12],
        "summary": {
            "police_within_outer": len(police),
            "hotspots_within": len(hot),
            "risk_cells_within": len(risk),
            "suggested_core_posts": base_posts,
            "suggested_traffic_posts": traffic_posts,
            "suggested_total_posts": base_posts + traffic_posts,
        },
        "note": "岗位数只是按人数和圈层做的粗略下限，不是正式警力测算；必须结合场地、出入口、交通和情报复核。",
        "disclaimer": DISCLAIMER,
    }


def _osrm_route(a: tuple[float, float], b: tuple[float, float]) -> tuple[list[list[float]], str]:
    url = f"https://router.project-osrm.org/route/v1/driving/{a[0]},{a[1]};{b[0]},{b[1]}"
    try:
        r = requests.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=20)
        r.raise_for_status()
        data = r.json()
        route = data["routes"][0]
        coords = route["geometry"]["coordinates"]
        if len(coords) < 2:
            raise ValueError("OSRM 未返回有效路径")
        return [[float(x), float(y)] for x, y in coords], "osrm"
    except Exception as exc:  # noqa: BLE001
        return [[a[0], a[1]], [b[0], b[1]]], f"直线降级：{exc}"


def _cumulative(coords: list[list[float]]) -> list[float]:
    out = [0.0]
    for i in range(1, len(coords)):
        out.append(out[-1] + _haversine_km(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]))
    return out


def _sample_line(coords: list[list[float]], sample_km: float) -> list[list[float]]:
    if not coords:
        return []
    cum = _cumulative(coords)
    total = cum[-1]
    targets = [0.0]
    step = max(0.2, sample_km)
    x = step
    while x < total:
        targets.append(x)
        x += step
    if targets[-1] != total:
        targets.append(total)
    out = []
    j = 0
    for t in targets:
        while j < len(cum) - 1 and cum[j + 1] < t:
            j += 1
        if j >= len(coords) - 1:
            out.append(coords[-1])
            continue
        span = cum[j + 1] - cum[j]
        ratio = 0 if span <= 0 else (t - cum[j]) / span
        lon = coords[j][0] + (coords[j + 1][0] - coords[j][0]) * ratio
        lat = coords[j][1] + (coords[j + 1][1] - coords[j][1]) * ratio
        out.append([lon, lat])
    return out


def _kml(name: str, route: list[list[float]], samples: list[dict]) -> str:
    coords = " ".join(f"{p[0]:.6f},{p[1]:.6f},0" for p in route)
    marks = []
    for s in samples:
        marks.append(
            "<Placemark><name>%s</name><Point><coordinates>%.6f,%.6f,%.1f</coordinates></Point></Placemark>"
            % (escape(f"采样 {s['seq']}"), s["lon"], s["lat"], s["terrain_m"] or 0)
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2">'
        f"<Document><name>{escape(name)}</name>"
        f"<Placemark><name>巡护路线</name><LineString><tessellate>1</tessellate>"
        f"<coordinates>{coords}</coordinates></LineString></Placemark>"
        + "".join(marks)
        + "</Document></kml>"
    )


def linear_patrol(
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    sample_km: float = 2.0,
    buffer_km: float = 1.0,
    title: str = "线性巡护",
) -> dict:
    if not (-180 <= start_lon <= 180 and -90 <= start_lat <= 90 and -180 <= end_lon <= 180 and -90 <= end_lat <= 90):
        raise ValueError("经纬度超出范围")
    if not 0.2 <= sample_km <= 20:
        raise ValueError("采样间距需在 0.2–20 km 之间")
    if not 0.1 <= buffer_km <= 10:
        raise ValueError("走廊缓冲需在 0.1–10 km 之间")
    route, mode = _osrm_route((start_lon, start_lat), (end_lon, end_lat))
    samples_xy = _sample_line(route, sample_km)
    if len(samples_xy) > 120:
        samples_xy = samples_xy[:120]
    coords = [(p[0], p[1]) for p in samples_xy]
    try:
        from app.risk import cached_elevations
        elevations = cached_elevations(coords)
    except Exception:
        elevations = [None] * len(coords)
    police = _police()
    hot = _hotspots()
    cells = _grid()
    samples = []
    near_hot: dict[str, dict] = {}
    near_cells: dict[str, dict] = {}
    distances = []
    for i, p in enumerate(samples_xy):
        pr, km = _nearest(p[0], p[1], police)
        if km is not None:
            distances.append(km)
        samples.append({
            "seq": i + 1, "lon": round(p[0], 6), "lat": round(p[1], 6),
            "terrain_m": elevations[i] if i < len(elevations) else None,
            "nearest_police": (pr or {}).get("name"), "dist_police_km": round(km, 1) if km is not None else None,
        })
    for h in hot:
        best = min((_haversine_km(p[0], p[1], h["lon"], h["lat"]) for p in samples_xy), default=None)
        if best is not None and best <= buffer_km:
            near_hot[h["id"]] = {**h, "dist_km": round(best, 2)}
    for c in cells:
        best = min((_haversine_km(p[0], p[1], c["lon"], c["lat"]) for p in samples_xy), default=None)
        if best is not None and best <= buffer_km and c["score"] >= 50:
            near_cells[c["cell_id"]] = {**c, "dist_km": round(best, 2)}
    total_km = _cumulative(route)[-1] if route else 0
    terrain_vals = [s["terrain_m"] for s in samples if s["terrain_m"] is not None]
    result = {
        "title": title.strip()[:80] or "线性巡护",
        "mode": "osrm" if mode == "osrm" else "straight",
        "mode_note": mode,
        "samples": samples,
        "stats": {
            "route_km": round(total_km, 2),
            "samples": len(samples),
            "terrain_min": min(terrain_vals) if terrain_vals else None,
            "terrain_max": max(terrain_vals) if terrain_vals else None,
            "police_min_km": round(min(distances), 1) if distances else None,
            "police_mean_km": round(sum(distances) / len(distances), 1) if distances else None,
            "police_within_10km": sum(1 for d in distances if d <= 10),
            "hotspots_near": len(near_hot),
            "risk_cells_near": len(near_cells),
        },
        "hotspots": sorted(near_hot.values(), key=lambda x: x["dist_km"])[:12],
        "risk_cells": sorted(near_cells.values(), key=lambda x: x["score"], reverse=True)[:12],
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"kind": "route", "name": title}, "geometry": {"type": "LineString", "coordinates": route}},
                *[
                    {"type": "Feature", "properties": {"kind": "sample", **s}, "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]}}
                    for s in samples
                ],
            ],
        },
        "disclaimer": DISCLAIMER,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    result["kml"] = _kml(result["title"], route, samples)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["seq", "lon", "lat", "terrain_m", "nearest_police", "dist_police_km"])
    for s in samples:
        writer.writerow([s["seq"], s["lon"], s["lat"], s["terrain_m"], s["nearest_police"], s["dist_police_km"]])
    result["csv"] = "\ufeff" + buf.getvalue()
    return result


def referral_options() -> dict:
    return {
        "departments": case_store.REFERRAL_DEPARTMENTS,
        "statuses": case_store.REFERRAL_STATUSES,
        "disclaimer": "移送只建立协同台账，不替代法定行刑衔接程序；反馈内容由人工填写。",
    }

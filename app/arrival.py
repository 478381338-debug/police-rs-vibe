"""车行到达圈：OSRM 路网采样 + 诚实降级。

设计取舍：
- 公共 OSRM 没有 isochrone 接口，这里用 table 服务对放射状采样点求时间，
  再按方位插值出近似轮廓；它是采样近似，不是严格等时线。
- OSRM 会把偏远坐标吸附到最近道路。吸附过远时结果会“看起来很近、实际
  起点在别处”，因此必须校验中心点和采样点的吸附距离，不可靠就降级为
  直线估算，并在响应里说明原因。
- 结果缓存 7 天；降级结果只缓存 10 分钟，避免临时网络故障被长期固化。
"""

from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from . import config

OSRM_BASE = os.getenv("POLICE_OSRM_BASE", "https://router.project-osrm.org").rstrip("/")
CACHE_DIR = config.DATA / "cache" / "routes"
CACHE_VERSION = "v1"
CACHE_TTL = 7 * 86400
DEGRADED_TTL = 600

BEARINGS = 36
RADII_FRACTIONS = (0.08, 0.16, 0.24, 0.34, 0.44, 0.55, 0.66, 0.78, 0.90, 1.0)
MAX_TABLE_DESTINATIONS = 99

CENTER_SNAP_LIMIT_M = float(os.getenv("POLICE_OSRM_CENTER_SNAP_M", "2000"))
DEST_SNAP_LIMIT_M = float(os.getenv("POLICE_OSRM_DEST_SNAP_M", "1500"))
MIN_VALID_BEARINGS = 12

DISCLAIMER = (
    "到达圈为 OSRM 公共实例的路网采样近似，不含实时路况、封路、限行、天气、"
    "检查站等待和车辆性能差异；不能作为出警时限承诺。"
)
STRAIGHT_DISCLAIMER = (
    "未获得可靠路网结果，已降级为直线估算（按 45 km/h、道路绕行系数 1.35 折算），"
    "只用于判断量级，不能用于承诺到达时间。"
)


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _destination(lon: float, lat: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    radius = 6371.0088
    bearing = math.radians(bearing_deg)
    angular = distance_km / radius
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(
        math.sin(p1) * math.cos(angular)
        + math.cos(p1) * math.sin(angular) * math.cos(bearing)
    )
    l2 = l1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(p1),
        math.cos(angular) - math.sin(p1) * math.sin(p2),
    )
    return math.degrees(l2), math.degrees(p2)


def _cache_path(lon: float, lat: float, minutes: int) -> Path:
    return CACHE_DIR / f"{lon:.4f}_{lat:.4f}_{minutes}_{CACHE_VERSION}.json"


def _cache_read(path: Path) -> tuple[dict | None, float]:
    try:
        age = time.time() - path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8"))
        ttl = DEGRADED_TTL if data.get("degraded") else CACHE_TTL
        if age <= ttl:
            return data, age
    except (OSError, ValueError, TypeError):
        pass
    return None, 0.0


def _cache_write(path: Path, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _table_chunk(center: tuple[float, float], points: list[dict]) -> tuple[list[dict], float | None]:
    coords = [f"{center[0]},{center[1]}"]
    coords.extend(f"{point['lon']},{point['lat']}" for point in points)
    url = (
        f"{OSRM_BASE}/table/v1/driving/{';'.join(coords)}"
        "?sources=0&annotations=duration,distance"
    )
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM 返回 {data.get('code')}")
    durations = (data.get("durations") or [[]])[0]
    distances = (data.get("distances") or [[]])[0]
    destinations = data.get("destinations") or []
    center_snap = destinations[0].get("distance") if destinations else None
    rows = []
    for index, point in enumerate(points):
        cursor = index + 1
        destination = destinations[cursor] if cursor < len(destinations) else {}
        rows.append({
            **point,
            "duration_s": durations[cursor] if cursor < len(durations) else None,
            "route_m": distances[cursor] if cursor < len(distances) else None,
            "snap_m": destination.get("distance"),
        })
    return rows, center_snap


def _query_osrm(lon: float, lat: float, max_km: float) -> tuple[list[dict], float | None, str | None]:
    points = []
    for bearing_index in range(BEARINGS):
        bearing = bearing_index * 360 / BEARINGS
        for fraction in RADII_FRACTIONS:
            distance = max(0.2, max_km * fraction)
            plon, plat = _destination(lon, lat, bearing, distance)
            points.append({
                "bearing": bearing,
                "radius_km": distance,
                "lon": plon,
                "lat": plat,
            })

    chunks = [points[i:i + MAX_TABLE_DESTINATIONS] for i in range(0, len(points), MAX_TABLE_DESTINATIONS)]
    results: list[list[dict] | None] = [None] * len(chunks)
    center_snaps: list[float] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as pool:
        futures = {
            pool.submit(_table_chunk, (lon, lat), chunk): index
            for index, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                rows, center_snap = future.result()
                results[index] = rows
                if center_snap is not None:
                    center_snaps.append(float(center_snap))
            except Exception as exc:  # 网络/限流/格式错误都走降级
                errors.append(str(exc)[:160])

    if any(row is None for row in results):
        return [], (min(center_snaps) if center_snaps else None), "；".join(errors[:2]) or "OSRM 采样不完整"

    samples = [row for rows in results if rows for row in rows]
    center_snap = min(center_snaps) if center_snaps else None
    if center_snap is None:
        return samples, None, "OSRM 未返回中心点吸附距离"
    return samples, center_snap, ("；".join(errors[:2]) if errors else None)


def _profile_from_samples(samples: list[dict], minutes: int) -> list[dict]:
    target = minutes * 60
    profile = []
    for bearing_index in range(BEARINGS):
        bearing = bearing_index * 360 / BEARINGS
        rows = sorted(
            (row for row in samples if row.get("bearing") == bearing),
            key=lambda row: row["radius_km"],
        )
        previous_ok = None
        next_over = None
        for row in rows:
            straight_km = row.get("straight_km")
            if straight_km is None:
                continue
            snap_m = row.get("snap_m")
            route_m = row.get("route_m")
            duration_s = row.get("duration_s")
            valid = (
                duration_s is not None
                and route_m is not None
                and snap_m is not None
                and snap_m <= max(DEST_SNAP_LIMIT_M, straight_km * 1000 * 0.3)
                and route_m >= straight_km * 1000 * 0.6
            )
            row["valid"] = valid
            row["duration_min"] = round(duration_s / 60, 1) if duration_s is not None else None
            if not valid:
                continue
            if duration_s <= target:
                previous_ok = row
            else:
                next_over = row
                break
        if previous_ok is None:
            profile.append({
                "bearing": bearing,
                "km": 0.0,
                "duration_min": None,
                "reachable": False,
            })
            continue
        km = previous_ok["radius_km"]
        duration_min = previous_ok["duration_min"]
        if next_over is not None and next_over.get("duration_min") is not None:
            span = next_over["duration_min"] - duration_min
            if span > 0:
                ratio = (minutes - duration_min) / span
                km = previous_ok["radius_km"] + (next_over["radius_km"] - previous_ok["radius_km"]) * max(0.0, min(1.0, ratio))
                duration_min = minutes
        profile.append({
            "bearing": bearing,
            "km": round(km, 3),
            "duration_min": duration_min,
            "reachable": True,
        })
    return profile


def _polygon_from_profile(profile: list[dict], lon: float, lat: float, floor_km: float) -> list[list[float]]:
    coordinates = []
    for point in profile:
        distance = point["km"] if point["km"] > 0 else floor_km
        plon, plat = _destination(lon, lat, point["bearing"], distance)
        coordinates.append([round(plon, 6), round(plat, 6)])
    coordinates.append(coordinates[0])
    return coordinates


def _straight_answer(lon: float, lat: float, minutes: int, reason: str, center_snap_m: float | None = None) -> dict:
    radius_km = max(0.5, minutes / 60 * 45 / 1.35)
    bearings = 72
    profile = [
        {"bearing": i * 360 / bearings, "km": round(radius_km, 3), "duration_min": minutes, "reachable": True, "estimated": True}
        for i in range(bearings)
    ]
    coordinates = _polygon_from_profile(profile, lon, lat, radius_km)
    return {
        "center": {"lon": lon, "lat": lat},
        "minutes": minutes,
        "mode": "straight",
        "degraded": True,
        "reason": reason,
        "center_snap_m": round(center_snap_m, 1) if center_snap_m is not None else None,
        "radius_km": {"min": round(radius_km, 3), "mean": round(radius_km, 3), "max": round(radius_km, 3)},
        "bearings": bearings,
        "valid_bearings": 0,
        "samples": 0,
        "polygon": {"type": "Polygon", "coordinates": [coordinates]},
        "geojson": {
            "type": "Feature",
            "properties": {
                "kind": "arrival_circle",
                "minutes": minutes,
                "mode": "straight",
                "radius_km": round(radius_km, 3),
                "disclaimer": STRAIGHT_DISCLAIMER,
            },
            "geometry": {"type": "Polygon", "coordinates": [coordinates]},
        },
        "source": "直线降级（未使用路网）",
        "disclaimer": STRAIGHT_DISCLAIMER,
        "profile": profile,
    }


def arrival_circle(lon: float, lat: float, minutes: int = 30, force: bool = False) -> dict:
    minutes = max(1, min(180, int(minutes)))
    path = _cache_path(lon, lat, minutes)
    if not force:
        cached, age = _cache_read(path)
        if cached:
            cached["cache"] = {"hit": True, "age_s": round(age, 1)}
            return cached

    max_km = max(2.0, minutes / 60 * 45)
    started = time.time()
    samples, center_snap, query_error = _query_osrm(lon, lat, max_km)
    if samples and center_snap is not None:
        for row in samples:
            row["straight_km"] = round(_haversine_km(lon, lat, row["lon"], row["lat"]), 3)

    reason = query_error
    if center_snap is not None and center_snap > CENTER_SNAP_LIMIT_M:
        reason = f"案件点距最近可路由道路约 {center_snap / 1000:.1f} km，路网结果会偏离现场"
    elif samples and center_snap is None:
        reason = "OSRM 未返回中心点吸附距离"
    elif not samples:
        reason = reason or "OSRM 无有效返回"

    if not samples or reason:
        answer = _straight_answer(lon, lat, minutes, reason or "OSRM 不可用", center_snap)
        answer["cache"] = {"hit": False, "age_s": 0.0}
        answer["elapsed_s"] = round(time.time() - started, 2)
        _cache_write(path, {**answer, "cache": None})
        return answer

    profile = _profile_from_samples(samples, minutes)
    valid_bearings = sum(1 for point in profile if point["reachable"])
    if valid_bearings < MIN_VALID_BEARINGS:
        answer = _straight_answer(
            lon, lat, minutes,
            f"路网有效采样仅 {valid_bearings}/{BEARINGS} 个方位，不足 {MIN_VALID_BEARINGS} 个，无法可靠成圈",
            center_snap,
        )
        answer["cache"] = {"hit": False, "age_s": 0.0}
        answer["elapsed_s"] = round(time.time() - started, 2)
        _cache_write(path, {**answer, "cache": None})
        return answer

    reachable = [point["km"] for point in profile if point["reachable"]]
    floor_km = max(0.2, max_km * 0.02)
    coordinates = _polygon_from_profile(profile, lon, lat, floor_km)
    version = {
        "center": {"lon": lon, "lat": lat},
        "minutes": minutes,
        "mode": "osrm",
        "degraded": False,
        "reason": None,
        "center_snap_m": round(center_snap, 1),
        "radius_km": {
            "min": round(min(reachable), 3),
            "mean": round(sum(reachable) / len(reachable), 3),
            "max": round(max(reachable), 3),
        },
        "bearings": BEARINGS,
        "valid_bearings": valid_bearings,
        "samples": len(samples),
        "polygon": {"type": "Polygon", "coordinates": [coordinates]},
        "geojson": {
            "type": "Feature",
            "properties": {
                "kind": "arrival_circle",
                "minutes": minutes,
                "mode": "osrm",
                "center_snap_m": round(center_snap, 1),
                "min_km": round(min(reachable), 3),
                "mean_km": round(sum(reachable) / len(reachable), 3),
                "max_km": round(max(reachable), 3),
                "disclaimer": DISCLAIMER,
            },
            "geometry": {"type": "Polygon", "coordinates": [coordinates]},
        },
        "source": "OSRM 公共实例（router.project-osrm.org）",
        "disclaimer": DISCLAIMER,
        "profile": profile,
        "cache": {"hit": False, "age_s": 0.0},
        "elapsed_s": round(time.time() - started, 2),
    }
    _cache_write(path, {**version, "cache": None})
    return version

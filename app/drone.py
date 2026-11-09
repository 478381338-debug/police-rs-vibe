"""无人机核查航线规划：DEM 跟随 + KML/CSV。

这不是适航工具：
- 不做禁飞区、空域审批、临时管制、磁偏角、风场、电池衰减校验；
- 高程来自公开 DEM/Open-Meteo 缓存，可能有几十米误差；
- 输出只用于桌面推演和现场人工复核。
"""
from __future__ import annotations

import csv
import io
import math
import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from app.risk import cached_elevations

MAX_WAYPOINTS = int(os.getenv("POLICE_DRONE_MAX_WAYPOINTS", "800"))
MAX_SIZE_KM = float(os.getenv("POLICE_DRONE_MAX_SIZE_KM", "5"))
EARTH_M_PER_DEG = 111320.0

DISCLAIMER = (
    "航线为桌面推演结果，未接入禁飞区、空域审批、临时管制、风场、电池衰减和避障模型；"
    "起飞前必须由持证飞手核对空域、天气、障碍物和返航电量，并以现场判断为准。"
)


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    return x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)


def _to_lonlat(x: float, y: float, lon0: float, lat0: float) -> tuple[float, float]:
    lon = lon0 + x / (EARTH_M_PER_DEG * max(0.2, math.cos(math.radians(lat0))))
    lat = lat0 + y / EARTH_M_PER_DEG
    return lon, lat


def _densify(start: tuple[float, float], end: tuple[float, float], sample_m: float) -> list[tuple[float, float]]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    steps = max(1, math.ceil(length / max(5.0, sample_m)))
    return [(start[0] + dx * i / steps, start[1] + dy * i / steps) for i in range(steps + 1)]


def _lawnmower(size_m: float, spacing_m: float, direction_deg: float) -> list[list[tuple[float, float]]]:
    rows = max(1, math.ceil(size_m / max(10.0, spacing_m)))
    half = size_m / 2
    legs: list[list[tuple[float, float]]] = []
    cursor: tuple[float, float] | None = None
    for index in range(rows + 1):
        y = -half + index * (size_m / rows)
        start, end = ((-half, y), (half, y)) if index % 2 == 0 else ((half, y), (-half, y))
        start = _rotate(*start, direction_deg)
        end = _rotate(*end, direction_deg)
        if cursor is not None:
            legs.append([cursor, start])
        legs.append([start, end])
        cursor = end
    return legs


def _perimeter(size_m: float, direction_deg: float) -> list[list[tuple[float, float]]]:
    half = size_m / 2
    corners = [(-half, -half), (half, -half), (half, half), (-half, half), (-half, -half)]
    rotated = [_rotate(x, y, direction_deg) for x, y in corners]
    return [[rotated[i], rotated[i + 1]] for i in range(4)]


def _csv_text(waypoints: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["seq", "lon", "lat", "terrain_m", "alt_m", "leg", "role"])
    for point in waypoints:
        writer.writerow([
            point["seq"],
            f"{point['lon']:.6f}",
            f"{point['lat']:.6f}",
            "" if point["terrain_m"] is None else f"{point['terrain_m']:.1f}",
            f"{point['alt_m']:.1f}",
            point["leg"],
            point["role"],
        ])
    return "\ufeff" + buffer.getvalue()


def _kml_text(waypoints: list[dict], title: str) -> str:
    coordinates = " ".join(
        f"{point['lon']:.6f},{point['lat']:.6f},{point['alt_m']:.1f}"
        for point in waypoints
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
        "<Document>\n"
        f"<name>{escape(title)}</name>\n"
        f"<description>{escape(DISCLAIMER)}</description>\n"
        "<Placemark>\n"
        "<name>核查航线</name>\n"
        "<LineString>\n"
        "<tessellate>1</tessellate>\n"
        "<altitudeMode>absolute</altitudeMode>\n"
        f"<coordinates>{coordinates}</coordinates>\n"
        "</LineString>\n"
        "</Placemark>\n"
        "</Document>\n"
        "</kml>\n"
    )


def plan_drone_route(
    lon: float,
    lat: float,
    pattern: str = "lawnmower",
    size_km: float = 0.6,
    spacing_m: float = 80.0,
    rel_height_m: float = 80.0,
    speed_mps: float = 8.0,
    direction_deg: float = 0.0,
    sample_m: float = 50.0,
    title: str = "无人机核查航线",
) -> dict:
    if pattern not in {"lawnmower", "perimeter"}:
        raise ValueError("pattern 只能是 lawnmower 或 perimeter")
    if not (0.05 <= size_km <= MAX_SIZE_KM):
        raise ValueError(f"范围需在 0.05–{MAX_SIZE_KM:g} km 之间")
    if not (10 <= spacing_m <= 1000):
        raise ValueError("航线间距需在 10–1000 m 之间")
    if not (20 <= rel_height_m <= 500):
        raise ValueError("相对地面高度需在 20–500 m 之间")
    if not (2 <= speed_mps <= 25):
        raise ValueError("速度需在 2–25 m/s 之间")
    if not (0 <= direction_deg < 360):
        raise ValueError("方向需在 0–359° 之间")

    size_m = size_km * 1000
    local_legs = (
        _perimeter(size_m, direction_deg)
        if pattern == "perimeter"
        else _lawnmower(size_m, spacing_m, direction_deg)
    )

    waypoints: list[dict] = []
    cursor: tuple[float, float] | None = None
    for leg_index, (start, end) in enumerate(local_legs, start=1):
        for index, (x, y) in enumerate(_densify(start, end, sample_m)):
            if cursor is not None and abs(x - cursor[0]) < 0.01 and abs(y - cursor[1]) < 0.01:
                continue
            cursor = (x, y)
            plon, plat = _to_lonlat(x, y, lon, lat)
            waypoints.append({
                "seq": len(waypoints) + 1,
                "lon": round(plon, 6),
                "lat": round(plat, 6),
                "terrain_m": None,
                "alt_m": rel_height_m,
                "leg": leg_index,
                "role": "航点",
            })
    if len(waypoints) > MAX_WAYPOINTS:
        raise ValueError(f"航点 {len(waypoints)} 个，超过上限 {MAX_WAYPOINTS}；请增大间距或减小范围")
    if not waypoints:
        raise ValueError("航线为空，请调整参数")

    elevations = cached_elevations([(p["lon"], p["lat"]) for p in waypoints])
    missing = 0
    for point, elevation in zip(waypoints, elevations):
        if elevation is None:
            missing += 1
            point["terrain_m"] = None
            point["alt_m"] = rel_height_m
        else:
            point["terrain_m"] = round(float(elevation), 1)
            point["alt_m"] = round(float(elevation) + rel_height_m, 1)

    distance_km = 0.0
    for previous, current in zip(waypoints, waypoints[1:]):
        distance_km += _haversine_km(previous["lon"], previous["lat"], current["lon"], current["lat"])
    turns = max(0, len(local_legs) - 1)
    flight_min = distance_km * 1000 / speed_mps / 60 + turns * 4 / 60

    terrains = [p["terrain_m"] for p in waypoints if p["terrain_m"] is not None]
    altitudes = [p["alt_m"] for p in waypoints]
    degraded = missing > 0
    reason = None
    if degraded:
        reason = f"{missing}/{len(waypoints)} 个航点缺少 DEM 高程，这些点按相对高度估算，不能作为真实离地高度"

    properties = {
        "kind": "drone_route",
        "pattern": pattern,
        "size_km": size_km,
        "spacing_m": spacing_m,
        "rel_height_m": rel_height_m,
        "speed_mps": speed_mps,
        "direction_deg": direction_deg,
        "waypoints": len(waypoints),
        "distance_km": round(distance_km, 3),
        "flight_min": round(flight_min, 1),
        "degraded": degraded,
        "disclaimer": DISCLAIMER,
    }
    return {
        "center": {"lon": lon, "lat": lat},
        "pattern": pattern,
        "size_km": size_km,
        "spacing_m": spacing_m,
        "rel_height_m": rel_height_m,
        "speed_mps": speed_mps,
        "direction_deg": direction_deg,
        "waypoints": waypoints,
        "stats": {
            "distance_km": round(distance_km, 3),
            "flight_min": round(flight_min, 1),
            "waypoints": len(waypoints),
            "legs": len(local_legs),
            "turns": turns,
            "terrain_min_m": round(min(terrains), 1) if terrains else None,
            "terrain_max_m": round(max(terrains), 1) if terrains else None,
            "alt_min_m": round(min(altitudes), 1),
            "alt_max_m": round(max(altitudes), 1),
            "missing_elevation": missing,
        },
        "degraded": degraded,
        "reason": reason,
        "geojson": {
            "type": "Feature",
            "properties": properties,
            "geometry": {
                "type": "LineString",
                "coordinates": [[p["lon"], p["lat"], p["alt_m"]] for p in waypoints],
            },
        },
        "kml": _kml_text(waypoints, title),
        "csv": _csv_text(waypoints),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
    }
"""高发点聚类 + 危险等级风险模型 + 扩展灾害风险格网。

风险模型：
  风险分 = 100 × Σ(AHP权重_i × 归一化因子_i)
  因子：类别严重度 / 异常数量 / 累计面积 / 复发性 / 地形 / 可达性 / 当前环境 / 数据置信度

AHP 权重由 MATLAB 计算（data/model_weights.json）；MATLAB 不可用时用默认权重。
每次刷新会用最新气象、最新影像新鲜度重算，因此危险等级会随数据变化。
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from shapely.geometry import MultiPoint, Polygon, mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config
from app.config import (
    AOI_BBOX,
    DANGER_LEVELS,
    DEFAULT_WEIGHTS,
    HAZARD_BY_ID,
    HAZARDS,
    level_for_score,
)

EARTH_R = 6371.0088


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def _points_from_geojson(path: Path) -> list[dict]:
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for f in d.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") == "Point":
            lon, lat = g["coordinates"][:2]
            out.append({"lon": lon, "lat": lat, **(f.get("properties") or {})})
    return out


def _lines_from_geojson(path: Path) -> list[list[tuple[float, float]]]:
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    for f in d.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") == "LineString":
            lines.append([tuple(c[:2]) for c in g["coordinates"]])
    return lines


def _dist_to_segment_km(p, a, b, lat0: float) -> float:
    kx = 111.32 * math.cos(math.radians(lat0))
    ky = 110.57
    px, py = p[0] * kx, p[1] * ky
    ax, ay = a[0] * kx, a[1] * ky
    bx, by = b[0] * kx, b[1] * ky
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def dist_to_lines_km(lon: float, lat: float, lines: list[list[tuple[float, float]]]) -> float:
    best = 9999.0
    for line in lines:
        for a, b in zip(line, line[1:]):
            best = min(best, _dist_to_segment_km((lon, lat), a, b, lat))
    return best


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------
class UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def load_incidents() -> list[dict]:
    path = config.DATA / "incidents.geojson"
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for f in d.get("features", []):
        p = f.get("properties") or {}
        c = p.get("centroid") or []
        if len(c) < 2:
            continue
        out.append({
            "id": p.get("id"),
            "class": p.get("class"),
            "hazard_id": p.get("hazard_id"),
            "area_ha": float(p.get("area_ha") or 0),
            "severity": float(p.get("severity") or 0.5),
            "period": p.get("period"),
            "period_label": p.get("period_label"),
            "lon": float(c[0]),
            "lat": float(c[1]),
        })
    return out


def cluster_incidents(incidents: list[dict], radius_km: float = 13.0) -> list[dict]:
    n = len(incidents)
    uf = UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if haversine(incidents[i]["lon"], incidents[i]["lat"],
                         incidents[j]["lon"], incidents[j]["lat"]) <= radius_km:
                uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    clusters = []
    for idxs in groups.values():
        members = [incidents[i] for i in idxs]
        lon = sum(m["lon"] for m in members) / len(members)
        lat = sum(m["lat"] for m in members) / len(members)
        classes: dict[str, int] = {}
        periods = set()
        for m in members:
            classes[m["class"]] = classes.get(m["class"], 0) + 1
            periods.add(m["period"])
        dominant_class = max(
            classes.keys(),
            key=lambda c: (classes[c], next((h["severity"] for h in HAZARDS if h["name"] == c), 0.5)),
        )
        hull = MultiPoint([(m["lon"], m["lat"]) for m in members]).convex_hull
        geom = hull.buffer(0.03 if len(members) == 1 else 0.018)
        clusters.append({
            "members": members,
            "lon": lon,
            "lat": lat,
            "count": len(members),
            "class_counts": classes,
            "dominant_class": dominant_class,
            "periods": sorted(p for p in periods if p),
            "area_ha": sum(m["area_ha"] for m in members),
            "severity": max(m["severity"] for m in members),
            "recurrence": min(len(classes) + len(periods) - 1, 4),
            "geom": geom,
        })
    return clusters


# ---------------------------------------------------------------------------
# 环境/可达性/地形
# ---------------------------------------------------------------------------
def load_weather() -> dict:
    path = config.DATA / "weather.json"
    if not path.exists():
        return {"points": [], "fetched_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"points": [], "fetched_at": None}


def weather_indices(current: dict) -> dict:
    temp = float(current.get("temperature_2m") or 5)
    hum = float(current.get("relative_humidity_2m") or 35)
    wind = float(current.get("wind_speed_10m") or 10)
    precip = float(current.get("precipitation") or 0)
    soil = float(current.get("soil_moisture_0_to_10cm") or 0.15)
    temp_n = clamp((temp + 10) / 40)
    hum_n = clamp(hum / 100)
    wind_n = clamp(wind / 40)
    precip_n = clamp(precip / 10)
    soil_n = clamp(soil / 0.4)
    fire_wx = clamp(0.40 * temp_n + 0.30 * (1 - hum_n) + 0.20 * wind_n + 0.10 * (1 - precip_n))
    wet = clamp(0.45 * precip_n + 0.30 * soil_n + 0.25 * hum_n)
    month = datetime.now().month
    return {
        "temp": temp,
        "humidity": hum,
        "wind": wind,
        "precip": precip,
        "soil": soil,
        "temp_n": temp_n,
        "hum_n": hum_n,
        "wind_n": wind_n,
        "precip_n": precip_n,
        "soil_n": soil_n,
        "fire_wx": fire_wx,
        "wet": wet,
        "dry": 1 - wet,
        "heat": temp_n,
        "winter": 1.0 if month in (11, 12, 1, 2, 3) else 0.0,
    }


def nearest_weather(lon: float, lat: float, weather: dict) -> dict:
    pts = weather.get("points") or []
    if not pts:
        return weather_indices({})
    best = min(pts, key=lambda p: haversine(lon, lat, p["lon"], p["lat"]) if p.get("lon") is not None else 9e9)
    return weather_indices(best.get("current") or {})


def environment_for_class(dominant_class: str, idx: dict, slope_n: float) -> float:
    if dominant_class == "火烧迹地":
        return clamp(0.7 * idx["fire_wx"] + 0.3 * idx["dry"])
    if dominant_class == "盗采痕迹":
        return clamp(0.6 * idx["dry"] + 0.2 * idx["fire_wx"] + 0.2 * slope_n)
    if dominant_class in ("滑坡", "泥石流"):
        return clamp(0.7 * idx["wet"] + 0.3 * slope_n)
    if dominant_class == "雪线变化":
        return clamp(idx["winter"])
    return clamp(0.5 * idx["fire_wx"] + 0.5 * idx["wet"])


def load_latest_s2() -> dict | None:
    path = config.DATA / "mpc_scenes.json"
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        s2 = d.get("sentinel2") or []
        return s2[0] if s2 else None
    except Exception:
        return None


def confidence_from_scene(scene: dict | None) -> tuple[float, int | None, float | None]:
    if not scene or not scene.get("datetime"):
        return 0.55, None, None
    try:
        dt = datetime.fromisoformat(scene["datetime"].replace("Z", "+00:00"))
        age = max(0, (datetime.now(timezone.utc) - dt).days)
        cloud = scene.get("cloud_cover")
        cloud = float(cloud) if cloud is not None else 30.0
        freshness = clamp(1 - age / 30)
        conf = clamp(0.6 * freshness + 0.4 * (1 - cloud / 100))
        return conf, age, cloud
    except Exception:
        return 0.55, None, None


ELEV_CACHE = config.DATA / "elevation_cache.json"


def _load_elev_cache() -> dict:
    try:
        return json.loads(ELEV_CACHE.read_text(encoding="utf-8")) if ELEV_CACHE.exists() else {}
    except Exception:
        return {}


def _save_elev_cache(cache: dict):
    try:
        ELEV_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def cached_elevations(coords: list[tuple[float, float]]) -> list[float | None]:
    """高程缓存：同一坐标只向 Open-Meteo 请求一次，刷新时不再重复打接口。"""
    cache = _load_elev_cache()
    keys = [f"{c[0]:.4f},{c[1]:.4f}" for c in coords]
    missing = [(i, c) for i, (k, c) in enumerate(zip(keys, coords)) if k not in cache]
    if missing:
        from crawler.mpc_crawler import fetch_elevations
        vals = fetch_elevations([c for _, c in missing])
        for (i, c), v in zip(missing, vals):
            if v is not None:
                cache[keys[i]] = v
        _save_elev_cache(cache)
    return [cache.get(k) for k in keys]


def load_weights() -> tuple[dict, dict]:
    path = config.DATA / "model_weights.json"
    meta = {}
    if path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            w = d.get("weights") or {}
            meta = {k: v for k, v in d.items() if k != "weights"}
            if w:
                s = sum(float(v) for v in w.values())
                if s > 0:
                    return {k: float(v) / s for k, v in w.items()}, meta
        except Exception:
            pass
    return dict(DEFAULT_WEIGHTS), {"engine": "Python fallback", "method": "default"}


# ---------------------------------------------------------------------------
# 扩展灾害格网权重（规则筛查，不是已训练模型）
# ---------------------------------------------------------------------------
GRID_WEIGHTS = {
    "fire_scar": {"hist_fire": 0.35, "fire_wx": 0.25, "dry": 0.15, "slope": 0.10, "exposure": 0.10, "wind": 0.05},
    "mining": {"hist_mining": 0.30, "access": 0.20, "exposure": 0.15, "flat": 0.15, "dry": 0.10, "hist_all": 0.10},
    "landslide": {"hist_landslide": 0.30, "slope": 0.30, "wet": 0.20, "elev": 0.10, "exposure": 0.10},
    "debris_flow": {"hist_debris": 0.30, "slope": 0.25, "wet": 0.20, "lowland": 0.15, "exposure": 0.10},
    "snowline": {"hist_snow": 0.30, "elev": 0.30, "winter": 0.20, "slope": 0.20},
    "construction": {"flat": 0.30, "exposure": 0.25, "access": 0.15, "dry": 0.10, "hist_all": 0.20},
    "dumping": {"flat": 0.30, "exposure": 0.30, "access": 0.20, "hist_all": 0.20},
    "water_anomaly": {"lowland": 0.30, "wet": 0.25, "hist_all": 0.20, "exposure": 0.15, "flat": 0.10},
    "wetland": {"lowland": 0.35, "wet": 0.25, "flat": 0.20, "hist_all": 0.20},
    "veg_degradation": {"slope": 0.25, "dry": 0.25, "heat": 0.25, "hist_all": 0.25},
    "bare_soil": {"dry": 0.30, "slope": 0.25, "exposure": 0.20, "hist_all": 0.25},
    "road_intrusion": {"exposure": 0.35, "slope": 0.20, "access": 0.20, "hist_all": 0.25},
    "mining_damage": {"hist_mining": 0.30, "access": 0.20, "exposure": 0.15, "flat": 0.15, "dry": 0.10, "hist_all": 0.10},
    "fire_risk": {"fire_wx": 0.35, "dry": 0.20, "wind": 0.15, "slope": 0.10, "exposure": 0.10, "hist_fire": 0.10},
    "flood_risk": {"wet": 0.35, "lowland": 0.30, "flat": 0.15, "hist_all": 0.20},
    "drought_risk": {"dry": 0.35, "heat": 0.25, "lowland": 0.10, "hist_all": 0.30},
    "geohazard_risk": {"slope": 0.30, "wet": 0.25, "hist_landslide": 0.20, "elev": 0.10, "exposure": 0.15},
}


def _factor_text(key: str, h: dict, weights: dict) -> str:
    w = weights.get(key, 0)
    if key == "severity":
        return f"高危类别：{h['dominant_class']}（权重 {w:.2f}）"
    if key == "count":
        return f"近 3 期共 {h['incident_count']} 个异常图斑（权重 {w:.2f}）"
    if key == "area":
        return f"累计面积 {h['area_ha']:.1f} 公顷（权重 {w:.2f}）"
    if key == "recurrence":
        return f"同地重复出现 {h['recurrence']} 类/期（权重 {w:.2f}）"
    if key == "terrain":
        return f"坡度 {h['slope_deg']:.1f}°，地形条件不利（权重 {w:.2f}）"
    if key == "access":
        return f"距最近警务点 {h['dist_police_km']:.1f} km，巡护难度较高（权重 {w:.2f}）"
    if key == "environment":
        return f"当前环境指数偏高：火险 {h['environment']['fire_wx']:.2f} / 湿润 {h['environment']['wet']:.2f}（权重 {w:.2f}）"
    if key == "confidence":
        return f"最新影像 {h.get('scene_age_days')} 天前，云量 {h.get('scene_cloud')}%（权重 {w:.2f}）"
    return key


def _action_for_level(level: str, dominant_class: str) -> str:
    base = {
        "red": "立即核查：建议无人机先侦察，民警随后到场",
        "orange": "24 小时内核查：联系就近派出所/检查站",
        "yellow": "计划核查：结合日常巡护安排",
        "blue": "关注：纳入月度巡查",
    }.get(level, "关注")
    if dominant_class in ("盗采痕迹", "火烧迹地"):
        base += "；注意固定前后影像、现场照片与时间地点证据"
    return base


def _hotspot_name(lon: float, lat: float, dominant_class: str, places: list[dict]) -> tuple[str, str | None]:
    if not places:
        return f"大青山{dominant_class}高发区", None
    p = min(places, key=lambda x: haversine(lon, lat, x["lon"], x["lat"]))
    d = haversine(lon, lat, p["lon"], p["lat"])
    if d <= 35:
        return f"近{p['name']}{dominant_class}高发区", p["name"]
    seg = "西段" if lon < 110.7 else ("中段" if lon < 111.7 else "东段")
    return f"大青山{seg}{dominant_class}高发区", None


def build_hotspots(incidents: list[dict], police: list[dict], places: list[dict],
                   roads: list[list[tuple[float, float]]], weather: dict,
                   weights: dict, scene: dict | None) -> list[dict]:
    clusters = cluster_incidents(incidents)
    if not clusters:
        return []

    # 批量取高程：每个热点取中心 + 东西南北 0.01°
    coords = []
    for c in clusters:
        coords += [(c["lon"], c["lat"]),
                   (c["lon"] + 0.01, c["lat"]), (c["lon"] - 0.01, c["lat"]),
                   (c["lon"], c["lat"] + 0.01), (c["lon"], c["lat"] - 0.01)]
    elevs = cached_elevations(coords)
    conf, age, cloud = confidence_from_scene(scene)

    out = []
    for k, c in enumerate(clusters):
        base = k * 5
        e0, ee, ew, en, es = elevs[base:base + 5]
        ele_m = e0 if e0 is not None else 1600.0
        if None in (ee, ew, en, es):
            slope_deg = 6.0
        else:
            dx = 0.02 * 111_320 * math.cos(math.radians(c["lat"]))
            dy = 0.02 * 110_540
            gx = (ee - ew) / dx
            gy = (en - es) / dy
            slope_deg = math.degrees(math.atan(math.hypot(gx, gy)))
        slope_deg = max(0.0, min(60.0, slope_deg))
        slope_n = clamp(slope_deg / 35.0)

        dist_police = min((haversine(c["lon"], c["lat"], p["lon"], p["lat"]) for p in police), default=30.0)
        dist_place = min((haversine(c["lon"], c["lat"], p["lon"], p["lat"]) for p in places), default=20.0)
        dist_road = dist_to_lines_km(c["lon"], c["lat"], roads)
        exposure = max(clamp(1 - dist_place / 25), clamp(1 - dist_road / 15))
        patrol = clamp(dist_police / 40)
        access_n = 0.5 * exposure + 0.5 * patrol

        env = nearest_weather(c["lon"], c["lat"], weather)
        env_n = environment_for_class(c["dominant_class"], env, slope_n)

        factors = {
            "severity": clamp(c["severity"]),
            "count": clamp(c["count"] / 6.0),
            "area": clamp(c["area_ha"] / 800.0),
            "recurrence": clamp(c["recurrence"] / 4.0),
            "terrain": slope_n,
            "access": access_n,
            "environment": env_n,
            "confidence": conf,
        }
        score = 100.0 * sum(weights.get(k, 0) * v for k, v in factors.items())
        score = round(clamp(score, 0, 100), 1)
        lv = level_for_score(score)
        name, nearest_place = _hotspot_name(c["lon"], c["lat"], c["dominant_class"], places)
        nearest_police = min(police, key=lambda p: haversine(c["lon"], c["lat"], p["lon"], p["lat"])) if police else None

        h = {
            "id": f"HS-{k + 1:03d}",
            "name": name,
            "dominant_class": c["dominant_class"],
            "class_counts": c["class_counts"],
            "incident_count": c["count"],
            "incident_ids": [m["id"] for m in c["members"]],
            "area_ha": round(c["area_ha"], 1),
            "recurrence": c["recurrence"],
            "severity": round(c["severity"], 2),
            "risk_score": score,
            "danger_level": lv["key"],
            "danger_label": lv["label"],
            "color": lv["color"],
            "advice": lv["advice"],
            "centroid": [round(c["lon"], 6), round(c["lat"], 6)],
            "geom": c["geom"],
            "slope_deg": round(slope_deg, 1),
            "elev_m": round(ele_m, 0),
            "dist_police_km": round(dist_police, 1),
            "dist_place_km": round(dist_place, 1),
            "dist_road_km": round(dist_road, 1),
            "nearest_police": nearest_police["name"] if nearest_police else None,
            "nearest_place": nearest_place,
            "factors": {k: round(v, 3) for k, v in factors.items()},
            "weights": {k: round(v, 3) for k, v in weights.items()},
            "environment": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in env.items()},
            "scene_age_days": age,
            "scene_cloud": cloud,
            "confidence": round(conf, 3),
            "reasons": [],
            "recommended_action": _action_for_level(lv["key"], c["dominant_class"]),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        contrib = sorted(((weights.get(k, 0) * v, k) for k, v in factors.items()), reverse=True)
        h["reasons"] = [_factor_text(k, h, weights) for _, k in contrib[:3]]
        out.append(h)

    out.sort(key=lambda x: x["risk_score"], reverse=True)
    for i, h in enumerate(out):
        h["rank"] = i + 1
    return out


def _cell_grid() -> Iterable[dict]:
    step = 0.2
    x0, y0, x1, y1 = AOI_BBOX
    j = 0
    lat = y0
    while lat < y1:
        i = 0
        lon = x0
        while lon < x1:
            yield {
                "id": f"G-{j:02d}-{i:02d}",
                "lon": lon + step / 2,
                "lat": lat + step / 2,
                "poly": Polygon([
                    (lon, lat), (lon + step, lat), (lon + step, lat + step),
                    (lon, lat + step), (lon, lat),
                ]),
            }
            i += 1
            lon += step
        j += 1
        lat += step


def build_risk_grid(incidents: list[dict], police: list[dict], places: list[dict],
                    roads: list[list[tuple[float, float]]], weather: dict) -> dict:
    cells = list(_cell_grid())
    # 高程批量取：中心 + 四邻
    coords = []
    for c in cells:
        coords += [(c["lon"], c["lat"]),
                   (c["lon"] + 0.01, c["lat"]), (c["lon"] - 0.01, c["lat"]),
                   (c["lon"], c["lat"] + 0.01), (c["lon"], c["lat"] - 0.01)]
    elevs = cached_elevations(coords)

    # 历史密度（按类别）
    def density(lon, lat, cls=None):
        s = 0.0
        for inc in incidents:
            if cls and inc["class"] != cls:
                continue
            d = haversine(lon, lat, inc["lon"], inc["lat"])
            if d < 45:
                s += inc["severity"] * math.exp(-d / 12.0)
        return s

    raw = []
    for k, c in enumerate(cells):
        base = k * 5
        e0, ee, ew, en, es = elevs[base:base + 5]
        ele = e0 if e0 is not None else 1600.0
        if None in (ee, ew, en, es):
            slope_deg = 6.0
        else:
            dx = 0.02 * 111_320 * math.cos(math.radians(c["lat"]))
            dy = 0.02 * 110_540
            slope_deg = math.degrees(math.atan(math.hypot((ee - ew) / dx, (en - es) / dy)))
        slope_deg = max(0.0, min(60.0, slope_deg))
        env = nearest_weather(c["lon"], c["lat"], weather)
        dist_police = min((haversine(c["lon"], c["lat"], p["lon"], p["lat"]) for p in police), default=30)
        dist_place = min((haversine(c["lon"], c["lat"], p["lon"], p["lat"]) for p in places), default=20)
        dist_road = dist_to_lines_km(c["lon"], c["lat"], roads)
        exposure = max(clamp(1 - dist_place / 25), clamp(1 - dist_road / 15))
        patrol = clamp(dist_police / 40)
        access = 0.5 * exposure + 0.5 * patrol
        raw.append({
            **c,
            "elev": ele,
            "slope_deg": slope_deg,
            "env": env,
            "exposure": exposure,
            "access": access,
            "dist_police": dist_police,
            "dist_road": dist_road,
            "hist_h": {
                "hist_all": density(c["lon"], c["lat"]),
                "hist_fire": density(c["lon"], c["lat"], "火烧迹地"),
                "hist_mining": density(c["lon"], c["lat"], "盗采痕迹"),
                "hist_landslide": density(c["lon"], c["lat"], "滑坡"),
                "hist_debris": density(c["lon"], c["lat"], "泥石流"),
                "hist_snow": density(c["lon"], c["lat"], "雪线变化"),
            },
        })

    max_hist = {k: max((r["hist_h"][k] for r in raw), default=0) or 1.0 for k in raw[0]["hist_h"]} if raw else {}

    feats = []
    for r in raw:
        hist = {k: clamp(v / max_hist.get(k, 1.0)) for k, v in r["hist_h"].items()}
        slope_n = clamp(r["slope_deg"] / 35)
        elev_n = clamp((r["elev"] - 900) / 1500)
        features = {
            **hist,
            "slope": slope_n,
            "elev": elev_n,
            "flat": 1 - slope_n,
            "lowland": 1 - elev_n,
            "access": r["access"],
            "exposure": r["exposure"],
            "fire_wx": r["env"]["fire_wx"],
            "wet": r["env"]["wet"],
            "dry": r["env"]["dry"],
            "heat": r["env"]["heat"],
            "wind": r["env"]["wind_n"],
            "winter": r["env"]["winter"],
        }
        risks = {}
        for hid, wmap in GRID_WEIGHTS.items():
            risks[hid] = round(100 * sum(w * features.get(f, 0) for f, w in wmap.items()), 1)
        overall = round(clamp(0.55 * hist["hist_all"] * 100 + 0.45 * max(risks.values(), default=0), 0, 100), 1)
        lv = level_for_score(overall)
        dominant = max(risks.items(), key=lambda kv: kv[1])[0]
        props = {
            "cell_id": r["id"],
            "risk": overall,
            "danger_level": lv["key"],
            "danger_label": lv["label"],
            "color": lv["color"],
            "dominant_hazard": dominant,
            "dominant_hazard_name": HAZARD_BY_ID.get(dominant, {}).get("name", dominant),
            "hist": round(hist["hist_all"], 3),
            "slope_deg": round(r["slope_deg"], 1),
            "elev_m": round(r["elev"], 0),
            "dist_police_km": round(r["dist_police"], 1),
            "dist_road_km": round(r["dist_road"], 1),
            "exposure": round(r["exposure"], 3),
            "access": round(r["access"], 3),
            "fire_wx": round(r["env"]["fire_wx"], 3),
            "wet": round(r["env"]["wet"], 3),
            "dry": round(r["env"]["dry"], 3),
            "weather_point": None,
        }
        for hid, val in risks.items():
            props[f"risk_{hid}"] = val
        feats.append({
            "type": "Feature",
            "geometry": mapping(r["poly"]),
            "properties": props,
        })
    return {"type": "FeatureCollection", "name": "risk_grid", "features": feats}


def _write_input_csv(hotspots: list[dict]):
    path = config.DATA / "hotspots_input.csv"
    cols = [
        "id", "name", "lon", "lat", "count", "area_ha", "severity", "recurrence",
        "slope_deg", "elev_m", "dist_police_km", "dist_place_km", "dist_road_km",
        "fire_wx", "wet", "dry", "temp", "humidity", "wind", "precip", "soil",
        "confidence", "dominant_class",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for h in hotspots:
            env = h["environment"]
            w.writerow({
                "id": h["id"], "name": h["name"],
                "lon": h["centroid"][0], "lat": h["centroid"][1],
                "count": h["incident_count"], "area_ha": h["area_ha"],
                "severity": h["severity"], "recurrence": h["recurrence"],
                "slope_deg": h["slope_deg"], "elev_m": h["elev_m"],
                "dist_police_km": h["dist_police_km"], "dist_place_km": h["dist_place_km"],
                "dist_road_km": h["dist_road_km"],
                "fire_wx": env["fire_wx"], "wet": env["wet"], "dry": env["dry"],
                "temp": env["temp"], "humidity": env["humidity"], "wind": env["wind"],
                "precip": env["precip"], "soil": env["soil"],
                "confidence": h["confidence"], "dominant_class": h["dominant_class"],
            })


def _to_geojson(hotspots: list[dict]) -> dict:
    feats = []
    for h in hotspots:
        c = h["centroid"]
        if h.get("geom") is not None:
            geom = mapping(h["geom"])
        else:
            buf = 0.03
            poly = Polygon([
                (c[0] - buf, c[1] - buf), (c[0] + buf, c[1] - buf),
                (c[0] + buf, c[1] + buf), (c[0] - buf, c[1] + buf),
                (c[0] - buf, c[1] - buf),
            ])
            geom = mapping(poly)
        props = {k: v for k, v in h.items() if k not in ("centroid", "geom")}
        props["centroid"] = c
        feats.append({
            "type": "Feature",
            "geometry": geom,
            "properties": props,
        })
    return {"type": "FeatureCollection", "name": "hotspots", "features": feats}


def run() -> dict:
    incidents = load_incidents()
    police = _points_from_geojson(config.DATA / "police.geojson")
    places = _points_from_geojson(config.DATA / "places.geojson")
    roads = _lines_from_geojson(config.DATA / "roads.geojson")
    weather = load_weather()
    scene = load_latest_s2()
    weights, wmeta = load_weights()

    hotspots = build_hotspots(incidents, police, places, roads, weather, weights, scene)
    grid = build_risk_grid(incidents, police, places, roads, weather)

    # 等级变化（与上一次 summary 对比）
    prev = {}
    sp = config.DATA / "summary.json"
    if sp.exists():
        try:
            prev = json.loads(sp.read_text(encoding="utf-8")).get("hotspot_levels", {})
        except Exception:
            prev = {}
    changes = []
    for h in hotspots:
        old = prev.get(h["id"])
        if old and old != h["danger_level"]:
            changes.append({
                "id": h["id"], "name": h["name"],
                "old": old, "new": h["danger_level"],
                "risk_score": h["risk_score"],
            })

    danger_counts = {lv["key"]: 0 for lv in DANGER_LEVELS}
    for h in hotspots:
        danger_counts[h["danger_level"]] = danger_counts.get(h["danger_level"], 0) + 1

    mpc_path = config.DATA / "mpc_scenes.json"
    mpc = {}
    if mpc_path.exists():
        try:
            mpc = json.loads(mpc_path.read_text(encoding="utf-8"))
        except Exception:
            mpc = {}
    natural_ok = [n for n in (mpc.get("natural") or []) if n.get("ok")]

    summary = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": {"name": config.AOI_NAME, "bbox": AOI_BBOX, "center": config.AOI_CENTER},
        "incidents": len(incidents),
        "hotspots": len(hotspots),
        "danger_counts": danger_counts,
        "top_hotspots": [
            {"id": h["id"], "name": h["name"], "risk_score": h["risk_score"],
             "danger_level": h["danger_level"], "dominant_class": h["dominant_class"],
             "incident_count": h["incident_count"]}
            for h in hotspots[:10]
        ],
        "hotspot_levels": {h["id"]: h["danger_level"] for h in hotspots},
        "changes": changes,
        "model": {
            "weights": {k: round(v, 4) for k, v in weights.items()},
            "meta": wmeta,
            "method": "AHP 加权 + 规则筛查" if wmeta.get("method") == "AHP" else "默认加权 + 规则筛查",
        },
        "mpc": {
            "fetched_at": mpc.get("fetched_at"),
            "s2_count": len(mpc.get("sentinel2") or []),
            "landsat_count": len(mpc.get("landsat") or []),
            "natural_ok": len(natural_ok),
            "latest_s2": (mpc.get("sentinel2") or [{}])[0].get("datetime") if mpc.get("sentinel2") else None,
            "latest_s2_cloud": (mpc.get("sentinel2") or [{}])[0].get("cloud_cover") if mpc.get("sentinel2") else None,
            "errors": mpc.get("errors") or [],
        },
        "weather": {
            "fetched_at": weather.get("fetched_at"),
            "points": weather.get("points", []),
        },
        "hazards": [
            {"id": h["id"], "name": h["name"], "status": h["status"], "color": h["color"]}
            for h in HAZARDS
        ],
        "disclaimer": (
            "5 个 validated 类别来自青山卫士任务二实测检测；screening/risk 为扩展规则筛查/气象风险，"
            "需人工核查，不作为定性结论。警务点位与路网为演示示意数据。"
        ),
    }

    config.DATA.joinpath("hotspots.geojson").write_text(
        json.dumps(_to_geojson(hotspots), ensure_ascii=False), encoding="utf-8"
    )
    config.DATA.joinpath("risk_grid.geojson").write_text(
        json.dumps(grid, ensure_ascii=False), encoding="utf-8"
    )
    config.DATA.joinpath("summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_input_csv(hotspots)
    if not (config.DATA / "model_weights.json").exists():
        config.DATA.joinpath("model_weights.json").write_text(json.dumps({
            "weights": weights, **wmeta,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"hotspots={len(hotspots)} grid={len(grid['features'])} changes={len(changes)}")
    for h in hotspots[:8]:
        print(f"  {h['id']} {h['danger_label']} {h['risk_score']:5.1f} {h['name']} ({h['dominant_class']}, {h['incident_count']})")
    return summary


if __name__ == "__main__":
    run()

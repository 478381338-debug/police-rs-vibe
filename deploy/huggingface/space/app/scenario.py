"""自然条件 → 灾害危险系数 → 未来 14 天时空推演。

模型定位
--------
这是一个“早期预警指数”，不是确定性的灾害预报。它把实时/预报气象、地形、
历史异常密度与可达性，映射成 0~100 的危险系数，并用红/橙/黄/蓝分级。

方法
----
1. 物理经验隶属度：高温、低湿、大风、累计降雨、土壤湿度、坡度、高程、
   植被、道路可达性等，用 sigmoid / 梯形隶属函数映射到 0~1。
2. AHP 权重：每个灾种一套可解释权重（与 MATLAB AHP 权重口径一致）。
3. 综合评分：score = 100 × Σ(weight_i × membership_i)。
4. 时空推演：取 Open-Meteo 未来 14 天逐小时预报，聚合成逐日气象；
   研究区 180 个风险格网按“高程订正 + 格网坡度/历史密度/可达性”逐日重算。

参考的开源方法（只借鉴方法，不直接复制代码）
--------------------------------------------
- 加拿大森林火险天气指数 FWI（cffdrs 项目）的火险因子思路
- 降雨强度-历时（ID）阈值在滑坡/泥石流预警中的常用形式
- SPI/SPEI 干旱指数的“降水亏缺 + 蒸散”思想
- 模糊综合评判 / AHP 的多因子加权方式
详见 docs/MODEL.md。
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from app import config
from app.config import AOI_CENTER, DANGER_LEVELS, level_for_score

CACHE_DIR = config.DATA / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
GRID_CACHE: list[dict] | None = None
GRID_MTIME: float = 0.0

HAZARD_ALIASES = {
    "fire": "fire_risk",
    "fire_risk": "fire_risk",
    "fire_scar": "fire_risk",
    "landslide": "landslide",
    "debris_flow": "debris_flow",
    "flood": "flood_risk",
    "flood_risk": "flood_risk",
    "drought": "drought_risk",
    "drought_risk": "drought_risk",
    "geohazard": "geohazard_risk",
    "geohazard_risk": "geohazard_risk",
    "mining": "mining",
    "mining_damage": "mining",
}

HAZARD_LABELS = {
    "fire_risk": "火灾风险",
    "landslide": "滑坡",
    "debris_flow": "泥石流",
    "flood_risk": "洪涝风险",
    "drought_risk": "干旱风险",
    "geohazard_risk": "地质灾害隐患",
    "mining": "盗采/矿山风险",
}

HAZARD_META = {
    "fire_risk": {"static": False, "unit": "火险指数", "source": "FWI 因子 + 可燃物/可达性"},
    "landslide": {"static": False, "unit": "滑坡危险系数", "source": "坡度 + 累计降雨 ID 阈值 + 土壤湿度"},
    "debris_flow": {"static": False, "unit": "泥石流危险系数", "source": "坡度 + 累计降雨 + 沟谷/土壤"},
    "flood_risk": {"static": False, "unit": "洪涝危险系数", "source": "累计降雨 + 低洼/平坦 + 土壤饱和度"},
    "drought_risk": {"static": False, "unit": "干旱危险系数", "source": "高温低湿 + 降水亏缺 + 土壤/NDVI"},
    "geohazard_risk": {"static": False, "unit": "地灾危险系数", "source": "坡度 + 降雨 + 土壤 + 历史灾害"},
    "mining": {"static": True, "unit": "盗采/矿山风险", "source": "平坦地形 + 道路可达 + 历史盗采密度"},
}

FACTOR_LABELS = {
    "temp": "高温", "dry_air": "低湿", "wind": "大风", "rain_short": "少雨",
    "rain3": "3 天累计降雨", "rain7": "7 天累计降雨", "intensity": "短时雨强",
    "soil": "土壤湿度", "slope": "坡度", "elev": "高程", "ndvi": "植被/可燃物",
    "road": "道路可达", "hist": "历史异常密度", "valley": "沟谷/低洼", "flat": "平坦地形",
    "lowland": "低洼", "dry": "干燥度",
}

DEFAULT_INPUTS = {
    "lon": AOI_CENTER[0], "lat": AOI_CENTER[1],
    "temperature": 5.0, "humidity": 35.0, "wind": 10.0,
    "precipitation": 0.0, "soil_moisture": 0.15,
    "slope_deg": 8.0, "elev_m": 1600.0, "ndvi": 0.45,
    "dist_road_km": 3.0, "hist_count": 1.0,
    "rain_3d": 0.0, "rain_7d": 0.0,
    "rain_factor": 1.0, "temp_offset": 0.0, "wind_factor": 1.0,
    "days": 14,
}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def safe_float(v, default: float) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def sigmoid(x: float, k: float, x0: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-k * (x - x0)))
    except OverflowError:
        return 0.0 if x < x0 else 1.0


def trap(x: float, a: float, b: float, c: float, d: float) -> float:
    if x <= a or x >= d:
        return 0.0
    if b <= x <= c:
        return 1.0
    if x < b:
        return (x - a) / max(1e-9, b - a)
    return (d - x) / max(1e-9, d - c)


def haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _geom_center(geom: dict) -> tuple[float, float]:
    xs, ys = [], []
    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            xs.append(float(c[0])); ys.append(float(c[1])); return
        if isinstance(c, (list, tuple)):
            for it in c:
                walk(it)
    walk((geom or {}).get("coordinates") or [])
    if not xs:
        return AOI_CENTER
    return sum(xs) / len(xs), sum(ys) / len(ys)


def load_grid() -> list[dict]:
    global GRID_CACHE, GRID_MTIME
    p = config.DATA / "risk_grid.geojson"
    if not p.exists():
        return []
    mtime = p.stat().st_mtime
    if GRID_CACHE is not None and mtime == GRID_MTIME:
        return GRID_CACHE
    d = json.loads(p.read_text(encoding="utf-8"))
    cells = []
    for f in d.get("features", []):
        props = f.get("properties") or {}
        lon, lat = _geom_center(f.get("geometry") or {})
        cells.append({
            "cell_id": props.get("cell_id"),
            "lon": lon, "lat": lat,
            "slope_deg": safe_float(props.get("slope_deg"), 8),
            "elev_m": safe_float(props.get("elev_m"), 1600),
            "hist": safe_float(props.get("hist"), 0),
            "dist_road_km": safe_float(props.get("dist_road_km"), 3),
            "dist_police_km": safe_float(props.get("dist_police_km"), 20),
            "exposure": safe_float(props.get("exposure"), 0.3),
            "access": safe_float(props.get("access"), 0.5),
        })
    GRID_CACHE, GRID_MTIME = cells, mtime
    return cells


def nearest_cell(lon: float, lat: float) -> dict | None:
    cells = load_grid()
    if not cells:
        return None
    return min(cells, key=lambda c: haversine(lon, lat, c["lon"], c["lat"]))


def load_weather() -> dict:
    p = config.DATA / "weather.json"
    if not p.exists():
        return {"points": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"points": []}


def nearest_weather(lon: float, lat: float) -> dict | None:
    pts = load_weather().get("points") or []
    if not pts:
        return None
    return min(pts, key=lambda p: haversine(lon, lat, p.get("lon", 0), p.get("lat", 0)))


def nearest_hotspot(lon: float, lat: float) -> dict | None:
    p = config.DATA / "hotspots.geojson"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    best = None
    best_d = 9e9
    for f in d.get("features", []):
        pr = f.get("properties") or {}
        c = pr.get("centroid") or []
        if len(c) < 2:
            continue
        dist = haversine(lon, lat, c[0], c[1])
        if dist < best_d:
            best_d, best = dist, pr
    return best


def normalize_inputs(payload: dict | None) -> dict:
    p = payload or {}
    lon = safe_float(p.get("lon"), DEFAULT_INPUTS["lon"])
    lat = safe_float(p.get("lat"), DEFAULT_INPUTS["lat"])
    cell = nearest_cell(lon, lat)
    weather = nearest_weather(lon, lat)
    hotspot = nearest_hotspot(lon, lat)
    current = (weather or {}).get("current") or {}

    def pick(key, default):
        return safe_float(p.get(key), default)

    out = {
        "lon": clamp(lon, 70, 140), "lat": clamp(lat, 15, 55),
        "temperature": pick("temperature", safe_float(current.get("temperature_2m"), DEFAULT_INPUTS["temperature"])),
        "humidity": pick("humidity", safe_float(current.get("relative_humidity_2m"), DEFAULT_INPUTS["humidity"])),
        "wind": pick("wind", safe_float(current.get("wind_speed_10m"), DEFAULT_INPUTS["wind"])),
        "precipitation": pick("precipitation", safe_float(current.get("precipitation"), DEFAULT_INPUTS["precipitation"])),
        "soil_moisture": pick("soil_moisture", safe_float(current.get("soil_moisture_0_to_10cm"), DEFAULT_INPUTS["soil_moisture"])),
        "slope_deg": pick("slope_deg", safe_float((cell or {}).get("slope_deg"), DEFAULT_INPUTS["slope_deg"])),
        "elev_m": pick("elev_m", safe_float((cell or {}).get("elev_m"), safe_float((weather or {}).get("elevation"), DEFAULT_INPUTS["elev_m"]))),
        "ndvi": pick("ndvi", DEFAULT_INPUTS["ndvi"]),
        "dist_road_km": pick("dist_road_km", safe_float((cell or {}).get("dist_road_km"), DEFAULT_INPUTS["dist_road_km"])),
        "hist_count": pick("hist_count", safe_float((hotspot or {}).get("incident_count"), DEFAULT_INPUTS["hist_count"])),
        "rain_3d": pick("rain_3d", 0.0),
        "rain_7d": pick("rain_7d", 0.0),
        "rain_factor": clamp(pick("rain_factor", 1.0), 0.1, 3.0),
        "temp_offset": clamp(pick("temp_offset", 0.0), -15.0, 15.0),
        "wind_factor": clamp(pick("wind_factor", 1.0), 0.2, 3.0),
        "days": int(clamp(safe_float(p.get("days"), 14), 1, 14)),
        "hazard": HAZARD_ALIASES.get(str(p.get("hazard") or "fire_risk"), "fire_risk"),
    }
    # 基础边界
    out["temperature"] = clamp(out["temperature"], -40, 50)
    out["humidity"] = clamp(out["humidity"], 1, 100)
    out["wind"] = clamp(out["wind"], 0, 150)
    out["precipitation"] = clamp(out["precipitation"], 0, 300)
    out["soil_moisture"] = clamp(out["soil_moisture"], 0.01, 0.6)
    out["slope_deg"] = clamp(out["slope_deg"], 0, 70)
    out["elev_m"] = clamp(out["elev_m"], 0, 6000)
    out["ndvi"] = clamp(out["ndvi"], 0, 1)
    out["dist_road_km"] = clamp(out["dist_road_km"], 0, 100)
    out["hist_count"] = clamp(out["hist_count"], 0, 50)
    out["rain_3d"] = clamp(out["rain_3d"], 0, 1000)
    out["rain_7d"] = clamp(out["rain_7d"], 0, 2000)
    out["_cell"] = cell
    out["_weather"] = weather
    out["_hotspot"] = hotspot
    return out


def _daily_from_hourly(raw: dict, fallback_soil: float) -> list[dict]:
    h = raw.get("hourly") or {}
    times = h.get("time") or []
    temps = h.get("temperature_2m") or []
    hums = h.get("relative_humidity_2m") or []
    precs = h.get("precipitation") or []
    winds = h.get("wind_speed_10m") or []
    soils = h.get("soil_moisture_0_to_10cm") or []

    groups: dict[str, dict] = {}
    for i, t in enumerate(times):
        date = str(t)[:10]
        g = groups.setdefault(date, {"temp": [], "hum": [], "precip": [], "wind": [], "soil": []})
        if i < len(temps) and temps[i] is not None: g["temp"].append(float(temps[i]))
        if i < len(hums) and hums[i] is not None: g["hum"].append(float(hums[i]))
        if i < len(precs) and precs[i] is not None: g["precip"].append(float(precs[i]))
        if i < len(winds) and winds[i] is not None: g["wind"].append(float(winds[i]))
        if i < len(soils) and soils[i] is not None: g["soil"].append(float(soils[i]))

    daily = []
    for date in sorted(groups):
        g = groups[date]
        daily.append({
            "date": date,
            "temp": round(sum(g["temp"]) / len(g["temp"]), 1) if g["temp"] else 5.0,
            "humidity": round(sum(g["hum"]) / len(g["hum"]), 1) if g["hum"] else 35.0,
            "wind": round(max(g["wind"]), 1) if g["wind"] else 10.0,
            "precip": round(sum(g["precip"]), 1) if g["precip"] else 0.0,
            "soil": round(sum(g["soil"]) / len(g["soil"]), 3) if g["soil"] else fallback_soil,
        })
    return daily


def fetch_forecast(lon: float, lat: float, days: int, fallback_soil: float) -> tuple[list[dict], str | None]:
    key = f"forecast_{lon:.3f}_{lat:.3f}_{days}.json"
    cache = CACHE_DIR / key
    if cache.exists() and time.time() - cache.stat().st_mtime < 1800:
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            return d.get("daily") or [], d.get("warning")
        except Exception:
            pass
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,soil_moisture_0_to_10cm",
        "timezone": "Asia/Shanghai",
        "forecast_days": max(1, min(days, 14)),
    }
    warning = None
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        daily = _daily_from_hourly(r.json(), fallback_soil)
        if not daily:
            raise RuntimeError("empty forecast")
    except Exception as e:
        warning = f"未来预报获取失败，使用当前气象常量推演：{e}"
        now = datetime.now().strftime("%Y-%m-%d")
        daily = [{
            "date": now, "temp": 5.0, "humidity": 35.0, "wind": 10.0,
            "precip": 0.0, "soil": fallback_soil,
        } for _ in range(days)]
    daily = daily[:days]
    # 滚动累计降雨
    for i, d in enumerate(daily):
        d["rain3"] = round(sum(x["precip"] for x in daily[max(0, i - 2):i + 1]), 1)
        d["rain7"] = round(sum(x["precip"] for x in daily[max(0, i - 6):i + 1]), 1)
    try:
        cache.write_text(json.dumps({"daily": daily, "warning": warning, "fetched_at": datetime.now(timezone.utc).isoformat()},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return daily, warning


def _apply_scenario(daily: list[dict], inp: dict) -> list[dict]:
    out = []
    for d in daily:
        x = dict(d)
        x["precip"] = round(x["precip"] * inp["rain_factor"], 1)
        x["temp"] = round(x["temp"] + inp["temp_offset"], 1)
        x["wind"] = round(x["wind"] * inp["wind_factor"], 1)
        x["soil"] = round(clamp(x["soil"] + (inp["rain_factor"] - 1) * 0.04, 0.02, 0.5), 3)
        out.append(x)
    for i, d in enumerate(out):
        d["rain3"] = round(sum(x["precip"] for x in out[max(0, i - 2):i + 1]), 1)
        d["rain7"] = round(sum(x["precip"] for x in out[max(0, i - 6):i + 1]), 1)
    return out


def _score(hazard: str, ctx: dict) -> dict:
    """返回 score / level / factors / memberships。"""
    temp = ctx["temperature"]
    hum = ctx["humidity"]
    wind = ctx["wind"]
    precip = ctx["precipitation"]
    soil = ctx["soil_moisture"]
    slope = ctx["slope_deg"]
    elev = ctx["elev_m"]
    ndvi = ctx["ndvi"]
    dist_road = ctx["dist_road_km"]
    hist = ctx["hist_count"]
    rain3 = ctx.get("rain_3d", 0)
    rain7 = ctx.get("rain_7d", 0)

    f: dict[str, tuple[float, float]] = {}  # key -> (membership, weight)

    if hazard == "fire_risk":
        f = {
            "temp": (sigmoid(temp, 0.12, 22), 0.20),
            "dry_air": (1 - sigmoid(hum, 0.10, 35), 0.18),
            "wind": (clamp(wind / 40), 0.14),
            "rain_short": (1 - clamp(precip / 10), 0.08),
            "rain3": (1 - clamp(rain3 / 20), 0.10),
            "rain7": (1 - clamp(rain7 / 40), 0.05),
            "ndvi": (clamp(ndvi / 0.65), 0.08),
            "slope": (clamp(slope / 30), 0.05),
            "road": (clamp(1 - dist_road / 10), 0.12),
        }
    elif hazard == "landslide":
        f = {
            "slope": (sigmoid(slope, 0.18, 16), 0.28),
            "rain3": (clamp(rain3 / 100), 0.24),
            "intensity": (clamp(max(precip, rain3 / 3) / 40), 0.14),
            "rain7": (clamp(rain7 / 180), 0.08),
            "soil": (clamp(soil / 0.35), 0.12),
            "hist": (clamp(hist / 5), 0.08),
            "elev": (clamp((elev - 900) / 1200), 0.03),
            "road": (clamp(1 - dist_road / 20), 0.03),
        }
    elif hazard == "debris_flow":
        f = {
            "slope": (sigmoid(slope, 0.16, 14), 0.22),
            "rain3": (clamp(rain3 / 100), 0.25),
            "intensity": (clamp(max(precip, rain3 / 3) / 40), 0.15),
            "rain7": (clamp(rain7 / 180), 0.06),
            "soil": (clamp(soil / 0.35), 0.12),
            "hist": (clamp(hist / 5), 0.08),
            "valley": (1 - clamp((elev - 900) / 1500), 0.07),
            "road": (clamp(1 - dist_road / 20), 0.05),
        }
    elif hazard == "flood_risk":
        f = {
            "rain3": (clamp(rain3 / 120), 0.30),
            "intensity": (clamp(max(precip, rain3 / 3) / 50), 0.18),
            "soil": (clamp(soil / 0.40), 0.15),
            "lowland": (1 - clamp((elev - 800) / 1200), 0.15),
            "flat": (1 - clamp(slope / 15), 0.10),
            "hist": (clamp(hist / 5), 0.07),
            "road": (clamp(1 - dist_road / 25), 0.05),
        }
    elif hazard == "drought_risk":
        f = {
            "temp": (sigmoid(temp, 0.10, 25), 0.20),
            "dry_air": (1 - clamp(hum / 100), 0.15),
            "rain7": (1 - clamp(rain7 / 30), 0.20),
            "soil": (1 - clamp(soil / 0.30), 0.18),
            "ndvi": (1 - clamp(ndvi / 0.60), 0.14),
            "wind": (clamp(wind / 40), 0.08),
            "rain3": (1 - clamp(rain3 / 20), 0.05),
        }
    elif hazard == "geohazard_risk":
        f = {
            "slope": (sigmoid(slope, 0.15, 18), 0.30),
            "rain3": (clamp(rain3 / 100), 0.24),
            "soil": (clamp(soil / 0.35), 0.15),
            "hist": (clamp(hist / 5), 0.15),
            "elev": (clamp((elev - 900) / 1200), 0.08),
            "road": (clamp(1 - dist_road / 20), 0.08),
        }
    else:  # mining：静态风险
        f = {
            "flat": (1 - clamp(slope / 20), 0.25),
            "road": (clamp(1 - dist_road / 10), 0.25),
            "dry": (1 - clamp(soil / 0.30), 0.15),
            "hist": (clamp(hist / 5), 0.20),
            "ndvi": (1 - clamp(ndvi / 0.60), 0.15),
        }

    total_w = sum(w for _, w in f.values()) or 1.0
    score = 100.0 * sum(v * w / total_w for v, w in f.values())
    score = round(clamp(score, 0, 100), 1)
    lv = level_for_score(score)
    factors = [{
        "key": k, "label": FACTOR_LABELS.get(k, k),
        "value": round(v, 3), "weight": round(w / total_w, 3),
        "contribution": round(v * w / total_w * 100, 1),
    } for k, (v, w) in f.items()]
    factors.sort(key=lambda x: x["contribution"], reverse=True)
    return {"score": score, "level": lv["key"], "level_label": lv["label"],
            "color": lv["color"], "factors": factors, "memberships": {k: round(v, 3) for k, (v, _) in f.items()}}


def _reasons(hazard: str, factors: list[dict]) -> list[str]:
    out = []
    for x in factors[:3]:
        key = x["key"]; val = x["value"]
        if key == "temp":
            out.append(f"高温：{val * 100:.0f}/100（当前气温对{HAZARD_LABELS.get(hazard, '风险')}有推升）")
        elif key == "dry_air":
            out.append(f"空气干燥：湿度因子 {val * 100:.0f}/100")
        elif key == "wind":
            out.append(f"风力偏大：风速因子 {val * 100:.0f}/100")
        elif key == "rain_short":
            out.append(f"当日少雨：降水抑制因子 {val * 100:.0f}/100")
        elif key == "rain3":
            out.append(f"3 天累计降雨因子 {val * 100:.0f}/100")
        elif key == "rain7":
            out.append(f"7 天累计降雨因子 {val * 100:.0f}/100")
        elif key == "intensity":
            out.append(f"短时雨强因子 {val * 100:.0f}/100")
        elif key == "soil":
            out.append(f"土壤湿度因子 {val * 100:.0f}/100")
        elif key == "slope":
            out.append(f"坡度因子 {val * 100:.0f}/100")
        elif key == "elev":
            out.append(f"高程因子 {val * 100:.0f}/100")
        elif key == "ndvi":
            out.append(f"植被/可燃物因子 {val * 100:.0f}/100")
        elif key == "road":
            out.append(f"道路可达性因子 {val * 100:.0f}/100")
        elif key == "hist":
            out.append(f"历史异常密度因子 {val * 100:.0f}/100")
        elif key == "valley":
            out.append(f"沟谷/低洼因子 {val * 100:.0f}/100")
        elif key == "flat":
            out.append(f"平坦地形因子 {val * 100:.0f}/100")
        elif key == "lowland":
            out.append(f"低洼地形因子 {val * 100:.0f}/100")
        elif key == "dry":
            out.append(f"干燥度因子 {val * 100:.0f}/100")
        else:
            out.append(f"{FACTOR_LABELS.get(key, key)}因子 {val * 100:.0f}/100")
    return out


def _model_info(hazard: str) -> dict:
    meta = HAZARD_META.get(hazard, {})
    return {
        "name": "物理经验隶属度 + AHP 加权",
        "version": "scenario-v1",
        "hazard": hazard,
        "hazard_label": HAZARD_LABELS.get(hazard, hazard),
        "unit": meta.get("unit", "危险系数"),
        "source": meta.get("source", ""),
        "static": bool(meta.get("static")),
        "assumptions": [
            "危险系数是早期预警指数，不是确定性灾害预报；需结合现场核查。",
            "未来气象来自 Open-Meteo，空间格网按高程/坡度/历史密度做订正。",
            "权重为可解释的经验/AHP 权重，可在 docs/MODEL.md 中查看与调整。",
        ],
        "references": [
            "cffdrs / Canadian Forest Fire Weather Index（FWI 因子思路）",
            "Rainfall intensity-duration thresholds for landslide early warning",
            "SPI/SPEI drought indices（降水亏缺 + 蒸散思路）",
            "AHP / fuzzy comprehensive evaluation",
        ],
    }


def assess(payload: dict | None) -> dict:
    inp = normalize_inputs(payload)
    hazard = inp["hazard"]
    ctx = dict(inp)
    # 当前评估：把用户输入的前期累计降雨也纳入；rain_factor 对前期降雨同样生效
    ctx["rain_3d"] = (inp["rain_3d"] if inp["rain_3d"] > 0 else inp["precipitation"]) * inp["rain_factor"]
    ctx["rain_7d"] = (inp["rain_7d"] if inp["rain_7d"] > 0 else inp["precipitation"]) * inp["rain_factor"]
    r = _score(hazard, ctx)
    return {
        "ok": True,
        "inputs": {k: v for k, v in inp.items() if not k.startswith("_")},
        "hazard": hazard,
        "hazard_label": HAZARD_LABELS.get(hazard, hazard),
        "score": r["score"],
        "level": r["level"],
        "level_label": r["level_label"],
        "color": r["color"],
        "factors": r["factors"],
        "reasons": _reasons(hazard, r["factors"]),
        "model": _model_info(hazard),
        "nearest_cell": (inp.get("_cell") or {}).get("cell_id"),
        "nearest_hotspot": (inp.get("_hotspot") or {}).get("name"),
    }


def timeline(payload: dict | None) -> dict:
    inp = normalize_inputs(payload)
    hazard = inp["hazard"]
    days = inp["days"]
    daily_base, warning = fetch_forecast(inp["lon"], inp["lat"], days, inp["soil_moisture"])
    daily = _apply_scenario(daily_base, inp)
    # 把输入的前期累计降雨作为“初始条件”加入时间序列，并按天衰减（排水/蒸散）
    for i, d in enumerate(daily):
        d["rain3"] = round(inp["rain_3d"] * inp["rain_factor"] * (0.7 ** i) + d.get("rain3", 0), 1)
        d["rain7"] = round(inp["rain_7d"] * inp["rain_factor"] * (0.85 ** i) + d.get("rain7", 0), 1)
    cells = load_grid()

    point_series = []
    frames = []
    first_orange = None
    first_red = None
    for i, d in enumerate(daily):
        ctx = dict(inp)
        ctx.update({
            "temperature": d["temp"], "humidity": d["humidity"], "wind": d["wind"],
            "precipitation": d["precip"], "soil_moisture": d["soil"],
            "rain_3d": d["rain3"], "rain_7d": d["rain7"],
        })
        pr = _score(hazard, ctx)
        if first_orange is None and pr["score"] >= 60:
            first_orange = d["date"]
        if first_red is None and pr["score"] >= 70:
            first_red = d["date"]
        point_series.append({
            "date": d["date"], "score": pr["score"], "level": pr["level"],
            "level_label": pr["level_label"], "color": pr["color"],
        })

        cell_scores = {}
        for cell in cells:
            elev_delta = cell["elev_m"] - inp["elev_m"]
            cell_ctx = dict(inp)
            cell_ctx.update({
                "temperature": d["temp"] - 0.0065 * elev_delta,
                "humidity": clamp(d["humidity"] + elev_delta * 0.008, 5, 100),
                "wind": max(0.0, d["wind"] * max(0.5, 1 + elev_delta / 3000)),
                "precipitation": max(0.0, d["precip"] * max(0.2, 1 + elev_delta / 2500)),
                "soil_moisture": clamp(d["soil"] + elev_delta * 0.00003, 0.02, 0.5),
                "rain_3d": max(0.0, d["rain3"] * max(0.2, 1 + elev_delta / 2500)),
                "rain_7d": max(0.0, d["rain7"] * max(0.2, 1 + elev_delta / 2500)),
                "slope_deg": cell["slope_deg"], "elev_m": cell["elev_m"],
                "dist_road_km": cell["dist_road_km"],
                "hist_count": cell["hist"] * 5,
            })
            cell_scores[cell["cell_id"]] = _score(hazard, cell_ctx)["score"]

        vals = sorted(cell_scores.values())
        p90 = vals[int(0.9 * (len(vals) - 1))] if vals else 0
        frames.append({
            "date": d["date"],
            "point_score": pr["score"], "point_level": pr["level"],
            "max": max(vals) if vals else 0, "mean": round(sum(vals) / len(vals), 1) if vals else 0,
            "p90": p90, "cells": cell_scores,
        })

    peak = max(point_series, key=lambda x: x["score"]) if point_series else {"date": None, "score": 0}
    if first_red:
        msg = f"预计 {first_orange or first_red} 达到橙色风险，{first_red} 达到红色风险；峰值 {peak['score']}（{peak['date']}）。"
    elif first_orange:
        msg = f"预计 {first_orange} 达到橙色风险；未来 {days} 天未达到红色阈值，峰值 {peak['score']}（{peak['date']}）。"
    else:
        msg = f"未来 {days} 天未达到橙色阈值；峰值 {peak['score']}（{peak['date']}）。"
    if HAZARD_META.get(hazard, {}).get("static"):
        msg = f"{HAZARD_LABELS.get(hazard, hazard)}是静态风险，时间推演主要用于展示未来气象对周边环境的影响；当前系数 {point_series[0]['score']}。"

    return {
        "ok": True,
        "inputs": {k: v for k, v in inp.items() if not k.startswith("_")},
        "hazard": hazard,
        "hazard_label": HAZARD_LABELS.get(hazard, hazard),
        "days": days,
        "daily": daily,
        "point_series": point_series,
        "frames": frames,
        "first_orange": first_orange,
        "first_red": first_red,
        "peak": peak,
        "message": msg,
        "warning": warning,
        "grid_cells": len(cells),
        "model": _model_info(hazard),
        "nearest_cell": (inp.get("_cell") or {}).get("cell_id"),
        "nearest_hotspot": (inp.get("_hotspot") or {}).get("name"),
    }


def defaults(lon: float | None = None, lat: float | None = None) -> dict:
    payload = {}
    if lon is not None:
        payload["lon"] = lon
    if lat is not None:
        payload["lat"] = lat
    inp = normalize_inputs(payload)
    return {
        "ok": True,
        "inputs": {k: v for k, v in inp.items() if not k.startswith("_")},
        "nearest_cell": (inp.get("_cell") or {}).get("cell_id"),
        "nearest_hotspot": (inp.get("_hotspot") or {}).get("name"),
        "hazard_options": [{"id": k, "name": v} for k, v in HAZARD_LABELS.items()],
        "thresholds": [{"key": x["key"], "label": x["label"], "min": x["min"], "color": x["color"]} for x in DANGER_LEVELS],
    }

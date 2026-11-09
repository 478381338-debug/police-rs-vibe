"""当前综合危险指数：自然致灾因子 × 暴露修正。

与 `risk.py` 的“历史核查优先级”分开：
  - 历史核查优先级回答“先核查哪条线索”；
  - 当前危险指数回答“此时此刻自然条件 + 人口/设施暴露有多危险”。

自然致灾因子使用 `scenario._score` 的物理经验隶属度（温度、湿度、风、
短时降水、3/7 天累计雨、土壤湿度、坡度、高程、NDVI、道路可达），
覆盖火灾、洪涝、干旱、地质灾害、滑坡、泥石流 6 类；不再使用历史异常密度。
未来 14 天使用 Open-Meteo 逐日预报做同样计算，输出峰值日和峰值灾种。
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

from app import config, scenario

NATURAL_LEVELS = [
    {"key": "red", "label": "当前危险高", "min": 75},
    {"key": "orange", "label": "当前危险较高", "min": 60},
    {"key": "yellow", "label": "当前危险中等", "min": 45},
    {"key": "blue", "label": "当前危险低", "min": 0},
]


def level_for_natural(score: float) -> dict:
    for lv in NATURAL_LEVELS:
        if score >= lv["min"]:
            return lv
    return NATURAL_LEVELS[-1]

NATURAL_HAZARDS = (
    "fire_risk",
    "flood_risk",
    "drought_risk",
    "geohazard_risk",
    "landslide",
    "debris_flow",
)
HAZARD_NAMES = {
    "fire_risk": "火灾风险",
    "flood_risk": "洪涝风险",
    "drought_risk": "干旱风险",
    "geohazard_risk": "地质灾害隐患",
    "landslide": "滑坡",
    "debris_flow": "泥石流",
}
NDVI_PATH = config.DATA / "external" / "ndvi_grid.json"


def _load_ndvi() -> tuple[dict[str, dict], dict]:
    """逐格网 MODIS NDVI；缺失时返回空表，由 _context 使用 0.45 并标记回退。"""
    if not NDVI_PATH.exists():
        return {}, {"source": "未接入", "valid_count": 0, "fallback_count": 0,
                    "composite_dates": [], "fallback_ndvi": 0.45}
    try:
        d = json.loads(NDVI_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        return {}, {"source": f"读取失败: {e}", "valid_count": 0,
                    "fallback_count": 0, "composite_dates": [], "fallback_ndvi": 0.45}
    cells = {str(c.get("cell_id")): c for c in (d.get("cells") or []) if c.get("cell_id")}
    meta = {
        "source": d.get("source"),
        "provider": d.get("provider"),
        "composite_dates": d.get("composite_dates") or d.get("observation_dates") or [],
        "observation_dates": d.get("observation_dates") or [],
        "product_type": d.get("product_type") or "unknown",
        "valid_count": d.get("valid_count", 0),
        "fallback_count": d.get("fallback_count", 0),
        "fallback_ndvi": d.get("fallback_ndvi", 0.45),
        "generated_at": d.get("generated_at"),
        "note": d.get("note"),
    }
    return cells, meta


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _cell_center(feature: dict) -> tuple[float, float]:
    coords = ((feature.get("geometry") or {}).get("coordinates") or [])
    # Polygon: [[[lon, lat], ...]]
    ring = coords[0] if coords and isinstance(coords[0], list) else []
    if ring and isinstance(ring[0], list):
        pts = ring
    else:
        pts = []
    if not pts:
        return 0.0, 0.0
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _nearest_weather(lon: float, lat: float, points: list[dict]) -> dict | None:
    best = None
    best_d = 1e18
    for p in points:
        try:
            d = (float(p["lon"]) - lon) ** 2 + (float(p["lat"]) - lat) ** 2
        except Exception:
            continue
        if d < best_d:
            best_d = d
            best = p
    return best


def _load_population() -> tuple[dict[str, dict], float]:
    path = config.DATA / "external" / "population_grid.geojson"
    out: dict[str, dict] = {}
    max_pop = 1.0
    if not path.exists():
        return out, max_pop
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return out, max_pop
    for f in d.get("features", []):
        p = f.get("properties") or {}
        cid = p.get("cell_id")
        if not cid:
            continue
        pop = float(p.get("population") or 0)
        out[cid] = {"population": pop, "pop_level": p.get("pop_level", 0),
                    "pop_label": p.get("pop_label"), "pop_density": p.get("pop_density")}
        max_pop = max(max_pop, pop)
    return out, max_pop


def _context(props: dict, current: dict, daily: dict | None, rain3: float, rain7: float, ndvi: float | None = None) -> dict:
    """把格网静态地形 + 气象（当前或某日预报）拼成 scenario._score 的上下文。"""
    d = daily or {}
    return {
        "temperature": float(d.get("temp", current.get("temperature_2m", 5.0)) or 5.0),
        "humidity": float(d.get("humidity", current.get("relative_humidity_2m", 35.0)) or 35.0),
        "wind": float(d.get("wind", current.get("wind_speed_10m", 10.0)) or 10.0),
        "precipitation": float(d.get("precip", current.get("precipitation", 0.0)) or 0.0),
        "soil_moisture": float(d.get("soil", current.get("soil_moisture_0_to_10cm", 0.18)) or 0.18),
        "slope_deg": float(props.get("slope_deg") or 0),
        "elev_m": float(props.get("elev_m") or 0),
        "ndvi": 0.45 if ndvi is None else float(ndvi),
        "dist_road_km": float(props.get("dist_road_km") or 30),
        # 当前危险指数不使用历史异常密度；历史只进入“核查优先级”。
        "hist_count": 0.0,
        "rain_3d": float(rain3 or 0),
        "rain_7d": float(rain7 or 0),
    }


def _scores(ctx: dict) -> dict[str, float]:
    out = {}
    for h in NATURAL_HAZARDS:
        try:
            out[h] = float(scenario._score(h, ctx)["score"])
        except Exception:
            out[h] = 0.0
    return out


def _composite(scores: dict[str, float]) -> float:
    """多灾种耦合：最高灾种为主，次高灾种提供 25% 的叠加贡献。"""
    vals = sorted((float(v) for v in scores.values()), reverse=True)
    if not vals:
        return 0.0
    second = vals[1] if len(vals) > 1 else 0.0
    return min(100.0, vals[0] + 0.25 * second)


def _combine(hazard_score: float, exposure: float, pop_n: float) -> float:
    """危险指数 = 致灾强度 × 暴露修正；暴露因子限制在 0.60–1.00。"""
    exposure_factor = 0.60 + 0.40 * max(_clamp(exposure), _clamp(pop_n))
    return round(_clamp(hazard_score / 100.0) * exposure_factor * 100.0, 1)


def _ring_center(geom):
    """Return a simple ring centroid in lon/lat without extra dependencies."""
    if not geom:
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if gtype == "Polygon" and coords:
        ring = coords[0]
    elif gtype == "MultiPolygon" and coords:
        ring = max((r[0] for r in coords if r), key=len, default=[])
    else:
        return None
    if len(ring) < 3:
        return None
    return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))


def enrich_hotspots(hotspots: dict, grid: dict) -> dict:
    """Attach nearest grid-cell current danger to each hotspot, without mixing history."""
    cells = []
    for f in grid.get("features") or []:
        p = f.get("properties") or {}
        c = p.get("centroid") or _ring_center(f.get("geometry"))
        if c:
            cells.append((float(c[0]), float(c[1]), p))
    if not cells:
        return hotspots
    if isinstance(hotspots, dict):
        items = hotspots.get("features") or []
    else:
        items = hotspots or []
    for f in items:
        p = f.get("properties") or {} if isinstance(f, dict) and f.get("properties") else (f or {})
        c = p.get("centroid")
        if not c and isinstance(f, dict) and f.get("geometry"):
            c = _ring_center(f.get("geometry"))
        if not c:
            continue
        lon, lat = float(c[0]), float(c[1])
        nearest = min(cells, key=lambda x: (x[0] - lon) ** 2 + (x[1] - lat) ** 2)[2]
        p["current_danger"] = nearest.get("current_danger")
        p["current_danger_level"] = nearest.get("current_danger_level")
        p["current_danger_label"] = nearest.get("current_danger_label")
        p["current_natural_risk"] = nearest.get("current_danger")
        p["current_natural_level"] = nearest.get("current_danger_level")
        p["current_natural_label"] = nearest.get("current_danger_label")
        p["current_natural_hazard"] = nearest.get("current_hazard")
        p["current_natural_hazard_name"] = nearest.get("current_hazard_name")
        p["current_ndvi"] = nearest.get("current_ndvi")
        p["current_ndvi_quality"] = nearest.get("current_ndvi_quality")
        p["current_ndvi_date"] = nearest.get("current_ndvi_date")
        p["forecast_peak"] = nearest.get("forecast_peak")
        p["forecast_peak_day"] = nearest.get("forecast_peak_day")
        p["forecast_peak_hazard_name"] = nearest.get("forecast_peak_hazard_name")
        p["current_p10"] = nearest.get("current_p10")
        p["current_p50"] = nearest.get("current_p50")
        p["current_p90"] = nearest.get("current_p90")
        p["current_prob_high"] = nearest.get("current_prob_high")
        p["current_prob_elevated"] = nearest.get("current_prob_elevated")
        p["current_ensemble_n"] = nearest.get("current_ensemble_n")
        p["ensemble_source"] = nearest.get("ensemble_source")
        p["current_probability_type"] = nearest.get("current_probability_type")
        p["current_rainstorm_label"] = nearest.get("current_rainstorm_label")
        p["forecast_rainstorm_label"] = nearest.get("forecast_rainstorm_label")
        p["forecast_rainstorm_day"] = nearest.get("forecast_rainstorm_day")
        p["current_affected_population"] = nearest.get("current_affected_population")
        p["forecast_affected_population"] = nearest.get("forecast_affected_population")
        p["current_population"] = nearest.get("current_population")
        p["weather_point"] = nearest.get("current_weather_point")
        p["weather_time"] = nearest.get("current_weather_time")
        p["weather_source"] = nearest.get("current_weather_source")
    return hotspots


RAINSTORM_LEVELS = [
    {"key": "red", "label": "暴雨红色量级", "rank": 4},
    {"key": "orange", "label": "暴雨橙色量级", "rank": 3},
    {"key": "yellow", "label": "暴雨黄色量级", "rank": 2},
    {"key": "blue", "label": "暴雨蓝色量级", "rank": 1},
    {"key": "none", "label": "未达到暴雨预警量级", "rank": 0},
]
RAINSTORM_REFERENCE = {
    "source": "中国气象局暴雨预警信号公开量级（12h/6h/3h 滚动雨量）",
    "thresholds": {
        "blue": "12 小时降雨量 ≥ 50 mm",
        "yellow": "6 小时降雨量 ≥ 50 mm",
        "orange": "3 小时降雨量 ≥ 50 mm",
        "red": "3 小时降雨量 ≥ 100 mm",
    },
    "note": "仅作量级对照，不是气象部门发布的预警；正式预警以 12379 等官方渠道为准。",
}


def _rainstorm_for_day(day: dict | None) -> dict:
    d = day or {}
    p3 = float(d.get("precip_3h") or 0)
    p6 = float(d.get("precip_6h") or 0)
    p12 = float(d.get("precip_12h") or 0)
    if p3 >= 100:
        return RAINSTORM_LEVELS[0]
    if p3 >= 50:
        return RAINSTORM_LEVELS[1]
    if p6 >= 50:
        return RAINSTORM_LEVELS[2]
    if p12 >= 50:
        return RAINSTORM_LEVELS[3]
    return RAINSTORM_LEVELS[4]


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    vals = sorted(float(v) for v in values)
    return round(vals[min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))], 1)


def _member_danger_series(props: dict, current: dict, ndvi: float | None,
                          exposure: float, pop_n: float, days: list[dict]) -> list[float]:
    out = []
    for d in days:
        ctx = _context(props, current, d, float(d.get("rain3") or 0),
                       float(d.get("rain7") or 0), ndvi)
        out.append(_combine(_composite(_scores(ctx)), exposure, pop_n))
    return out


def _ensemble(ctx: dict, exposure: float, pop_n: float, seed: int, n: int = 40) -> dict:
    """Parameter-perturbation ensemble for uncertainty, not calibrated probability.

    The forecast itself is deterministic Open-Meteo; this quantifies how much the
    index moves under plausible input error and exposes P10/P50/P90 and the
    fraction of scenarios above the two operational thresholds.
    """
    rng = random.Random(seed)
    values = []
    for _ in range(n):
        c = dict(ctx)
        c["temperature"] = float(c.get("temperature", 5.0)) + rng.uniform(-1.5, 1.5)
        c["humidity"] = max(0.0, min(100.0, float(c.get("humidity", 35.0)) + rng.uniform(-8.0, 8.0)))
        c["wind"] = max(0.0, float(c.get("wind", 10.0)) * rng.uniform(0.75, 1.25))
        c["precipitation"] = max(0.0, float(c.get("precipitation", 0.0)) * rng.uniform(0.70, 1.30))
        c["rain_3d"] = max(0.0, float(c.get("rain_3d", 0.0)) * rng.uniform(0.70, 1.30))
        c["rain_7d"] = max(0.0, float(c.get("rain_7d", 0.0)) * rng.uniform(0.70, 1.30))
        c["soil_moisture"] = max(0.01, min(0.60, float(c.get("soil_moisture", 0.18)) + rng.uniform(-0.04, 0.04)))
        c["ndvi"] = max(0.0, min(1.0, float(c.get("ndvi", 0.45)) + rng.uniform(-0.05, 0.05)))
        c["slope_deg"] = max(0.0, float(c.get("slope_deg", 0.0)) * rng.uniform(0.90, 1.10))
        scores = _scores(c)
        val = _combine(_composite(scores), exposure, pop_n) * rng.uniform(0.90, 1.10)
        values.append(max(0.0, min(100.0, val)))
    values.sort()
    def pct(q):
        if not values:
            return 0.0
        return round(values[min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))], 1)
    return {
        "p10": pct(0.10), "p50": pct(0.50), "p90": pct(0.90),
        "prob_high": round(sum(v >= 75 for v in values) / len(values), 3) if values else 0.0,
        "prob_elevated": round(sum(v >= 60 for v in values) / len(values), 3) if values else 0.0,
        "n": len(values),
    }


def enrich_grid(grid: dict, weather: dict, forecast_days: int = 14) -> dict:
    """就地给 risk_grid 的每个 feature 增加 current_* / forecast_* 字段。"""
    points = [p for p in (weather.get("points") or []) if p.get("lon") is not None and p.get("lat") is not None]
    pop_map, max_pop = _load_population()
    ndvi_map, ndvi_meta = _load_ndvi()
    log_max = math.log1p(max_pop) or 1.0
    forecasts: dict[str, dict] = {}
    ensembles: dict[str, dict] = {}
    warnings: dict[str, str] = {}

    for p in points:
        name = p.get("name") or f"{p.get('lon')},{p.get('lat')}"
        cur = p.get("current") or {}
        soil = float(cur.get("soil_moisture_0_to_10cm", 0.18) or 0.18)
        try:
            daily, warning = scenario.fetch_forecast(float(p["lon"]), float(p["lat"]), forecast_days, soil)
            forecasts[name] = {"daily": daily or [], "warning": warning}
        except Exception as e:
            forecasts[name] = {"daily": [], "warning": str(e)}
        if forecasts[name].get("warning"):
            warnings[name] = forecasts[name]["warning"]
        try:
            ensembles[name] = scenario.fetch_ensemble(float(p["lon"]), float(p["lat"]), forecast_days, soil)
        except Exception as e:
            ensembles[name] = {"members": [], "member_count": 0,
                               "warning": str(e), "source": "集合预报不可用"}
        if ensembles[name].get("warning"):
            warnings[f"{name} 集合"] = ensembles[name]["warning"]

    max_current = 0.0
    max_forecast = 0.0
    for feature in grid.get("features", []):
        props = feature.get("properties") or {}
        lon, lat = _cell_center(feature)
        point = _nearest_weather(lon, lat, points)
        if not point:
            continue
        name = point.get("name") or ""
        current = point.get("current") or {}
        # 当前 3/7 天雨量：weather.json 的 daily 只有 3 天，能取多少取多少。
        wdaily = point.get("daily") or {}
        precip_sum = wdaily.get("precipitation_sum") or []
        rain3 = sum(float(x or 0) for x in precip_sum[:3])
        rain7 = sum(float(x or 0) for x in precip_sum[:7])
        ndvi_info = ndvi_map.get(str(props.get("cell_id"))) or {}
        ndvi_value = ndvi_info.get("ndvi")
        ctx = _context(props, current, None, rain3, rain7, ndvi_value)
        cur_scores = _scores(ctx)
        cur_h = max(cur_scores, key=cur_scores.get) if cur_scores else "drought_risk"
        cur_hs = cur_scores.get(cur_h, 0.0)
        cur_comp = _composite(cur_scores)

        pop_info = pop_map.get(str(props.get("cell_id")), {})
        pop = float(pop_info.get("population") or 0)
        pop_n = math.log1p(pop) / log_max if pop > 0 else 0.0
        exposure = float(props.get("exposure") or 0)
        danger = _combine(cur_comp, exposure, pop_n)
        lv = level_for_natural(danger)

        daily = (forecasts.get(name) or {}).get("daily") or []
        peak = danger
        peak_day = None
        peak_h = cur_h
        peak_hs = cur_hs
        peak_scores = cur_scores
        peak_ctx = ctx
        for d in daily:
            dctx = _context(props, current, d, float(d.get("rain3") or 0), float(d.get("rain7") or 0), ndvi_value)
            ds = _scores(dctx)
            dh = max(ds, key=ds.get) if ds else cur_h
            dhs = ds.get(dh, 0.0)
            dd = _combine(_composite(ds), exposure, pop_n)
            if dd > peak:
                peak, peak_day, peak_h, peak_hs, peak_scores = dd, d.get("date"), dh, dhs, ds
                peak_ctx = dctx

        # 真实预报集合：31 个 GFS 成员的当前/峰值分布；失败时才退回参数扰动。
        members = (ensembles.get(name) or {}).get("members") or []
        current_member_vals: list[float] = []
        peak_member_vals: list[float] = []
        if members:
            for m in members:
                vals = _member_danger_series(props, current, ndvi_value, exposure, pop_n, m.get("daily") or [])
                if vals:
                    current_member_vals.append(vals[0])
                    peak_member_vals.append(max(vals))
        if current_member_vals:
            ensemble_source = (ensembles.get(name) or {}).get("source") or "Open-Meteo GFS ensemble"
            current_p10 = _pct(current_member_vals, 0.10)
            current_p50 = _pct(current_member_vals, 0.50)
            current_p90 = _pct(current_member_vals, 0.90)
            current_prob_high = round(sum(v >= 75 for v in current_member_vals) / len(current_member_vals), 3)
            current_prob_elevated = round(sum(v >= 60 for v in current_member_vals) / len(current_member_vals), 3)
            peak_p50 = _pct(peak_member_vals, 0.50)
            peak_p90 = _pct(peak_member_vals, 0.90)
            peak_prob_high = round(sum(v >= 75 for v in peak_member_vals) / len(peak_member_vals), 3)
            peak_prob_elevated = round(sum(v >= 60 for v in peak_member_vals) / len(peak_member_vals), 3)
            ensemble_n = len(current_member_vals)
            probability_type = "Open-Meteo GFS 集合情景频率（未校准）"
        else:
            ens = _ensemble(ctx, exposure, pop_n, seed=sum(ord(ch) for ch in str(props.get("cell_id") or "")))
            ensemble_source = "参数扰动（集合预报不可用）"
            current_p10, current_p50, current_p90 = ens["p10"], ens["p50"], ens["p90"]
            current_prob_high, current_prob_elevated = ens["prob_high"], ens["prob_elevated"]
            peak_p50 = peak_p90 = None
            peak_prob_high = peak_prob_elevated = None
            ensemble_n = ens["n"]
            probability_type = "参数扰动敏感性（非校准概率）"

        current_rainstorm = _rainstorm_for_day(daily[0] if daily else None)
        forecast_rainstorm = current_rainstorm
        forecast_rainstorm_day = peak_day
        for d in daily:
            r = _rainstorm_for_day(d)
            if r["rank"] > forecast_rainstorm["rank"]:
                forecast_rainstorm, forecast_rainstorm_day = r, d.get("date")

        props.update({
            "current_danger": danger,
            "current_danger_level": lv["key"],
            "current_danger_label": lv["label"],
            "current_hazard": cur_h,
            "current_hazard_name": HAZARD_NAMES.get(cur_h, cur_h),
            "current_hazard_score": round(cur_hs, 1),
            "current_composite_score": round(cur_comp, 1),
            "current_components": {k: round(v, 1) for k, v in cur_scores.items()},
            "current_ndvi": ndvi_value,
            "current_ndvi_quality": ndvi_info.get("quality", "missing"),
            "current_ndvi_date": ndvi_info.get("date"),
            "current_ndvi_source": (ndvi_meta.get("source") or "MODIS NDVI") if ndvi_value is not None else "固定代理值 0.45（缺少 NDVI）",
            "current_exposure": round(exposure, 3),
            "current_population": int(pop),
            "current_pop_level": pop_info.get("pop_level"),
            "current_pop_label": pop_info.get("pop_label"),
            "forecast_peak": round(peak, 1),
            "forecast_peak_day": peak_day,
            "forecast_peak_hazard": peak_h,
            "forecast_peak_hazard_name": HAZARD_NAMES.get(peak_h, peak_h),
            "forecast_peak_hazard_score": round(peak_hs, 1),
            "forecast_peak_composite_score": round(_composite(peak_scores), 1),
            "forecast_peak_components": {k: round(v, 1) for k, v in peak_scores.items()},
            "current_p10": current_p10, "current_p50": current_p50, "current_p90": current_p90,
            "current_prob_high": current_prob_high, "current_prob_elevated": current_prob_elevated,
            "forecast_peak_p50": peak_p50, "forecast_peak_p90": peak_p90,
            "forecast_prob_high": peak_prob_high, "forecast_prob_elevated": peak_prob_elevated,
            "current_ensemble_n": ensemble_n,
            "ensemble_source": ensemble_source,
            "current_probability_type": probability_type,
            "current_rainstorm_level": current_rainstorm["key"],
            "current_rainstorm_label": current_rainstorm["label"],
            "forecast_rainstorm_level": forecast_rainstorm["key"],
            "forecast_rainstorm_label": forecast_rainstorm["label"],
            "forecast_rainstorm_day": forecast_rainstorm_day,
            "current_affected_population": int(round(pop * current_p90 / 100.0)),
            "forecast_affected_population": int(round(pop * (peak_p90 if peak_p90 is not None else peak) / 100.0)),
            "current_weather_time": current.get("time") or weather.get("fetched_at"),
            "current_weather_point": name,
            "current_weather_source": "Open-Meteo",
            "current_model": "自然致灾因子 × 暴露修正；集合情景不是官方预警",
            "forecast_source": "Open-Meteo 14 天逐日预报 + GFS 集合" if daily else "预报不可用，仅当前",
        })
        max_current = max(max_current, danger)
        max_forecast = max(max_forecast, peak)

    grid["current_summary"] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "max_danger": round(max_current, 1),
        "max_forecast_peak": round(max_forecast, 1),
        "weather_points": len(points),
        "forecast_days": forecast_days,
        "ndvi": ndvi_meta,
        "warnings": warnings,
        "method": "6 类自然致灾因子多灾种耦合（最高灾种 + 次高灾种 25%）× 人口暴露修正；GFS 31 成员集合给出情景分布",
        "probability": {
            "type": "gfs_ensemble_frequency",
            "member_count": max([int((e or {}).get("member_count") or 0) for e in ensembles.values()] or [0]),
            "note": "P10/P50/P90 与超阈值比例来自 Open-Meteo GFS 集合成员的场景分布；不是校准概率，也不是官方预警。集合不可用时退回参数扰动。",
        },
        "rainstorm_reference": RAINSTORM_REFERENCE,
        "ensemble_sources": {k: (v or {}).get("source") for k, v in ensembles.items()},
        "disclaimer": "当前危险指数是基于 Open-Meteo 当前/预报气象、GFS 集合、MODIS NDVI、地形与人口暴露的筛查值/情景估计，不是确定性灾害预报，也不是已发布预警；暴雨等级仅为公开预警量级对照。",
    }
    return grid

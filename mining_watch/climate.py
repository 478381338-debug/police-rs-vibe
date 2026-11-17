"""气候因素增强层。

本模块把云、降雨、降雪、土壤湿度和冻融从“背景噪声”变成三类可用信息：

1. 观测适用性：这一景/这一天到底能不能看见地表变化；
2. 天气归一化：把指数序列中由天气驱动的部分拟合并扣除；
3. 类型化解释：不同采矿类型对天气的敏感方向不同，分别给出漏报/误报解释。

边界说明（与 evidence.py 保持一致的数据诚信要求）：
- ``climate_adjusted_score`` 是线索排序分，不是概率、置信度或 F1；
- 天气模型不能替代真实标签，只报告 LOO-CV 的解释增益；
- 任何“未检出”都必须同时报告观测能力，避免把“没看见”当成“没有”；
- 窗口先验只描述观测/作业时机，不进入证据族，不作为活动概率。
"""
from __future__ import annotations

import copy
import json
import math
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_CACHE_DIR = ROOT / "data" / "climate"

# Open-Meteo 日值字段。snowfall_sum 单位 cm，snow_depth_max 单位 m。
DAILY_VARS = [
    "precipitation_sum",
    "snowfall_sum",
    "snow_depth_max",
    "cloud_cover_mean",
    "temperature_2m_mean",
    "temperature_2m_min",
    "temperature_2m_max",
    "soil_moisture_0_to_7cm_mean",
    "et0_fao_evapotranspiration",
]

# Sentinel-2 L2A SCL 类别。valid 与 yantai_2025_activity.py 的 {4,5,7} 对齐。
SCL_VALID = {4, 5, 7}
SCL_CLASS_NAMES = {
    0: "nodata",
    1: "saturated",
    2: "dark_area",
    3: "shadow",
    4: "vegetation",
    5: "bare",
    6: "water",
    7: "unclassified",
    8: "cloud_medium",
    9: "cloud_high",
    10: "thin_cloud",
    11: "snow",
}

TIER_ORDER = ("A", "B", "C", "D")

# 六类采矿画像。family_weights 与 evidence.py 的 O/S/L/D/T/C/H 七族对齐。
TYPE_PROFILES: dict[str, dict[str, Any]] = {
    "open_pit": {
        "key": "open_pit",
        "label": "露天采坑/采石",
        "primary": ["光学 ΔBSI/NDVI 季节归一化", "NBR 扰动", "高分纹理与边界"],
        "secondary": ["SAR 后向散射", "运输/夜间灯光", "用地手续"],
        "family_weights": {"O": 0.40, "S": 0.25, "L": 0.10, "D": 0.08, "T": 0.02, "C": 0.10, "H": 0.05},
        "weather": {"cloud": 0.9, "snow": 1.0, "rain": 0.5, "soil": 0.4, "freeze": 0.2},
        "primary_modality": "optical",
        "clear_min": 6,
        "max_gap_days": 45,
        "signal_threshold": 0.15,
        "confounder_penalty": 0.7,
        "confounders": ["雨后湿土反照率下降", "积雪/融雪覆盖", "云影残留", "农田翻耕"],
        "dry_months": [3, 4, 5, 9, 10, 11],
        "window_strength": 0.12,
    },
    "river_sand": {
        "key": "river_sand",
        "label": "河道采砂",
        "primary": ["水体/沙洲指数与河道形态", "采砂船/设备", "运输链路"],
        "secondary": ["光学裸沙变化", "SAR 水面/沙洲对比", "水政/许可"],
        "family_weights": {"O": 0.30, "S": 0.25, "L": 0.15, "D": 0.05, "T": 0.15, "C": 0.05, "H": 0.05},
        "weather": {"cloud": 0.8, "snow": 0.7, "rain": 0.9, "soil": 0.1, "freeze": 0.3},
        "primary_modality": "optical",
        "clear_min": 4,
        "max_gap_days": 60,
        "signal_threshold": 0.10,
        "confounder_penalty": 0.8,
        "confounders": ["洪水冲淤", "水体浑浊", "枯水期沙洲自然出露", "河道整治"],
        "dry_months": [1, 2, 3, 4, 11, 12],
        "window_strength": 0.20,
    },
    "underground": {
        "key": "underground",
        "label": "硐采/井工",
        "primary": ["InSAR 形变/相干", "井口与厂房", "运输/用电异常"],
        "secondary": ["光学设施变化", "夜间灯光", "矿权与台账"],
        "family_weights": {"O": 0.10, "S": 0.15, "L": 0.20, "D": 0.30, "T": 0.10, "C": 0.10, "H": 0.05},
        "weather": {"cloud": 0.05, "snow": 0.6, "rain": 0.7, "soil": 0.1, "freeze": 0.8},
        "primary_modality": "sar",
        "clear_min": 3,
        "max_gap_days": 60,
        "signal_threshold": 0.12,
        "confounder_penalty": 0.6,
        "confounders": ["冻融", "降水去相干", "农田灌溉", "采煤沉陷"],
        "dry_months": [],
        "window_strength": 0.0,
    },
    "stockpile": {
        "key": "stockpile",
        "label": "堆场/排土场/尾矿",
        "primary": ["堆体几何/DSM 体积", "纹理与阴影", "运输频次"],
        "secondary": ["光学裸地变化", "SAR 后向散射", "环保台账"],
        "family_weights": {"O": 0.30, "S": 0.30, "L": 0.15, "D": 0.10, "T": 0.05, "C": 0.05, "H": 0.05},
        "weather": {"cloud": 0.7, "snow": 0.9, "rain": 0.4, "soil": 0.2, "freeze": 0.1},
        "primary_modality": "hybrid",
        "clear_min": 4,
        "max_gap_days": 60,
        "signal_threshold": 0.12,
        "confounder_penalty": 0.6,
        "confounders": ["雨蚀沟", "雪覆盖", "物料正常周转"],
        "dry_months": [3, 4, 5, 9, 10, 11],
        "window_strength": 0.10,
    },
    "remining": {
        "key": "remining",
        "label": "废弃矿山复采",
        "primary": ["历史扰动底图 + 新增道路/设备", "光学裸地再扩张"],
        "secondary": ["SAR 后向散射", "运输链路", "复垦/治理台账"],
        "family_weights": {"O": 0.30, "S": 0.20, "L": 0.20, "D": 0.10, "T": 0.10, "C": 0.05, "H": 0.05},
        "weather": {"cloud": 0.8, "snow": 1.0, "rain": 0.5, "soil": 0.4, "freeze": 0.2},
        "primary_modality": "optical",
        "clear_min": 6,
        "max_gap_days": 45,
        "signal_threshold": 0.12,
        "confounder_penalty": 0.7,
        "confounders": ["季节返绿", "复垦工程", "历史裸地纹理"],
        "dry_months": [3, 4, 5, 9, 10, 11],
        "window_strength": 0.12,
    },
    "transport": {
        "key": "transport",
        "label": "运输/外运异常",
        "primary": ["卡口/称重/AIS/夜光", "道路轨迹", "时段异常"],
        "secondary": ["光学车辆/道路", "SAR 车辆检测", "矿权台账"],
        "family_weights": {"O": 0.10, "S": 0.10, "L": 0.40, "D": 0.10, "T": 0.20, "C": 0.05, "H": 0.05},
        "weather": {"cloud": 0.1, "snow": 0.4, "rain": 0.6, "soil": 0.0, "freeze": 0.3},
        "primary_modality": "logistics",
        "clear_min": 0,
        "max_gap_days": 30,
        "signal_threshold": 0.15,
        "confounder_penalty": 0.5,
        "confounders": ["雨雪停运", "节假日", "农忙"],
        "dry_months": [],
        "window_strength": 0.0,
    },
}

AOI_TYPE_HINTS = {
    "mining_belt": "open_pit",
    "A_control": "open_pit",
    "zhaoyuan": "open_pit",
    "laizhou": "open_pit",
    "penglai": "open_pit",
    "qixia": "open_pit",
    "longkou": "underground",
    "jiehe": "river_sand",
    "wulonghe": "river_sand",
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    v = _f(value) if not isinstance(value, (int, float)) else float(value)
    if not math.isfinite(v):
        return float(lo)
    return float(min(hi, max(lo, v)))


def _f(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return v if math.isfinite(v) else float("nan")


def _ramp(value: float, lo: float, hi: float) -> float:
    if not math.isfinite(value):
        return 0.0
    if hi <= lo:
        return 0.0
    return clamp((value - lo) / (hi - lo))


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _weighted_geomean(parts: Sequence[tuple[float, float]], floor: float = 0.02) -> float:
    total_w = sum(max(w, 0.0) for _, w in parts)
    if total_w <= 0:
        return 1.0
    acc = 0.0
    for q, w in parts:
        if w <= 0:
            continue
        acc += w * math.log(max(q, floor))
    return clamp(math.exp(acc / total_w))


def cap_tier(tier: str, ceiling: str) -> str:
    """返回两者中更保守的等级（A 最高，D 最低）。"""
    t = tier if tier in TIER_ORDER else "D"
    c = ceiling if ceiling in TIER_ORDER else "D"
    return t if TIER_ORDER.index(t) >= TIER_ORDER.index(c) else c


def profile_for(key: str | None) -> dict[str, Any]:
    return TYPE_PROFILES.get(key or "", TYPE_PROFILES["open_pit"])


def profile_for_aoi(aoi_key: str) -> dict[str, Any]:
    return profile_for(AOI_TYPE_HINTS.get(aoi_key, "open_pit"))


def load_daily_csv(path: str | Path) -> list[dict[str, Any]]:
    """读取 pipeline 落盘的 daily.csv（便于离线复算）。"""
    import csv

    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            row: dict[str, Any] = {}
            for key, value in raw.items():
                if key in {"date"}:
                    row[key] = value
                    continue
                if key in {"freeze_thaw", "rain_on_snow"}:
                    row[key] = str(value).strip().lower() in {"1", "true", "yes"}
                    continue
                row[key] = _f(value)
            if row.get("date"):
                rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Open-Meteo 历史日值
# ---------------------------------------------------------------------------
def fetch_open_meteo(
    lat: float,
    lon: float,
    start: str,
    end: str,
    cache_path: str | Path | None = None,
    force: bool = False,
    session: Any = None,
    timeout: int = 90,
    retries: int = 4,
    endpoint: str = ARCHIVE_URL,
) -> dict[str, Any]:
    """获取 Open-Meteo Archive 日值，带本地缓存。

    缓存命中条件：同一坐标、请求区间被缓存区间覆盖。缓存文件同时保存
    变量列表，便于以后升级时识别旧缓存。
    """
    cache_file = Path(cache_path) if cache_path else None
    if cache_file and cache_file.exists() and not force:
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            meta = cached.get("meta", {})
            if (
                abs(float(meta.get("lat", 9e9)) - lat) < 1e-4
                and abs(float(meta.get("lon", 9e9)) - lon) < 1e-4
                and str(meta.get("start", "")) <= start
                and str(meta.get("end", "")) >= end
                and set(cached.get("daily", {})) >= set(DAILY_VARS + ["time"])
            ):
                return cached
        except (OSError, ValueError, TypeError):
            pass

    if session is None:
        import requests

        session = requests.Session()
    params = {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "start_date": start,
        "end_date": end,
        "daily": ",".join(DAILY_VARS),
        "timezone": "Asia/Shanghai",
    }
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            resp = session.get(endpoint, params=params, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(8 * (attempt + 1))
                last = RuntimeError("HTTP 429")
                continue
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload.get("daily", {}).get("time"), list):
                raise ValueError("Open-Meteo 返回缺少 daily.time")
            doc = {
                "meta": {
                    "provider": "open-meteo-archive",
                    "endpoint": endpoint,
                    "lat": lat,
                    "lon": lon,
                    "start": start,
                    "end": end,
                    "timezone": "Asia/Shanghai",
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "daily_vars": list(DAILY_VARS),
                },
                "daily": payload["daily"],
            }
            if cache_file:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            return doc
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Open-Meteo 获取失败: {last}")


def _window_sum(values: Sequence[float], idx: int, window: int) -> float:
    segment = values[max(0, idx - window + 1) : idx + 1]
    good = [v for v in segment if math.isfinite(v)]
    expected = min(window, idx + 1)
    if len(good) < max(1, math.ceil(0.8 * expected)):
        return float("nan")
    return float(sum(good))


def _window_mean(values: Sequence[float], idx: int, window: int) -> float:
    segment = values[max(0, idx - window + 1) : idx + 1]
    good = [v for v in segment if math.isfinite(v)]
    expected = min(window, idx + 1)
    if len(good) < max(1, math.ceil(0.8 * expected)):
        return float("nan")
    return float(sum(good) / len(good))


def build_daily_features(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把 Open-Meteo 原始日值转成分析用特征。

    派生量：
    - precip_1d/3d/7d/30d：前期累计降水（mm）；
    - snowfall_7d：前期降雪（cm）；
    - snow_depth_cm：日最大雪深（cm，由 m 换算）；
    - dry_spell_days：连续无有效降水天数（<1 mm）；
    - freeze_thaw：日最低 <0 ℃ 且最高 >0 ℃，或均温在 ±1 ℃；
    - rain_on_snow：有降水且地面积雪。
    """
    daily = doc.get("daily", {}) if isinstance(doc, Mapping) else {}
    times = daily.get("time") or []
    if not times:
        return []

    def col(name: str) -> list[float]:
        raw = daily.get(name) or []
        return [_f(v) for v in raw]

    precip = col("precipitation_sum")
    snowfall = col("snowfall_sum")
    snow_depth_m = col("snow_depth_max")
    cloud = col("cloud_cover_mean")
    tmean = col("temperature_2m_mean")
    tmin = col("temperature_2m_min")
    tmax = col("temperature_2m_max")
    soil = col("soil_moisture_0_to_7cm_mean")
    et0 = col("et0_fao_evapotranspiration")

    rows: list[dict[str, Any]] = []
    for i, stamp in enumerate(times):
        d = _date(stamp)
        if d is None:
            continue
        snow_cm = snow_depth_m[i] * 100.0 if math.isfinite(snow_depth_m[i]) else float("nan")
        dry = 0
        j = i
        while j >= 0:
            p = precip[j] if j < len(precip) else float("nan")
            if not math.isfinite(p) or p >= 1.0:
                break
            dry += 1
            j -= 1
        freeze = False
        if math.isfinite(tmin[i]) and math.isfinite(tmax[i]) and tmin[i] < 0.0 < tmax[i]:
            freeze = True
        elif math.isfinite(tmean[i]) and abs(tmean[i]) <= 1.0:
            freeze = True
        rain_on_snow = bool(math.isfinite(precip[i]) and precip[i] >= 2.0 and math.isfinite(snow_cm) and snow_cm > 0)
        rows.append(
            {
                "date": d.isoformat(),
                "month": d.month,
                "doy": d.timetuple().tm_yday,
                "precip_1d": precip[i],
                "precip_3d": _window_sum(precip, i, 3),
                "precip_7d": _window_sum(precip, i, 7),
                "precip_30d": _window_sum(precip, i, 30),
                "snowfall_1d": snowfall[i],
                "snowfall_7d": _window_sum(snowfall, i, 7),
                "snow_depth_cm": snow_cm,
                "soil_moisture": soil[i],
                "cloud_cover": cloud[i],
                "temp_mean": tmean[i],
                "temp_min": tmin[i],
                "temp_max": tmax[i],
                "et0": et0[i],
                "freeze_thaw": freeze,
                "rain_on_snow": rain_on_snow,
                "dry_spell_days": dry,
            }
        )
    return rows


def daily_index(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["date"]): dict(row) for row in rows}


def weather_on(index: Mapping[str, Mapping[str, Any]], day: Any, max_skew_days: int = 3) -> dict[str, Any]:
    """取指定日期的天气；缺测时前后各找最多 max_skew_days 天。"""
    d = _date(day)
    if d is None:
        return {}
    key = d.isoformat()
    if key in index:
        return dict(index[key])
    for skew in range(1, max_skew_days + 1):
        for signed in (skew, -skew):
            alt = (d + timedelta(days=signed)).isoformat()
            if alt in index:
                return dict(index[alt])
    return {}


# ---------------------------------------------------------------------------
# SCL 质量与观测适用性
# ---------------------------------------------------------------------------
def scl_fractions(arr: Any) -> dict[str, float]:
    """统计 SCL 各宏观类别占比。非 0–11 的填充值不计入总数。"""
    a = np.asarray(arr)
    if a.size == 0:
        return {
            "valid": 0.0,
            "cloud": 0.0,
            "thin_cloud": 0.0,
            "shadow": 0.0,
            "snow": 0.0,
            "water": 0.0,
            "nodata": 0.0,
            "other": 1.0,
        }
    flat = a.astype(np.int64, copy=False).ravel()
    flat = flat[(flat >= 0) & (flat <= 11)]
    if flat.size == 0:
        return {k: 0.0 for k in ("valid", "cloud", "thin_cloud", "shadow", "snow", "water", "nodata", "other")}
    counts = np.bincount(flat, minlength=12)
    total = float(counts.sum())
    valid = float(sum(counts[c] for c in SCL_VALID))
    cloud = float(counts[8] + counts[9])
    thin = float(counts[10])
    shadow = float(counts[3])
    snow = float(counts[11])
    water = float(counts[6])
    nodata = float(counts[0] + counts[1])
    other = max(0.0, total - valid - cloud - thin - shadow - snow - water - nodata)
    return {
        "valid": round(valid / total, 4),
        "cloud": round(cloud / total, 4),
        "thin_cloud": round(thin / total, 4),
        "shadow": round(shadow / total, 4),
        "snow": round(snow / total, 4),
        "water": round(water / total, 4),
        "nodata": round(nodata / total, 4),
        "other": round(other / total, 4),
    }


def build_quality(
    valid_frac: float | None = None,
    scl: Mapping[str, float] | None = None,
    cloud_pct: float | None = None,
    snow_depth_cm: float | None = None,
) -> dict[str, Any]:
    """统一质量描述。有 SCL 用 SCL，无 SCL 用 valid_frac + 天气代理。"""
    if scl:
        q = {
            "valid_frac": float(scl.get("valid", valid_frac if valid_frac is not None else float("nan"))),
            "cloud_frac": float(scl.get("cloud", 0.0)) + float(scl.get("thin_cloud", 0.0)),
            "snow_frac": float(scl.get("snow", 0.0)),
            "shadow_frac": float(scl.get("shadow", 0.0)),
            "thin_cloud_frac": float(scl.get("thin_cloud", 0.0)),
            "source": "scl",
        }
    else:
        vf = _f(valid_frac)
        cp = _f(cloud_pct)
        cloud = clamp(cp / 100.0) if math.isfinite(cp) else (clamp(1.0 - vf) if math.isfinite(vf) else 0.5)
        q = {
            "valid_frac": vf if math.isfinite(vf) else float("nan"),
            "cloud_frac": cloud,
            "snow_frac": 0.0,
            "shadow_frac": 0.0,
            "thin_cloud_frac": 0.0,
            "source": "valid_frac+weather",
        }
    q["snow_depth_cm"] = _f(snow_depth_cm) if snow_depth_cm is not None else 0.0
    return q


def optical_suitability(
    profile: Mapping[str, Any], quality: Mapping[str, Any], weather: Mapping[str, Any]
) -> dict[str, Any]:
    """光学观测适用性（0–1），加权几何平均。

    雪覆盖是硬约束：可见光/短波红外被积雪遮挡时直接判 0。
    云、雨、土壤湿度和冻融按类型画像的权重衰减。
    """
    cloud = _f(quality.get("cloud_frac"))
    if quality.get("source") != "scl":
        wc = _f(weather.get("cloud_cover"))
        if math.isfinite(wc):
            cloud = clamp(wc / 100.0)
    if not math.isfinite(cloud):
        cloud = 0.5

    snow_frac = _f(quality.get("snow_frac"))
    if not math.isfinite(snow_frac):
        snow_frac = 0.0
    snow_depth = _f(weather.get("snow_depth_cm"))
    if not math.isfinite(snow_depth):
        snow_depth = _f(quality.get("snow_depth_cm"))
    if not math.isfinite(snow_depth):
        snow_depth = 0.0
    snow_signal = max(snow_frac / 0.25, snow_depth / 5.0)
    if snow_frac >= 0.5:
        q_snow = 0.0
    else:
        q_snow = 1.0 - clamp(snow_signal)

    rain7 = _f(weather.get("precip_7d"))
    soil = _f(weather.get("soil_moisture"))
    q_cloud = 0.0 if cloud >= 0.95 else 1.0 - 0.95 * _ramp(cloud, 0.10, 0.90)
    q_rain = 1.0 - 0.8 * _ramp(rain7, 5.0, 45.0)
    q_soil = 1.0 - 0.5 * _ramp(soil, 0.22, 0.45)
    q_freeze = 0.75 if weather.get("freeze_thaw") else 1.0

    weights = profile.get("weather", {})
    parts = [
        (q_cloud, float(weights.get("cloud", 0.8))),
        (q_snow, float(weights.get("snow", 1.0))),
        (q_rain, float(weights.get("rain", 0.4))),
        (q_soil, float(weights.get("soil", 0.3))),
        (q_freeze, float(weights.get("freeze", 0.2))),
    ]
    score = 0.0 if q_snow <= 0.0 or q_cloud <= 0.0 else _weighted_geomean(parts)
    return {
        "score": round(clamp(score), 4),
        "q_cloud": round(q_cloud, 4),
        "q_snow": round(q_snow, 4),
        "q_rain": round(q_rain, 4),
        "q_soil": round(q_soil, 4),
        "q_freeze": round(q_freeze, 4),
        "cloud_frac": round(cloud, 4),
        "snow_frac": round(snow_frac, 4),
        "snow_depth_cm": round(snow_depth, 3),
        "rain_7d_mm": round(rain7, 2) if math.isfinite(rain7) else None,
        "soil_moisture": round(soil, 4) if math.isfinite(soil) else None,
        "freeze_thaw": bool(weather.get("freeze_thaw")),
    }


def sar_suitability(profile: Mapping[str, Any], quality: Mapping[str, Any], weather: Mapping[str, Any]) -> dict[str, Any]:
    """SAR 相干性代理（不是真实相干系数，仅用天气做可用性约束）。"""
    rain3 = _f(weather.get("precip_3d"))
    snow_cm = _f(weather.get("snow_depth_cm"))
    q_rain = 1.0 - 0.7 * _ramp(rain3, 2.0, 25.0)
    q_snow = 1.0 - 0.5 * _ramp(snow_cm, 0.0, 5.0)
    q_freeze = 0.55 if weather.get("freeze_thaw") else 1.0
    score = _weighted_geomean([(q_rain, 0.5), (q_snow, 0.3), (q_freeze, 0.2)])
    return {
        "score": round(clamp(score, 0.15, 1.0), 4),
        "q_rain": round(q_rain, 4),
        "q_snow": round(q_snow, 4),
        "q_freeze": round(q_freeze, 4),
    }


def scene_observation(
    profile: Mapping[str, Any], weather: Mapping[str, Any], quality: Mapping[str, Any]
) -> dict[str, Any]:
    optical = optical_suitability(profile, quality, weather)
    sar = sar_suitability(profile, quality, weather)
    return {
        "type": profile.get("key"),
        "date": weather.get("date"),
        "optical_suitability": optical["score"],
        "sar_suitability": sar["score"],
        "usable_optical": optical["score"] >= 0.5,
        "usable_sar": sar["score"] >= 0.5,
        "optical_components": optical,
        "sar_components": sar,
    }


def primary_reliability(profile: Mapping[str, Any], observation: Mapping[str, Any]) -> float:
    modality = profile.get("primary_modality", "optical")
    if modality == "logistics":
        return 1.0
    if modality == "sar":
        return float(observation.get("sar_suitability", 0.5))
    if modality == "hybrid":
        return float(max(observation.get("optical_suitability", 0.0), observation.get("sar_suitability", 0.0)))
    return float(observation.get("optical_suitability", 0.0))


def detection_power(profile: Mapping[str, Any], n_usable: int, gap_days: int) -> dict[str, Any]:
    """观测能力：区分“没检出”与“没观测”。

    ``1-exp(-n/n_req)`` 表示在 n 次可用观测下至少看见一次变化的概率近似；
    gap 超过类型允许上限时按比例惩罚。
    """
    req = int(profile.get("clear_min", 6))
    n = max(0, int(n_usable))
    if req <= 0:
        base = 1.0
    else:
        base = 1.0 - math.exp(-n / req)
    max_gap = int(profile.get("max_gap_days", 45))
    gap = max(0, int(gap_days)) if math.isfinite(_f(gap_days)) else 0
    if gap <= max_gap:
        gap_factor = 1.0
    else:
        gap_factor = max(0.15, max_gap / float(gap))
    power = clamp(base * gap_factor)
    level = "adequate" if power >= 0.75 else "limited" if power >= 0.45 else "insufficient"
    return {
        "n_usable": n,
        "required": req,
        "max_gap_days": max_gap,
        "observed_gap_days": gap,
        "gap_factor": round(gap_factor, 4),
        "power": round(power, 4),
        "level": level,
    }


def window_prior(profile: Mapping[str, Any], weather: Mapping[str, Any]) -> dict[str, Any]:
    """作业/观测窗口先验（0–1），只用于解释时机，不进入证据。"""
    key = profile.get("key")
    month = weather.get("month")
    prior = 0.5
    reasons: list[str] = []
    strength = float(profile.get("window_strength", 0.1))
    dry_months = profile.get("dry_months") or []
    if month in dry_months:
        prior += strength
        reasons.append(f"{month} 月属于该类型的少雨/可观测窗口")
    precip30 = _f(weather.get("precip_30d"))
    precip7 = _f(weather.get("precip_7d"))
    snow_cm = _f(weather.get("snow_depth_cm"))
    dry_spell = _f(weather.get("dry_spell_days"))
    if key == "river_sand":
        if math.isfinite(precip30) and precip30 < 20:
            prior += 0.15
            reasons.append("30 天累计降水偏低，河道低流量")
        elif math.isfinite(precip30) and precip30 > 120:
            prior -= 0.20
            reasons.append("30 天累计降水偏高，洪水/冲淤影响")
        if math.isfinite(dry_spell) and dry_spell >= 10:
            prior += 0.08
            reasons.append(f"连续 {int(dry_spell)} 天无有效降水，沙洲易出露")
    elif key == "transport":
        if math.isfinite(precip7) and precip7 > 10:
            prior -= 0.15
            reasons.append("近期降雨抑制运输")
        if math.isfinite(snow_cm) and snow_cm > 0:
            prior -= 0.10
            reasons.append("积雪抑制运输")
    else:
        if math.isfinite(snow_cm) and snow_cm > 1:
            prior -= 0.10
            reasons.append("积雪覆盖")
        if math.isfinite(precip30) and precip30 > 180:
            prior -= 0.08
            reasons.append("持续强降水")
    prior = clamp(prior, 0.05, 0.95)
    return {
        "prior": round(prior, 4),
        "reasons": reasons,
        "note": "作业/观测窗口先验，不进入证据族，不代表活动概率",
    }


def explain_weather(
    profile: Mapping[str, Any],
    weather: Mapping[str, Any],
    quality: Mapping[str, Any],
    metrics: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """把天气条件翻成可审计的漏报/误报解释。"""
    metrics = metrics or {}
    out: list[dict[str, Any]] = []

    snow_frac = _f(quality.get("snow_frac"))
    snow_cm = _f(weather.get("snow_depth_cm"))
    snow_strength = max(
        (snow_frac / 0.05) if math.isfinite(snow_frac) else 0.0,
        (snow_cm / 2.0) if math.isfinite(snow_cm) else 0.0,
    )
    if snow_strength >= 0.25:
        out.append(
            {
                "factor": "snow",
                "strength": round(clamp(snow_strength), 4),
                "direction": "不可比",
                "text": f"积雪覆盖 {snow_frac:.1%} / 雪深 {snow_cm:.1f} cm，光学指数不可比",
                "families": ["O", "S"],
            }
        )

    cloud = _f(quality.get("cloud_frac"))
    if not math.isfinite(cloud):
        cloud = _f(weather.get("cloud_cover")) / 100.0
    cloud_strength = _ramp(cloud, 0.20, 0.80)
    if cloud_strength >= 0.25:
        out.append(
            {
                "factor": "cloud",
                "strength": round(clamp(cloud_strength), 4),
                "direction": "不可比",
                "text": f"云量/薄云 {cloud:.1%}，存在云影残留与漏观测",
                "families": ["O"],
            }
        )

    rain7 = _f(weather.get("precip_7d"))
    wet_strength = 0.8 * _ramp(rain7, 10.0, 50.0)
    if math.isfinite(_f(metrics.get("delta_bsi"))) and _f(metrics.get("delta_bsi")) >= 0.08:
        wet_strength *= 1.0
    else:
        wet_strength *= 0.6
    if wet_strength >= 0.25:
        out.append(
            {
                "factor": "rain_wet_surface",
                "strength": round(clamp(wet_strength), 4),
                "direction": "双向",
                "text": f"近 7 天降水 {rain7:.1f} mm，湿土使 BSI 下降、返干后回升，ΔBSI 可被放大或抵消",
                "families": ["O", "S"],
            }
        )

    soil = _f(weather.get("soil_moisture"))
    soil_strength = 0.6 * _ramp(soil, 0.22, 0.45)
    if soil_strength >= 0.25:
        out.append(
            {
                "factor": "soil_moisture",
                "strength": round(clamp(soil_strength), 4),
                "direction": "漏报",
                "text": f"表层土壤含水量 {soil:.3f} m³/m³ 偏高，裸地指数被压低",
                "families": ["O"],
            }
        )

    if weather.get("freeze_thaw"):
        out.append(
            {
                "factor": "freeze_thaw",
                "strength": 0.5,
                "direction": "双向",
                "text": "日冻融交替，地表反射率与 SAR 相干性均会变化",
                "families": ["O", "D"],
            }
        )

    if profile.get("key") == "river_sand":
        precip30 = _f(weather.get("precip_30d"))
        flow_strength = _ramp(precip30, 60.0, 180.0)
        if flow_strength >= 0.25:
            out.append(
                {
                    "factor": "flood_flow",
                    "strength": round(clamp(flow_strength), 4),
                    "direction": "误报",
                    "text": f"30 天累计降水 {precip30:.1f} mm，洪水冲淤/水体浑浊可造成河道假变化",
                    "families": ["O", "S"],
                }
            )
        dry_spell = _f(weather.get("dry_spell_days"))
        exposure = 0.7 * _ramp(dry_spell, 7.0, 30.0)
        if exposure >= 0.25:
            out.append(
                {
                    "factor": "dry_exposure",
                    "strength": round(clamp(exposure), 4),
                    "direction": "误报",
                    "text": f"连续 {int(dry_spell)} 天无雨，沙洲自然出露可能被误判为采砂",
                    "families": ["O", "S"],
                }
            )

    out.sort(key=lambda item: item["strength"], reverse=True)
    return out


def confounder_strength(explanations: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not explanations:
        return {"max": 0.0, "combined": 0.0}
    strengths = [clamp(_f(item.get("strength"))) for item in explanations]
    combined = 1.0 - float(np.prod([1.0 - s for s in strengths]))
    return {"max": round(max(strengths), 4), "combined": round(clamp(combined), 4)}


# ---------------------------------------------------------------------------
# 天气归一化模型
# ---------------------------------------------------------------------------
WEATHER_COVARIATES = ("precip_7d", "soil_moisture", "snow_depth_cm", "cloud_cover")


def _huber_fit(X: np.ndarray, y: np.ndarray, c: float = 1.345, max_iter: int = 30) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    for _ in range(max_iter):
        resid = y - X @ beta
        scale = 1.4826 * float(np.median(np.abs(resid - np.median(resid))))
        if not math.isfinite(scale) or scale <= 1e-9:
            break
        w = np.minimum(1.0, c * scale / np.maximum(np.abs(resid), 1e-12))
        w = np.maximum(w, 0.05)
        sw = np.sqrt(w)
        new, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
        if float(np.max(np.abs(new - beta))) < 1e-9:
            beta = new
            break
        beta = new
    return beta


def _r2(y: np.ndarray, pred: np.ndarray) -> float:
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    if ss_tot <= 0:
        return 0.0
    return 1.0 - ss_res / ss_tot


def _design_harmonic(days: np.ndarray) -> np.ndarray:
    t = days / 365.25
    return np.column_stack(
        [
            np.ones_like(t),
            np.sin(2.0 * np.pi * t),
            np.cos(2.0 * np.pi * t),
            np.sin(4.0 * np.pi * t),
            np.cos(4.0 * np.pi * t),
        ]
    )


def fit_weather_model(
    dates: Sequence[Any],
    values: Sequence[float],
    weather_index: Mapping[str, Mapping[str, Any]],
    index_name: str,
    min_n: int = 30,
    min_improvement_pct: float = 2.0,
) -> dict[str, Any]:
    """拟合“季节谐波 + 天气协变量”的指数序列模型。

    返回 LOO-CV 对比。只有天气协变量把 LOO-RMSE 降低至少
    ``min_improvement_pct`` 个百分点时才 ``applied=True``，否则原样使用观测值。
    """
    origin = date(2020, 1, 1)
    pairs: list[tuple[date, float, dict[str, Any]]] = []
    for raw_date, value in zip(dates, values):
        d = _date(raw_date)
        v = _f(value)
        if d is None or not math.isfinite(v):
            continue
        w = weather_index.get(d.isoformat())
        if not w:
            continue
        pairs.append((d, v, w))
    pairs.sort(key=lambda item: item[0])

    # 同日重复取均值，避免同一天多景造成伪样本。
    agg: dict[date, list[tuple[float, dict[str, Any]]]] = {}
    for d, v, w in pairs:
        agg.setdefault(d, []).append((v, w))
    days: list[float] = []
    y_vals: list[float] = []
    w_rows: list[dict[str, Any]] = []
    for d in sorted(agg):
        items = agg[d]
        days.append(float((d - origin).days))
        y_vals.append(float(np.mean([v for v, _ in items])))
        w_rows.append(items[0][1])

    n = len(y_vals)
    stub = {
        "index": index_name,
        "n": n,
        "applied": False,
        "reason": "样本不足或天气缺测",
        "coefficients": {},
        "weather_coefficients": {},
        "scaler": {},
        "harmonic_r2": None,
        "full_r2": None,
        "loo_rmse_harmonic": None,
        "loo_rmse_full": None,
        "improvement_pct": None,
        "residual_scale": None,
    }
    if n < max(8, min_n):
        return stub

    y = np.asarray(y_vals, dtype=float)
    t = np.asarray(days, dtype=float)
    Xh = _design_harmonic(t)

    W = np.zeros((n, len(WEATHER_COVARIATES)), dtype=float)
    for j, key in enumerate(WEATHER_COVARIATES):
        col = np.asarray([_f(row.get(key)) for row in w_rows], dtype=float)
        med = float(np.nanmedian(col)) if np.any(np.isfinite(col)) else 0.0
        col = np.where(np.isfinite(col), col, med)
        W[:, j] = col
    means = np.mean(W, axis=0)
    stds = np.std(W, axis=0)
    stds = np.where(stds < 1e-6, 1.0, stds)
    Z = (W - means) / stds
    Xf = np.column_stack([Xh, Z])

    beta_h = _huber_fit(Xh, y)
    beta_f = _huber_fit(Xf, y)
    pred_h = Xh @ beta_h
    pred_f = Xf @ beta_f

    # LOO-CV：只有这个指标能说明天气项是否真的带来样本外增益。
    loo_h = np.full(n, np.nan)
    loo_f = np.full(n, np.nan)
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        bh = _huber_fit(Xh[mask], y[mask])
        bf = _huber_fit(Xf[mask], y[mask])
        loo_h[i] = y[i] - float(Xh[i] @ bh)
        loo_f[i] = y[i] - float(Xf[i] @ bf)
    rmse_h = float(np.sqrt(np.mean(loo_h**2)))
    rmse_f = float(np.sqrt(np.mean(loo_f**2)))
    improvement = (rmse_h - rmse_f) / rmse_h * 100.0 if rmse_h > 0 else 0.0

    names = ["intercept", "sin_annual", "cos_annual", "sin_semiannual", "cos_semiannual"]
    coef = {name: float(beta_f[i]) for i, name in enumerate(names)}
    weather_coef = {key: float(beta_f[len(names) + j]) for j, key in enumerate(WEATHER_COVARIATES)}
    coef.update(weather_coef)
    scaler = {key: [float(means[j]), float(stds[j])] for j, key in enumerate(WEATHER_COVARIATES)}
    resid = y - pred_f
    scale = 1.4826 * float(np.median(np.abs(resid - np.median(resid))))

    applied = bool(improvement >= float(min_improvement_pct))
    if applied:
        reason = f"天气协变量使 LOO-RMSE 下降 {improvement:.2f}%"
    else:
        reason = f"LOO-RMSE 仅下降 {improvement:.2f}%，低于阈值 {min_improvement_pct:.1f}%，不启用校正"

    return {
        "index": index_name,
        "n": n,
        "applied": applied,
        "reason": reason,
        "coefficients": {k: round(v, 8) for k, v in coef.items()},
        "weather_coefficients": {k: round(v, 8) for k, v in weather_coef.items()},
        "scaler": {k: [round(v[0], 8), round(v[1], 8)] for k, v in scaler.items()},
        "harmonic_r2": round(_r2(y, pred_h), 4),
        "full_r2": round(_r2(y, pred_f), 4),
        "loo_rmse_harmonic": round(rmse_h, 6),
        "loo_rmse_full": round(rmse_f, 6),
        "improvement_pct": round(improvement, 2),
        "residual_scale": round(scale, 6) if math.isfinite(scale) else None,
    }


def weather_model_effect(model: Mapping[str, Any] | None, weather: Mapping[str, Any]) -> float:
    """相对于训练期平均天气的模型偏移量（与指数同单位）。"""
    if not model or not model.get("applied"):
        return 0.0
    coef = model.get("weather_coefficients") or {}
    scaler = model.get("scaler") or {}
    effect = 0.0
    for key, (mean, std) in scaler.items():
        if key not in coef:
            continue
        x = _f(weather.get(key))
        if not math.isfinite(x):
            x = float(mean)
        effect += float(coef[key]) * ((x - float(mean)) / max(float(std), 1e-9))
    return float(effect)


def adjust_value(
    model: Mapping[str, Any] | None,
    value: float,
    alarm_weather: Mapping[str, Any],
    reference_weather: Mapping[str, Any],
) -> tuple[float, float]:
    """把观测值校正到参考气候（基线同季）下，返回 (校正值, 天气偏移)。"""
    v = _f(value)
    if not math.isfinite(v):
        return float("nan"), 0.0
    shift = weather_model_effect(model, alarm_weather) - weather_model_effect(model, reference_weather)
    return float(v - shift), float(shift)


def baseline_weather_reference(
    daily_rows: Sequence[Mapping[str, Any]],
    baseline_years: Sequence[int],
    month: int,
    half_window: int = 1,
) -> dict[str, Any]:
    """基线期同季气候均值（月份循环窗口，12 月与 1 月相邻）。"""
    years = {int(y) for y in baseline_years}
    selected = []
    for row in daily_rows:
        d = _date(row.get("date"))
        if d is None or d.year not in years:
            continue
        dist = abs(d.month - month)
        dist = min(dist, 12 - dist)
        if dist <= half_window:
            selected.append(row)
    if not selected:
        selected = [row for row in daily_rows if (_date(row.get("date")) or date(1900, 1, 1)).year in years]
    keys = ("precip_7d", "precip_30d", "soil_moisture", "snow_depth_cm", "cloud_cover", "temp_mean", "snowfall_7d")
    out: dict[str, Any] = {"n_days": len(selected), "month": month}
    for key in keys:
        vals = [_f(row.get(key)) for row in selected]
        vals = [v for v in vals if math.isfinite(v)]
        out[key] = float(np.mean(vals)) if vals else float("nan")
    out["freeze_thaw"] = False
    return out


# ---------------------------------------------------------------------------
# 候选级融合
# ---------------------------------------------------------------------------
def signal_strength(profile: Mapping[str, Any], props: Mapping[str, Any], delta_bsi_adj: float) -> float:
    """变化信号强度（0–1），只用于排序，不构成证据。"""
    thr = float(profile.get("signal_threshold", 0.15))
    d = _f(delta_bsi_adj)
    if math.isfinite(d):
        z = (d - 0.02) / max(thr * 1.5 - 0.02, 1e-6)
        z_score = clamp(z)
    else:
        z_score = 0.0
    area_v = _f(props.get("area_ha"))
    area = clamp(area_v / 5.0) if math.isfinite(area_v) else 0.0
    persistent = 1.0 if props.get("persistent_bare") else 0.0
    return round(clamp(0.65 * z_score + 0.20 * area + 0.15 * persistent), 4)


def adjust_candidate(
    profile: Mapping[str, Any],
    props: Mapping[str, Any],
    *,
    alarm_weather: Mapping[str, Any],
    reference_weather: Mapping[str, Any],
    model_bsi: Mapping[str, Any] | None = None,
    model_ndvi: Mapping[str, Any] | None = None,
    quality: Mapping[str, Any] | None = None,
    known_sites: Sequence[Mapping[str, Any]] | None = None,
    n_usable: int | None = None,
    gap_days: int | None = None,
    observation_basis: str = "alarm_scene",
) -> dict[str, Any]:
    """对单个候选做气候校正、可靠性折扣与等级封顶。"""
    q = dict(quality or build_quality(
        valid_frac=_f(props.get("valid_frac")) if math.isfinite(_f(props.get("valid_frac"))) else 0.7,
        cloud_pct=_f(alarm_weather.get("cloud_cover")),
        snow_depth_cm=_f(alarm_weather.get("snow_depth_cm")),
    ))
    obs = scene_observation(profile, alarm_weather, q)
    n_clear = _f(n_usable if n_usable is not None else props.get("n_clear_dates"))
    gap_value = _f(gap_days if gap_days is not None else props.get("object_gap_days"))
    power = detection_power(
        profile,
        int(n_clear) if math.isfinite(n_clear) else 0,
        int(gap_value) if math.isfinite(gap_value) else 0,
    )
    prior = window_prior(profile, alarm_weather)
    explanations = explain_weather(
        profile,
        alarm_weather,
        q,
        {"delta_bsi": props.get("delta_bsi"), "ndvi_drop": props.get("ndvi_drop")},
    )
    conf = confounder_strength(explanations)

    raw_bsi = _f(props.get("delta_bsi"))
    adj_bsi, shift_bsi = adjust_value(model_bsi, raw_bsi, alarm_weather, reference_weather)
    raw_ndvi = _f(props.get("ndvi_drop"))
    adj_ndvi, shift_ndvi = adjust_value(model_ndvi, raw_ndvi, alarm_weather, reference_weather)

    signal_raw = signal_strength(profile, props, raw_bsi)
    signal_adj = signal_strength(profile, props, adj_bsi)
    evidence = clamp(_f(props.get("evidence_score")) / 100.0)
    reliability = primary_reliability(profile, obs)

    # 校正前：只用原始变化信号与证据分，不含任何气候折扣。
    pre_score = 100.0 * clamp(0.55 * evidence + 0.45 * signal_raw)
    # 校正后：天气归一化信号 × 主模态可靠性 − 混淆折减。
    post_score = 100.0 * clamp(0.55 * evidence + 0.45 * signal_adj) * (0.5 + 0.5 * reliability)
    post_score *= 1.0 - 0.35 * conf["combined"]
    post_score = clamp(post_score, 0.0, 100.0)

    ceiling = "A"
    if power["level"] == "insufficient":
        ceiling = "C"
    if conf["max"] >= 0.75:
        ceiling = "C"
    snow_dominant = any(item["factor"] == "snow" and item["strength"] >= 0.6 for item in explanations)
    if snow_dominant or (profile.get("primary_modality") == "optical" and obs["optical_suitability"] < 0.25):
        ceiling = "D"

    flags: list[str] = []
    for item in explanations:
        if item["strength"] >= 0.5:
            flags.append(item["factor"])
    if power["level"] == "insufficient":
        flags.append("detection_insufficient")
    if power["level"] == "limited":
        flags.append("detection_limited")
    if conf["max"] >= 0.65:
        flags.append("weather_explained")
    if profile.get("primary_modality") == "optical" and not obs["usable_optical"]:
        flags.append("no_usable_optical")

    return {
        "climate_type": profile.get("key"),
        "climate_type_label": profile.get("label"),
        "climate_primary_methods": profile.get("primary", []),
        "climate_secondary_methods": profile.get("secondary", []),
        "climate_delta_bsi_raw": round(raw_bsi, 6) if math.isfinite(raw_bsi) else None,
        "climate_delta_bsi": round(adj_bsi, 6) if math.isfinite(adj_bsi) else None,
        "climate_weather_shift_bsi": round(shift_bsi, 6),
        "climate_ndvi_drop_raw": round(raw_ndvi, 6) if math.isfinite(raw_ndvi) else None,
        "climate_ndvi_drop": round(adj_ndvi, 6) if math.isfinite(adj_ndvi) else None,
        "climate_weather_shift_ndvi": round(shift_ndvi, 6),
        "climate_signal": round(signal_adj, 4),
        "climate_signal_raw": round(signal_raw, 4),
        "climate_optical_suitability": obs["optical_suitability"],
        "climate_sar_suitability": obs["sar_suitability"],
        "climate_cloud_frac": obs["optical_components"]["cloud_frac"],
        "climate_snow_frac": obs["optical_components"]["snow_frac"],
        "climate_rain_7d_mm": obs["optical_components"]["rain_7d_mm"],
        "climate_rain_30d_mm": round(_f(alarm_weather.get("precip_30d")), 2)
        if math.isfinite(_f(alarm_weather.get("precip_30d")))
        else None,
        "climate_soil_moisture": obs["optical_components"]["soil_moisture"],
        "climate_freeze_thaw": obs["optical_components"]["freeze_thaw"],
        "climate_window_prior": prior["prior"],
        "climate_window_reasons": prior["reasons"],
        "climate_detection_power": power["power"],
        "climate_observation_level": power["level"],
        "climate_observation_required": power["required"],
        "climate_observation_basis": observation_basis,
        "climate_observation_samples": int(n_clear) if math.isfinite(n_clear) else None,
        "climate_confounder_strength": conf["max"],
        "climate_confounder_combined": conf["combined"],
        "climate_flags": flags,
        "climate_explanations": explanations,
        "climate_pre_score": round(pre_score, 2),
        "climate_adjusted_score": round(post_score, 2),
        "climate_tier_ceiling": ceiling,
        "climate_tier": cap_tier(str(props.get("evidence_tier") or "D"), ceiling),
        "climate_reliability": round(reliability, 4),
        "climate_model_bsi_applied": bool(model_bsi and model_bsi.get("applied")),
        "climate_model_ndvi_applied": bool(model_ndvi and model_ndvi.get("applied")),
        "climate_known_site_context": _known_context(props, known_sites),
        "climate_note": "排序分，不是概率；天气校正为场景级近似",
    }


def _known_context(props: Mapping[str, Any], known_sites: Sequence[Mapping[str, Any]] | None) -> dict[str, Any] | None:
    if not known_sites:
        return None
    return {
        "nearest_known": props.get("nearest_known"),
        "distance_known_m": props.get("distance_known_m"),
        "near_known": props.get("near_known"),
    }


def rank_adjusted(features: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """按气候校正分排序，并给出相对证据分的排名变化。"""
    rows = [copy.deepcopy(dict(f)) for f in features]

    def evidence_key(f: Mapping[str, Any]) -> tuple[float, float]:
        p = f.get("properties", {})
        return (float(_f(p.get("evidence_score")) or 0.0), float(_f(p.get("area_ha")) or 0.0))

    by_evidence = sorted(rows, key=evidence_key, reverse=True)
    for rank, f in enumerate(by_evidence, start=1):
        f.setdefault("properties", {})["climate_evidence_rank"] = rank

    def climate_key(f: Mapping[str, Any]) -> tuple[float, float]:
        p = f.get("properties", {})
        return (float(_f(p.get("climate_adjusted_score")) or 0.0), float(_f(p.get("area_ha")) or 0.0))

    ranked = sorted(rows, key=climate_key, reverse=True)
    for rank, f in enumerate(ranked, start=1):
        p = f.setdefault("properties", {})
        p["climate_rank"] = rank
        p["climate_rank_change"] = int(p.get("climate_evidence_rank", rank)) - rank
    return ranked


# ---------------------------------------------------------------------------
# 观测窗口汇总
# ---------------------------------------------------------------------------
def summarize_observation_windows(
    scene_rows: Sequence[Mapping[str, Any]], profile: Mapping[str, Any]
) -> dict[str, Any]:
    """按时间顺序汇总可用观测，输出盲区与探测能力。"""
    modality = profile.get("primary_modality", "optical")
    usable: list[date] = []
    months: dict[int, dict[str, int]] = {}
    for row in scene_rows:
        d = _date(row.get("date"))
        if d is None:
            continue
        obs = row.get("observation") or {}
        ok = bool(obs.get("usable_sar" if modality == "sar" else "usable_optical"))
        if modality == "logistics":
            ok = True
        months.setdefault(d.month, {"n": 0, "usable": 0})
        months[d.month]["n"] += 1
        if ok:
            usable.append(d)
            months[d.month]["usable"] += 1
    usable.sort()
    gaps = [int((b - a).days) for a, b in zip(usable, usable[1:])]
    max_gap = max(gaps) if gaps else 0
    power = detection_power(profile, len(usable), max_gap)
    return {
        "type": profile.get("key"),
        "modality": modality,
        "n_scenes": len(scene_rows),
        "n_usable": len(usable),
        "first_usable": usable[0].isoformat() if usable else None,
        "last_usable": usable[-1].isoformat() if usable else None,
        "max_gap_days": max_gap,
        "median_gap_days": int(np.median(gaps)) if gaps else None,
        "gaps_gt_limit": sum(1 for g in gaps if g > int(profile.get("max_gap_days", 45))),
        "detection": power,
        "months": {str(k): v for k, v in sorted(months.items())},
        "note": "n_usable 为天气/SAR 适用观测数，不是全部影像数",
    }
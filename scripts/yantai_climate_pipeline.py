"""烟台 2025 气候增强分析链路。

在既有 ``data/yantai_2025_v2`` 多时相候选结果之上，增加：

- Open-Meteo 历史日值（云、降雨、降雪、土壤湿度、冻融）；
- Sentinel-2 SCL 云/雪/阴影占比（可选，需要 Planetary Computer SAS）；
- 逐景观测适用性与天气归一化模型；
- 按采矿类型的探测能力、天气混淆解释与候选排序调整；
- 无标签条件下的可复算评估（已知点排名、负对照、雨量相关性、LOO-CV）。

输出统一落在 ``data/yantai_climate/``。所有分数都是排序分，不是概率。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_CA = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
if _CA and Path(_CA).exists():
    for _key in ("GDAL_CURL_CA_BUNDLE", "PROJ_CURL_CA_BUNDLE", "GDAL_HTTP_CAINFO"):
        os.environ.setdefault(_key, str(_CA))

from mining_watch import climate as cl  # noqa: E402
from mining_watch.settings import load_config  # noqa: E402

V2_DIR = ROOT / "data" / "yantai_2025_v2"
OUT_ROOT = ROOT / "data" / "yantai_climate"
CLIMATE_CACHE = ROOT / "data" / "climate"
SAS_CACHE = OUT_ROOT / ".sas_cache.json"
SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"

TARGET_YEAR = 2025
DEFAULT_BASELINE_YEARS = (2022, 2023, 2024)
DEFAULT_SCORE_THRESHOLD = 25.0

KNOWN_SITES = [
    {"id": "YT-007", "lon": 120.23434, "lat": 37.13838, "kind": "quarry"},
    {"id": "B-quarry-1", "lon": 120.30465, "lat": 37.11278, "kind": "quarry"},
    {"id": "B-quarry-2", "lon": 120.33115, "lat": 37.11626, "kind": "quarry"},
]

V2_AOIS: dict[str, dict[str, Any]] = {
    "mining_belt": {
        "label": "莱州东南—p07 采石群",
        "type": "open_pit",
        "bbox": [120.15, 37.08, 120.36, 37.21],
        "cache_dir": V2_DIR / "cache" / "mining_belt" / "boa",
        "candidates": V2_DIR / "candidates_mining_belt.corroborated.geojson",
        "known": KNOWN_SITES,
    },
    "A_control": {
        "label": "招远北部农田负对照",
        "type": "open_pit",
        "bbox": [120.06, 37.40, 120.13, 37.47],
        "cache_dir": V2_DIR / "cache" / "A_control" / "boa",
        "candidates": V2_DIR / "candidates_A_control.corroborated.geojson",
        "known": KNOWN_SITES,
    },
}

SCENARIO_PROFILE = {
    "S1": "open_pit",
    "S2": "underground",
    "S3": "open_pit",
    "S4": "transport",
    "S5": "river_sand",
    "S6": "stockpile",
    "S7": "remining",
}


# ---------------------------------------------------------------------------
# 通用 IO
# ---------------------------------------------------------------------------
def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"不可序列化: {type(value)!r}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    if value is None:
        return ""
    return value


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _bbox_center(bbox: Sequence[float]) -> tuple[float, float]:
    lon = (float(bbox[0]) + float(bbox[2])) / 2.0
    lat = (float(bbox[1]) + float(bbox[3])) / 2.0
    return lon, lat


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------------------
# 配置与区域
# ---------------------------------------------------------------------------
def climate_settings() -> dict[str, Any]:
    cfg = load_config()
    raw = {}
    try:
        import yaml

        raw = yaml.safe_load((ROOT / "config_yantai.yaml").read_text(encoding="utf-8")).get("climate", {}) or {}
    except Exception:  # noqa: BLE001
        raw = {}
    return {
        "start": str(raw.get("start", "2022-01-01")),
        "end": str(raw.get("end", "2025-12-31")),
        "baseline_years": tuple(int(y) for y in raw.get("baseline_years", DEFAULT_BASELINE_YEARS)),
        "min_samples": int(raw.get("min_samples", 30)),
        "min_improvement_pct": float(raw.get("min_improvement_pct", 2.0)),
        "cache_dir": ROOT / str(raw.get("cache_dir", "data/climate")),
        "out_dir": ROOT / str(raw.get("out_dir", "data/yantai_climate")),
        "target_year": int(raw.get("target_year", TARGET_YEAR)),
        "score_threshold": float(raw.get("score_threshold", DEFAULT_SCORE_THRESHOLD)),
        "config_aois": cfg.aois,
    }


def aoi_specs(keys: Sequence[str], settings: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for key in keys:
        key = key.strip()
        if not key:
            continue
        if key in V2_AOIS:
            spec = dict(V2_AOIS[key])
            spec["cache_dir"] = Path(spec["cache_dir"])
            spec["candidates"] = Path(spec["candidates"])
            spec["profile"] = cl.profile_for(spec.get("type"))
            specs[key] = spec
            continue
        cfg_aois = settings.get("config_aois", {})
        if key in cfg_aois:
            aoi = cfg_aois[key]
            spec = {
                "label": aoi.name,
                "type": cl.AOI_TYPE_HINTS.get(key, "open_pit"),
                "bbox": list(aoi.bbox),
                "cache_dir": None,
                "candidates": None,
                "known": KNOWN_SITES,
            }
            spec["profile"] = cl.profile_for(spec["type"])
            specs[key] = spec
            continue
        raise SystemExit(f"未知 AOI: {key}；可选 {', '.join(list(V2_AOIS) + list(cfg_aois))}")
    return specs


# ---------------------------------------------------------------------------
# 阶段 1：气候日值
# ---------------------------------------------------------------------------
def fetch_daily(aoi: str, spec: Mapping[str, Any], settings: Mapping[str, Any], force: bool = False) -> list[dict[str, Any]]:
    lon, lat = _bbox_center(spec["bbox"])
    cache_file = Path(settings["cache_dir"]) / aoi / "open_meteo.json"
    doc = cl.fetch_open_meteo(
        lat,
        lon,
        str(settings["start"]),
        str(settings["end"]),
        cache_path=cache_file,
        force=force,
    )
    rows = cl.build_daily_features(doc)
    out_dir = Path(settings["out_dir"]) / aoi
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = [
        "date", "month", "doy", "precip_1d", "precip_3d", "precip_7d", "precip_30d",
        "snowfall_1d", "snowfall_7d", "snow_depth_cm", "soil_moisture", "cloud_cover",
        "temp_mean", "temp_min", "temp_max", "et0", "freeze_thaw", "rain_on_snow", "dry_spell_days",
    ]
    write_csv(out_dir / "daily.csv", rows, fields)
    meta = {
        "aoi": aoi,
        "center": [round(lon, 5), round(lat, 5)],
        "start": settings["start"],
        "end": settings["end"],
        "n_days": len(rows),
        "provider": doc.get("meta", {}).get("provider"),
        "fetched_at": doc.get("meta", {}).get("fetched_at"),
        "cache": str(cache_file.relative_to(ROOT)) if cache_file.is_relative_to(ROOT) else str(cache_file),
    }
    write_json(out_dir / "daily_meta.json", meta)
    print(f"[fetch] {aoi}: {len(rows)} 天，中心 ({lon:.4f}, {lat:.4f})")
    return rows


# ---------------------------------------------------------------------------
# 阶段 2：逐景指数序列
# ---------------------------------------------------------------------------
def _catalog() -> list[dict[str, Any]]:
    data = read_json(V2_DIR / "catalog_2025.json", default=[])
    if not isinstance(data, list):
        raise SystemExit("catalog_2025.json 格式异常")
    return data


def read_scene_series(
    aoi: str,
    spec: Mapping[str, Any],
    force: bool = False,
) -> list[dict[str, Any]]:
    out_dir = OUT_ROOT / aoi
    out_file = out_dir / "scene_series.csv"
    cache_dir = spec.get("cache_dir")
    if not cache_dir or not Path(cache_dir).exists():
        if out_file.exists() and not force:
            print(f"[scenes] {aoi}: 无本地缓存，沿用已有 {out_file.name}")
            return _read_scene_csv(out_file)
        raise SystemExit(f"[scenes] {aoi}: 缺少缓存目录 {cache_dir}，无法反演逐景序列")

    items = _catalog()
    paths = [Path(cache_dir) / f"{item['id']}.npz" for item in items]
    available = [(item, p) for item, p in zip(items, paths) if p.exists()]
    if not available:
        raise SystemExit(f"[scenes] {aoi}: 缓存 npz 为空")

    shape = None
    counts = None
    for _, path in available:
        with np.load(path) as npz:
            valid = npz["valid"]
            if shape is None:
                shape = valid.shape
                counts = np.zeros(shape, dtype=np.uint16)
            counts += valid.astype(np.uint16)
    assert counts is not None and shape is not None
    min_count = max(6, int(round(0.70 * len(available))))
    stable = counts >= min_count

    rows: list[dict[str, Any]] = []
    for item, path in available:
        with np.load(path) as npz:
            valid = npz["valid"]
            mask = valid & stable
            n = int(mask.sum())
            row: dict[str, Any] = {
                "date": item.get("date"),
                "item_id": item.get("id"),
                "role": item.get("role"),
                "cloud": item.get("cloud"),
                "valid_frac": round(float(valid.mean()), 4),
                "stable_valid_frac": round(float(mask.mean()), 4),
                "n_stable_px": n,
            }
            for name in ("bsi", "ndvi", "nbr", "ndwi"):
                arr = npz[name]
                row[name] = round(float(np.nanmedian(arr[mask])), 6) if n >= 50 else None
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("date")))
    fields = ["date", "item_id", "role", "cloud", "valid_frac", "stable_valid_frac", "n_stable_px", "bsi", "ndvi", "nbr", "ndwi"]
    write_csv(out_file, rows, fields)
    write_json(
        out_dir / "scene_series_meta.json",
        {
            "aoi": aoi,
            "n_scenes": len(rows),
            "grid_shape": list(shape),
            "stable_pixels": int(stable.sum()),
            "stable_rule": f"valid in >= {min_count}/{len(available)} scenes",
            "note": "AOI 稳定像元中值；用于场景级天气归一化，不替代像元级变化检测",
        },
    )
    print(f"[scenes] {aoi}: {len(rows)} 景，稳定像元 {int(stable.sum())}")
    return rows


def _read_scene_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            row: dict[str, Any] = {}
            for key, value in raw.items():
                if key in {"date", "item_id", "role"}:
                    row[key] = value
                elif value == "":
                    row[key] = None
                else:
                    try:
                        row[key] = float(value)
                    except ValueError:
                        row[key] = value
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 阶段 3：SCL 云/雪占比（可选）
# ---------------------------------------------------------------------------
def _load_sas_cache() -> dict[str, Any]:
    data = read_json(SAS_CACHE, default={})
    return data if isinstance(data, dict) else {}


def _save_sas_cache(cache: Mapping[str, Any]) -> None:
    write_json(SAS_CACHE, cache)


def signed(href: str) -> str:
    cache = _load_sas_cache()
    rec = cache.get(href)
    if isinstance(rec, dict) and time.time() - float(rec.get("t", 0)) < 3000:
        return str(rec["u"])
    import requests

    last: Exception | None = None
    for attempt in range(6):
        try:
            resp = requests.get(SIGN_URL, params={"href": href}, timeout=90)
            if resp.status_code == 429:
                time.sleep(20 * (attempt + 1))
                last = RuntimeError("HTTP 429")
                continue
            resp.raise_for_status()
            url = resp.json()["href"]
            cache[href] = {"u": url, "t": time.time()}
            _save_sas_cache(cache)
            time.sleep(1.0)
            return str(url)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"SAS 签名失败: {last}")


def scl_stats_for_scene(item: Mapping[str, Any], bbox: Sequence[float], out_dir: Path, force: bool = False) -> dict[str, Any] | None:
    scene_id = str(item.get("id"))
    cache_file = out_dir / "scl" / f"{scene_id}.json"
    if cache_file.exists() and not force:
        data = read_json(cache_file, default=None)
        if isinstance(data, dict) and data.get("scl"):
            return data
    href = (item.get("assets") or {}).get("SCL")
    if not href:
        return None
    try:
        import rasterio
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds as window_from_bounds

        url = signed(str(href))
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            with rasterio.open(url) as src:
                bounds = transform_bounds("EPSG:4326", src.crs, *[float(v) for v in bbox])
                win = window_from_bounds(bounds[0], bounds[1], bounds[2], bounds[3], transform=src.transform)
                win = win.round_offsets().round_lengths()
                arr = src.read(1, window=win, boundless=True, fill_value=0)
        stats = cl.scl_fractions(arr)
        payload = {
            "item_id": scene_id,
            "date": item.get("date"),
            "cloud_meta": item.get("cloud"),
            "window": [float(win.col_off), float(win.row_off), float(win.width), float(win.height)],
            "scl": stats,
        }
        write_json(cache_file, payload)
        return payload
    except Exception as exc:  # noqa: BLE001
        print(f"[scl] {scene_id}: 失败 {exc}")
        return None


def fetch_scl(
    aoi: str,
    spec: Mapping[str, Any],
    settings: Mapping[str, Any],
    force: bool = False,
) -> dict[str, dict[str, Any]]:
    out_dir = Path(settings["out_dir"]) / aoi
    items = _catalog()
    stats: dict[str, dict[str, Any]] = {}
    failures = 0
    for item in items:
        payload = scl_stats_for_scene(item, spec["bbox"], out_dir, force=force)
        if payload:
            stats[str(item["id"])] = payload
        else:
            failures += 1
    write_json(
        out_dir / "scl_meta.json",
        {
            "aoi": aoi,
            "n_items": len(items),
            "n_ok": len(stats),
            "n_failed": failures,
            "source": "Planetary Computer SAS + Sentinel-2 L2A SCL",
            "note": "SCL 占比用于逐景云/雪/阴影质量；失败时回退 valid_frac + 天气代理",
        },
    )
    print(f"[scl] {aoi}: {len(stats)}/{len(items)} 景成功，{failures} 景回退")
    return stats


# ---------------------------------------------------------------------------
# 阶段 4：逐景天气与天气归一化
# ---------------------------------------------------------------------------
SCENE_WX_FIELDS = [
    "date", "item_id", "role", "cloud", "valid_frac", "stable_valid_frac", "bsi", "ndvi", "nbr", "ndwi",
    "optical_suitability", "sar_suitability", "usable_optical", "usable_sar",
    "cloud_frac", "snow_frac", "precip_7d", "precip_30d", "snow_depth_cm", "soil_moisture",
    "cloud_cover", "temp_mean", "freeze_thaw", "dry_spell_days", "window_prior",
    "bsi_weather_effect", "bsi_adj", "ndvi_weather_effect", "ndvi_adj",
]


def build_scene_weather(
    aoi: str,
    spec: Mapping[str, Any],
    scene_rows: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    scl_stats: Mapping[str, Mapping[str, Any]],
    settings: Mapping[str, Any],
    force: bool = False,
) -> list[dict[str, Any]]:
    out_file = Path(settings["out_dir"]) / aoi / "scene_weather.csv"
    if out_file.exists() and not force:
        return _read_scene_csv(out_file)
    idx = cl.daily_index(daily_rows)
    profile = spec["profile"]
    rows: list[dict[str, Any]] = []
    for scene in scene_rows:
        w = cl.weather_on(idx, scene.get("date"))
        if not w:
            w = {"date": scene.get("date"), "month": cl._date(scene.get("date")).month if cl._date(scene.get("date")) else None}
        scl = (scl_stats.get(str(scene.get("item_id"))) or {}).get("scl")
        q = cl.build_quality(
            valid_frac=scene.get("valid_frac"),
            scl=scl,
            cloud_pct=w.get("cloud_cover"),
            snow_depth_cm=w.get("snow_depth_cm"),
        )
        obs = cl.scene_observation(profile, w, q)
        prior = cl.window_prior(profile, w)
        row = dict(scene)
        row.update(
            {
                "optical_suitability": obs["optical_suitability"],
                "sar_suitability": obs["sar_suitability"],
                "usable_optical": obs["usable_optical"],
                "usable_sar": obs["usable_sar"],
                "cloud_frac": obs["optical_components"]["cloud_frac"],
                "snow_frac": obs["optical_components"]["snow_frac"],
                "precip_7d": w.get("precip_7d"),
                "precip_30d": w.get("precip_30d"),
                "snow_depth_cm": w.get("snow_depth_cm"),
                "soil_moisture": w.get("soil_moisture"),
                "cloud_cover": w.get("cloud_cover"),
                "temp_mean": w.get("temp_mean"),
                "freeze_thaw": bool(w.get("freeze_thaw")),
                "dry_spell_days": w.get("dry_spell_days"),
                "window_prior": prior["prior"],
            }
        )
        rows.append(row)
    write_csv(out_file, rows, SCENE_WX_FIELDS)
    return rows


def fit_models(
    aoi: str,
    scene_rows: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    out_file = Path(settings["out_dir"]) / aoi / "model.json"
    if out_file.exists() and not force:
        data = read_json(out_file, default=None)
        if isinstance(data, dict) and data.get("models"):
            return data
    idx = cl.daily_index(daily_rows)
    dates = [row.get("date") for row in scene_rows]
    models = {}
    for name in ("bsi", "ndvi"):
        values = [row.get(name) for row in scene_rows]
        models[name] = cl.fit_weather_model(
            dates,
            values,
            idx,
            name,
            min_n=int(settings.get("min_samples", 30)),
            min_improvement_pct=float(settings.get("min_improvement_pct", 2.0)),
        )
    payload = {
        "aoi": aoi,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "covariates": list(cl.WEATHER_COVARIATES),
        "design": "annual+semiannual harmonics + standardized weather covariates, Huber IRLS",
        "validation": "leave-one-out CV; applied only when LOO-RMSE improves >= threshold",
        "models": models,
        "note": "场景级 AOI 中值序列模型；候选校正为 first_alarm 当日相对基线同季均值的近似",
    }
    write_json(out_file, payload)
    for name, model in models.items():
        print(f"[model] {aoi}/{name}: n={model['n']} applied={model['applied']} improvement={model['improvement_pct']}%")
    return payload


def apply_models_to_scenes(
    scene_rows: Sequence[Mapping[str, Any]],
    model_payload: Mapping[str, Any],
    daily_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    idx = cl.daily_index(daily_rows)
    models = model_payload.get("models", {})
    out: list[dict[str, Any]] = []
    for row in scene_rows:
        item = dict(row)
        w = cl.weather_on(idx, item.get("date"))
        for name in ("bsi", "ndvi"):
            model = models.get(name)
            effect = cl.weather_model_effect(model, w)
            value = cl._f(item.get(name))
            item[f"{name}_weather_effect"] = round(effect, 6)
            item[f"{name}_adj"] = round(value - effect, 6) if math.isfinite(value) else None
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 阶段 5：候选解释与排序
# ---------------------------------------------------------------------------
def candidate_profile(aoi_profile: Mapping[str, Any], props: Mapping[str, Any]) -> Mapping[str, Any]:
    if aoi_profile.get("key") in {"river_sand", "underground"}:
        return aoi_profile
    scen = (props.get("scenario") or {}).get("primary")
    return cl.profile_for(SCENARIO_PROFILE.get(str(scen), str(aoi_profile.get("key"))))


def load_candidates(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = spec.get("candidates")
    if not path or not Path(path).exists():
        return []
    data = read_json(Path(path), default={})
    feats = data.get("features", []) if isinstance(data, dict) else []
    return [f for f in feats if isinstance(f, dict)]


def _scene_by_date(scene_rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in scene_rows:
        d = row.get("date")
        if d:
            out[str(d)] = str(row.get("item_id"))
    return out


def _finite_mean(values: Sequence[Any]) -> float:
    vals = [cl._f(v) for v in values]
    vals = [v for v in vals if math.isfinite(v)]
    return float(np.mean(vals)) if vals else float("nan")


def target_window_summary(
    spec: Mapping[str, Any],
    scene_rows: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    scl_stats: Mapping[str, Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> dict[str, Any] | None:
    """无告警日期的静态图层（persistent_bare）用目标年夏季聚合观测做气候画像。"""
    year = int(settings.get("target_year", TARGET_YEAR))
    idx = cl.daily_index(daily_rows)
    target_rows = [row for row in scene_rows if (cl._date(row.get("date")) or date(1900, 1, 1)).year == year]
    summer = [row for row in target_rows if (cl._date(row.get("date")) or date(1900, 1, 1)).month in (6, 7, 8)]
    use_rows = summer or target_rows
    if not use_rows:
        return None
    wx_rows = [cl.weather_on(idx, row.get("date")) for row in use_rows]
    wx_rows = [w for w in wx_rows if w]
    weather: dict[str, Any] = {
        "date": None,
        "month": None,
        "freeze_thaw": False,
    }
    freeze_frac = (
        sum(1 for w in wx_rows if w.get("freeze_thaw")) / len(wx_rows) if wx_rows else 0.0
    )
    weather["freeze_thaw"] = freeze_frac >= 0.25
    for key in ("precip_7d", "precip_30d", "snow_depth_cm", "soil_moisture", "cloud_cover", "temp_mean", "dry_spell_days"):
        weather[key] = _finite_mean([w.get(key) for w in wx_rows])

    scl_values: dict[str, list[float]] = {}
    valid_values: list[float] = []
    usable_dates: list[date] = []
    for row in use_rows:
        valid_values.append(cl._f(row.get("valid_frac")))
        scl = (scl_stats.get(str(row.get("item_id"))) or {}).get("scl")
        if isinstance(scl, Mapping):
            for key, value in scl.items():
                scl_values.setdefault(key, []).append(cl._f(value))
        w = cl.weather_on(idx, row.get("date"))
        q = cl.build_quality(
            valid_frac=row.get("valid_frac"),
            scl=scl,
            cloud_pct=(w or {}).get("cloud_cover"),
            snow_depth_cm=(w or {}).get("snow_depth_cm"),
        )
        if cl.scene_observation(spec["profile"], w or {}, q)["usable_optical"]:
            d = cl._date(row.get("date"))
            if d:
                usable_dates.append(d)
    scl_mean = {key: _finite_mean(vals) for key, vals in scl_values.items()} or None
    valid_mean = _finite_mean(valid_values)
    q = cl.build_quality(
        valid_frac=valid_mean if math.isfinite(valid_mean) else None,
        scl=scl_mean,
        cloud_pct=weather.get("cloud_cover"),
        snow_depth_cm=weather.get("snow_depth_cm"),
    )
    usable_dates.sort()
    gaps = [(b - a).days for a, b in zip(usable_dates, usable_dates[1:])]
    return {
        "weather": weather,
        "quality": q,
        "scl": scl_mean,
        "n_usable": len(usable_dates),
        "gap_days": max(gaps) if gaps else 0,
        "basis": f"target_year_{'summer' if summer else 'all'}_aggregate",
        "n_scenes": len(use_rows),
    }


def explain_candidates(
    aoi: str,
    spec: Mapping[str, Any],
    features: Sequence[Mapping[str, Any]],
    scene_rows: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    model_payload: Mapping[str, Any],
    scl_stats: Mapping[str, Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> list[dict[str, Any]]:
    idx = cl.daily_index(daily_rows)
    scene_map = _scene_by_date(scene_rows)
    models = model_payload.get("models", {})
    baseline_years = settings.get("baseline_years", DEFAULT_BASELINE_YEARS)
    ref_cache: dict[int, dict[str, Any]] = {}
    static_window = target_window_summary(spec, scene_rows, daily_rows, scl_stats, settings)
    enriched: list[dict[str, Any]] = []

    for feature in features:
        feat = json.loads(json.dumps(feature, ensure_ascii=False))
        props = feat.setdefault("properties", {})
        profile = candidate_profile(spec["profile"], props)
        alarm_day = props.get("first_alarm") or props.get("t1") or props.get("last_alarm")
        aggregate = static_window if not alarm_day else None
        if aggregate:
            w = aggregate["weather"]
            q = aggregate["quality"]
            month = None
            ref: dict[str, Any] = {}
            scl = aggregate["scl"]
        else:
            w = cl.weather_on(idx, alarm_day)
            d = cl._date(alarm_day)
            if not w:
                w = {"date": alarm_day, "month": d.month if d else None, "freeze_thaw": False}
            month = d.month if d else int(w.get("month") or 1)
            if month not in ref_cache:
                ref_cache[month] = cl.baseline_weather_reference(daily_rows, baseline_years, month)
            ref = ref_cache[month]
            item_id = scene_map.get(str(alarm_day))
            scl = (scl_stats.get(item_id) or {}).get("scl") if item_id else None
            q = cl.build_quality(
                valid_frac=props.get("valid_frac"),
                scl=scl,
                cloud_pct=w.get("cloud_cover"),
                snow_depth_cm=w.get("snow_depth_cm"),
            )
        adjusted = cl.adjust_candidate(
            profile,
            props,
            alarm_weather=w,
            reference_weather=ref,
            model_bsi=models.get("bsi"),
            model_ndvi=models.get("ndvi"),
            quality=q,
            known_sites=spec.get("known"),
            n_usable=aggregate["n_usable"] if aggregate else None,
            gap_days=aggregate["gap_days"] if aggregate else None,
            observation_basis=aggregate["basis"] if aggregate else "alarm_scene",
        )
        props.update(adjusted)
        props["climate_alarm_date"] = alarm_day
        props["climate_reference_month"] = month
        props["climate_reference_days"] = ref.get("n_days")
        props["climate_scene_scl_available"] = bool(scl)
        props["climate_aggregate_scenes"] = aggregate["n_scenes"] if aggregate else None
        enriched.append(feat)

    ranked = cl.rank_adjusted(enriched)
    out_dir = Path(settings["out_dir"]) / aoi
    out_dir.mkdir(parents=True, exist_ok=True)
    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "aoi": aoi,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "score_note": "climate_adjusted_score 是排序分，不是概率；已知点仅用于评估，不参与排序",
            "profile_matrix": {
                k: {
                    "label": v["label"],
                    "primary": v["primary"],
                    "secondary": v["secondary"],
                    "primary_modality": v["primary_modality"],
                    "clear_min": v["clear_min"],
                    "max_gap_days": v["max_gap_days"],
                }
                for k, v in cl.TYPE_PROFILES.items()
            },
        },
        "features": ranked,
    }
    write_json(out_dir / "candidates_climate.geojson", geojson)

    csv_fields = [
        "climate_rank", "climate_rank_change", "climate_evidence_rank", "layer", "scenario_label",
        "area_ha", "centroid_lonlat", "climate_alarm_date", "climate_type", "climate_type_label",
        "evidence_score", "climate_pre_score", "climate_adjusted_score", "climate_tier", "climate_tier_ceiling",
        "climate_delta_bsi_raw", "climate_delta_bsi", "climate_weather_shift_bsi",
        "climate_signal_raw", "climate_signal", "climate_optical_suitability", "climate_sar_suitability",
        "climate_detection_power", "climate_observation_level", "climate_confounder_strength",
        "climate_cloud_frac", "climate_snow_frac", "climate_rain_7d_mm", "climate_rain_30d_mm",
        "climate_soil_moisture", "climate_freeze_thaw", "climate_window_prior", "climate_flags",
        "climate_explanations", "climate_model_bsi_applied", "nearest_known", "distance_known_m",
    ]
    csv_rows = []
    for feat in ranked:
        p = feat.get("properties", {})
        row = {key: p.get(key) for key in csv_fields}
        row["scenario_label"] = (p.get("scenario") or {}).get("label")
        csv_rows.append(row)
    write_csv(out_dir / "climate_candidates.csv", csv_rows, csv_fields)

    # 观测窗口只使用目标年，避免把基线期当成 2025 的观测机会。
    target_rows = []
    for row in scene_rows:
        d = cl._date(row.get("date"))
        if d and d.year == int(settings.get("target_year", TARGET_YEAR)):
            w = cl.weather_on(idx, row.get("date"))
            scl = (scl_stats.get(str(row.get("item_id"))) or {}).get("scl")
            q = cl.build_quality(
                valid_frac=row.get("valid_frac"),
                scl=scl,
                cloud_pct=w.get("cloud_cover"),
                snow_depth_cm=w.get("snow_depth_cm"),
            )
            target_rows.append({**row, "observation": cl.scene_observation(spec["profile"], w, q)})
    windows = cl.summarize_observation_windows(target_rows, spec["profile"])
    windows["aoi"] = aoi
    windows["target_year"] = int(settings.get("target_year", TARGET_YEAR))
    write_json(out_dir / "observation_windows.json", windows)
    print(f"[explain] {aoi}: {len(ranked)} 个候选，观测能力 {windows['detection']['level']}")
    return ranked


# ---------------------------------------------------------------------------
# 阶段 6：无标签评估
# ---------------------------------------------------------------------------
def _rank(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    order = np.argsort(arr, kind="mergesort")
    ranks = np.empty(len(arr), dtype=float)
    i = 0
    while i < len(arr):
        j = i + 1
        while j < len(arr) and arr[order[j]] == arr[order[i]]:
            j += 1
        avg = (i + j - 1) / 2.0 + 1.0
        ranks[order[i:j]] = avg
        i = j
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    rx = _rank(x)
    ry = _rank(y)
    if float(np.std(rx)) <= 0 or float(np.std(ry)) <= 0:
        return None
    return round(float(np.corrcoef(rx, ry)[0, 1]), 4)


def evaluate_aoi(
    aoi: str,
    spec: Mapping[str, Any],
    features: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    model_payload: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    rows = [dict(f.get("properties", {})) for f in features]
    target_year = int(settings.get("target_year", TARGET_YEAR))
    threshold = float(settings.get("score_threshold", DEFAULT_SCORE_THRESHOLD))

    # 1) 已知点：最近候选的排名变化。已知点只评估，不排序。
    known_rows = []
    for site in spec.get("known") or []:
        scored = []
        for p in rows:
            lonlat = p.get("centroid_lonlat") or []
            if len(lonlat) != 2:
                continue
            dist = haversine_m(float(lonlat[0]), float(lonlat[1]), float(site["lon"]), float(site["lat"]))
            scored.append((dist, p))
        if not scored:
            continue
        scored.sort(key=lambda item: item[0])
        dist, nearest = scored[0]
        if dist > 3000:
            continue
        known_rows.append(
            {
                "site": site["id"],
                "kind": site.get("kind"),
                "nearest_candidate_layer": nearest.get("layer"),
                "distance_m": round(dist, 1),
                "evidence_rank": nearest.get("climate_evidence_rank"),
                "climate_rank": nearest.get("climate_rank"),
                "rank_change": nearest.get("climate_rank_change"),
                "delta_bsi_raw": nearest.get("climate_delta_bsi_raw"),
                "delta_bsi_adj": nearest.get("climate_delta_bsi"),
                "flags": nearest.get("climate_flags"),
            }
        )
    if known_rows:
        known_summary = {
            "n_sites_matched": len(known_rows),
            "mean_rank_change": round(float(np.mean([r["rank_change"] or 0 for r in known_rows])), 2),
            "n_improved": sum(1 for r in known_rows if (r["rank_change"] or 0) > 0),
            "n_worse": sum(1 for r in known_rows if (r["rank_change"] or 0) < 0),
            "rows": known_rows,
            "note": "只反映最近候选的排序变化，不构成命中/精度",
        }
    else:
        known_summary = {"n_sites_matched": 0, "rows": [], "note": "3 km 内无候选"}

    # 2) 负对照：应尽量减少天气可解释的候选。
    flagged = [p for p in rows if (p.get("climate_confounder_strength") or 0) >= 0.65]
    no_optical = [p for p in rows if "no_usable_optical" in (p.get("climate_flags") or [])]
    control = {
        "n_candidates": len(rows),
        "area_ha": round(sum(float(p.get("area_ha") or 0) for p in rows), 2),
        "n_weather_explained": len(flagged),
        "area_weather_explained_ha": round(sum(float(p.get("area_ha") or 0) for p in flagged), 2),
        "n_no_usable_optical": len(no_optical),
        "mean_pre_score": round(float(np.mean([p.get("climate_pre_score") or 0 for p in rows])), 2) if rows else 0,
        "mean_adjusted_score": round(float(np.mean([p.get("climate_adjusted_score") or 0 for p in rows])), 2) if rows else 0,
        "n_below_threshold_after": sum(1 for p in rows if (p.get("climate_adjusted_score") or 0) < threshold),
        "note": "负对照不应出现采矿命中；这里只统计被天气解释/降级的候选",
    }

    # 3) 月度候选数与降雨的相关性：校正后应减少“雨多候选多”的天气驱动。
    months = [f"{target_year}-{m:02d}" for m in range(1, 13)]
    rain = {m: 0.0 for m in months}
    for row in daily_rows:
        d = cl._date(row.get("date"))
        if d and d.year == target_year:
            key = f"{d.year}-{d.month:02d}"
            rain[key] += float(cl._f(row.get("precip_1d")) or 0.0)
    before_counts = {m: 0 for m in months}
    after_counts = {m: 0 for m in months}
    for p in rows:
        d = cl._date(p.get("climate_alarm_date") or p.get("first_alarm"))
        if not d or d.year != target_year:
            continue
        key = f"{d.year}-{d.month:02d}"
        if key not in before_counts:
            continue
        before_counts[key] += 1
        if (p.get("climate_adjusted_score") or 0) >= threshold and "weather_explained" not in (p.get("climate_flags") or []):
            after_counts[key] += 1
    rho_before = spearman([rain[m] for m in months], [before_counts[m] for m in months])
    rho_after = spearman([rain[m] for m in months], [after_counts[m] for m in months])
    rain_corr = {
        "monthly_rain_mm": {m: round(rain[m], 1) for m in months},
        "candidate_counts_before": before_counts,
        "candidate_counts_after": after_counts,
        "spearman_before": rho_before,
        "spearman_after": rho_after,
        "delta": round((rho_after - rho_before), 4) if rho_before is not None and rho_after is not None else None,
        "threshold": threshold,
        "note": "12 个月样本；相关性下降只说明天气驱动减少，不等于精度提升",
    }

    models = model_payload.get("models", {})
    return {
        "aoi": aoi,
        "type": spec["profile"].get("key"),
        "n_candidates": len(rows),
        "known_sites": known_summary,
        "negative_control": control,
        "rain_correlation": rain_corr,
        "models": {
            name: {
                "n": m.get("n"),
                "applied": m.get("applied"),
                "harmonic_r2": m.get("harmonic_r2"),
                "full_r2": m.get("full_r2"),
                "loo_rmse_harmonic": m.get("loo_rmse_harmonic"),
                "loo_rmse_full": m.get("loo_rmse_full"),
                "improvement_pct": m.get("improvement_pct"),
                "reason": m.get("reason"),
            }
            for name, m in models.items()
        },
        "score_distribution": {
            "pre_max": round(max([p.get("climate_pre_score") or 0 for p in rows], default=0), 2),
            "adjusted_max": round(max([p.get("climate_adjusted_score") or 0 for p in rows], default=0), 2),
            "adjusted_ge_threshold": sum(1 for p in rows if (p.get("climate_adjusted_score") or 0) >= threshold),
        },
        "note": "无独立标签；不报告精度/F1，只报告排序、负对照和可观测性变化",
    }


# ---------------------------------------------------------------------------
# 阶段 7：汇总报告
# ---------------------------------------------------------------------------
def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _target_weather_summary(
    daily_rows: Sequence[Mapping[str, Any]],
    scene_rows: Sequence[Mapping[str, Any]],
    target_year: int,
) -> dict[str, Any]:
    def rounded(value: float, digits: int = 1) -> float | None:
        return round(value, digits) if math.isfinite(value) else None

    monthly: dict[str, list[Mapping[str, Any]]] = {}
    for row in daily_rows:
        d = cl._date(row.get("date"))
        if d and d.year == target_year:
            monthly.setdefault(f"{d.year}-{d.month:02d}", []).append(row)
    months = []
    for month in sorted(monthly):
        items = monthly[month]
        months.append(
            {
                "month": month,
                "precip_mm": rounded(sum(cl._f(r.get("precip_1d")) for r in items if math.isfinite(cl._f(r.get("precip_1d"))))),
                "snowfall_cm": rounded(sum(cl._f(r.get("snowfall_1d")) for r in items if math.isfinite(cl._f(r.get("snowfall_1d"))))),
                "snow_depth_max_cm": rounded(max([cl._f(r.get("snow_depth_cm")) for r in items if math.isfinite(cl._f(r.get("snow_depth_cm")))], default=0.0)),
                "cloud_cover_mean_pct": rounded(_finite_mean([r.get("cloud_cover") for r in items])),
                "temp_mean_c": rounded(_finite_mean([r.get("temp_mean") for r in items])),
            }
        )
    scl_rows = [r for r in scene_rows if r.get("cloud_frac") is not None]
    return {
        "monthly": months,
        "scl": {
            "n_scenes": len(scl_rows),
            "cloud_frac_mean": rounded(_finite_mean([r.get("cloud_frac") for r in scl_rows]), 4) if scl_rows else None,
            "snow_frac_mean": rounded(_finite_mean([r.get("snow_frac") for r in scl_rows]), 4) if scl_rows else None,
            "valid_frac_mean": rounded(_finite_mean([r.get("valid_frac") for r in scl_rows]), 4) if scl_rows else None,
        },
    }


def build_summary(
    specs: Mapping[str, Mapping[str, Any]],
    settings: Mapping[str, Any],
    payloads: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    generated = datetime.now().isoformat(timespec="seconds")
    summary: dict[str, Any] = {
        "generated_at": generated,
        "target_year": int(settings.get("target_year", TARGET_YEAR)),
        "data_sources": {
            "weather": "Open-Meteo Archive (ERA5)",
            "optical_catalog": str((V2_DIR / "catalog_2025.json").relative_to(ROOT)),
            "scl": "Sentinel-2 L2A SCL via Planetary Computer SAS (optional)",
        },
        "aois": {},
        "disclaimer": "所有分数为排序分，不是概率、置信度或 F1；无独立标签，不声称精度提升。",
    }
    for aoi, spec in specs.items():
        payload = payloads.get(aoi, {})
        model = payload.get("model") or {}
        evaluation = payload.get("evaluation") or {}
        windows = payload.get("windows") or {}
        candidates = payload.get("candidates") or []
        summary["aois"][aoi] = {
            "label": spec.get("label"),
            "type": spec["profile"].get("label"),
            "bbox": list(spec["bbox"]),
            "n_scenes": len(payload.get("scene_rows") or []),
            "n_candidates": len(candidates),
            "model": model.get("models"),
            "observation": {
                "n_usable": windows.get("n_usable"),
                "n_scenes": windows.get("n_scenes"),
                "max_gap_days": windows.get("max_gap_days"),
                "detection": windows.get("detection"),
            },
            "target_weather": _target_weather_summary(
                payload.get("daily_rows") or [],
                payload.get("scene_rows") or [],
                int(settings.get("target_year", TARGET_YEAR)),
            ),
            "evaluation": evaluation,
        }

    type_methods = {
        key: {
            "label": v["label"],
            "primary": v["primary"],
            "secondary": v["secondary"],
            "family_weights": v["family_weights"],
            "primary_modality": v["primary_modality"],
            "clear_min": v["clear_min"],
            "max_gap_days": v["max_gap_days"],
            "weather_sensitivity": v["weather"],
            "confounders": v["confounders"],
        }
        for key, v in cl.TYPE_PROFILES.items()
    }
    write_json(OUT_ROOT / "type_methods.json", type_methods)

    lines: list[str] = []
    lines.append("# 烟台 2025 气候因素增强分析报告")
    lines.append("")
    lines.append(f"生成时间：{generated}；目标年：{summary['target_year']}。")
    lines.append("")
    lines.append("> 本报告中的分数均为**排序分**，不是概率、置信度或 F1。没有独立标签，因此不声称精度提升；评估只回答“排序是否更合理、天气驱动是否减少、盲区在哪里”。")
    lines.append("")
    lines.append("## 1. 数据与方法")
    lines.append("")
    lines.append("- 气候：Open-Meteo Archive（ERA5）逐日降水、降雪、雪深、云量、土壤湿度、气温、冻融。")
    lines.append("- 影像：`data/yantai_2025_v2/catalog_2025.json` 的 81 景 Sentinel-2 L2A（全为 51STB 瓦片）。")
    lines.append("- 质量：优先使用 Sentinel-2 SCL 的云/雪/阴影占比；无 SCL 时回退为 `valid_frac + 天气代理`。")
    lines.append("- 天气归一化：逐景 AOI 稳定像元中值序列，拟合“年/半年谐波 + 前期降水 + 土壤湿度 + 雪深 + 云量”，Huber IRLS，LOO-CV 验证。")
    lines.append("- 候选校正：以 `first_alarm` 当日天气相对基线（2022–2024）同季均值的偏移，校正 `delta_bsi`/`ndvi_drop`，再按类型可靠性折扣与混淆折减排序。")
    lines.append("")
    lines.append("## 2. 模型拟合与样本外增益")
    lines.append("")
    lines.append("| AOI | 指数 | n | 谐波 R² | 全模型 R² | LOO-RMSE 谐波 | LOO-RMSE 全模型 | 改进 | 是否启用 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for aoi, info in summary["aois"].items():
        for name, m in (info.get("model") or {}).items():
            if not m:
                continue
            lines.append(
                f"| {aoi} | {name} | {m.get('n')} | {_fmt(m.get('harmonic_r2'), 3)} | {_fmt(m.get('full_r2'), 3)} | "
                f"{_fmt(m.get('loo_rmse_harmonic'), 4)} | {_fmt(m.get('loo_rmse_full'), 4)} | "
                f"{_fmt(m.get('improvement_pct'))}% | {'是' if m.get('applied') else '否'} |"
            )
    lines.append("")
    lines.append("启用规则：只有天气项把 LOO-RMSE 降低至少 2% 才用于校正；否则保留原始观测值，避免用不稳定的系数“制造”变化。")
    lines.append("")
    lines.append("## 3. 云/雨/雪与观测条件")
    lines.append("")
    for aoi, info in summary["aois"].items():
        tw = info.get("target_weather") or {}
        scl = tw.get("scl") or {}
        lines.append(f"### {aoi}")
        lines.append("")
        lines.append("| 月份 | 降水 mm | 降雪 cm | 最大雪深 cm | 云量 % | 均温 ℃ |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for m in tw.get("monthly") or []:
            lines.append(
                f"| {m['month']} | {_fmt(m.get('precip_mm'), 1)} | {_fmt(m.get('snowfall_cm'), 1)} | "
                f"{_fmt(m.get('snow_depth_max_cm'), 1)} | {_fmt(m.get('cloud_cover_mean_pct'), 1)} | {_fmt(m.get('temp_mean_c'), 1)} |"
            )
        lines.append("")
        lines.append(
            f"- SCL 可用景数 {scl.get('n_scenes', 0)}；平均云占比 {_fmt(scl.get('cloud_frac_mean'), 3)}、"
            f"雪占比 {_fmt(scl.get('snow_frac_mean'), 3)}、有效像元 {_fmt(scl.get('valid_frac_mean'), 3)}。"
        )
        lines.append("")
    lines.append("## 4. 观测能力（区分“没检出”与“没观测”）")
    lines.append("")
    lines.append("| AOI | 类型 | 目标年景数 | 可用观测 | 最大间隔(天) | 探测能力 | 等级 |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    for aoi, info in summary["aois"].items():
        obs = info.get("observation") or {}
        det = obs.get("detection") or {}
        lines.append(
            f"| {aoi} | {info.get('type')} | {obs.get('n_scenes')} | {obs.get('n_usable')} | "
            f"{obs.get('max_gap_days')} | {_fmt(det.get('power'), 3)} | {det.get('level')} |"
        )
    lines.append("")
    lines.append("观测能力 `1-exp(-n/n_req)` 按类型设定所需可用观测数（露天 6、河道 4、井工 3），超过类型允许间隔再乘 gap 惩罚。探测能力不足的候选只能给到 C 级封顶，不能当作“已排除”。")
    lines.append("")
    lines.append("## 5. 类型化方法矩阵")
    lines.append("")
    lines.append("| 类型 | 主方法 | 次方法 | 主模态 | 最少可用观测 | 最大间隔 | 主要天气混淆 |")
    lines.append("|---|---|---|---|---:|---:|---|")
    for key, v in type_methods.items():
        lines.append(
            f"| {v['label']} | {'；'.join(v['primary'])} | {'；'.join(v['secondary'])} | {v['primary_modality']} | "
            f"{v['clear_min']} | {v['max_gap_days']} | {'；'.join(v['confounders'])} |"
        )
    lines.append("")
    lines.append("## 6. 候选排序与可观测性")
    lines.append("")
    for aoi, info in summary["aois"].items():
        ev = info.get("evaluation") or {}
        nc = ev.get("negative_control") or {}
        ks = ev.get("known_sites") or {}
        rc = ev.get("rain_correlation") or {}
        control_label = "负对照天气可解释候选" if aoi == "A_control" else "本区天气可解释候选"
        mean_change = ks.get("mean_rank_change")
        mean_change_text = "—" if mean_change is None else str(mean_change)
        lines.append(f"### {aoi}（{info.get('type')}，{info.get('n_candidates')} 个候选）")
        lines.append("")
        lines.append(f"- {control_label}：{nc.get('n_weather_explained', 0)} 个、{nc.get('area_weather_explained_ha', 0)} ha；无可用光学观测 {nc.get('n_no_usable_optical', 0)} 个。")
        lines.append(f"- 平均排序分：校正前 {_fmt(nc.get('mean_pre_score'), 2)} → 校正后 {_fmt(nc.get('mean_adjusted_score'), 2)}；校正后低于阈值 {nc.get('n_below_threshold_after')} 个。")
        lines.append(f"- 已知点最近候选排名变化：匹配 {ks.get('n_sites_matched', 0)} 个，平均变化 {mean_change_text}（正值为上升），改善 {ks.get('n_improved', 0)} / 变差 {ks.get('n_worse', 0)}。")
        lines.append(f"- 月度候选数与降雨的 Spearman：校正前 {_fmt(rc.get('spearman_before'), 3)} → 校正后 {_fmt(rc.get('spearman_after'), 3)}（Δ {_fmt(rc.get('delta'), 3)}）。")
        lines.append("")
    lines.append("## 7. 可复算结论")
    lines.append("")
    for aoi, info in summary["aois"].items():
        models = info.get("model") or {}
        applied = [name for name, m in models.items() if m and m.get("applied")]
        ev = info.get("evaluation") or {}
        nc = ev.get("negative_control") or {}
        known = ev.get("known_sites") or {}
        lines.append(
            f"- **{aoi}**：天气模型启用 {('、'.join(applied) if applied else '无')}；"
            f"校正后低于排序阈值 {nc.get('n_below_threshold_after')}/{nc.get('n_candidates')} 个；"
            f"已知点最近候选平均排名变化 {('—' if known.get('mean_rank_change') is None else known.get('mean_rank_change'))}。"
        )
    lines.append("")
    lines.append("这些数字只说明模型是否稳定、排序是否随天气变化、负对照是否被降级；**不构成精度或命中率**。")
    lines.append("")
    lines.append("## 8. 解释与使用边界")
    lines.append("")
    lines.append("1. **天气校正不是精度提升的证明**。没有独立高分/现场标签，本报告不报告 precision/recall/F1。")
    lines.append("2. **场景级近似**。指数序列模型使用 AOI 稳定像元中值，候选校正使用告警日相对基线同季均值，不能替代逐像元辐射归一化。")
    lines.append("3. **雨雪方向因类型而异**。露天采坑雨后湿土压低 BSI，可能漏报；河道采砂洪水冲淤、沙洲出露可能误报；井工 SAR 受降水去相干和冻融影响。")
    lines.append("4. **已知点只评估不排序**。3 个已知采石点仅用于观察最近候选排名变化，不参与候选打分。")
    lines.append("5. **输出即线索**。候选只表示“疑似地表扰动/异常”，不构成违法认定。")
    lines.append("")
    lines.append("## 9. 产物")
    lines.append("")
    lines.append("```")
    lines.append("data/yantai_climate/")
    lines.append("├── summary.json / summary.md")
    lines.append("├── type_methods.json")
    for idx, aoi in enumerate(specs):
        branch = "└──" if idx == len(specs) - 1 else "├──"
        pad = "    " if idx == len(specs) - 1 else "│   "
        lines.append(f"{branch} {aoi}/")
        lines.append(f"{pad}├── daily.csv, daily_meta.json")
        lines.append(f"{pad}├── scene_series.csv, scene_weather.csv")
        lines.append(f"{pad}├── model.json, observation_windows.json")
        lines.append(f"{pad}├── candidates_climate.geojson, climate_candidates.csv")
        lines.append(f"{pad}└── evaluation.json")
    lines.append("```")
    markdown = "\n".join(lines) + "\n"
    (OUT_ROOT / "summary.md").parent.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "summary.md").write_text(markdown, encoding="utf-8")
    write_json(OUT_ROOT / "summary.json", summary)
    return summary, markdown


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------
def run_aoi(
    aoi: str,
    spec: Mapping[str, Any],
    settings: Mapping[str, Any],
    stage: str,
    use_scl: bool,
    force: bool,
) -> dict[str, Any]:
    out_dir = Path(settings["out_dir"]) / aoi
    out_dir.mkdir(parents=True, exist_ok=True)
    daily_file = out_dir / "daily.csv"
    daily_rows = cl.load_daily_csv(daily_file) if daily_file.exists() and not force else []
    if stage in {"fetch", "all"} or not daily_rows:
        daily_rows = fetch_daily(aoi, spec, settings, force=force)

    scene_file = out_dir / "scene_series.csv"
    if stage in {"scenes", "all"} or not scene_file.exists():
        scene_rows = read_scene_series(aoi, spec, force=force)
    else:
        scene_rows = _read_scene_csv(scene_file)

    scl_stats: dict[str, dict[str, Any]] = {}
    scl_dir = out_dir / "scl"
    if scl_dir.exists():
        for path in scl_dir.glob("*.json"):
            data = read_json(path, default=None)
            if isinstance(data, dict) and data.get("scl"):
                scl_stats[str(data.get("item_id"))] = data
    if stage in {"scl", "all"} and use_scl:
        scl_stats = fetch_scl(aoi, spec, settings, force=force)

    model_payload: dict[str, Any] = {}
    scene_wx: list[dict[str, Any]] = []
    if stage in {"model", "explain", "evaluate", "report", "all"}:
        scene_wx = build_scene_weather(aoi, spec, scene_rows, daily_rows, scl_stats, settings, force=force)
        model_payload = fit_models(aoi, scene_wx, daily_rows, settings, force=force)
        scene_wx = apply_models_to_scenes(scene_wx, model_payload, daily_rows)
        write_csv(Path(settings["out_dir"]) / aoi / "scene_weather.csv", scene_wx, SCENE_WX_FIELDS)

    features: list[dict[str, Any]] = []
    windows: dict[str, Any] = {}
    if stage in {"explain", "evaluate", "report", "all"}:
        features = load_candidates(spec)
        if features:
            features = explain_candidates(aoi, spec, features, scene_wx, daily_rows, model_payload, scl_stats, settings)
        else:
            print(f"[explain] {aoi}: 无候选 GeoJSON，跳过")
        windows_file = out_dir / "observation_windows.json"
        windows = read_json(windows_file, default={}) if windows_file.exists() else {}

    evaluation: dict[str, Any] = {}
    if stage in {"evaluate", "report", "all"}:
        evaluation = evaluate_aoi(aoi, spec, features, daily_rows, model_payload, settings)
        write_json(out_dir / "evaluation.json", evaluation)
        print(f"[evaluate] {aoi}: 完成")

    return {
        "daily_rows": daily_rows,
        "scene_rows": scene_wx or scene_rows,
        "model": model_payload,
        "candidates": features,
        "windows": windows,
        "evaluation": evaluation,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="烟台 2025 气候因素增强分析链路")
    parser.add_argument("--aoi", default="mining_belt,A_control", help="逗号分隔；默认 mining_belt,A_control")
    parser.add_argument(
        "--stage",
        default="all",
        choices=["all", "fetch", "scenes", "scl", "model", "explain", "evaluate", "report"],
    )
    parser.add_argument("--scl", action="store_true", help="读取 Sentinel-2 SCL 云/雪占比（较慢，需 SAS）")
    parser.add_argument("--force", action="store_true", help="忽略已有中间产物并重算")
    args = parser.parse_args(argv)

    settings = climate_settings()
    keys = [k for k in str(args.aoi).split(",") if k.strip()]
    specs = aoi_specs(keys, settings)
    payloads: dict[str, Any] = {}
    for aoi, spec in specs.items():
        print(f"=== {aoi} ({spec['label']}) ===")
        payloads[aoi] = run_aoi(aoi, spec, settings, args.stage, args.scl, args.force)

    if args.stage in {"report", "all"}:
        summary, _ = build_summary(specs, settings, payloads)
        print(f"[report] 写入 {OUT_ROOT / 'summary.json'}")
        print(f"[report] 写入 {OUT_ROOT / 'summary.md'}")
        for aoi, info in summary["aois"].items():
            print(f"  {aoi}: scenes={info['n_scenes']} candidates={info['n_candidates']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
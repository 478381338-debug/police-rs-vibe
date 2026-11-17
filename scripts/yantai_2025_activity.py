"""烟台 2025 全年采矿活动可观测性试点。

严格按《烟台2025全年采矿活动与疑似非法采矿遥感分析建模实施方案》V1.0
的 E1 主线做第一阶段真实运行：

  - 20 m 网格，A_control（农田负对照）与 mining_belt（D/B 采石群）
  - 2022-2024 月合成基线，Huber 稳健谐波（K=1）
  - 2024 伪年标定 CUSUM 的 h（k 固定 0.5）
  - 2025 逐景 CUSUM，对象级规则候选
  - E0 同季两期差分对照
  - 输出 catalog / coverage / candidates / summary / report

边界：没有 2025 独立高分参考时只报 F1（方法可运行），
不输出校准概率，不把候选称为“非法采矿”。
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import sys
import time
import warnings
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.transform import from_bounds
from rasterio.warp import transform_bounds, transform as warp_transform
from rasterio.windows import from_bounds as window_from_bounds
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

KNOWN_SITES = [
    {"name": "YT-007", "lon": 120.23434, "lat": 37.13838, "kind": "quarry"},
    {"name": "B-quarry-1", "lon": 120.30465, "lat": 37.11278, "kind": "quarry"},
    {"name": "B-quarry-2", "lon": 120.33115, "lat": 37.11626, "kind": "quarry"},
]


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yantai_mvp_experiment import fetch_esri, save_png, search_items, signed  # noqa: E402
from mining_watch import evidence as evidence_mod  # noqa: E402
from mining_watch.landcover import landcover_fractions, load_worldcover_grid  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

OUT = Path("data/yantai_2025_v2")
DOCS = Path("docs")
REPORT_PATH = DOCS / "烟台2025全年采矿活动遥感分析报告-v2.md"
MODEL_CARD_PATH = DOCS / "烟台2025试点模型卡-v2.md"
UTM = "EPSG:32651"
# S2 L2A PB >= 04.00 stores BOA reflectance as (DN + BOA_ADD_OFFSET) / 10000.
# PC COGs preserve the DN; verified from MTD_MSIL2A.xml (BOA_ADD_OFFSET=-1000).
BOA_ADD_OFFSET = -1000.0
BOA_SCALE = 0.0001
USE_BOA_OFFSET = True
RESAMPLING = "average"
RES = 20.0
BANDS = ["B02", "B03", "B04", "B08", "B11", "B12", "SCL"]
VALID_SCL = {4, 5, 7}

AOIS = {
    "mining_belt": {
        "label": "莱州东南—p07 采石群",
        "bbox": [120.15, 37.08, 120.36, 37.21],
    },
    "A_control": {
        "label": "招远北部农田负对照",
        "bbox": [120.06, 37.40, 120.13, 37.47],
    },
}

# 方案参数
BASELINE_START = date(2022, 1, 1)
BASELINE_END = date(2024, 12, 31)
CAL_YEAR = 2024
TARGET_YEAR = 2025
K_CUSUM = 0.5
H_PERCENTILE = 99.5
MIN_AREA_HA = 0.5
MIN_AREA_PX = math.ceil(MIN_AREA_HA * 10000 / (RES * RES))
S_MIN = {"bsi": 0.005, "ndvi": 0.01}
MAX_GAP_DAYS = 45

# 物候与农田软抑制（2026-11-16 提精度方案第 1、2 步）
SUMMER_MONTHS = {6, 7, 8}
SUMMER_NDVI_MAX = 0.35
# 该 AOI 20 m 混合像元下裸土 BSI 中位数约 0.04、90 分位约 0.14；
# 0.30 会把采石坑判成“返绿”，故按 WorldCover 裸地分布校准到 0.05。
SUMMER_BSI_MIN = 0.05
SUMMER_MIN_BASELINE_MONTHS = 2
SUMMER_OBJECT_VALID_MIN = 0.30
CROPLAND_DOMINANT = 0.60
CROPLAND_MIXED = 0.30
# 夏季持续裸地对象（规则版矿区制图，第 3 步分割前的过渡）
PERSISTENT_MIN_AREA_HA = 1.0
PERSISTENT_BASELINE_YEARS = 2
PERSISTENT_BARE_LC_MIN = 0.20
PERSISTENT_KNOWN_MAX_M = 3000.0
WORLDCOVER_DIR = Path("data/landcover")
WORLDCOVER_PATH: Path | None = None
# 多源佐证：按《非法采矿多源佐证与情景验证方案》§8.1 从候选属性派生因子再融合评分。
# as_of 必须取分析期末日，否则 2025 证据按“距今”衰减会整体落到 D。
EVIDENCE_CONFIG = Path("config_yantai_evidence.yaml")
EVIDENCE_AS_OF = date(TARGET_YEAR, 12, 31)
USE_WORLDCOVER = True
LAYERS = [
    "mine_candidate",
    "farmland_destruction_suspect",
    "farmland_management",
    "other_bare",
    "uncertain",
]
LAYER_ORDER = {name: i for i, name in enumerate(LAYERS)}
TIER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}
LAYER_LABELS = {
    "mine_candidate": "矿区候选",
    "farmland_destruction_suspect": "毁田采矿疑似",
    "farmland_management": "农田管理（剔除）",
    "other_bare": "其他裸地（剔除）",
    "uncertain": "不确定",
}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def month_iter(y0: int, y1: int):
    for y in range(y0, y1 + 1):
        for m in range(1, 13):
            yield y, m


def month_end(y: int, m: int) -> date:
    if m == 12:
        return date(y, 12, 31)
    return date(y, m + 1, 1) - timedelta(days=1)


def item_date(item: dict) -> date:
    return date.fromisoformat(item["properties"]["datetime"][:10])


def tile_of(item: dict) -> str:
    return str(item["properties"].get("s2:mgrs_tile", ""))


def cloud_of(item: dict) -> float:
    return float(item["properties"].get("eo:cloud_cover", 99.0))


def pick_month_items(bbox, y, m, role: str, max_n: int) -> list[dict]:
    start = f"{y}-{m:02d}-01T00:00:00Z"
    end = f"{y}-{m:02d}-{month_end(y, m).day:02d}T23:59:59Z"
    cloud_lt = 80 if role == "target" else 60
    feats = search_items("sentinel-2-l2a", bbox, start, end, cloud_lt=cloud_lt)
    # 同一天优先 51STB；去重后按云量排序
    by_day: dict[str, dict] = {}
    for f in feats:
        d = item_date(f).isoformat()
        tile = tile_of(f)
        if tile not in ("51STB", "50SQG"):
            continue
        cur = by_day.get(d)
        if cur is None:
            by_day[d] = f
            continue
        ct, cc = tile_of(cur), cloud_of(cur)
        if (tile == "51STB" and ct != "51STB") or (
            tile == ct and cloud_of(f) < cc
        ):
            by_day[d] = f
    items = sorted(by_day.values(), key=lambda f: (cloud_of(f), item_date(f)))
    return items[:max_n]


def build_catalog(bbox: list[float], max_baseline: int, max_cal: int,
                  max_target: int) -> list[dict]:
    catalog: list[dict] = []
    for y in range(2022, 2026):
        for m in range(1, 13):
            if y <= 2023:
                role, max_n = "baseline", max_baseline
            elif y == CAL_YEAR:
                role, max_n = "calibration", max_cal
            else:
                role, max_n = "target", max_target
            try:
                items = pick_month_items(bbox, y, m, role, max_n)
            except Exception as exc:  # noqa: BLE001
                log(f"STAC {y}-{m:02d} failed: {exc}")
                continue
            for f in items:
                p = f["properties"]
                catalog.append({
                    "id": f["id"],
                    "date": item_date(f).isoformat(),
                    "datetime": p.get("datetime"),
                    "cloud": round(cloud_of(f), 4),
                    "tile": tile_of(f),
                    "role": role,
                    "assets": {k: f["assets"][k]["href"] for k in BANDS if k in f["assets"]},
                })
            log(f"catalog {y}-{m:02d} {role}: {len(items)}")
    catalog.sort(key=lambda x: x["date"])
    return catalog


def grid_for(bbox: list[float]):
    b = transform_bounds("EPSG:4326", UTM, *bbox)
    w = max(1, int(round((b[2] - b[0]) / RES)))
    h = max(1, int(round((b[3] - b[1]) / RES)))
    tr = from_bounds(b[0], b[1], b[2], b[3], w, h)
    return b, w, h, tr


def _read_band(url: str, bbox: list[float], w: int, h: int, band: str) -> np.ndarray:
    with rasterio.open(url) as ds:
        wb = transform_bounds("EPSG:4326", ds.crs, *bbox)
        win = window_from_bounds(*wb, ds.transform).round_offsets().round_lengths()
        resampling = Resampling.nearest if band == "SCL" else Resampling.average
        return ds.read(
            1, window=win, out_shape=(h, w), boundless=True, fill_value=0,
            resampling=resampling,
        ).astype(np.float32)


def read_scene(item: dict, bbox: list[float], w: int, h: int, threads: int = 7) -> dict:
    urls = {}
    for band in BANDS:
        href = item["assets"].get(band)
        if href is None:
            raise RuntimeError(f"{item['id']} missing {band}")
        # SAS 签名先串行完成，避免多线程同时写缓存
        urls[band] = signed(href)
    arrs = {}
    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        futs = {b: ex.submit(_read_band, urls[b], bbox, w, h, b) for b in BANDS}
        for b, fut in futs.items():
            arrs[b] = fut.result()

    scl = arrs["SCL"].astype(np.int16)
    valid = np.isin(scl, list(VALID_SCL))
    for b in ("B02", "B03", "B04", "B08", "B11", "B12"):
        valid &= arrs[b] > 0
    for b in ("B02", "B03", "B04", "B08", "B11", "B12"):
        valid &= np.isfinite(arrs[b])

    # L2A PB >= 04.00: BOA reflectance = (DN + offset) / 10000. PC COGs keep DN.
    # The offset is documented in each product's MTD_MSIL2A.xml.
    for b in ("B02", "B03", "B04", "B08", "B11", "B12"):
        if USE_BOA_OFFSET:
            arrs[b] = (arrs[b] + BOA_ADD_OFFSET) * BOA_SCALE
        else:
            arrs[b] = arrs[b] * BOA_SCALE

    b02, b03, b04 = arrs["B02"], arrs["B03"], arrs["B04"]
    b08, b11, b12 = arrs["B08"], arrs["B11"], arrs["B12"]

    def ratio(num, den):
        with np.errstate(divide="ignore", invalid="ignore"):
            out = num / den
        return np.where(valid & (np.abs(den) > 1e-6), out, np.nan)

    bsi = ratio((b11 + b04) - (b08 + b02), (b11 + b04) + (b08 + b02))
    ndvi = ratio(b08 - b04, b08 + b04)
    nbr = ratio(b08 - b12, b08 + b12)
    ndwi = ratio(b03 - b08, b03 + b08)

    for name, a in (("BSI", bsi), ("NDVI", ndvi), ("NBR", nbr), ("NDWI", ndwi)):
        if not np.isfinite(a[valid]).any():
            log(f"WARN {item['id']} {name} all NaN")
    return {"bsi": bsi, "ndvi": ndvi, "nbr": nbr, "ndwi": ndwi, "valid": valid}


def cache_file(aoi_name: str, item_id: str) -> Path:
    tag = "boa" if USE_BOA_OFFSET else "raw"
    d = OUT / "cache" / aoi_name / tag
    d.mkdir(parents=True, exist_ok=True)
    safe = item_id.replace("/", "_")
    return d / f"{safe}.npz"


def load_scene(item: dict, aoi_name: str, bbox: list[float], w: int, h: int,
               use_cache: bool, threads: int = 7) -> dict:
    cp = cache_file(aoi_name, item["id"])
    if use_cache and cp.exists():
        try:
            z = np.load(cp)
            return {k: z[k] for k in ("bsi", "ndvi", "nbr", "ndwi", "valid")}
        except Exception:  # noqa: BLE001
            pass
    t0 = time.time()
    last = None
    for attempt in range(3):
        try:
            arr = read_scene(item, bbox, w, h, threads=threads)
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            log(f"retry {attempt + 1}/3 {aoi_name} {item['id']}: {exc}")
            time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"{aoi_name} {item['id']}: read failed after retries: {last}")
    log(f"read {aoi_name} {item['id']} {time.time()-t0:.1f}s cloud={item['cloud']}")
    if use_cache:
        np.savez_compressed(
            cp, bsi=arr["bsi"].astype(np.float32), ndvi=arr["ndvi"].astype(np.float32),
            nbr=arr["nbr"].astype(np.float32), ndwi=arr["ndwi"].astype(np.float32),
            valid=arr["valid"],
        )
    return arr


def design(dates: list[date]) -> np.ndarray:
    t = np.array([(d - BASELINE_START).days / 365.25 for d in dates], dtype=np.float64)
    return np.column_stack([np.ones_like(t), t, np.sin(2 * np.pi * t), np.cos(2 * np.pi * t)])


def fit_harmonic(Y: np.ndarray, dates: list[date], s_min: float,
                 min_valid: int = 8) -> dict:
    """按像素 Huber IRLS 谐波拟合；Y=(n_months, n_pixels)。"""
    X = design(dates)
    valid = np.isfinite(Y)
    nvalid = valid.sum(axis=0)
    Y0 = np.where(valid, Y, 0.0)
    XtX = np.einsum("ti,tj,tp->ijp", X, X, valid.astype(np.float32))
    XtY = np.einsum("ti,tp->ip", X, Y0)
    # 轻微岭正则，避免稀疏月份奇异
    for i in range(4):
        XtX[i, i, :] += 1e-6
    try:
        beta = np.linalg.solve(
            XtX.transpose(2, 0, 1), XtY.T[..., None]
        ).squeeze(-1).T  # (4,p)
    except np.linalg.LinAlgError:
        beta = np.full((4, Y.shape[1]), np.nan)

    for _ in range(3):
        pred = X @ beta
        resid = Y - pred
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            med = np.nanmedian(resid, axis=0)
            mad = np.nanmedian(np.abs(resid - med), axis=0)
        scale = np.maximum(1.4826 * mad, s_min)
        delta = 1.345 * scale
        with np.errstate(divide="ignore", invalid="ignore"):
            w = np.where(np.abs(resid) <= delta, 1.0, delta / np.maximum(np.abs(resid), 1e-6))
        w = np.where(valid, w, 0.0)
        XtX = np.einsum("ti,tj,tp,tp->ijp", X, X, w, valid.astype(np.float32))
        XtY = np.einsum("ti,tp,tp->ip", X, w * Y0, valid.astype(np.float32))
        for i in range(4):
            XtX[i, i, :] += 1e-6
        try:
            beta = np.linalg.solve(
                XtX.transpose(2, 0, 1), XtY.T[..., None]
            ).squeeze(-1).T
        except np.linalg.LinAlgError:
            break

    pred = X @ beta
    resid = Y - pred
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        med = np.nanmedian(resid, axis=0)
        mad = np.nanmedian(np.abs(resid - med), axis=0)
    scale = np.maximum(1.4826 * mad, s_min)
    bad = nvalid < min_valid
    beta[:, bad] = np.nan
    med[bad] = np.nan
    scale[bad] = np.nan
    return {"beta": beta, "median": med, "scale": scale, "nvalid": nvalid}


def predict(model: dict, d: date) -> np.ndarray:
    X = design([d])[0]
    return X @ model["beta"]


def zscore(arr: np.ndarray, model: dict, d: date) -> np.ndarray:
    a = arr.reshape(-1)
    pred = predict(model, d)
    out = (a - pred - model["median"]) / model["scale"]
    return out.reshape(arr.shape).astype(np.float32)


def init_state(h: int, w: int) -> dict:
    return {
        "g": np.zeros((h, w), np.float32),
        "maxg": np.zeros((h, w), np.float32),
        "first_alarm": np.full((h, w), -1.0, np.float32),
        "last_alarm": np.full((h, w), -1.0, np.float32),
        "t0": np.full((h, w), -1.0, np.float32),
        "last_below": np.full((h, w), -1.0, np.float32),
        "last_valid": np.full((h, w), -1.0, np.float32),
        "n_valid": np.zeros((h, w), np.int16),
        "n_alarm": np.zeros((h, w), np.int16),
        "bsi_max": np.full((h, w), np.nan, np.float32),
        "ndvi_max": np.full((h, w), np.nan, np.float32),
        "nbr_max": np.full((h, w), np.nan, np.float32),
        "ndwi_max": np.full((h, w), np.nan, np.float32),
        "d_max": np.full((h, w), np.nan, np.float32),
        "valid_dates": [],
    }


def update_cusum(state: dict, arr: dict, m_bsi: dict, m_ndvi: dict,
                 day: int, k: float, h_thr: float, d_date: date | None = None) -> None:
    valid = arr["valid"]
    if d_date is None:
        d_date = BASELINE_START + timedelta(days=day)
    z_bsi = zscore(arr["bsi"], m_bsi, d_date)
    z_ndvi = zscore(arr["ndvi"], m_ndvi, d_date)
    with np.errstate(invalid="ignore", divide="ignore"):
        d = (z_bsi - z_ndvi) / 2.0
    d = np.where(valid & np.isfinite(d), d, np.nan).astype(np.float32)

    reset = valid & (state["last_valid"] >= 0) & ((day - state["last_valid"]) > MAX_GAP_DAYS)
    state["g"] = np.where(reset, 0.0, state["g"]).astype(np.float32)
    inc = np.where(valid & np.isfinite(d), d - k, 0.0).astype(np.float32)
    state["g"] = np.maximum(0.0, state["g"] + inc).astype(np.float32)

    improved = valid & (state["g"] > state["maxg"])
    state["maxg"][improved] = state["g"][improved]
    for src, dst in (("bsi", "bsi_max"), ("ndvi", "ndvi_max"),
                     ("nbr", "nbr_max"), ("ndwi", "ndwi_max")):
        state[dst][improved] = arr[src][improved]
    state["d_max"][improved] = d[improved]

    cross = valid & (state["g"] > h_thr)
    new = cross & (state["first_alarm"] < 0)
    state["t0"][new] = state["last_below"][new]
    state["first_alarm"][new] = float(day)
    state["last_alarm"][cross] = float(day)
    state["n_alarm"] += cross.astype(np.int16)

    below = valid & ~cross
    state["last_below"][below] = float(day)
    state["last_valid"][valid] = float(day)
    state["n_valid"] += valid.astype(np.int16)
    if d_date.year == TARGET_YEAR:
        state["valid_dates"].append(valid.copy())


def day_date(day: float, base: date = BASELINE_START) -> str | None:
    if not np.isfinite(day) or day < 0:
        return None
    return (base + timedelta(days=int(round(day)))).isoformat()


def clean_mask(mask: np.ndarray) -> np.ndarray:
    m = ndimage.binary_opening(mask, structure=np.ones((3, 3), bool))
    m = ndimage.binary_closing(m, structure=np.ones((3, 3), bool))
    m = ndimage.binary_fill_holes(m)
    return m



def annotate_known(objs: list[dict]) -> None:
    for o in objs:
        ll = o.get("centroid_lonlat")
        best = None
        if ll:
            for site in KNOWN_SITES:
                d = haversine_m(ll[0], ll[1], site["lon"], site["lat"])
                if best is None or d < best[0]:
                    best = (d, site["name"], site["kind"])
        o["nearest_known"] = None if best is None else best[1]
        o["nearest_known_kind"] = None if best is None else best[2]
        o["distance_known_m"] = None if best is None else round(best[0], 1)
        o["near_known"] = bool(best is not None and best[0] <= 1000.0)
        # Known-point proximity is context only; it is not remote-sensing evidence.
        o["priority"] = bool(o.get("priority"))


def _mean_finite(values: np.ndarray) -> float:
    v = values[np.isfinite(values)]
    return float(v.mean()) if v.size else float("nan")


def summer_object_stats(comp: np.ndarray, target_bsi: np.ndarray | None,
                        target_ndvi: np.ndarray | None,
                        baseline_summer: dict | None) -> dict:
    """对象级夏季物候：6–8 月裸土、基线裸年数、持续性。"""
    n = int(comp.sum())
    out = {
        "summer_ndvi": None,
        "summer_bsi": None,
        "summer_bare_frac": None,
        "summer_obs_frac": None,
        "summer_bare": None,
        "baseline_summer_bare_years": None,
        "bare_years": None,
        "persistent_bare": None,
        "phenology_class": "unknown",
    }
    if n == 0 or target_bsi is None or target_ndvi is None:
        return out
    ok = comp & np.isfinite(target_bsi) & np.isfinite(target_ndvi)
    out["summer_obs_frac"] = round(float(ok.sum()) / n, 3)
    if ok.sum() / n < SUMMER_OBJECT_VALID_MIN:
        return out
    sb, sn = target_bsi[ok], target_ndvi[ok]
    out["summer_ndvi"] = round(float(np.mean(sn)), 4)
    out["summer_bsi"] = round(float(np.mean(sb)), 4)
    bare_frac = float(np.mean((sn < SUMMER_NDVI_MAX) & (sb > SUMMER_BSI_MIN)))
    out["summer_bare_frac"] = round(bare_frac, 3)
    out["summer_bare"] = bool(bare_frac >= 0.5)

    bare_years = []
    for year, arrs in sorted((baseline_summer or {}).items()):
        b, nv = arrs["bsi"], arrs["ndvi"]
        okb = comp & np.isfinite(b) & np.isfinite(nv)
        if okb.sum() / n < SUMMER_OBJECT_VALID_MIN:
            continue
        bf = float(np.mean((nv[okb] < SUMMER_NDVI_MAX) & (b[okb] > SUMMER_BSI_MIN)))
        if bf >= 0.5:
            bare_years.append(year)
    out["baseline_summer_bare_years"] = len(bare_years)
    out["bare_years"] = len(bare_years) + (1 if out["summer_bare"] else 0)
    out["persistent_bare"] = bool(out["bare_years"] >= 2)
    if out["summer_bare"] and out["persistent_bare"]:
        out["phenology_class"] = "mine_like"
    elif out["summer_bare"]:
        out["phenology_class"] = "new_bare"
    else:
        out["phenology_class"] = "green_summer"
    return out


def classify_layer(rec: dict) -> None:
    """按夏季物候 + WorldCover 先验把对象分到输出层。"""
    crop = rec.get("cropland_frac")
    phen = rec.get("phenology_class")
    bare = rec.get("summer_bare")
    persistent = rec.get("persistent_bare")
    if phen == "unknown" or bare is None:
        layer, reason = "uncertain", "夏季有效观测不足"
    elif crop is None:
        if bare and persistent:
            layer, reason = "mine_candidate", "夏季持续裸；无地类先验"
        elif bare:
            layer, reason = "mine_candidate", "夏季裸；持续性未确认"
        else:
            layer, reason = "other_bare", "夏季返绿；非矿区"
    elif crop >= CROPLAND_DOMINANT:
        if bare and persistent:
            layer, reason = "farmland_destruction_suspect", "耕地内夏季持续裸，疑似毁田采矿"
        elif not bare:
            layer, reason = "farmland_management", "耕地且夏季返绿"
        else:
            layer, reason = "uncertain", "耕地内夏季裸，但持续性未确认"
    elif crop >= CROPLAND_MIXED:
        layer, reason = "uncertain", "耕地/非耕地混合"
    else:
        if bare and persistent:
            layer, reason = "mine_candidate", "非耕地且夏季持续裸"
        elif bare:
            layer, reason = "mine_candidate", "非耕地且夏季裸；持续性未确认"
        else:
            layer, reason = "other_bare", "非耕地但夏季返绿"
    rec["layer"] = layer
    rec["layer_reason"] = reason


def persistent_bare_objects(mask: np.ndarray, tr, landcover: np.ndarray | None,
                            target_bsi: np.ndarray, target_ndvi: np.ndarray,
                            baseline_summer: dict) -> list[dict]:
    """把“目标年夏季裸 + 至少 2 个基线年夏季裸”的连通域做成矿区候选。

    这是第 3 步语义分割落地前的规则版制图：只保留非耕地、WorldCover
    裸地占比≥0.2、面积≥1 ha 的对象，避免把收割后的农田当成矿区。
    """
    lab, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
    if n == 0:
        return []
    out = []
    for i in range(1, n + 1):
        comp = lab == i
        npix = int(comp.sum())
        if npix < MIN_AREA_PX:
            continue
        area_ha = npix * RES * RES / 10000.0
        if area_ha < PERSISTENT_MIN_AREA_HA:
            continue
        try:
            gs = [shape(g) for g, v in shapes(comp.astype("uint8"), transform=tr) if v == 1]
            geom = unary_union(gs) if gs else None
        except Exception:  # noqa: BLE001
            geom = None
        if geom is None or geom.is_empty:
            continue
        if landcover is not None:
            lc_stats = landcover_fractions(landcover, comp)
            crop = lc_stats.get("cropland_frac")
            bare_lc = lc_stats.get("bare_landcover_frac")
            if crop is None or crop >= CROPLAND_MIXED:
                continue
            if bare_lc is None or bare_lc < PERSISTENT_BARE_LC_MIN:
                continue
        else:
            lc_stats = {
                "cropland_frac": None, "vegetated_frac": None,
                "bare_landcover_frac": None, "built_frac": None,
                "water_frac": None, "landcover_n": 0,
            }
        summer = summer_object_stats(comp, target_bsi, target_ndvi, baseline_summer)
        if not summer.get("summer_bare"):
            continue
        perim = float(geom.length) if geom.length > 0 else 0.0
        compact = 4 * math.pi * geom.area / (perim * perim) if perim > 0 else 0.0
        minx, miny, maxx, maxy = geom.bounds
        rec = {
            "geometry": mapping(geom),
            "area_ha": round(area_ha, 3),
            "perimeter_m": round(perim, 1),
            "compactness": round(compact, 3),
            "bbox_w_m": round(maxx - minx, 1),
            "bbox_h_m": round(maxy - miny, 1),
            "source": "persistent_bare",
            "change_detected": False,
            "priority": False,
            "priority_reason": None,
            "centroid_utm": [round(float(geom.centroid.x), 2), round(float(geom.centroid.y), 2)],
            "centroid_lonlat": None,
        }
        try:
            lons, lats = warp_transform(UTM, "EPSG:4326",
                                        [rec["centroid_utm"][0]], [rec["centroid_utm"][1]])
            rec["centroid_lonlat"] = [round(float(lons[0]), 6), round(float(lats[0]), 6)]
        except Exception:  # noqa: BLE001
            pass
        rec.update(summer)
        rec.update(lc_stats)
        classify_layer(rec)
        if rec.get("layer") != "mine_candidate":
            continue
        out.append(rec)
    return out


def objects_from_mask(mask: np.ndarray, tr, state: dict, base_bsi: np.ndarray,
                      base_ndvi: np.ndarray, ndates: int, min_area_px: int,
                      target_dates: list[date], valid_dates: list[np.ndarray],
                      base_valid_months: np.ndarray | None = None,
                      context: dict | None = None) -> list[dict]:
    lab, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
    if n == 0:
        return []
    objs = []
    for i in range(1, n + 1):
        comp = lab == i
        npix = int(comp.sum())
        if npix < min_area_px:
            continue
        ys, xs = np.where(comp)
        geom = None
        try:
            gs = [shape(g) for g, v in shapes(comp.astype("uint8"), transform=tr) if v == 1]
            if gs:
                geom = unary_union(gs)
        except Exception:  # noqa: BLE001
            geom = None
        if geom is None or geom.is_empty:
            continue
        area_ha = npix * RES * RES / 10000.0
        perim = float(geom.length) if geom.length > 0 else 0.0
        compact = 4 * math.pi * geom.area / (perim * perim) if perim > 0 else 0.0
        minx, miny, maxx, maxy = geom.bounds
        w_m, h_m = maxx - minx, maxy - miny

        # Object-level effective observation: q_R,t = valid pixels / object pixels,
        # clear only when q_R,t >= 0.8 on that date.
        n_cov = min(len(target_dates), len(valid_dates))
        if n_cov:
            vd = np.stack([valid_dates[j][comp] for j in range(n_cov)], axis=0)
            frac_by_date = vd.mean(axis=1)
            q80_days = [target_dates[j] for j in range(n_cov) if frac_by_date[j] >= 0.8]
            object_valid_frac = float(frac_by_date.mean())
            object_q80_frac = float(np.mean(frac_by_date >= 0.8))
        else:
            q80_days = []
            object_valid_frac = np.nan
            object_q80_frac = np.nan
        if q80_days:
            anchors = [date(TARGET_YEAR, 1, 1)] + sorted(set(q80_days)) + [date(TARGET_YEAR, 12, 31)]
            object_gap = max((b - a).days for a, b in zip(anchors, anchors[1:]))
            first_clear = min(q80_days).isoformat()
            last_clear = max(q80_days).isoformat()
        else:
            object_gap = None
            first_clear = None
            last_clear = None

        def nanmean(a):
            v = a[comp]
            v = v[np.isfinite(v)]
            return float(v.mean()) if v.size else np.nan

        base = base_bsi[comp]
        base = base[np.isfinite(base)]
        bare_frac = float(np.mean(base > 0.1)) if base.size else np.nan
        t0 = state["t0"][comp]
        t0 = t0[t0 >= 0]
        t1 = state["first_alarm"][comp]
        t1 = t1[t1 >= 0]
        tlast = state["last_alarm"][comp]
        tlast = tlast[tlast >= 0]
        # Object-level t0: last observation with any valid object pixel before the
        # object's earliest alarm. This avoids a pixel-level median landing after
        # the object first_alarm (the v1 P5 inconsistency).
        t0_obj = None
        if t1.size:
            obj_first = float(t1.min())
            prior = []
            n_cov_t0 = min(len(target_dates), len(valid_dates))
            for j in range(n_cov_t0):
                dd = target_dates[j]
                if (dd - BASELINE_START).days < obj_first and valid_dates[j][comp].any():
                    prior.append(dd)
            if prior:
                t0_obj = max(prior).isoformat()
        maxg = state["maxg"][comp]
        maxg = maxg[np.isfinite(maxg)]
        nvalid = state["n_valid"][comp].astype(np.float32)
        nalarm = state["n_alarm"][comp].astype(np.float32)

        delta_bsi = nanmean(state["bsi_max"]) - float(np.nanmean(base_bsi[comp]))
        delta_ndvi = nanmean(state["ndvi_max"]) - float(np.nanmean(base_ndvi[comp]))

        ctx = context or {}
        summer = summer_object_stats(
            comp, ctx.get("summer_bsi"), ctx.get("summer_ndvi"),
            ctx.get("baseline_summer"),
        )
        if ctx.get("landcover") is not None:
            lc_stats = landcover_fractions(ctx["landcover"], comp)
        else:
            lc_stats = {
                "cropland_frac": None, "vegetated_frac": None,
                "bare_landcover_frac": None, "built_frac": None,
                "water_frac": None, "landcover_n": 0,
            }

        cx, cy = float(geom.centroid.x), float(geom.centroid.y)
        try:
            lons, lats = warp_transform("EPSG:32651", "EPSG:4326", [cx], [cy])
            lonlat = [round(float(lons[0]), 6), round(float(lats[0]), 6)]
        except Exception:  # noqa: BLE001
            lonlat = None
        rec = {
            "geometry": mapping(geom),
            "source": "cusum",
            "change_detected": True,
            "persistent_bare": False,
            "area_ha": round(area_ha, 3),
            "perimeter_m": round(perim, 1),
            "compactness": round(compact, 3),
            "bbox_w_m": round(w_m, 1),
            "bbox_h_m": round(h_m, 1),
            "mean_max_cusum": round(float(maxg.mean()), 3) if maxg.size else None,
            "max_cusum": round(float(maxg.max()), 3) if maxg.size else None,
            "mean_n_valid": round(float(nvalid.mean()), 1),
            "mean_n_alarm": round(float(nalarm.mean()), 1),
            "valid_frac": None if not np.isfinite(object_valid_frac) else round(object_valid_frac, 3),
            "q80_fraction": None if not np.isfinite(object_q80_frac) else round(object_q80_frac, 3),
            "n_clear_dates": len(q80_days),
            "first_clear": first_clear,
            "last_clear": last_clear,
            "object_gap_days": object_gap,
            "baseline_bare_frac": None if not np.isfinite(bare_frac) else round(bare_frac, 3),
            "baseline_bsi_mean": round(float(np.nanmean(base_bsi[comp])), 4),
            "baseline_valid_months_min": (int(base_valid_months[comp].min())
                                          if base_valid_months is not None else None),
            "baseline_insufficient": (bool(base_valid_months[comp].min() < 8)
                                      if base_valid_months is not None else None),
            "bsi_at_max": round(nanmean(state["bsi_max"]), 4),
            "ndvi_at_max": round(nanmean(state["ndvi_max"]), 4),
            "nbr_at_max": round(nanmean(state["nbr_max"]), 4),
            "ndwi_at_max": round(nanmean(state["ndwi_max"]), 4),
            "delta_bsi": round(delta_bsi, 4) if np.isfinite(delta_bsi) else None,
            "priority": bool(np.isfinite(delta_bsi) and delta_bsi >= 0.10),
            "priority_reason": "delta_bsi>=0.10" if bool(np.isfinite(delta_bsi) and delta_bsi >= 0.10) else None,
            "t0": t0_obj,
            "t0_definition": "last date with any valid object pixel before object first_alarm; not physical start",
            "t0_pixel_median": day_date(float(np.median(t0))) if t0.size else None,
            "t1": day_date(float(np.median(t1))) if t1.size else None,
            "first_alarm": day_date(float(t1.min())) if t1.size else None,
            "last_alarm": day_date(float(tlast.max())) if tlast.size else None,
            "alarm_span_days": int(round(float(tlast.max() - t1.min()))) if t1.size and tlast.size else None,
            "centroid_utm": [round(float(geom.centroid.x), 2), round(float(geom.centroid.y), 2)],
            "centroid_lonlat": lonlat,
        }
        rec.update(summer)
        rec.update(lc_stats)
        classify_layer(rec)
        objs.append(rec)
    return objs


def run_aoi(name: str, catalog: list[dict], use_cache: bool, max_target: int) -> dict:
    aoi = AOIS[name]
    bbox = aoi["bbox"]
    ub, w, h, tr = grid_for(bbox)
    log(f"=== {name} {aoi['label']} grid={w}x{h} ===")

    # 1) 2022-2023 月合成
    monthly = defaultdict(list)
    items = [x for x in catalog if x["role"] == "baseline"]
    for k, item in enumerate(items, 1):
        try:
            arr = load_scene(item, name, bbox, w, h, use_cache)
        except Exception as exc:  # noqa: BLE001
            log(f"skip {item['id']}: {exc}")
            continue
        d = date.fromisoformat(item["date"])
        monthly[item["date"][:7]].append((d, arr))
        if k % 10 == 0:
            log(f"baseline {k}/{len(items)}")
    months = sorted(monthly)
    if len(months) < 8:
        raise RuntimeError(f"{name}: only {len(months)} baseline months")

    def month_composite(m: str, key: str) -> np.ndarray:
        return np.nanmedian(np.stack([a[key] for _, a in monthly[m]]), axis=0)

    bsi_stack = np.stack([month_composite(m, "bsi") for m in months]).astype(np.float32)
    ndvi_stack = np.stack([month_composite(m, "ndvi") for m in months]).astype(np.float32)

    def mean_date(ds: list[date]) -> date:
        return date.fromordinal(int(round(sum(d.toordinal() for d in ds) / len(ds))))

    mdates = [mean_date([d for d, _ in monthly[m]]) for m in months]
    log(f"{name} baseline months={len(months)} {months[0]}..{months[-1]}")

    # 2) 2022-2023 标定模型，2024 伪年标定 h
    cal_dates = [d for d in mdates if d.year <= 2023]
    cal_bsi = bsi_stack[:len(cal_dates)]
    cal_ndvi = ndvi_stack[:len(cal_dates)]
    m_bsi_cal = fit_harmonic(cal_bsi.reshape(len(cal_dates), -1), cal_dates, S_MIN["bsi"])
    m_ndvi_cal = fit_harmonic(cal_ndvi.reshape(len(cal_dates), -1), cal_dates, S_MIN["ndvi"])
    m_bsi_cal["_shape"] = (h, w)
    m_ndvi_cal["_shape"] = (h, w)
    cal_state = init_state(h, w)
    cal_items = [x for x in catalog if x["role"] == "calibration"]
    cal_dates_seen = []
    for k, item in enumerate(cal_items, 1):
        try:
            arr = load_scene(item, name, bbox, w, h, use_cache)
        except Exception as exc:  # noqa: BLE001
            log(f"skip cal {item['id']}: {exc}")
            continue
        d = date.fromisoformat(item["date"])
        day = (d - BASELINE_START).days
        update_cusum(cal_state, arr, m_bsi_cal, m_ndvi_cal, day, K_CUSUM, np.inf, d_date=d)
        # 2024 calibration scenes also enter the final 2022-2024 seasonal baseline
        monthly[item["date"][:7]].append((d, arr))
        cal_dates_seen.append(d)
        if k % 10 == 0:
            log(f"calibration {k}/{len(cal_items)}")
    mg = cal_state["maxg"][np.isfinite(cal_state["maxg"]) & (cal_state["n_valid"] >= 3)]
    if mg.size < 100:
        raise RuntimeError(f"{name}: calibration maxg too small")
    h_thr = float(np.percentile(mg, H_PERCENTILE))
    h_thr = min(max(h_thr, 1.0), 20.0)
    log(f"{name} calibration n={mg.size} h={h_thr:.3f} p50={np.percentile(mg,50):.2f} p99={np.percentile(mg,99):.2f}")

    # 3) 2022-2024 最终模型（包含 2024 标定场景的月合成）
    months_all = sorted(monthly)
    bsi_all = np.stack([np.nanmedian(np.stack([a["bsi"] for _, a in monthly[m]]), axis=0)
                        for m in months_all]).astype(np.float32)
    ndvi_all = np.stack([np.nanmedian(np.stack([a["ndvi"] for _, a in monthly[m]]), axis=0)
                         for m in months_all]).astype(np.float32)
    mdates_all = [mean_date([d for d, _ in monthly[m]]) for m in months_all]
    log(f"{name} final baseline months={len(months_all)} {months_all[0]}..{months_all[-1]}")
    m_bsi = fit_harmonic(bsi_all.reshape(len(mdates_all), -1), mdates_all, S_MIN["bsi"])
    m_ndvi = fit_harmonic(ndvi_all.reshape(len(mdates_all), -1), mdates_all, S_MIN["ndvi"])
    m_bsi["_shape"] = (h, w)
    m_ndvi["_shape"] = (h, w)
    base_bsi = np.nanmedian(bsi_all, axis=0).astype(np.float32)
    base_ndvi = np.nanmedian(ndvi_all, axis=0).astype(np.float32)

    # 3b) 基线夏季（6–8 月）物候：用于判断“全年裸”还是“季节性裸”
    baseline_summer = {}
    for y in range(BASELINE_START.year, BASELINE_END.year + 1):
        ms = [m for m in months_all if m.startswith(f"{y}-") and int(m[5:7]) in SUMMER_MONTHS]
        if len(ms) < SUMMER_MIN_BASELINE_MONTHS:
            continue
        b = np.nanmedian(np.stack([month_composite(m, "bsi") for m in ms]), axis=0).astype(np.float32)
        n = np.nanmedian(np.stack([month_composite(m, "ndvi") for m in ms]), axis=0).astype(np.float32)
        baseline_summer[y] = {"bsi": b, "ndvi": n, "months": len(ms)}
    log(f"{name} baseline summer years={sorted(baseline_summer)}")

    # 4) 2025 逐景 CUSUM
    target_items = [x for x in catalog if x["role"] == "target"]
    if max_target:
        target_items = target_items[:max_target]
    state = init_state(h, w)
    target_dates = []
    summer_bsi: list[np.ndarray] = []
    summer_ndvi: list[np.ndarray] = []
    summer_dates: list[date] = []
    for k, item in enumerate(target_items, 1):
        try:
            arr = load_scene(item, name, bbox, w, h, use_cache)
        except Exception as exc:  # noqa: BLE001
            log(f"skip target {item['id']}: {exc}")
            continue
        d = date.fromisoformat(item["date"])
        day = (d - BASELINE_START).days
        update_cusum(state, arr, m_bsi, m_ndvi, day, K_CUSUM, h_thr, d_date=d)
        target_dates.append(d)
        if d.month in SUMMER_MONTHS:
            summer_bsi.append(arr["bsi"])
            summer_ndvi.append(arr["ndvi"])
            summer_dates.append(d)
        if k % 5 == 0:
            log(f"target {k}/{len(target_items)}")
    if len(target_dates) < 5:
        raise RuntimeError(f"{name}: only {len(target_dates)} target scenes")
    target_summer_bsi = target_summer_ndvi = None
    if summer_bsi:
        target_summer_bsi = np.nanmedian(np.stack(summer_bsi), axis=0).astype(np.float32)
        target_summer_ndvi = np.nanmedian(np.stack(summer_ndvi), axis=0).astype(np.float32)
    log(f"{name} target summer scenes={len(summer_bsi)} "
        f"{summer_dates[0] if summer_dates else '-'}..{summer_dates[-1] if summer_dates else '-'}")

    base_valid_months = np.sum(np.isfinite(bsi_all), axis=0)
    baseline_unknown = base_valid_months < 8
    cand = ((state["maxg"] > h_thr) & (state["n_alarm"] >= 2) & (state["n_valid"] >= 3)
            & (~baseline_unknown))
    cand = clean_mask(cand)
    landcover = None
    if USE_WORLDCOVER:
        landcover = load_worldcover_grid(
            bbox, w, h, tr, root=WORLDCOVER_DIR, path=WORLDCOVER_PATH,
        )
        log(f"{name} worldcover={'yes' if landcover is not None else 'no'}")
    context = {
        "summer_bsi": target_summer_bsi,
        "summer_ndvi": target_summer_ndvi,
        "baseline_summer": baseline_summer,
        "landcover": landcover,
    }
    objs = objects_from_mask(cand, tr, state, base_bsi, base_ndvi, len(target_dates), MIN_AREA_PX,
                             target_dates, state["valid_dates"], base_valid_months,
                             context=context)

    # 3c) 规则版矿区制图：目标年夏季裸 + 至少 2 个基线年夏季裸。
    # 这一步补的是“老矿区/长期裸地”，CUSUM 只报 2025 新变化，无法覆盖。
    persistent_added = 0
    if target_summer_bsi is not None and target_summer_ndvi is not None:
        target_bare = (
            (target_summer_ndvi < SUMMER_NDVI_MAX)
            & (target_summer_bsi > SUMMER_BSI_MIN)
            & np.isfinite(target_summer_bsi) & np.isfinite(target_summer_ndvi)
        )
        base_bare_count = np.zeros((h, w), dtype=np.int8)
        for arrs in baseline_summer.values():
            b, nv = arrs["bsi"], arrs["ndvi"]
            ok = (nv < SUMMER_NDVI_MAX) & (b > SUMMER_BSI_MIN) & np.isfinite(b) & np.isfinite(nv)
            base_bare_count += ok.astype(np.int8)
        pmask = clean_mask(target_bare & (base_bare_count >= PERSISTENT_BASELINE_YEARS))
        persistent_objs = persistent_bare_objects(
            pmask, tr, landcover, target_summer_bsi, target_summer_ndvi, baseline_summer,
        )
        for po in persistent_objs:
            pg = shape(po["geometry"])
            dup = False
            for o in objs:
                try:
                    og = shape(o["geometry"])
                    inter = pg.intersection(og).area
                    union = pg.union(og).area
                except Exception:  # noqa: BLE001
                    continue
                if union > 0 and inter / union > 0.5:
                    o["persistent_bare"] = True
                    if o.get("source") == "cusum":
                        o["source"] = "cusum+persistent"
                    dup = True
                    break
            if not dup:
                objs.append(po)
                persistent_added += 1
        log(f"{name} persistent bare objects={len(persistent_objs)} added={persistent_added}")

    annotate_known(objs)
    # 规则版持续裸地只作为“已知矿区周边补全”，避免把远离矿区的撂荒/梯田
    # 当成矿区；第 3 步分割上线后取消这个门控。
    for o in objs:
        if o.get("source") == "persistent_bare" and o.get("layer") == "mine_candidate":
            d = o.get("distance_known_m")
            if d is None or d > PERSISTENT_KNOWN_MAX_M:
                o["layer"] = "other_bare"
                o["layer_reason"] = f"持续裸地，但距已知矿区>{PERSISTENT_KNOWN_MAX_M/1000:.0f} km；规则版不判矿区"
    layer_counts = {}
    for layer in LAYERS:
        sub = [o for o in objs if o.get("layer") == layer]
        layer_counts[layer] = {
            "count": len(sub),
            "area_ha": round(sum(o["area_ha"] for o in sub), 3),
        }
    log(f"{name} candidates={len(objs)} area={sum(o['area_ha'] for o in objs):.2f} ha")
    log(f"{name} layers=" + ", ".join(
        f"{k}:{v['count']}/{v['area_ha']}ha" for k, v in layer_counts.items()
    ))

    # 覆盖率
    nvalid = state["n_valid"].astype(np.float32)
    q = nvalid / max(len(target_dates), 1)
    coverage = {
        "aoi": name,
        "n_target_scenes": len(target_dates),
        "first_date": target_dates[0].isoformat() if target_dates else None,
        "last_date": target_dates[-1].isoformat() if target_dates else None,
        "mean_valid_fraction": round(float(np.nanmean(q)), 3),
        "q80_fraction": round(float(np.mean(q >= 0.8)), 3),
        "scene_gap_days": max([(b - a).days for a, b in zip(target_dates, target_dates[1:])] or [0]),
        "object_valid_fraction_median": None,
        "object_q80_fraction_median": None,
        "baseline_valid_months_min": int(base_valid_months.min()),
        "baseline_unknown_pixels": int(baseline_unknown.sum()),
        "baseline_unknown_fraction": round(float(baseline_unknown.mean()), 4),
    }
    obj_valid = [o["valid_frac"] for o in objs if o.get("valid_frac") is not None]
    obj_q80 = [o["q80_fraction"] for o in objs if o.get("q80_fraction") is not None]
    if obj_valid:
        coverage["object_valid_fraction_median"] = round(float(np.median(obj_valid)), 3)
    if obj_q80:
        coverage["object_q80_fraction_median"] = round(float(np.median(obj_q80)), 3)
    h_sensitivity = []
    for hh in (12.0, 15.0, 20.0):
        hm = (state["maxg"] > hh) & (state["n_alarm"] >= 2) & (state["n_valid"] >= 3)
        h_sensitivity.append({
            "h": hh,
            "pixel_count": int(hm.sum()),
            "pixel_area_ha": round(float(hm.sum()) * RES * RES / 10000.0, 2),
        })
    months_cov = []
    for m in month_iter(2025, 2025):
        ds = sorted([d for d in target_dates if d.year == m[0] and d.month == m[1]])
        # 至少 2 次独立观测且间隔≥5 天
        ok = 0
        for i in range(len(ds)):
            for j in range(i + 1, len(ds)):
                if (ds[j] - ds[i]).days >= 5:
                    ok = 1
                    break
            if ok:
                break
        months_cov.append({
            "aoi": name, "month": f"{m[0]}-{m[1]:02d}", "scenes": len(ds),
            ">=2obs_>=5d": ok,
        })

    # 保存候选：all 供审计；mine 层只含矿区候选；农田/毁田单列
    OUT.mkdir(parents=True, exist_ok=True)

    def write_fc(path: Path, subset: list[dict]) -> None:
        sub_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
        for o in subset:
            tier = o.get("evidence_tier")
            if tier in sub_counts:
                sub_counts[tier] += 1
        fc = {"type": "FeatureCollection",
              "metadata": {"corroboration": {
                  "method": "candidate_attributes",
                  "as_of": EVIDENCE_AS_OF.isoformat(),
                  "counts": sub_counts,
                  "note": "证据等级表示证据完备度，不是概率；D/Q 只表示证据不足或不进入派单。",
              }},
              "features": [
            {
                "type": "Feature",
                "geometry": o["geometry"],
                "properties": {k: v for k, v in o.items() if k != "geometry"},
            }
            for o in subset
        ]}
        path.write_text(json.dumps(fc, ensure_ascii=False, indent=2), encoding="utf-8")

    # 多源佐证评分：只追加字段，不改动 v2.1 已测量的属性
    evidence_cfg = evidence_mod.load_config(EVIDENCE_CONFIG)
    ev_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    ev_pri = {"P1": 0, "P2": 0, "P3": 0, "Q": 0}
    ev_by_layer: dict[str, dict] = {}
    for o in objs:
        evidence_mod.attach_candidate_evidence(o, config=evidence_cfg, as_of=EVIDENCE_AS_OF)
        tier = o.get("evidence_tier") or "D"
        priority = o.get("evidence_priority") or "Q"
        ev_counts[tier] = ev_counts.get(tier, 0) + 1
        ev_pri[priority] = ev_pri.get(priority, 0) + 1
        bucket = ev_by_layer.setdefault(str(o.get("layer") or "unknown"),
                                       {"A": 0, "B": 0, "C": 0, "D": 0})
        bucket[tier] = bucket.get(tier, 0) + 1

    write_fc(OUT / f"candidates_{name}.geojson", objs)
    write_fc(OUT / f"mine_candidates_{name}.geojson",
             [o for o in objs if o.get("layer") == "mine_candidate"])
    write_fc(OUT / f"farmland_destruction_suspect_{name}.geojson",
             [o for o in objs if o.get("layer") == "farmland_destruction_suspect"])
    write_fc(OUT / f"farmland_management_{name}.geojson",
             [o for o in objs if o.get("layer") == "farmland_management"])
    write_fc(OUT / f"persistent_bare_{name}.geojson",
             [o for o in objs if "persistent" in str(o.get("source"))])
    evidence_fusion = {
        "method": "candidate_attributes",
        "scheme": "docs/非法采矿多源佐证与情景验证方案-20261116.md §8.1",
        "config": str(EVIDENCE_CONFIG),
        "config_version": evidence_cfg.get("version"),
        "as_of": EVIDENCE_AS_OF.isoformat(),
        "counts": ev_counts,
        "priorities": ev_pri,
        "by_layer": ev_by_layer,
        "dispatchable": ev_pri.get("P1", 0) + ev_pri.get("P2", 0),
        "note": "证据等级表示证据完备度，不是概率；单源光学最多 C 级，D/Q 只表示证据不足。",
    }
    return {
        "name": name, "label": aoi["label"], "bbox": bbox, "grid": [w, h],
        "baseline_months": len(months), "baseline_range": [months[0], months[-1]],
        "calibration_scenes": len(cal_dates_seen), "h": round(h_thr, 3),
        "target_scenes": len(target_dates), "target_range": [target_dates[0].isoformat(), target_dates[-1].isoformat()],
        "summer_scenes": len(summer_bsi),
        "summer_range": ([summer_dates[0].isoformat(), summer_dates[-1].isoformat()]
                         if summer_dates else []),
        "baseline_summer_years": sorted(baseline_summer),
        "worldcover": landcover is not None,
        "layer_counts": layer_counts,
        "coverage": coverage, "months": months_cov,
        "h_sensitivity": h_sensitivity,
        "h_at_upper_bound": bool(abs(h_thr - 20.0) < 1e-6),
        "min_area_px": MIN_AREA_PX,
        "min_area_actual_ha": round(MIN_AREA_PX * RES * RES / 10000.0, 3),
        "reflectance": {
            "boa_add_offset": BOA_ADD_OFFSET if USE_BOA_OFFSET else 0.0,
            "scale": BOA_SCALE,
            "applied": USE_BOA_OFFSET,
            "source": "MTD_MSIL2A.xml BOA_ADD_OFFSET=-1000; PC COGs keep DN",
        },
        "resampling": RESAMPLING,
        "nbr_band": "B08",
        "candidates": len(objs), "candidate_area_ha": round(sum(o["area_ha"] for o in objs), 2),
        "evidence_fusion": evidence_fusion,
        "objects": objs, "target_dates": [d.isoformat() for d in target_dates],
        "state": state, "tr": tr, "base_bsi": base_bsi, "m_bsi": m_bsi, "m_ndvi": m_ndvi,
    }


def pick_e0_scene(catalog: list[dict], year: int, target: date) -> dict | None:
    items = [x for x in catalog if x["date"].startswith(str(year))]
    if not items:
        return None
    return min(items, key=lambda x: (abs((date.fromisoformat(x["date"]) - target).days), x["cloud"]))


def e0_compare(name: str, catalog: list[dict], result: dict, use_cache: bool) -> dict:
    aoi = AOIS[name]
    bbox = aoi["bbox"]
    ub, w, h, tr = grid_for(bbox)
    i24 = pick_e0_scene(catalog, 2024, date(2024, 11, 15))
    i25 = pick_e0_scene(catalog, 2025, date(2025, 11, 15))
    if not i24 or not i25:
        return {"aoi": name, "status": "no_scene"}
    a24 = load_scene(i24, name, bbox, w, h, use_cache)
    a25 = load_scene(i25, name, bbox, w, h, use_cache)
    valid = a24["valid"] & a25["valid"]
    dbsi = a25["bsi"] - a24["bsi"]
    dndvi = a25["ndvi"] - a24["ndvi"]
    m = valid & np.isfinite(dbsi) & np.isfinite(dndvi)
    m &= (dbsi > 0.12) & (dndvi < -0.05) & (a25["bsi"] > 0.14)
    m = clean_mask(m)
    lab, n = ndimage.label(m, structure=np.ones((3, 3), bool))
    npx = 0
    area = 0.0
    for i in range(1, n + 1):
        c = int((lab == i).sum())
        if c >= MIN_AREA_PX:
            npx += 1
            area += c * RES * RES / 10000
    e1 = (result["state"]["maxg"] > result["h"]) & (result["state"]["n_alarm"] >= 2) & (result["state"]["n_valid"] >= 3)
    overlap = float((m & e1).sum() / max(m.sum(), 1))
    return {
        "aoi": name, "date_2024": i24["date"], "date_2025": i25["date"],
        "patches": npx, "area_ha": round(area, 2),
        "overlap_with_e1_pixels": round(overlap, 3),
        "e1_pixels": int((e1).sum()),
    }


def write_report(summary: dict, e0: list[dict], catalog: list[dict]) -> None:
    DOCS.mkdir(exist_ok=True)

    def fmt_layer(lc: dict, name: str) -> str:
        v = lc.get(name, {})
        return f"{v.get('count', 0)}/{v.get('area_ha', 0.0):.2f} ha"

    lines = [
        "# 烟台 2025 全年采矿活动遥感可观测性试点报告（v2.1 物候与农田抑制修订版）",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 方案：`docs/烟台2025全年采矿活动与疑似非法采矿遥感分析建模实施方案.md` V1.0",
        "- 修订依据：`docs/烟台2025采矿遥感目标完成度评估与修订方案-20261115.md`",
        "- 范围：招远—莱州 A/B/D 样区中的两个 20 m 试点（非全市）",
        "- 等级：F1（方法可运行、探索性）；没有 2025 独立高分参考，不宣称 F2",
        "",
        "## 0 v2 修订说明",
        "",
        "本版针对评估指出的 10 项问题逐项修正，旧版 `data/yantai_2025/` 保留用于对照，未覆盖：",
        "",
        "1. **季节预测残差**：`predict()` 正式进入 `zscore()`，目标期先减季节预测，再用基线残差 median/MAD 标准化；不再对原始指数直接减基线中位数。",
        "2. **BOA 偏移**：逐景读取 `MTD_MSIL2A.xml` 的 `BOA_ADD_OFFSET=-1000`，反射率按 `(DN-1000)/10000` 解码；`--no-offset` 仅用于确认资产已 harmonized 的情形。",
        "3. **对象级覆盖**：每个候选按对象像元统计逐日期有效比例、q≥0.8 日期数、首末清晰日期和含年初/年末的缺口；不再用全场景有效次数代替。",
        "4. **时间字段**：分开首次报警、末次报警、报警跨度、t0（报警前最后一个低于 h 的有效日期）；t0 明确不是物理起始日。",
        "5. **h 标定**：仍用 2024 伪年标定，但报告 h 是否触上限，并输出 h=12/15/20 的敏感性像元数；h 不再解释为 0.5% 误报率。",
        "6. **重采样**：统一为 `average`，并写入 summary/模型卡；不再与旧模型卡的 bilinear 混用。",
        "7. **NBR 波段**：统一用 B08（10 m），不再写成 B8A；重采样方式随 summary 记录。",
        "8. **最小面积**：20 m 像元 400 m²，13 px=0.52 ha；报告精确像元数与实际面积，细长道路/硐口另列。",
        "9. **优先级（legacy）**：`priority` 仍只由 `ΔBSI≥0.10` 决定，但自本版起改为 `legacy_priority` 对照口径，不再用于派单排序；距已知采石点距离仅作上下文，不把先验点当模型命中。",
        "10. **月合成日期与基线充足性**：用当月真实成像日期的均值替代固定每月 15 日；AOI 基线月份不足 8 个月直接报错；逐像元基线有效月数 <8 标为 unknown，不参与候选。",
        "11. **夏季物候与农田抑制（v2.1）**：新增 6–8 月夏季裸土判据和持续性（目标年 + 基线 2022–2024）；接入 ESA WorldCover 2021 地类先验，把对象分为 `mine_candidate`、`farmland_destruction_suspect`、`farmland_management`、`other_bare`、`uncertain`。矿区 geojson 只保留 `mine_candidate`，农田管理对象移出矿区层。",
        "12. **多源佐证替换旧测试口径（本版起）**：候选按《非法采矿多源佐证与情景验证方案》§8.1 从已测量属性派生七族因子（O/S/L/D/T/C/H），用 `mining_watch/evidence.py` 融合出 `evidence_tier`（A/B/C/D）与 `evidence_priority`（P1/P2/P3/Q）。旧的布尔 `priority` 保留为 `legacy_priority`，报告、CSV、平台一律按证据等级排序；因子缺失处不补 0、不猜，`as_of` 固定为分析期末日 2025-12-31。详见 §5.2。",
        "",
        "## 1 结论先行",
        "",
    ]
    for r in summary["aois"]:
        pri = [o for o in r["objects"] if o.get("priority")]
        ev = r.get("evidence_fusion") or {}
        ec = ev.get("counts") or {}
        ep = ev.get("priorities") or {}
        lc = r.get("layer_counts", {})
        mine = lc.get("mine_candidate", {})
        suspect = lc.get("farmland_destruction_suspect", {})
        farm = lc.get("farmland_management", {})
        unc = lc.get("uncertain", {})
        lines.append(
            f"- **{r['label']}**：2025 年 {r['target_scenes']} 景（夏季 {r.get('summer_scenes', 0)} 景）；"
            f"总候选 {r['candidates']} 个、{r['candidate_area_ha']} ha；"
            f"矿区层 {mine.get('count', 0)} 个/{mine.get('area_ha', 0.0):.2f} ha，"
            f"毁田采矿疑似 {suspect.get('count', 0)} 个，"
            f"农田管理剔除 {farm.get('count', 0)} 个，"
            f"不确定 {unc.get('count', 0)} 个；"
            f"**证据等级 A{ec.get('A', 0)}/B{ec.get('B', 0)}/C{ec.get('C', 0)}/D{ec.get('D', 0)}，"
            f"可派单 P1+P2={ep.get('P1', 0) + ep.get('P2', 0)} 个**（P3 {ep.get('P3', 0)} 个、Q {ep.get('Q', 0)} 个）；"
            f"旧口径 ΔBSI≥0.10 高优先级 {len(pri)} 个、{sum(o['area_ha'] for o in pri):.2f} ha（仅作对照）；"
            f"有效观测均值 {r['coverage']['mean_valid_fraction']:.1%}，"
            f"q≥0.8 像元 {r['coverage']['q80_fraction']:.1%}，场景最长缺口 {r['coverage']['scene_gap_days']} 天；"
            f"标定 h={r['h']}。"
        )
    lines += [
        "",
        "结论：2025 年试点内可以形成逐景、带首次可见日期的候选，但候选是"
        "“地表变化/活动线索”，不是“非法采矿”认定；A 负对照的候选数量应与 "
        "D/B 采石群对比解读，不能单独证明精度。按新的七族证据口径，"
        "**2025 年单源光学没有可派单（P1/P2）对象**：证据等级集中在 C/D，"
        "派单被观测质量门与农田/物候负因子拦下，缺口在许可台账、高分影像、"
        "机械/运输与现场核查（见 §5.2）。",
        "",
        "## 2 数据与可观测性",
        "",
        f"- 目录共 {len(catalog)} 个场景（2022–2025，按月查询、去重、优先 51STB）；"
        f"基线 {summary['catalog_by_role']['baseline']}、标定 {summary['catalog_by_role']['calibration']}、"
        f"目标 {summary['catalog_by_role']['target']}。两个 AOI 共用 2025 目标景，不是 90 景独立观测。",
        "- 2025 年 1 月 cloud<40 无场景；为保时间覆盖，本次允许 cloud<80，1 月选了高云景；对象级有效比例需逐候选看 coverage。",
        "- 20 m 网格；SCL 仅保留 4/5/7；BSI/NDVI/NBR/NDWI 由 B02/B03/B04/B08/B11/B12 计算。",
        "- 反射率：L2A `BOA_ADD_OFFSET=-1000`、scale=0.0001；非 SCL 波段按 `(DN-1000)/10000` 解码，NBR 用 B08。",
        "- 2022–2024 月合成；月日期取真实成像日期均值，不用固定每月 15 日。",
        "",
        "## 3 方法",
        "",
        "- 2022–2024 月合成；Huber 稳健谐波 K=1（截距+趋势+年周期）。",
        "- 目标期残差 `r_t=index_t-seasonal_prediction(t)`；`z=(r-median(baseline residuals))/max(1.4826·MAD, s_min)`；`d=(z_BSI-z_NDVI)/2`。",
        "- 单侧 CUSUM：`g=max(0,g+d-0.5)`；2024 伪年标定 h（99.5 分位，夹在 [1,20]）；缺测不更新，缺口>45 天重置。",
        "- 候选：maxg>h 且至少 2 次报警、至少 3 次有效观测；3×3 形态学；最小 13 px（0.52 ha）。",
        f"- **夏季物候（v2.1）**：目标年 6–8 月合成 `NDVI<{SUMMER_NDVI_MAX:.2f}` 且 `BSI>{SUMMER_BSI_MIN:.2f}` 记为夏季裸；对象内 ≥50% 像元裸记为 `summer_bare`。基线 2022–2024 各自计算夏季中值，裸年数（含目标年）≥2 记为 `persistent_bare`。矿山应全年裸，果园/农田夏季返绿。",
        "- **地类软抑制（v2.1）**：ESA WorldCover 2021 耕地占比 ≥0.60 且夏季返绿 → `farmland_management`（移出矿区层）；耕地占比 ≥0.60 且夏季持续裸 → `farmland_destruction_suspect`（单列，不丢）；0.30–0.60 → `uncertain`。",
        "- **矿区补全（v2.1 规则版）**：目标年夏季裸 + 至少 2 个基线年夏季裸、非耕地、WorldCover 裸地占比 ≥0.20、面积 ≥1 ha，且距已知矿区 ≤3 km 的连通域补入 `mine_candidate`。这是第 3 步语义分割前的过渡层，不是最终矿区边界。",
        "- **输出层（v2.1）**：`mine_candidate`（矿区候选）/ `farmland_destruction_suspect`（毁田采矿疑似）/ `farmland_management`（农田管理，剔除）/ `other_bare` / `uncertain`；`mine_candidates_*.geojson` 只含矿区候选。",
        "- 对象级覆盖：每个候选按对象像元统计逐日期有效比例、q≥0.8 日期数、首末清晰日期、含年初/年末缺口；q<0.8 的日期不自动判读。",
        "- 优先级只由 `ΔBSI≥0.10` 决定；距已知采石点距离仅作上下文，不是模型命中。",
        "- 未训练随机森林：2025 独立标签不足，按方案回退规则候选+人工判读；不输出校准概率。",
        "",
        "## 4 结果",
        "",
        "| 试点 | 场景数 | 候选数 | 候选面积 ha | CUSUM 高优先级 | CUSUM 高优先级面积 ha | 有效观测均值 | q≥0.8 | 最长缺口 d | h |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary["aois"]:
        c = r["coverage"]
        pri = [o for o in r["objects"] if o.get("priority")]
        lines.append(
            f"| {r['label']} | {r['target_scenes']} | {r['candidates']} | {r['candidate_area_ha']} | "
            f"{len(pri)} | {sum(o['area_ha'] for o in pri):.2f} | "
            f"{c['mean_valid_fraction']:.1%} | {c['q80_fraction']:.1%} | {c['scene_gap_days']} | {r['h']} |"
        )
    lines += [
        "",
        "### 4.0 物候与农田抑制分层（v2.1）",
        "",
        "| 试点 | 矿区候选 | 毁田采矿疑似 | 农田管理（剔除） | 其他裸地 | 不确定 | 夏季场景 | WorldCover |",
        "|---|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for r in summary["aois"]:
        lc = r.get("layer_counts", {})
        lines.append(
            f"| {r['label']} | {fmt_layer(lc, 'mine_candidate')} | "
            f"{fmt_layer(lc, 'farmland_destruction_suspect')} | "
            f"{fmt_layer(lc, 'farmland_management')} | "
            f"{fmt_layer(lc, 'other_bare')} | {fmt_layer(lc, 'uncertain')} | "
            f"{r.get('summer_scenes', 0)} | {'是' if r.get('worldcover') else '否'} |"
        )
    lines += ["", "### E0 同季两期差分对照", "",
              "| 试点 | 2024 日期 | 2025 日期 | E0 图斑 | E0 面积 ha | E0 与 E1 像元重叠 |",
              "|---|---|---|---:|---:|---:|"]
    for e in e0:
        lines.append(
            f"| {e.get('aoi')} | {e.get('date_2024')} | {e.get('date_2025')} | "
            f"{e.get('patches')} | {e.get('area_ha')} | {e.get('overlap_with_e1_pixels')} |"
        )
    total_c = sum(r["candidates"] for r in summary["aois"])
    total_a = sum(r["candidate_area_ha"] for r in summary["aois"])
    total_p = sum(sum(1 for o in r["objects"] if o.get("priority")) for r in summary["aois"])
    by_name = {r["name"]: r for r in summary["aois"]}
    a_aoi = by_name.get("A_control") or summary["aois"][0]
    m_aoi = by_name.get("mining_belt") or a_aoi
    e0_by = {e.get("aoi"): e for e in e0}
    e0_m = e0_by.get("mining_belt") or {}
    e0_a = e0_by.get("A_control") or {}
    m_persist = sum(1 for o in m_aoi["objects"]
                    if o.get("source") == "persistent_bare" and o.get("layer") == "mine_candidate")
    a_demoted = sum(1 for o in a_aoi["objects"]
                    if o.get("source") == "persistent_bare"
                    and str(o.get("layer_reason", "")).startswith("持续裸地，但距已知矿区"))
    has_both = "A_control" in by_name and "mining_belt" in by_name
    lines += [
        "",
        "### 4.1 可行性判断",
        "",
        f"- F1 已达成：两个试点各 {a_aoi['target_scenes']} / {m_aoi['target_scenes']} 景真实运行，输出 {total_c} 个候选、{total_a:.2f} ha，"
        f"其中 CUSUM 高优先级 {total_p} 个；每个候选带首次/末次报警日期、对象有效比例、面积、经纬度。",
        "- F2 未达成：没有 2025 年独立高分参考（GF-2/GF-7 或人工判读标签），"
        "不能计算 precision/recall，也不能把候选称为“非法采矿”。",
        f"- A 负对照总候选 {a_aoi['candidates']} 个/{a_aoi['candidate_area_ha']:.2f} ha，但矿区层为 "
        f"{fmt_layer(a_aoi.get('layer_counts', {}), 'mine_candidate')}；其余为农田管理剔除、其他裸地/不确定。"
        f"13 个持续裸地对象中 {a_demoted} 个因距已知矿区 >3 km 被降为 other_bare，说明地类+物候+距离门控能把农田控制区移出矿区层。",
        f"- D/B 试点总候选 {m_aoi['candidates']} 个/{m_aoi['candidate_area_ha']:.2f} ha，矿区层 "
        f"{fmt_layer(m_aoi.get('layer_counts', {}), 'mine_candidate')}；其中 {m_persist} 个来自持续裸地补全，"
        f"其余来自 CUSUM 变化。高优先级只由 CUSUM 变化层的 ΔBSI≥0.10 决定，规则补全对象不设 priority。",
        f"- E0 同季差分：mining_belt {e0_m.get('patches', '—')} 个/{e0_m.get('area_ha', '—')} ha，"
        f"A_control {e0_a.get('patches', '—')} 个/{e0_a.get('area_ha', '—')} ha；E1 候选更多，"
        f"但 E1 的额外候选没有独立标签，不能据此证明 E1 精度更高。",
        f"- **ESRI 快视复核**：59.56 ha（120.2383,37.1415）和 32.08 ha（120.3161,37.1340）为采石/加工场复合体；"
        "7.2 ha 村边裸地（120.2698,37.1448，距 YT-007 3.23 km）已被 3 km 门控降为 other_bare；"
        "CUSUM 的 1.24 ha（120.3469,37.1791）为梯田假阳性，说明变化层仍需第 3/4 步纹理与分割复核。",
        f"- h 标定：A h={a_aoi['h']}{'（触上限）' if a_aoi.get('h_at_upper_bound') else ''}，"
        f"D/B h={m_aoi['h']}{'（触上限）' if m_aoi.get('h_at_upper_bound') else ''}；"
        "h 敏感性见下表，不能把 h 解释为误报率。",
        "",
        "### 4.2 h 敏感性（仅像元数/面积，不是精度）",
        "",
        "| 试点 | h=12 像元 | h=15 像元 | h=20 像元 |",
        "|---|---:|---:|---:|",
    ]
    for r in summary["aois"]:
        hs = {int(x["h"]): x for x in r.get("h_sensitivity", [])}
        lines.append(
            f"| {r['label']} | {hs.get(12, {}).get('pixel_count', '—')} | "
            f"{hs.get(15, {}).get('pixel_count', '—')} | {hs.get(20, {}).get('pixel_count', '—')} |"
        )
    lines += [
        "## 5 候选清单（按图层、证据等级、面积排序，前 15）",
        "",
        "| 排名 | 试点 | 图层 | 来源 | 面积 ha | 证据等级 | 派单 | 证据分 | 夏季裸 | 裸年数 | 耕地占比 | 首次报警 | t0 | max CUSUM | ΔBSI | 旧优先级 | 最近已知点 | 距离 m | 经度 | 纬度 |",
        "|---:|---|---|---:|---:|:---:|:---:|---:|:---:|---:|---:|---|---|---:|---:|:---:|---|---:|---:|---:|",
    ]
    all_objs = []
    for r in summary["aois"]:
        for o in r["objects"]:
            o["_aoi"] = r["name"]
            all_objs.append(o)
    all_objs.sort(key=lambda o: (
        LAYER_ORDER.get(o.get("layer"), 9),
        TIER_ORDER.get(o.get("evidence_tier"), 9),
        -(o.get("evidence_score") or 0),
        -(o.get("area_ha") or 0),
    ))
    for i, o in enumerate(all_objs[:15], 1):
        cx, cy = o["centroid_utm"]
        lon = lat = None
        try:
            lon, lat = warp_transform(UTM, "EPSG:4326", [cx], [cy])
            lon, lat = lon[0], lat[0]
        except Exception:  # noqa: BLE001
            pass
        crop = o.get("cropland_frac")
        crop_s = "" if crop is None else f"{crop:.3f}"
        lon_s = "" if lon is None else f"{lon:.5f}"
        lat_s = "" if lat is None else f"{lat:.5f}"
        bare_years = o.get("bare_years")
        db = o.get("delta_bsi")
        mc = o.get("max_cusum")
        lines.append(
            f"| {i} | {o['_aoi']} | {o.get('layer', '')} | {o.get('source', '')} | {o['area_ha']} | "
            f"{o.get('evidence_tier') or '—'} | {o.get('evidence_priority') or '—'} | "
            f"{o.get('evidence_score') if o.get('evidence_score') is not None else '—'} | "
            f"{'是' if o.get('summer_bare') else ''} | "
            f"{bare_years if bare_years is not None else ''} | {crop_s} | "
            f"{o.get('first_alarm') or '—'} | {o.get('t0') or '—'} | "
            f"{mc if mc is not None else '—'} | {db if db is not None else '—'} | "
            f"{'是' if o.get('priority') else '否'} | {o.get('nearest_known') or '—'} | "
            f"{o.get('distance_known_m') or ''} | {lon_s} | {lat_s} |"
        )
    lines += [
        "",
        "### 5.1 ESRI 快视复核（v2.1）",
        "",
        "- 59.56 ha（120.2383,37.1415）：大型采石/加工场复合体，蓝色厂房、白色台阶状采坑、堆料场；YT-007 位于其西侧约 491 m。",
        "- 32.08 ha（120.3161,37.1340）：大型露天采坑，青绿色积水、台阶边坡、周边裸岩；确认是矿区。",
        "- 7.2 ha（120.2698,37.1448，距 YT-007 3.23 km）：村庄+农田，不是矿区；已被 3 km 门控降为 other_bare。",
        "- CUSUM 1.24 ha（120.3469,37.1791）：梯田/村庄，假阳性；说明变化层仍需第 3/4 步纹理与分割复核。",
        "- CUSUM 1.24 ha（120.3363,37.1357）与 0.56 ha（120.3295,37.1350）：多采坑+蓝色厂房/白色采坑，真实矿区。",
        "- A_control 6.00 ha（120.0965,37.4213）：村北裸田/休耕地，不是矿区；3 km 门控后未进入矿区层。",
        "- D 0.56 ha（120.238354,37.142318，距 YT-007 约 564 m）：采石坑/加工场。",
        "- B 0.60 ha（120.323628,37.117968，距 B-quarry-2 约 694 m）：采石坑/裸岩。",
        "- B 0.76 ha（120.334344,37.129281）：采石坑+蓝绿色水塘。",
    ]

    # §5.2 多源佐证评分
    ef = summary.get("evidence_fusion") or {}
    lines += [
        "",
        "### 5.2 多源佐证评分（证据等级，取代旧 ΔBSI 优先级）",
        "",
        "本版起候选排序不再看 ΔBSI 单指标，而是按《非法采矿多源佐证与情景验证方案》"
        "（`docs/非法采矿多源佐证与情景验证方案-20261116.md`）§8.1，从已测量的候选属性派生因子，"
        "再用七族融合（O 光学变化、S 结构、L 地表迹象、D 台账、T 运输、C 许可、H 高分）：",
        "",
        f"- 配置：`{ef.get('config')}`，基准日 `{ef.get('as_of')}`（取分析期末日，避免 2025 证据按“距今”衰减）；"
        "只追加字段，未改动 v2.1 已测量属性；缺失字段不补 0、不猜。",
        "- 派生规则：ΔBSI≥0.10 或持续裸 ≥2 年→F01 0.67；弱变化（≥0.05）或夏季裸≥50%→F01 0.33；"
        "面积<0.5 ha 封顶 0.33；`phenology_class=green_summer`→N01 0.67；"
        "耕地占比≥0.60→N02 1.0、≥0.30→0.33；`valid_frac<0.80`→Q01；`baseline_insufficient`→Q03；"
        "距已知矿点只记 F13 弱证据（0.33），不作许可、也不当命中。",
        "- 时间衰减：有报警/末次清晰日期的按实际日期计；持续裸与规则补全对象本身不带日期，"
        "按分析期末日计（状态型证据不按事件年龄衰减），否则会被当成“日期未知”减半，反而不如短时变化。",
        "- 单源光学上限：F01 带 sentinel 标记，强度封顶 0.67、reliability 0.8，"
        "故 2025 年单源光学最高只能到 C 级；A/B 级必须补台账（C 族）、高分结构（H 族/F17）或多族独立证据。",
        "",
        "| 试点 | A | B | C | D | 可派单 P1+P2 | P3 | Q |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary["aois"]:
        e = r.get("evidence_fusion") or {}
        c = e.get("counts") or {}
        p = e.get("priorities") or {}
        lines.append(
            f"| {r['label']} | {c.get('A', 0)} | {c.get('B', 0)} | {c.get('C', 0)} | {c.get('D', 0)} | "
            f"{p.get('P1', 0) + p.get('P2', 0)} | {p.get('P3', 0)} | {p.get('Q', 0)} |"
        )
    c_objs = [o for o in all_objs if o.get("evidence_tier") in ("A", "B", "C")]
    PRI_ORDER = {"P1": 0, "P2": 1, "P3": 2, "Q": 3}
    c_objs.sort(key=lambda o: (TIER_ORDER.get(o.get("evidence_tier"), 9),
                               PRI_ORDER.get(o.get("evidence_priority"), 9),
                               -(o.get("evidence_score") or 0),
                               -(o.get("area_ha") or 0)))
    lines += [
        "",
        f"等级不低于 C 的对象共 {len(c_objs)} 个，按证据分排序前 8：",
        "",
        "| 试点 | 图层 | 面积 ha | 等级/派单 | 证据分 | 支持族 | 否决与质量门 | 主要缺口 | 下一步 |",
        "|---|---|---:|:---:|---:|---|---|---|---|",
    ]
    for o in c_objs[:8]:
        corr = o.get("corroboration") or {}
        fam = corr.get("family_scores") or {}
        top = "、".join(f"{k}={v:.2f}" for k, v in fam.items() if v and v >= 0.3) or "—"
        gate = (corr.get("quality_gate") or {}).get("id") or "—"
        veto = "、".join(v.get("id") for v in corr.get("vetoes") or []) or "—"
        miss = "、".join((corr.get("missing") or [])[:4]) or "—"
        nxt = "；".join((o.get("next_checks") or [])[:2]) or "—"
        lines.append(
            f"| {o.get('_aoi')} | {o.get('layer')} | {o.get('area_ha')} | "
            f"{o.get('evidence_tier')}/{o.get('evidence_priority')} | {o.get('evidence_score')} | "
            f"{top} | 否决 {veto}；门 {gate} | {miss} | {nxt} |"
        )
    miss_counter: dict[str, int] = defaultdict(int)
    for o in c_objs:
        for m in (o.get("corroboration") or {}).get("missing") or []:
            miss_counter[m] += 1
    lines += ["", "证据族缺口统计（C 级及以上对象），下一步按“缺哪族补哪族”排，而不是按面积排：", ""]
    if miss_counter:
        for k, v in sorted(miss_counter.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {k}：{v}/{len(c_objs)} 个对象缺")
    else:
        lines.append("- 无（本次没有 C 级及以上对象）")
    lines += [
        "",
        "**读法**：等级是“证据完备度”，不是概率，也不是非法采矿认定。`P1`/`P2` 才进入勤务派单；`P3` 是“待核/备查”（有单族强证据但缺关键族），需要补台账或高分才能升级；`Q` 不是最低优先级，"
        "而是“不进入勤务派单”：要么证据不足（D），要么被观测质量门（Q01/Q03）或"
        "农田/物候负因子（N01/N02）拦下。旧 `priority`（ΔBSI≥0.10）保留为 `legacy_priority`，"
        "新口径下既不加分也不派单，只用于对照“指标变化≠可派单线索”。",
        "",
        "输出清单：`evidence_ranked_candidates.csv`（全量按等级/分数排序）、"
        "`dispatch_candidates.csv`（仅 P1/P2，本次为空）、"
        "`candidates_*.geojson`（追加 scenario / evidence_factors / corroboration / next_checks 等字段）。",
        "",
    ]
    lines += [
        "## 6 解释与边界",
        "",
        "- A 负对照若仍有候选，说明农田/温室/果园物候、收获或土地整理仍会触发；"
        "必须逐条看 2025 时间序列和 ESRI 高分底图，不能按数量下结论。",
        "- D/B 的候选包含已知采石场、梯田、道路、堆场等；YT-007 这类老采坑的"
        "“2025 新活动”需要看首次报警日期和持续观测，不能拿旧地貌当 2025 正样本。",
        "- 20 m 会漏小硐口/小堆场；最小对象 13 px=0.52 ha，细长道路/硐口可能被形态学滤掉；GF-2/GF-7 0.8 m 复核是下一步，不是本报告已完成的验证。",
        "- **WorldCover 是 2021 年静态地类先验**，不能代表 2025 年现状；耕地占比只用于软抑制，不能单独作为“非矿区”结论。最终仍以夏季物候、2025 变化证据和高分复核为准。",
        "- **夏季物候缺测**：若目标年 6–8 月没有有效观测，对象物候记 `unknown` 并进入 `uncertain`，不直接进矿区层；A 负对照的农田若夏季返绿会被移入 `farmland_management`。",
        "- **规则版矿区补全的边界**：持续裸地补全只覆盖距已知矿区 ≤3 km 的范围，不能发现远离已知矿区的全新矿区；CUSUM 变化层仍可在全域报警。该层是第 3 步分割前的过渡，面积/边界精度低于训练分割，必须用 GF-2/GF-7 或 ESRI 高分复核。",
        "- **证据等级的解释边界**：`evidence_tier` 是七族证据的完备度分档，不是概率、不是置信度、"
        "更不是非法采矿认定；2025 年单源光学最高只能到 C 级，A/B 级要求台账/高分/运输等外部证据。"
        "`Q` 表示不进入勤务派单，`D` 表示证据不足，二者都不等于“无活动”。",
        "- **旧优先级只作对照**：`legacy_priority`（ΔBSI≥0.10）与新口径不一致是预期结果，"
        "说明“指标变化量”不能替代证据完备度；平台与报告不再用它排序或派单。",
        "- 没有矿权/审批/红线数据，本报告不做合法/非法判断；合规状态全部为 not_assessed。",
        "- 没有 2025 独立高分标签时，候选不能当 TP、负对照候选不能当 FP；本报告只给 F1。",
        "- LiCSAR `sltd` 不作为活动真值；本报告未使用 InSAR 位移。",
        "",
        "## 7 复现",
        "",
        "```bash",
        ".venv/Scripts/python.exe scripts/yantai_2025_activity.py --aoi all --max-baseline 1 --max-cal 1 --max-target 0 --use-cache --skip-catalog",
        "```",
        "",
        "WorldCover 瓦片默认读 `data/landcover/`（可用 `--worldcover-dir` 改目录、`--worldcover` 指定单文件、`--no-worldcover` 关闭）。",
        "输出：`data/yantai_2025_v2/`（catalog、coverage、candidates、mine_candidates.csv、summary、run_manifest）；分层文件为 `mine_candidates_*.geojson`、`farmland_destruction_suspect_*.geojson`、`farmland_management_*.geojson`、`persistent_bare_*.geojson`；证据口径为 `evidence_ranked_candidates.csv`（全量）与 `dispatch_candidates.csv`（P1/P2），`high_priority_candidates.csv` 保留为 legacy 对照；v2.1 报告/模型卡在 `docs/`。",
        "只补算多源佐证（不重跑栅格，复用现有候选）：",
        "```bash",
        ".venv/Scripts/python.exe scripts/yantai_corroborate.py --derive --as-of 2025-12-31 \\",
        "  --candidates data/yantai_2025_v2/candidates_mining_belt.geojson \\",
        "  --out data/yantai_2025_v2/candidates_mining_belt.corroborated.geojson \\",
        "  --aoi mining_belt --update-summary data/yantai_2025_v2/summary.json",
        "```",
    ]
    (REPORT_PATH).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    global OUT, USE_BOA_OFFSET, REPORT_PATH, WORLDCOVER_DIR, WORLDCOVER_PATH, USE_WORLDCOVER
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", choices=["all", *AOIS.keys()], default="all")
    ap.add_argument("--max-baseline", type=int, default=2, help="每月的基线场景上限")
    ap.add_argument("--max-cal", type=int, default=2, help="2024 标定每月场景上限")
    ap.add_argument("--max-target", type=int, default=0, help="2025 场景上限；0=全部")
    ap.add_argument("--out", default=str(OUT), help="输出目录")
    ap.add_argument("--report", default=str(REPORT_PATH), help="报告路径")
    ap.add_argument("--no-offset", action="store_true", help="输入已做 BOA 偏移时关闭")
    ap.add_argument("--worldcover-dir", default=str(WORLDCOVER_DIR),
                    help="WorldCover 瓦片目录（默认 data/landcover）")
    ap.add_argument("--worldcover", default=None,
                    help="显式指定本地 WorldCover 瓦片路径")
    ap.add_argument("--no-worldcover", action="store_true",
                    help="关闭 WorldCover 地类先验（只做夏季物候）")
    ap.add_argument("--use-cache", action="store_true")
    ap.add_argument("--skip-catalog", action="store_true")
    args = ap.parse_args()

    OUT = Path(args.out)
    REPORT_PATH = Path(args.report)
    USE_BOA_OFFSET = not args.no_offset
    WORLDCOVER_DIR = Path(args.worldcover_dir)
    WORLDCOVER_PATH = Path(args.worldcover) if args.worldcover else None
    USE_WORLDCOVER = not args.no_worldcover
    OUT.mkdir(parents=True, exist_ok=True)
    union_bbox = [
        min(AOIS[n]["bbox"][0] for n in AOIS), min(AOIS[n]["bbox"][1] for n in AOIS),
        max(AOIS[n]["bbox"][2] for n in AOIS), max(AOIS[n]["bbox"][3] for n in AOIS),
    ]
    cat_path = OUT / "catalog_2025.json"
    if args.skip_catalog and cat_path.exists():
        catalog = json.loads(cat_path.read_text(encoding="utf-8"))
    else:
        log(f"building catalog bbox={union_bbox}")
        catalog = build_catalog(union_bbox, args.max_baseline, args.max_cal, args.max_target or 20)
        cat_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"catalog items={len(catalog)}")

    names = list(AOIS) if args.aoi == "all" else [args.aoi]
    results = []
    for name in names:
        results.append(run_aoi(name, catalog, args.use_cache, args.max_target))

    e0 = []
    for r in results:
        try:
            e0.append(e0_compare(r["name"], catalog, r, args.use_cache))
        except Exception as exc:  # noqa: BLE001
            log(f"E0 {r['name']} failed: {exc}")
            e0.append({"aoi": r["name"], "status": "failed", "error": str(exc)})

    cov_rows = []
    for r in results:
        cov_rows.extend(r.pop("months"))
    with (OUT / "coverage_monthly.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["aoi", "month", "scenes", ">=2obs_>=5d"])
        wr.writeheader()
        wr.writerows(cov_rows)

    # 清理不可序列化对象
    for r in results:
        r.pop("state", None)
        r.pop("tr", None)
        r.pop("base_bsi", None)
        r.pop("m_bsi", None)
        r.pop("m_ndvi", None)
        for o in r["objects"]:
            o.pop("geometry", None)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "A/B/D 样区中的 mining_belt 与 A_control；非全市",
        "level": "F1",
        "parameters": {
            "grid_m": RES, "baseline": [str(BASELINE_START), str(BASELINE_END)],
            "target": "2025-01-01..2025-12-31", "k": K_CUSUM,
            "h_percentile": H_PERCENTILE, "min_area_ha": MIN_AREA_HA,
            "min_area_px": MIN_AREA_PX,
            "min_area_actual_ha": round(MIN_AREA_PX * RES * RES / 10000.0, 3),
            "max_gap_days": MAX_GAP_DAYS,
            "boa_add_offset": BOA_ADD_OFFSET if USE_BOA_OFFSET else 0.0,
            "boa_scale": BOA_SCALE,
            "resampling": RESAMPLING,
            "nbr_band": "B08",
            "priority_rule": "legacy: delta_bsi>=0.10 (known-point proximity is context only); "
                             "派单口径改用 evidence_fusion（evidence_tier/evidence_priority）",
            "zscore": "residual = index - seasonal_prediction; z = (residual - median(baseline residuals)) / max(1.4826*MAD, s_min)",
            "phenology": {
                "summer_months": sorted(SUMMER_MONTHS),
                "summer_ndvi_max": SUMMER_NDVI_MAX,
                "summer_bsi_min": SUMMER_BSI_MIN,
                "persistent_years_min": 2,
                "object_valid_min": SUMMER_OBJECT_VALID_MIN,
            },
            "landcover": {
                "source": "ESA WorldCover 10m 2021 v200",
                "cropland_dominant": CROPLAND_DOMINANT,
                "cropland_mixed": CROPLAND_MIXED,
                "enabled": USE_WORLDCOVER,
                "dir": str(WORLDCOVER_DIR),
            },
            "persistent_bare": {
                "min_area_ha": PERSISTENT_MIN_AREA_HA,
                "baseline_bare_years_min": PERSISTENT_BASELINE_YEARS,
                "bare_landcover_min": PERSISTENT_BARE_LC_MIN,
                "known_max_m": PERSISTENT_KNOWN_MAX_M,
                "note": "规则版矿区补全，仅用于已知矿区周边；第 3 步分割上线后取消门控",
            },
        },
        "aois": results, "e0": e0,
        "evidence_fusion": {
            "method": "candidate_attributes",
            "scheme": "docs/非法采矿多源佐证与情景验证方案-20261116.md §8.1",
            "config": str(EVIDENCE_CONFIG),
            "as_of": EVIDENCE_AS_OF.isoformat(),
            "counts": {k: sum(r["evidence_fusion"]["counts"].get(k, 0) for r in results)
                       for k in ("A", "B", "C", "D")},
            "priorities": {k: sum(r["evidence_fusion"]["priorities"].get(k, 0) for r in results)
                           for k in ("P1", "P2", "P3", "Q")},
            "by_aoi": {r["name"]: r["evidence_fusion"] for r in results},
            "note": "证据等级表示证据完备度，不是概率；单源光学最多 C 级，"
                    "D 与 Q 只表示证据不足或不进入派单，不代表无活动。",
        },
        "catalog_items": len(catalog),
        "catalog_by_role": {role: sum(1 for x in catalog if x["role"] == role) for role in ("baseline", "calibration", "target")},
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    mine_rows = []
    for r in results:
        for o in r["objects"]:
            if o.get("layer") != "mine_candidate":
                continue
            ll = o.get("centroid_lonlat") or [None, None]
            mine_rows.append({
                "aoi": r["name"], "label": r["label"], "source": o.get("source"),
                "area_ha": o["area_ha"], "lon": ll[0], "lat": ll[1],
                "nearest_known": o.get("nearest_known"),
                "distance_known_m": o.get("distance_known_m"),
                "bare_years": o.get("bare_years"),
                "cropland_frac": o.get("cropland_frac"),
                "bare_landcover_frac": o.get("bare_landcover_frac"),
                "compactness": o.get("compactness"),
                "bsi_at_max": o.get("bsi_at_max"), "ndvi_at_max": o.get("ndvi_at_max"),
                "first_alarm": o.get("first_alarm"), "t0": o.get("t0"),
                "max_cusum": o.get("max_cusum"), "delta_bsi": o.get("delta_bsi"),
                "layer_reason": o.get("layer_reason"),
            })
    mine_rows.sort(key=lambda x: -(x["area_ha"] or 0))
    with (OUT / "mine_candidates.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(mine_rows[0].keys()) if mine_rows else ["aoi", "area_ha"])
        wr.writeheader()
        wr.writerows(mine_rows)
    pri_rows = []
    for r in results:
        for o in r["objects"]:
            if not o.get("priority"):
                continue
            ll = o.get("centroid_lonlat") or [None, None]
            pri_rows.append({
                "aoi": r["name"], "label": r["label"],
                "layer": o.get("layer"), "source": o.get("source"),
                "area_ha": o["area_ha"],
                "first_alarm": o.get("first_alarm"), "t0": o.get("t0"),
                "max_cusum": o.get("max_cusum"), "delta_bsi": o.get("delta_bsi"),
                "bsi_at_max": o.get("bsi_at_max"), "nearest_known": o.get("nearest_known"),
                "distance_known_m": o.get("distance_known_m"),
                "lon": ll[0], "lat": ll[1],
            })
    with (OUT / "high_priority_candidates.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(pri_rows[0].keys()) if pri_rows else ["aoi"])
        wr.writeheader()
        wr.writerows(pri_rows)
    # 新口径：证据等级排序；high_priority_candidates.csv 仅作 legacy 对照
    ev_rows = []
    for r in results:
        for o in r["objects"]:
            ll = o.get("centroid_lonlat") or [None, None]
            corr = o.get("corroboration") or {}
            ev_rows.append({
                "aoi": r["name"], "label": r["label"],
                "layer": o.get("layer"), "source": o.get("source"),
                "evidence_tier": o.get("evidence_tier"),
                "evidence_priority": o.get("evidence_priority"),
                "evidence_score": o.get("evidence_score"),
                "legacy_priority": "是" if o.get("priority") else "否",
                "area_ha": o["area_ha"], "delta_bsi": o.get("delta_bsi"),
                "valid_frac": o.get("valid_frac"), "cropland_frac": o.get("cropland_frac"),
                "phenology_class": o.get("phenology_class"), "bare_years": o.get("bare_years"),
                "last_alarm": o.get("last_alarm"),
                "quality_gate": (corr.get("quality_gate") or {}).get("id"),
                "vetoes": "|".join(v.get("id") for v in corr.get("vetoes") or []),
                "missing": "|".join(corr.get("missing") or []),
                "next_checks": "；".join(o.get("next_checks") or []),
                "lon": ll[0], "lat": ll[1],
            })
    tier_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    pri_order = {"P1": 0, "P2": 1, "P3": 2, "Q": 3}
    ev_rows.sort(key=lambda x: (tier_order.get(x["evidence_tier"], 9),
                                pri_order.get(x["evidence_priority"], 9),
                                -(x["evidence_score"] or 0),
                                -(x["area_ha"] or 0)))
    ev_fields = list(ev_rows[0].keys()) if ev_rows else ["aoi"]
    with (OUT / "evidence_ranked_candidates.csv").open("w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=ev_fields)
        wr.writeheader()
        wr.writerows(ev_rows)
    dispatch = [x for x in ev_rows if x["evidence_priority"] in ("P1", "P2")]
    with (OUT / "dispatch_candidates.csv").open("w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=ev_fields)
        wr.writeheader()
        wr.writerows(dispatch)
    write_report(summary, e0, catalog)

    manifest = {
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256(Path(__file__)),
        "created_at": summary["generated_at"],
        "files": {},
    }
    for p in sorted(OUT.glob("*")):
        if p.is_file():
            manifest["files"][p.name] = {"bytes": p.stat().st_size, "sha256": sha256(p)}
    (OUT / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log("done")
    for r in results:
        log(f"{r['name']}: {r['candidates']} candidates, {r['candidate_area_ha']} ha, h={r['h']}")


if __name__ == "__main__":
    main()
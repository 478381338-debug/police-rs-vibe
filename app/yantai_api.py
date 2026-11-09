"""烟台专题：矿产资源、河段与疑似采掘/采砂线索的展示与研判记录。

设计边界（与《烟台非法采矿与河道采砂真实预警实施方案》一致）：
1. 资源点只表示公开资料中的位置，不代表许可范围或违法状态；
2. 观测质量、活动判断、合规判断、处置状态分开保存，不生成“违法概率”；
3. 没有许可、监测或现场材料时写“待核/无法判断”，不用 0 代替缺失；
4. 当前只有 P07 一个 10 m 样区（120.185–120.245°E, 37.125–37.175°N），不能宣称全市实时监测；
5. 图斑展示几何为 WGS84（EPSG:4326），分析几何保留 EPSG:32651，面积按像元数计算；
6. 规则评分未校准，只用于排序，不称“置信度/概率”；
7. 样区一律用编号与经纬度范围表述，不使用未经行政区划核实的地名；
8. 原版、v2 小试、v2 全年是三次不同运行，必须分开陈述，不能合并成一组数字。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from rasterio.warp import transform, transform_bounds, transform_geom
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from app import case_api, config
from mining_watch import evidence as evidence_mod

router = APIRouter(prefix="/api/yantai", tags=["yantai"])

DATA_DIR = config.DATA / "yantai_rs"
INSTANCE = Path(config.ROOT / "instance")
REVIEW_PATH = INSTANCE / "yantai_reviews.json"
CHECK_PATH = INSTANCE / "yantai_check.json"
P07_DIR = DATA_DIR / "p07_2023b"

# 只有本地同时具备同范围基线/当前影像的样区才进入前端下拉；
# 其他分析结果没有成对影像，不能在这里拼出“变化图”。
_REGION_SPECS = [
    {
        "id": "p07_2023b",
        "dir": P07_DIR,
        "label": "P07 样区 · 跨三年",
        "role": "矿山集中区试点",
        "caveat": "两期差异，不是逐景持续监测；差异起始日未知，不能当作近期新增。",
    },
    {
        "id": "p07_recent",
        "dir": DATA_DIR / "p07",
        "label": "P07 样区 · 近一年",
        "role": "同区近期窗口",
        "caveat": "与跨三年结果同范围、不同窗口；近期 0 个候选不代表 2023—2026 的累积变化没有发生。",
    },
    {
        "id": "p01",
        "dir": DATA_DIR / "p01",
        "label": "P01 样区",
        "role": "年度同季对比",
        "caveat": "同季年度对比；只代表本次规则阈值的筛查结果。",
    },
]

# 三次运行必须分开：原版全年、v2 小试、v2 全年。
# 优先使用全年 v2；全年尚未写完时回退到小试，但页面必须标注当前展示的是哪一个。
ASSESS_ORIG_DIR = config.DATA / "yantai_2025"
ASSESS_TEST_DIR = config.DATA / "yantai_2025_v2_test"
ASSESS_DIR = config.DATA / "yantai_2025_v2"
ASSESS_FULL_DIR = ASSESS_DIR
ASSESS_REPORT = config.ROOT / "docs" / "烟台2025全年采矿活动遥感分析报告-v2.md"
ASSESS_TEST_REPORT = config.ROOT / "docs" / "_v2_test_report.md"
ASSESS_MODEL_CARD = config.ROOT / "docs" / "烟台2025试点模型卡-v2.md"

# 气候增强层：由 scripts/yantai_climate_pipeline.py 生成，独立于 v2 主结果。
CLIMATE_DIR = config.DATA / "yantai_climate"
CLIMATE_SUMMARY = CLIMATE_DIR / "summary.json"
CLIMATE_REPORT = CLIMATE_DIR / "summary.md"
CLIMATE_TYPES = CLIMATE_DIR / "type_methods.json"
CLIMATE_TIER_LABELS = {"A": "A 证据较全", "B": "B 多族支持",
                       "C": "C 单源/受限", "D": "D 证据不足"}

# The assessment's 10 findings and how the v2 run answers them. This is a
# method audit, not a legal conclusion.
ASSESSMENT_FIXES = [
    {"id": 1, "title": "季节预测未进入标准化",
     "before": "predict() 定义后未调用，zscore 用原始值减基线中位数",
     "after": "先算 r=index−seasonal_prediction(t)，再以基线残差的 median/MAD 标准化",
     "effect": "E1 不再把季节物候误当作活动；候选时间序列可解释"},
    {"id": 2, "title": "L2A 反射率偏移未核实",
     "before": "直接使用 PC COG DN，未确认 BOA_ADD_OFFSET",
     "after": "按产品 MTD_MSIL2A.xml 的 BOA_ADD_OFFSET=-1000 做 (DN−1000)/10000，并写入运行清单",
     "effect": "水体/裸地指数不再整体偏高；指数阈值可复现"},
    {"id": 3, "title": "覆盖率按场景而非对象",
     "before": "有效次数/目标景数；最长缺口只按景日期",
     "after": "每个候选对象算逐日期有效比例、q≥0.8 日期数、首尾缺口与对象级最长空窗",
     "effect": "能区分“场景有云”与“这个图斑没看清”"},
    {"id": 4, "title": "t0/首次报警/末次报警口径混用",
     "before": "last_alarm 取像元首次报警的最大值；t0 被当成事件起点",
     "after": "分开 first_alarm、last_alarm、alarm_span_days、t0(last below before alarm)，并标注 t0 不是物理起点",
     "effect": "候选带可核对的报警区间，不再把算法状态写成事件日期"},
    {"id": 5, "title": "h 标定不可比且触上限",
     "before": "12 景 2024 标定对 45 景 2025，h 99.5 分位夹在 [1,20]，A 触顶",
     "after": "保留 h 并报 h_sensitivity(12/15/20)、h_at_upper_bound；不把 h 称为误报率",
     "effect": "读者能看到阈值敏感性，不会把 20 当成 0.5% 误报率"},
    {"id": 6, "title": "重采样口径不一致",
     "before": "模型卡写双线性，脚本用 Resampling.average",
     "after": "统一为 average，并写入 summary 与模型卡",
     "effect": "方法描述与代码一致"},
    {"id": 7, "title": "NBR 波段口径不一致",
     "before": "方案写 B8A，脚本用 B08",
     "after": "明确 NBR 使用 B08(10 m)，并记录该偏差；不再声称使用 B8A",
     "effect": "波段与分辨率可追溯"},
    {"id": 8, "title": "最小面积表述不精确",
     "before": "MIN_AREA_HA=0.5，实际 13 px×400 m²=0.52 ha",
     "after": "输出 min_area_px=13、min_area_actual_ha=0.52，并在报告写明",
     "effect": "面积门槛不再被四舍五入成 0.5 ha"},
    {"id": 9, "title": "高优先级混入已知点邻近",
     "before": "priority = ΔBSI≥0.10 or 距已知采石点≤1 km",
     "after": "priority 只保留 ΔBSI≥0.10；near_known 仅作上下文展示，不参与排序",
     "effect": "不会把先验点位当成遥感命中"},
    {"id": 10, "title": "基线月合成日期固定 15 日、基线不足未标未知",
     "before": "每月合成强制记为 15 日；基线有效月数不足时仍可能给出候选",
     "after": "用该月实际成像日期均值；AOI 基线少于 8 个月报错；逐像元基线有效月数 <8 标 unknown，不参与候选",
     "effect": "季节相位更接近真实观测，背景不足区域不再被当作“无变化”或强行出候选"},
]

_LOCK = threading.Lock()
_REVIEW_ROLES = {"admin", "commander", "reviewer"}
_PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "Q": 3}

# 固定记录本次试点的产品与观测质量；2025 年严格阈值未检出，不等于没有活动。
OBSERVATIONS = [
    {
        "id": "OBS-2023-11-02",
        "role": "baseline",
        "acquired_at": "2023-11-02",
        "scene_id": "S2B_MSIL2A_20231102T024849_R132_T51STB_20231102T061854",
        "sensor": "Sentinel-2B MSI",
        "resolution_m": 10,
        "swir_m": 20,
        "cloud_pct": 0.01,
        "status": "有效",
        "quality_note": "基线期；同季、无雪；SCL 有效类 4/5/6/7",
    },
    {
        "id": "OBS-2025-11-21",
        "role": "mid",
        "acquired_at": "2025-11-21",
        "scene_id": "S2B_MSIL2A_20251121T024919_R132_T51STB_20251121T044654",
        "sensor": "Sentinel-2B MSI",
        "resolution_m": 10,
        "swir_m": 20,
        "cloud_pct": 0.022,
        "status": "有效；严格阈值未检出",
        "quality_note": "严格阈值未检出，不能据此证明 2025—2026 无活动；未做逐景连续性检验",
    },
    {
        "id": "OBS-2026-11-11",
        "role": "current",
        "acquired_at": "2026-11-11",
        "scene_id": "S2C_MSIL2A_20261111T024931_R132_T51STB_20261111T055708",
        "sensor": "Sentinel-2C MSI",
        "resolution_m": 10,
        "swir_m": 20,
        "cloud_pct": 1.543,
        "status": "有效",
        "quality_note": "当前期；分析窗口 549×571 像元，云/水掩膜后有效像元 306,024",
    },
]

RIVER_SEGMENTS = [
    {"id": "R-界河", "name": "界河", "length_km": 22.91, "segments": 1},
    {"id": "R-王河", "name": "王河", "length_km": 36.53, "segments": 1},
    {"id": "R-大沽夹河", "name": "大沽夹河", "length_km": 32.44, "segments": 1},
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _load_resources() -> list[dict]:
    return _read_json(DATA_DIR / "resources.json", {}).get("items", [])


def _load_rivers() -> list[dict]:
    return _read_json(DATA_DIR / "rivers.json", {}).get("items", [])


def _load_patches() -> list[dict]:
    return _read_json(P07_DIR / "patches.geojson", {}).get("features", [])


def _patch_map() -> dict[str, dict]:
    out = {}
    for feature in _load_patches():
        props = feature.get("properties") or {}
        if props.get("id"):
            out[props["id"]] = feature
    return out


def _summary() -> dict:
    return _read_json(P07_DIR / "summary.json", {})


def _bbox_of(features: list[dict]) -> list[float] | None:
    boxes = [(f.get("properties") or {}).get("bbox_wgs84") for f in features]
    boxes = [b for b in boxes if b and len(b) == 4]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _union_geometry(features: list[dict]) -> dict | None:
    geoms = [shape(f["geometry"]) for f in features if f.get("geometry")]
    if not geoms:
        return None
    return mapping(unary_union(geoms))


def _region_norm_ring(ring: list, ub) -> list:
    return [
        [round((x - ub[0]) / (ub[2] - ub[0]), 5),
         round((ub[3] - y) / (ub[3] - ub[1]), 5)]
        for x, y in ring
    ]


def _region_patch(feature: dict, ub) -> dict | None:
    props = feature.get("properties") or {}
    geom = props.get("geometry_utm")
    if geom is None and feature.get("geometry"):
        geom = transform_geom("EPSG:4326", "EPSG:32651", feature["geometry"])
    if not geom:
        return None
    polygons = geom.get("coordinates") or []
    if geom.get("type") == "Polygon":
        polygons = [polygons]
    rings = [_region_norm_ring(ring, ub) for poly in polygons for ring in poly]
    rings = [ring for ring in rings if len(ring) >= 3]
    if not rings:
        return None
    center = props.get("center_lonlat")
    center_norm = None
    if center and len(center) == 2:
        xs, ys = transform("EPSG:4326", "EPSG:32651", [center[0]], [center[1]])
        center_norm = [
            round((xs[0] - ub[0]) / (ub[2] - ub[0]), 5),
            round((ub[3] - ys[0]) / (ub[3] - ub[1]), 5),
        ]
    return {
        "id": props.get("id"),
        "area_ha": props.get("area_ha"),
        "pixel_count": props.get("pixel_count"),
        "center_lonlat": center,
        "center_norm": center_norm,
        "bbox_wgs84": props.get("bbox_wgs84"),
        "d_bsi": props.get("d_bsi"),
        "d_ndvi": props.get("d_ndvi"),
        "rule_score": props.get("rule_score"),
        "rings": rings,
    }


def _scene_date(value) -> str | None:
    """日期字段可能是 YYYY-MM-DD，也可能误写成整景产品 ID；两种都按真实内容解析。"""
    if not value:
        return None
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    match = re.search(r"(20\d{2})(\d{2})(\d{2})T", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


def _days_between(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except ValueError:
        return None


def _span(dates: dict) -> dict | None:
    """按真实观测日期算间隔；缺日期就返回 None，不用默认值补。"""
    days = _days_between(dates.get("base"), dates.get("now"))
    if days is None:
        return None
    return {
        "base": dates.get("base"),
        "now": dates.get("now"),
        "days": days,
        "years": round(days / 365.25, 2),
    }


def _region_record(spec: dict) -> dict | None:
    directory = spec["dir"]
    summary = _read_json(directory / "summary.json", {})
    bbox = summary.get("bbox_wgs84")
    if not summary or not bbox or len(bbox) != 4:
        return None
    if not (directory / "before.png").exists() or not (directory / "after.png").exists():
        return None
    patches_path = directory / "patches.geojson"
    if summary.get("patch_count") and not patches_path.exists():
        return None
    try:
        ub = transform_bounds("EPSG:4326", "EPSG:32651", *bbox)
    except Exception:
        return None
    width = max(1, round((ub[2] - ub[0]) / 10))
    height = max(1, round((ub[3] - ub[1]) / 10))
    patches = []
    for feature in _read_json(patches_path, {}).get("features", []):
        patch = _region_patch(feature, ub)
        if patch:
            patches.append(patch)
    valid = int(summary.get("valid_pixels") or 0)
    patch_area = round(sum((p.get("area_ha") or 0) for p in patches), 2)
    raw_dates = summary.get("dates") or {}
    items = summary.get("items") or {}
    dates = {
        "base": _scene_date(raw_dates.get("base")) or _scene_date(items.get("base")),
        "now": _scene_date(raw_dates.get("now")) or _scene_date(items.get("now")),
    }
    base = f"/api/yantai/asset/{spec['id']}"
    images = {"before": f"{base}/before.png", "after": f"{base}/after.png"}
    if (directory / "overlay.png").exists():
        images["overlay"] = f"{base}/overlay.png"
    return {
        "id": spec["id"],
        "label": spec["label"],
        "role": spec["role"],
        "caveat": spec["caveat"],
        "scope": summary.get("region"),
        "bbox_wgs84": bbox,
        "dates": dates,
        "span": _span(dates),
        "resolution_m": summary.get("resolution_m") or 10,
        "swir_m": 20,
        "algorithm": summary.get("algorithm"),
        "width": width,
        "height": height,
        "window_km2": round((ub[2] - ub[0]) * (ub[3] - ub[1]) / 1_000_000, 3),
        "valid_pixels": valid or None,
        "valid_ratio": round(valid / (width * height), 4) if valid else None,
        "patch_count": len(patches),
        "patch_area_ha": patch_area,
        "anomaly_area_ha": summary.get("anomaly_area_ha"),
        "rule_score_note": summary.get("rule_score_note"),
        "disclaimer": summary.get("disclaimer"),
        "zero_note": (
            "本次规则阈值下 0 个规则候选；0 不等于无采矿，未接入的观测不能当作排除依据。"
            if not patches else None
        ),
        "images": images,
        "patches": patches,
    }


def _regions() -> list[dict]:
    records = []
    for spec in _REGION_SPECS:
        record = _region_record(spec)
        if record:
            records.append(record)
    return records


def _analysis_sites() -> list[dict]:
    """已分析地点的统一清单：影像对样区 + 2025 全年时序 AOI。

    影像对样区可以进“同步影像精查”；年度时序 AOI 只有候选矢量，
    没有成对真彩影像，这里明确标注，不拼假影像。
    """
    sites = []
    for region in _regions():
        span = region.get("span") or {}
        sites.append({
            "id": region["id"],
            "place": region["label"],
            "role": region["role"],
            "method": "双时相影像对",
            "date_from": (region.get("dates") or {}).get("base"),
            "date_to": (region.get("dates") or {}).get("now"),
            "span_days": span.get("days"),
            "span_years": span.get("years"),
            "result_count": region.get("patch_count"),
            "result_area_ha": region.get("patch_area_ha"),
            "mine_candidate_count": None,
            "mine_candidate_area_ha": None,
            "region_id": region["id"],
            "bbox_wgs84": region.get("bbox_wgs84"),
            "note": region.get("zero_note") or region.get("caveat"),
        })
    summary = _read_json(ASSESS_FULL_DIR / "summary.json", {})
    for aoi in summary.get("aois") or []:
        bbox = aoi.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        target = aoi.get("target_range") or []
        start = target[0] if len(target) > 0 else None
        end = target[1] if len(target) > 1 else None
        mine = (aoi.get("layer_counts") or {}).get("mine_candidate") or {}
        days = _days_between(start, end)
        sites.append({
            "id": "annual_" + str(aoi.get("name")),
            "place": aoi.get("label") or aoi.get("name"),
            "role": "2025 全年时序",
            "method": "年度时序（无成对影像）",
            "date_from": start,
            "date_to": end,
            "span_days": days,
            "span_years": round(days / 365.25, 2) if days is not None else None,
            "result_count": aoi.get("candidates"),
            "result_area_ha": None,
            "mine_candidate_count": mine.get("count"),
            "mine_candidate_area_ha": mine.get("area_ha"),
            "region_id": None,
            "bbox_wgs84": bbox,
            "note": "2025 全年逐景时序分析；只有候选矢量，没有成对真彩影像，不能放进卷帘对比。",
        })
    return sites


def _review_store() -> dict:
    return _read_json(REVIEW_PATH, {})


def _review_entry(event_id: str) -> tuple[dict | None, list[dict]]:
    """兼容旧结构：旧版直接存一条意见；新版存 history/current。"""
    raw = _review_store().get(event_id)
    if not raw:
        return None, []
    if isinstance(raw, dict) and isinstance(raw.get("history"), list):
        return raw.get("current") or {}, raw["history"]
    return raw, [raw]


def _apply_review(event: dict) -> dict:
    current, history = _review_entry(event["id"])
    if not current:
        return event
    event = dict(event)
    event["review"] = current
    event["review_history"] = history
    event["lifecycle"] = current.get("lifecycle") or event.get("lifecycle")
    event["disposition_status"] = event["lifecycle"]
    if current.get("decision") == "非采掘变化":
        event["activity_status"] = "非采掘变化（已研判）"
    return event


def _events() -> list[dict]:
    patches = _patch_map()
    p7 = patches.get("YT-007", {})
    p7p = p7.get("properties") or {}
    cluster_ids = ("YT-001", "YT-002", "YT-003")
    cluster = [patches.get(i) for i in cluster_ids]
    cluster = [p for p in cluster if p]
    cluster_area = round(sum((p.get("properties") or {}).get("area_ha", 0) for p in cluster), 2)
    cluster_score = max(((p.get("properties") or {}).get("rule_score") or 0) for p in cluster) if cluster else None
    cluster_center = [120.222, 37.132]
    cluster_bbox = _bbox_of(cluster)
    cluster_geom = _union_geometry(cluster)
    p7_area = p7p.get("area_ha")
    p7_score = p7p.get("rule_score")

    events = [
        {
            "id": "YT-E-2026-001",
            "domain": "mining",
            "domain_label": "非采掘变化",
            "title": "P07 农田清表/裸化（非采掘变化）",
            "subtitle": f"YT-007 · {p7_area if p7_area is not None else '待重算'} 公顷 · 规则评分（未校准）",
            "activity_status": "非采掘变化（农田管理）",
            "compliance_status": "不适用",
            "disposition_status": "待排除",
            "priority": "Q",
            "legacy_priority": "P2",
            "lifecycle": "待排除",
            "area_ha": p7_area,
            "center_lonlat": p7p.get("center_lonlat"),
            "bbox": p7p.get("bbox_wgs84"),
            "geometry": p7.get("geometry"),
            "geometry_analysis": p7p.get("geometry_utm"),
            "crs_display": "EPSG:4326",
            "crs_analysis": "EPSG:32651",
            "observed_at": "2026-11-11",
            "baseline_at": "2023-11-02",
            "window": "2023-11-02 至 2026-11-11",
            "rule_score": p7_score,
            "score_label": "规则评分（未校准）",
            "score_note": "由面积、dBSI 和常数构成，未做概率校准；只用于排序，不代表违法概率。",
            "rule": (
                f"BSI 上升 {p7p.get('d_bsi', '—')}、NDVI 下降 {abs(p7p.get('d_ndvi', 0)):.3f}；"
                f"变化面积 {p7_area} 公顷；WorldCover 2021 图斑内 100% 为耕地，影像为梯田/条田"
            ),
            "activity_note": "变化与秋收后翻耕/清表一致；未见坑口、堆场、道路或机械证据，按农田管理负样本处理。",
            "compliance_note": "不适用：当前按农田管理负样本处理；如后续发现采矿手续或现场证据，再复核。",
            "time_note": "差异存在于 2023-11-02 至 2026-11-11 之间；2025-11-21 严格阈值未检出，支持季节性农田变化，但不能据此证明没有其他活动。",
            "quality": {
                "resolution_m": 10,
                "swir_m": 20,
                "valid_pixels": 306024,
                "valid_ratio": 0.976,
                "cloud_pct_now": 1.543,
                "cloud_pct_base": 0.01,
                "registration_error_m": "未评估",
            },
            "counter_evidence": [
                "ESA WorldCover 2021 该图斑 82/82 像元为耕地（class 40）",
                "影像为梯田/条田，变化与秋收后翻耕/清表一致",
                "紧邻采石场不等于图斑内发生采矿；10 m 看不到坑口、台阶或机械",
                "2023 与 2026 是两期差异，不是逐景持续监测",
            ],
            "missing": [
                "地块承包/农业项目与秋收翻耕记录",
                "逐景影像（确认农事周期）",
                "仅在有采矿疑点时补高分/现场核查",
            ],
            "recommended_actions": ["核对承包/农业项目资料", "补逐景影像", "有采矿疑点再补高分/现场"],
            "permits": [],
            "source": "Sentinel-2 L2A · P07 样区（120.185–120.245°E / 37.125–37.175°N）",
            "images": {
                "before": "/api/yantai/asset/p07_before.png",
                "after": "/api/yantai/asset/p07_after.png",
                "overlay": "/api/yantai/asset/p07_overlay.png",
                "cluster": "/api/yantai/asset/p07_cluster.png",
            },
            "region_id": "p07_2023b",
            "focus_patches": ["YT-007"],
        },
        {
            "id": "YT-E-2026-002",
            "domain": "mining",
            "domain_label": "非采掘变化",
            "title": "大面积清表/裸化候选",
            "subtitle": f"YT-001/002/003 相邻簇 · {cluster_area} 公顷 · 待时相核实",
            "activity_status": "非采掘变化（农田管理）",
            "compliance_status": "不适用",
            "disposition_status": "待排除",
            "priority": "Q",
            "legacy_priority": "P3",
            "lifecycle": "待排除",
            "area_ha": cluster_area,
            "center_lonlat": cluster_center,
            "bbox": cluster_bbox,
            "geometry": cluster_geom,
            "geometry_analysis": [p.get("properties", {}).get("geometry_utm") for p in cluster],
            "crs_display": "EPSG:4326",
            "crs_analysis": "EPSG:32651",
            "observed_at": "2026-11-11",
            "baseline_at": "2023-11-02",
            "window": "2023-11-02 至 2026-11-11",
            "rule_score": cluster_score,
            "score_label": "规则评分（未校准）",
            "score_note": "该簇是规则高分候选，但公开底图目视更似农田/温室清表；不是已确认负样本。",
            "rule": "BSI 显著上升、NDVI 显著下降；公开底图目视更似农田/温室清表",
            "activity_note": "该簇是规则候选，倾向农业/清表；公开底图日期未知，需用时相和现场材料核实，不提前写成确定负样本。",
            "compliance_note": "不涉及采矿许可判断；不得把面积大等同于违法。",
            "time_note": "差异存在于 2023-11-02 至 2026-11-11 之间；无法确定起始日。",
            "quality": {
                "resolution_m": 10,
                "valid_pixels": 306024,
                "valid_ratio": 0.976,
                "cloud_pct_now": 1.543,
                "cloud_pct_base": 0.01,
                "registration_error_m": "未评估",
            },
            "counter_evidence": [
                "公开底图目视更像农田、温室或清表",
                "公开底图没有可核实的拍摄日期",
                "同一景多指数不构成独立证据",
            ],
            "missing": ["有日期的农业/工程材料", "现场核验", "逐时相影像"],
            "recommended_actions": ["补时相核实", "作为困难负样本保留", "必要时现场核查"],
            "permits": [],
            "source": "Sentinel-2 L2A · P07 样区（120.185–120.245°E / 37.125–37.175°N）",
            "images": {
                "before": "/api/yantai/asset/p07_before.png",
                "after": "/api/yantai/asset/p07_after.png",
                "overlay": "/api/yantai/asset/p07_overlay.png",
                "cluster": "/api/yantai/asset/p07_cluster.png",
            },
            "region_id": "p07_2023b",
            "focus_patches": ["YT-001", "YT-002", "YT-003"],
        },
        {
            "id": "YT-E-2026-003",
            "domain": "river",
            "domain_label": "河道采砂",
            "title": "大沽夹河试点段观测缺口",
            "subtitle": "未取得许可、水文与逐景影像",
            "activity_status": "无法判断",
            "compliance_status": "不适用",
            "disposition_status": "待补资料",
            "priority": "Q",
            "legacy_priority": "Q",
            "lifecycle": "待补资料",
            "area_ha": None,
            "center_lonlat": [121.15, 37.12],
            "bbox": None,
            "geometry": None,
            "geometry_analysis": None,
            "crs_display": "EPSG:4326",
            "crs_analysis": None,
            "observed_at": None,
            "baseline_at": None,
            "window": "尚未建立有效观测区间",
            "rule_score": None,
            "score_label": "不评分",
            "score_note": "没有有效观测，不能用规则评分代替观测。",
            "rule": "资料不足：没有采砂许可、禁采期、水文条件和有效影像对比",
            "activity_note": "当前不能判断是否存在采砂活动；没有观测不等于没有活动。",
            "compliance_note": "未取得水利部门管理范围与许可台账，不能生成“非法采砂”结论。",
            "time_note": "无有效观测日；仅记录资料缺口。",
            "quality": None,
            "counter_evidence": ["没有观测，不存在可反向解释的变化"],
            "missing": ["水利管理范围", "采砂许可/禁采期", "水位/流量", "逐景影像"],
            "recommended_actions": ["补水利资料", "补逐景影像", "补水位对照"],
            "permits": [],
            "source": "OpenStreetMap 河段 + 待接入水利资料",
            "images": {},
            "region_id": None,
            "focus_patches": [],
            "river_ids": ["R-大沽夹河"],
        },
    ]
    try:
        events = evidence_mod.attach_events(events)
    except Exception as exc:  # 证据层失败不应让整页 500；错误随事件返回，便于定位
        for event in events:
            event["evidence_error"] = f"多源佐证计算失败：{exc}"
    return [_apply_review(e) for e in events]


def _counts(events: list[dict]) -> dict:
    def count(key, value):
        return sum(1 for e in events if e.get(key) == value)
    return {
        "total": len(events),
        "p1": count("priority", "P1"),
        "p2": count("priority", "P2"),
        "p3": count("priority", "P3"),
        "q": count("priority", "Q"),
        "mining": count("domain", "mining"),
        "river": count("domain", "river"),
        "pending": sum(1 for e in events if e.get("lifecycle") in ("待研判", "待核查", "待补资料", "待排除")),
    }


def _coverage(events: list[dict]) -> dict:
    summary = _summary()
    patches = _load_patches()
    valid = int(summary.get("valid_pixels") or 306024)
    bbox = summary.get("bbox_wgs84") or [120.185, 37.125, 120.245, 37.175]
    # 549×571 像元、10 m：分析窗口面积约 31.35 km²；有效像元面积约 30.60 km²。
    window_km2 = 31.35
    valid_km2 = round(valid * 100 / 1_000_000, 2)
    patch_area_ha = round(sum(float(f["properties"].get("area_ha") or 0) for f in patches), 2)
    return {
        "as_of": "2026-11-15",
        "latest_observation": "2026-11-11",
        "baseline": "2023-11-02",
        "observation_age_days": max(0, (date.today() - date(2026, 11, 11)).days),
        "monitored_area": "P07 样区 5.5×5.7 km（120.185–120.245°E / 37.125–37.175°N）",
        "monitored_area_km2": window_km2,
        "sample_site": {
            "id": "P07",
            "bbox_wgs84": bbox,
            "grid": "549×571",
            "resolution_m": 10,
            "swir_m": 20,
        },
        "valid_area_km2": valid_km2,
        "valid_ratio": round(valid_km2 / window_km2, 4),
        "valid_pixels": valid,
        "bbox_wgs84": bbox,
        "rule_candidates": int(summary.get("patch_count") or 43),
        "rule_candidate_area_ha": float(summary.get("anomaly_area_ha") or 24.0),
        "patch_area_ha": patch_area_ha,
        "rule_candidate_note": "规则候选仅用于排查，未做独立样本验证；只有 2 条进入事件队列。",
        "coverage_note": "仅一个采矿样区完成 10 m 复算；全市与河道尚未形成逐景覆盖。",
        "scope_note": "全市逐景覆盖 0 km²；三条河段无有效影像。当前覆盖只代表 P07 一个样区。",
        "citywide_covered": False,
        "threshold_note": "99.5% 是历史分位阈值，不等于 0.5% 业务误报率；严格阈值未检出也不等于没有活动。",
        "gaps": [
            {"area": "烟台全市", "reason": "未接入逐景 Sentinel-2 目录与自动处理", "status": "观测缺口"},
            {"area": "界河/王河/大沽夹河", "reason": "未取得管理范围、许可、水文与逐景影像", "status": "资料待核"},
            {"area": "地下开采", "reason": "InSAR 旧结果语义问题未修正，不作为告警依据", "status": "不纳入一期"},
        ],
        "events": len(events),
    }


def _check_state() -> dict:
    state = _read_json(CHECK_PATH, {})
    return {
        "checked_at": state.get("checked_at"),
        "check_count": int(state.get("check_count") or 0),
        "data_fingerprint": state.get("data_fingerprint"),
    }


def _fingerprint() -> str:
    h = hashlib.sha256()
    for path in (P07_DIR / "patches.geojson", P07_DIR / "summary.json"):
        try:
            h.update(path.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:16]


def _catalog() -> dict:
    return _read_json(DATA_DIR / "catalog.json", {
        "status": "missing",
        "note": "数据台账未生成；不得据此声称可追溯。",
    })


_BEHAVIOR_CLASSES = [
    {
        "id": "BC1",
        "name": "露天采石 / 取土（坡面新增裸化）",
        "target": "坡面、台阶、坑缘新增裸化斑块",
        "primary": "BSI 上升 + NDVI 下降 + NBR 下降；坡向坡度约束",
        "secondary": "Sentinel-1 VV/VH 变化；高分目视；可得时 DEM 差分",
        "method": "季节谐波残差 + CUSUM 持续超阈，要求连续 ≥2 期",
        "min_target": "≥13 px @20 m ≈0.52 ha；0.8 m 高分可到 0.02 ha 量级（取决于采购覆盖）",
        "confusion": "火灾、滑坡、施工清表、农田翻耕",
        "not_concluded": "不认定违法；候选面积不是测绘面积",
        "status_key": "runnable",
        "status": "可运行（原版与 v2 小试只覆盖此类）",
    },
    {
        "id": "BC2",
        "name": "堆场 / 尾矿 / 弃渣新增",
        "target": "平坦堆体边界扩张、料堆增高",
        "primary": "对象面积增长率 + 边缘外扩距离 + 亮度/纹理",
        "secondary": "高分时序轮廓；可得时 DSM/DEM 高差",
        "method": "对象级轮廓扩张追踪，不用全区像元阈值",
        "min_target": "20 m 只能发现成片扩张；小堆场需 0.8 m 高分",
        "confusion": "合法排土场、物料周转、建筑渣土",
        "not_concluded": "不能区分合法排土与非法弃渣，必须叠加许可",
        "status_key": "partial",
        "status": "未单独建模（仅在候选目视抽样中见到）",
    },
    {
        "id": "BC3",
        "name": "地下开采硐口 / 井口及附属扰动",
        "target": "小面积硐口、井口平台、运输便道",
        "primary": "0.8 m 高分目视 + 平台/便道新增；20 m 光学不可靠",
        "secondary": "InSAR 形变仅作线索（相对参考点的视线向分量）",
        "method": "高分复查为主，形变时序为辅；不作活动真值",
        "min_target": "20 m 明确漏检；需高分专项覆盖",
        "confusion": "其他地下工程、边坡失稳",
        "not_concluded": "LiCSAR sltd 是对流层延迟，不是位移；当前未建模",
        "status_key": "not_modeled",
        "status": "未建模（报告列为缺口）",
    },
    {
        "id": "BC4",
        "name": "河道采砂 / 疏浚",
        "target": "河床挖深、砂堆、临时便道、水体浊度",
        "primary": "水体边界变化 + 砂堆/裸露 + NDWI/NDTI 浊度",
        "secondary": "SAR 水体提取（云雨条件）；高分；采砂许可与禁采期",
        "method": "先按水位/流量归一化再比，不能直接跨季节比较",
        "min_target": "需 10 m 多光谱叠加高分；当前三条河段有效覆盖 0 km²",
        "confusion": "自然冲淤、汛期、合法清淤",
        "not_concluded": "当前无有效影像，不能给出任何河段结论",
        "status_key": "no_data",
        "status": "无有效数据（界河/王河/大沽夹河均未形成逐景覆盖）",
    },
    {
        "id": "BC5",
        "name": "洗选 / 加工厂区扩张",
        "target": "厂区硬化、料棚、沉淀池",
        "primary": "NDBI 等不透水面指数 + 裸地指数组合",
        "secondary": "高分目视；可得时用地/环评/工商资料",
        "method": "对象级扩张 + 不透水面变化，单靠 BSI 会误判",
        "min_target": "10 m 可发现厂区级扩张；小构筑物需高分",
        "confusion": "其他工业用地、仓储物流",
        "not_concluded": "需用地与环评资料，遥感不能单独定性",
        "status_key": "not_modeled",
        "status": "未建模",
    },
    {
        "id": "BC6",
        "name": "运输便道 / 线性扰动",
        "target": "新增或加宽的线性裸化",
        "primary": "沿线 NDVI 下降/裸化 + 宽度与长度",
        "secondary": "高分目视；路网与林区道路数据",
        "method": "线性对象追踪，不做面状聚类",
        "min_target": "10 m 可发现主干便道；窄便道需高分",
        "confusion": "防火道、施工便道、农田道路",
        "not_concluded": "不能单独作为采矿证据，只作旁证",
        "status_key": "not_modeled",
        "status": "未建模",
    },
]


def _overview() -> dict:
    resources = _load_resources()
    rivers = _load_rivers()
    events = _events()
    state = _check_state()
    summary = _summary()
    return {
        "as_of": "2026-11-15",
        "product": "烟台专题 · 非法采矿与河道采砂遥感预警",
        "version": "0.2.0-pilot",
        "data_status": "试点 / 资料待核",
        "stage": "F1 阶段 · 方法可运行（项目层级，不是 F1 分数）",
        "capability_note": "小范围历史遥感分析 + 有结果的算法原型 + 可记录研判的业务平台；"
                           "尚未达到全市持续运行、精度经独立验证、自动生成并闭环处置新增预警。",
        "disclaimer": "遥感只能发现地表活动迹象，不能独立认定违法；未取得许可与现场材料时一律标“待核/无法判断”。",
        "behavior_classes": _BEHAVIOR_CLASSES,
        "behavior_note": "不同行为类别的观测对象、主指标与最小可识别尺度不同：当前只有 BC1（露天坡面裸化）有可运行方法，其余类别不能直接套用同一套 BSI+CUSUM 阈值。",
        "behavior_coverage": {"total": len(_BEHAVIOR_CLASSES), "runnable": 1},
        "resources_count": len(resources),
        "rivers_count": len(rivers),
        "events_count": len(events),
        "counts": _counts(events),
        "coverage": _coverage(events),
        "monitor": {
            "checked_at": state["checked_at"],
            "check_count": state["check_count"],
            "mode": "本地试点数据检查（非实时卫星推送）",
            "latest_observation": "2026-11-11",
            "data_fingerprint": _fingerprint(),
            "data_generated_at": summary.get("generated_at") or None,
        },
        "observations": OBSERVATIONS,
        "permit_status": "未取得",
        "permit_note": "未取得正式采矿权、采砂许可、禁采期与河道管理范围；不显示违法概率。",
    }


def _workspace() -> dict:
    events = _events()
    return {
        "overview": _overview(),
        "events": events,
        "resources": _load_resources(),
        "rivers": _load_rivers(),
        "observations": OBSERVATIONS,
        "analysis_sites": _analysis_sites(),
        "river_segments": RIVER_SEGMENTS,
        "permits": {"items": [], "status": "未取得", "note": "正式许可台账未取得，全部合规结论保持待核。"},
        "catalog": _catalog(),
        "river_channel": _catalog().get("river_channel"),
    }


class ReviewIn(BaseModel):
    decision: Literal["需补许可", "需高分复核", "需现场核查", "非采掘变化"]
    note: str | None = Field(default=None, max_length=1000)
    basis: str | None = Field(default=None, max_length=1000)


_LIFECYCLE = {
    "需补许可": "待补资料",
    "需高分复核": "待补资料",
    "需现场核查": "待核查",
    "非采掘变化": "已排除",
}


def _high_priority(directory: Path, aois: list[dict]) -> dict:
    """从候选 GeoJSON 统计高优先级。summary 没有该字段时不猜。"""
    total, area = 0, 0.0
    by_aoi: dict[str, dict] = {}
    for r in aois:
        gj = _read_json(directory / f"candidates_{r['name']}.geojson", {"features": []})
        high = [f for f in gj.get("features", [])
                if (f.get("properties") or {}).get("priority")]
        a = round(sum((f["properties"].get("area_ha") or 0) for f in high), 2)
        by_aoi[r.get("name")] = {"count": len(high), "area_ha": a}
        total += len(high)
        area += a
    return {"count": total, "area_ha": round(area, 2), "by_aoi": by_aoi}


def _run_record(directory: Path, key: str, label: str, status_label: str,
                period: str, note: str) -> dict:
    """一次运行只描述自己；缺 summary.json 就写未完成，不用别的运行补数。"""
    summary = _read_json(directory / "summary.json", {})
    aois = summary.get("aois") or []
    if not aois:
        return {
            "key": key, "label": label, "status": "missing",
            "status_label": status_label, "generated_at": None, "scope": None,
            "level": None, "scenes": None, "candidates": None, "area_ha": None,
            "high_priority": None, "high_priority_area_ha": None,
            "evidence_counts": None, "dispatchable": None,
            "period": period, "note": note,
            "files": sorted(p.name for p in directory.glob("*") if p.is_file())
            if directory.exists() else [],
        }
    high = _high_priority(directory, aois)
    pairs = sum(int(a.get("target_scenes") or 0) for a in aois)
    return {
        "key": key,
        "label": label,
        "status": "complete",
        "status_label": status_label,
        "generated_at": summary.get("generated_at"),
        "scope": summary.get("scope"),
        "level": summary.get("level"),
        # 两个样区共用同一组目标影像：按最大值计，不把 45 景写成 90 景。
        "scenes": max((int(a.get("target_scenes") or 0) for a in aois), default=0),
        "scene_pairs": pairs,
        "candidates": sum(int(a.get("candidates") or 0) for a in aois),
        "area_ha": round(sum(float(a.get("candidate_area_ha") or 0) for a in aois), 2),
        "high_priority": high["count"],
        "high_priority_area_ha": high["area_ha"],
        "evidence_counts": (summary.get("evidence_fusion") or {}).get("counts"),
        "dispatchable": ((int(((summary.get("evidence_fusion") or {}).get("priorities") or {}).get("P1", 0))
                          + int(((summary.get("evidence_fusion") or {}).get("priorities") or {}).get("P2", 0)))
                         if summary.get("evidence_fusion") else None),
        "period": period,
        "note": note,
    }


def _assessment_runs() -> list[dict]:
    return [
        _run_record(
            ASSESS_ORIG_DIR, "original", "原版 2025 全年（已归档，仅对照）",
            "已完成 · 方法有缺陷",
            "2025-01-05 至 2025-12-28",
            "当前「2025 全年分析」入口已改用 v2.1 全年结果；此行只供对照。"
            "两个样区各 45 景；68 个候选是规则线索，不是 68 起采矿事件，"
            "6 个高优先级也不是已核实案件。季节预测未进入标准化、h 触上限等问题未修正。",
        ),
        _run_record(
            ASSESS_TEST_DIR, "v2_test", "v2 修订版小试",
            "已完成 · 探索性（F1 阶段）",
            "2025-01-05 至 2025-02-24",
            "仅 A_control 一个样区、8 景；不是全年，也不是两个样区。"
            "可作为方法可运行的证据，不能作为精度结论。",
        ),
        _run_record(
            ASSESS_FULL_DIR, "v2_full", "v2 全年重跑",
            "已完成" if (ASSESS_FULL_DIR / "summary.json").exists() else "未完成",
            "2025-01-01 至 2025-12-31",
            ("两个试点样区、45 景目标；F1 探索性结果，不是全市，也不是非法认定。"
             if (ASSESS_FULL_DIR / "summary.json").exists() else
             "summary.json 缺失，只见到 mining_belt 候选 GeoJSON；"
             "不能作为完整结果展示，需要端到端修订后重跑并复核。"),
        ),
    ]


def _assessment() -> dict:
    # 全年结果优先；未写完时才显示小试，且文案必须明确当前范围。
    full_ready = (ASSESS_DIR / "summary.json").exists()
    data_dir = ASSESS_DIR if full_ready else ASSESS_TEST_DIR
    summary = _read_json(data_dir / "summary.json", {
        "status": "missing",
        "note": "v2 结果尚未生成；请先运行 scripts/yantai_2025_activity.py。",
    })
    if "aois" not in summary:
        return {"status": summary.get("status", "missing"), "summary": summary,
                "fixes": ASSESSMENT_FIXES, "runs": _assessment_runs()}
    features = []
    for r in summary["aois"]:
        gj = _read_json(data_dir / f"candidates_{r['name']}.geojson", {"features": []})
        for f in gj.get("features", []):
            props = dict(f.get("properties") or {})
            props["aoi"] = r["name"]
            props["label"] = r.get("label", r["name"])
            geom = f.get("geometry")
            try:
                # Candidate GeoJSON is written in the 20 m UTM analysis grid.
                geom = transform_geom("EPSG:32651", "EPSG:4326", geom)
            except Exception:  # noqa: BLE001
                pass
            features.append({"type": "Feature", "geometry": geom,
                             "properties": props})
    high = [f for f in features if f["properties"].get("priority")]
    ev_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    ev_pri = {"P1": 0, "P2": 0, "P3": 0, "Q": 0}
    for f in features:
        tier = f["properties"].get("evidence_tier")
        pri = f["properties"].get("evidence_priority")
        if tier in ev_counts:
            ev_counts[tier] += 1
        if pri in ev_pri:
            ev_pri[pri] += 1
    dispatch = [f for f in features
                if f["properties"].get("evidence_priority") in ("P1", "P2")]
    aois = []
    for r in summary["aois"]:
        cov = r.get("coverage") or {}
        own = [f for f in features if f["properties"].get("aoi") == r.get("name")]
        fusion = r.get("evidence_fusion") or {}
        own_pri = fusion.get("priorities") or {}
        aois.append({
            "name": r.get("name"), "label": r.get("label"), "bbox": r.get("bbox"),
            "target_scenes": r.get("target_scenes"),
            "target_range": r.get("target_range"),
            "h": r.get("h"),
            "h_at_upper_bound": r.get("h_at_upper_bound"),
            "h_sensitivity": r.get("h_sensitivity"),
            "candidates": r.get("candidates"), "candidate_area_ha": r.get("candidate_area_ha"),
            "high_priority": sum(1 for f in own if f["properties"].get("priority")),
            "high_priority_area_ha": round(sum(
                f["properties"].get("area_ha") or 0 for f in own
                if f["properties"].get("priority")), 2),
            "evidence_counts": fusion.get("counts") or {},
            "evidence_priorities": own_pri,
            "dispatchable": int(own_pri.get("P1", 0)) + int(own_pri.get("P2", 0)),
            "coverage": cov,
            "min_area_px": r.get("min_area_px"),
            "min_area_actual_ha": r.get("min_area_actual_ha"),
        })
    return {
        "status": "ok",
        "title": ("2025 采矿活动遥感评估 · v2 全年" if full_ready
                  else "2025 采矿活动遥感评估 · v2 小试"),
        "scope_label": ("两个试点样区 · 2025-01-01 至 2025-12-31" if full_ready
                        else "A_control 单样区 · 8 景 · 2025-01-05 至 2025-02-24"),
        "level": summary.get("level", "F1"),
        "level_note": "F1 是项目阶段层级（方法可运行），不是 F1 分数；F2（独立精度验证）未达成。",
        "generated_at": summary.get("generated_at"),
        "scope": summary.get("scope"),
        "data_status": ("2025 全年试点 / 方法可运行 / 未做合法性与精度认定" if full_ready
                        else "探索性小试 / 方法可运行 / 未做合法性与精度认定"),
        "disclaimer": "候选是地表变化线索，不是非法采矿认定。",
        "boundaries": [
            "原版 68 个候选不是 68 起采矿事件；6 个高优先级不是 6 起已核实案件。",
            "对照区候选不能直接计作误报；旧口径高优先级（ΔBSI≥0.10）自本版起只作对照，"
            "派单改为证据等级 P1/P2，邻近已知点仍只算上下文。",
            "2025 年单源光学最高只能到证据 C 级，可派单对象为 0 不是统计故障，而是缺台账/高分/现场证据。",
            "两个样区共用同一组目标影像，不能按 90 景独立观测计算。",
            "P07 跨三年变化不能定位为近期新增，也不能与 2025 候选相加。",
            ("本页展示 v2 全年试点（两个样区、45 景目标）；仍不是全市，也不是非法认定。"
             if full_ready else
             "本页展示 v2 小试（A_control、8 景）；v2 全年重跑尚未完成。"),
        ],
        "metrics": {
            "status": "not_evaluated",
            "status_label": "事件级指标未评定",
            "required": [
                "事件级 Precision / Recall / F1",
                "空间块或矿点留出、未来时段测试",
                "未知与缺测不计入 TN",
                "TP/FP/FN 相对明确类别与时段",
            ],
            "reason": "没有 2025 独立高分辨率参考样本，不能计算 precision/recall；"
                      "不能把候选当 TP、把负对照候选当 FP。",
            "sample_size_example": "Precision≈0.80、95% 区间半宽≈0.05 约需 246 个已判明预测正事件（规划量级，不是当前样本量）。",
        },
        "runs": _assessment_runs(),
        "catalog_items": summary.get("catalog_items"),
        "catalog_by_role": summary.get("catalog_by_role"),
        "parameters": summary.get("parameters"),
        "aois": aois,
        "e0": summary.get("e0", []),
        "e0_note": ("E0 仅用两期差分作敏感性对照；候选减少可能来自时间变短、影像减少、"
                    "处理变化或阈值变化，不能直接归因于模型更准确。"),
        "candidate_count": len(features),
        "candidate_area_ha": round(sum(f["properties"].get("area_ha") or 0
                                       for f in features), 2),
        "high_priority_count": len(high),
        "high_priority_area_ha": round(sum(f["properties"].get("area_ha") or 0
                                           for f in high), 2),
        "high_priority_note": "高优先级是 v2.1 旧口径（ΔBSI≥0.10），自证据评分上线后仅作对照，不用于派单。",
        "evidence_counts": ev_counts,
        "evidence_priorities": ev_pri,
        "evidence_as_of": (summary.get("evidence_fusion") or {}).get("as_of"),
        "dispatchable_count": len(dispatch),
        "dispatchable_area_ha": round(sum(f["properties"].get("area_ha") or 0
                                          for f in dispatch), 2),
        "evidence_note": "证据等级是七族证据（O/S/L/D/T/C/H）的完备度分档，不是概率；"
                         "2025 单源光学最高到 C 级，可派单指 P1+P2。",
        "candidates": {"type": "FeatureCollection", "features": features},
        "fixes": ASSESSMENT_FIXES,
        "report_url": ("/api/yantai/assessment/report"
                       if (ASSESS_REPORT if full_ready else ASSESS_TEST_REPORT).exists()
                       else None),
        "report_label": ("打开 v2 全年报告" if full_ready else "打开 v2 小试报告"),
        "report_note": ("v2 全年报告已生成；仍为 F1 探索性结果。"
                        if full_ready else "v2 全年报告尚未生成；此处是小试结果。"),
        "model_card_url": ("/api/yantai/assessment/model-card"
                           if ASSESS_MODEL_CARD.exists() else None),
        "model_card_note": ("v2 模型卡已生成；适用范围见卡片。"
                            if ASSESS_MODEL_CARD.exists()
                            else "v2 模型卡未生成；原版模型卡不适用于 v2 口径。"),
    }


@router.get("/overview")
def overview():
    return _overview()


@router.get("/workspace")
def workspace():
    return _workspace()


@router.get("/catalog")
def catalog():
    return _catalog()


@router.get("/observations")
def observations():
    return {"items": OBSERVATIONS, "river_segments": RIVER_SEGMENTS,
            "note": "只有 P07 有有效双时相；河段尚无有效影像，显示缺测。"}


@router.get("/permits")
def permits():
    return {"items": [], "status": "未取得",
            "note": "正式采矿权、采砂许可、禁采期与河道管理范围未取得，不生成违法结论。"}


@router.get("/coverage")
def coverage():
    return _coverage(_events())


@router.get("/assessment")
def assessment():
    return _assessment()


_LAYER_LABELS = {
    "mine_candidate": "矿区候选",
    "farmland_destruction_suspect": "毁田采矿疑似",
    "farmland_management": "农田管理（移出矿区层）",
    "other_bare": "其他裸地",
    "uncertain": "不确定",
}


def _candidate_reason(p: dict, aoi: dict) -> list[dict]:
    """把候选的属性翻译成可核对的“为什么被标记”。"""
    h = aoi.get("h")
    v2 = p.get("layer") is not None
    pb = p.get("delta_bsi") is None and bool(p.get("persistent_bare"))
    items = [
        {"label": "面积", "value": f"{float(p.get('area_ha') or 0):.2f} ha（20 m 网格聚类；0.5 ha 是筛选门槛，不是稳定识别能力）"},
    ]
    if pb:
        items.append({"label": "来源", "value": "持续裸土规则补全（非 CUSUM 报警对象，无 ΔBSI/报警统计）"})
        items.append({"label": "基线持续裸",
                      "value": f"基线 {int(p.get('baseline_summer_bare_years') or 0)} 年夏季裸"})
    else:
        items += [
            {"label": "BSI 变化", "value": f"{float(p.get('delta_bsi') or 0):+.3f}（相对 2022–2024 基线）"},
            {"label": "报警窗口", "value": f"{p.get('first_alarm') or '—'} — {p.get('last_alarm') or '—'}，平均 {float(p.get('mean_n_alarm') or 0):.1f} 个有效日期触发"},
            {"label": "CUSUM 峰值", "value": (f"{float(p.get('max_cusum') or 0):.1f}（阈值 h={float(h):.2f}）" if h else f"{float(p.get('max_cusum') or 0):.1f}")},
            {"label": "有效观测", "value": f"{float(p.get('valid_frac') or 0):.0%}（q≥0.8 日期少时，证据依赖少数晴天）"},
        ]
    if v2:
        layer = p.get("layer")
        items.insert(1, {"label": "地类分层",
                         "value": f"{_LAYER_LABELS.get(layer, layer)}（{p.get('layer_reason') or '—'}）"})
        if p.get("evidence_tier"):
            score = p.get("evidence_score")
            items.insert(2, {"label": "证据等级",
                             "value": f"{p['evidence_tier']} 级 / 派单 {p.get('evidence_priority') or 'Q'}"
                                      + (f"（排序分 {float(score):.1f}，未校准，不是概率）" if score is not None else "")})
        if p.get("summer_ndvi") is not None:
            items.append({"label": "夏季物候",
                          "value": f"NDVI {float(p['summer_ndvi']):+.2f} · BSI {float(p.get('summer_bsi') or 0):+.2f} · "
                                   f"裸土 {float(p.get('summer_bare_frac') or 0):.0%} → {p.get('phenology_class') or '—'}"})
        items.append({"label": "持续裸土",
                      "value": ("是（规则版矿区补全）" if p.get("persistent_bare")
                                else f"否（基线夏季裸 {int(p.get('baseline_summer_bare_years') or 0)} 年）")})
    if p.get("nearest_known"):
        d = p.get("distance_known_m")
        items.append({"label": "最近已知点",
                      "value": f"{p.get('nearest_known')}（{p.get('nearest_known_kind') or '未知类型'}）" +
                               (f"，{float(d) / 1000:.1f} km" if d is not None else "") + "；只作上下文"})
    if v2:
        items.append({"label": "旧口径高优先级",
                      "value": ("是（ΔBSI≥0.10，仅作对照，不用于派单）" if p.get("priority") else "否")})
    else:
        items.append({"label": "高优先级",
                      "value": ("是（原版口径含已知点邻近，见修订第 9 项）" if p.get("priority")
                                else "否")})
    d_bsi = float(p.get("delta_bsi") or 0)
    if not v2 and p.get("priority") and d_bsi < 0.02:
        items.append({"label": "判读提示",
                      "value": f"高优先级主要由 CUSUM 累计报警触发，ΔBSI 仅 {d_bsi:+.3f}；"
                               "原版未扣季节项，需人工排除季节性（修订第 1 项）"})
    if not v2 and p.get("priority") and p.get("near_known"):
        items.append({"label": "口径提示",
                      "value": "原版 priority 含“邻近已知点”规则，v2 已改为仅 ΔBSI≥0.10；"
                               "两者不能直接比较"})
    return items


def _analysis2025() -> dict:
    """2025 全年结果：优先现行 v2.1；v2 缺失时回退原版并明确标注。"""
    full_ready = (ASSESS_FULL_DIR / "summary.json").exists()
    data_dir = ASSESS_FULL_DIR if full_ready else ASSESS_ORIG_DIR
    summary = _read_json(data_dir / "summary.json", {})
    aois = summary.get("aois") or []
    if not aois:
        return {"status": "missing",
                "note": "2025 全年结果不存在；先运行 scripts/yantai_2025_activity.py。"}
    high = _high_priority(data_dir, aois)
    features, per_aoi = [], []
    for r in aois:
        name = r.get("name")
        gj = _read_json(data_dir / f"candidates_{name}.geojson", {"features": []})
        for f in gj.get("features", []):
            props = dict(f.get("properties") or {})
            geom = f.get("geometry")
            try:
                geom = transform_geom("EPSG:32651", "EPSG:4326", geom)
            except Exception:  # noqa: BLE001
                pass
            props["aoi"] = name
            props["aoi_label"] = r.get("label")
            props["high"] = bool(props.get("priority"))
            props["reason"] = _candidate_reason(props, r)
            features.append({"type": "Feature", "geometry": geom, "properties": props})
        hi = high["by_aoi"].get(name) or {}
        fusion = r.get("evidence_fusion") or {}
        pri = fusion.get("priorities") or {}
        per_aoi.append({
            "name": name, "label": r.get("label"), "bbox": r.get("bbox"),
            "grid": r.get("grid"), "target_scenes": r.get("target_scenes"),
            "target_range": r.get("target_range"), "h": r.get("h"),
            "h_at_upper_bound": r.get("h_at_upper_bound"),
            "candidates": r.get("candidates"),
            "candidate_area_ha": r.get("candidate_area_ha"),
            "high_priority": hi.get("count"),
            "high_priority_area_ha": hi.get("area_ha"),
            "layer_counts": r.get("layer_counts"),
            "evidence_counts": fusion.get("counts") or {},
            "evidence_priorities": pri,
            "dispatchable": int(pri.get("P1", 0)) + int(pri.get("P2", 0)),
            "min_area_actual_ha": r.get("min_area_actual_ha"),
            "coverage": r.get("coverage") or {},
        })
    layer_counts: dict[str, dict] = {}
    ev_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    ev_pri = {"P1": 0, "P2": 0, "P3": 0, "Q": 0}
    for f in features:
        p = f["properties"]
        layer = p.get("layer") or "unclassified"
        cell = layer_counts.setdefault(layer, {"count": 0, "area_ha": 0.0})
        cell["count"] += 1
        cell["area_ha"] += float(p.get("area_ha") or 0)
        if p.get("evidence_tier") in ev_counts:
            ev_counts[p["evidence_tier"]] += 1
        if p.get("evidence_priority") in ev_pri:
            ev_pri[p["evidence_priority"]] += 1
    for cell in layer_counts.values():
        cell["area_ha"] = round(cell["area_ha"], 2)
    mine = layer_counts.get("mine_candidate") or {"count": 0, "area_ha": 0.0}
    fm = layer_counts.get("farmland_management") or {"count": 0, "area_ha": 0.0}
    ob = layer_counts.get("other_bare") or {"count": 0, "area_ha": 0.0}
    un = layer_counts.get("uncertain") or {"count": 0, "area_ha": 0.0}
    total_area = round(sum(float(f["properties"].get("area_ha") or 0) for f in features), 2)
    dispatchable = ev_pri["P1"] + ev_pri["P2"]
    scenes = max((int(a.get("target_scenes") or 0) for a in aois), default=0)
    upper = [str(a.get("name")) for a in aois if a.get("h_at_upper_bound")]
    h_bits = "、".join(f"{a.get('name')} h={float(a.get('h')):.2f}"
                       for a in aois if a.get("h") is not None)
    if full_ready:
        title = "2025 全年采矿活动遥感分析 · v2.1（现行方法）"
        method = {
            "name": "季节基线 + CUSUM + 夏季物候/地类分层 + 七族证据融合（v2.1）",
            "steps": [
                "1) 以 2022-01-01—2024-12-31 可见观测构造逐像元季节基线（趋势 + 年周期项）",
                "2) 先扣季节预测再标准化：z=(残差−基线残差中位数)/max(1.4826·MAD, s_min)",
                f"3) 逐像元 CUSUM（k={float((summary.get('parameters') or {}).get('k') or 0.5):.2f}），"
                f"h 用基线残差分位标定（{h_bits or '各样区分别标定'}）"
                + (f"；{'、'.join(upper)} 的 h 触上限 20.0" if upper else ""),
                "4) 20 m 网格聚类，最小面积 0.5 ha（13 px）；对象级统计 ΔBSI、报警窗口、最大 CUSUM、有效观测比例",
                "5) 夏季物候（6—8 月 NDVI/BSI/裸土占比）：夏季返绿按物候或农事解释，不再直接当采矿",
                "6) ESA WorldCover 2021 地类先验：耕地且夏季返绿→farmland_management；"
                "耕地内夏季裸但持续性未确认→uncertain；非耕地返绿→other_bare；其余→mine_candidate",
                "7) 持续裸土补全：基线≥2 年夏季裸 + 目标年仍裸 + 距已知矿区≤3 km；规则版，仅用于已知矿区周边",
                "8) 证据融合（config_yantai_evidence.yaml v1.0）：七族证据评 A/B/C/D 与 P1/P2/P3/Q；"
                "缺许可台账、高分结构证据、现场核查时最高只能到 C 级",
            ],
            "formula": [
                "x(t)=a+b·t+Σ[c_k·sin(2πkt)+d_k·cos(2πkt)]+ε(t)",
                "z(t)=[x(t)−x̂(t)−median(ε_base)]/max(1.4826·MAD(ε_base), s_min)",
                "G(t)=max(0, G(t−1)+z_direction(t)−k)",
            ],
            "note": "面积与阈值按 20 m 网格换算；h 仍按样区标定，跨区使用需重新标定。"
                    "证据等级表示证据完备度，不是概率；缺失族按 0 计分，只表示证据不足。",
        }
        caveats = [
            f"候选是地表变化线索，不是非法采矿认定，也不是事件数：{len(features)} 个图斑不等于 {len(features)} 起事件；"
            "证据等级 A/B/C/D 是证据完备度，不是概率。",
            f"候选面积 {total_area:.2f} ha 中，矿区候选 {mine['count']} 个 / {mine['area_ha']:.2f} ha，"
            f"农田管理 {fm['count']} 个 / {fm['area_ha']:.2f} ha，其他裸地 {ob['count']} 个 / {ob['area_ha']:.2f} ha，"
            f"不确定 {un['count']} 个 / {un['area_ha']:.2f} ha；这些是候选面积，不是违法面积，"
            "不能与 P07 的 24.0 ha 相加。",
            f"可派单 P1+P2 = {dispatchable} 个不是统计故障：2025 年只有单源光学，"
            "缺许可台账、高分结构证据和现场核查，最高只能到 C 级。",
            "两个样区共用同一组 45 景目标影像，不是 90 景独立观测；差异主要来自样区位置。",
            ("A_control 的 h 标定触上限 20.0，阈值敏感性见样区表；" if upper else "")
            + "F2（独立精度验证）未达成，不计算 precision / recall。",
        ]
        hp_note = (f"旧口径高优先级（ΔBSI≥0.10）{high['count']} 个 / {high['area_ha']:.2f} ha，"
                   f"只作与原版对照，不用于派单；本轮证据派单 P1+P2 = {dispatchable} 个。")
    else:
        title = "2025 全年采矿活动遥感分析 · 原版（v2 结果缺失）"
        method = {
            "name": "基线中位数 / MAD 标准化 + CUSUM 持续超阈（原版）",
            "steps": [
                "1) 以 2022-01-01—2024-12-31 全部可见观测构造逐像元基线",
                "2) 用基线中位数与 MAD 把当期指数标准化为 z（原版未扣季节预测）",
                "3) 按方向累计 CUSUM，连续超阈才保留，降低单景噪声",
                "4) 20 m 网格聚类，≥0.5 ha 才输出对象",
                "5) 对象级统计面积、ΔBSI、报警起止、最大 CUSUM、有效观测比例",
            ],
            "formula": [
                "x(t)=a+b·t+Σ[c_k·sin(2πkt)+d_k·cos(2πkt)]+ε(t)",
                "z(t)=[x(t)−x̂(t)−median(ε_base)]/max(1.4826·MAD(ε_base), s_min)",
                "G(t)=max(0, G(t−1)+z_direction(t)−k)",
            ],
            "note": "原版 z 未扣季节预测，阈值也未做端到端校准；本板块用于定位和复核，不作为精度结论。",
        }
        caveats = [
            "v2 全年结果缺失，当前显示原版；不得用这组结果做验收或对外承诺。",
            "候选是地表变化线索，不是非法采矿认定，也不是事件数。",
            "两个样区各 45 景来自同一组目标影像，不是 90 景独立观测。",
            "没有 2025 年独立高分参考，F2 未达成，不计算 precision / recall。",
        ]
        hp_note = (f"原版口径高优先级 {high['count']} 个 / {high['area_ha']:.2f} ha，"
                   "含 CUSUM 累计报警与邻近已知点规则，只作参考。")
    return {
        "status": "ok",
        "title": title,
        "generated_at": summary.get("generated_at"),
        "level": summary.get("level", "F1"),
        "level_note": "F1 是项目阶段层级（方法可运行），不是 F1 分数；F2（独立验证）未达成。",
        "scope": summary.get("scope"),
        "parameters": summary.get("parameters"),
        "candidate_count": len(features),
        "candidate_area_ha": total_area,
        "mine_candidate_count": mine["count"],
        "mine_candidate_area_ha": mine["area_ha"],
        "layer_counts": layer_counts,
        "evidence_counts": ev_counts,
        "evidence_priorities": ev_pri,
        "dispatchable_count": dispatchable,
        "persistent_bare_count": sum(1 for f in features
                                     if f["properties"].get("persistent_bare")),
        "high_priority_count": high["count"],
        "high_priority_area_ha": high["area_ha"],
        "high_priority_note": hp_note,
        "scene_count": scenes,
        "scene_note": "两个样区各 45 景，但共用同一组 2025 目标影像；不能按 90 景独立观测计算。",
        "aois": per_aoi,
        "candidates": {"type": "FeatureCollection", "features": features},
        "method": method,
        "caveats": caveats,
        "fixes_url": "/api/yantai/assessment",
    }


@router.get("/analysis2025")
def analysis2025():
    return _analysis2025()


def _climate_reason(p: dict) -> list[dict]:
    """把气候增强字段翻译成页面可核对的短句。"""
    items: list[dict] = []
    raw = p.get("climate_delta_bsi_raw")
    adj = p.get("climate_delta_bsi")
    shift = p.get("climate_weather_shift_bsi")
    if raw is not None and adj is not None:
        items.append({"label": "ΔBSI 天气校正",
                      "value": f"{float(raw):+.3f} → {float(adj):+.3f}"
                               f"（扣除天气偏移 {float(shift or 0):+.3f}）"})
    elif p.get("persistent_bare"):
        items.append({"label": "ΔBSI 天气校正",
                      "value": "持续裸土候选无告警日，改用目标年夏季聚合观测"})
    cloud = p.get("climate_cloud_frac")
    snow = p.get("climate_snow_frac")
    rain = p.get("climate_rain_7d_mm")
    suit = p.get("climate_optical_suitability")
    items.append({"label": "观测条件",
                  "value": f"云 {float(cloud or 0):.0%} · 雪 {float(snow or 0):.0%} · "
                           f"近 7 日雨 {float(rain or 0):.1f} mm · 光学适用性 {float(suit or 0):.2f}"})
    level = p.get("climate_observation_level") or "—"
    power = p.get("climate_detection_power")
    samples = p.get("climate_observation_samples")
    basis = ("目标年夏季聚合" if p.get("climate_observation_basis") == "target_year_summer_aggregate"
             else "告警日场景")
    items.append({"label": "探测能力",
                  "value": f"{level}（power {float(power or 0):.2f}，{basis}，"
                           f"{samples if samples is not None else '—'} 次可用观测）"})
    explanations = p.get("climate_explanations") or []
    if explanations:
        items.append({"label": "天气解释",
                      "value": "；".join(str(x.get("text")) for x in explanations[:3])})
    conf = p.get("climate_confounder_combined")
    if conf is not None and float(conf) >= 0.25:
        items.append({"label": "天气混淆",
                      "value": f"综合强度 {float(conf):.2f}（雨/雪/云影/土壤湿度）"})
    flags = p.get("climate_flags") or []
    if flags:
        items.append({"label": "标记", "value": "、".join(str(x) for x in flags)})
    items.append({"label": "排序分",
                  "value": f"证据 {p.get('evidence_tier') or '—'}/{p.get('evidence_priority') or 'Q'} · "
                           f"校正前 {float(p.get('climate_pre_score') or 0):.2f} → "
                           f"校正后 {float(p.get('climate_adjusted_score') or 0):.2f}"
                           f"（排序分，不是概率）"})
    return items


def _climate() -> dict:
    summary = _read_json(CLIMATE_SUMMARY, {})
    aois = summary.get("aois") or {}
    if not aois:
        return {"status": "missing",
                "note": "气候增强结果不存在；先运行 scripts/yantai_climate_pipeline.py --stage all。"}
    types = _read_json(CLIMATE_TYPES, {})
    tier_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    features: list[dict] = []
    per_aoi: list[dict] = []
    models: list[dict] = []
    for name, info in aois.items():
        gj = _read_json(CLIMATE_DIR / name / "candidates_climate.geojson", {"features": []})
        for f in gj.get("features", []):
            props = dict(f.get("properties") or {})
            geom = f.get("geometry")
            try:
                geom = transform_geom("EPSG:32651", "EPSG:4326", geom)
            except Exception:  # noqa: BLE001
                pass
            props["aoi"] = name
            props["aoi_label"] = info.get("label")
            props["climate_tier_label"] = CLIMATE_TIER_LABELS.get(props.get("climate_tier"), "—")
            props["reason"] = _climate_reason(props)
            features.append({"type": "Feature", "geometry": geom, "properties": props})
        obs = info.get("observation") or {}
        det = obs.get("detection") or {}
        ev = info.get("evaluation") or {}
        nc = ev.get("negative_control") or {}
        known = ev.get("known_sites") or {}
        per_aoi.append({
            "name": name,
            "label": info.get("label"),
            "type": info.get("type"),
            "bbox": info.get("bbox"),
            "n_scenes": info.get("n_scenes"),
            "n_candidates": info.get("n_candidates"),
            "model": info.get("model") or {},
            "observation": {
                "n_scenes": obs.get("n_scenes"),
                "n_usable": obs.get("n_usable"),
                "max_gap_days": obs.get("max_gap_days"),
                "level": det.get("level"),
                "power": det.get("power"),
                "required": det.get("required"),
            },
            "target_weather": info.get("target_weather") or {},
            "known_sites": known,
            "negative_control": nc,
            "rain_correlation": ev.get("rain_correlation") or {},
            "score_distribution": ev.get("score_distribution") or {},
        })
        for index_name, model in (info.get("model") or {}).items():
            models.append({
                "aoi": name,
                "aoi_label": info.get("label"),
                "index": index_name,
                "n": model.get("n"),
                "applied": bool(model.get("applied")),
                "improvement_pct": model.get("improvement_pct"),
                "harmonic_r2": model.get("harmonic_r2"),
                "full_r2": model.get("full_r2"),
                "reason": model.get("reason"),
            })
    features.sort(key=lambda f: (tier_order.get(f["properties"].get("climate_tier"), 9),
                                 -float(f["properties"].get("climate_adjusted_score") or 0),
                                 -float(f["properties"].get("area_ha") or 0)))
    threshold = 25.0
    for info in per_aoi:
        threshold = float((info.get("rain_correlation") or {}).get("threshold") or threshold)
    total_area = round(sum(float(f["properties"].get("area_ha") or 0) for f in features), 2)
    above = sum(1 for f in features
                if float(f["properties"].get("climate_adjusted_score") or 0) >= threshold)
    weather = sum(1 for f in features
                  if "weather_explained" in (f["properties"].get("climate_flags") or []))
    blind = sum(1 for f in features
                if (f["properties"].get("climate_observation_level") or "adequate") != "adequate")
    enabled = [m for m in models if m["applied"]]
    no_model = sorted({i["name"] for i in per_aoi
                       if not any(m["applied"] for m in models if m["aoi"] == i["name"])})
    caveats = [
        summary.get("disclaimer") or "排序分不是概率，不构成精度或命中率。",
        f"候选面积 {total_area:.2f} ha 是变化像元聚类面积，不是违法面积；候选不是事件数。",
        f"校正后 ≥{threshold:.0f} 分 {above} 个、天气降级 {weather} 个、观测不足 {blind} 个；"
        "这些是排序与降级计数，不是 TP/FP。",
    ]
    if no_model:
        caveats.append(f"{'、'.join(no_model)} 的天气项未通过 LOO-CV，保留原始观测；"
                       "这是数据诚信回退，不是链路故障。")
    if any(i["name"] == "A_control" for i in per_aoi):
        caveats.append("A_control 是无矿农田负对照，只用于观察降级；其候选不能当作 FP。")
    return {
        "status": "ok",
        "title": "烟台 2025 气候增强分析 · 云 / 雨 / 雪",
        "generated_at": summary.get("generated_at"),
        "target_year": summary.get("target_year"),
        "disclaimer": summary.get("disclaimer"),
        "data_sources": summary.get("data_sources") or {},
        "candidate_count": len(features),
        "candidate_area_ha": total_area,
        "threshold": threshold,
        "above_threshold": above,
        "weather_explained": weather,
        "observation_blind": blind,
        "model_enabled": len(enabled),
        "model_total": len(models),
        "aois": per_aoi,
        "models": models,
        "types": types,
        "candidates": {"type": "FeatureCollection", "features": features},
        "caveats": caveats,
        "report_url": "/api/yantai/climate/report",
    }


@router.get("/climate")
def climate():
    return _climate()


@router.get("/climate/report")
def climate_report():
    if not CLIMATE_REPORT.exists():
        raise HTTPException(404, "气候增强报告尚未生成")
    return FileResponse(CLIMATE_REPORT, media_type="text/markdown; charset=utf-8",
                        headers={"Cache-Control": "private, max-age=60"})


@router.get("/assessment/report")
def assessment_report():
    path = ASSESS_REPORT if ASSESS_REPORT.exists() else ASSESS_TEST_REPORT
    if not path.exists():
        raise HTTPException(404, "v2 报告尚未生成")
    return FileResponse(path, media_type="text/markdown; charset=utf-8",
                        headers={"Cache-Control": "private, max-age=60"})


@router.get("/assessment/model-card")
def assessment_model_card():
    if not ASSESS_MODEL_CARD.exists():
        raise HTTPException(404, "v2 模型卡尚未生成；原版模型卡口径不同，不在此提供")
    return FileResponse(ASSESS_MODEL_CARD, media_type="text/markdown; charset=utf-8",
                        headers={"Cache-Control": "private, max-age=60"})


@router.get("/resources")
def resources():
    return {"as_of": "2026-11-15", "items": _load_resources(),
            "data_status": "OSM 公开采石场 + 遥感识别点，位置示意，待核"}


@router.get("/rivers")
def rivers():
    return {"as_of": "2026-11-15", "items": _load_rivers(),
            "data_status": "OSM 河段中心线，非法定管理范围，待水利确认"}


@router.get("/events")
def events(priority: str | None = Query(default=None),
           domain: str | None = Query(default=None),
           status: str | None = Query(default=None)):
    items = _events()
    if priority:
        items = [e for e in items if e.get("priority") == priority]
    if domain:
        items = [e for e in items if e.get("domain") == domain]
    if status:
        items = [e for e in items if e.get("activity_status") == status]
    items.sort(key=lambda e: _PRIORITY_ORDER.get(e.get("priority", "Q"), 9))
    return {"as_of": "2026-11-15", "items": items, "counts": _counts(items)}


@router.get("/events/{event_id}")
def event_detail(event_id: str):
    for item in _events():
        if item["id"] == event_id:
            return item
    raise HTTPException(404, "事件不存在")


@router.get("/regions")
def regions():
    items = _regions()
    return {
        "as_of": "2026-11-15",
        "items": items,
        "note": "只列出本地同时具备同范围基线/当前影像的样区；没有影像对的地区不生成变化图。",
        "data_status": "本地 Sentinel-2 L2A 双时相试点数据",
    }


@router.post("/events/{event_id}/review")
def review(event_id: str, body: ReviewIn, request: Request):
    if not any(e["id"] == event_id for e in _events()):
        raise HTTPException(404, "事件不存在")
    user = case_api.current_user(request)
    if user.get("role") not in _REVIEW_ROLES:
        raise HTTPException(403, "当前角色无权研判；仅管理员、指挥员、审核员可写入")
    reviewer = user.get("display_name") or user.get("username") or "未知用户"
    entry = {
        "decision": body.decision,
        "note": body.note or "",
        "basis": body.basis or "",
        "reviewer": reviewer,
        "role": user.get("role"),
        "lifecycle": _LIFECYCLE[body.decision],
        "reviewed_at": _now(),
    }
    with _LOCK:
        raw = _review_store().get(event_id)
        if isinstance(raw, dict) and isinstance(raw.get("history"), list):
            history = list(raw["history"])
        elif raw:
            history = [raw]
        else:
            history = []
        history.append(entry)
        _write_json(REVIEW_PATH, {**_review_store(), event_id: {"current": entry, "history": history}})
    return {"ok": True, "event": event_detail(event_id)}


@router.post("/check")
def check(request: Request):
    user = case_api.optional_user(request)
    with _LOCK:
        state = _check_state()
        fingerprint = _fingerprint()
        changed = bool(state.get("data_fingerprint") and state["data_fingerprint"] != fingerprint)
        state["checked_at"] = _now()
        state["check_count"] += 1
        state["data_fingerprint"] = fingerprint
        _write_json(CHECK_PATH, state)
    return {
        "ok": True,
        "checked_at": state["checked_at"],
        "check_count": state["check_count"],
        "new_events": 0,
        "data_changed": changed,
        "checked_by": (user or {}).get("username") or "未登录检查",
        "note": "已检查本地试点文件；未接入实时卫星目录，不把“检查完成”写成“无异常”。",
        "coverage": _coverage(_events()),
    }


_ASSETS = {
    "p07_before.png": P07_DIR / "before.png",
    "p07_after.png": P07_DIR / "after.png",
    "p07_overlay.png": P07_DIR / "overlay.png",
    "p07_diff.png": P07_DIR / "diff_bsi.png",
    "p07_cluster.png": P07_DIR / "result_top_cluster.png",
}


@router.get("/asset/{name}")
def asset(name: str):
    path = _ASSETS.get(name)
    if not path or not path.exists():
        raise HTTPException(404, "影像不存在")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})


_REGION_ASSET_FILES = {"before.png", "after.png", "overlay.png", "diff_bsi.png"}


@router.get("/asset/{region_id}/{kind}")
def region_asset(region_id: str, kind: str):
    spec = next((s for s in _REGION_SPECS if s["id"] == region_id), None)
    if not spec or kind not in _REGION_ASSET_FILES:
        raise HTTPException(404, "影像不存在")
    path = spec["dir"] / kind
    if not path.exists():
        raise HTTPException(404, "影像不存在")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})
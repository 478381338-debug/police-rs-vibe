"""把青山卫士的历史检测结果转换成网页数据底座。

输出：
  data/incidents.geojson   53 个历史异常（含面积/期次/严重度）
  data/aoi.geojson         研究区边界
  data/police.geojson      警务设施/巡护路线（示意点位）
  data/places.geojson      乡镇/村庄/地名（近似坐标）
  data/roads.geojson       示意路网
  data/catalog.json        灾害/风险目录
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import MultiPoint, Polygon, LineString, mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import (  # noqa: E402
    AOI_BBOX,
    AOI_CENTER,
    AOI_NAME,
    DATA,
    HAZARDS,
    SOURCE_INCIDENTS,
)

CLASS_TO_HAZARD = {
    "火烧迹地": "fire_scar",
    "盗采痕迹": "mining",
    "滑坡": "landslide",
    "泥石流": "debris_flow",
    "雪线变化": "snowline",
}
CLASS_SEVERITY = {
    "火烧迹地": 0.90,
    "盗采痕迹": 1.00,
    "滑坡": 0.70,
    "泥石流": 0.70,
    "雪线变化": 0.40,
}
CLASS_PERIOD = {
    "火烧迹地": ("2023-09-30", "夏季2期（2023-09）"),
    "盗采痕迹": ("2023-09-30", "夏季2期（2023-09）"),
    "滑坡": ("2023-09-30", "夏季2期（2023-09）"),
    "泥石流": ("2023-09-30", "夏季2期（2023-09）"),
    "雪线变化": ("2024-01-28", "冬季雪景（2024-01）"),
}
CLASS_COLOR = {
    "火烧迹地": "#ff4d4f",
    "盗采痕迹": "#a50026",
    "滑坡": "#f46d43",
    "泥石流": "#e8a33d",
    "雪线变化": "#74add1",
}


def _rings(geom: dict):
    if geom["type"] == "Polygon":
        yield from geom["coordinates"]
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield from poly


def _area_ha(poly: Polygon) -> float:
    """用局部等距近似把经纬度面积换成公顷。"""
    c = poly.centroid
    kx = 111_320.0 * math.cos(math.radians(c.y))
    ky = 110_540.0

    def xy(coords):
        return [(x * kx, y * ky) for x, y in coords]

    try:
        p = Polygon(xy(list(poly.exterior.coords)))
        return abs(p.area) / 10_000.0
    except Exception:
        return abs(poly.area) * 111_320.0 * 110_540.0 / 10_000.0


def _clean_polygon(feature: dict) -> Polygon | None:
    try:
        g = shape(feature["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        if g.is_empty:
            return None
        return g
    except Exception:
        return None


def build_incidents() -> dict:
    raw = json.loads(SOURCE_INCIDENTS.read_text(encoding="utf-8"))
    feats = []
    for f in raw.get("features", []):
        cls = (f.get("properties") or {}).get("class")
        if cls not in CLASS_TO_HAZARD:
            continue
        g = _clean_polygon(f)
        if g is None:
            continue
        # 大图斑适度简化，保证网页加载速度；小图斑保留细节
        simp = g.simplify(0.0012, preserve_topology=True)
        if simp.is_empty:
            simp = g
        c = simp.centroid
        area = _area_ha(g)
        period, period_label = CLASS_PERIOD.get(cls, ("2023-09-30", "历史期次"))
        props = {
            "class": cls,
            "id": f.get("properties", {}).get("id"),
            "hazard_id": CLASS_TO_HAZARD[cls],
            "area_ha": round(area, 2),
            "period": period,
            "period_label": period_label,
            "severity": CLASS_SEVERITY[cls],
            "color": CLASS_COLOR[cls],
            "centroid": [round(c.x, 6), round(c.y, 6)],
            "source": "青山卫士任务二检测结果（历史）",
        }
        feats.append({
            "type": "Feature",
            "geometry": mapping(simp),
            "properties": props,
        })
    return {
        "type": "FeatureCollection",
        "name": "history_anomalies",
        "features": feats,
    }


def build_aoi(incidents: dict) -> dict:
    pts = [tuple(f["properties"]["centroid"]) for f in incidents["features"]]
    hull = MultiPoint(pts).convex_hull
    # 把凸包外扩，形成研究区边界；再轻微简化
    aoi = hull.buffer(0.12).simplify(0.01, preserve_topology=True)
    return {
        "type": "FeatureCollection",
        "name": "aoi",
        "features": [{
            "type": "Feature",
            "geometry": mapping(aoi),
            "properties": {
                "name": AOI_NAME,
                "type": "研究区",
                "center": AOI_CENTER,
                "bbox": AOI_BBOX,
            },
        }],
    }


POLICE_POINTS = [
    ("呼和浩特市公安局新城区分局", "公安局", 111.665, 40.858, "呼和浩特市新城区"),
    ("呼和浩特市公安局回民区分局", "公安局", 111.625, 40.809, "呼和浩特市回民区"),
    ("武川县公安局", "公安局", 111.457, 41.093, "呼和浩特市武川县"),
    ("土默特左旗公安局", "公安局", 111.131, 40.718, "呼和浩特市土默特左旗"),
    ("包头市石拐区公安分局", "公安局", 110.273, 40.680, "包头市石拐区"),
    ("固阳县公安局", "公安局", 110.063, 41.030, "包头市固阳县"),
    ("大青山自然保护区派出所（示意）", "派出所", 111.050, 40.905, "保护区中段"),
    ("哈拉沁检查站（示意）", "检查站", 111.720, 40.900, "保护区东段"),
    ("白石头沟检查站（示意）", "检查站", 111.200, 40.950, "保护区中段"),
    ("东段巡护站（示意）", "巡护站", 111.800, 41.000, "保护区东段"),
    ("西段巡护站（示意）", "巡护站", 110.500, 40.800, "保护区西段"),
    ("中段巡护站（示意）", "巡护站", 111.100, 40.880, "保护区中段"),
]

POLICE_ROUTES = [
    ("南缘巡护线（G110 示意）", "巡护路线", [[110.00, 40.55], [110.80, 40.56], [111.60, 40.58], [112.30, 40.60]]),
    ("中段旅游公路（示意）", "巡护路线", [[110.50, 40.85], [111.00, 40.88], [111.50, 40.93], [112.00, 40.98]]),
    ("武川—呼市通道（示意）", "巡护路线", [[111.45, 41.09], [111.55, 41.00], [111.66, 40.86]]),
    ("东段巡护线（示意）", "巡护路线", [[111.20, 40.95], [111.80, 41.00], [112.30, 41.05]]),
    ("西段巡护线（示意）", "巡护路线", [[110.10, 40.70], [110.30, 40.80], [110.50, 40.85]]),
]


def build_police() -> dict:
    feats = []
    for name, typ, lon, lat, addr in POLICE_POINTS:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "name": name,
                "type": typ,
                "address": addr,
                "demo": True,
                "note": "示意点位（演示数据，非真实警务布点）",
                "phone": "110",
            },
        })
    for name, typ, coords in POLICE_ROUTES:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "name": name,
                "type": typ,
                "demo": True,
                "note": "示意路线（演示数据）",
            },
        })
    return {"type": "FeatureCollection", "name": "police", "features": feats}


PLACES = [
    ("武川县城", "县城", 111.457, 41.093),
    ("可可以力更镇", "镇", 111.450, 41.090),
    ("哈拉沁", "村", 111.720, 40.900),
    ("毫赖沟", "村", 111.550, 40.950),
    ("白石头沟", "村", 111.200, 40.950),
    ("大青山乡", "乡", 111.100, 40.880),
    ("得胜沟", "村", 110.800, 40.850),
    ("井尔沟", "村", 111.900, 40.950),
    ("察素齐镇", "镇", 111.130, 40.720),
    ("土默特左旗", "旗", 111.131, 40.718),
    ("石拐区", "区", 110.273, 40.680),
    ("固阳县城", "县城", 110.063, 41.030),
    ("萨拉齐镇", "镇", 110.750, 40.550),
    ("五当召", "地标", 110.300, 40.800),
    ("美岱召", "地标", 110.700, 40.600),
    ("哈素海", "湖泊", 110.950, 40.600),
    ("大青山主峰", "地标", 111.050, 40.905),
    ("蜈蚣坝", "地标", 111.500, 40.950),
    ("黑牛沟", "村", 110.650, 40.800),
    ("东柜村", "村", 111.350, 40.850),
]


def build_places() -> dict:
    return {
        "type": "FeatureCollection",
        "name": "places",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": name, "type": typ, "approx": True},
        } for name, typ, lon, lat in PLACES],
    }


ROADS = [
    ("G110 南缘（示意）", [[109.90, 40.55], [110.80, 40.56], [111.60, 40.58], [112.40, 40.60]]),
    ("中段旅游公路（示意）", [[110.45, 40.84], [111.00, 40.88], [111.50, 40.93], [112.05, 40.98]]),
    ("武川—呼市（示意）", [[111.45, 41.09], [111.55, 41.00], [111.66, 40.86]]),
    ("包头—固阳（示意）", [[110.06, 41.03], [110.27, 40.68]]),
    ("东段林区路（示意）", [[111.20, 40.95], [111.80, 41.00], [112.30, 41.05]]),
    ("西段林区路（示意）", [[110.10, 40.70], [110.30, 40.80], [110.50, 40.85]]),
]


def build_roads() -> dict:
    return {
        "type": "FeatureCollection",
        "name": "roads",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"name": name, "type": "道路", "demo": True, "approx": True},
        } for name, coords in ROADS],
    }


def build_catalog() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hazards": HAZARDS,
        "validated": [h["id"] for h in HAZARDS if h["status"] == "validated"],
        "disclaimer": (
            "5 个 validated 类别来自青山卫士任务二的实测检测结果；"
            "screening/risk 类别为扩展规则筛查或气象风险，需人工核查，不作为定性结论。"
        ),
    }


def main():
    incidents = build_incidents()
    aoi = build_aoi(incidents)
    police = build_police()
    places = build_places()
    roads = build_roads()
    catalog = build_catalog()

    (DATA / "incidents.geojson").write_text(json.dumps(incidents, ensure_ascii=False), encoding="utf-8")
    (DATA / "aoi.geojson").write_text(json.dumps(aoi, ensure_ascii=False), encoding="utf-8")
    (DATA / "police.geojson").write_text(json.dumps(police, ensure_ascii=False), encoding="utf-8")
    (DATA / "places.geojson").write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
    (DATA / "roads.geojson").write_text(json.dumps(roads, ensure_ascii=False), encoding="utf-8")
    (DATA / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"incidents: {len(incidents['features'])}")
    print(f"aoi: {len(aoi['features'])}")
    print(f"police: {len(police['features'])}")
    print(f"places: {len(places['features'])}")
    print(f"roads: {len(roads['features'])}")
    print(f"hazards: {len(catalog['hazards'])}")


if __name__ == "__main__":
    main()

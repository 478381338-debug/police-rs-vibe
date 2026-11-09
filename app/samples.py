"""本地核查/灾情样本清单与弱标签回测。

重要边界：
- 现有 53 条记录是“青山卫士任务二”的历史遥感检测结果，不是独立的
  现场核查真值；这里只能称为**历史异常样本**，不能当成未来灾害标签。
- 背景对照格网也不是“已确认无灾”，只能用于检查筛查是否过度集中。
- 因此本模块输出样本清单和回测覆盖情况，不输出可被误解为预报精度的
  AUC/准确率；`verified=false` 的样本必须经过现场核查后才能用于校准。
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone

from app import config

DATA = config.DATA
OUT = DATA / "samples" / "verification_samples.geojson"
SUMMARY = DATA / "samples" / "samples_summary.json"
VERIFICATION = DATA / "samples" / "verification_records.csv"


def _apply_verification(samples: list[dict]) -> dict:
    """Merge local field-review records; only explicit field_verified counts.

    The CSV is deliberately a local file, not a public write API: this site has
    no authentication, so anonymous visitors must not be able to bless a sample.
    """
    if not VERIFICATION.exists():
        return {"records": 0, "verified": 0, "source": "无现场核查记录"}
    by_id = {s.get("properties", {}).get("sample_id"): s for s in samples}
    records = 0
    verified = 0
    for row in csv.DictReader(VERIFICATION.read_text(encoding="utf-8-sig").splitlines()):
        sid = (row.get("sample_id") or "").strip()
        if not sid or sid not in by_id:
            continue
        records += 1
        status = (row.get("status") or "").strip().lower()
        ok = status in {"verified", "field_verified", "confirmed"}
        props = by_id[sid].setdefault("properties", {})
        props["verification_status"] = status or "reviewed"
        props["verified"] = ok
        props["verified_by"] = (row.get("verified_by") or "").strip() or None
        props["verified_at"] = (row.get("verified_at") or "").strip() or None
        props["verification_source"] = (row.get("source") or "").strip() or None
        props["verification_note"] = (row.get("note") or "").strip() or None
        if ok:
            props["label_type"] = "field_verified"
            props["quality"] = "field_verified"
            verified += 1
    return {"records": records, "verified": verified, "source": "现场/人工核查 CSV"}


def _rebuild_summary(samples: list[dict], controls: list, top4: list[dict], verification: dict) -> dict:
    hit_ids = {h["id"] for h in top4}
    hist = [s for s in samples if s["properties"]["label"] == 1]
    hits = [s for s in hist if s["properties"].get("nearest_hotspot_id") in hit_ids]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "青山卫士任务二历史检测结果 + AOI 背景格网",
        "label_quality": "mixed: weak_retrospective / field_verified" if verification.get("verified") else "weak_retrospective",
        "verified_count": verification.get("verified", 0),
        "verification_records": verification.get("records", 0),
        "verification_source": verification.get("source"),
        "counts": {
            "total": len(samples),
            "incidents": len(hist),
            "background_controls": len(controls),
            "top4_hotspots": len(top4),
        },
        "historical_screening": {
            "top4_ids": [h["id"] for h in top4],
            "incidents_near_top4": len(hits),
            "incidents_total": len(hist),
            "recall_at_top4": round(len(hits) / len(hist), 3) if hist else None,
            "note": "这是对历史异常的回溯覆盖率，不是对未来灾害的命中率。",
        },
        "disclaimer": "未标记 field_verified 的样本仍是弱标签；不能据此宣称预报准确率。只有经过现场核查并写入 verification_records.csv 后，才可用于时间外验证和概率校准。",
    }


def _center(feature: dict) -> tuple[float, float] | None:
    p = feature.get("properties") or {}
    c = p.get("centroid")
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        return float(c[0]), float(c[1])
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if geom.get("type") == "Point" and len(coords) >= 2:
        return float(coords[0]), float(coords[1])
    ring = coords[0] if coords and isinstance(coords[0], list) else []
    if ring and isinstance(ring[0], (list, tuple)):
        return (sum(x[0] for x in ring) / len(ring), sum(x[1] for x in ring) / len(ring))
    return None


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def build() -> dict:
    incidents = json.loads((DATA / "incidents.geojson").read_text(encoding="utf-8"))
    hotspots = json.loads((DATA / "hotspots.geojson").read_text(encoding="utf-8"))
    grid = json.loads((DATA / "risk_grid.geojson").read_text(encoding="utf-8"))

    hs = []
    for f in hotspots.get("features") or []:
        p = f.get("properties") or {}
        c = _center(f)
        if c:
            hs.append({"id": p.get("id"), "priority": float(p.get("priority", p.get("risk", 0)) or 0), "lon": c[0], "lat": c[1]})
    hs.sort(key=lambda x: x["priority"], reverse=True)
    top4 = hs[:4]

    samples = []
    for i, f in enumerate(incidents.get("features") or [], 1):
        p = f.get("properties") or {}
        c = _center(f)
        if not c:
            continue
        nearest = min(hs, key=lambda h: _km(c, (h["lon"], h["lat"]))) if hs else None
        samples.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c[0], c[1]]},
            "properties": {
                "sample_id": f"S-INC-{i:03d}",
                "label": 1,
                "label_type": "historical_incident",
                "verified": False,
                "quality": "weak_retrospective",
                "date": p.get("period"),
                "hazard_id": p.get("hazard_id"),
                "hazard_name": p.get("class"),
                "severity": p.get("severity"),
                "area_ha": p.get("area_ha"),
                "source": p.get("source") or "青山卫士任务二检测结果（历史）",
                "nearest_hotspot_id": nearest["id"] if nearest else None,
                "nearest_hotspot_priority": nearest["priority"] if nearest else None,
                "note": "历史遥感检测结果，非现场核实真值；用于回溯检查覆盖，不用于宣称预报精度。",
            },
        })

    # 背景对照：选距离所有历史异常 >= 15 km 的格网中心，最多 30 个。
    inc_points = [_center(f) for f in incidents.get("features") or [] if _center(f)]
    controls = []
    for f in sorted(grid.get("features") or [], key=lambda x: (x.get("properties") or {}).get("cell_id", "")):
        p = f.get("properties") or {}
        c = _center(f)
        if not c:
            continue
        if inc_points and min(_km(c, q) for q in inc_points) < 15:
            continue
        controls.append((c, p))
        if len(controls) >= 30:
            break
    for i, (c, p) in enumerate(controls, 1):
        samples.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c[0], c[1]]},
            "properties": {
                "sample_id": f"S-CTRL-{i:03d}",
                "label": 0,
                "label_type": "background_control",
                "verified": False,
                "quality": "background_control_not_confirmed_absence",
                "date": None,
                "hazard_id": None,
                "hazard_name": None,
                "cell_id": p.get("cell_id"),
                "priority": p.get("priority"),
                "current_danger": p.get("current_danger"),
                "source": "AOI 背景格网（距离历史异常 ≥15 km）",
                "note": "背景对照不等于已确认无灾；仅用于检查筛查集中度。",
            },
        })

    verification = _apply_verification(samples)
    summary = _rebuild_summary(samples, controls, top4, verification)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "本地历史异常核查样本（弱标签）",
        "metadata": summary,
        "features": samples,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "features": samples}


def load() -> dict:
    if not OUT.exists() or not SUMMARY.exists():
        return build()
    try:
        data = json.loads(OUT.read_text(encoding="utf-8"))
        features = data.get("features") or []
        verification = _apply_verification(features)
        summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
        if verification.get("records"):
            # Rebuild only the verification fields; keep the historical coverage
            # based on the original sample/hotspot relationship.
            hist = [s for s in features if s.get("properties", {}).get("label") == 1]
            hit_ids = set((summary.get("historical_screening") or {}).get("top4_ids") or [])
            hits = [s for s in hist if s.get("properties", {}).get("nearest_hotspot_id") in hit_ids]
            summary["verified_count"] = verification["verified"]
            summary["verification_records"] = verification["records"]
            summary["verification_source"] = verification["source"]
            summary["label_quality"] = "mixed: weak_retrospective / field_verified" if verification["verified"] else "weak_retrospective"
            summary["historical_screening"]["incidents_near_top4"] = len(hits)
        return {"summary": summary, "features": features}
    except Exception:
        return build()


if __name__ == "__main__":
    r = build()
    print(json.dumps(r["summary"], ensure_ascii=False, indent=2))

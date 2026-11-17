"""把 WorldPop 格网裁剪到研究区 AOI，避免专题图出现研究区外的色块。

人口按裁剪面积比例缩放，保持格网总量不因边界裁切而虚高。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from shapely.geometry import box, mapping, shape

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "external" / "population_grid.geojson"
BACKUP = P.with_name("population_grid_raw.geojson")
AOI = box(108.90, 39.95, 112.80, 41.58)


def main():
    if not BACKUP.exists():
        shutil.copyfile(P, BACKUP)
    data = json.loads(BACKUP.read_text(encoding="utf-8"))
    out = []
    for f in data.get("features", []):
        geom = shape(f["geometry"])
        clipped = geom.intersection(AOI)
        if clipped.is_empty:
            continue
        ratio = clipped.area / geom.area if geom.area else 0.0
        props = dict(f.get("properties") or {})
        if "population" in props:
            props["population"] = int(round(float(props["population"]) * ratio))
        if "pop_density" in props:
            props["pop_density"] = round(float(props["pop_density"]), 2)
        out.append({"type": "Feature", "properties": props, "geometry": mapping(clipped)})
    data["features"] = out
    P.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    total = sum(int((f.get("properties") or {}).get("population", 0)) for f in out)
    print(f"[ok] {len(out)} cells, population {total}")


if __name__ == "__main__":
    main()

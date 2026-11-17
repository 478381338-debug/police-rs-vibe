"""Build the Zhangqiu (Jinan, Shandong) public-data layer.

Sources:
  - DataV/Jinan district boundary (370114)
  - OpenStreetMap via Overpass (ODbL)
  - WorldPop 2020 1 km raster (CC BY 4.0)
  - AWS Terrain Tiles for terrain (via existing /api/terrain proxy)
  - Open-Meteo forecast (CC BY 4.0)
  - Microsoft Planetary Computer IO LULC preview (CC BY 4.0)

No internal police data is read or published. The grid is a transparent
risk-screening layer, not a police incident record or official warning.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask as raster_mask
import requests
from shapely.geometry import Point, Polygon, MultiPolygon, shape
from shapely.ops import nearest_points

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "zhangqiu"
ASSETS = ROOT / "web" / "assets"
EXT = ROOT / "data" / "external"
UA = {"User-Agent": "eco-police-zhangqiu/1.0 (public-data research)"}
OVERPASS = "https://overpass-api.de/api/interpreter"


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_boundary() -> dict:
    return json.loads((OUT / "boundary.geojson").read_text(encoding="utf-8"))


def boundary_shape(boundary: dict) -> Polygon | MultiPolygon:
    return shape(boundary["features"][0]["geometry"])


def overpass(query: str, retries: int = 2) -> dict:
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(OVERPASS, params={"data": query}, headers=UA, timeout=300)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:180]}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        if i < retries:
            time.sleep(4)
    raise RuntimeError(f"Overpass failed: {last}")


def _el_lonlat(el: dict) -> tuple[float, float] | None:
    if el.get("type") == "node":
        return el.get("lon"), el.get("lat")
    center = el.get("center") or {}
    return center.get("lon"), center.get("lat")


def fetch_osm() -> None:
    b = boundary_shape(load_boundary())
    minx, miny, maxx, maxy = b.bounds
    bbox = f"{miny:.6f},{minx:.6f},{maxy:.6f},{maxx:.6f}"
    q_poi = (
        "[out:json][timeout:180];("
        f'nwr["amenity"~"police|hospital|clinic|school|university|college|kindergarten|fire_station|government|library|museum|bus_station|marketplace|police"]({bbox});'
        f'nwr["office"="government"]({bbox});'
        f'nwr["railway"="station"]({bbox});'
        ");out center tags;"
    )
    q_road = (
        "[out:json][timeout:180];"
        f'way["highway"~"motorway|trunk|primary|secondary|tertiary"]({bbox});'
        "out geom tags;"
    )
    q_water = (
        "[out:json][timeout:180];("
        f'way["waterway"~"river|stream|canal|drain"]({bbox});'
        f'way["natural"="water"]({bbox});'
        ");out geom tags;"
    )
    q_bld = (
        "[out:json][timeout:180];"
        'way["building"](36.62,117.42,36.82,117.64);'
        "out geom tags;"
    )

    pois = overpass(q_poi)
    write_json(OUT / "osm_pois.raw.json", pois)
    roads = overpass(q_road)
    write_json(OUT / "osm_roads.raw.json", roads)
    waters = overpass(q_water)
    write_json(OUT / "osm_waterways.raw.json", waters)
    blds = overpass(q_bld)
    write_json(OUT / "osm_buildings.raw.json", blds)

    pfeats = []
    for el in pois.get("elements", []):
        ll = _el_lonlat(el)
        if not ll or ll[0] is None:
            continue
        lon, lat = ll
        tags = el.get("tags") or {}
        amenity = tags.get("amenity", "")
        if amenity == "police" or tags.get("office") == "government":
            kind = "police" if amenity == "police" else "government"
        elif amenity in ("hospital", "clinic", "pharmacy"):
            kind = "hospital"
        elif amenity in ("school", "university", "college", "kindergarten"):
            kind = "school"
        elif amenity == "fire_station":
            kind = "fire"
        elif tags.get("railway") == "station":
            kind = "station"
        elif amenity in ("library", "museum"):
            kind = "culture"
        elif amenity in ("bus_station", "marketplace"):
            kind = "transport"
        else:
            kind = "other"
        if kind == "other":
            continue
        pfeats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "name": tags.get("name") or tags.get("name:zh") or kind,
                "kind": kind,
                "amenity": amenity or tags.get("office", ""),
                "source": "OpenStreetMap",
                "license": "ODbL-1.0",
            },
        })
    write_json(OUT / "osm_pois.geojson", {"type": "FeatureCollection", "features": pfeats})

    rfeats = []
    for el in roads.get("elements", []):
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        rfeats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in geom]},
            "properties": {
                "name": (el.get("tags") or {}).get("name", ""),
                "highway": (el.get("tags") or {}).get("highway", ""),
                "source": "OpenStreetMap",
                "license": "ODbL-1.0",
            },
        })
    write_json(OUT / "osm_roads.geojson", {"type": "FeatureCollection", "features": rfeats})

    wfeats = []
    for el in waters.get("elements", []):
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        wfeats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in geom]},
            "properties": {
                "name": (el.get("tags") or {}).get("name", ""),
                "waterway": (el.get("tags") or {}).get("waterway", "water"),
                "source": "OpenStreetMap",
                "license": "ODbL-1.0",
            },
        })
    write_json(OUT / "osm_waterways.geojson", {"type": "FeatureCollection", "features": wfeats})

    bfeats = []
    for el in blds.get("elements", []):
        geom = el.get("geometry") or []
        if len(geom) < 3:
            continue
        bfeats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[p["lon"], p["lat"]] for p in geom]]},
            "properties": {"source": "OpenStreetMap", "license": "ODbL-1.0"},
        })
    write_json(OUT / "osm_buildings.geojson", {"type": "FeatureCollection", "features": bfeats})
    print(f"OSM: {len(pfeats)} POI, {len(rfeats)} roads, {len(wfeats)} waterways, {len(bfeats)} buildings")


def fetch_weather() -> None:
    b = boundary_shape(load_boundary())
    minx, miny, maxx, maxy = b.bounds
    pts = [
        ("西", minx + (maxx - minx) * .18, miny + (maxy - miny) * .35),
        ("中", (minx + maxx) / 2, (miny + maxy) / 2),
        ("东", minx + (maxx - minx) * .82, miny + (maxy - miny) * .35),
        ("北", (minx + maxx) / 2, miny + (maxy - miny) * .82),
        ("南", (minx + maxx) / 2, miny + (maxy - miny) * .18),
    ]
    hourly_vars = "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,soil_moisture_0_to_10cm"
    points = []
    for name, lon, lat in pts:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": round(lat, 5), "longitude": round(lon, 5),
                "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,soil_moisture_0_to_10cm",
                "hourly": hourly_vars, "forecast_days": 14, "timezone": "Asia/Shanghai",
            },
            timeout=40,
        )
        r.raise_for_status()
        d = r.json()
        hourly = d.get("hourly") or {}
        times = hourly.get("time") or []
        # aggregate daily sums
        daily = {}
        for i, t in enumerate(times):
            day = t[:10]
            rec = daily.setdefault(day, {"rain": 0.0, "temps": [], "hum": [], "wind": [], "soil": []})
            rec["rain"] += float((hourly.get("precipitation") or [0] * len(times))[i] or 0)
            for key, target in [("temperature_2m", "temps"), ("relative_humidity_2m", "hum"),
                                ("wind_speed_10m", "wind"), ("soil_moisture_0_to_10cm", "soil")]:
                vals = hourly.get(key) or []
                if i < len(vals) and vals[i] is not None:
                    rec[target].append(float(vals[i]))
        days = []
        for day, rec in sorted(daily.items()):
            days.append({
                "date": day,
                "rain_mm": round(rec["rain"], 2),
                "temp_c": round(float(np.mean(rec["temps"])), 2) if rec["temps"] else None,
                "humidity": round(float(np.mean(rec["hum"])), 1) if rec["hum"] else None,
                "wind_kmh": round(float(np.mean(rec["wind"])), 1) if rec["wind"] else None,
                "soil_moisture": round(float(np.mean(rec["soil"])), 4) if rec["soil"] else None,
            })
        cur = d.get("current") or {}
        points.append({
            "name": name, "lon": lon, "lat": lat, "current": cur, "days": days,
        })
        print(f"weather {name}: {len(days)} days")
    write_json(OUT / "weather.json", {
        "source": "Open-Meteo forecast (CC BY 4.0)",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "points": points,
    })


def _sample_raster(ds, lon, lat, default=0.0) -> float:
    row, col = ds.index(lon, lat)
    if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
        return default
    v = ds.read(1)[row, col]
    if ds.nodata is not None and (v == ds.nodata or (isinstance(v, float) and math.isnan(v))):
        return default
    return float(v)


def build_grid() -> None:
    boundary = load_boundary()
    poly = boundary_shape(boundary)
    minx, miny, maxx, maxy = poly.bounds
    step = 0.02
    weather = json.loads((OUT / "weather.json").read_text(encoding="utf-8"))["points"]
    roads = json.loads((OUT / "osm_roads.geojson").read_text(encoding="utf-8"))["features"]
    waters = json.loads((OUT / "osm_waterways.geojson").read_text(encoding="utf-8"))["features"]
    pois = json.loads((OUT / "osm_pois.geojson").read_text(encoding="utf-8"))["features"]
    road_geoms = [shape(f["geometry"]) for f in roads if f.get("geometry")]
    water_geoms = [shape(f["geometry"]) for f in waters if f.get("geometry")]
    police = [shape(f["geometry"]) for f in pois if f["properties"].get("kind") == "police"]
    hospital = [shape(f["geometry"]) for f in pois if f["properties"].get("kind") == "hospital"]
    features = []

    with rasterio.open(EXT / "worldpop_chn_2020_1km.tif") as pop_ds, rasterio.open(EXT / "china_dem.tif") as dem_ds:
        dem = dem_ds.read(1).astype("float32")
        pop_arr = pop_ds.read(1).astype("float32")

        def sample(ds, arr, lon, lat, default=0.0):
            row, col = ds.index(lon, lat)
            if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
                return default
            v = arr[row, col]
            if ds.nodata is not None and (v == ds.nodata or (isinstance(v, float) and math.isnan(v))):
                return default
            return float(v)

        def cell_pop(lon, lat, half=step / 2):
            r0, c0 = pop_ds.index(lon - half, lat + half)
            r1, c1 = pop_ds.index(lon + half, lat - half)
            r0, r1 = max(0, min(r0, r1)), min(pop_ds.height - 1, max(r0, r1))
            c0, c1 = max(0, min(c0, c1)), min(pop_ds.width - 1, max(c0, c1))
            block = pop_arr[r0:r1 + 1, c0:c1 + 1]
            if pop_ds.nodata is not None:
                block = block[block != pop_ds.nodata]
            return float(np.nansum(block))

        cells = []
        y = miny + step / 2
        while y < maxy:
            x = minx + step / 2
            while x < maxx:
                p = Point(x, y)
                if poly.contains(p):
                    cells.append(p)
                x += step
            y += step
        # WorldPop pixel values sampled at cell centers; scale to the exact
        # boundary-clipped raster total to avoid double-counting edge pixels.
        _clipped, _ = raster_mask(pop_ds, [poly], crop=True, all_touched=True)
        _vals = _clipped[0]
        _vals = _vals[_vals != pop_ds.nodata] if pop_ds.nodata is not None else _vals
        clipped_total = float(_vals.sum())
        sample_total = 0.0
        rows = []
        for p in cells:
            lon, lat = p.x, p.y
            pop = max(0.0, sample(pop_ds, pop_arr, lon, lat, 0.0))
            sample_total += pop
            elev = sample(dem_ds, dem, lon, lat, 0.0)
            row, col = dem_ds.index(lon, lat)
            dzdx = dzdy = 0.0
            if 1 <= row < dem_ds.height - 1 and 1 <= col < dem_ds.width - 1:
                dzdx = (dem[row, col + 1] - dem[row, col - 1]) / (2 * 0.025 * 111320 * math.cos(math.radians(lat)))
                dzdy = (dem[row - 1, col] - dem[row + 1, col]) / (2 * 0.025 * 110574)
            slope = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
            # nearest weather point
            wp = min(weather, key=lambda w: (w["lon"] - lon) ** 2 + (w["lat"] - lat) ** 2)
            cur = wp.get("current") or {}
            today = (wp.get("days") or [{}])[0]
            rain3 = float(today.get("rain_mm") or 0)
            rain7 = sum(float(d.get("rain_mm") or 0) for d in (wp.get("days") or [])[:7])
            temp = float(cur.get("temperature_2m") if cur.get("temperature_2m") is not None else (today.get("temp_c") or 15))
            hum = float(cur.get("relative_humidity_2m") if cur.get("relative_humidity_2m") is not None else (today.get("humidity") or 50))
            wind = float(cur.get("wind_speed_10m") if cur.get("wind_speed_10m") is not None else (today.get("wind_kmh") or 5))
            soil = float(cur.get("soil_moisture_0_to_10cm") if cur.get("soil_moisture_0_to_10cm") is not None else (today.get("soil_moisture") or 0.2))
            dp = min((p.distance(x) for x in police), default=99)
            dh = min((p.distance(x) for x in hospital), default=99)
            dr = min((p.distance(x) for x in road_geoms), default=99)
            dw = min((p.distance(x) for x in water_geoms), default=99)
            # degree-ish distances to km
            km = 111.0
            dp, dh, dr, dw = dp * km, dh * km, dr * km, dw * km
            flat = max(0.0, 1.0 - slope / 18.0)
            river_near = math.exp(-dw / 3.0)
            flood = 0.55 * min(1.0, rain3 / 50.0) + 0.25 * flat + 0.20 * river_near
            fire = 0.35 * (1.0 - min(1.0, hum / 100.0)) + 0.25 * min(1.0, wind / 30.0) + 0.20 * (1.0 - min(1.0, soil / 0.4)) + 0.20 * min(1.0, max(0, temp) / 35.0)
            drought = 0.45 * (1.0 - min(1.0, soil / 0.4)) + 0.30 * min(1.0, max(0, temp) / 35.0) + 0.25 * (1.0 - min(1.0, hum / 100.0))
            geohazard = 0.45 * min(1.0, slope / 25.0) + 0.30 * min(1.0, rain7 / 100.0) + 0.25 * river_near
            hazard = max(flood, fire, drought, geohazard)
            rows.append({
                "lon": lon, "lat": lat, "population": round(pop, 1), "elev_m": round(elev, 1),
                "slope_deg": round(slope, 2), "dist_police_km": round(dp, 2),
                "dist_hospital_km": round(dh, 2), "dist_road_km": round(dr, 2),
                "dist_water_km": round(dw, 2), "rain3_mm": round(rain3, 2),
                "rain7_mm": round(rain7, 2), "temp_c": round(temp, 1),
                "humidity": round(hum, 1), "wind_kmh": round(wind, 1),
                "soil_moisture": round(soil, 4), "weather_point": wp["name"],
                "flood": flood, "fire": fire, "drought": drought,
                "geohazard": geohazard, "hazard": hazard,
            })
        scale = (clipped_total / sample_total) if sample_total > 0 else 0.0
        max_pop = 1.0
        for r in rows:
            r["population"] = round(r["population"] * scale, 1)
            max_pop = max(max_pop, r["population"])
        for r in rows:
            r["pop_norm"] = math.log1p(r["population"]) / math.log1p(max_pop)
            r["facility_gap"] = min(1.0, r["dist_police_km"] / 20.0)
            r["road_gap"] = min(1.0, r["dist_road_km"] / 10.0)
            score = 100 * (0.55 * r["hazard"] + 0.25 * r["pop_norm"] + 0.10 * r["facility_gap"] + 0.10 * r["road_gap"])
            r["risk"] = round(max(0.0, min(100.0, score)), 1)
            r["level"] = "高" if r["risk"] >= 70 else "较高" if r["risk"] >= 55 else "中" if r["risk"] >= 40 else "低"
            r["dominant"] = max(("洪涝", "fire", "drought", "geohazard"), key=lambda k: r["flood" if k == "洪涝" else k])
            r["dominant"] = {"fire": "林火", "drought": "干旱", "geohazard": "地灾", "洪涝": "洪涝"}.get(r["dominant"], "综合")
            dx = step / 2
            r["cell_id"] = f"ZQ-{r['lat']:.3f}-{r['lon']:.3f}"
            r["polygon"] = [[r["lon"] - dx, r["lat"] - dx], [r["lon"] + dx, r["lat"] - dx],
                            [r["lon"] + dx, r["lat"] + dx], [r["lon"] - dx, r["lat"] + dx]]
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [r["polygon"] + [r["polygon"][0]]]},
                "properties": {k: v for k, v in r.items() if k != "polygon"},
            })
    feats = sorted(features, key=lambda f: -f["properties"]["risk"])
    for i, f in enumerate(feats):
        f["properties"]["rank"] = i + 1
    write_json(OUT / "risk_grid.geojson", {"type": "FeatureCollection", "features": feats})
    counts = {}
    for f in feats:
        counts[f["properties"]["level"]] = counts.get(f["properties"]["level"], 0) + 1
    pop_total = sum(f["properties"]["population"] for f in feats)
    write_json(OUT / "summary.json", {
        "area": "山东省济南市章丘区",
        "adcode": "370114",
        "center": [117.54069, 36.71209],
        "cells": len(feats),
        "population_sum": round(pop_total),
        "levels": counts,
        "max_risk": feats[0]["properties"]["risk"] if feats else 0,
        "top": [{"rank": f["properties"]["rank"], "lon": f["properties"]["lon"], "lat": f["properties"]["lat"],
                 "risk": f["properties"]["risk"], "level": f["properties"]["level"],
                 "dominant": f["properties"]["dominant"], "population": f["properties"]["population"]}
                for f in feats[:10]],
        "method": "公开数据巡查关注指数：自然致灾因子×0.55 + 人口暴露×0.25 + 警务设施可达性×0.10 + 道路可达性×0.10；非警情、非官方预警",
        "sources": ["DataV 行政区划 370114", "OpenStreetMap ODbL", "WorldPop 2020 CC BY 4.0",
                    "Open-Meteo forecast CC BY 4.0", "AWS Terrarium"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"grid: {len(feats)} cells, pop={pop_total:.0f}, levels={counts}")


def fetch_lulc() -> None:
    boundary = load_boundary()
    # use bbox search; previews are server-rendered, so no COG read needed
    b = boundary_shape(boundary)
    payload = {
        "collections": ["io-lulc-annual-v02"],
        "bbox": list(b.bounds),
        "limit": 5,
        "datetime": "2023-01-01T00:00:00Z/2023-12-31T23:59:59Z",
    }
    r = requests.post("https://planetarycomputer.microsoft.com/api/stac/v1/search",
                      json=payload, headers=UA, timeout=60)
    r.raise_for_status()
    items = r.json().get("features", [])
    if not items:
        print("LULC: no item")
        return
    item = items[0]
    assets = item.get("assets") or {}
    preview = assets.get("rendered_preview") or assets.get("preview")
    if not preview:
        print("LULC: no preview", list(assets)[:10])
        return
    rr = requests.get(preview["href"], headers=UA, timeout=60)
    rr.raise_for_status()
    (ASSETS / "zq_lulc.jpg").write_bytes(rr.content)
    write_json(OUT / "lulc.json", {
        "collection": "io-lulc-annual-v02", "id": item.get("id"),
        "bbox": item.get("bbox"), "datetime": item.get("properties", {}).get("datetime"),
        "license": "CC-BY-4.0", "preview": "/assets/zq_lulc.jpg",
    })
    print("LULC:", item.get("id"), len(rr.content))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    steps = [("osm", fetch_osm), ("weather", fetch_weather), ("lulc", fetch_lulc), ("grid", build_grid)]
    only = sys.argv[1:] or [s[0] for s in steps]
    for name, fn in steps:
        if name in only:
            print(f"== {name} ==", flush=True)
            fn()


if __name__ == "__main__":
    main()

"""开放地理数据接入：OSM、WorldPop、MPC 生态影像、DEM 地形。

只使用已安装的 requests / numpy / rasterio / shapely / Pillow，不额外安装依赖。
输出统一放在 data/external/，网页通过 /data/external/ 静态读取。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from shapely.geometry import LineString, Point, Polygon, mapping, shape

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXT = DATA / "external"
EXT.mkdir(parents=True, exist_ok=True)
AOI = (108.90, 39.95, 112.80, 41.58)
UA = {"User-Agent": "eco-police-open-data/1.0 (research; contact: local)"}

OVERPASS = "https://overpass-api.de/api/interpreter"
MPC = "https://planetarycomputer.microsoft.com/api/stac/v1"
DEM_TILE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
WORLDPOP = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km/2020/"
    "CHN/chn_ppp_2020_1km_Aggregated.tif"
)


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def write_json(path: Path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"写入 {path.relative_to(ROOT)} ({path.stat().st_size/1024:.0f} KB)")


# ---------------------------------------------------------------------------
# OpenStreetMap
# ---------------------------------------------------------------------------
def overpass(query: str, retries: int = 2) -> dict:
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(OVERPASS, params={"data": query}, headers=UA, timeout=240)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:160]}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if i < retries:
            time.sleep(3)
    raise RuntimeError(f"Overpass 查询失败: {last}")


def fetch_osm():
    pois_path = EXT / "osm_pois.json"
    roads_path = EXT / "osm_roads.json"
    bld_path = EXT / "osm_buildings_hohhot.json"
    bbox = f"{AOI[1]},{AOI[0]},{AOI[3]},{AOI[2]}"

    if not pois_path.exists():
        q = (
            f'[out:json][timeout:120];'
            f'(nwr["amenity"~"police|hospital|school|university|college|fire_station"]({bbox}););'
            f'out center tags;'
        )
        write_json(pois_path, overpass(q))
    if not roads_path.exists():
        q = (
            f'[out:json][timeout:120];'
            f'way["highway"~"motorway|trunk|primary|secondary"]({bbox});'
            f'out geom tags;'
        )
        write_json(roads_path, overpass(q))
    if not bld_path.exists():
        # 3D 建筑样例：呼和浩特北部城区；不下载整个研究区的建筑，避免数百 MB。
        bb = "40.78,111.60,40.90,111.78"
        q = f'[out:json][timeout:120];way["building"]({bb});out geom tags;'
        write_json(bld_path, overpass(q))

    pois = json.loads(pois_path.read_text(encoding="utf-8"))
    roads = json.loads(roads_path.read_text(encoding="utf-8"))
    blds = json.loads(bld_path.read_text(encoding="utf-8"))

    # POI -> GeoJSON
    pfeats = []
    for el in pois.get("elements", []):
        tags = el.get("tags") or {}
        if el.get("type") == "node":
            lon, lat = el.get("lon"), el.get("lat")
        else:
            center = el.get("center") or {}
            lon, lat = center.get("lon"), center.get("lat")
        if lon is None or lat is None:
            continue
        amenity = tags.get("amenity", "other")
        kind = (
            "police" if amenity == "police"
            else "hospital" if amenity == "hospital"
            else "school" if amenity in ("school", "university", "college")
            else "fire" if amenity == "fire_station"
            else "other"
        )
        pfeats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "osm_id": el.get("id"), "osm_type": el.get("type"), "amenity": amenity,
                "kind": kind,
                "name": tags.get("name:zh") or tags.get("name") or tags.get("name:en") or amenity,
                "operator": tags.get("operator") or "",
                "phone": tags.get("phone") or tags.get("contact:phone") or "",
                "website": tags.get("website") or "",
                "address": " ".join(x for x in [tags.get("addr:province"), tags.get("addr:city"),
                                                tags.get("addr:district"), tags.get("addr:street"),
                                                tags.get("addr:housenumber")] if x),
                "source": "OpenStreetMap",
            },
        })
    write_json(EXT / "osm_pois.geojson", {"type": "FeatureCollection", "features": pfeats})

    # 道路 -> GeoJSON；做轻度抽稀，防止网页一次画 9 千条原始节点。
    rfeats = []
    for el in roads.get("elements", []):
        geom = el.get("geometry") or []
        pts = [(p.get("lon"), p.get("lat")) for p in geom if p.get("lon") is not None and p.get("lat") is not None]
        if len(pts) < 2:
            continue
        try:
            line = LineString(pts).simplify(0.0012, preserve_topology=False)
        except Exception:
            continue
        tags = el.get("tags") or {}
        rfeats.append({
            "type": "Feature",
            "geometry": mapping(line),
            "properties": {
                "osm_id": el.get("id"), "name": tags.get("name") or "",
                "highway": tags.get("highway") or "", "surface": tags.get("surface") or "",
                "lanes": tags.get("lanes") or "", "maxspeed": tags.get("maxspeed") or "",
                "bridge": tags.get("bridge") or "", "tunnel": tags.get("tunnel") or "",
                "source": "OpenStreetMap",
            },
        })
    write_json(EXT / "osm_roads.geojson", {"type": "FeatureCollection", "features": rfeats})

    # 建筑样例 -> GeoJSON（带高度，供 ArcGIS 3D）
    bfeats = []
    for el in blds.get("elements", []):
        geom = el.get("geometry") or []
        pts = [(p.get("lon"), p.get("lat")) for p in geom if p.get("lon") is not None and p.get("lat") is not None]
        if len(pts) < 3:
            continue
        try:
            poly = Polygon(pts).buffer(0)
            if poly.is_empty or poly.area <= 0:
                continue
        except Exception:
            continue
        tags = el.get("tags") or {}
        h = 6.0
        for key in ("height", "building:height"):
            if tags.get(key):
                m = re.search(r"[\d.]+", str(tags[key]))
                if m:
                    h = max(3.0, float(m.group()))
                break
        else:
            if tags.get("building:levels"):
                m = re.search(r"[\d.]+", str(tags["building:levels"]))
                if m:
                    h = max(3.0, float(m.group()) * 3.2)
        bfeats.append({
            "type": "Feature",
            "geometry": mapping(poly),
            "properties": {
                "osm_id": el.get("id"), "name": tags.get("name") or "",
                "building": tags.get("building") or "yes", "height_m": round(h, 1),
                "levels": tags.get("building:levels") or "", "source": "OpenStreetMap",
            },
        })
    write_json(EXT / "osm_buildings.geojson", {"type": "FeatureCollection", "features": bfeats})
    return len(pfeats), len(rfeats), len(bfeats)


# ---------------------------------------------------------------------------
# WorldPop 人口
# ---------------------------------------------------------------------------
def fetch_worldpop():
    raster = EXT / "worldpop_chn_2020_1km.tif"
    if not raster.exists() or raster.stat().st_size < 1_000_000:
        log("下载 WorldPop 2020 中国 1km 人口栅格（约 50 MB）…")
        with requests.get(WORLDPOP, headers=UA, stream=True, timeout=180) as r:
            r.raise_for_status()
            with raster.open("wb") as f:
                for chunk in r.iter_content(1024 * 512):
                    if chunk:
                        f.write(chunk)
    return raster


def population_grid(raster: Path):
    import rasterio
    from rasterio.mask import mask

    grid = json.loads((DATA / "risk_grid.geojson").read_text(encoding="utf-8"))
    vals = []
    for f in grid["features"]:
        geom = shape(f["geometry"])
        try:
            with rasterio.open(raster) as ds:
                out, _ = mask(ds, [geom], crop=True, filled=True, nodata=-99999.0)
                arr = out[0].astype("float64")
                arr[arr < 0] = np.nan
                pop = float(np.nansum(arr))
        except Exception as e:  # noqa: BLE001
            log(f"人口采样失败 {f['properties'].get('cell_id')}: {e}")
            pop = 0.0
        vals.append(pop)

    positives = sorted(v for v in vals if v > 0)
    if positives:
        q = lambda p: positives[min(len(positives) - 1, int(len(positives) * p))]
        cuts = [q(0.2), q(0.4), q(0.6), q(0.8)]
    else:
        cuts = [0, 0, 0, 0]
    colors = ["#0a84ff", "#22d3ee", "#ffd60a", "#ff9500", "#ff3b30"]
    labels = ["低", "较低", "中", "较高", "高"]
    for f, pop in zip(grid["features"], vals):
        lv = 0
        for i, c in enumerate(cuts):
            if pop >= c:
                lv = i + 1
        p = f["properties"]
        p["population"] = int(round(pop))
        p["pop_level"] = lv
        p["pop_label"] = labels[lv]
        p["pop_color"] = colors[lv]
        # 单元格约 0.2°×0.2°，按纬度估算面积，仅用于展示密度。
        lat = (f["geometry"]["coordinates"][0][0][1] + f["geometry"]["coordinates"][0][2][1]) / 2
        area_km2 = (0.2 * 111.32) * (0.2 * 111.32 * math.cos(math.radians(lat)))
        p["pop_density"] = round(pop / max(area_km2, 1), 1)
    write_json(EXT / "population_grid.geojson", grid)
    return int(sum(vals)), max(vals) if vals else 0


# ---------------------------------------------------------------------------
# MPC：生态与地形影像
# ---------------------------------------------------------------------------
def stac_item(collection: str, extra: dict | None = None) -> dict:
    body = {"collections": [collection], "bbox": list(AOI), "limit": 1,
            "sortby": [{"field": "datetime", "direction": "desc"}]}
    if extra:
        body.update(extra)
    r = requests.post(f"{MPC}/search", json=body, headers=UA, timeout=90)
    r.raise_for_status()
    feats = r.json().get("features", [])
    if not feats:
        raise RuntimeError(f"{collection}: AOI 无数据")
    return feats[0]


def save_preview(item: dict, asset: str, name: str, max_w: int = 1200) -> str | None:
    a = (item.get("assets") or {}).get(asset)
    if not a:
        return None
    try:
        r = requests.get(a["href"], headers=UA, timeout=120)
        r.raise_for_status()
        p = EXT / name
        p.write_bytes(r.content)
        try:
            im = Image.open(p).convert("RGB")
            im.thumbnail((max_w, max_w), Image.LANCZOS)
            im.save(p, "JPEG", quality=84, optimize=True)
        except Exception:
            pass
        return name
    except Exception as e:  # noqa: BLE001
        log(f"预览下载失败 {name}: {e}")
        return None


def fetch_mpc():
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "source": "Microsoft Planetary Computer",
              "aoi": list(AOI), "collections": {}}
    jobs = [
        ("sentinel-1-rtc", "sentinel1", "rendered_preview", "sentinel1_preview.jpg", "CC-BY-4.0"),
        ("alos-dem", "alos_dem", "rendered_preview", "alos_dem_preview.jpg", "JAXA / other"),
        ("modis-11A1-061", "modis_lst", "rendered_preview", "modis_lst_preview.jpg", "NASA / other"),
        ("modis-10A2-061", "modis_snow", None, None, "NASA / other"),
        ("io-lulc-annual-v02", "lulc_2023", "rendered_preview", "lulc_2023_preview.jpg", "CC-BY-4.0"),
        ("sentinel-5p-l2-netcdf", "sentinel5p_so2", None, None, "Copernicus / other"),
    ]
    for cid, key, asset, fname, lic in jobs:
        try:
            item = stac_item(cid)
            entry = {
                "collection": cid, "item": item.get("id"),
                "datetime": (item.get("properties") or {}).get("datetime"),
                "license": lic,
                "assets": list((item.get("assets") or {}).keys()),
            }
            tj = (item.get("assets") or {}).get("tilejson")
            if tj:
                try:
                    tr = requests.get(tj["href"], headers=UA, timeout=60)
                    if tr.status_code == 200:
                        entry["tilejson"] = tr.json()
                except Exception:
                    pass
            if asset and fname:
                prev = save_preview(item, asset, fname)
                if prev:
                    entry["preview"] = prev
            result["collections"][key] = entry
            log(f"MPC {key}: {item.get('id')} preview={entry.get('preview')}")
        except Exception as e:  # noqa: BLE001
            log(f"MPC {cid} 失败: {e}")
            result["collections"][key] = {"collection": cid, "error": str(e)}

    # 年度 LULC：单独取 2017，和 2023 做对比。
    try:
        item = stac_item("io-lulc-annual-v02", {"datetime": "2017-01-01T00:00:00Z/2017-12-31T23:59:59Z"})
        prev = save_preview(item, "rendered_preview", "lulc_2017_preview.jpg")
        result["collections"]["lulc_2017"] = {
            "collection": "io-lulc-annual-v02", "item": item.get("id"),
            "license": "CC-BY-4.0", "preview": prev,
        }
    except Exception as e:  # noqa: BLE001
        log(f"LULC 2017 失败: {e}")

    # LULC 2023 的 tilejson 可能存在多个 MGRS 图幅；网页用第一个主图幅即可。
    write_json(EXT / "mpc_open_data.json", result)
    return result


# ---------------------------------------------------------------------------
# DEM（AWS Terrain Tiles / Mapzen Terrarium，公开数据）
# ---------------------------------------------------------------------------
def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def fetch_dem():
    z = 10
    x0, y0 = lonlat_to_tile(AOI[0], AOI[3], z)  # top-left
    x1, y1 = lonlat_to_tile(AOI[2], AOI[1], z)  # bottom-right
    xs = range(x0, x1 + 1)
    ys = range(y0, y1 + 1)
    log(f"DEM 瓦片范围 z={z} x={x0}-{x1} y={y0}-{y1}，共 {len(list(xs))*len(list(ys))} 块")
    W = len(list(xs)) * 256
    H = len(list(ys)) * 256
    mosaic = np.full((H, W), np.nan, dtype="float32")
    for iy, y in enumerate(ys):
        for ix, x in enumerate(xs):
            url = DEM_TILE.format(z=z, x=x, y=y)
            try:
                r = requests.get(url, headers=UA, timeout=40)
                r.raise_for_status()
                im = Image.open(__import__("io").BytesIO(r.content)).convert("RGB")
                arr = np.asarray(im).astype("float32")
                elev = arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0 - 32768.0
                mosaic[iy * 256:(iy + 1) * 256, ix * 256:(ix + 1) * 256] = elev
            except Exception as e:  # noqa: BLE001
                log(f"DEM 瓦片失败 {z}/{x}/{y}: {e}")
    if np.isnan(mosaic).all():
        raise RuntimeError("DEM 全部瓦片失败")

    # Web Mercator 地理参考
    R = 6378137.0
    n = 2 ** z
    res = 2.0 * R / (256.0 * n)
    xmin = -R + x0 * 256 * res
    ymax = R - y0 * 256 * res
    import rasterio
    from rasterio.transform import from_origin
    dem = EXT / "dem_aoi.tif"
    with rasterio.open(dem, "w", driver="GTiff", height=H, width=W, count=1,
                       dtype="float32", crs="EPSG:3857",
                       transform=from_origin(xmin, ymax, res, res), nodata=-9999.0) as ds:
        ds.write(np.nan_to_num(mosaic, nan=-9999.0).astype("float32"), 1)

    # 色采 + 山体阴影（网页叠加）
    valid = np.isfinite(mosaic)
    if valid.any():
        lo, hi = np.nanpercentile(mosaic, 2), np.nanpercentile(mosaic, 98)
    else:
        lo, hi = 0, 1
    norm = np.clip((mosaic - lo) / max(hi - lo, 1), 0, 1)
    # 简单地形色带：低地绿 → 山地棕 → 高山灰白
    color = np.zeros((H, W, 3), dtype="uint8")
    color[..., 0] = (50 + 180 * norm).astype("uint8")
    color[..., 1] = (90 + 120 * (1 - np.abs(norm - 0.55) * 2)).clip(0, 255).astype("uint8")
    color[..., 2] = (70 + 100 * norm ** 2).astype("uint8")
    color[~valid] = 0
    Image.fromarray(color).save(EXT / "terrain_color.jpg", "JPEG", quality=85, optimize=True)

    gy, gx = np.gradient(np.nan_to_num(mosaic, nan=lo))
    slope = np.pi / 2.0 - np.arctan(np.sqrt(gx * gx + gy * gy) / max(res, 1))
    aspect = np.arctan2(-gx, gy)
    az, alt = math.radians(315), math.radians(45)
    hs = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    hs = (np.clip(hs, 0, 1) * 255).astype("uint8")
    Image.fromarray(hs).save(EXT / "hillshade.png", "PNG", optimize=True)

    # 供 Leaflet 叠加的 WGS84 外框
    bounds = {"south": AOI[1], "west": AOI[0], "north": AOI[3], "east": AOI[2]}
    write_json(EXT / "dem_bounds.json", {"bounds": bounds, "zoom": z, "elev_min": round(float(lo), 1),
                                         "elev_max": round(float(hi), 1), "source": "AWS Terrain Tiles / Mapzen Terrarium"})
    write_json(EXT / "dem_meta.json", {"path": "dem_aoi.tif", "crs": "EPSG:3857", "width": W, "height": H,
                                       "pixel_m": round(res, 1), "bounds_wgs84": bounds})
    return dem


def main():
    log("=== 开放数据接入开始 ===")
    try:
        n = fetch_osm()
        log(f"OSM: {n[0]} 个 POI / {n[1]} 条道路 / {n[2]} 栋建筑样例")
    except Exception as e:  # noqa: BLE001
        log(f"OSM 失败（保留已有文件）: {e}")
    try:
        raster = fetch_worldpop()
        total, mx = population_grid(raster)
        log(f"WorldPop: AOI 人口合计约 {total:,.0f}，单格最大 {mx:,.0f}")
    except Exception as e:  # noqa: BLE001
        log(f"WorldPop 失败: {e}")
    try:
        fetch_mpc()
    except Exception as e:  # noqa: BLE001
        log(f"MPC 失败: {e}")
    try:
        fetch_dem()
    except Exception as e:  # noqa: BLE001
        log(f"DEM 失败: {e}")

    sources = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aoi": list(AOI),
        "sources": [
            {"name": "OpenStreetMap", "license": "ODbL 1.0", "attribution": "© OpenStreetMap contributors",
             "url": "https://www.openstreetmap.org/copyright", "layers": ["osm_pois.geojson", "osm_roads.geojson", "osm_buildings.geojson"]},
            {"name": "WorldPop 2020 1km", "license": "CC BY 4.0", "attribution": "WorldPop (www.worldpop.org)",
             "url": "https://www.worldpop.org/", "layers": ["population_grid.geojson"]},
            {"name": "Microsoft Planetary Computer", "license": "按各 collection 标注",
             "attribution": "Microsoft Planetary Computer / ESA / NASA / JAXA", "url": "https://planetarycomputer.microsoft.com/",
             "layers": ["mpc_open_data.json", "sentinel1_preview.jpg", "lulc_2023_preview.jpg", "alos_dem_preview.jpg"]},
            {"name": "AWS Terrain Tiles (Mapzen Terrarium)", "license": "CC-BY 4.0 / Mapzen",
             "attribution": "Mapzen / AWS Open Data", "url": "https://registry.opendata.aws/terrain-tiles/",
             "layers": ["dem_aoi.tif", "hillshade.png", "terrain_color.jpg"]},
        ],
        "notes": [
            "OSM 为公开众包数据，点位可能缺失或滞后；真实警务布点必须使用内网授权数据。",
            "公网站点不展示真实警务敏感点位；当前警务点位为演示/公开众包数据。",
            "MPC 的 Sentinel-5P SO2 仅提供原始 HDF，未提供可直接渲染的 preview。",
            "MS Buildings 的 MPC 预览当前返回 424；建筑 3D 样例使用 OSM 建筑轮廓。",
        ],
    }
    write_json(EXT / "sources.json", sources)
    log("=== 开放数据接入完成 ===")


if __name__ == "__main__":
    main()

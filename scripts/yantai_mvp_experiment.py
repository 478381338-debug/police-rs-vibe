"""烟台非法采矿最小可行组合实验。

三段：
1) Sentinel-2 L2A 2023/2025/2026 同季多时相 BSI/NDVI 变化（广域筛查）
2) Sentinel-1 RTC 同季 VV 后向散射变化（全天候交叉验证；不是 InSAR）
3) Esri World Imagery 高分辨率切片（目视复核参考，非 GF-2 正式影像）

所有结果只说明“疑似地表扰动”，不构成违法认定。
"""
from __future__ import annotations

import argparse
import json
import io
import math
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

_CA = Path(r"C:\Users\lenovo\cacert.pem")
if _CA.exists():
    for _k in ("GDAL_CURL_CA_BUNDLE", "PROJ_CURL_CA_BUNDLE", "GDAL_HTTP_CAINFO"):
        os.environ[_k] = str(_CA)

import numpy as np
import requests
import rasterio
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from rasterio.features import shapes
from rasterio.transform import from_bounds
from rasterio.warp import transform_bounds, transform as warp_transform, reproject, Resampling
from rasterio.windows import from_bounds as window_from_bounds
from shapely.geometry import shape, mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
OUT = Path("data/yantai_mvp")
BANDS = ["B02", "B03", "B04", "B08", "B11", "SCL"]
VALID_SCL = {4, 5, 7}  # 植被 / 裸土 / 未分类；排除水、云、雪、阴影

AOIS = {
    "A_zhaoyuan_north": {
        "label": "招远北部大图斑",
        "bbox": [120.0786, 37.4163, 120.1146, 37.4523],
    },
    "B_laizhou_south": {
        "label": "莱州东南采石群",
        "bbox": [120.2948, 37.1112, 120.3388, 37.1552],
    },
    "C_zhaoyuan_mid": {
        "label": "招远中部矿区",
        "bbox": [120.3686, 37.1995, 120.4046, 37.2355],
    },
    "D_p07_quarry": {
        "label": "莱州p07已知采石场",
        "bbox": [120.185, 37.125, 120.245, 37.175],
    },
}

PERIODS = {
    "2023": ("2023-10-20T00:00:00Z", "2023-12-10T23:59:59Z", date(2023, 11, 2)),
    "2025": ("2025-11-01T00:00:00Z", "2025-12-10T23:59:59Z", date(2025, 11, 21)),
    "2026": ("2026-10-25T00:00:00Z", "2026-12-10T23:59:59Z", date(2026, 11, 11)),
}


def http_json(url: str, **kw):
    last = None
    for i in range(4):
        try:
            r = requests.get(url, timeout=90, **kw)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def search_items(collection: str, bbox: list[float], start: str, end: str,
                 cloud_lt: float | None = None) -> list[dict]:
    body = {
        "collections": [collection],
        "bbox": bbox,
        "datetime": f"{start}/{end}",
        "limit": 100,
    }
    if cloud_lt is not None:
        body["query"] = {"eo:cloud_cover": {"lt": cloud_lt}}
    last = None
    for i in range(4):
        try:
            r = requests.post(f"{STAC}/search", json=body, timeout=90)
            r.raise_for_status()
            feats = r.json().get("features", [])
            uniq = {f["id"]: f for f in feats}
            return list(uniq.values())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"STAC search failed: {last}")


def pick_s2(bbox: list[float], start: str, end: str, target: date) -> dict:
    feats = search_items("sentinel-2-l2a", bbox, start, end, cloud_lt=15)
    if not feats:
        raise RuntimeError("no Sentinel-2 items")

    def key(f):
        p = f["properties"]
        d = date.fromisoformat(p["datetime"][:10])
        tile = p.get("s2:mgrs_tile", "")
        cloud = p.get("eo:cloud_cover", 99)
        return (0 if tile == "51STB" else 1, 0 if cloud < 2 else 1, abs((d - target).days))

    f = sorted(feats, key=key)[0]
    p = f["properties"]
    return {
        "id": f["id"],
        "date": p["datetime"][:10],
        "cloud": p.get("eo:cloud_cover"),
        "tile": p.get("s2:mgrs_tile"),
        "assets": {k: f["assets"][k]["href"] for k in BANDS if k in f["assets"]},
    }


def pick_s1(bbox: list[float], start: str, end: str, target: date) -> dict:
    feats = search_items("sentinel-1-rtc", bbox, start, end)
    if not feats:
        raise RuntimeError("no Sentinel-1 RTC items")
    # 统一用升轨，避免升降轨几何差异
    asc = [f for f in feats if f["properties"].get("sat:orbit_state") == "ascending"] or feats

    def key(f):
        d = date.fromisoformat(f["properties"]["datetime"][:10])
        return abs((d - target).days)

    f = sorted(asc, key=key)[0]
    p = f["properties"]
    return {
        "id": f["id"],
        "date": p["datetime"][:10],
        "orbit": p.get("sat:orbit_state"),
        "assets": {k: f["assets"][k]["href"] for k in ("vv", "vh") if k in f["assets"]},
    }


SAS_CACHE = OUT / "_sas_cache.json"


def signed(href: str) -> str:
    cache = {}
    if SAS_CACHE.exists():
        try:
            cache = json.loads(SAS_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    rec = cache.get(href)
    if rec and time.time() - rec.get("t", 0) < 3000:
        return rec["u"]
    last = None
    for attempt in range(6):
        try:
            r = requests.get(SIGN, params={"href": href}, timeout=90)
            if r.status_code == 429:
                time.sleep(20 * (attempt + 1))
                last = "429"
                continue
            r.raise_for_status()
            u = r.json()["href"]
            cache[href] = {"u": u, "t": time.time()}
            SAS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SAS_CACHE.write_text(json.dumps(cache), encoding="utf-8")
            time.sleep(1.0)
            return u
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"sign failed after retries: {last}")


def read_band(url: str, bbox: list[float], width: int, height: int,
              dst_transform, resampling=Resampling.bilinear) -> np.ndarray:
    with rasterio.open(url) as ds:
        b = transform_bounds("EPSG:4326", ds.crs, *bbox)
        win = window_from_bounds(*b, transform=ds.transform).round_offsets().round_lengths()
        nodata = ds.nodata if ds.nodata is not None else 0
        src = ds.read(1, window=win, boundless=True, fill_value=nodata)
        dst = np.full((height, width), nodata, dtype=np.float32)
        reproject(
            src, dst,
            src_transform=ds.window_transform(win), src_crs=ds.crs,
            dst_transform=dst_transform, dst_crs="EPSG:32651",
            resampling=resampling, src_nodata=nodata, dst_nodata=nodata,
        )
    return dst


def stretch(rgb: np.ndarray) -> np.ndarray:
    out = np.zeros_like(rgb, dtype=np.float32)
    for i in range(3):
        ch = rgb[..., i]
        v = ch[np.isfinite(ch)]
        lo, hi = np.percentile(v, [2, 98]) if v.size else (0, 1)
        out[..., i] = np.clip((ch - lo) / max(hi - lo, 1e-6), 0, 1)
    return np.clip(out ** 0.9, 0, 1)


def to_db(a: np.ndarray) -> np.ndarray:
    """PC 的 Sentinel-1 RTC 是线性 gamma0（0~10+），不是 dB；负值才按 dB 处理。"""
    a = a.astype(np.float32)
    a = np.where(a <= -32000, np.nan, a)
    if np.nanpercentile(a, 1) >= 0:
        return 10.0 * np.log10(np.maximum(a, 1e-6))
    return a


def morphology(mask: np.ndarray) -> np.ndarray:
    m = Image.fromarray((mask * 255).astype("uint8"))
    m = m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return np.asarray(m) > 128


def vectorize(mask: np.ndarray, tr, min_area: float, max_area: float,
              ub: list[float] | None = None, edge_m: float = 100) -> list[dict]:
    out = []
    for geom, _ in shapes(mask.astype("uint8"), transform=tr):
        g = shape(geom)
        if g.area < min_area or g.area > max_area:
            continue
        rec = {"geometry": mapping(g), "area_ha": round(g.area / 10000, 3),
               "centroid_utm": [g.centroid.x, g.centroid.y],
               "bounds_utm": list(g.bounds)}
        if ub is not None:
            minx, miny, maxx, maxy = g.bounds
            rec["edge"] = bool(minx <= ub[0] + edge_m or miny <= ub[1] + edge_m
                               or maxx >= ub[2] - edge_m or maxy >= ub[3] - edge_m)
        out.append(rec)
    return out


def patch_stats(p: dict, arrs: dict, tr, height: int, width: int) -> dict:
    cx, cy = p["centroid_utm"]
    row = int((tr.f - cy) / 10)
    col = int((cx - tr.c) / 10)
    r0, r1 = max(0, row - 3), min(height, row + 4)
    c0, c1 = max(0, col - 3), min(width, col + 4)
    for k, a in arrs.items():
        v = a[r0:r1, c0:c1]
        p[k] = round(float(np.nanmean(v)), 3) if v.size else None
    return p


def save_png(arr: np.ndarray, path: Path, mode: str = "L") -> None:
    a = np.asarray(arr)
    if mode == "RGB":
        Image.fromarray((np.clip(a, 0, 1) * 255).astype("uint8")).save(path)
    else:
        good = a[np.isfinite(a)]
        lo, hi = np.percentile(good, [2, 98]) if good.size else (0, 1)
        b = np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1)
        Image.fromarray((b * 255).astype("uint8")).save(path)


def tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def fetch_esri(bbox: list[float], z: int, path: Path, max_tiles: int = 80) -> bool:
    """拼接 Esri World Imagery 切片；仅作目视参考，日期未知。"""
    lon0, lat0, lon1, lat1 = bbox
    x0, y0 = tile_xy(lon0, lat1, z)  # 左上
    x1, y1 = tile_xy(lon1, lat0, z)  # 右下
    xs, ys = range(x0, x1 + 1), range(y0, y1 + 1)
    if len(xs) * len(ys) > max_tiles:
        return False
    canvas = Image.new("RGB", (len(xs) * 256, len(ys) * 256), (40, 40, 40))
    ok = 0
    for ix, x in enumerate(xs):
        for iy, y in enumerate(ys):
            url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
            data = None
            try:
                # ArcGIS 对 Python requests 的 TLS 指纹返回 403，curl 正常
                cp = subprocess.run(
                    ["curl", "-sS", "-L", "--max-time", "25", "-A", "Mozilla/5.0", url],
                    capture_output=True, timeout=30,
                )
                if cp.returncode == 0 and cp.stdout[:2] == bytes([0xFF, 0xD8]):
                    data = cp.stdout
            except Exception:
                data = None
            if data is None:
                try:
                    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                    if r.ok and r.content[:2] == bytes([0xFF, 0xD8]):
                        data = r.content
                except Exception:
                    data = None
            if data:
                try:
                    canvas.paste(Image.open(io.BytesIO(data)).convert("RGB"), (ix * 256, iy * 256))
                    ok += 1
                except Exception:
                    pass
    canvas.save(path)
    return ok > 0


def process_aoi(name: str, cfg: dict, run_s1: bool = True, run_esri: bool = True) -> dict:
    bbox = cfg["bbox"]
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    ub = transform_bounds("EPSG:4326", "EPSG:32651", *bbox)
    width = max(1, round((ub[2] - ub[0]) / 10))
    height = max(1, round((ub[3] - ub[1]) / 10))
    tr = from_bounds(*ub, width, height)
    print(f"\n=== {name} {cfg['label']} {bbox} -> {width}x{height} @10m", flush=True)

    picks = {}
    for year, (start, end, target) in PERIODS.items():
        picks[year] = pick_s2(bbox, start, end, target)
        print(f"  S2 {year}: {picks[year]['date']} cloud={picks[year]['cloud']} tile={picks[year]['tile']}", flush=True)

    refl, scl = {}, {}
    for year, item in picks.items():
        urls = {b: signed(h) for b, h in item["assets"].items()}
        refl[year], scl[year] = {}, None
        for b in BANDS:
            t = time.time()
            res = Resampling.nearest if b == "SCL" else Resampling.bilinear
            arr = read_band(urls[b], bbox, width, height, tr, res)
            if b == "SCL":
                scl[year] = arr.astype("int16")
            else:
                refl[year][b] = np.clip(arr * 0.0001 - 0.1, 0, 1)
            print(f"    {year} {b} {time.time()-t:.1f}s", flush=True)

    def indices(year):
        b2, b4, b8, b11 = (refl[year]["B02"], refl[year]["B04"],
                           refl[year]["B08"], refl[year]["B11"])
        bsi = ((b11 + b4) - (b8 + b2)) / np.maximum((b11 + b4) + (b8 + b2), 1e-6)
        ndvi = (b8 - b4) / np.maximum(b8 + b4, 1e-6)
        ndbi = (b11 - b8) / np.maximum(b11 + b8, 1e-6)
        lum = 0.2126 * refl[year]["B04"] + 0.7152 * refl[year]["B03"] + 0.0722 * refl[year]["B02"]
        return bsi, ndvi, ndbi, lum

    idx = {y: indices(y) for y in PERIODS}
    for y in PERIODS:
        bsi, ndvi, ndbi, lum = idx[y]
        np.save(out / f"bsi_{y}.npy", bsi.astype("float32"))
        np.save(out / f"ndvi_{y}.npy", ndvi.astype("float32"))
        save_png(bsi, out / f"bsi_{y}.png")
        save_png(ndvi, out / f"ndvi_{y}.png")
        rgb = stretch(np.dstack([refl[y]["B04"], refl[y]["B03"], refl[y]["B02"]]))
        save_png(rgb, out / f"rgb_{y}.png", "RGB")

    # 三种阈值，看敏感性
    thresholds = {
        "loose": (0.10, -0.03),
        "standard": (0.12, -0.05),
        "strict": (0.15, -0.08),
    }
    pairs = {"2023_2026": ("2023", "2026"), "2025_2026": ("2025", "2026")}
    results = {}
    for pname, (base, now) in pairs.items():
        bsi_b, ndvi_b, _, lum_b = idx[base]
        bsi_n, ndvi_n, _, lum_n = idx[now]
        valid = ((np.isin(scl[base], list(VALID_SCL))) &
                 (np.isin(scl[now], list(VALID_SCL))) &
                 (scl[base] != 0) & (scl[now] != 0))
        d_bsi = bsi_n - bsi_b
        d_ndvi = ndvi_n - ndvi_b
        d_lum = lum_n - lum_b
        results[pname] = {"valid_pixels": int(valid.sum()), "thresholds": {}}
        for tname, (td_bsi, td_ndvi) in thresholds.items():
            mask = valid & (d_bsi > td_bsi) & (d_ndvi < td_ndvi) & (bsi_n > 0.14) & (d_lum > 0.015)
            mask = morphology(mask)
            patches = vectorize(mask, tr, min_area=1000, max_area=1_000_000, ub=ub)
            for p in patches:
                patch_stats(p, {"d_bsi": d_bsi, "d_ndvi": d_ndvi, "bsi_base": bsi_b,
                                "bsi_now": bsi_n, "ndvi_base": ndvi_b, "ndvi_now": ndvi_n}, tr, height, width)
                lon, lat = warp_transform("EPSG:32651", "EPSG:4326",
                                          [p["centroid_utm"][0]], [p["centroid_utm"][1]])
                p["center_lonlat"] = [round(float(lon[0]), 5), round(float(lat[0]), 5)]
            patches.sort(key=lambda x: x["area_ha"], reverse=True)
            for i, p in enumerate(patches, 1):
                p["id"] = f"YT-{pname}-{tname}-{i:03d}"
            interior = [p for p in patches if not p.get("edge")]
            results[pname]["thresholds"][tname] = {
                "patch_count": len(patches),
                "area_ha": round(sum(p["area_ha"] for p in patches), 2),
                "interior_patch_count": len(interior),
                "interior_area_ha": round(sum(p["area_ha"] for p in interior), 2),
                "patches": patches,
            }
        # 保存标准阈值的叠加图
        std = results[pname]["thresholds"]["standard"]
        ov = Image.fromarray((stretch(np.dstack([refl[now]["B04"], refl[now]["B03"],
                                                refl[now]["B02"]])) * 255).astype("uint8"))
        d = ImageDraw.Draw(ov)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
        except Exception:
            font = ImageFont.load_default()
        for p in std["patches"][:12]:
            minx, miny, maxx, maxy = p["bounds_utm"]
            px0 = (minx - ub[0]) / (ub[2] - ub[0]) * width
            px1 = (maxx - ub[0]) / (ub[2] - ub[0]) * width
            py0 = (ub[3] - maxy) / (ub[3] - ub[1]) * height
            py1 = (ub[3] - miny) / (ub[3] - ub[1]) * height
            d.rectangle([px0, py0, px1, py1], outline=(255, 45, 45), width=3)
            d.text((px0 + 2, py0 + 1), str(p["id"].split("-")[-1]), fill=(255, 255, 0), font=font)
        ov.save(out / f"overlay_{pname}.png")
        save_png(d_bsi, out / f"dbsi_{pname}.png")
        save_png(d_ndvi, out / f"dndvi_{pname}.png")
        geojson = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {k: v for k, v in p.items() if k != "geometry"},
             "geometry": p["geometry"]} for p in std["patches"]
        ]}
        (out / f"patches_{pname}.geojson").write_text(
            json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")

    # Sentinel-1 RTC 幅度变化
    s1_info = {}
    if run_s1:
        try:
            s1_picks = {}
            for year, (start, end, target) in PERIODS.items():
                s1_picks[year] = pick_s1(bbox, start, end, target)
                print(f"  S1 {year}: {s1_picks[year]['date']} {s1_picks[year]['orbit']}", flush=True)
            vv, vh = {}, {}
            for year, item in s1_picks.items():
                urls = {k: signed(h) for k, h in item["assets"].items()}
                vv[year] = to_db(read_band(urls["vv"], bbox, width, height, tr))
                vh[year] = to_db(read_band(urls["vh"], bbox, width, height, tr))
                save_png(vv[year], out / f"s1_vv_{year}.png")
            for pname, (base, now) in pairs.items():
                dvv = vv[now] - vv[base]
                dvh = vh[now] - vh[base]
                valid = (vv[base] > -35) & (vv[now] > -35)
                strong = morphology(valid & (np.abs(dvv) > 2.5))
                s1_patches = vectorize(strong, tr, min_area=2000, max_area=1_000_000)
                for p in s1_patches:
                    patch_stats(p, {"d_vv": dvv, "d_vh": dvh}, tr, height, width)
                s1_patches.sort(key=lambda x: x["area_ha"], reverse=True)
                # 与 S2 标准图斑交叉
                s2 = results[pname]["thresholds"]["standard"]["patches"]
                for p in s2:
                    minx, miny, maxx, maxy = p["bounds_utm"]
                    r0 = max(0, int((tr.f - maxy) / 10)); r1 = min(height, int((tr.f - miny) / 10) + 1)
                    c0 = max(0, int((minx - tr.c) / 10)); c1 = min(width, int((maxx - tr.c) / 10) + 1)
                    win = dvv[r0:r1, c0:c1]
                    win_valid = valid[r0:r1, c0:c1]
                    p["s1_mean_dvv"] = round(float(np.nanmean(win)), 2) if win.size else None
                    p["s1_strong_frac"] = round(float(np.mean(np.abs(win[win_valid]) > 2.5)), 3) if win_valid.any() else None
                s1_info[pname] = {
                    "patch_count": len(s1_patches),
                    "area_ha": round(sum(p["area_ha"] for p in s1_patches), 2),
                    "patches": s1_patches[:20],
                }
                save_png(dvv, out / f"s1_dvv_{pname}.png")
                geojson = {"type": "FeatureCollection", "features": [
                    {"type": "Feature", "properties": {k: v for k, v in p.items() if k != "geometry"},
                     "geometry": p["geometry"]} for p in s1_patches
                ]}
                (out / f"s1_patches_{pname}.geojson").write_text(
                    json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            s1_info = {"error": str(exc)}

    # 高分辨率目视参考
    if run_esri:
        fetch_esri(bbox, 16, out / "esri_overview_z16.png")
        std = results.get("2023_2026", {}).get("thresholds", {}).get("standard", {}).get("patches", [])
        for p in std[:5]:
            cx, cy = p["centroid_utm"]
            lon, lat = warp_transform("EPSG:32651", "EPSG:4326", [cx], [cy])
            dlon, dlat = 0.0020, 0.0016
            pb = [lon[0] - dlon, lat[0] - dlat, lon[0] + dlon, lat[0] + dlat]
            fetch_esri(pb, 18, out / f"esri_{p['id']}.png", max_tiles=40)

    summary = {
        "aoi": name,
        "label": cfg["label"],
        "bbox_wgs84": bbox,
        "grid": {"epsg": 32651, "width": width, "height": height, "res_m": 10},
        "s2_items": {y: {k: v for k, v in picks[y].items() if k != "assets"} for y in picks},
        "s2_results": {p: {"valid_pixels": r["valid_pixels"],
                           "thresholds": {t: {k: v[k] for k in ("patch_count", "area_ha",
                                                               "interior_patch_count", "interior_area_ha")}
                                          for t, v in r["thresholds"].items()}}
                       for p, r in results.items()},
        "s1_results": {p: {k: v for k, v in r.items() if k != "patches"} for p, r in s1_info.items()} if s1_info else {},
        "top_patches_2023_2026": results.get("2023_2026", {}).get("thresholds", {}).get("standard", {}).get("patches", [])[:10],
        "top_patches_2025_2026": results.get("2025_2026", {}).get("thresholds", {}).get("standard", {}).get("patches", [])[:10],
        "disclaimer": "仅为公开卫星影像的疑似地表扰动筛查，不能认定违法采矿；需矿权叠加、人工复核和现场核查。",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", default="all", help="AOI key or all")
    ap.add_argument("--no-s1", action="store_true")
    ap.add_argument("--no-esri", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    keys = list(AOIS) if args.aoi == "all" else [args.aoi]
    all_sum = {}
    for k in keys:
        all_sum[k] = process_aoi(k, AOIS[k], run_s1=not args.no_s1, run_esri=not args.no_esri)
    (OUT / "summary_all.json").write_text(json.dumps(all_sum, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nDONE", json.dumps({k: v["s2_results"] for k, v in all_sum.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
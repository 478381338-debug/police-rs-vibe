"""烟台试点：Sentinel-2 L2A 双时相 10 m 裸土/地表扰动检测（演示/筛查，不下执法结论）。"""
from __future__ import annotations
import argparse, json, os, time
from pathlib import Path

# Windows 中文路径下 GDAL 的 CA 路径无法解码；优先使用环境变量中的 ASCII 证书。
_CA = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
if _CA and Path(_CA).exists():
    for _k in ("GDAL_CURL_CA_BUNDLE", "PROJ_CURL_CA_BUNDLE", "GDAL_HTTP_CAINFO"):
        os.environ[_k] = _CA

import numpy as np
import requests
import rasterio
from rasterio.features import shapes, geometry_mask
from rasterio.transform import from_bounds
from rasterio.warp import transform_bounds, transform as warp_transform, transform_geom, reproject, Resampling
from rasterio.windows import from_bounds as window_from_bounds
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from shapely.geometry import shape, mapping

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
ITEMS = {
    "now": "S2C_MSIL2A_20261111T024931_R132_T51STB_20261111T055708",
    "base": "S2B_MSIL2A_20251121T024919_R132_T51STB_20251121T044654",
}
BANDS = ["B02", "B03", "B04", "B08", "B11", "SCL"]
VALID_SCL = {4, 5, 6, 7}


def signed_urls(item_id: str) -> dict[str, str]:
    item = requests.get(f"{STAC}/collections/sentinel-2-l2a/items/{item_id}", timeout=60).json()
    out = {}
    for b in BANDS:
        href = item["assets"][b]["href"]
        out[b] = requests.get(
            "https://planetarycomputer.microsoft.com/api/sas/v1/sign",
            params={"href": href}, timeout=60,
        ).json()["href"]
    return out


def read_band(url: str, bbox: list[float], width: int, height: int,
              dst_transform, resampling=Resampling.bilinear) -> np.ndarray:
    with rasterio.open(url) as ds:
        b = transform_bounds("EPSG:4326", ds.crs, *bbox)
        win = window_from_bounds(*b, transform=ds.transform).round_offsets().round_lengths()
        src = ds.read(1, window=win, boundless=True, fill_value=0)
        dst = np.zeros((height, width), dtype=np.float32)
        reproject(src, dst, src_transform=ds.window_transform(win), src_crs=ds.crs,
                  dst_transform=dst_transform, dst_crs="EPSG:32651",
                  resampling=resampling, src_nodata=0, dst_nodata=0)
    return dst


def stretch(rgb: np.ndarray) -> np.ndarray:
    out = np.zeros_like(rgb, dtype=np.float32)
    for i in range(3):
        ch = rgb[..., i]
        v = ch[np.isfinite(ch)]
        lo, hi = np.percentile(v, [2, 98]) if v.size else (0, 1)
        out[..., i] = np.clip((ch - lo) / max(hi - lo, 1e-6), 0, 1)
    return np.clip(out ** 0.9, 0, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", default="120.355,37.195,120.415,37.245")
    ap.add_argument("--base-item", default=ITEMS["base"])
    ap.add_argument("--dbsi", type=float, default=0.12)
    ap.add_argument("--dndvi", type=float, default=-0.10)
    ap.add_argument("--min-bsi", type=float, default=0.25)
    ap.add_argument("--dl", type=float, default=0.02)
    ap.add_argument("--out", default="data/yantai_rs")
    args = ap.parse_args()
    ITEMS["base"] = args.base_item
    bbox = [float(x) for x in args.bbox.split(",")]
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    ub = transform_bounds("EPSG:4326", "EPSG:32651", *bbox)
    width = max(1, round((ub[2] - ub[0]) / 10))
    height = max(1, round((ub[3] - ub[1]) / 10))
    tr = from_bounds(*ub, width, height)
    print(f"AOI {bbox} -> {width}x{height} @10m", flush=True)

    data = {}
    for tag, item in ITEMS.items():
        urls = signed_urls(item)
        data[tag] = {}
        for b in BANDS:
            t = time.time()
            arr = read_band(urls[b], bbox, width, height, tr,
                            Resampling.nearest if b == "SCL" else Resampling.bilinear)
            data[tag][b] = arr
            print(f"{tag} {b} {arr.shape} {time.time()-t:.1f}s", flush=True)

    refl = {}
    for tag in ITEMS:
        refl[tag] = {}
        for b in ["B02", "B03", "B04", "B08", "B11"]:
            # Sentinel-2 L2A BOA: DN*0.0001 - 0.1；负值截到 0。
            refl[tag][b] = np.clip(data[tag][b] * 0.0001 - 0.1, 0, 1)

    def indices(t):
        b2, b4, b8, b11 = (refl[t]["B02"], refl[t]["B04"], refl[t]["B08"], refl[t]["B11"])
        bsi = ((b11 + b4) - (b8 + b2)) / np.maximum((b11 + b4) + (b8 + b2), 1e-6)
        ndvi = (b8 - b4) / np.maximum(b8 + b4, 1e-6)
        return bsi, ndvi

    bsi_now, ndvi_now = indices("now")
    bsi_base, ndvi_base = indices("base")
    scl_now, scl_base = data["now"]["SCL"], data["base"]["SCL"]
    valid = (np.isin(scl_now, list(VALID_SCL)) & np.isin(scl_base, list(VALID_SCL))
             & (scl_now != 6) & (scl_base != 6))
    d_bsi = bsi_now - bsi_base
    d_ndvi = ndvi_now - ndvi_base
    lum_now = 0.2126 * refl["now"]["B04"] + 0.7152 * refl["now"]["B03"] + 0.0722 * refl["now"]["B02"]
    lum_base = 0.2126 * refl["base"]["B04"] + 0.7152 * refl["base"]["B03"] + 0.0722 * refl["base"]["B02"]

    mask = valid & (d_bsi > args.dbsi) & (d_ndvi < args.dndvi) & (bsi_now > args.min_bsi) & (lum_now - lum_base > args.dl)
    m = Image.fromarray((mask * 255).astype("uint8")).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    mask = np.asarray(m) > 128

    patches = []
    for geom, value in shapes(mask.astype("uint8"), transform=tr):
        # 只对前景（变化像元=1）矢量化；背景连通区不得进入候选。
        if value != 1:
            continue
        g = shape(geom)
        geom_area = g.area
        if geom_area < 1000 or geom_area > 1_000_000:
            continue
        # 在真实图斑内统计，不用质心邻域近似。
        inside = geometry_mask([mapping(g)], out_shape=(height, width), transform=tr,
                               invert=True, all_touched=False)
        inside &= mask
        px = int(inside.sum())
        if px <= 0:
            continue
        area_ha = px * 100.0 / 10000.0
        c = g.centroid
        lon, lat = warp_transform("EPSG:32651", "EPSG:4326", [c.x], [c.y])
        disp = transform_geom("EPSG:32651", "EPSG:4326", mapping(g), precision=8)
        minx, miny, maxx, maxy = g.bounds
        lon0, lat0 = warp_transform("EPSG:32651", "EPSG:4326", [minx, maxx], [miny, maxy])
        lon_w = [float(min(lon0[0], lon0[1])), float(max(lon0[0], lon0[1]))]
        lat_w = [float(min(lat0[0], lat0[1])), float(max(lat0[0], lat0[1]))]
        d_bsi_px = float(np.nanmean(d_bsi[inside]))
        d_ndvi_px = float(np.nanmean(d_ndvi[inside]))
        patches.append({
            "area_ha": round(area_ha, 2),
            "area_ha_geom": round(geom_area / 10000.0, 2),
            "pixel_count": px,
            "center_lonlat": [round(float(lon[0]), 5), round(float(lat[0]), 5)],
            "bbox_wgs84": [round(lon_w[0], 5), round(lat_w[0], 5), round(lon_w[1], 5), round(lat_w[1], 5)],
            "d_bsi": round(d_bsi_px, 3),
            "d_ndvi": round(d_ndvi_px, 3),
            # 这是规则评分，不是概率；未做样本校准，页面不得称“置信度”。
            "rule_score": round(min(0.96, 0.6 + min(0.2, area_ha / 50000) + max(0, d_bsi_px) * 0.4), 2),
            "geometry": disp,
            "geometry_utm": mapping(g),
            "crs_analysis": "EPSG:32651",
            "crs_display": "EPSG:4326",
        })
    patches.sort(key=lambda x: x["area_ha"], reverse=True)
    for i, p in enumerate(patches, 1):
        p["id"] = f"YT-{i:03d}"

    # RGB 与叠加图
    rgb_now = stretch(np.dstack([refl["now"]["B04"], refl["now"]["B03"], refl["now"]["B02"]]))
    rgb_base = stretch(np.dstack([refl["base"]["B04"], refl["base"]["B03"], refl["base"]["B02"]]))
    Image.fromarray((rgb_now * 255).astype("uint8")).save(out / "after.png")
    Image.fromarray((rgb_base * 255).astype("uint8")).save(out / "before.png")

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
    except Exception:
        font = ImageFont.load_default()
    ov = Image.fromarray((rgb_now * 255).astype("uint8")); d = ImageDraw.Draw(ov)
    for i, p in enumerate(patches[:12], 1):
        lon0, lat0, lon1, lat1 = p["bbox_wgs84"]
        # WGS84 bbox 近似映射到像元：用多边形 bounds 的 UTM 坐标更准
        g = shape(p["geometry_utm"]); minx, miny, maxx, maxy = g.bounds
        px0 = (minx - ub[0]) / (ub[2] - ub[0]) * width; px1 = (maxx - ub[0]) / (ub[2] - ub[0]) * width
        py0 = (ub[3] - maxy) / (ub[3] - ub[1]) * height; py1 = (ub[3] - miny) / (ub[3] - ub[1]) * height
        d.rectangle([px0, py0, px1, py1], outline=(255, 45, 45), width=3)
        d.text((px0 + 3, py0 + 1), p["id"], fill=(255, 255, 0), font=font)
    ov.save(out / "overlay.png")

    heat = np.clip((d_bsi + 0.1) / 0.5, 0, 1)
    Image.fromarray((heat * 255).astype("uint8")).save(out / "diff_bsi.png")

    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {k: v for k, v in p.items() if k != "geometry"}, "geometry": p["geometry"]}
        for p in patches
    ]}
    (out / "patches.geojson").write_text(json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "region": "烟台莱州—招远矿山集中区试点",
        "bbox_wgs84": bbox,
        "dates": {"now": "2026-11-11", "base": args.base_item},
        "items": ITEMS,
        "resolution_m": 10,
        "algorithm": f"BSI/NDVI 双时相变化 + SCL 云/水掩膜；dBSI>{args.dbsi} 且 dNDVI<{args.dndvi}",
        "valid_pixels": int(valid.sum()),
        "anomaly_pixels": int(mask.sum()),
        "anomaly_area_ha": round(float(mask.sum()) * 100 / 10000, 1),
        "rule_score_note": "规则评分未做概率校准，仅用于排序，不代表违法概率。",
        "patch_count": len(patches),
        "patches": patches[:20],
        "disclaimer": "仅为公开卫星影像的疑似地表扰动筛查，不能据此认定违法采矿；需人工复核和现场核查。",
        "elapsed_s": round(time.time() - started, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "patches"}, ensure_ascii=False, indent=2), flush=True)
    print("TOP", json.dumps([{k: p[k] for k in ("id", "area_ha", "center_lonlat", "d_bsi", "d_ndvi", "rule_score")} for p in patches[:8]], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

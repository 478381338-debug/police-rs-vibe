"""微软行星计算机（Microsoft Planetary Computer）爬虫 + Open-Meteo 实时气象。

每次 refresh：
  1. 查询 Sentinel-2 / Landsat 最新场景（含云量、缩略图、直接可用瓦片 URL）
  2. 查询 MODIS NDVI/LST/GPP/过火面积、DEM、土地覆盖、地表水、GPM 降水等自然数据
  3. 拉取研究区 5 个气象点的实时温度/湿度/风/降水/土壤湿度
  4. 写入 data/mpc_scenes.json 与 data/weather.json

不依赖栅格解码（MPC 的 preview/tilejson 已由服务端渲染），因此刷新很快，
也不需要在本机安装 GDAL/rasterio 去读 COG。
"""
from __future__ import annotations

import io
import json
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image
from pystac_client import Client
from pystac import Item

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import AOI_BBOX, AOI_CENTER, DATA, THUMBS, WEATHER_POINTS  # noqa: E402

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
UA = {"User-Agent": "EcoPolice-RemoteSensing/1.0 (academic demo)"}
SESSION = requests.Session()
SESSION.headers.update(UA)

NATURAL_COLLECTIONS = [
    {
        "id": "modis-13A1-061",
        "name": "MODIS NDVI（16 天合成）",
        "type": "vegetation",
        "desc": "植被指数，用于植被退化、干旱与生态背景判读。",
        "asset": "500m_16_days_NDVI",
        "colormap": "rdylgn",
        "rescale": "-2000,10000",
    },
    {
        "id": "modis-14A2-061",
        "name": "MODIS 火点异常（8 天合成）",
        "type": "fire",
        "desc": "MODIS 热异常/火点掩膜，用于森林火灾与火源管控。",
        "asset": "FireMask",
        "colormap": "fire",
        "rescale": "0,9",
    },
    {
        "id": "modis-17A2H-061",
        "name": "MODIS 总初级生产力 GPP",
        "type": "productivity",
        "desc": "植被生产力，用于生态质量与退化趋势判读。",
        "asset": "Gpp_500m",
        "colormap": "viridis",
        "rescale": "0,3000",
    },
    {
        "id": "modis-64A1-061",
        "name": "MODIS 过火面积（月度）",
        "type": "fire",
        "desc": "月度过火迹地，用于森林火灾历史与火险背景。",
        "asset": "Burn_Date",
        "colormap": "fire",
        "rescale": "1,366",
    },
    {
        "id": "cop-dem-glo-30",
        "name": "Copernicus DEM 30 m",
        "type": "terrain",
        "desc": "高程与坡度，用于滑坡、泥石流、洪涝与可达性分析。",
        "asset": "data",
        "colormap": "terrain",
        "rescale": "1000,2400",
    },
    {
        "id": "io-lulc-annual-v02",
        "name": "10 m 土地覆盖（年度）",
        "type": "landcover",
        "desc": "地表覆盖类型，用于生态背景与人类活动判读。",
        "asset": "data",
        "colormap": "tab20",
        "rescale": "0,20",
    },
    {
        "id": "esa-worldcover",
        "name": "ESA WorldCover 10 m",
        "type": "landcover",
        "desc": "地表覆盖分类，用于湿地、裸土、建设用地背景。",
        "asset": "map",
        "colormap": "tab20",
        "rescale": "0,20",
    },
    {
        "id": "jrc-gsw",
        "name": "JRC 全球地表水",
        "type": "water",
        "desc": "地表水范围，用于水体异常、湿地侵占与洪涝筛查。",
        "asset": "occurrence",
        "colormap": "blues",
        "rescale": "0,100",
    },
    {
        "id": "gpm-imerg-hhr",
        "name": "GPM IMERG 降水",
        "type": "precipitation",
        "desc": "全球降水产品，用于洪涝、滑坡与干旱背景。",
        "asset": "precipitation",
        "colormap": "blues",
        "rescale": "0,50",
    },
]

S2_COLLECTION = "sentinel-2-l2a"
LANDSAT_COLLECTION = "landsat-c2-l2"


def _log(msg: str, steps: list[str] | None = None):
    print(msg, flush=True)
    if steps is not None:
        steps.append(msg)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request(url: str, *, params: dict | None = None, timeout: int = 60, tries: int = 3):
    """带 429/网络错误退避的请求。MPC 对高频访问会限流。"""
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                wait = 5 * (i + 1)
                _log(f"[throttle] 429，等待 {wait}s 后重试")
                time.sleep(wait)
                last = r
                continue
            return r
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    if isinstance(last, Exception):
        raise last
    return last


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)[-120:]


def _cloud(item) -> float | None:
    p = item.properties or {}
    for k in ("eo:cloud_cover", "s2:cloud_cover", "landsat:cloud_cover_land", "cloud_cover"):
        v = p.get(k)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
    return None


def _item_dt(item) -> datetime | None:
    dt = item.datetime
    if dt is not None:
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    props = item.properties or {}
    for key in ("start_datetime", "end_datetime"):
        v = props.get(key)
        if v:
            try:
                d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
            except Exception:
                pass
    # MODIS 产品 ID 形如 MOD13A1.A2026273.h26v04.061...
    m = re.search(r"\.A(\d{4})(\d{3})\.", item.id or "")
    if m:
        try:
            return datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc) + timedelta(days=int(m.group(2)) - 1)
        except Exception:
            pass
    return None


def _search(catalog: Client, collection: str, days: int, limit: int, sort_desc: bool = True):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    kwargs = dict(
        collections=[collection],
        bbox=AOI_BBOX,
        datetime=f"{_iso(start)}/{_iso(end)}",
        limit=limit,
    )
    items = []
    try:
        if sort_desc:
            kwargs["sortby"] = [{"field": "datetime", "direction": "desc"}]
        items = list(catalog.search(**kwargs).items())
    except Exception:
        kwargs.pop("sortby", None)
        try:
            items = list(catalog.search(**kwargs).items())
        except Exception as e:
            _log(f"[warn] {collection} 查询失败: {e}")
            return []
    if sort_desc:
        items.sort(key=lambda it: _item_dt(it) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items


def _search_direct(collection: str, limit: int = 10, timeout: int = 12):
    """直接调用 STAC /collections/{id}/items，避免 pystac-client 在限流时长时间挂起。"""
    url = f"{STAC_URL}/collections/{collection}/items"
    params = {"bbox": ",".join(str(x) for x in AOI_BBOX), "limit": limit}
    try:
        r = _request(url, params=params, timeout=timeout, tries=1)
        if not r.ok:
            return []
        feats = (r.json() or {}).get("features") or []
        items = []
        for f in feats:
            try:
                items.append(Item.from_dict(f))
            except Exception:
                pass
        items.sort(key=lambda it: _item_dt(it) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items
    except Exception:
        return []


def _preview_href(item) -> str | None:
    for key in ("rendered_preview", "preview", "visual", "thumbnail"):
        a = item.assets.get(key)
        if a and a.href:
            return a.href
    return None


def _download_image(href: str, name: str, max_px: int = 1100) -> str | None:
    fn = THUMBS / f"{_safe(name)}.jpg"
    if fn.exists() and fn.stat().st_size > 1000:
        return f"/data/thumbs/{fn.name}"
    try:
        r = _request(href, timeout=20, tries=1)
        if not r.ok or not r.content:
            return None
        im = Image.open(io.BytesIO(r.content))
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (12, 17, 24))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        im.save(fn, "JPEG", quality=86, optimize=True)
        return f"/data/thumbs/{fn.name}"
    except Exception as e:
        _log(f"[warn] 预览下载失败 {name}: {e}")
        return None


def _download_preview(item, name: str, max_px: int = 1100) -> str | None:
    """下载 MPC 服务端渲染的预览图，压缩成网页友好的 JPEG。返回 /data/thumbs/... 路径。"""
    href = _preview_href(item)
    if not href:
        return None
    return _download_image(href, name, max_px)


def _generic_preview(collection: str, item_id: str, asset: str, colormap: str, rescale: str) -> str | None:
    """对没有 rendered_preview 资产的产品，用 MPC 的 TiTiler 渲染端点生成预览。"""
    import urllib.parse
    q = urllib.parse.urlencode({
        "collection": collection,
        "item": item_id,
        "assets": asset,
        "colormap_name": colormap,
        "rescale": rescale,
    })
    return f"https://planetarycomputer.microsoft.com/api/data/v1/item/preview.png?{q}"


def _generic_tilejson(collection: str, item_id: str, asset: str, colormap: str, rescale: str) -> str | None:
    import urllib.parse
    q = urllib.parse.urlencode({
        "collection": collection,
        "item": item_id,
        "assets": asset,
        "colormap_name": colormap,
        "rescale": rescale,
    })
    return f"https://planetarycomputer.microsoft.com/api/data/v1/item/tilejson.json?{q}"


def _tilejson_from_href(href: str | None) -> tuple[str | None, list[float] | None, str | None]:
    if not href:
        return None, None, None
    try:
        r = _request(href, timeout=20, tries=1)
        if not r.ok:
            return None, None, href
        tj = r.json()
        tiles = tj.get("tiles") or []
        bounds = tj.get("bounds")
        return (tiles[0] if tiles else None), bounds, href
    except Exception:
        return None, None, href


def _tilejson(item) -> tuple[str | None, list[float] | None, str | None]:
    a = item.assets.get("tilejson")
    if not a:
        return None, None, None
    return _tilejson_from_href(a.href)


def _summary(item, collection: str, preview: str | None, tile_url: str | None,
             tilejson_href: str | None, bounds: list[float] | None) -> dict:
    dt = _item_dt(item)
    props = item.properties or {}
    return {
        "id": item.id,
        "collection": collection,
        "datetime": dt.isoformat() if dt else None,
        "cloud_cover": _cloud(item),
        "bbox": item.bbox or bounds,
        "preview": preview,
        "tile_url": tile_url,
        "tilejson": tilejson_href,
        "assets": sorted(item.assets.keys()),
        "platform": props.get("platform"),
        "center": AOI_CENTER,
        "source": "Microsoft Planetary Computer",
    }


def crawl_sentinel2(catalog: Client, steps: list[str]) -> list[dict]:
    _log("[1/4] 查询 Sentinel-2 L2A 最新场景…", steps)
    items = _search(catalog, S2_COLLECTION, days=75, limit=80, sort_desc=True)
    items = [it for it in items if (_cloud(it) is None or _cloud(it) <= 90)]
    if not items:
        _log("[warn] Sentinel-2 无可用场景", steps)
        return []
    items.sort(key=lambda it: _item_dt(it) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    latest_date = (_item_dt(items[0]) or datetime.now(timezone.utc)).date()
    picked = [it for it in items if _item_dt(it) and _item_dt(it).date() == latest_date]
    if len(picked) < 3:
        # 最新一天覆盖不全时，把前一天也带上，最多 8 景
        prev_dates = sorted({_item_dt(it).date() for it in items if _item_dt(it)}, reverse=True)
        for d in prev_dates[1:3]:
            picked += [it for it in items if _item_dt(it) and _item_dt(it).date() == d]
    picked = picked[:8]
    picked.sort(key=lambda it: _cloud(it) if _cloud(it) is not None else 999)

    out = []
    for idx, it in enumerate(picked):
        # 只为最新 2 景下载缩略图，其余用切片服务，减少 MPC 请求量
        preview = _download_preview(it, f"s2_{it.id}") if idx < 2 else None
        tile_url, bounds, tj = _tilejson(it)
        out.append(_summary(it, S2_COLLECTION, preview, tile_url, tj, bounds))
        time.sleep(0.4)
    _log(f"      Sentinel-2：{len(out)} 景，最新 {latest_date.isoformat()}", steps)
    return out


def crawl_landsat(catalog: Client, steps: list[str]) -> list[dict]:
    _log("[2/4] 查询 Landsat C2 L2 最新场景…", steps)
    items = _search(catalog, LANDSAT_COLLECTION, days=120, limit=30, sort_desc=True)
    items = [it for it in items if (_cloud(it) is None or _cloud(it) <= 95)]
    out = []
    for idx, it in enumerate(items[:6]):
        preview = _download_preview(it, f"landsat_{it.id}") if idx < 1 else None
        tile_url, bounds, tj = _tilejson(it)
        out.append(_summary(it, LANDSAT_COLLECTION, preview, tile_url, tj, bounds))
        time.sleep(0.4)
    _log(f"      Landsat：{len(out)} 景", steps)
    return out


def _choose_item(items, prefer_center: bool = True):
    if not items:
        return None
    if prefer_center:
        lon, lat = AOI_CENTER
        for it in items:
            b = it.bbox
            if b and len(b) >= 4 and b[0] <= lon <= b[2] and b[1] <= lat <= b[3]:
                return it
    # 取 datetime 最新的
    valid = [it for it in items if _item_dt(it)]
    if valid:
        return max(valid, key=lambda it: _item_dt(it))
    return items[0]


def crawl_natural(catalog: Client, steps: list[str]) -> list[dict]:
    _log("[4/4] 查询 MODIS / DEM / 土地覆盖 / 水 / 降水等自然数据…", steps)
    out = []
    # 静态/产品型集合的 MPC 原生预览很稳定；MODIS HDF 产品的服务端渲染容易慢，
    # 这里先取元数据，避免刷新被单个产品拖住。
    preview_ok = {
        "modis-13A1-061", "modis-14A2-061", "modis-17A2H-061", "modis-64A1-061",
        "cop-dem-glo-30", "io-lulc-annual-v02", "esa-worldcover", "jrc-gsw",
    }
    deadline = time.time() + 120
    for spec in NATURAL_COLLECTIONS:
        cid = spec["id"]
        started = time.time()
        if time.time() > deadline:
            out.append({**spec, "ok": False, "error": "natural crawl deadline"})
            _log(f"      [warn] {spec['name']}：超出自然数据抓取时限，跳过", steps)
            continue
        try:
            items = _search_direct(cid, limit=10, timeout=12)
            item = _choose_item(items, prefer_center=True)
            if not item:
                out.append({**spec, "ok": False, "error": "no item"})
                _log(f"      [warn] {spec['name']}：无可用条目", steps)
                continue
            asset = spec.get("asset")
            has_asset = bool(asset and asset in item.assets)
            want_preview = cid in preview_ok
            preview = _download_preview(item, f"nat_{cid}_{item.id}", max_px=1000) if want_preview else None
            tile_url, bounds, tj = _tilejson(item)
            if want_preview and not preview and has_asset:
                gp = _generic_preview(cid, item.id, asset, spec["colormap"], spec["rescale"])
                preview = _download_image(gp, f"nat_{cid}_{item.id}", max_px=1000)
            if want_preview and not tile_url and has_asset:
                gtj = _generic_tilejson(cid, item.id, asset, spec["colormap"], spec["rescale"])
                t2, b2, h2 = _tilejson_from_href(gtj)
                tile_url = t2 or tile_url
                bounds = bounds or b2
                tj = tj or h2
            row = _summary(item, cid, preview, tile_url, tj, bounds)
            row.update({
                "name": spec["name"],
                "type": spec["type"],
                "desc": spec["desc"],
                "asset": asset if has_asset else None,
                "ok": True,
                "preview_available": bool(preview or tile_url),
            })
            out.append(row)
            took = time.time() - started
            _log(f"      {spec['name']}：{item.id[:50]}（{took:.1f}s）", steps)
            time.sleep(0.35)
        except Exception as e:
            _log(f"      [warn] {spec['name']} 失败: {e}", steps)
            out.append({**spec, "ok": False, "error": str(e)[:200]})
    return out


def fetch_weather() -> dict:
    lats = ",".join(str(p["lat"]) for p in WEATHER_POINTS)
    lons = ",".join(str(p["lon"]) for p in WEATHER_POINTS)
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lats,
        "longitude": lons,
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,soil_moisture_0_to_10cm",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
        "timezone": "Asia/Shanghai",
        "forecast_days": 3,
    }
    r = _request(url, params=params, timeout=45, tries=3)
    r.raise_for_status()
    raw = r.json()
    rows = raw if isinstance(raw, list) else [raw]
    points = []
    for i, row in enumerate(rows):
        base = WEATHER_POINTS[i] if i < len(WEATHER_POINTS) else {"name": f"点{i+1}", "lon": None, "lat": None}
        points.append({
            "name": base["name"],
            "lon": base["lon"],
            "lat": base["lat"],
            "elevation": row.get("elevation"),
            "current": row.get("current") or {},
            "daily": row.get("daily") or {},
        })
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "Open-Meteo（实时气象，补充 MPC 自然数据）",
        "points": points,
    }


def fetch_elevations(coords: list[tuple[float, float]]) -> list[float | None]:
    """批量查询高程（Open-Meteo elevation API），每次最多 80 个点。"""
    out: list[float | None] = []
    for i in range(0, len(coords), 80):
        chunk = coords[i:i + 80]
        lats = ",".join(f"{c[1]:.6f}" for c in chunk)
        lons = ",".join(f"{c[0]:.6f}" for c in chunk)
        try:
            r = _request(
                "https://api.open-meteo.com/v1/elevation",
                params={"latitude": lats, "longitude": lons},
                timeout=45,
                tries=3,
            )
            r.raise_for_status()
            data = r.json().get("elevation") or []
            out.extend([float(x) if x is not None else None for x in data])
        except Exception:
            out.extend([None] * len(chunk))
        time.sleep(0.2)
    if len(out) < len(coords):
        out.extend([None] * (len(coords) - len(out)))
    return out[:len(coords)]


def run(force: bool = False) -> dict:
    steps: list[str] = []
    errors: list[str] = []
    started = time.time()
    _log(f"===== MPC 数据刷新开始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====", steps)
    catalog = Client.open(STAC_URL)
    s2 = crawl_sentinel2(catalog, steps)
    landsat = crawl_landsat(catalog, steps)
    _log("[3/4] 拉取 Open-Meteo 实时气象…", steps)
    try:
        weather = fetch_weather()
        _log(f"      气象点：{len(weather.get('points', []))} 个", steps)
    except Exception as e:
        weather = {"fetched_at": datetime.now(timezone.utc).isoformat(), "points": [], "error": str(e)}
        errors.append(f"weather: {e}")
        _log(f"      [warn] 气象失败: {e}", steps)
    natural = crawl_natural(catalog, steps)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "Microsoft Planetary Computer STAC API",
        "aoi": {"bbox": AOI_BBOX, "center": AOI_CENTER},
        "sentinel2": s2,
        "landsat": landsat,
        "natural": natural,
        "weather_file": "/data/weather.json",
        "errors": errors,
        "elapsed_sec": round(time.time() - started, 2),
        "steps": steps,
    }
    (DATA / "mpc_scenes.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "weather.json").write_text(json.dumps(weather, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"===== MPC 数据刷新完成，用时 {payload['elapsed_sec']}s =====", steps)
    return payload


if __name__ == "__main__":
    result = run(force="--force" in sys.argv)
    print(json.dumps({
        "s2": len(result["sentinel2"]),
        "landsat": len(result["landsat"]),
        "natural": len([x for x in result["natural"] if x.get("ok")]),
        "elapsed": result["elapsed_sec"],
        "errors": result["errors"],
    }, ensure_ascii=False, indent=2))

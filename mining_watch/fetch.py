"""STAC 检索与 COG 窗口读取。

只依赖标准库 + rasterio，不需要 GEE 账号。
默认数据源 Earth Search（AWS Open Data），无需签名、可直接 HTTP Range 读。

关键约束：
  - 同一 AOI 必须落在同一 MGRS 瓦片内，否则基线期与目标期网格不一致，无法做差。
    本模块按瓦片号分组，取占比最大的瓦片。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

# 逻辑波段 -> 数据源里可能的 asset key（兼容 Earth Search 与 Planetary Computer）
BAND_KEYS: dict[str, tuple[str, ...]] = {
    "blue": ("blue", "B02"),
    "green": ("green", "B03"),
    "red": ("red", "B04"),
    "nir": ("nir", "B08"),
    "swir16": ("swir16", "B11"),
    "swir22": ("swir22", "B12"),
    "scl": ("scl", "SCL"),
}

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MAX_RETRY="5",
    GDAL_HTTP_RETRY_DELAY="2",
    # libcurl 默认无超时，连接被对端半关时会永久阻塞
    GDAL_HTTP_TIMEOUT="45",
    GDAL_HTTP_CONNECTTIMEOUT="10",
    # 默认读块只有 16KB，跨洋链路上每块一次往返太贵
    CPL_VSIL_CURL_CHUNK_SIZE="2097152",
    VSI_CACHE="TRUE",
    VSI_CACHE_SIZE="64000000",
)

# 用于探测 GDAL 自身能否走 HTTPS 的公共 COG（小范围读一次即可）
_PROBE_COG = (
    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
    "50/S/QG/2024/8/S2B_50SQG_20240828_0_L2A/B11.tif"
)

_TUNNEL = None
_EXTRA_ENV: dict[str, str] = {}
_REWRITE = False


def configure_transport(verbose: bool = True) -> str:
    """确认 GDAL 能直连 HTTPS；不行就起本地隧道。

    本机 GDAL 的 curl+Schannel 对任意 https 站点都握手失败，而 Python 的
    OpenSSL 正常。此时让 GDAL 说 http://、由本进程转发到 https:// 上游，
    数据链路仍是加密的。
    """
    global _TUNNEL, _REWRITE

    import contextlib
    import io

    ok = False
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):  # 屏蔽 rasterio 解码错误产生的噪音
        try:
            with rasterio.Env(**GDAL_ENV, GDAL_HTTP_TIMEOUT="20"):
                with rasterio.open(_PROBE_COG):
                    ok = True
        except Exception:
            ok = False

    if ok:
        msg = "GDAL 直连 HTTPS 正常"
    else:
        import os

        from .tunnel import HttpsTunnel

        _TUNNEL = HttpsTunnel()
        port = _TUNNEL.start()
        proxy = f"http://127.0.0.1:{port}"
        _EXTRA_ENV["CPL_CURL_PROXY"] = proxy
        # 实测本机 GDAL 忽略 CPL_CURL_PROXY，但 curl 会读 http_proxy 环境变量。
        # 只设 http_proxy：STAC 走 https，不受影响。
        os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = proxy
        _REWRITE = True
        msg = f"GDAL 无法直连 HTTPS，已启用本地隧道 127.0.0.1:{port}（上游仍为 HTTPS）"

    if verbose:
        print(f"[transport] {msg}")
    return msg


def _env() -> dict[str, str]:
    return {**GDAL_ENV, **_EXTRA_ENV}


def _href(href: str) -> str:
    return href.replace("https://", "http://", 1) if _REWRITE else href


# --------------------------------------------------------------------------- STAC
def rfc3339_range(dt_range: str) -> str:
    """把 '2024-06-01/2024-08-31' 补成 STAC 要求的完整 RFC3339 区间。

    Earth Search 会拒绝只到日期的写法（BadRequest: datetime value is invalid）。
    开放区间（'../2024-08-31' 或 '2024-06-01/..'）原样保留端点。
    """
    if "/" not in dt_range:
        raise ValueError(f"时间区间格式应为 start/end，收到 {dt_range!r}")

    def _fix(s: str, end: bool) -> str:
        s = s.strip()
        if s in ("..", ""):
            return ".."
        if "T" in s:
            return s if s.endswith("Z") or "+" in s[10:] else f"{s}Z"
        return s + ("T23:59:59Z" if end else "T00:00:00Z")

    a, b = dt_range.split("/", 1)
    return f"{_fix(a, False)}/{_fix(b, True)}"


_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open_json(req: urllib.request.Request, rounds: int = 4) -> dict[str, Any]:
    """带重试的 JSON 请求。

    本机系统代理（127.0.0.1:7897）对部分域名会偶发 TLS 断连
    （SSLEOFError）。每轮先走系统代理，失败后立刻用直连再试一次。
    """
    last: Exception | None = None
    for r in range(rounds):
        for opener, label in (
            (urllib.request.build_opener(), "proxy"),
            (_DIRECT_OPENER, "direct"),
        ):
            try:
                with opener.open(req, timeout=60) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError:
                raise
            except Exception as e:  # 网络/TLS 类错误，换通道重试
                last = e
                if r == rounds - 1 and label == "direct":
                    break
                time.sleep(1.5 * (r + 1))
    raise RuntimeError(f"STAC 请求失败（已重试 {rounds} 轮）: {last}") from last


def search_items(
    endpoint: str,
    collection: str,
    bbox: Iterable[float],
    datetime_range: str,
    max_cloud: float,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """POST /search，返回 STAC item 列表。"""
    body = {
        "collections": [collection],
        "bbox": list(bbox),
        "datetime": rfc3339_range(datetime_range),
        "limit": limit,
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
    }
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/search",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "mining-watch/0.1"},
    )
    try:
        data = _open_json(req)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"STAC 检索失败 HTTP {e.code}: {detail or e.reason}") from e

    feats = data.get("features", [])
    if not feats:
        raise RuntimeError(
            f"该区域在 {datetime_range} 无符合云量<{max_cloud}% 的影像，"
            "可放宽 max_cloud 或更换时段"
        )
    return feats


def tile_of(item: dict[str, Any]) -> str:
    """提取 MGRS 瓦片号。"""
    t = item.get("properties", {}).get("s2:mgrs_tile")
    if t:
        return str(t)
    m = re.search(r"_(T?\d{2}[A-Z]{3})_", item.get("id", ""))
    return m.group(1) if m else "UNKNOWN"


def pick_scenes(items: list[dict[str, Any]], max_scenes: int) -> list[dict[str, Any]]:
    """选同一瓦片、云量最低、日期互不相同的若干景。"""
    if not items:
        raise RuntimeError("无可用影像")

    tile = Counter(tile_of(i) for i in items).most_common(1)[0][0]
    same = [i for i in items if tile_of(i) == tile]
    same.sort(key=lambda i: (i["properties"].get("eo:cloud_cover", 100), i["id"]))

    picked: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for it in same:
        d = (it["properties"].get("datetime") or "")[:10]
        if d in seen_dates:
            continue
        seen_dates.add(d)
        picked.append(it)
        if len(picked) >= max_scenes:
            break

    if not picked:
        raise RuntimeError(f"瓦片 {tile} 内无可用影像")
    return picked


def asset_href(item: dict[str, Any], band: str) -> str:
    for key in BAND_KEYS[band]:
        a = item.get("assets", {}).get(key)
        if a and a.get("href"):
            return a["href"]
    raise KeyError(f"影像 {item.get('id')} 缺少波段 {band}")


# ------------------------------------------------------------------------ 读取
def _window_for(src: rasterio.DatasetReader, bbox: Iterable[float]):
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", src.crs, *bbox, densify_pts=21
    )
    sb = src.bounds
    l, r = max(left, sb.left), min(right, sb.right)
    b, t = max(bottom, sb.bottom), min(top, sb.top)
    if l >= r or b >= t:
        raise RuntimeError(
            f"AOI 与影像范围无交集（来源 {src.name[:60]}...），请缩小 AOI 或换瓦片"
        )
    win = from_bounds(l, b, r, t, transform=src.transform)
    return win.round_offsets().round_lengths()


def _grid_for(
    bbox: Iterable[float], crs: Any, resolution: float
) -> tuple[Affine, int, int]:
    """由 AOI 经纬度框推导输出网格：等分辨率、北向上、左上角对齐。

    所有期次、所有波段都套用这一个网格，跨 MGRS 瓦片/UTM 带也能逐像元相减。
    """
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", crs, *bbox, densify_pts=21
    )
    res = float(resolution)
    W = max(1, int(round((right - left) / res)))
    H = max(1, int(round((top - bottom) / res)))
    return Affine(res, 0.0, left, 0.0, -res, top), H, W


def _read_band(
    item: dict[str, Any],
    band: str,
    bbox: Iterable[float],
    grid: tuple[Any, Any, int, int],
    attempts: int = 3,
) -> np.ndarray:
    """读单景单波段的一个窗口，输出对齐到统一网格。

    跨洋链路上偶发连接中断/分片截断，重开数据集重试比整批失败划算。
    """
    _, _, H, W = grid
    resampling = Resampling.nearest if band == "scl" else Resampling.bilinear
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with rasterio.Env(**_env()):
                with rasterio.open(_href(asset_href(item, band))) as src:
                    win = _window_for(src, bbox)
                    arr = src.read(
                        1, window=win, out_shape=(H, W), resampling=resampling
                    ).astype("float32")
                    nodata = src.nodata
            break
        except Exception as exc:  # 网络类错误：退避后重试
            last = exc
            if attempt == attempts - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    else:  # pragma: no cover
        raise last if last else RuntimeError("read failed")

    if nodata is not None:
        arr[arr == nodata] = np.nan
    if band != "scl":
        # L2A 反射率放大 10000 倍；自适应判断，兼容已归一化数据
        finite = arr[np.isfinite(arr)]
        if finite.size and np.nanpercentile(finite, 99) > 1.5:
            arr = arr / 10000.0
    return arr


def read_stack(
    items: list[dict[str, Any]],
    bbox: Iterable[float],
    resolution: int = 20,
    grid: tuple[Any, Any, int, int] | None = None,
    workers: int = 2,
) -> dict[str, Any]:
    """按统一网格读取多期多波段（波段级并发预取）。

    grid=(crs, transform, H, W) 可强制沿用既有网格——基线与目标期若落在
    不同 MGRS 瓦片，用同一 grid 读取即可保证逐像元可比。

    返回：
        bands: {band: ndarray(n_dates, H, W) float32}，已 /10000 归一化为反射率
        dates: list[str]
        transform, crs, shape
    """
    bands = ("blue", "green", "red", "nir", "swir16", "swir22", "scl")

    if grid is None:
        with rasterio.Env(**_env()):
            with rasterio.open(_href(asset_href(items[0], "swir16"))) as src:
                transform, H, W = _grid_for(bbox, src.crs, resolution)
                crs = src.crs
        print(f"[read] 网格 {W}x{H} @{resolution}m {crs}", flush=True)
    else:
        crs, transform, H, W = grid
        if isinstance(transform, (tuple, list)):
            transform = Affine(*transform[:6])
    grid = (crs, transform, H, W)

    jobs = [(i, band) for i in range(len(items)) for band in bands]
    out: dict[tuple[int, str], np.ndarray] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_read_band, items[i], band, bbox, grid): (i, band)
            for i, band in jobs
        }
        for fut in as_completed(futures):
            key = futures[fut]
            out[key] = fut.result()
            done += 1
            print(f"[read] {done}/{len(jobs)} {key[1]} 景{key[0] + 1}", flush=True)

    stacks = {b: [out[(i, b)] for i in range(len(items))] for b in bands}

    return {
        "bands": {b: np.stack(v) if v else np.empty((0, H, W), "float32") for b, v in stacks.items()},
        "dates": [(it["properties"].get("datetime") or "")[:10] for it in items],
        "clouds": [float(it["properties"].get("eo:cloud_cover", -1)) for it in items],
        "ids": [it["id"] for it in items],
        "transform": transform,
        "crs": crs,
        "shape": (H, W),
        "resolution": resolution,
    }

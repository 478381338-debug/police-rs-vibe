"""FastAPI 后端：数据服务 + 刷新 + 警务任务。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import requests
from pydantic import BaseModel

from app import aircase_api
from app import arrival_api
from app import case_api
from app import case_store
from app import config
from app import drone_api
from app import imagery_watch
from app import legal_api
from app import mobile_api
from app import operations_api
from app import risk as risk_model
from app import scenario
from app import security
from app import sixg_api
from app import yantai_api

ROOT = config.ROOT
DATA = config.DATA
WEB = config.WEB
APP_VERSION = os.getenv("POLICE_VERSION", "1.2.0")
APP_STARTED = time.time()


class _BoundedCache:
    """线程安全的 LRU 缓存，同时限制条目数和总字节数，防止被刷爆内存。"""

    def __init__(self, max_entries: int, max_bytes: int):
        self._data: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._lock = threading.Lock()

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def __getitem__(self, key: str) -> tuple[bytes, str]:
        with self._lock:
            item = self._data[key]
            self._data.move_to_end(key)
            return item

    def __setitem__(self, key: str, value: tuple[bytes, str]) -> None:
        data, ctype = value
        size = len(data)
        with self._lock:
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= len(old[0])
            self._data[key] = (data, ctype)
            self._bytes += size
            while self._data and (
                len(self._data) > self._max_entries or self._bytes > self._max_bytes
            ):
                _, evicted = self._data.popitem(last=False)
                self._bytes -= len(evicted[0])

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            self._data.move_to_end(key)
            return item

    def put(self, key: str, value: tuple[bytes, str]) -> None:
        self[key] = value

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._data), "bytes": self._bytes,
                    "max_entries": self._max_entries, "max_bytes": self._max_bytes}

_docs_disabled = os.getenv("POLICE_DISABLE_DOCS", "0").strip().lower() in {"1", "true", "yes", "on"}
app = FastAPI(
    title=config.APP_TITLE,
    description="警务遥感：生态安全与灾害风险遥感地图（历史异常 + 高发点危险等级 + 最新遥感/自然数据 + 自然条件推演 + 警务任务）",
    version=APP_VERSION,
    docs_url=None if _docs_disabled else "/docs",
    redoc_url=None if _docs_disabled else "/redoc",
    openapi_url=None if _docs_disabled else "/openapi.json",
)

# 同源部署不需要 CORS；只为本机开发端口和显式配置的来源放行。
# 真实警务部署应通过反向代理同源访问，并通过 POLICE_CORS_ORIGINS 精确增加来源。
_cors_origins = [
    "http://127.0.0.1:8080",
    "http://localhost:8080",
]
_cors_origins += [
    item.strip().rstrip("/")
    for item in os.getenv("POLICE_CORS_ORIGINS", "").split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(set(_cors_origins)),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
# 公网首屏最大的内容是 GeoJSON/JSON（道路 2.7MB、章丘道路 2.2MB）。
# 对这些文本响应启用 gzip，图片（JPEG/PNG）已压缩，不做二次压缩。
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(security.RateLimitMiddleware)
app.add_middleware(security.SecurityHeadersMiddleware)

_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH: dict = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "progress": 0,
    "message": "尚未刷新",
    "elapsed_sec": 0,
    "warnings": [],
}


# ---------------------------------------------------------------------------
# 静态文件
# ---------------------------------------------------------------------------
# 不再把整个 data/ 目录静态暴露：核查样本、原始缓存、任务文件都留在服务端。
# 前端只读取以下已公开的底图/边界数据。
app.mount("/data/zhangqiu", StaticFiles(directory=str(DATA / "zhangqiu")), name="data-zhangqiu")
app.mount("/data/external", StaticFiles(directory=str(DATA / "external")), name="data-external")
for mount_path, directory, name in (
    ("/data/thumbs", DATA / "thumbs", "data-thumbs"),
    ("/assets", config.ASSETS, "assets"),
    ("/vendor", WEB / "vendor", "vendor"),
    ("/static", WEB, "static"),
    ("/demo", ROOT / "docs" / "video" / "public", "demo-video"),
):
    if Path(directory).exists():
        app.mount(mount_path, StaticFiles(directory=str(directory)), name=name)

# ---------------------------------------------------------------------------
# 演示站访问口令：HTML 表单 + 签名 Cookie
# 设置 POLICE_WEB_USER / POLICE_WEB_PASSWORD 后生效；不设置则保持公开。
# 不用 HTTP Basic，避免部分浏览器/内嵌浏览器不弹原生口令框。
# ---------------------------------------------------------------------------
_WEB_USER = os.getenv("POLICE_WEB_USER", "").strip()
_WEB_PASSWORD = os.getenv("POLICE_WEB_PASSWORD", "")
_GATE_COOKIE = "police_gate"
_GATE_TTL = max(600, int(os.getenv("POLICE_GATE_HOURS", "12")) * 3600)
_GATE_MAX_FAILS = max(1, int(os.getenv("POLICE_GATE_MAX_FAILS", "5")))
_GATE_LOCK_SECONDS = max(60, int(float(os.getenv("POLICE_GATE_LOCK_MINUTES", "15")) * 60))

_GATE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>访问口令 · 警务遥感</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:
  radial-gradient(900px 500px at 15% 0%,rgba(56,189,248,.14),transparent 60%),
  radial-gradient(700px 420px at 90% 100%,rgba(34,211,238,.09),transparent 60%),
  #070c12;color:#e7eef7;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;padding:20px}
.card{width:100%;max-width:380px;background:#0f1823;border:1px solid #22364a;border-radius:18px;
  box-shadow:0 24px 70px rgba(0,0,0,.45);padding:30px 28px 24px;position:relative;overflow:hidden}
.card:before{content:"";position:absolute;inset:0 0 auto;height:3px;
  background:linear-gradient(90deg,#38bdf8,#22d3ee,#34d399)}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:22px}
.mark{width:38px;height:38px;border-radius:10px;display:grid;place-items:center;
  background:#0b2a33;border:1px solid #1d5665;color:#38bdf8;font-size:18px;font-weight:800}
.brand b{display:block;font-size:14px;letter-spacing:.03em}
.brand span{display:block;color:#5f7285;font-size:10px;letter-spacing:.12em;margin-top:2px}
h1{font-size:24px;margin:0 0 6px;letter-spacing:-.01em}
.lead{color:#8496a8;font-size:12px;line-height:1.7;margin:0 0 20px}
label{display:block;color:#8496a8;font-size:11px;margin:12px 0 6px}
input{width:100%;background:#0a141d;border:1px solid #22364a;color:#e7eef7;border-radius:9px;
  padding:11px 12px;font-size:14px;outline:none;transition:.15s}
input:focus{border-color:#38bdf8;box-shadow:0 0 0 3px rgba(56,189,248,.13)}
button{width:100%;margin-top:18px;border:0;border-radius:9px;padding:11px 14px;cursor:pointer;
  background:linear-gradient(135deg,#0ea5e9,#22d3ee);color:#04141b;font-size:14px;font-weight:800;letter-spacing:.02em}
button:hover{filter:brightness(1.08)}
.err{margin:12px 0 0;padding:9px 11px;border-radius:8px;background:rgba(255,91,82,.1);
  border:1px solid rgba(255,91,82,.35);color:#ff9b95;font-size:12px}
.foot{margin:18px 0 0;color:#5f7285;font-size:10.5px;line-height:1.7;text-align:center}
</style>
</head>
<body>
<main class="card">
  <div class="brand">
    <div class="mark">警</div>
    <div><b>警务遥感</b><span>POLICE REMOTE SENSING</span></div>
  </div>
  <h1>访问口令</h1>
  <p class="lead">这是授权演示入口。请输入访问账号与口令后进入；案件工作台仍需单独登录。</p>
  <form method="post" action="/__gate">
    <input type="hidden" name="next" value="__NEXT__">
    <label for="gate-user">账号</label>
    <input id="gate-user" name="username" autocomplete="username" placeholder="请输入访问账号" required autofocus>
    <label for="gate-pass">访问口令</label>
    <input id="gate-pass" name="password" type="password" autocomplete="current-password" placeholder="请输入访问口令" required>
    __ERROR__
    <button type="submit">进入平台</button>
  </form>
  <p class="foot">仅限授权人员使用 · 请勿在演示环境存放真实警务数据<br>登录状态保留 12 小时</p>
</main>
</body>
</html>"""


def _gate_signature(expires: int) -> str:
    return hmac.new(
        _WEB_PASSWORD.encode("utf-8"),
        f"{_WEB_USER}:{expires}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _gate_token() -> str:
    expires = int(time.time()) + _GATE_TTL
    return f"{expires}.{_gate_signature(expires)}"


def _gate_ok(request: Request) -> bool:
    raw = request.cookies.get(_GATE_COOKIE, "")
    try:
        expires_s, signature = raw.split(".", 1)
        expires = int(expires_s)
    except (ValueError, AttributeError):
        return False
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(signature, _gate_signature(expires))


def _safe_next(value: str | None) -> str:
    value = (value or "/").strip()
    if not value.startswith("/") or value.startswith("//") or "\r" in value or "\n" in value:
        return "/"
    return value


# 访问口令失败计数：按“客户端地址 + 账号”统计滑动窗口内的失败次数。
# 单进程 uvicorn 下用内存即可；多 worker 部署需要换成 Redis 等共享存储。
_gate_failures: dict[str, list[float]] = {}
_gate_failures_lock = threading.Lock()


def _gate_client_key(request: Request, username: str) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    host = forwarded or (request.client.host if request.client else "unknown")
    return f"{host}|{username.strip().lower()[:64]}"


def _gate_prune(now: float) -> None:
    for key in [k for k, v in _gate_failures.items() if not v or now - v[-1] > _GATE_LOCK_SECONDS]:
        _gate_failures.pop(key, None)


def _gate_lock_left(key: str) -> int:
    """返回剩余锁定秒数；0 表示未锁定。"""
    now = time.time()
    with _gate_failures_lock:
        stamps = [t for t in _gate_failures.get(key, []) if now - t < _GATE_LOCK_SECONDS]
        if stamps:
            _gate_failures[key] = stamps
        else:
            _gate_failures.pop(key, None)
        if len(stamps) < _GATE_MAX_FAILS:
            return 0
        return max(1, int(_GATE_LOCK_SECONDS - (now - stamps[-1])))


def _gate_note_failure(key: str) -> None:
    now = time.time()
    with _gate_failures_lock:
        stamps = [t for t in _gate_failures.get(key, []) if now - t < _GATE_LOCK_SECONDS]
        stamps.append(now)
        _gate_failures[key] = stamps
        if len(_gate_failures) > 4096:
            _gate_prune(now)


def _gate_clear_failures(key: str) -> None:
    with _gate_failures_lock:
        _gate_failures.pop(key, None)


def _gate_reject(request: Request, next_url: str, message: str, status: int, retry_after: int = 0) -> Response:
    headers = {"Cache-Control": "no-store"}
    if retry_after:
        headers["Retry-After"] = str(retry_after)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": message}, status_code=status, headers=headers)
    return Response(
        content=_gate_page(next_url, message),
        status_code=status,
        media_type="text/html; charset=utf-8",
        headers=headers,
    )


def _gate_page(next_url: str, error: str = "") -> str:
    error_html = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return (
        _GATE_HTML.replace("__NEXT__", html.escape(next_url, quote=True))
        .replace("__ERROR__", error_html)
    )


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


@app.middleware("http")
async def _access_gate(request: Request, call_next):
    if not _WEB_USER or not _WEB_PASSWORD:
        return await call_next(request)
    path = request.url.path
    if request.method == "OPTIONS" or path in ("/__gate", "/__gate/status", "/__gate/logout", "/favicon.ico"):
        return await call_next(request)
    if path in ("/api/health", "/api/health/ready"):
        return await call_next(request)
    if _gate_ok(request):
        return await call_next(request)
    # 空中办案/6G 相关页面统一回到站点根口令页登录，登录后回到原页面。
    if path in ("/aircase", "/sixg", "/sixg-lab"):
        target = path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse("/?next=" + quote(target, safe=""), status_code=303)
    if path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
        return JSONResponse(
            {"detail": "需要访问口令"},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
    if path == "/":
        gate_next = request.query_params.get("next") or "/"
    else:
        gate_next = path + (("?" + request.url.query) if request.url.query else "")
    return Response(
        content=_gate_page(_safe_next(gate_next)),
        status_code=401,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/__gate/status")
def _gate_status():
    return JSONResponse(
        {"enabled": bool(_WEB_USER and _WEB_PASSWORD)},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/__gate")
def _gate_form(request: Request):
    return Response(
        content=_gate_page(_safe_next(request.query_params.get("next"))),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/__gate")
async def _gate_login(request: Request):
    form = await request.form()
    next_url = _safe_next(str(form.get("next") or "/"))
    user = str(form.get("username") or "")
    password = str(form.get("password") or "")
    key = _gate_client_key(request, user)
    wait = _gate_lock_left(key)
    if wait:
        message = f"尝试次数过多，请 {max(1, (wait + 59) // 60)} 分钟后再试。"
        return _gate_reject(request, next_url, message, 429, retry_after=wait)
    # compare_digest 对非 ASCII 字符串会抛 TypeError，统一转 bytes 比较。
    if not (
        hmac.compare_digest(user.encode("utf-8"), _WEB_USER.encode("utf-8"))
        and hmac.compare_digest(password.encode("utf-8"), _WEB_PASSWORD.encode("utf-8"))
    ):
        _gate_note_failure(key)
        wait = _gate_lock_left(key)
        if wait:
            message = f"尝试次数过多，请 {max(1, (wait + 59) // 60)} 分钟后再试。"
            return _gate_reject(request, next_url, message, 429, retry_after=wait)
        return _gate_reject(request, next_url, "账号或访问口令不正确，请重新输入。", 401)
    _gate_clear_failures(key)
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        _GATE_COOKIE,
        _gate_token(),
        max_age=_GATE_TTL,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/__gate/logout")
def _gate_logout():
    response = RedirectResponse("/__gate", status_code=303)
    response.delete_cookie(_GATE_COOKIE, path="/")
    return response


@app.get("/data/china_provinces.json", include_in_schema=False)
def data_china_provinces():
    return FileResponse(
        DATA / "china_provinces.json",
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/data/china_outline.geojson", include_in_schema=False)
def data_china_outline():
    return FileResponse(
        DATA / "china_outline.geojson",
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/")
def index():
    return FileResponse(
        WEB / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/workspace")
def workspace_page():
    return FileResponse(
        WEB / "workspace.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/operations")
def operations_page():
    return FileResponse(
        WEB / "operations.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/sixg-lab")
def sixg_lab_page():
    """旧完整推演页入口兼容：统一到完整 6G 大屏。"""
    return RedirectResponse("/sixg", status_code=307)


@app.get("/sixg")
def sixg_page():
    """完整 6G 空天地一体推演大屏；从空中办案页的 6G 链路进入。"""
    return FileResponse(
        WEB / "sixg.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/yantai")
def yantai_page():
    """烟台非法采矿与河道采砂试点工作区。"""
    return FileResponse(
        WEB / "yantai.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/aircase")
def aircase_page():
    """空中办案：卫星初筛 → 无人机同机位精查 → 变化测量意见（新增页面）。"""
    return FileResponse(
        WEB / "aircase.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/m")
def mobile_page():
    """现场核查移动端。令牌放在 URL 片段，不进入服务端访问日志。"""
    return FileResponse(
        WEB / "mobile.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        WEB / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/mobile.webmanifest")
def mobile_manifest():
    return FileResponse(WEB / "mobile.webmanifest", media_type="application/manifest+json")


@app.get("/api/imagery/watch")
def imagery_watch_status(request: Request):
    """查看上次 Sentinel-2 到访检查结果；需登录。"""
    case_api.current_user(request)
    return imagery_watch.status()


@app.post("/api/imagery/watch/refresh")
async def imagery_watch_refresh(request: Request, days: int = 14, cloud_max: float = 60.0):
    """实时查询 MPC STAC；只建立“新场景”基线，不做异常识别。"""
    case_api.current_user(request)
    try:
        return await asyncio.to_thread(imagery_watch.refresh, days, cloud_max)
    except Exception as exc:  # noqa: BLE001 - 对外返回可操作的提示
        print(f"[imagery-watch] {exc}", flush=True)
        raise HTTPException(502, "STAC 查询失败，请稍后重试；本次未写入新状态。") from exc


app.include_router(case_api.router)
app.include_router(mobile_api.router)
app.include_router(arrival_api.router)
app.include_router(drone_api.router)
app.include_router(legal_api.router)
app.include_router(operations_api.router)
app.include_router(sixg_api.router)
app.include_router(yantai_api.router)
app.include_router(aircase_api.router)


@app.get("/zhangqiu")
def zhangqiu_page():
    # 章丘已并入主站同一张图：旧入口统一跳回主站，不再提供独立页面或 iframe。
    return RedirectResponse(url="/", status_code=307)


TILE_SOURCES = {
    "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "eox": "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg",
    "esri_topo": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    "osm_org": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "osm_fr": "https://a.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
    "opentopo": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
}
# 前端只认 osm / esri / esri_topo / opentopo 四个样式；每个样式按顺序试上游，全挂了返回本地占位瓦片。
# ArcGIS 对 python-requests 的请求直接 403，影像链因此必须有 EOX 兜底，否则整片灰块。
# 街道图只用 openstreetmap.org（z18 仍有真实瓦片）+ openstreetmap.fr（z≤17）：
#   - tile.openstreetmap.de 已对普通请求返回 404；
#   - cartocdn 无密钥时返回固定 2049 字节、满屏 "API KEY REQUIRED" 水印的瓦片，
#     属于 HTTP 200 的假成功，放进链路就会让最大缩放整片变成水印墙。
TILE_CHAINS = {
    "osm": ("osm_org", "osm_fr", "eox"),
    "esri": ("esri", "eox"),
    "esri_topo": ("esri_topo", "osm_org", "osm_fr", "eox"),
    "opentopo": ("opentopo", "osm_org", "osm_fr", "eox"),
}
# 底图代理缓存：内存 LRU + 磁盘落盘。演示机出网带宽有限，深缩放级别的瓦片必须能复用，
# 否则每次换视角都要重新拉取，录制与现场演示都会出现一半黑图。
_TILE_CACHE = _BoundedCache(max_entries=512, max_bytes=48 * 1024 * 1024)
_TILE_DISK = Path(__file__).resolve().parent.parent / "data" / "tile_cache"
_TILE_DISK_EXT = {".png": "image/png", ".jpg": "image/jpeg"}
_TILE_BREAK_SECONDS = 90
_TILE_BREAK_UNTIL: dict[str, float] = {}
# OSM 官方瓦片对并发很敏感：一次缩放会同时要 30+ 张瓦片，齐发时部分请求会被限流（429），
# 回退到影像源后同一屏里街道图和卫星图混排，看起来就是"底图接缝/花屏"。
# 给街道图源加并发闸门，让突发请求排队而不是被拒。
_TILE_LIMITS = {
    "osm_org": threading.BoundedSemaphore(4),
    "osm_fr": threading.BoundedSemaphore(4),
}
_TILE_PLACEHOLDER = None


def _tile_usable(data: bytes, ctype: str) -> bool:
    """上游会把空瓦片当成功返回，这类瓦片一旦缓存就会在底图上留下白洞。"""
    if not data or len(data) < 512 or not ctype.startswith("image/"):
        return False
    try:
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(data))
        im.verify()
        im = Image.open(_io.BytesIO(data)).convert("RGB")
    except Exception:
        return False
    # 整块只有一两种颜色＝上游没这块数据（opentopomap 在 z18+、osm.org 在 z19 都是这种纯色瓦片），
    # 颜色不一定全白，所以必须按"颜色数"判，而不是只判白。
    colors = im.getcolors(maxcolors=8)
    if colors is not None and len(colors) <= 2:
        return False
    small = im.resize((16, 16))
    # 近白瓦片（例如带轻微 JPEG 噪声的空图）同样按失败处理。
    return not all(mx >= 250 and mx - mn < 2 for mn, mx in small.getextrema())


def _tile_placeholder() -> bytes:
    global _TILE_PLACEHOLDER
    if _TILE_PLACEHOLDER is None:
        from PIL import Image, ImageDraw
        import io as _io
        img = Image.new("RGB", (256, 256), (236, 233, 226))
        draw = ImageDraw.Draw(img)
        for pos in range(0, 257, 32):
            draw.line([(pos, 0), (pos, 256)], fill=(226, 222, 214))
            draw.line([(0, pos), (256, pos)], fill=(226, 222, 214))
        buf = _io.BytesIO()
        img.save(buf, "PNG")
        _TILE_PLACEHOLDER = buf.getvalue()
    return _TILE_PLACEHOLDER


def _fetch_tile(src: str, z: int, x: int, y: int):
    """返回 (瓦片或 None, 是否该熔断该上游)。

    熔断只针对"上游整体挂了"：网络异常、401/403、5xx。
    200 但内容是空瓦片属于"这个坐标没数据"（海面、超出覆盖范围），换个坐标就好了，
    不能据此冻结整条链路，否则一张海面瓦片就能让之后 90 秒的所有瓦片都变占位图。
    429 是限流，属于暂时现象：先退避重试一次，仍被拒就放弃这张瓦片但不熔断。
    """
    url = TILE_SOURCES[src].format(z=z, x=x, y=y)
    # 多个上游（openstreetmap.org / eox / opentopomap）会按 User-Agent 分流：
    # 自定义 UA 拿到的是 200 + 103 字节纯色"假瓦片"，浏览器 UA 才是真图。
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*",
        "Referer": "http://127.0.0.1:8080/",
    }
    limiter = _TILE_LIMITS.get(src)
    for attempt in (0, 1):
        if limiter is not None:
            limiter.acquire()
        try:
            r = requests.get(url, timeout=8, headers=headers)
        except Exception:
            r = None
        finally:
            if limiter is not None:
                limiter.release()
        if r is None:
            if attempt:
                return None, True
            time.sleep(0.3)
            continue
        if r.status_code == 429:
            if attempt:
                return None, False
            time.sleep(0.8)
            continue
        if not r.ok:
            return None, r.status_code in (401, 403) or r.status_code >= 500
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
        if not _tile_usable(r.content, ctype):
            return None, False
        return (r.content, ctype), False
    return None, False


def _tile_from_disk(key: str):
    for ext, ctype in _TILE_DISK_EXT.items():
        path = _TILE_DISK / (key + ext)
        try:
            if path.is_file() and path.stat().st_size > 512:
                return path.read_bytes(), ctype
        except OSError:
            continue
    return None


def _tile_to_disk(key: str, data: bytes, ctype: str):
    ext = ".png" if "png" in ctype else ".jpg"
    path = _TILE_DISK / (key + ext)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        pass


@app.get("/api/tile/{style}/{z}/{x}/{y}")
def tile_proxy(style: str, z: int, x: int, y: int):
    """同源瓦片代理：避免公网域名下第三方底图瓦片被来源策略拦截。

    每个样式按 TILE_CHAINS 顺序回退，缓存按真实上游分键（避免影像瓦片被当成街道瓦片复用），
    全部上游都失败时返回本地占位瓦片，前端永远不会出现灰块或白洞。
    """
    if style not in TILE_CHAINS:
        raise HTTPException(404, "unknown tile style")
    if not (0 <= z <= 19):
        raise HTTPException(400, "bad zoom")
    n = 1 << z
    if not (0 <= x < n and 0 <= y < n):
        raise HTTPException(400, "bad tile coordinate")
    for src in TILE_CHAINS[style]:
        cache_key = f"{src}/{z}/{x}/{y}"
        cached = _TILE_CACHE.get(cache_key)
        if cached is None:
            cached = _tile_from_disk(cache_key)
            if cached is not None:
                _TILE_CACHE.put(cache_key, cached)
        if cached is not None:
            data, ctype = cached
            return Response(content=data, media_type=ctype, headers={
                "Cache-Control": "public, max-age=86400",
                "X-Tile-Source": src,
            })
        if time.monotonic() < _TILE_BREAK_UNTIL.get(src, 0.0):
            continue
        fetched, should_break = _fetch_tile(src, z, x, y)
        if should_break:
            # 上游整体不可用时短路一段时间，避免每个瓦片都先等一次超时。
            _TILE_BREAK_UNTIL[src] = time.monotonic() + _TILE_BREAK_SECONDS
        if fetched is None:
            continue
        _TILE_BREAK_UNTIL.pop(src, None)
        _TILE_CACHE.put(cache_key, fetched)
        _tile_to_disk(cache_key, *fetched)
        data, ctype = fetched
        return Response(content=data, media_type=ctype, headers={
            "Cache-Control": "public, max-age=86400",
            "X-Tile-Source": src,
        })
    return Response(content=_tile_placeholder(), media_type="image/png", headers={
        "Cache-Control": "no-store",
        "X-Tile-Source": "placeholder",
    })


@app.get("/favicon.ico")
def favicon():
    p = WEB / "favicon.svg"
    if p.exists():
        return FileResponse(p)
    return JSONResponse({"ok": False}, status_code=404)


# ---------------------------------------------------------------------------
# 基础数据
# ---------------------------------------------------------------------------
def _read_json(name: str, default=None):
    p = DATA / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


@app.get("/api/health")
def health():
    db_ok = case_store.DB_PATH.exists()
    evidence_ok = case_store.EVIDENCE_DIR.exists() and os.access(case_store.EVIDENCE_DIR, os.W_OK)
    key_ok = case_store.EVIDENCE_KEY_PATH.exists()
    status = "ok" if db_ok and evidence_ok else "degraded"
    return {
        "status": status,
        "version": APP_VERSION,
        "time": datetime.now(timezone.utc).isoformat(),
        "uptime_s": round(time.time() - APP_STARTED, 1),
        "data_ready": (DATA / "hotspots.geojson").exists(),
        "db": "ok" if db_ok else "missing",
        "evidence_dir": "ok" if evidence_ok else "unwritable",
        "evidence_key": "present" if key_ok else "missing",
        "demo_mode": os.getenv("POLICE_DEMO_MODE", "1").strip().lower() not in {"0", "false", "no", "off"},
        "refresh": _LAST_REFRESH,
    }


@app.get("/api/health/ready")
def health_ready():
    payload = health()
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 503)


@app.get("/api/config")
def get_config():
    return {
        "title": config.APP_TITLE,
        "subtitle": config.APP_SUBTITLE,
        "aoi": {"name": config.AOI_NAME, "bbox": config.AOI_BBOX,
                "center": config.AOI_CENTER, "zoom": config.AOI_ZOOM},
        "danger_levels": config.DANGER_LEVELS,
        "hazards": config.HAZARDS,
        "weather_points": config.WEATHER_POINTS,
        "data_sources": [
            {"name": "Microsoft Planetary Computer", "type": "遥感/自然数据", "url": "https://planetarycomputer.microsoft.com/"},
            {"name": "Open-Meteo", "type": "实时气象/高程", "url": "https://open-meteo.com/"},
            {"name": "OpenStreetMap", "type": "公开警务设施/道路", "url": "https://www.openstreetmap.org/"},
            {"name": "青山卫士任务二检测结果", "type": "历史异常（53 图斑）", "url": ""},
            {"name": "ArcGIS Pro", "type": "专题图导出", "url": ""},
            {"name": "MATLAB R2025b", "type": "AHP 风险建模", "url": ""},
        ],
        "disclaimer": (
            "5 个 validated 类别来自青山卫士任务二实测检测；screening/risk 为扩展规则筛查/气象风险，"
            "需人工核查，不作为定性结论。警务设施与道路来自 OpenStreetMap 公开数据（ODbL），不是内部警力部署；核查任务为平台生成建议。"
        ),
    }


@app.get("/api/summary")
def summary():
    return _read_json("summary.json", {})


@app.get("/api/hotspots")
def hotspots():
    return _read_json("hotspots.geojson", {"type": "FeatureCollection", "features": []})


@app.get("/api/incidents")
def incidents():
    return _read_json("incidents.geojson", {"type": "FeatureCollection", "features": []})


@app.get("/api/risk-grid")
def risk_grid():
    return _read_json("risk_grid.geojson", {"type": "FeatureCollection", "features": []})


@app.get("/api/police")
def police():
    return _read_json("police.geojson", {"type": "FeatureCollection", "features": []})


@app.get("/api/places")
def places():
    return _read_json("places.geojson", {"type": "FeatureCollection", "features": []})


@app.get("/api/roads")
def roads():
    return _read_json("roads.geojson", {"type": "FeatureCollection", "features": []})


@app.get("/api/aoi")
def aoi():
    return _read_json("aoi.geojson", {"type": "FeatureCollection", "features": []})


@app.get("/api/scenes")
def scenes():
    return _read_json("mpc_scenes.json", {})


@app.get("/api/weather")
def weather():
    return _read_json("weather.json", {})


@app.get("/api/catalog")
def catalog():
    return _read_json("catalog.json", {"hazards": config.HAZARDS})


# ---------------------------------------------------------------------------
# 多源开放数据（OSM / WorldPop / MPC / DEM）
# ---------------------------------------------------------------------------
# 地形瓦片缓存：最多 256 张 / 64MB，超出按 LRU 淘汰。
_TERRAIN_CACHE = _BoundedCache(max_entries=256, max_bytes=64 * 1024 * 1024)
TERRAIN_TILE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


@app.get("/api/terrain-color/{z}/{x}/{y}.png")
def terrain_color_tile(z: int, x: int, y: int):
    """Terrarium 高程解码为带晕渲的地形色瓦片，供 MapLibre 3D 铺在地形表面。"""
    if not (0 <= z <= 14):
        raise HTTPException(400, "bad terrain zoom")
    n = 1 << z
    if not (0 <= x < n and 0 <= y < n):
        raise HTTPException(400, "bad terrain coordinate")
    # 高倍率改用 OpenTopoMap 的真实地形底图，避免 30m DEM 在近景被放大成模糊色块。
    if z > 8:
        key = f"o:{z}/{x}/{y}"
        cached = _TERRAIN_CACHE.get(key)
        if cached is not None:
            data, ctype = cached
            return Response(content=data, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})
        try:
            r = requests.get(f"https://a.tile.opentopomap.org/{z}/{x}/{y}.png", timeout=20,
                             headers={"User-Agent": "EcoPolice-RemoteSensing/1.0", "Referer": "http://127.0.0.1:8080/"})
        except Exception as e:
            raise HTTPException(502, f"terrain detail upstream error: {e}")
        if not r.ok or not r.content:
            raise HTTPException(502, f"terrain detail upstream HTTP {r.status_code}")
        _TERRAIN_CACHE[key] = (r.content, "image/png")
        return Response(content=r.content, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    key = f"c:{z}/{x}/{y}"
    cached = _TERRAIN_CACHE.get(key)
    if cached is not None:
        data, ctype = cached
        return Response(content=data, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})
    try:
        r = None
        for attempt in range(3):
            r = requests.get(TERRAIN_TILE.format(z=z, x=x, y=y), timeout=20,
                             headers={"User-Agent": "EcoPolice-RemoteSensing/1.0"})
            if r.ok and r.content:
                break
            time.sleep(0.25)
    except Exception as e:
        raise HTTPException(502, f"terrain upstream error: {e}")
    if not r or not r.ok or not r.content:
        raise HTTPException(502, f"terrain upstream HTTP {getattr(r, 'status_code', 'unknown')}")
    try:
        import io
        import numpy as np
        from PIL import Image
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        a = np.asarray(im).astype(np.float32)
        elev = a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0
        # 地形色带：低地青绿 → 丘陵土黄 → 高山灰白
        stops = np.array([0.0, 0.12, 0.35, 0.62, 0.82, 1.0], dtype=np.float32)
        colors = np.array([
            [13, 59, 75], [24, 95, 91], [63, 122, 82],
            [154, 132, 83], [190, 177, 148], [245, 247, 248],
        ], dtype=np.float32)
        t = np.clip(elev / 4500.0, 0.0, 1.0)
        out = np.stack([np.interp(t, stops, colors[:, i]) for i in range(3)], axis=-1)
        # 简易晕渲：东西/南北梯度
        gy, gx = np.gradient(elev)
        shade = np.clip(0.78 + (-gx * 0.55 - gy * 0.35) / 120.0, 0.55, 1.12)
        out = np.clip(out * shade[..., None], 0, 255).astype(np.uint8)
        out[elev <= 0] = [16, 58, 86]
        buf = io.BytesIO()
        Image.fromarray(out, "RGB").save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
    except Exception as e:
        raise HTTPException(502, f"terrain color decode error: {e}")
    _TERRAIN_CACHE[key] = (data, "image/png")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/gmt/render")
def api_gmt_render(az: int = 140, el: int = 24):
    """Render a fresh GMT China 3D perspective for the requested view."""
    try:
        from app import gmt_render
        p = gmt_render.render(az=az, el=el)
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
    except Exception as e:
        raise HTTPException(503, f"GMT 渲染不可用：{e}")


@app.get("/api/terrain/{z}/{x}/{y}.png")
def terrain_tile(z: int, x: int, y: int):
    """同源 Terrarium 地形瓦片，供 MapLibre 交互式 3D 使用。"""
    if not (0 <= z <= 14):
        raise HTTPException(400, "bad terrain zoom")
    n = 1 << z
    if not (0 <= x < n and 0 <= y < n):
        raise HTTPException(400, "bad terrain coordinate")
    key = f"{z}/{x}/{y}"
    cached = _TERRAIN_CACHE.get(key)
    if cached is not None:
        data, ctype = cached
        return Response(content=data, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})
    try:
        r = requests.get(TERRAIN_TILE.format(z=z, x=x, y=y), timeout=20,
                         headers={"User-Agent": "EcoPolice-RemoteSensing/1.0"})
    except Exception as e:
        raise HTTPException(502, f"terrain upstream error: {e}")
    if not r.ok or not r.content:
        raise HTTPException(502, f"terrain upstream HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "image/png")
    _TERRAIN_CACHE[key] = (r.content, ctype)
    return Response(content=r.content, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})

EXT = DATA / "external"
_MPC_TILE_CACHE: dict[str, dict] = {}


def _external_json(name: str, default=None):
    p = EXT / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


@app.get("/api/open-data")
def api_open_data():
    sources = _external_json("sources.json", {"sources": []})
    mpc = _external_json("mpc_open_data.json", {"collections": {}})
    pop = _external_json("population_grid.geojson", {"features": []})
    poi = _external_json("osm_pois.geojson", {"features": []})
    roads = _external_json("osm_roads.geojson", {"features": []})
    bld = _external_json("osm_buildings.geojson", {"features": []})
    dem = _external_json("dem_meta.json", {})
    arc = {}
    arc_meta = config.ASSETS / "arcgis_open_data_meta.json"
    if arc_meta.exists():
        try:
            arc = json.loads(arc_meta.read_text(encoding="utf-8"))
        except Exception:
            arc = {}
    feats = pop.get("features", [])
    total = sum(int((f.get("properties") or {}).get("population", 0)) for f in feats)
    levels: dict[str, int] = {}
    for f in feats:
        p = f.get("properties") or {}
        lv = str(int(p.get("pop_level", 0)))
        levels[lv] = levels.get(lv, 0) + 1
    return {
        "generated_at": sources.get("generated_at"),
        "aoi": sources.get("aoi"),
        "population": {
            "total": total,
            "cells": len(feats),
            "max_cell": max([int((f.get("properties") or {}).get("population", 0)) for f in feats] or [0]),
            "levels": levels,
        },
        "counts": {
            "pois": len(poi.get("features", [])),
            "roads": len(roads.get("features", [])),
            "buildings": len(bld.get("features", [])),
        },
        "sources": sources.get("sources", []),
        "notes": sources.get("notes", []),
        "mpc": mpc.get("collections", {}),
        "dem": dem,
        "arcgis": arc,
        "attribution": "© OpenStreetMap contributors (ODbL) · WorldPop (CC BY 4.0) · Microsoft Planetary Computer · AWS Terrain Tiles",
    }


@app.get("/api/ndvi")
def api_ndvi():
    """逐格网 MODIS 16-day NDVI；无数据时明确返回 available=false。"""
    d = _external_json("ndvi_grid.json", None)
    if not d:
        return {"available": False, "message": "尚未提取 NDVI；运行 python -m crawler.ndvi_daily"}
    return {k: d.get(k) for k in ("source", "provider", "generated_at", "composite_dates",
                                  "grid_count", "valid_count", "fallback_count", "note", "cells")}


@app.get("/api/verification-samples")
def api_verification_samples():
    """本地历史异常样本 + 背景对照；弱标签，不宣称预报精度。"""
    from app import samples as sample_store
    r = sample_store.load()
    return {"summary": r.get("summary") or {}, "features": r.get("features") or []}


@app.get("/api/mpc-tile/{key}/{z}/{x}/{y}")
def mpc_tile(key: str, z: int, x: int, y: int):
    """同源代理 MPC 服务端渲染瓦片，避免公网页面直连 planetarycomputer 失败。"""
    mpc = _MPC_TILE_CACHE.get("data")
    if mpc is None:
        mpc = _external_json("mpc_open_data.json", {"collections": {}})
        _MPC_TILE_CACHE["data"] = mpc
    entry = (mpc.get("collections") or {}).get(key) or {}
    tiles = ((entry.get("tilejson") or {}).get("tiles") or [])
    if not tiles:
        raise HTTPException(404, "no tilejson for this layer")
    if not (0 <= z <= 24):
        raise HTTPException(400, "bad zoom")
    url = tiles[0].format(z=z, x=x, y=y)
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "EcoPolice-RemoteSensing/1.0"})
    except Exception as e:
        raise HTTPException(502, f"MPC tile upstream error: {e}")
    if not r.ok or not r.content:
        raise HTTPException(502, f"MPC tile HTTP {r.status_code}")
    return Response(content=r.content, media_type=r.headers.get("content-type", "image/png"),
                    headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------------------
# 自然条件推演：输入自然数据 → 危险系数 → 未来 14 天时空动画
# ---------------------------------------------------------------------------
@app.get("/api/scenario/defaults")
def scenario_defaults(lon: float | None = None, lat: float | None = None):
    return scenario.defaults(lon, lat)


@app.post("/api/scenario/assess")
def scenario_assess(body: dict):
    return scenario.assess(body)


@app.post("/api/scenario/timeline")
def scenario_timeline(body: dict):
    return scenario.timeline(body)


@app.post("/api/scenario/simulate")
def scenario_simulate(body: dict):
    return scenario.simulate(body)


# ---------------------------------------------------------------------------
# 刷新：抓最新 MPC/气象 + 重算危险等级
# ---------------------------------------------------------------------------
def _refresh_sync(force: bool = False, lock_held: bool = False) -> dict:
    global _LAST_REFRESH
    if not lock_held:
        if not _REFRESH_LOCK.acquire(blocking=False):
            return {"ok": False, "busy": True, "message": "已有刷新任务在进行，请稍候"}
    started = time.time()
    started_at = _LAST_REFRESH.get("started_at") if _LAST_REFRESH.get("status") == "running" else None
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    _LAST_REFRESH = {
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "progress": 3,
        "message": "正在连接 Microsoft Planetary Computer…",
        "elapsed_sec": 0,
        "warnings": [],
    }
    steps: list[str] = []
    warnings: list[str] = []

    def _set_progress(message: str, progress: float | None = None):
        if progress is None:
            if "[1/4]" in message:
                progress = 10
            elif "[2/4]" in message:
                progress = 22
            elif "[3/4]" in message:
                progress = 34
            elif "[4/4]" in message:
                progress = 46
            elif any(k in message for k in ("MODIS", "Copernicus", "ESA", "JRC", "GPM")):
                progress = min(72, float(_LAST_REFRESH.get("progress", 46)) + 1.2)
            elif "MPC 数据刷新完成" in message:
                progress = 74
            else:
                progress = float(_LAST_REFRESH.get("progress", 3))
        _LAST_REFRESH.update(
            status="running",
            message=message,
            progress=round(float(progress), 1),
            elapsed_sec=round(time.time() - started, 1),
        )

    try:
        # 1) MPC + 气象
        try:
            from crawler import mpc_crawler
            mpc = mpc_crawler.run(force=force, on_step=_set_progress)
            steps.extend(mpc.get("steps") or [])
            warnings.extend([f"MPC: {e}" for e in (mpc.get("errors") or [])])
        except Exception as e:
            warnings.append(f"MPC 爬虫失败，使用缓存数据：{e}")
            steps.append("MPC 抓取失败，回退到本地缓存")
            _set_progress("MPC 抓取失败，回退到本地缓存；正在重算危险等级…", 60)

        # 1.5) 逐格网 NDVI（MCD43A4 每日 NBAR，2 天缓存）
        _set_progress("正在检查 MODIS 16 天 NDVI 缓存…", 76)
        try:
            from crawler import ndvi_daily
            nd = ndvi_daily.ensure(max_age_days=2, force=False)
            steps.append(f"NDVI 已就绪：{nd.get('valid_count', 0)}/{nd.get('grid_count', 0)} 格网，"
                         f"合成日期 {', '.join((nd.get('composite_dates') or [])[:2])}")
        except Exception as e:
            warnings.append(f"NDVI 更新失败，沿用已有缓存：{e}")

        # 2) 风险模型
        _set_progress("MPC 与气象数据已就绪，正在重算高发点危险等级…", 80)
        try:
            s = risk_model.run()
            steps.append(f"危险等级已重算：{s.get('hotspots', 0)} 个高发点，"
                         f"{len(s.get('changes', []))} 个等级变化")
        except Exception as e:
            warnings.append(f"风险模型失败：{e}")
            s = _read_json("summary.json", {})
        _set_progress("危险等级重算完成，正在整理刷新结果…", 96)

        elapsed = round(time.time() - started, 1)
        _LAST_REFRESH = {
            "status": "done",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "progress": 100,
            "message": f"刷新完成，用时 {elapsed}s",
            "elapsed_sec": elapsed,
            "warnings": warnings,
            "summary": s,
            "steps": steps[-20:],
        }
        return {
            "ok": True,
            "steps": steps,
            "warnings": warnings,
            "summary": s,
            "elapsed_sec": elapsed,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        elapsed = round(time.time() - started, 1)
        _LAST_REFRESH = {
            "status": "error",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "progress": 100,
            "message": f"刷新失败：{e}",
            "elapsed_sec": elapsed,
            "warnings": warnings,
            "error": str(e),
        }
        return {"ok": False, "steps": steps, "warnings": warnings, "error": str(e)}
    finally:
        _REFRESH_LOCK.release()


@app.post("/api/refresh")
async def refresh(force: bool = False):
    result = await asyncio.to_thread(_refresh_sync, force)
    if result.get("busy"):
        raise HTTPException(409, result.get("message", "刷新进行中"))
    return result


@app.post("/api/refresh/start")
def refresh_start(force: bool = False):
    """Start refresh in a worker thread and return immediately.

    The browser polls /api/refresh/status; no long-lived HTTP request is held
    open through the public tunnel while MPC crawling takes ~40 seconds.
    """
    global _LAST_REFRESH
    if not _REFRESH_LOCK.acquire(blocking=False):
        raise HTTPException(409, "已有刷新任务在进行，请稍候")
    started_at = datetime.now(timezone.utc).isoformat()
    _LAST_REFRESH = {
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "progress": 2,
        "message": "刷新已启动，正在连接 Microsoft Planetary Computer…",
        "elapsed_sec": 0,
        "warnings": [],
    }
    try:
        threading.Thread(
            target=_refresh_sync,
            kwargs={"force": force, "lock_held": True},
            name="mpc-refresh",
            daemon=True,
        ).start()
    except Exception as e:
        _REFRESH_LOCK.release()
        _LAST_REFRESH = {
            "status": "error",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "progress": 100,
            "message": f"刷新线程启动失败：{e}",
            "elapsed_sec": 0,
            "warnings": [],
            "error": str(e),
        }
        raise HTTPException(500, f"刷新线程启动失败：{e}")
    return {"ok": True, "status": "started", "started_at": started_at, "message": "刷新已启动"}


@app.get("/api/refresh/status")
def refresh_status():
    out = dict(_LAST_REFRESH)
    if out.get("status") == "running" and out.get("started_at"):
        try:
            started = datetime.fromisoformat(out["started_at"])
            out["elapsed_sec"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# MATLAB 重算（可选按钮）
# ---------------------------------------------------------------------------
@app.post("/api/model/recompute")
async def model_recompute():
    def _run():
        try:
            from app.matlab_bridge import run_matlab_and_rebuild
            return run_matlab_and_rebuild()
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# ArcGIS 专题图导出（可选按钮）
# ---------------------------------------------------------------------------
@app.post("/api/export/arcgis")
async def export_arcgis():
    def _run():
        try:
            from app.arcgis_bridge import run_arcgis_export
            return run_arcgis_export()
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return await asyncio.to_thread(_run)


# 旧的 /api/tasks 演示接口已由 app/case_api.py 的案件、任务、证据、复核接口取代。
# 不保留写 data/tasks.json 的兼容层，避免前端误把演示记录当成真实派警。


# ---------------------------------------------------------------------------
# 启动：首次缺数据时自动构建
# ---------------------------------------------------------------------------
def _ensure_data():
    try:
        if not (DATA / "incidents.geojson").exists():
            from crawler import build_data
            build_data.main()
        if not (DATA / "hotspots.geojson").exists():
            risk_model.run()
    except Exception as e:
        print(f"[startup] 数据初始化失败: {e}", flush=True)


@app.on_event("startup")
async def on_startup():
    # 案件库必须先生成表；数据补齐仍放后台，避免首屏被大文件阻塞。
    try:
        case_store.init_db()
    except Exception as e:
        print(f"[startup] 案件库初始化失败: {e}", flush=True)
    threading.Thread(target=_ensure_data, daemon=True).start()

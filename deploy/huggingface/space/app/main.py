"""FastAPI 后端：数据服务 + 刷新 + 警务任务。"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import requests
from pydantic import BaseModel

from app import config
from app import risk as risk_model
from app import scenario

ROOT = config.ROOT
DATA = config.DATA
WEB = config.WEB

app = FastAPI(
    title=config.APP_TITLE,
    description="警务遥感：生态安全与灾害风险一张图（历史异常 + 高发点危险等级 + 最新遥感/自然数据 + 自然条件推演 + 警务任务）",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_REFRESH_LOCK = threading.Lock()
_TASK_LOCK = threading.Lock()
_LAST_REFRESH: dict = {"status": "idle", "started_at": None, "finished_at": None, "message": "尚未刷新"}


# ---------------------------------------------------------------------------
# 静态文件
# ---------------------------------------------------------------------------
app.mount("/data", StaticFiles(directory=str(DATA)), name="data")
app.mount("/assets", StaticFiles(directory=str(config.ASSETS)), name="assets")
app.mount("/vendor", StaticFiles(directory=str(WEB / "vendor")), name="vendor")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


TILE_STYLES = {
    "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "esri_topo": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
}


@app.get("/api/tile/{style}/{z}/{x}/{y}")
def tile_proxy(style: str, z: int, x: int, y: int):
    """同源瓦片代理：避免公网域名下第三方底图瓦片被来源策略拦截。"""
    if style not in TILE_STYLES:
        raise HTTPException(404, "unknown tile style")
    if not (0 <= z <= 19):
        raise HTTPException(400, "bad zoom")
    n = 1 << z
    if not (0 <= x < n and 0 <= y < n):
        raise HTTPException(400, "bad tile coordinate")
    url = TILE_STYLES[style].format(z=z, x=x, y=y)
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "EcoPolice-RemoteSensing/1.0 (academic demo)",
            "Referer": "http://127.0.0.1:8080/",
        })
    except Exception as e:
        raise HTTPException(502, f"tile upstream error: {e}")
    if not r.ok or not r.content:
        raise HTTPException(502, f"tile upstream HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "image/jpeg")
    return Response(content=r.content, media_type=ctype, headers={
        "Cache-Control": "public, max-age=86400",
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
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "data_ready": (DATA / "hotspots.geojson").exists(),
        "refresh": _LAST_REFRESH,
    }


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
            {"name": "青山卫士任务二检测结果", "type": "历史异常（53 图斑）", "url": ""},
            {"name": "ArcGIS Pro", "type": "专题图导出", "url": ""},
            {"name": "MATLAB R2025b", "type": "AHP 风险建模", "url": ""},
        ],
        "disclaimer": (
            "5 个 validated 类别来自青山卫士任务二实测检测；screening/risk 为扩展规则筛查/气象风险，"
            "需人工核查，不作为定性结论。警务点位与路网为演示示意数据。"
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


# ---------------------------------------------------------------------------
# 刷新：抓最新 MPC/气象 + 重算危险等级
# ---------------------------------------------------------------------------
def _refresh_sync(force: bool = False) -> dict:
    global _LAST_REFRESH
    if not _REFRESH_LOCK.acquire(blocking=False):
        return {"ok": False, "busy": True, "message": "已有刷新任务在进行，请稍候"}
    started = time.time()
    _LAST_REFRESH = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                     "finished_at": None, "message": "正在抓取最新地理数据"}
    steps: list[str] = []
    warnings: list[str] = []
    try:
        # 1) MPC + 气象
        try:
            from crawler import mpc_crawler
            mpc = mpc_crawler.run(force=force)
            steps.extend(mpc.get("steps") or [])
            warnings.extend([f"MPC: {e}" for e in (mpc.get("errors") or [])])
        except Exception as e:
            warnings.append(f"MPC 爬虫失败，使用缓存数据：{e}")
            steps.append("MPC 抓取失败，回退到本地缓存")

        # 2) 风险模型
        try:
            s = risk_model.run()
            steps.append(f"危险等级已重算：{s.get('hotspots', 0)} 个高发点，"
                         f"{len(s.get('changes', []))} 个等级变化")
        except Exception as e:
            warnings.append(f"风险模型失败：{e}")
            s = _read_json("summary.json", {})

        elapsed = round(time.time() - started, 1)
        _LAST_REFRESH = {
            "status": "done",
            "started_at": _LAST_REFRESH.get("started_at"),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "message": f"刷新完成，用时 {elapsed}s",
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
        _LAST_REFRESH = {
            "status": "error",
            "started_at": _LAST_REFRESH.get("started_at"),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "message": f"刷新失败：{e}",
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


@app.get("/api/refresh/status")
def refresh_status():
    return _LAST_REFRESH


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


# ---------------------------------------------------------------------------
# 警务任务（演示级）
# ---------------------------------------------------------------------------
class TaskIn(BaseModel):
    hotspot_id: str
    hotspot_name: Optional[str] = None
    assignee: Optional[str] = None
    priority: Optional[str] = None
    note: Optional[str] = None


def _tasks_path() -> Path:
    return DATA / "tasks.json"


def _load_tasks() -> list[dict]:
    p = _tasks_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_tasks(rows: list[dict]):
    _tasks_path().write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/tasks")
def list_tasks():
    return _load_tasks()


@app.post("/api/tasks")
def create_task(body: TaskIn):
    with _TASK_LOCK:
        rows = _load_tasks()
        task = {
            "id": f"TASK-{len(rows) + 1:04d}",
            "hotspot_id": body.hotspot_id,
            "hotspot_name": body.hotspot_name,
            "assignee": body.assignee or "就近派出所（演示）",
            "priority": body.priority or "按危险等级",
            "status": "待接收",
            "note": body.note or "由生态警务平台生成（演示任务）",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "demo": True,
        }
        rows.append(task)
        _save_tasks(rows)
        return task


@app.post("/api/tasks/{task_id}/status")
def update_task(task_id: str, status: str = "已接收"):
    with _TASK_LOCK:
        rows = _load_tasks()
        for r in rows:
            if r["id"] == task_id:
                r["status"] = status
                r["updated_at"] = datetime.now(timezone.utc).isoformat()
                _save_tasks(rows)
                return r
    raise HTTPException(404, "任务不存在")


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
    # 不阻塞启动；后台补齐数据
    threading.Thread(target=_ensure_data, daemon=True).start()

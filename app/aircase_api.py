"""空中办案 REST 接口。所有能力为新增，路由前缀 /api/aircase，不影响既有接口。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app import aircase

router = APIRouter(prefix="/api/aircase", tags=["aircase"])


class SortieRequest(BaseModel):
    patch_id: str = Field(..., min_length=2, max_length=8)
    mode: Literal["precise", "scene"] = "precise"
    rel_height_m: float = Field(120.0, ge=40.0, le=300.0)


@router.get("/overview")
def overview() -> dict:
    """演示 AOI、候选图斑、最近任务。"""
    try:
        return aircase.overview()
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/sortie")
def create_sortie(payload: SortieRequest) -> dict:
    """为指定图斑生成同机位航线与精查任务。"""
    try:
        return aircase.create_sortie(payload.patch_id, payload.mode, payload.rel_height_m)
    except KeyError as exc:
        raise HTTPException(404, "未找到指定图斑") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/sortie/{sortie_id}")
def get_sortie(sortie_id: str) -> dict:
    try:
        return aircase.get_sortie(sortie_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/sortie/{sortie_id}/run")
def run_sortie(sortie_id: str) -> dict:
    """执行变化检测、面积量算、测量意见与证据清单（幂等）。"""
    try:
        return aircase.run_sortie(sortie_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/sortie/{sortie_id}/measure")
def measure(sortie_id: str, format: str = Query("md", pattern="^(md|html|json)$")) -> Response:
    try:
        path, media = aircase.measure_content(sortie_id, format)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    headers = {"Cache-Control": "no-cache", "Content-Disposition": "inline"}
    return Response(content=path.read_bytes(), media_type=media, headers=headers)


@router.get("/sortie/{sortie_id}/scene-map")
def scene_map(sortie_id: str) -> FileResponse:
    try:
        path = aircase.asset_path(sortie_id, "scene_map.png")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "该任务尚未生成现场方位图，请先执行精查") from exc
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.post("/sortie/{sortie_id}/verify")
def verify(sortie_id: str) -> dict:
    """重算逐文件 SHA-256、清单根哈希与 HMAC 签名。"""
    try:
        return aircase.verify_sortie(sortie_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/demo/{name}")
def demo_asset(name: str) -> FileResponse:
    """初始三维态势使用的演示影像（不依赖具体任务）。"""
    if name not in {"before.png", "after.png", "before_hd.png", "after_hd.png"}:
        raise HTTPException(404, "未找到演示影像")
    path = aircase.DEMO / name
    if not path.is_file():
        raise HTTPException(404, "演示影像尚未生成")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.get("/asset/{sortie_id}/{name}")
def asset(sortie_id: str, name: str) -> FileResponse:
    try:
        path = aircase.asset_path(sortie_id, name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, "文件名不合法") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    suffix = path.suffix.lower()
    media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".kml": "application/vnd.google-earth.kml+xml",
        ".csv": "text/csv; charset=utf-8",
        ".geojson": "application/geo+json",
        ".md": "text/markdown; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json",
    }.get(suffix, "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})
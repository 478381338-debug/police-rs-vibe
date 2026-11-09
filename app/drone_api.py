"""无人机航线规划 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import drone
from app.case_api import current_user
from app.drone_models import DronePlanIn

router = APIRouter(prefix="/api/drone", tags=["drone"])


@router.post("/plan")
def plan(body: DronePlanIn, user: dict = Depends(current_user)):
    try:
        return drone.plan_drone_route(
            body.lon,
            body.lat,
            pattern=body.pattern,
            size_km=body.size_km,
            spacing_m=body.spacing_m,
            rel_height_m=body.rel_height_m,
            speed_mps=body.speed_mps,
            direction_deg=body.direction_deg,
            sample_m=body.sample_m,
            title=body.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"航线规划失败：{exc}") from exc
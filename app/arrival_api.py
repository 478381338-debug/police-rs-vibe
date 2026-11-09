"""车行到达圈 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import arrival
from app.case_api import current_user

router = APIRouter(prefix="/api/arrival", tags=["arrival"])


class ArrivalIn(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    minutes: int = Field(default=30, ge=1, le=180)
    refresh: bool = False


@router.post("/circle")
def circle(body: ArrivalIn, user: dict = Depends(current_user)):
    try:
        return arrival.arrival_circle(body.lon, body.lat, body.minutes, body.refresh)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"到达圈计算失败：{exc}") from exc

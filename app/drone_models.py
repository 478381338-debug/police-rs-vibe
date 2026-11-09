"""无人机航线请求模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DroneOptions(BaseModel):
    pattern: Literal["lawnmower", "perimeter"] = "lawnmower"
    size_km: float = Field(default=0.6, ge=0.05, le=5)
    spacing_m: float = Field(default=80, ge=10, le=1000)
    rel_height_m: float = Field(default=80, ge=20, le=500)
    speed_mps: float = Field(default=8, ge=2, le=25)
    direction_deg: float = Field(default=0, ge=0, lt=360)
    sample_m: float = Field(default=50, ge=10, le=500)
    title: str = Field(default="无人机核查航线", max_length=120)


class DronePlanIn(DroneOptions):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
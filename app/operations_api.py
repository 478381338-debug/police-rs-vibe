"""五类扩展场景的 API。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import case_api, case_store, operations

router = APIRouter(prefix="/api/operations", tags=["operations"])


class EventIn(BaseModel):
    name: str = Field(default="大型活动", max_length=80)
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    radius_km: float = Field(default=3.0, ge=0.2, le=10.0)
    people: int = Field(default=0, ge=0, le=1_000_000)
    level: Literal["一级", "二级", "三级"] = "三级"


class LinearIn(BaseModel):
    start_lon: float = Field(ge=-180, le=180)
    start_lat: float = Field(ge=-90, le=90)
    end_lon: float = Field(ge=-180, le=180)
    end_lat: float = Field(ge=-90, le=90)
    sample_km: float = Field(default=2.0, ge=0.2, le=20.0)
    buffer_km: float = Field(default=1.0, ge=0.1, le=10.0)
    title: str = Field(default="线性巡护", max_length=80)


class ReferralIn(BaseModel):
    department: str = Field(min_length=2, max_length=60)
    contact: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=1000)
    due_at: str | None = Field(default=None, max_length=40)


class ReferralUpdateIn(BaseModel):
    status: Literal["待接收", "已接收", "已反馈", "已退回"]
    feedback: str | None = Field(default=None, max_length=2000)


def _fail(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(502, "研判服务暂时不可用，请稍后重试") from exc


@router.get("/options")
def options(user: dict = Depends(case_api.current_user)):
    return operations.referral_options()


@router.get("/eco")
def eco(user: dict = Depends(case_api.current_user)):
    return operations.eco_overview(user)


@router.get("/emergency")
def emergency(
    hazard: str | None = Query(default=None),
    user: dict = Depends(case_api.current_user),
):
    try:
        return operations.emergency_overview(hazard)
    except Exception as exc:
        _fail(exc)


@router.post("/event")
def event(body: EventIn, user: dict = Depends(case_api.current_user)):
    try:
        return operations.event_security(
            body.name, body.lon, body.lat, body.radius_km, body.people, body.level
        )
    except Exception as exc:
        _fail(exc)


@router.post("/linear")
def linear(body: LinearIn, user: dict = Depends(case_api.current_user)):
    try:
        return operations.linear_patrol(
            body.start_lon, body.start_lat, body.end_lon, body.end_lat,
            body.sample_km, body.buffer_km, body.title,
        )
    except Exception as exc:
        _fail(exc)


@router.get("/referrals")
def referrals(
    case_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(case_api.current_user),
):
    return {"items": case_store.list_referrals(user, case_id, status, limit)}


@router.post("/cases/{case_id}/referrals")
def create_referral(
    case_id: str,
    body: ReferralIn,
    user: dict = Depends(case_api.current_user),
):
    try:
        return case_store.create_referral(
            user, case_id, body.department, body.contact, body.note, body.due_at
        )
    except Exception as exc:
        _fail(exc)


@router.patch("/referrals/{referral_id}")
def update_referral(
    referral_id: str,
    body: ReferralUpdateIn,
    user: dict = Depends(case_api.current_user),
):
    try:
        return case_store.update_referral(user, referral_id, body.status, body.feedback)
    except Exception as exc:
        _fail(exc)

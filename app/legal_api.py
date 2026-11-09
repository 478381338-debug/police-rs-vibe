"""法条与证据清单检索 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app import legal
from app.case_api import current_user

router = APIRouter(prefix="/api/legal", tags=["legal"])


@router.get("/meta")
def meta(user: dict = Depends(current_user)):
    return legal.meta()


@router.get("/hazards")
def hazards(user: dict = Depends(current_user)):
    return {"items": [{"code": code, "label": label} for code, label in legal.HAZARD_LABELS.items()]}


@router.get("/search")
def search(
    q: str = Query("", max_length=120),
    hazard: str = Query("", max_length=40),
    limit: int = Query(20, ge=1, le=50),
    user: dict = Depends(current_user),
):
    return legal.search(q, hazard or None, limit)


@router.get("/checklist")
def checklist(
    q: str = Query("", max_length=120),
    hazard: str = Query("", max_length=40),
    limit: int = Query(20, ge=1, le=50),
    user: dict = Depends(current_user),
):
    return legal.checklist(q, hazard or None, limit)


@router.get("/entry/{entry_id}")
def entry(entry_id: str, user: dict = Depends(current_user)):
    item = legal.get_entry(entry_id)
    if item is None:
        raise HTTPException(status_code=404, detail="未找到该法条条目")
    return item
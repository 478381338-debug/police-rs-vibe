"""案件工作台 API：登录、案件、任务、证据、复核、报告与二维码。"""
from __future__ import annotations

import csv
import base64
import hashlib
import json
import mimetypes
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import arrival
from app import case_store as store
from app import drone
from app import legal
from app.drone_models import DroneOptions

router = APIRouter(prefix="/api", tags=["case-workbench"])

MAX_EVIDENCE_BYTES = int(os.getenv("POLICE_MAX_EVIDENCE_MB", "50")) * 1024 * 1024
ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/webp", "application/pdf",
    "text/plain", "video/mp4", "video/quicktime",
}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".txt", ".mp4", ".mov"}


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=256)


class CaseCreateIn(BaseModel):
    source_type: Literal["hotspot", "grid", "imagery"] = "hotspot"
    source_id: str | None = None
    hotspot_id: str | None = None
    title: str | None = None
    summary: str | None = None
    assignee_id: int | None = None
    due_at: str | None = None


class ArrivalTokenIn(BaseModel):
    hours: int = Field(default=24, ge=1, le=720)
    max_uses: int = Field(default=0, ge=0, le=10000)
    note: str | None = Field(default=None, max_length=300)


class VerificationIn(BaseModel):
    verdict: Literal["confirmed", "excluded", "inconclusive"]
    note: str | None = Field(default=None, max_length=1000)
    evidence_id: str | None = None


class TaskCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    assignee_id: int | None = None
    due_at: str | None = None
    note: str | None = None


class TaskUpdateIn(BaseModel):
    status: Literal["待接收", "核查中", "已完成", "已退回"]
    note: str | None = None


class ReviewIn(BaseModel):
    decision: Literal["通过", "退回", "补充核查"]
    comment: str | None = None


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    role: Literal["admin", "commander", "officer", "reviewer"]
    password: str = Field(min_length=8, max_length=256)


def current_user(request: Request) -> dict:
    token = request.cookies.get(store.COOKIE_NAME)
    user = store.session_user(token)
    if not user:
        raise HTTPException(401, "请先登录案件工作台", headers={"WWW-Authenticate": "Cookie"})
    return user


def optional_user(request: Request) -> dict | None:
    return store.session_user(request.cookies.get(store.COOKIE_NAME))


def _cookie_secure(request: Request) -> bool:
    return request.url.scheme == "https" or os.getenv("POLICE_COOKIE_SECURE", "0") == "1"


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        store.COOKIE_NAME,
        token,
        max_age=store.SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
    )


def _fail(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    raise exc


def _require_role(user: dict, *roles: str) -> None:
    if user["role"] not in roles:
        raise HTTPException(403, "当前角色无权执行该操作")


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    user = store.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    token, expires = store.create_session(user["id"], request.headers.get("user-agent"))
    _set_session_cookie(response, request, token)
    return {"ok": True, "user": user, "expires_at": expires}


@router.get("/auth/me")
def me(request: Request):
    user = optional_user(request)
    return {"authenticated": bool(user), "user": user}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    user = optional_user(request)
    store.delete_session(request.cookies.get(store.COOKIE_NAME))
    response.delete_cookie(store.COOKIE_NAME, path="/")
    if user:
        with store.write_conn() as conn:
            store.audit(conn, user, "logout", "user", str(user["id"]))
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(body: PasswordIn, user: dict = Depends(current_user)):
    try:
        ok = store.change_password(user["id"], body.old_password, body.new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not ok:
        raise HTTPException(400, "原密码错误")
    with store.write_conn() as conn:
        store.audit(conn, user, "change_password", "user", str(user["id"]))
    return {"ok": True}


@router.get("/users")
def users(role: str | None = None, user: dict = Depends(current_user)):
    _require_role(user, "admin", "commander", "reviewer")
    return store.list_users(role)


@router.post("/users")
def create_user(body: UserCreateIn, user: dict = Depends(current_user)):
    _require_role(user, "admin")
    try:
        created = store.create_user(body.username, body.display_name, body.role, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with store.write_conn() as conn:
        store.audit(conn, user, "create_user", "user", str(created["id"]),
                    {"username": created["username"], "role": created["role"]})
    return created


@router.get("/cases/stats")
def stats(user: dict = Depends(current_user)):
    return store.case_stats(user)


@router.get("/cases")
def cases(
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    user: dict = Depends(current_user),
):
    return store.list_cases(user, status=status, limit=limit, offset=offset)


@router.post("/cases")
def create_case(body: CaseCreateIn, user: dict = Depends(current_user)):
    source_id = body.source_id or body.hotspot_id
    if not source_id:
        raise HTTPException(400, "必须提供 source_id 或 hotspot_id")
    try:
        return store.create_case(
            user,
            body.source_type,
            source_id,
            body.title,
            body.summary,
            body.assignee_id,
            body.due_at,
        )
    except Exception as exc:
        _fail(exc)


@router.get("/cases/{case_id}")
def case_detail(case_id: str, user: dict = Depends(current_user)):
    try:
        return store.get_case(case_id, user)
    except Exception as exc:
        _fail(exc)


@router.get("/cases/{case_id}/arrival-circle")
def case_arrival_circle(
    case_id: str,
    minutes: int = Query(30, ge=1, le=180),
    refresh: bool = False,
    user: dict = Depends(current_user),
):
    try:
        case = store.get_case(case_id, user)
        if case.get("lon") is None or case.get("lat") is None:
            raise ValueError("案件缺少坐标，无法计算到达圈")
        return arrival.arrival_circle(float(case["lon"]), float(case["lat"]), minutes, refresh)
    except Exception as exc:
        _fail(exc)


@router.post("/cases/{case_id}/drone-plan")
def case_drone_plan(case_id: str, body: DroneOptions, user: dict = Depends(current_user)):
    try:
        case = store.get_case(case_id, user)
        if case.get("lon") is None or case.get("lat") is None:
            raise ValueError("案件缺少坐标，无法规划航线")
        title = body.title or f"{case.get('case_no') or case_id} 无人机核查航线"
        return drone.plan_drone_route(
            float(case["lon"]),
            float(case["lat"]),
            pattern=body.pattern,
            size_km=body.size_km,
            spacing_m=body.spacing_m,
            rel_height_m=body.rel_height_m,
            speed_mps=body.speed_mps,
            direction_deg=body.direction_deg,
            sample_m=body.sample_m,
            title=title,
        )
    except Exception as exc:
        _fail(exc)


@router.get("/cases/{case_id}/legal-checklist")
def case_legal_checklist(case_id: str, user: dict = Depends(current_user)):
    try:
        case = store.get_case(case_id, user)
        return legal.case_checklist(case)
    except Exception as exc:
        _fail(exc)


@router.post("/cases/{case_id}/tasks")
def create_task(case_id: str, body: TaskCreateIn, user: dict = Depends(current_user)):
    try:
        task = store.create_task(user, case_id, body.title, body.assignee_id, body.due_at, body.note)
        return task
    except Exception as exc:
        _fail(exc)


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdateIn, user: dict = Depends(current_user)):
    try:
        return store.update_task(user, task_id, body.status, body.note)
    except Exception as exc:
        _fail(exc)


def _safe_name(filename: str) -> str:
    name = Path(filename or "evidence.bin").name
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .")
    return (name or "evidence.bin")[:120]


def _mime_for(filename: str, content_type: str | None) -> str:
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct in ALLOWED_MIME:
        return ct
    guessed = mimetypes.guess_type(filename)[0] or ""
    if guessed in ALLOWED_MIME:
        return guessed
    ext = Path(filename).suffix.lower()
    if ext in ALLOWED_EXT:
        return guessed or "application/octet-stream"
    raise ValueError("只允许 JPG/PNG/WEBP/PDF/TXT/MP4/MOV 证据文件")


@router.post("/cases/{case_id}/evidence")
async def upload_evidence(
    case_id: str,
    file: UploadFile = File(...),
    task_id: str | None = Form(None),
    captured_at: str | None = Form(None),
    lon: float | None = Form(None),
    lat: float | None = Form(None),
    note: str | None = Form(None),
    user: dict = Depends(current_user),
):
    filename = _safe_name(file.filename or "evidence.bin")
    try:
        mime = _mime_for(filename, file.content_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if lon is not None and not -180 <= lon <= 180:
        raise HTTPException(400, "经度超出范围")
    if lat is not None and not -90 <= lat <= 90:
        raise HTTPException(400, "纬度超出范围")

    EVIDENCE_DIR = store.EVIDENCE_DIR
    case_dir = EVIDENCE_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    tmp = case_dir / f".upload-{uuid.uuid4().hex}.part"
    sha = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_EVIDENCE_BYTES:
                    raise HTTPException(413, f"文件超过 {MAX_EVIDENCE_BYTES // 1024 // 1024} MB 限制")
                sha.update(chunk)
                out.write(chunk)
        digest = sha.hexdigest()
        final = case_dir / f"{digest[:16]}_{filename}"
        if final.exists():
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(final)
        rel = final.relative_to(store.config.ROOT).as_posix()
        result = store.add_evidence(
            user, case_id, task_id, filename, rel, digest, size, mime,
            captured_at, lon, lat, note,
        )
        return result
    except HTTPException:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        _fail(exc)


@router.get("/evidence/{evidence_id}/download")
def download_evidence(evidence_id: str, user: dict = Depends(current_user)):
    try:
        ev, path = store.evidence_for_download(evidence_id, user)
    except Exception as exc:
        _fail(exc)
    return FileResponse(
        path,
        media_type=ev.get("mime") or "application/octet-stream",
        filename=ev["filename"],
        headers={"Cache-Control": "no-store"},
    )


@router.post("/cases/{case_id}/review")
def review(case_id: str, body: ReviewIn, user: dict = Depends(current_user)):
    try:
        return store.add_review(user, case_id, body.decision, body.comment)
    except Exception as exc:
        _fail(exc)


@router.post("/cases/{case_id}/archive")
def archive(case_id: str, user: dict = Depends(current_user)):
    try:
        return store.archive_case(user, case_id)
    except Exception as exc:
        _fail(exc)


@router.post("/cases/{case_id}/arrival-tokens")
def create_arrival_token(
    case_id: str,
    body: ArrivalTokenIn,
    request: Request,
    response: Response,
    user: dict = Depends(current_user),
):
    try:
        result = store.issue_arrival_token(user, case_id, body.hours, body.max_uses, body.note)
        base = os.getenv("POLICE_PUBLIC_BASE_URL", "").strip().rstrip("/") or str(request.base_url).rstrip("/")
        result["mobile_url"] = f"{base}/m#t={quote(result['token'], safe='')}"
        qr_png = make_qr_png(result["mobile_url"])
        result["qr_data_url"] = "data:image/png;base64," + base64.b64encode(qr_png).decode("ascii")
        response.headers["Cache-Control"] = "no-store"
        return result
    except Exception as exc:
        _fail(exc)


@router.get("/cases/{case_id}/arrival-tokens")
def arrival_tokens(case_id: str, user: dict = Depends(current_user)):
    try:
        return {"items": store.list_arrival_tokens(user, case_id)}
    except Exception as exc:
        _fail(exc)


@router.post("/cases/{case_id}/arrival-tokens/{token_id}/revoke")
def revoke_arrival_token(case_id: str, token_id: int, user: dict = Depends(current_user)):
    try:
        return store.revoke_arrival_token(user, case_id, token_id)
    except Exception as exc:
        _fail(exc)


@router.post("/cases/{case_id}/verification")
def record_verification(
    case_id: str, body: VerificationIn, user: dict = Depends(current_user)
):
    try:
        return store.record_verification(user, case_id, body.verdict, body.note, body.evidence_id)
    except Exception as exc:
        _fail(exc)


@router.get("/verifications")
def verifications(limit: int = 500, user: dict = Depends(current_user)):
    _require_role(user, "admin", "commander", "reviewer")
    return {"items": store.list_verifications(user, limit=limit)}


@router.get("/verifications/stats")
def verification_stats(user: dict = Depends(current_user)):
    return store.verification_stats(user)


@router.get("/cases/{case_id}/evidence-chain")
def evidence_chain(case_id: str, deep: bool = False, user: dict = Depends(current_user)):
    try:
        return store.evidence_chain_report(case_id, user, deep=deep)
    except Exception as exc:
        _fail(exc)


@router.get("/audit")
def audit(
    limit: int = 100,
    object_type: str | None = None,
    object_id: str | None = None,
    user: dict = Depends(current_user),
):
    _require_role(user, "admin", "commander", "reviewer")
    return store.get_audit(user, limit=limit, object_type=object_type, object_id=object_id)


# ---------------------------------------------------------------------------
# 报告与二维码
# ---------------------------------------------------------------------------
_FONT_READY = False


def _ensure_pdf_font() -> None:
    global _FONT_READY
    if _FONT_READY:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    pdfmetrics.registerFontFamily(
        "STSong-Light", normal="STSong-Light", bold="STSong-Light",
        italic="STSong-Light", boldItalic="STSong-Light",
    )
    _FONT_READY = True


def _xml(text) -> str:
    return (
        str(text if text is not None else "—")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _spaced_hash(value: str) -> str:
    return " ".join(value[i:i + 8] for i in range(0, len(value), 8))


def make_qr_png(url: str) -> bytes:
    import qrcode

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=9, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0b3b36", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def case_workspace_url(request: Request, case_id: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/workspace?case={quote(case_id)}"


@router.get("/cases/{case_id}/qrcode.png")
def case_qrcode(case_id: str, request: Request, user: dict = Depends(current_user)):
    try:
        store.get_case(case_id, user)
    except Exception as exc:
        _fail(exc)
    return Response(
        content=make_qr_png(case_workspace_url(request, case_id)),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


def _offline_checklist(bundle: dict) -> bytes:
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["类型", "编号", "状态/结论", "标题或文件名", "责任人", "时间", "说明"])
    for task in bundle.get("tasks") or []:
        writer.writerow([
            "任务", task.get("id"), task.get("status"), task.get("title"),
            task.get("assignee_name") or "—", task.get("due_at") or task.get("created_at") or "—",
            (task.get("note") or "").replace("\n", " "),
        ])
    for ev in bundle.get("evidence") or []:
        writer.writerow([
            "证据", ev.get("id"), "已上传", ev.get("filename"),
            ev.get("uploaded_by") or "—", ev.get("captured_at") or ev.get("created_at") or "—",
            f"SHA-256={ev.get('sha256')}；{str(ev.get('note') or '').replace(chr(10), ' ')}",
        ])
    for review in bundle.get("reviews") or []:
        writer.writerow([
            "复核", review.get("id"), review.get("decision"), "复核意见",
            review.get("reviewer_name") or "—", review.get("created_at") or "—",
            (review.get("comment") or "").replace("\n", " "),
        ])
    return buf.getvalue().encode("utf-8-sig")


def _offline_readme(bundle: dict) -> bytes:
    text = f"""警务遥感 · 生态警务离线核查包
================================

案件号：{bundle.get('case_no', '—')}
案件标题：{bundle.get('title', '—')}
案件状态：{bundle.get('status', '—')}
来源：{bundle.get('source', '—')}
生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}

包含内容
--------
1. case.json        案件快照：双分数、AHP 因子、任务、证据元数据、复核与审计。
2. checklist.csv    现场核查清单：任务、证据哈希、复核意见，可用 Excel 打开。
3. manifest.sha256  上述文件的 SHA-256，便于核对包内文件是否被改动。

边界说明（请务必阅读）
----------------------
1. 本包不包含卫星底图、瓦片、DEM 或离线地图，不能替代联网影像判读。
2. 证据原件不打包，只记录文件名和 SHA-256；原件仍在案件库，回传后才能作为证据使用。
3. 本包由原型系统导出，不是法定取证文书，也不替代现场笔录、照片、视频和审批手续。
4. 案件分数是核查排序/风险沟通工具，不是灾害发生概率，也不是官方预警。
"""
    return text.encode("utf-8")


@router.get("/cases/{case_id}/offline-pack.zip")
def case_offline_pack(case_id: str, user: dict = Depends(current_user)):
    """导出案件快照与现场核查清单；不打包底图、瓦片和证据原件。"""
    try:
        bundle = store.get_case(case_id, user)
    except Exception as exc:
        _fail(exc)

    case_json = json.dumps(
        {
            "schema": "police-remote-sensing/offline-case-v1",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "boundary": [
                "不含卫星底图/瓦片/DEM，不能替代联网影像判读",
                "证据仅列文件名与 SHA-256，原件未打包",
                "原型系统导出，不是法定取证文书",
                "分数不是灾害概率，也不是官方预警",
            ],
            "case": bundle,
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    checklist = _offline_checklist(bundle)
    readme = _offline_readme(bundle)
    manifest = "\n".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}"
        for name, data in (
            ("case.json", case_json),
            ("checklist.csv", checklist),
            ("README.txt", readme),
        )
    ).encode("utf-8") + b"\n"

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("case.json", case_json)
        zf.writestr("checklist.csv", checklist)
        zf.writestr("README.txt", readme)
        zf.writestr("manifest.sha256", manifest)
    content = buf.getvalue()

    store.record_audit(
        user, "export_offline_pack", "case", case_id,
        {"case_no": bundle.get("case_no"), "bytes": len(content)},
    )
    ascii_name = f"{bundle.get('case_no', 'case')}_offline.zip"
    utf8_name = quote(f"{bundle.get('case_no', 'case')}_离线核查包.zip")
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}",
            "Cache-Control": "no-store",
        },
    )


def _build_report_pdf(bundle: dict, qr_png: bytes) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    _ensure_pdf_font()
    teal = colors.HexColor("#0b3b36")
    light = colors.HexColor("#e6f4f1")
    grey = colors.HexColor("#5b6b68")
    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        "cn", parent=styles["Normal"], fontName="STSong-Light", fontSize=10,
        leading=15, textColor=colors.HexColor("#1f2d2a"), spaceAfter=4,
        wordWrap="CJK",
    )
    small = ParagraphStyle("smallcn", parent=base, fontSize=8, leading=11,
                           textColor=grey)
    cell = ParagraphStyle("cellcn", parent=base, fontSize=8, leading=11)
    tiny = ParagraphStyle("tinycn", parent=base, fontSize=7, leading=10)
    h1 = ParagraphStyle("h1cn", parent=base, fontSize=16, leading=22,
                        textColor=teal, spaceBefore=6, spaceAfter=8)
    h2 = ParagraphStyle("h2cn", parent=base, fontSize=12, leading=17,
                        textColor=teal, spaceBefore=10, spaceAfter=4)
    title = ParagraphStyle("titlecn", parent=base, fontSize=20, leading=28,
                           alignment=TA_CENTER, textColor=teal, spaceAfter=6)
    center = ParagraphStyle("centercn", parent=base, fontSize=9, alignment=TA_CENTER)

    snap = bundle.get("data_snapshot") or {}
    props = snap.get("properties") or {}
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = bundle.get("status") or "—"
    final_report = status in ("已复核", "已归档")
    fingerprint_src = json.dumps(
        {
            "case": {k: bundle.get(k) for k in (
                "id", "case_no", "status", "priority_score", "current_danger",
                "updated_at", "data_snapshot",
            )},
            "evidence": [e.get("sha256") for e in bundle.get("evidence", [])],
            "generated": generated,
        },
        ensure_ascii=False, sort_keys=True,
    )
    fingerprint = hashlib.sha256(fingerprint_src.encode("utf-8")).hexdigest()

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=1.7 * cm, bottomMargin=1.7 * cm,
        title=f"{bundle.get('case_no', '')} 生态警务线索核查报告",
        author="警务遥感原型系统",
    )
    story = []
    story.append(Paragraph("生态警务线索核查报告", title))
    story.append(Paragraph(
        f"{'已复核案件报告' if final_report else '过程稿'} · 案件编号 { _xml(bundle.get('case_no')) }",
        center,
    ))
    story.append(Spacer(1, 0.25 * cm))
    story.append(Paragraph(
        "本报告由“警务遥感”原型根据公开遥感数据、公开气象数据与规则模型生成。"
        "它不是官方灾害预警、不是司法鉴定意见，也不能替代现场勘验、法定取证和人工审核。",
        small,
    ))
    story.append(Spacer(1, 0.2 * cm))

    header = Table(
        [[
            Paragraph(f"<b>案件状态</b><br/>{_xml(status)}", cell),
            Paragraph(f"<b>线索编号</b><br/>{_xml(bundle.get('source_id'))}", cell),
            Paragraph(f"<b>承办人</b><br/>{_xml(bundle.get('assignee_name') or '待指派')}", cell),
        ], [
            Paragraph(f"<b>核查优先级</b><br/>{_xml(bundle.get('priority_label'))} · {float(bundle.get('priority_score') or 0):.1f}", cell),
            Paragraph(f"<b>当前危险指数</b><br/>{_xml(bundle.get('current_danger_label'))} · {float(bundle.get('current_danger') or 0):.1f}", cell),
            Paragraph(f"<b>生成时间</b><br/>{_xml(generated[:19].replace('T', ' '))} UTC", cell),
        ]],
        colWidths=[5.8 * cm, 5.8 * cm, 5.8 * cm],
    )
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), light),
        ("BOX", (0, 0), (-1, -1), 0.5, teal),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b9d9d2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(header)

    story.append(Paragraph("一、线索来源与双分数口径", h2))
    source_rows = [
        [Paragraph("项目", cell), Paragraph("内容", cell)],
        [Paragraph("线索名称", cell), Paragraph(_xml(bundle.get("hotspot_name")), cell)],
        [Paragraph("数据来源", cell), Paragraph(_xml(bundle.get("source")), cell)],
        [Paragraph("空间位置", cell), Paragraph(
            f"{float(bundle.get('lon') or 0):.5f}°E, {float(bundle.get('lat') or 0):.5f}°N", cell)],
        [Paragraph("核查优先级", cell), Paragraph(
            f"{float(bundle.get('priority_score') or 0):.2f} / 100（历史异常 AHP 加权；"
            "只回答先核查哪一处，不是灾害概率）", cell)],
        [Paragraph("当前危险指数", cell), Paragraph(
            f"{float(bundle.get('current_danger') or 0):.2f} / 100（当前/未来自然致灾因子 × 人口暴露；"
            "历史异常不参与该分数）", cell)],
    ]
    t = Table(source_rows, colWidths=[3.2 * cm, 14.4 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), teal),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b9d9d2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)

    story.append(Paragraph("二、历史核查优先级因子贡献", h2))
    factors = props.get("factors") or {}
    weights = props.get("priority_weights") or props.get("weights") or {}
    if factors and weights:
        rows = [[Paragraph("因子", cell), Paragraph("归一化值", cell),
                 Paragraph("权重", cell), Paragraph("加权贡献", cell)]]
        total = 0.0
        for key, value in factors.items():
            w = float(weights.get(key, 0) or 0)
            contribution = float(value or 0) * w * 100
            if key in weights:
                total += contribution
            rows.append([
                Paragraph(_xml(key), cell),
                Paragraph(f"{float(value or 0):.3f}", cell),
                Paragraph(f"{w:.4f}", cell),
                Paragraph(f"{contribution:.2f}", cell),
            ])
        rows.append([Paragraph("合计", cell), Paragraph("—", cell),
                     Paragraph("—", cell), Paragraph(f"{total:.2f}", cell)])
        t = Table(rows, colWidths=[5.2 * cm, 3.8 * cm, 3.8 * cm, 4.8 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), teal),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b9d9d2")),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("BACKGROUND", (0, -1), (-1, -1), light),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
        story.append(Paragraph(
            "注：网页核查排序会剔除“当前环境”因子后重新归一化；本表展示案件快照中的原始 AHP 权重。"
            "AHP 一致性比例 CR=0.0026（MATLAB R2025b 计算）。", small))
    else:
        comps = props.get("current_components") or {}
        if comps:
            rows = [[Paragraph("当前危险分量", cell), Paragraph("分值", cell)]]
            for k, v in sorted(comps.items(), key=lambda kv: -float(kv[1] or 0)):
                rows.append([Paragraph(_xml(k), cell), Paragraph(f"{float(v or 0):.2f}", cell)])
            t = Table(rows, colWidths=[10 * cm, 4 * cm])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), teal),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b9d9d2")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(t)
        else:
            story.append(Paragraph("该数据源没有可展示的因子明细。", small))

    story.append(Paragraph("三、现场核查任务", h2))
    tasks = bundle.get("tasks", [])
    if tasks:
        rows = [[Paragraph("任务", cell), Paragraph("承办人", cell),
                 Paragraph("状态", cell), Paragraph("期限", cell)]]
        for task in tasks:
            rows.append([
                Paragraph(f"{_xml(task.get('id'))}<br/>{_xml(task.get('title'))}", cell),
                Paragraph(_xml(task.get("assignee_name") or "待指派"), cell),
                Paragraph(_xml(task.get("status")), cell),
                Paragraph(_xml((task.get("due_at") or "—")[:19].replace("T", " ")), cell),
            ])
        t = Table(rows, colWidths=[7.2 * cm, 4.0 * cm, 3.2 * cm, 3.2 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), teal),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b9d9d2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("尚未创建现场核查任务。", small))

    story.append(Paragraph("四、证据清单与完整性校验", h2))
    evidence = bundle.get("evidence", [])
    if evidence:
        rows = [[Paragraph("文件", cell), Paragraph("SHA-256（分组显示）", cell),
                 Paragraph("大小", cell), Paragraph("拍摄/上传", cell)]]
        for ev in evidence:
            rows.append([
                Paragraph(_xml(ev.get("filename")), cell),
                Paragraph(_spaced_hash(ev.get("sha256") or ""), tiny),
                Paragraph(f"{int(ev.get('size') or 0) / 1024:.1f} KB", cell),
                Paragraph(_xml((ev.get("captured_at") or ev.get("created_at") or "")[:19].replace("T", " ")), cell),
            ])
        t = Table(rows, colWidths=[4.4 * cm, 8.4 * cm, 2.2 * cm, 2.6 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), teal),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b9d9d2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
        story.append(Paragraph(
            "哈希校验只能证明“当前文件与上传时一致”，不能证明拍摄时间、地点或内容真实；"
            "要形成法定证据，仍需原始载体、提取笔录、见证人/双人执法和法定检验鉴定。", small))
    else:
        story.append(Paragraph("尚未上传证据。按系统规则，没有证据不能把任务标记为“已完成”。", small))

    story.append(Paragraph("五、复核意见", h2))
    reviews = bundle.get("reviews", [])
    if reviews:
        rows = [[Paragraph("时间", cell), Paragraph("复核人", cell),
                 Paragraph("结论", cell), Paragraph("意见", cell)]]
        for r in reviews:
            rows.append([
                Paragraph(_xml((r.get("created_at") or "")[:19].replace("T", " ")), cell),
                Paragraph(_xml(r.get("reviewer_name") or ""), cell),
                Paragraph(_xml(r.get("decision")), cell),
                Paragraph(_xml(r.get("comment") or ""), cell),
            ])
        t = Table(rows, colWidths=[4.0 * cm, 3.0 * cm, 2.4 * cm, 7.6 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), teal),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b9d9d2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("尚未提交复核。", small))

    story.append(Paragraph("六、审计轨迹（最近 30 条）", h2))
    audits = bundle.get("audit", [])[-30:]
    if audits:
        rows = [[Paragraph("时间", cell), Paragraph("操作人", cell),
                 Paragraph("动作", cell), Paragraph("对象/说明", cell)]]
        for a in audits:
            rows.append([
                Paragraph(_xml((a.get("at") or "")[:19].replace("T", " ")), cell),
                Paragraph(_xml(a.get("actor_name") or ""), cell),
                Paragraph(_xml(a.get("action")), cell),
                Paragraph(_xml((a.get("detail") or "")[:160]), tiny),
            ])
        t = Table(rows, colWidths=[3.8 * cm, 2.8 * cm, 4.2 * cm, 7.2 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), teal),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b9d9d2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("暂无审计记录。", small))

    story.append(Paragraph("七、边界与免责声明", h2))
    story.append(Paragraph(
        "1. 本报告是软件原型输出，不是公安机关正式文书，不构成立案、处罚、灾害预警或司法鉴定意见。"
        "2. 核查优先级来自 53 个历史异常图斑的 AHP 排序，不代表灾害发生概率；当前危险指数来自公开气象、"
        "NDVI、地形和人口暴露，是情景估计，不是校准概率。"
        "3. 公开数据可能存在云量、重访周期、分辨率、行政边界和人口统计误差；现场核查结论优先于模型输出。"
        "4. 报告中的 SHA-256 仅用于文件完整性比对，不替代法定电子签章、时间戳或证据保管链。"
        "5. 真实警务部署必须完成等保、权限审计、数据脱敏、内网/专网部署和法制审核；当前系统默认演示账号仅用于功能验证。",
        base,
    ))

    story.append(Spacer(1, 0.25 * cm))
    qr_img = Image(BytesIO(qr_png), width=3.0 * cm, height=3.0 * cm)
    foot = Table(
        [[Paragraph(
            f"报告内容指纹（SHA-256）：<br/>{_spaced_hash(fingerprint)}<br/>"
            "该指纹用于比对报告是否被改动，不是电子签章。", tiny), qr_img]],
        colWidths=[13.6 * cm, 3.8 * cm],
    )
    foot.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#b9d9d2")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(foot)

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("STSong-Light", 8)
        canvas.setFillColor(grey)
        canvas.drawString(1.8 * cm, 1.0 * cm, "警务遥感 · 生态警务线索核查报告（原型系统生成，非正式文书）")
        canvas.drawRightString(A4[0] - 1.8 * cm, 1.0 * cm, f"第 {doc_.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


@router.get("/cases/{case_id}/report.pdf")
def case_report(
    case_id: str,
    request: Request,
    final: bool = False,
    user: dict = Depends(current_user),
):
    try:
        bundle = store.get_case(case_id, user)
    except Exception as exc:
        _fail(exc)
    if final and bundle.get("status") not in ("已复核", "已归档"):
        raise HTTPException(409, "只有已复核或已归档案件可以导出正式报告")
    try:
        pdf = _build_report_pdf(bundle, make_qr_png(case_workspace_url(request, case_id)))
    except Exception as exc:
        raise HTTPException(500, f"报告生成失败：{exc}") from exc
    ascii_name = f"{bundle.get('case_no', 'case')}_report.pdf"
    utf8_name = quote(f"{bundle.get('case_no', 'case')}_生态警务核查报告.pdf")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}",
            "Cache-Control": "no-store",
        },
    )

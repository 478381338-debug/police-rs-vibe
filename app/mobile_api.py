"""现场核查移动端 API。

安全边界：
1. 到场码只存在 URL 片段（#t=），由前端放进 X-Arrival-Token 请求头，避免进入访问日志；
2. 服务端只保存令牌 SHA-256，令牌过期/作废/次数用尽即拒；
3. 移动端不能改案件分数、不能复核、不能归档，只能签到和上传证据；
4. 上传文件沿用工作台的大小、MIME 与扩展名白名单，并逐块计算 SHA-256。
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app import case_store as store
from app import config
from app.case_api import ALLOWED_EXT, ALLOWED_MIME, MAX_EVIDENCE_BYTES, _mime_for, _safe_name

router = APIRouter(prefix="/api/mobile", tags=["mobile-checkin"])


def _token(value: str | None) -> str:
    token = (value or "").strip()
    if not token:
        raise HTTPException(401, "缺少到场码")
    return token


def _fail(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(404, str(exc))
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc))
    raise exc


def _no_store(payload: dict) -> JSONResponse:
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


async def _save_upload(
    upload: UploadFile,
    actor: dict,
    case_id: str,
    task_id: str | None,
    captured_at: str | None,
    lon: float | None,
    lat: float | None,
    note: str | None,
) -> dict:
    filename = _safe_name(upload.filename or "evidence.jpg")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"不支持的文件类型：{ext or '未知'}")
    mime = _mime_for(filename, upload.content_type)
    if mime not in ALLOWED_MIME:
        raise ValueError(f"不支持的文件 MIME：{mime}")
    store.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = store.EVIDENCE_DIR / f".mobile-{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as fh:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_EVIDENCE_BYTES:
                    raise ValueError(f"文件超过 {MAX_EVIDENCE_BYTES // (1024 * 1024)}MB 限制")
                digest.update(chunk)
                fh.write(chunk)
        if size == 0:
            raise ValueError("空文件不能作为证据")
        sha256 = digest.hexdigest()
        final = store.EVIDENCE_DIR / f"{sha256[:16]}_{filename}"
        if final.exists():
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(final)
        stored_path = str(final.relative_to(config.ROOT)) if config.ROOT in final.parents else str(final)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return store.add_evidence(
        actor, case_id, task_id or None, filename, stored_path, sha256, size, mime,
        captured_at, lon, lat, note,
    )


@router.get("/context")
def context(x_arrival_token: str | None = Header(default=None, alias="X-Arrival-Token")):
    try:
        return _no_store(store.arrival_token_context(_token(x_arrival_token)))
    except Exception as exc:
        _fail(exc)


@router.post("/checkin")
async def checkin(
    actor_name: str = Form(...),
    lon: float | None = Form(default=None),
    lat: float | None = Form(default=None),
    accuracy: float | None = Form(default=None),
    task_id: str | None = Form(default=None),
    note: str | None = Form(default=None),
    device: str | None = Form(default=None),
    captured_at: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    x_arrival_token: str | None = Header(default=None, alias="X-Arrival-Token"),
):
    token = _token(x_arrival_token)
    try:
        result = store.redeem_arrival_token(
            token, actor_name, lon, lat, accuracy, task_id, note, device, captured_at
        )
    except Exception as exc:
        _fail(exc)
    actor = store.user_by_id(int(result["actor_id"]))
    if not actor:
        raise HTTPException(403, "到场码创建人已不存在，无法上传证据")
    uploaded: list[dict] = []
    errors: list[str] = []
    for upload in files:
        try:
            ev = await _save_upload(
                upload, actor, result["case_id"], task_id, captured_at, lon, lat,
                f"到场码上传 · {actor_name}",
            )
            store.link_checkin_evidence(result["checkin_id"], result["case_id"], ev["id"])
            uploaded.append(ev)
        except Exception as exc:
            errors.append(f"{upload.filename}: {exc}")
    payload = dict(result)
    payload["evidence"] = uploaded
    payload["errors"] = errors
    return _no_store(payload)


@router.post("/evidence")
async def mobile_evidence(
    checkin_id: str = Form(...),
    file: UploadFile = File(...),
    task_id: str | None = Form(default=None),
    captured_at: str | None = Form(default=None),
    lon: float | None = Form(default=None),
    lat: float | None = Form(default=None),
    note: str | None = Form(default=None),
    x_arrival_token: str | None = Header(default=None, alias="X-Arrival-Token"),
):
    token = _token(x_arrival_token)
    try:
        ctx = store.arrival_token_context(token)
        case_id = ctx["case"]["id"]
        existed = store.get_checkin(checkin_id, case_id)
        if not existed:
            raise LookupError("签到记录不存在或不属于该案件")
        if not ctx.get("can_checkin"):
            raise ValueError("案件已复核或归档，不能再上传证据")
        actor = store.user_by_id(int(ctx["actor_id"]))
        if not actor:
            raise PermissionError("到场码创建人已不存在，无法上传证据")
        ev = await _save_upload(
            file, actor, case_id, task_id or existed.get("task_id"), captured_at, lon, lat,
            note or f"到场码补充上传 · {existed.get('actor_name') or ''}".strip(),
        )
        store.link_checkin_evidence(checkin_id, case_id, ev["id"])
        return _no_store({"evidence": ev, "checkin_id": checkin_id, "case_id": case_id})
    except Exception as exc:
        _fail(exc)

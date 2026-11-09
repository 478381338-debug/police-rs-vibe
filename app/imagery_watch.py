"""Sentinel-2 新影像到访监听。

只做一件事：查询 Microsoft Planetary Computer STAC 上研究区最新的
Sentinel-2 L2A 场景，并与上次检查/本地缓存中的 scene id 去重。

它不判断“有没有异常”，也不生成灾情结论；新场景是否形成线索必须由
人工打开影像比对。这样既复用真实的公开 STAC，又不把“新影像”伪装成
“新警情”。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from app import config

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"
STATE_PATH = config.ROOT / "instance" / "imagery_watch.json"
CACHE_PATH = config.DATA / "mpc_scenes.json"
MAX_SEEN = 1500
DISCLAIMER = (
    "这里只报告“有新卫星影像到访”，不表示影像里一定存在异常，也不是火情/灾情预警。"
    "是否形成线索，必须由民警打开影像、对照历史底图并现场核查。"
)
_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _read_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _seed_ids_from_cache() -> tuple[set[str], str, datetime | None]:
    """把上一次 MPC 抓取结果当作基线，避免首次检查把旧场景全部报成“新”。"""
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        ids = {
            str(item.get("id"))
            for item in (cache.get("sentinel2") or [])
            if item.get("id")
        }
        if ids:
            return ids, "data/mpc_scenes.json", _parse_dt(cache.get("fetched_at"))
    except Exception:
        pass
    return set(), "", None


def _search(days: int, cloud_max: float) -> list[dict]:
    end = _now()
    start = end - timedelta(days=days)
    body = {
        "collections": [COLLECTION],
        "bbox": list(config.AOI_BBOX),
        "datetime": f"{_iso(start)}/{_iso(end)}",
        "limit": 60,
        "sortby": [{"field": "datetime", "direction": "desc"}],
    }
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = requests.post(
                f"{STAC_URL}/search",
                json=body,
                timeout=30,
                headers={"User-Agent": "eco-police-imagery-watch/1.0"},
            )
            if response.status_code == 429 or response.status_code >= 500:
                last_error = RuntimeError(f"STAC HTTP {response.status_code}")
                if attempt == 0:
                    time.sleep(2)
                    continue
            response.raise_for_status()
            features = (response.json() or {}).get("features") or []
            items: list[dict] = []
            for feature in features:
                props = feature.get("properties") or {}
                cloud = props.get("eo:cloud_cover")
                if cloud is not None:
                    try:
                        if float(cloud) > cloud_max:
                            continue
                    except (TypeError, ValueError):
                        pass
                assets = feature.get("assets") or {}
                preview = (assets.get("rendered_preview") or {}).get("href")
                tilejson = (assets.get("tilejson") or {}).get("href")
                item_id = str(feature.get("id") or "")
                if not item_id:
                    continue
                items.append(
                    {
                        "id": item_id,
                        "collection": feature.get("collection") or COLLECTION,
                        "datetime": props.get("datetime"),
                        "cloud_cover": cloud,
                        "platform": props.get("platform"),
                        "tile": props.get("s2:mgrs_tile"),
                        "bbox": feature.get("bbox"),
                        "preview": preview,
                        "tilejson": tilejson,
                        "item_url": f"{STAC_URL}/collections/{COLLECTION}/items/{item_id}",
                    }
                )
            items.sort(
                key=lambda item: _parse_dt(item.get("datetime"))
                or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return items
        except Exception as exc:  # noqa: BLE001 - 对外统一抛真实错误
            last_error = exc
            if attempt == 0:
                time.sleep(2)
    raise RuntimeError(f"STAC 查询失败：{last_error}") from last_error


def _public(state: dict, *, checked: bool = True) -> dict:
    return {
        "ok": True,
        "checked": checked and bool(state),
        "collection": COLLECTION,
        "aoi_name": config.AOI_NAME,
        "aoi_bbox": list(config.AOI_BBOX),
        "window_days": state.get("window_days", 14),
        "cloud_max": state.get("cloud_max", 60),
        "last_checked_at": state.get("last_checked_at"),
        "new_count": int(state.get("new_count") or 0),
        "new_scenes": state.get("new_scenes") or [],
        "latest": state.get("latest") or [],
        "baseline_source": state.get("baseline_source"),
        "baseline_at": state.get("baseline_at"),
        "last_error": state.get("last_error"),
        "disclaimer": DISCLAIMER,
    }


def status() -> dict:
    state = _read_state()
    if not state:
        return _public({}, checked=False)
    return _public(state)


def refresh(days: int = 14, cloud_max: float = 60.0) -> dict:
    days = max(1, min(int(days), 30))
    cloud_max = max(0.0, min(float(cloud_max), 100.0))
    with _LOCK:
        state = _read_state()
        seen = {str(item) for item in (state.get("seen_ids") or []) if item}
        baseline_source = state.get("baseline_source") or ""
        baseline_at = _parse_dt(state.get("last_checked_at")) or _parse_dt(state.get("baseline_at"))
        if not seen:
            seen, baseline_source, cache_at = _seed_ids_from_cache()
            baseline_at = baseline_at or cache_at
        first_baseline = not seen

        items = _search(days, cloud_max)
        if first_baseline:
            new_items: list[dict] = []
            baseline_source = baseline_source or "first-live-query"
        else:
            new_items = []
            for item in items:
                if item["id"] in seen:
                    continue
                scene_at = _parse_dt(item.get("datetime"))
                # 首次用本地缓存做基线时，只把“缓存抓取时间之后”的场景算新增，
                # 避免把 14 天窗口里缓存没收录的旧场景一次性报成几十个“新影像”。
                if baseline_at and (not scene_at or scene_at <= baseline_at):
                    continue
                new_items.append(item)

        ordered_ids: list[str] = []
        for item in items:
            if item["id"] not in ordered_ids:
                ordered_ids.append(item["id"])
        for old_id in state.get("seen_ids") or []:
            if old_id not in ordered_ids:
                ordered_ids.append(str(old_id))
        ordered_ids = ordered_ids[:MAX_SEEN]

        state = {
            "last_checked_at": _iso(_now()),
            "window_days": days,
            "cloud_max": cloud_max,
            "seen_ids": ordered_ids,
            "new_count": len(new_items),
            "new_scenes": new_items[:12],
            "latest": items[:12],
            "baseline_source": baseline_source or "existing-state",
            "baseline_at": _iso(baseline_at) if baseline_at else None,
            "last_error": None,
        }
        _write_state(state)
        return _public(state)

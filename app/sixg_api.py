"""6G 空天地一体公共安全智能体：网络数字孪生 + 边缘变化检测演示。

说明：
- 6G 网络指标由确定性数字孪生模型生成，仅用于产品演示，不代表真实网络实测。
- 变化检测使用真实 Sentinel-2 影像对（2023-10-08 / 2025-09-27，T49TCG），
  算法为多特征融合 + 光照不变抑制 + 形态学后处理，输出为候选图斑，需人工复核。
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFilter
from pydantic import BaseModel

from app import case_store, config, legal

router = APIRouter(prefix="/api/sixg", tags=["sixg"])

DEMO_DIR = Path(config.DATA) / "sixg_demo"
OUT_DIR = DEMO_DIR / "out"

SCENARIOS: dict[str, dict[str, Any]] = {
    "normal": {
        "label": "常态巡检",
        "code": "NORMAL",
        "seed": 11,
        "desc": "地面 6G 基站与边缘节点在线，低轨卫星 NTN 热备份。",
        "slices": {"eMBB": 55, "URLLC": 30, "mMTC": 15},
    },
    "outage": {
        "label": "基站中断",
        "code": "OUTAGE",
        "seed": 23,
        "desc": "地面基站退服，业务在 10 秒内切换至低轨卫星 NTN，影像回传降速但不断链。",
        "slices": {"eMBB": 35, "URLLC": 45, "mMTC": 20},
    },
    "handover": {
        "label": "卫星切换",
        "code": "HANDOVER",
        "seed": 37,
        "desc": "低轨卫星过顶切换，URLLC 切片经历短时抖动，波束切换后恢复。",
        "slices": {"eMBB": 50, "URLLC": 40, "mMTC": 10},
    },
    "congestion": {
        "label": "切片拥塞",
        "code": "CONGEST",
        "seed": 41,
        "desc": "大型活动导致 eMBB 需求激增，网络按优先级重调度切片，保障指挥控制。",
        "slices": {"eMBB": 78, "URLLC": 17, "mMTC": 5},
    },
}

_DETECTION_LOCK = threading.Lock()
_DETECTION_CACHE: dict[str, Any] = {}
_FULL_LOCK = threading.Lock()
_FULL_CACHE: dict[str, Any] | None = None

# 网络感知推理：瓦片网格 2×2，按 6G 可用带宽决定回传几块高变化瓦片。
PROFILES: dict[str, dict[str, Any]] = {
    "full": {"tiles": 4, "label": "全量四瓦片", "model": "CD-Multi-Feature"},
    "balanced": {"tiles": 3, "label": "三瓦片均衡", "model": "CD-Multi-Feature"},
    "roi": {"tiles": 2, "label": "双瓦片 ROI", "model": "CD-Multi-Feature"},
    "narrow": {"tiles": 1, "label": "单瓦片窄带", "model": "CD-Multi-Feature"},
}
PROFILE_BY_SCENARIO = {
    "normal": "full",
    "handover": "balanced",
    "congestion": "roi",
    "outage": "narrow",
}


LEADS_DIR = DEMO_DIR / "leads"


class LeadConvertIn(BaseModel):
    assignee_id: int | None = None
    due_hours: int = 72


def _lead_path(case_no: str) -> Path:
    if not re.match(r"^SIXG-\d{8}-[A-Z]{2,12}$", case_no or ""):
        raise HTTPException(400, "演示线索编号不合法")
    return LEADS_DIR / f"{case_no}.json"


def _save_demo_lead(result: dict[str, Any]) -> None:
    try:
        case_no = str((result.get("case") or {}).get("case_no") or "")
        path = _lead_path(case_no)
        LEADS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        print(f"[sixg] 演示线索缓存失败：{exc}", flush=True)


def _load_demo_lead(case_no: str) -> dict[str, Any]:
    path = _lead_path(case_no)
    if not path.is_file():
        raise HTTPException(404, "演示线索缓存不存在，请先在 6G 链路面板重新推演")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(500, f"演示线索缓存损坏：{exc}") from exc


def _lead_evidence(case_no: str) -> list[dict[str, Any]]:
    items = [
        (OUT_DIR / "after_overlay.png", "after_overlay.png", "image/png", "AI 变化检测叠加图"),
        (OUT_DIR / "diff_heatmap.png", "diff_heatmap.png", "image/png", "变化强度热力图"),
        (DEMO_DIR / "before.png", "before.png", "image/png", "基线影像 2023-10-08"),
        (DEMO_DIR / "after.png", "after.png", "image/png", "复拍影像 2025-09-27"),
    ]
    return [
        {"name": name, "path": str(path), "mime": mime,
         "note": f"6G 演示线索 {case_no} · {note}"}
        for path, name, mime, note in items if path.is_file()
    ]
TILE_COLS, TILE_ROWS = 2, 2

# AI 工作形态：同一套候选池，不同网络档位使用不同的复核特征、回传对象与输出方式。
# 这里不伪装成“不同大模型”：全幅档做四特征精检，ROI/窄带档只做纹理+ExG 复核，
# 窄带档把像素瓦片换成任务语义特征包；差异在流水线，不在模型名称。
AI_MODES: dict[str, dict[str, Any]] = {
    "full": {
        "label": "全幅多特征精检",
        "short": "全幅精检",
        "features": ["纹理", "高通", "色彩", "ExG"],
        "input": "4 块瓦片像素",
        "payload": "tile",
        "note": "全幅四特征复核，优先保证召回；适合 eMBB 大带宽。",
    },
    "balanced": {
        "label": "三瓦片均衡复核",
        "short": "均衡复核",
        "features": ["纹理", "高通", "色彩", "ExG"],
        "input": "3 块瓦片像素",
        "payload": "tile",
        "note": "保留主要候选，减少一块瓦片回传；适合切换期。",
    },
    "roi": {
        "label": "ROI 双特征轻检",
        "short": "ROI 轻检",
        "features": ["纹理", "ExG"],
        "input": "2 块 ROI 瓦片",
        "payload": "tile",
        "note": "只对高变化瓦片做纹理+ExG 轻量复核，牺牲部分召回换时效。",
    },
    "narrow": {
        "label": "语义特征包",
        "short": "语义包",
        "features": ["纹理", "ExG"],
        "input": "1 块 ROI 瓦片",
        "payload": "semantic",
        "note": "窄带回传结构化特征包，不传整块像素；覆盖缺口由 AI 反向请求补传。",
    },
}
PROFILE_AI_MODE = {key: key for key in PROFILES}


class ScenarioIn(BaseModel):
    scenario: str = "normal"
    outage_at_s: int | None = None
    slice_quota_pct: int | None = None
    uav_enabled: bool | None = None

    def params(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.outage_at_s is not None:
            data["outage_at_s"] = max(5, min(40, int(self.outage_at_s)))
        if self.slice_quota_pct is not None:
            data["slice_quota_pct"] = max(40, min(100, int(self.slice_quota_pct)))
        if self.uav_enabled is not None:
            data["uav_enabled"] = bool(self.uav_enabled)
        return data


def _scenario(name: str) -> dict[str, Any]:
    cfg = SCENARIOS.get(name)
    if not cfg:
        raise HTTPException(400, "未知推演场景")
    return cfg


# ---------------------------------------------------------------------------
# 6G 网络数字孪生
# ---------------------------------------------------------------------------
def _network_series(scenario: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _scenario(scenario)
    params = params or {}
    outage_at = max(5, min(40, int(params.get("outage_at_s", 10))))
    recover_at = outage_at + 10
    quota_pct = max(40, min(100, int(params.get("slice_quota_pct", 100))))
    uav_enabled = bool(params.get("uav_enabled", True))
    rng = random.Random(cfg["seed"])
    points: list[dict[str, Any]] = []
    for index in range(26):
        t = index * 5
        if scenario == "normal":
            latency = 11.5 + 1.8 * math.sin(index / 3.1) + rng.uniform(-0.6, 0.6)
            down = 420 - 10 * math.sin(index / 2.7) + rng.uniform(-6, 6)
            loss = 0.08 + rng.uniform(0, 0.05)
            jitter = 1.2 + rng.uniform(0, 0.4)
            edge = 38 + rng.uniform(-2, 2)
            bs, ntn, state = 1, 0, "地面 6G 主链路"
        elif scenario == "outage":
            if t < outage_at:
                latency = 12.0 + rng.uniform(-0.5, 0.5)
                down = 400 + rng.uniform(-5, 5)
                loss = 0.1 + rng.uniform(0, 0.05)
                jitter = 1.4 + rng.uniform(0, 0.3)
                edge = 40 + rng.uniform(-2, 2)
                bs, ntn, state = 1, 0, "地面 6G 主链路"
            elif t < recover_at:
                latency = 92.0 - (t - outage_at) * 3.4 + rng.uniform(-4, 4)
                down = 120 + rng.uniform(-8, 8)
                loss = 1.6 + rng.uniform(-0.2, 0.4)
                jitter = 11 + rng.uniform(-1, 1)
                edge = 58 + rng.uniform(-3, 3)
                bs, ntn, state = 0, 1, "NTN 接管中"
            else:
                latency = 46 + 2.2 * math.sin(index / 2.5) + rng.uniform(-1.5, 1.5)
                down = 96 + rng.uniform(-5, 5)
                loss = 0.85 + rng.uniform(0, 0.2)
                jitter = 8.5 + rng.uniform(-0.8, 0.8)
                edge = 62 + rng.uniform(-2, 2)
                bs, ntn, state = 0, 1, "NTN 备份链路"
        elif scenario == "handover":
            if 20 <= t <= 30:
                latency = 132 - abs(t - 25) * 7 + rng.uniform(-6, 6)
                down = 210 + rng.uniform(-15, 15)
                loss = 2.6 + rng.uniform(-0.3, 0.6)
                jitter = 18 + rng.uniform(-2, 2)
                edge = 55 + rng.uniform(-3, 3)
                bs, ntn, state = 1, 1, "卫星波束切换"
            else:
                latency = 21 + 2.5 * math.sin(index / 3.4) + rng.uniform(-1, 1)
                down = 360 + rng.uniform(-10, 10)
                loss = 0.18 + rng.uniform(0, 0.08)
                jitter = 2.4 + rng.uniform(0, 0.5)
                edge = 46 + rng.uniform(-2, 2)
                bs, ntn, state = 1, 0, "地面 6G 主链路"
        else:  # congestion
            load = math.exp(-((t - 55) / 24) ** 2)
            latency = 20 + 150 * load + rng.uniform(-5, 5)
            down = 520 - 340 * load + rng.uniform(-12, 12)
            loss = 0.25 + 3.2 * load + rng.uniform(0, 0.2)
            jitter = 3.2 + 11 * load + rng.uniform(0, 0.8)
            edge = 74 + 18 * load + rng.uniform(-2, 2)
            bs, ntn, state = 1, 0, "eMBB 切片拥塞" if load > 0.25 else "地面 6G 主链路"
        if quota_pct < 100:
            down *= quota_pct / 100.0
        points.append({
            "t": t,
            "latency_ms": round(max(6.0, latency), 1),
            "jitter_ms": round(max(0.2, jitter), 1),
            "down_mbps": round(max(8.0, down), 1),
            "loss_pct": round(max(0.0, min(8.0, loss)), 2),
            "edge_load_pct": round(max(5.0, min(99.0, edge)), 1),
            "bs_online": bool(bs),
            "ntn_active": bool(ntn),
            "state": state,
        })

    avg = lambda key: sum(row[key] for row in points) / len(points)  # noqa: E731
    events: list[dict[str, Any]] = []
    if scenario == "outage":
        events = [
            {"t": outage_at, "level": "warn", "text": "地面基站退服，链路中断告警"},
            {"t": outage_at + 5, "level": "ok", "text": "低轨卫星 NTN 接管，业务恢复"},
            {"t": outage_at + 15, "level": "info", "text": "URLLC 切片重分配，指挥控制优先"},
        ]
    elif scenario == "handover":
        events = [
            {"t": 20, "level": "warn", "text": "卫星波束切换，时延短时抬升"},
            {"t": 30, "level": "ok", "text": "波束切换完成，链路恢复"},
        ]
    elif scenario == "congestion":
        events = [
            {"t": 35, "level": "warn", "text": "eMBB 需求超过切片配额"},
            {"t": 55, "level": "info", "text": "按优先级重调度，URLLC 保底"},
            {"t": 85, "level": "ok", "text": "负载回落，切片恢复"},
        ]
    else:
        events = [{"t": 0, "level": "ok", "text": "6G 端到端切片就绪"}]

    events.append({
        "t": 0 if not events else events[-1]["t"] + 5,
        "level": "ok" if uav_enabled else "warn",
        "text": "无人机复核已启用" if uav_enabled else "无人机复核未启用，覆盖缺口需等待下一轨",
    })
    return {
        "scenario": scenario,
        "label": cfg["label"],
        "code": cfg["code"],
        "desc": cfg["desc"],
        "series": points,
        "events": events,
        "kpi": {
            "avg_latency_ms": round(avg("latency_ms"), 1),
            "peak_latency_ms": round(max(row["latency_ms"] for row in points), 1),
            "avg_down_mbps": round(avg("down_mbps"), 1),
            "avg_loss_pct": round(avg("loss_pct"), 2),
            "link_availability_pct": round(100 - avg("loss_pct"), 2),
            "ground_uptime_pct": round(100 * sum(1 for row in points if row["bs_online"]) / len(points), 1),
            "ntn_active_pct": round(100 * sum(1 for row in points if row["ntn_active"]) / len(points), 1),
            "avg_edge_load_pct": round(avg("edge_load_pct"), 1),
        },
        "slices": cfg["slices"],
        "topology": {
            "base_station": "offline" if scenario == "outage" else ("busy" if scenario == "congestion" else "online"),
            "satellite": "active" if scenario in ("outage", "handover") else "standby",
            "edge": "busy" if scenario == "congestion" else "online",
            "uav": "online" if uav_enabled else "offline",
            "command": "online",
        },
        "params": {
            "outage_at_s": outage_at,
            "slice_quota_pct": quota_pct,
            "uav_enabled": uav_enabled,
        },
        "simulated": True,
    }


# ---------------------------------------------------------------------------
# 变化检测
# ---------------------------------------------------------------------------
def _blur(array: np.ndarray, radius: int = 1) -> np.ndarray:
    image = Image.fromarray(np.clip(array * 255, 0, 255).astype(np.uint8))
    image = image.filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(image, dtype=np.float32) / 255.0


def _dilate(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    for _ in range(radius):
        grown = mask.copy()
        grown[1:, :] |= mask[:-1, :]
        grown[:-1, :] |= mask[1:, :]
        grown[:, 1:] |= mask[:, :-1]
        grown[:, :-1] |= mask[:, 1:]
        mask = grown
    return mask


def _erode(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    return ~_dilate(~mask, radius)


def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    result: list[list[tuple[int, int]]] = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            pixels: list[tuple[int, int]] = []
            while stack:
                cy, cx = stack.pop()
                pixels.append((cy, cx))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            result.append(pixels)
    return result


def _normalize(array: np.ndarray) -> np.ndarray:
    low, high = np.percentile(array, [2, 98])
    return np.clip((array - low) / max(float(high - low), 1e-6), 0.0, 1.0)


def _features(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    excess_green = 2 * green - red - blue
    grad_y, grad_x = np.gradient(luminance)
    return luminance, excess_green, np.hypot(grad_x, grad_y)


def _tile_order(before: np.ndarray, after: np.ndarray) -> list[int]:
    """用低分辨率缩略图先算变化能量，决定高分辨率瓦片的回传优先级。"""
    small_before = np.asarray(Image.fromarray((before * 255).astype(np.uint8)).resize((64, 32)), dtype=np.float32) / 255.0
    small_after = np.asarray(Image.fromarray((after * 255).astype(np.uint8)).resize((64, 32)), dtype=np.float32) / 255.0
    diff = np.abs(small_after.mean(axis=2) - small_before.mean(axis=2))
    energy: list[tuple[float, int]] = []
    for row in range(TILE_ROWS):
        for col in range(TILE_COLS):
            block = diff[row * 16:(row + 1) * 16, col * 32:(col + 1) * 32]
            energy.append((float(block.mean()), row * TILE_COLS + col))
    energy.sort(reverse=True)
    return [index for _, index in energy]


def _detect_tile(before: np.ndarray, after: np.ndarray, x0: int, y0: int, mode: str = "full") -> tuple[list[dict[str, Any]], int, float, float]:
    """在单块 256×128 瓦片上执行变化检测；只处理已回传的瓦片。"""
    tile_before = before[y0:y0 + 128, x0:x0 + 256]
    tile_after = after[y0:y0 + 128, x0:x0 + 256]
    started = time.perf_counter()

    lum1, exg1, tex1 = _features(tile_before)
    lum2, exg2, tex2 = _features(tile_after)

    diff_rgb = _blur(np.sqrt(((tile_after - tile_before) ** 2).mean(axis=2)), 1)
    diff_exg = _blur(np.abs(exg2 - exg1), 1)
    diff_texture = _blur(np.abs(tex2 - tex1), 1)
    highpass1 = lum1 - _blur(lum1, 8)
    highpass2 = lum2 - _blur(lum2, 8)
    diff_highpass = _blur(np.abs(highpass2 - highpass1), 1)

    if mode == "light":
        # ROI/窄带档只复核纹理与 ExG，减少特征计算；候选池仍由全局粗筛提供。
        score = (
            0.65 * _normalize(diff_texture)
            + 0.25 * _normalize(diff_exg)
            + 0.10 * _normalize(diff_rgb)
        )
        threshold = max(float(np.quantile(score, 0.90)), 0.10)
    else:
        score = (
            0.45 * _normalize(diff_texture)
            + 0.25 * _normalize(diff_highpass)
            + 0.20 * _normalize(diff_rgb)
            + 0.10 * _normalize(diff_exg)
        )
        threshold = max(float(np.quantile(score, 0.93)), 0.10)
    mask = _dilate(_erode(score >= threshold, 1), 2)
    candidates = [p for p in _components(mask) if len(p) >= 150]

    rows: list[dict[str, Any]] = []
    suppressed = 0
    for pixels in candidates:
        ys = [p[0] for p in pixels]
        xs = [p[1] for p in pixels]
        index = np.asarray(pixels)
        delta_lum = float((lum2 - lum1)[index[:, 0], index[:, 1]].mean())
        if delta_lum <= 0.03:
            suppressed += 1
            continue
        delta_exg = float((exg2 - exg1)[index[:, 0], index[:, 1]].mean())
        mean_score = float(score[index[:, 0], index[:, 1]].mean())
        area_px = len(pixels)
        change_class = "疑似采矿/取土" if delta_lum > 0.12 and area_px >= 150 else "裸土/建设扰动"
        confidence = 0.62 + 0.22 * min(1.0, (delta_lum - 0.03) / 0.20) \
            + 0.10 * min(1.0, area_px / 400) + 0.06 * min(1.0, mean_score / 0.8)
        confidence = round(max(0.60, min(0.96, confidence)), 2)
        rows.append({
            "bbox": [
                2 * (x0 + min(xs)),
                2 * (y0 + min(ys)),
                2 * (max(xs) - min(xs) + 1),
                2 * (max(ys) - min(ys) + 1),
            ],
            "area_ha": round(area_px * 4.76, 1),
            "confidence": confidence,
            "class": change_class,
            "score": round(mean_score, 3),
            "delta_lum": round(delta_lum, 3),
            "delta_exg": round(delta_exg, 3),
            "change": f"亮度 {delta_lum:+.3f} / 植被 {delta_exg:+.3f}",
            "tile": y0 // 128 * TILE_COLS + x0 // 256,
        })

    elapsed = (time.perf_counter() - started) * 1000
    return rows, suppressed, threshold, elapsed


def _full_detection() -> dict[str, Any]:
    """整幅影像的粗筛结果；作为各网络档位共享的候选池。"""
    global _FULL_CACHE
    if _FULL_CACHE is not None:
        return _FULL_CACHE
    with _FULL_LOCK:
        if _FULL_CACHE is not None:
            return _FULL_CACHE
        before_path = DEMO_DIR / "before.png"
        after_path = DEMO_DIR / "after.png"
        if not before_path.exists() or not after_path.exists():
            raise HTTPException(503, "演示影像缺失，请先准备 data/sixg_demo/before.png 与 after.png")

        started = time.perf_counter()
        image_sha256 = hashlib.sha256(after_path.read_bytes()).hexdigest()
        before = np.asarray(Image.open(before_path).convert("RGB").resize((512, 256)), dtype=np.float32) / 255.0
        after = np.asarray(Image.open(after_path).convert("RGB").resize((512, 256)), dtype=np.float32) / 255.0

        lum1, exg1, tex1 = _features(before)
        lum2, exg2, tex2 = _features(after)

        diff_rgb = _blur(np.sqrt(((after - before) ** 2).mean(axis=2)), 1)
        diff_exg = _blur(np.abs(exg2 - exg1), 1)
        diff_texture = _blur(np.abs(tex2 - tex1), 1)
        highpass1 = lum1 - _blur(lum1, 8)
        highpass2 = lum2 - _blur(lum2, 8)
        diff_highpass = _blur(np.abs(highpass2 - highpass1), 1)

        score = (
            0.45 * _normalize(diff_texture)
            + 0.25 * _normalize(diff_highpass)
            + 0.20 * _normalize(diff_rgb)
            + 0.10 * _normalize(diff_exg)
        )

        baseline_mask = _erode(diff_rgb >= 0.16, 1)
        baseline_patches = [p for p in _components(baseline_mask) if len(p) >= 100]

        threshold = max(float(np.quantile(score, 0.93)), 0.10)
        mask = _dilate(_erode(score >= threshold, 1), 2)
        candidates = [p for p in _components(mask) if len(p) >= 150]

        rows: list[dict[str, Any]] = []
        suppressed = 0
        for pixels in candidates:
            ys = [p[0] for p in pixels]
            xs = [p[1] for p in pixels]
            index = np.asarray(pixels)
            delta_lum = float((lum2 - lum1)[index[:, 0], index[:, 1]].mean())
            if delta_lum <= 0.03:
                suppressed += 1
                continue
            delta_exg = float((exg2 - exg1)[index[:, 0], index[:, 1]].mean())
            mean_score = float(score[index[:, 0], index[:, 1]].mean())
            area_px = len(pixels)
            change_class = "疑似采矿/取土" if delta_lum > 0.12 and area_px >= 150 else "裸土/建设扰动"
            confidence = 0.62 + 0.22 * min(1.0, (delta_lum - 0.03) / 0.20) \
                + 0.10 * min(1.0, area_px / 400) + 0.06 * min(1.0, mean_score / 0.8)
            confidence = round(max(0.60, min(0.96, confidence)), 2)
            rows.append({
                "bbox": [min(xs) * 2, min(ys) * 2, (max(xs) - min(xs) + 1) * 2, (max(ys) - min(ys) + 1) * 2],
                "area_ha": round(area_px * 4.76, 1),
                "confidence": confidence,
                "class": change_class,
                "score": round(mean_score, 3),
                "delta_lum": round(delta_lum, 3),
                "delta_exg": round(delta_exg, 3),
                "change": f"亮度 {delta_lum:+.3f} / 植被 {delta_exg:+.3f}",
            })

        rows.sort(key=lambda item: (item["confidence"], item["area_ha"]), reverse=True)
        for idx, row in enumerate(rows, 1):
            row["id"] = f"P-{idx:02d}"
            x, y, w, h = row["bbox"]
            row["center_lonlat"] = [
                round(108.5688 + (x + w / 2) / 1024 * (109.9199 - 108.5688), 5),
                round(42.2504 - (y + h / 2) / 512 * (42.2504 - 41.7459), 5),
            ]

        _FULL_CACHE = {
            "rows": rows,
            "score": score,
            "before": before,
            "after": after,
            "baseline_patches": len(baseline_patches),
            "suppressed": suppressed,
            "threshold": round(threshold, 3),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "image_sha256": image_sha256,
            "before_path": before_path,
            "after_path": after_path,
        }
        return _FULL_CACHE


def _tile_of(row: dict[str, Any]) -> int:
    x, y, w, h = row["bbox"]
    col = min(TILE_COLS - 1, max(0, int((x + w / 2) / 512)))
    rowno = min(TILE_ROWS - 1, max(0, int((y + h / 2) / 256)))
    return rowno * TILE_COLS + col


def _detect(profile: str = "full") -> dict[str, Any]:
    if profile not in PROFILES:
        profile = "full"
    cached = _DETECTION_CACHE.get(profile)
    if cached is not None:
        return cached
    with _DETECTION_LOCK:
        cached = _DETECTION_CACHE.get(profile)
        if cached is not None:
            return cached

        base = _full_detection()
        rows_all = base["rows"]
        if not rows_all:
            raise HTTPException(500, "演示影像未检出候选图斑")

        # 按“候选置信度”给瓦片排序；没有候选的瓦片用正变化能量补位。
        tile_rank: dict[int, float] = {}
        for row in rows_all:
            tile = _tile_of(row)
            tile_rank[tile] = max(tile_rank.get(tile, 0.0), row["confidence"])
        ranked = [tile for tile, _ in sorted(tile_rank.items(), key=lambda item: item[1], reverse=True)]
        for tile in _tile_order(base["before"], base["after"]):
            if tile not in ranked:
                ranked.append(tile)

        sent = ranked[:PROFILES[profile]["tiles"]]
        filtered = [dict(row) for row in rows_all if _tile_of(row) in sent]
        if not filtered:
            filtered = [dict(row) for row in rows_all]
            sent = [_tile_of(rows_all[0])]
        filtered.sort(key=lambda item: (item["confidence"], item["area_ha"]), reverse=True)
        for idx, row in enumerate(filtered, 1):
            row["id"] = f"P-{idx:02d}"

        roi_ms = 0.0
        roi_rows: list[dict[str, Any]] = []
        verify_mode = "light" if profile in ("roi", "narrow") else "full"
        for tile in sent:
            x0 = (tile % TILE_COLS) * 256
            y0 = (tile // TILE_COLS) * 128
            rows_tile, _, _, tile_ms = _detect_tile(base["before"], base["after"], x0, y0, verify_mode)
            roi_rows.extend(rows_tile)
            roi_ms += tile_ms

        suffix = "" if profile == "full" else "_" + profile
        _render_assets(base["after_path"], filtered, base["score"], suffix)
        high_confidence = sum(1 for row in filtered if row["confidence"] >= 0.80)
        _DETECTION_CACHE[profile] = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "profile": profile,
            "profile_label": PROFILES[profile]["label"],
            "model": AI_MODES[profile]["label"],
            "ai_mode": AI_MODES[profile],
            "images": {
                "before": {"date": "2023-10-08", "id": "S2B_MSIL2A_20231008T033629_R061_T49TCG", "cloud_pct": 0.65},
                "after": {"date": "2025-09-27", "id": "S2B_MSIL2A_20250927T033539_R061_T49TCG", "cloud_pct": 3.50},
            },
            "bbox": [108.5688, 41.7459, 109.9199, 42.2504],
            "size": [1024, 512],
            "metrics": {
                "baseline_patches": base["baseline_patches"],
                "ai_patches": len(filtered),
                "high_confidence": high_confidence,
                "mean_confidence": round(sum(r["confidence"] for r in filtered) / len(filtered), 2) if filtered else 0,
                "suppressed": base["suppressed"],
                "threshold": base["threshold"],
                "inference_ms": round(roi_ms, 1),
                "roi_verified": len(roi_rows),
                "total_area_ha": round(sum(r["area_ha"] for r in filtered), 1),
            },
            "verification": {
                "mode": verify_mode,
                "features": AI_MODES[profile]["features"],
                "verified": len(roi_rows),
                "note": "候选池由全局粗筛共享，档位差异在复核特征与回传对象。",
            },
            "tiles": {
                "total": TILE_COLS * TILE_ROWS,
                "sent": len(sent),
                "indices": sent,
                "order": ranked,
            },
            "patches": filtered[:10],
            "overlay_url": f"/api/sixg/asset/after_overlay{suffix}.png",
            "heatmap_url": f"/api/sixg/asset/diff_heatmap{suffix}.png",
            "method": "全局粗筛 + ROI 瓦片精检：复用边缘基线影像，仅回传高变化瓦片；多特征融合与光照不变抑制",
            "disclaimer": "图斑为算法候选，面积按像元估算，需人工复核；6G 指标为数字孪生仿真。",
        }
        return _DETECTION_CACHE[profile]


def _render_assets(after_path: Path, rows: list[dict[str, Any]], score: np.ndarray, suffix: str = "") -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(after_path) as source:
        base = source.convert("RGBA")

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for row in rows:
        x, y, w, h = row["bbox"]
        pad = 6
        draw.rectangle(
            [max(0, x - pad), max(0, y - pad), min(base.width - 1, x + w + pad), min(base.height - 1, y + h + pad)],
            outline=(255, 196, 64, 255),
            width=3,
        )
        draw.rectangle([max(0, x - pad), max(0, y - pad), max(0, x - pad) + 34, max(0, y - pad) + 18], fill=(255, 196, 64, 235))
        draw.text((max(0, x - pad) + 4, max(0, y - pad) + 3), row["id"], fill=(16, 24, 36, 255))
    Image.alpha_composite(base, overlay).convert("RGB").save(OUT_DIR / f"after_overlay{suffix}.png")

    # 热力图：低值深蓝，高值橙红。
    clipped = np.clip(score, 0, 1)
    heat = np.zeros((*clipped.shape, 3), dtype=np.float32)
    heat[..., 0] = np.clip(1.6 * clipped, 0, 1)
    heat[..., 1] = np.clip(1.2 * clipped - 0.15, 0, 0.85)
    heat[..., 2] = np.clip(0.55 - 1.2 * clipped, 0, 0.55)
    Image.fromarray((heat * 255).astype(np.uint8)).resize((1024, 512), Image.BILINEAR).save(OUT_DIR / f"diff_heatmap{suffix}.png")


# ---------------------------------------------------------------------------
# 线索、法条与报告
# ---------------------------------------------------------------------------
def _legal_query(change_class: str) -> tuple[str, str]:
    if "采矿" in change_class:
        return "非法采矿 盗采 砂石 破坏性采矿", "mining"
    if "建设" in change_class or "裸土" in change_class:
        return "非法占用农用地 耕地 硬化 取土", "bare_soil"
    return "毁林 采伐 林木 植被破坏", "veg_degradation"


def _semantic_packet(detection: dict[str, Any], full_detection: dict[str, Any]) -> dict[str, Any]:
    """任务导向语义回传包：只传图斑结构化特征，不传整块像素。"""
    lead = detection["patches"][0]
    bbox = lead["bbox"]
    descriptor = [
        round(float(lead.get("score", 0)), 4),
        round(float(lead.get("delta_lum", 0)), 4),
        round(float(lead.get("delta_exg", 0)), 4),
        round(float(lead.get("confidence", 0)), 4),
        round(float(lead.get("area_ha", 0)), 1),
        round(bbox[0] / 1024, 4),
        round(bbox[1] / 512, 4),
        round(bbox[2] / 1024, 4),
        round(bbox[3] / 512, 4),
    ]
    payload = {
        "v": 1,
        "type": "change-patch",
        "cls": lead["class"],
        "bbox": bbox,
        "conf": lead["confidence"],
        "area_ha": lead["area_ha"],
        "desc": descriptor,
        "sha256": str(full_detection.get("image_sha256", ""))[:16],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    feature_kb = max(1.2, round(len(raw) / 1024, 2))
    return {
        "mode": "semantic",
        "format": "TSF-1",
        "fields": ["bbox", "class", "confidence", "area_ha", "descriptor9", "image_sha256"],
        "feature_kb": feature_kb,
        "pixel_equivalent_mb": 1.6,
        "compression_ratio": round(1.6 * 1024 / feature_kb),
        "payload": payload,
        "note": f"单条线索语义包约 {feature_kb} KB，像素瓦片等效 1.6 MB。",
    }


def _feedback(scenario: str, network: dict[str, Any], detection: dict[str, Any], full_detection: dict[str, Any]) -> dict[str, Any]:
    """AI 的不确定性反向触发网络补传/重访；默认断网场景演示一次补传闭环。"""
    kept = int(detection["metrics"]["ai_patches"])
    full_count = int(full_detection["metrics"]["ai_patches"])
    missed = max(0, full_count - kept)
    mean_conf = float(detection["metrics"].get("mean_confidence") or 0)
    uav_enabled = bool(network.get("params", {}).get("uav_enabled", True))
    if missed <= 0 and mean_conf >= 0.75:
        return {
            "status": "none",
            "trigger": "无需补传",
            "detail": f"当前 {kept} 条线索平均置信度 {mean_conf:.2f}，覆盖完整。",
            "request": "无",
            "granted": False,
            "extra_tiles": 0,
            "extra_mb": 0.0,
            "extra_ms": 0.0,
            "recovered": 0,
            "note": "AI 置信度与覆盖率均满足要求，不额外占用网络资源。",
        }
    uplink_mbps = max(18.0, float(network["kpi"]["avg_down_mbps"]) * 0.45)
    extra_mb = 1.6 if missed else 0.0
    extra_ms = round(extra_mb * 8 / uplink_mbps * 1000, 1)
    trigger = "覆盖缺口" if missed else "置信度不足"
    granted = bool(missed and (uav_enabled or scenario != "outage"))
    request = "经 NTN 补传 1 个高变化瓦片" if scenario == "outage" else "经当前切片补传 1 个高变化瓦片"
    return {
        "status": "granted" if granted else "pending",
        "trigger": trigger,
        "detail": f"未回传区域仍有 {missed} 个候选，平均置信度 {mean_conf:.2f}；AI 请求补传。",
        "request": request,
        "granted": granted,
        "extra_tiles": 1 if granted else 0,
        "extra_mb": extra_mb if granted else 0.0,
        "extra_ms": extra_ms if granted else 0.0,
        "recovered": missed if granted else 0,
        "note": (
            f"补传 1 个瓦片（+{extra_mb} MB / +{extra_ms} ms），恢复 {missed} 个候选。"
            if granted
            else "无人机复核未启用，缺口只能等待下一轨或人工复核。"
        ),
    }


def _comparison(plan: dict[str, Any], network: dict[str, Any]) -> dict[str, Any]:
    """传统巡检与 6G×AI 编排的演示对照；全部为假设口径。"""
    trad_min = 45.0
    trad_mb = 6.4
    sixg_ms = float(plan["end_to_end_ms"])
    sixg_mb = float(plan["uplink_mb"])
    return {
        "traditional": {
            "detect_min": trad_min,
            "uplink_mb": trad_mb,
            "note": "人工巡查 + 全量四瓦片回传假设",
        },
        "sixg": {
            "detect_ms": sixg_ms,
            "uplink_mb": sixg_mb,
            "patch_count": plan["patch_count"],
            "note": "网络感知编排 + 边缘 AI + AI 回馈补传",
        },
        "delta": {
            "latency_reduction_pct": round((1 - sixg_ms / (trad_min * 60000)) * 100, 2),
            "uplink_saved_pct": round((1 - sixg_mb / trad_mb) * 100, 1),
        },
        "assumptions": [
            "传统巡检按人工发现 45 分钟、全量 4 块瓦片回传估算。",
            "6G 指标为数字孪生仿真，端到端时延不含人工核查与现场处置。",
            "对比用于说明编排方向，不作为真实效能承诺。",
        ],
        "simulated": True,
    }


def _orchestration(
    scenario: str,
    network: dict[str, Any],
    detection: dict[str, Any],
    full_detection: dict[str, Any],
    semantic: dict[str, Any] | None = None,
    feedback: dict[str, Any] | None = None,
    final_count: int | None = None,
) -> dict[str, Any]:
    """把 6G 网络状态翻译成 AI 推理编排：切片、瓦片数、回传量、AI 反馈与端到端预算。"""
    kpi = network["kpi"]
    tiles = detection["tiles"]
    sent = int(tiles["sent"])
    total = int(tiles["total"])
    feedback = feedback or {}
    extra_tiles = int(feedback.get("extra_tiles", 0) or 0)
    effective_tiles = sent + extra_tiles
    tile_mb = 1.6  # 单块 256×128 多光谱瓦片（演示估算）
    semantic_applied = bool(semantic and semantic.get("mode") == "semantic")
    base_mb = (float(semantic["feature_kb"]) / 1024.0) if semantic_applied else sent * tile_mb
    uplink_mb = round(base_mb + extra_tiles * tile_mb, 4)
    uplink_mbps = max(18.0, kpi["avg_down_mbps"] * 0.45)
    uplink_ms = round(uplink_mb * 8 / uplink_mbps * 1000, 1)
    compute_ms = detection["metrics"]["inference_ms"]
    end_to_end_ms = round(kpi["avg_latency_ms"] + uplink_ms + compute_ms + 6, 1)
    saved_pct = round((1 - uplink_mb / (total * tile_mb)) * 100, 1)
    if semantic_applied:
        saved_pct = min(saved_pct, 99.9)
    full_count = full_detection["metrics"]["ai_patches"]
    kept = int(final_count if final_count is not None else detection["metrics"]["ai_patches"])
    missed = max(0, full_count - kept)
    slice_name = {
        "normal": "eMBB",
        "handover": "URLLC",
        "congestion": "URLLC",
        "outage": "URLLC + NTN",
    }[scenario]
    ai_mode = detection.get("ai_mode", {})
    model = str(ai_mode.get("label") or detection["model"])
    if feedback.get("granted"):
        note = (
            f"初始 {sent}/{total} 个瓦片；AI 回馈补传 {extra_tiles} 个后 {effective_tiles}/{total}，"
            f"最终 {kept} 条线索。"
        )
    elif semantic_applied:
        note = (
            f"窄带回传 {sent}/{total} 个瓦片的结构化特征包（{semantic['feature_kb']} KB），"
            f"保留 {kept} 条线索；未回传区域等待网络恢复后补检。"
        )
    elif missed:
        note = (
            f"弱网下仅回传 {sent}/{total} 个高变化瓦片，保留 {kept} 个图斑；"
            f"未回传区域（约 {missed} 个候选）在网络恢复后自动补检。"
        )
    else:
        note = f"{sent}/{total} 个瓦片覆盖全部 {kept} 个候选图斑，端到端 {end_to_end_ms} ms，满足核查时效。"
    steps = [
        {"step": "任务下发", "status": "ok", "detail": f"指挥中心经 {slice_name} 切片下发核查任务"},
        {"step": "切片选择", "status": "ok", "detail": f"{slice_name}；可用下行 {kpi['avg_down_mbps']} Mbps，平均时延 {kpi['avg_latency_ms']} ms"},
        {"step": "瓦片筛选", "status": "ok", "detail": f"复用边缘基线影像，选出 {sent}/{total} 个高变化瓦片"},
        {"step": "影像回传", "status": "warn" if scenario == "outage" else "ok", "detail": f"{round(base_mb, 2)} MB，约 {round(base_mb * 8 / uplink_mbps * 1000, 1)} ms（按可用带宽折算）"},
        {"step": "边缘推理", "status": "ok", "detail": f"{model}；{compute_ms} ms；输出 {detection['metrics']['ai_patches']} 个候选"},
    ]
    if feedback.get("granted"):
        steps.append({"step": "AI 回馈", "status": "ok", "detail": f"{feedback.get('trigger')}：请求补传 1 个瓦片，网络已授权"})
        steps.append({"step": "补传复核", "status": "ok", "detail": f"+{feedback.get('extra_mb')} MB / +{feedback.get('extra_ms')} ms，恢复 {feedback.get('recovered')} 个候选"})
    steps.append({"step": "结果回传", "status": "warn" if missed else "ok", "detail": f"节省 {saved_pct}% 回传带宽；{('遗留 '+str(missed)+' 个待补检') if missed else '候选全覆盖'}"})
    return {
        "slice": slice_name,
        "model": model,
        "ai_mode": ai_mode,
        "tiles_sent": sent,
        "tiles_total": total,
        "tiles_effective": effective_tiles,
        "extra_tiles": extra_tiles,
        "uplink_mb": uplink_mb,
        "uplink_ms": uplink_ms,
        "compute_ms": compute_ms,
        "end_to_end_ms": end_to_end_ms,
        "bandwidth_saved_pct": saved_pct,
        "patch_count": kept,
        "full_patch_count": full_count,
        "missed_patches": missed,
        "deadline_ms": 1500,
        "within_sla": end_to_end_ms <= 1500,
        "note": note,
        "steps": steps,
        "payload": semantic,
        "feedback": feedback,
        "simulated": True,
    }

def _demo(scenario: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    network = _network_series(scenario, params)
    profile = PROFILE_BY_SCENARIO.get(scenario, "full")
    initial = _detect(profile)
    full_detection = _detect("full")
    feedback = _feedback(scenario, network, initial, full_detection)

    final_detection = initial
    if feedback.get("granted"):
        revisit = _detect("roi")
        final_detection = revisit if len(revisit["patches"]) >= len(initial["patches"]) else full_detection
    final_detection = dict(final_detection)
    patches = final_detection["patches"]
    if not patches:
        raise HTTPException(500, "演示影像未检出候选图斑")

    semantic = _semantic_packet(final_detection, full_detection)
    if profile != "narrow":
        semantic["mode"] = "pixel"
        semantic["applied"] = False
        semantic["note"] = (
            f"当前档位回传像素瓦片；同一条线索也可压缩为 {semantic['feature_kb']} KB 语义包，"
            f"约为像素的 1/{semantic['compression_ratio']}。"
        )
    else:
        semantic["applied"] = True

    plan = _orchestration(
        scenario,
        network,
        initial,
        full_detection,
        semantic=semantic if profile == "narrow" else None,
        feedback=feedback,
        final_count=len(patches),
    )
    plan["payload"] = semantic
    comparison = _comparison(plan, network)

    final_detection["initial_patches"] = initial["metrics"]["ai_patches"]
    final_detection["initial_tiles"] = initial["tiles"]["sent"]
    final_detection["feedback"] = feedback
    final_detection["semantic"] = semantic

    lead = patches[0]
    query, hazard = _legal_query(lead["class"])
    legal_result = legal.search(query=query, hazard=hazard, limit=5)
    laws = [
        {
            "id": item.get("id"),
            "law": item.get("law"),
            "article": item.get("article"),
            "heading": item.get("heading"),
            "score": item.get("score"),
        }
        for item in legal_result.get("items", [])[:3]
    ]

    today = datetime.now().strftime("%Y%m%d")
    case_no = f"SIXG-{today}-{network['code'][:3]}"
    case = {
        "case_no": case_no,
        "title": f"6G 遥感智能体发现{lead['class']}线索",
        "source": "天枢·6G 空天地一体公共安全智能体",
        "hazard": lead["class"],
        "location": "演示区域（内蒙古中部，Sentinel-2 T49TCG）",
        "center_lonlat": lead["center_lonlat"],
        "area_ha": lead["area_ha"],
        "confidence": lead["confidence"],
        "evidence": [
            "2023-10-08 Sentinel-2 L2A",
            "2025-09-27 Sentinel-2 L2A",
            "AI 变化检测叠加图",
            f"6G 推演场景：{network['label']}",
            f"AI 形态：{plan['ai_mode'].get('label')}",
            f"6G×AI 编排：{plan['slice']} · 初始 {plan['tiles_sent']}/{plan['tiles_total']} 瓦片 · 最终 {plan['tiles_effective']}/{plan['tiles_total']} · 端到端 {plan['end_to_end_ms']} ms",
            f"AI 回馈：{feedback['trigger']} · {feedback['status']}",
            f"语义回传：{semantic['format']} · {semantic['feature_kb']} KB",
        ],
        "legal": laws,
        "suggestions": ["现场核查并固定影像证据", "调取采矿/用地许可与历史审批", "对图斑范围开展无人机复核"],
        "status": "待核查",
    }

    timeline = [
        {"t": 0, "phase": "网络", "level": "ok", "title": "6G 端到端切片建立", "detail": "eMBB 影像回传 + URLLC 指挥控制 + mMTC 感知接入"},
        {"t": 6, "phase": "感知", "level": "ok", "title": "双时相影像回传", "detail": "2023-10-08 / 2025-09-27 Sentinel-2 影像经 6G 切片到达边缘节点"},
        {"t": 6, "phase": "6G", "level": "ok", "title": "网络感知编排", "detail": f"{plan['slice']} 切片；AI 形态：{plan['ai_mode'].get('short')}；初始回传 {plan['tiles_sent']}/{plan['tiles_total']} 个瓦片，节省 {plan['bandwidth_saved_pct']}% 带宽"},
    ]
    if feedback.get("granted"):
        timeline.append({"t": 10, "phase": "AI", "level": "warn", "title": "AI 回馈网络", "detail": feedback["note"]})
    timeline.extend([
        {"t": 12, "phase": "AI", "level": "ok", "title": "边缘变化检测完成", "detail": f"{plan['model']}，{final_detection['metrics']['ai_patches']} 个新增扰动候选，{final_detection['metrics']['high_confidence']} 个高置信，推理 {final_detection['metrics']['inference_ms']} ms"},
        {"t": 18, "phase": "案件", "level": "warn", "title": "自动生成核查线索", "detail": f"{case_no} · {lead['class']} · {lead['area_ha']} 公顷"},
        {"t": 24, "phase": "法条", "level": "ok", "title": "法条与取证方向匹配", "detail": "；".join(f"{law['law']}{law['article']}" for law in laws[:2]) or "未命中法条"},
        {"t": 30, "phase": "报告", "level": "ok", "title": "核查报告生成", "detail": "包含影像、图斑、6G 网络指标、AI 编排、法条依据与处置建议"},
    ])
    for event in network["events"]:
        timeline.append({
            "t": event["t"] + 2,
            "phase": "6G",
            "level": event["level"],
            "title": "网络事件",
            "detail": event["text"],
        })
    timeline.sort(key=lambda item: item["t"])

    report = _report(network, final_detection, plan, lead, case, laws, comparison, semantic)
    return {
        "scenario": network,
        "detection": final_detection,
        "lead": lead,
        "case": case,
        "legal": {
            "query": query,
            "hazard": hazard,
            "primary": legal_result.get("primary"),
            "items": laws,
            "disclaimer": legal_result.get("disclaimer"),
        },
        "timeline": timeline,
        "plan": plan,
        "ai_mode": plan["ai_mode"],
        "feedback": feedback,
        "semantic": semantic,
        "comparison": comparison,
        "sandbox": network.get("params", {}),
        "report": report,
        "simulated": True,
    }

def _size_label(mb: float) -> str:
    value = float(mb)
    return f"{value * 1024:.1f} KB" if value < 0.1 else f"{value:.2f} MB"


def _semantic_line(semantic: dict[str, Any]) -> str:
    if semantic.get("applied"):
        return f"TSF-1 · {semantic['feature_kb']} KB（像素等效 {semantic['pixel_equivalent_mb']} MB，约 1/{semantic['compression_ratio']}）"
    return f"当前档位像素回传；同一条线索可压至 {semantic['feature_kb']} KB（约 1/{semantic['compression_ratio']}）"


def _report(network: dict[str, Any], detection: dict[str, Any], plan: dict[str, Any], lead: dict[str, Any], case: dict[str, Any], laws: list[dict[str, Any]], comparison: dict[str, Any], semantic: dict[str, Any]) -> str:
    kpi = network["kpi"]
    metrics = detection["metrics"]
    law_lines = "\n".join(
        f"{idx}. {law['law']}{law['article']} {law['heading']}" for idx, law in enumerate(laws, 1)
    ) or "暂未匹配到法条，请人工研判。"
    return f"""# 天枢·6G 空天地一体公共安全智能体

## 线索核查报告（演示）

- 报告编号：{case['case_no']}
- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
- 推演场景：{network['label']}（{network['code']}）
- 数据来源：Sentinel-2 L2A（2023-10-08 / 2025-09-27，T49TCG）

## 一、线索概况

| 项目 | 内容 |
| --- | --- |
| 线索类型 | {case['hazard']} |
| 中心坐标 | {case['center_lonlat'][0]:.5f}, {case['center_lonlat'][1]:.5f} |
| 估算面积 | {case['area_ha']} 公顷 |
| 模型置信度 | {case['confidence']:.2f} |
| 当前状态 | {case['status']} |

## 二、6G × AI 协同编排

| 项目 | 内容 |
| --- | --- |
| 切片 | {plan['slice']} |
| AI 形态 | {plan['ai_mode'].get('label')}（{ '、'.join(plan['ai_mode'].get('features', [])) }） |
| 初始回传 | {plan['tiles_sent']} / {plan['tiles_total']} 瓦片 |
| 最终回传 | {plan['tiles_effective']} / {plan['tiles_total']} 瓦片（{'AI 回馈补传 ' + str(plan['extra_tiles']) + ' 块' if plan['extra_tiles'] else 'AI 回馈未补传'}） |
| 回传数据量 | {_size_label(plan['uplink_mb'])}（约 {plan['uplink_ms']} ms） |
| 语义回传 | {_semantic_line(semantic)} |
| AI 回馈 | {plan['feedback'].get('trigger')} · {({'granted': '已授权', 'pending': '待补传', 'skipped': '无需补传'}).get(plan['feedback'].get('status'), plan['feedback'].get('status'))} · {plan['feedback'].get('note')} |
| 边缘推理 | {plan['compute_ms']} ms（{plan['model']}） |
| 端到端时延 | {plan['end_to_end_ms']} ms（SLA {plan['deadline_ms']} ms，{'达标' if plan['within_sla'] else '超时'}） |
| 带宽节省 | {plan['bandwidth_saved_pct']}% |
| 覆盖说明 | {plan['note']} |

## 三、融合效果对比（演示假设）

| 指标 | 传统巡检 | 6G×AI 编排 |
| --- | --- | --- |
| 发现时延 | {comparison['traditional']['detect_min']} 分钟 | {comparison['sixg']['detect_ms']:.0f} ms |
| 上行流量 | {comparison['traditional']['uplink_mb']} MB | {_size_label(comparison['sixg']['uplink_mb'])} |
| 有效线索 | 人工判读 | {comparison['sixg']['patch_count']} 条 |
| 口径说明 | {comparison['traditional']['note']} | {comparison['sixg']['note']} |

## 四、6G 网络保障

| 指标 | 数值 |
| --- | --- |
| 平均时延 | {kpi['avg_latency_ms']} ms |
| 峰值时延 | {kpi['peak_latency_ms']} ms |
| 平均下行带宽 | {kpi['avg_down_mbps']} Mbps |
| 链路可用率 | {kpi['link_availability_pct']}% |
| 地面网在线占比 | {kpi['ground_uptime_pct']}% |
| NTN 启用占比 | {kpi['ntn_active_pct']}% |

## 五、AI 变化检测

- 规则基线候选：{metrics['baseline_patches']} 个
- AI 保留新增扰动：{metrics['ai_patches']} 个（高置信 {metrics['high_confidence']} 个）
- 抑制光照/物候疑似误报：{metrics['suppressed']} 个
- 平均置信度：{metrics['mean_confidence']}
- 推理耗时：{metrics['inference_ms']} ms

首选图斑 {lead['id']}：{lead['class']}，面积约 {lead['area_ha']} 公顷，置信度 {lead['confidence']:.2f}，{lead['change']}。

## 六、法条依据

{law_lines}

## 七、处置建议

{chr(10).join(f"{idx}. {item}" for idx, item in enumerate(case['suggestions'], 1))}

## 八、数据与仿真说明

- 影像为 Sentinel-2 真实观测数据，图斑由算法自动提取，面积按像元估算，需人工复核。
- 6G 网络时延、带宽、丢包、切片利用率为数字孪生仿真值，用于演示空天地一体保障流程，不代表真实网络实测。
- 本报告不构成执法结论，仅作为线索发现与核查辅助材料。
"""


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview() -> dict[str, Any]:
    detection = _detect()
    return {
        "product": "天枢·6G 空天地一体公共安全智能体",
        "version": "0.3.0-demo",
        "scenarios": [
            {"id": key, "label": value["label"], "code": value["code"], "desc": value["desc"]}
            for key, value in SCENARIOS.items()
        ],
        "images": detection["images"],
        "detection": detection,
        "ai_modes": [
            {"id": key, "label": value["label"], "short": value["short"], "features": value["features"], "note": value["note"]}
            for key, value in AI_MODES.items()
        ],
        "sandbox": {"outage_at_s": 10, "slice_quota_pct": 100, "uav_enabled": True},
        "simulated": True,
        "disclaimer": "6G 网络指标为数字孪生仿真；影像为 Sentinel-2 真实数据；图斑需人工复核。",
    }


@router.post("/network")
def network(payload: ScenarioIn) -> dict[str, Any]:
    return _network_series(payload.scenario, payload.params())


@router.post("/demo")
def demo(payload: ScenarioIn) -> dict[str, Any]:
    result = _demo(payload.scenario, payload.params())
    _save_demo_lead(result)
    return result


@router.get("/leads/{case_no}")
def lead_status(case_no: str) -> dict[str, Any]:
    _lead_path(case_no)
    record = case_store.sixg_lead_status(case_no)
    if not record:
        return {"case_no": case_no, "converted": False}
    return {"case_no": case_no, "converted": True, "case": record}


@router.post("/leads/{case_no}/convert")
def convert_lead(case_no: str, body: LeadConvertIn, request: Request) -> dict[str, Any]:
    data = _load_demo_lead(case_no)
    if str((data.get("case") or {}).get("case_no")) != case_no:
        raise HTTPException(409, "演示线索编号与缓存不一致")
    user = case_store.session_user(request.cookies.get(case_store.COOKIE_NAME))
    try:
        actor = user or case_store.demo_actor()
    except LookupError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        result = case_store.import_sixg_lead(
            actor, data,
            assignee_id=body.assignee_id,
            due_hours=body.due_hours,
            extra_evidence=_lead_evidence(case_no),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, str(exc)) from exc
    case = result.get("case")
    ref = case or result.get("case_ref") or {}
    case_id = ref.get("id")
    evidence_count = 0
    if isinstance(case, dict) and isinstance(case.get("evidence"), list):
        evidence_count = len(case["evidence"])
    else:
        evidence_count = int(ref.get("evidence_count") or 0)
    return {
        "converted": True,
        "duplicate": bool(result.get("duplicate")),
        "case": {
            "id": case_id,
            "case_no": ref.get("case_no"),
            "title": ref.get("title"),
            "status": ref.get("status"),
            "due_at": ref.get("due_at") or result.get("due_at"),
            "assignee_name": ref.get("assignee_name"),
            "created_at": ref.get("created_at"),
            "evidence_count": evidence_count,
            "task_count": ref.get("task_count") or (1 if result.get("task") else 0),
        },
        "task": result.get("task"),
        "evidence_added": result.get("evidence_added", 0),
        "workspace_url": f"/workspace?case={case_id}" if case_id else "/workspace",
        "actor": actor.get("display_name"),
        "notice": (
            "该演示线索已转过，已为你打开正式案件。"
            if result.get("duplicate") else
            "已转正式案件：编号、期限、证据与核查任务均已写入案件库。"
        ),
    }


@router.get("/asset/{name}")
def asset(name: str) -> FileResponse:
    assets = {
        "before.png": DEMO_DIR / "before.png",
        "after.png": DEMO_DIR / "after.png",
    }
    for profile in PROFILES:
        suffix = "" if profile == "full" else "_" + profile
        assets[f"after_overlay{suffix}.png"] = OUT_DIR / f"after_overlay{suffix}.png"
        assets[f"diff_heatmap{suffix}.png"] = OUT_DIR / f"diff_heatmap{suffix}.png"
    if name not in assets:
        raise HTTPException(404, "资源不存在")
    path = assets[name]
    if not path.exists():
        stem = name.replace("after_overlay", "").replace("diff_heatmap", "").replace(".png", "").lstrip("_")
        _detect(stem or "full")
    if not path.exists():
        raise HTTPException(404, "资源尚未生成")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})
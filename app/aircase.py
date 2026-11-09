"""空中办案：卫星初筛 → 无人机同机位精查 → 变化测量意见 → 可信取证链。

本模块是新增能力，不改动既有案件、六 G、无人机航线模块：
- 复用 app.drone 的航线规划与 DEM 高程，但任务独立存放在 data/aircase；
- 复用 app.case_store 的证据 HMAC 密钥，不写入案件库；
- 演示影像以 data/sixg_demo/before.png 为基线，在同一机位生成“模拟复拍时相”，
  用于打通“发现—复核—量算—出件—存证”的完整链路，所有产物均标注演示口径。
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import math
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app import case_store, config, drone, legal

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
BASE = Path(config.DATA) / "aircase"
DEMO = BASE / "demo"
SORTIES = BASE / "sorties"
OUT = BASE / "out"
AUDIT = BASE / "audit.jsonl"

# 演示 AOI：约 4.1 km × 2.1 km，像素约 4 m；与样例影像中的矿区尺度一致。
DEMO_BBOX = (109.0750, 41.9500, 109.1250, 41.9686)
IMG_W, IMG_H = 1024, 512
DEMO_VERSION = "v1"
GIMBAL_PITCH = -90.0

_LOCK = threading.RLock()
_AUDIT_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None, "value": None}

DISCLAIMER = (
    "本页为演示件：影像为样例底图与同机位模拟复拍，面积按像元估算，"
    "不构成鉴定意见、测绘成果或执法依据。"
)
EVIDENCE_NOTE = "签名校验只证明清单与文件未被改动，不证明拍摄行为真实；仍需飞行日志、像控与现场笔录互证。"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _lon0() -> float:
    return DEMO_BBOX[0]


def _lat1() -> float:
    return DEMO_BBOX[3]


def _lon_span() -> float:
    return DEMO_BBOX[2] - DEMO_BBOX[0]


def _lat_span() -> float:
    return DEMO_BBOX[3] - DEMO_BBOX[1]


def _px_to_lon(x: float) -> float:
    return _lon0() + x / IMG_W * _lon_span()


def _px_to_lat(y: float) -> float:
    return _lat1() - y / IMG_H * _lat_span()


def _lonlat_to_px(lon: float, lat: float) -> tuple[float, float]:
    return (lon - _lon0()) / _lon_span() * IMG_W, (_lat1() - lat) / _lat_span() * IMG_H


def _lonlat_box(box: list[float]) -> list[float]:
    x0, y0, x1, y1 = box
    return [round(_px_to_lon(x0), 6), round(_px_to_lat(y1), 6), round(_px_to_lon(x1), 6), round(_px_to_lat(y0), 6)]


def _box_area_ha(box: list[float]) -> float:
    x0, y0, x1, y1 = box
    lon_a, lon_b = _px_to_lon(x0), _px_to_lon(x1)
    lat_a, lat_b = _px_to_lat(y1), _px_to_lat(y0)
    mid = (lat_a + lat_b) / 2
    width_m = _haversine_m(lon_a, mid, lon_b, mid)
    height_m = _haversine_m(lon_a, lat_a, lon_a, lat_b)
    return width_m * height_m / 10000.0


def _square_gsd_m(box: list[float], width_px: int, height_px: int) -> float:
    return math.sqrt(_box_area_ha(box) * 10000.0 / max(1, width_px * height_px))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root_hash(files: list[dict[str, Any]]) -> str:
    rows = [{"name": row["name"], "bytes": row["bytes"], "sha256": row["sha256"]} for row in files]
    rows.sort(key=lambda row: row["name"])
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(payload.encode("utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"] if bold else [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyhbd.ttc",
    ]
    for item in candidates:
        try:
            return ImageFont.truetype(item, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _audit(action: str, sortie_id: str | None, detail: dict[str, Any] | None = None) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    row = {
        "at": now_iso(),
        "action": action,
        "sortie": sortie_id,
        "actor": "aircase-demo",
        "detail": detail or {},
    }
    with _AUDIT_LOCK:
        with AUDIT.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 演示影像：真实基线 + 同机位模拟复拍
# ---------------------------------------------------------------------------
def _simulate_after(base: Image.Image) -> Image.Image:
    """以基线影像为纹理来源，在真实矿区边缘生成一处新增采挖/堆料变化。

    变化位置、道路、料堆、工棚均为固定种子的演示合成，目的在于让同机位比对
    有可量算的真实差异，不冒充另一时相的真实无人机照片。
    """
    width, height = base.size
    rng = __import__("random").Random(20261111)
    sources = [(380, 350, 720, 512), (600, 300, 760, 420), (300, 430, 520, 512)]

    def tex_tile(tone: tuple[int, int, int], contrast: float = 1.0) -> Image.Image:
        canvas = Image.new("RGB", (width, height))
        tile = 26
        for y in range(0, height, tile):
            for x in range(0, width, tile):
                sx0, sy0, sx1, sy1 = rng.choice(sources)
                tw = rng.randint(22, 40)
                th = rng.randint(18, 34)
                px = rng.randint(sx0, max(sx0, sx1 - tw))
                py = rng.randint(sy0, max(sy0, sy1 - th))
                piece = base.crop((px, py, px + tw, py + th)).resize((tile, tile), Image.Resampling.LANCZOS)
                if rng.random() < 0.5:
                    piece = piece.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                canvas.paste(piece, (x, y))
        canvas = canvas.filter(ImageFilter.GaussianBlur(0.5))
        array = np.asarray(canvas).astype(np.float32)
        mean = array.reshape(-1, 3).mean(axis=0)
        array = (array - mean) * contrast + np.array(tone, dtype=np.float32)
        return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))

    def poly(px: float, py: float, rx: float, ry: float, n: int = 18, jitter: float = 0.16, phase: float = 0.0) -> list[tuple[float, float]]:
        points = []
        for index in range(n):
            angle = 2 * math.pi * index / n + phase
            radius = 1 + rng.uniform(-jitter, jitter)
            points.append((px + math.cos(angle) * rx * radius, py + math.sin(angle) * ry * radius))
        return points

    def mask_of(points: list[tuple[float, float]], blur: float = 1.2) -> Image.Image:
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).polygon(points, fill=255)
        return mask.filter(ImageFilter.GaussianBlur(blur))

    after = ImageEnhance.Color(base).enhance(1.04)
    after = ImageEnhance.Brightness(after).enhance(1.01)
    changed = Image.new("L", (width, height), 0)

    def paint(points: list[tuple[float, float]], tone: tuple[int, int, int], contrast: float = 1.0, blur: float = 1.2, alpha: int = 255) -> Image.Image:
        nonlocal after
        mask = mask_of(points, blur)
        if alpha < 255:
            mask = mask.point(lambda value: int(value * alpha / 255))
        texture = tex_tile(tone, contrast)
        after = Image.composite(texture, after, mask)
        return mask

    # 主采坑向北扩展，贴着已有亮色矿区，避免“凭空出现”。
    main_points = poly(452, 340, 92, 52, 20, 0.14, 0.5)
    shadow = mask_of([(x + 7, y + 9) for x, y in main_points], 6)
    after = Image.composite(Image.new("RGB", (width, height), (45, 38, 30)), after, shadow.point(lambda value: int(value * 0.38)))
    changed = ImageChops.lighter(changed, paint(main_points, (226, 219, 204), 1.12, 1.6))
    for scale, tone in ((0.72, (214, 206, 190)), (0.50, (238, 232, 218))):
        inner = poly(452, 342, 92 * scale, 52 * scale, 18, 0.10, 0.5 + scale)
        changed = ImageChops.lighter(changed, paint(inner, tone, 1.04, 1.0))

    # 东侧新料场/裸地。
    east_points = poly(676, 378, 54, 34, 18, 0.18, 1.3)
    shadow = mask_of([(x + 5, y + 7) for x, y in east_points], 5)
    after = Image.composite(Image.new("RGB", (width, height), (50, 42, 34)), after, shadow.point(lambda value: int(value * 0.32)))
    changed = ImageChops.lighter(changed, paint(east_points, (219, 208, 189), 1.06, 1.4))

    # 两条矿区内部道路，不跨区，避免把三处变化连成一个连通域。
    for road in (
        [(365, 388), (405, 365), (452, 340), (505, 328)],
        [(630, 362), (660, 372), (700, 396), (726, 414)],
    ):
        road_mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(road_mask).line(road, fill=255, width=9, joint="curve")
        road_mask = road_mask.filter(ImageFilter.GaussianBlur(1.0))
        after = Image.composite(tex_tile((220, 207, 184), 1.06), after, road_mask)
        road_draw = ImageDraw.Draw(after)
        road_draw.line([(x, y + 1) for x, y in road], fill=(158, 143, 120), width=2, joint="curve")
        road_draw.line([(x, y - 1) for x, y in road], fill=(236, 226, 208), width=1, joint="curve")
        changed = ImageChops.lighter(changed, road_mask)

    # 西侧取土点。
    west_points = poly(300, 388, 30, 21, 16, 0.22, 2.2)
    changed = ImageChops.lighter(changed, paint(west_points, (214, 198, 172), 1.02, 1.6, 230))

    # 工棚/设备，提供尺度参照。
    shadow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    top_draw = ImageDraw.Draw(after)
    for _ in range(12):
        x = rng.uniform(400, 700)
        y = rng.uniform(330, 400)
        w = rng.uniform(3, 6)
        h = rng.uniform(2, 4)
        shadow_draw.rectangle([x + 2, y + 3, x + w + 2, y + h + 3], fill=(25, 20, 16, 100))
        top_draw.rectangle([x, y, x + w, y + h], fill=(231, 228, 218))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(1.8))
    after = Image.alpha_composite(after.convert("RGBA"), shadow_layer).convert("RGB")

    # 只在变化区加细噪声，保留原影像纹理。
    array = np.asarray(after).astype(np.float32)
    noise = np.random.default_rng(11).normal(0, 3.5, (height, width, 1))
    weight = (np.asarray(changed).astype(np.float32) / 255.0)[:, :, None]
    array = np.clip(array + noise * weight, 0, 255).astype(np.uint8)
    return Image.fromarray(array)


def ensure_demo() -> dict[str, Any]:
    """生成/复用演示影像对，返回元数据。首次约 3-5 秒，之后直接读缓存。"""
    source = Path(config.DATA) / "sixg_demo" / "before.png"
    if not source.is_file():
        raise FileNotFoundError("缺少样例影像 data/sixg_demo/before.png")
    source_digest = _sha256_file(source)[:16]
    meta_path = DEMO / "meta.json"
    with _LOCK:
        if meta_path.is_file() and (DEMO / "before.png").is_file() and (DEMO / "after.png").is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            if meta.get("source_sha16") == source_digest and meta.get("version") == DEMO_VERSION:
                return meta

        DEMO.mkdir(parents=True, exist_ok=True)
        before = Image.open(source).convert("RGB")
        if before.size != (IMG_W, IMG_H):
            before = before.resize((IMG_W, IMG_H), Image.Resampling.LANCZOS)
        before.save(DEMO / "before.png")
        after = _simulate_after(before)
        after.save(DEMO / "after.png")
        meta = {
            "version": DEMO_VERSION,
            "source_sha16": source_digest,
            "generated_at": now_iso(),
            "bbox": list(DEMO_BBOX),
            "size": [IMG_W, IMG_H],
            "baseline": {"date": "2023-10-08", "sensor": "Sentinel-2 L2A 样例"},
            "after": {"date": "模拟复拍", "note": "同一机位、同一范围，由基线影像演化生成"},
            "note": "用于演示同机位复拍、变化检测、面积量算与出件链路，不是真实无人机照片。",
        }
        _write_json(meta_path, meta)
        _CACHE["key"] = None
        _audit("demo_generated", None, {"version": DEMO_VERSION, "source_sha16": source_digest})
        return meta


# ---------------------------------------------------------------------------
# 变化检测（与 sixg 演示同源，独立实现，避免耦合）
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
        row = mask[y]
        for x in range(width):
            if not row[x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            pixels: list[tuple[int, int]] = []
            while stack:
                cy, cx = stack.pop()
                pixels.append((cy, cx))
                if cy + 1 < height and mask[cy + 1, cx] and not seen[cy + 1, cx]:
                    seen[cy + 1, cx] = True
                    stack.append((cy + 1, cx))
                if cy - 1 >= 0 and mask[cy - 1, cx] and not seen[cy - 1, cx]:
                    seen[cy - 1, cx] = True
                    stack.append((cy - 1, cx))
                if cx + 1 < width and mask[cy, cx + 1] and not seen[cy, cx + 1]:
                    seen[cy, cx + 1] = True
                    stack.append((cy, cx + 1))
                if cx - 1 >= 0 and mask[cy, cx - 1] and not seen[cy, cx - 1]:
                    seen[cy, cx - 1] = True
                    stack.append((cy, cx - 1))
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


def _detect_arrays(
    before: np.ndarray,
    after: np.ndarray,
    *,
    min_px: int,
    suppress: float,
    area_ha_per_px: float,
    quantile: float,
) -> dict[str, Any]:
    luminance1, exg1, texture1 = _features(before)
    luminance2, exg2, texture2 = _features(after)
    diff_rgb = _blur(np.sqrt(((after - before) ** 2).mean(axis=2)), 1)
    diff_exg = _blur(np.abs(exg2 - exg1), 1)
    diff_texture = _blur(np.abs(texture2 - texture1), 1)
    highpass1 = luminance1 - _blur(luminance1, 8)
    highpass2 = luminance2 - _blur(luminance2, 8)
    diff_highpass = _blur(np.abs(highpass2 - highpass1), 1)
    score = (
        0.45 * _normalize(diff_texture)
        + 0.25 * _normalize(diff_highpass)
        + 0.20 * _normalize(diff_rgb)
        + 0.10 * _normalize(diff_exg)
    )
    threshold = max(float(np.quantile(score, quantile)), 0.10)
    mask = _dilate(_erode(score >= threshold, 1), 2)
    candidates = [pixels for pixels in _components(mask) if len(pixels) >= min_px]

    rows: list[dict[str, Any]] = []
    suppressed = 0
    for pixels in candidates:
        ys = [item[0] for item in pixels]
        xs = [item[1] for item in pixels]
        index = np.asarray(pixels)
        delta_lum = float((luminance2 - luminance1)[index[:, 0], index[:, 1]].mean())
        if delta_lum <= suppress:
            suppressed += 1
            continue
        delta_exg = float((exg2 - exg1)[index[:, 0], index[:, 1]].mean())
        mean_score = float(score[index[:, 0], index[:, 1]].mean())
        area_ha = len(pixels) * area_ha_per_px
        change_class = "疑似采矿/取土" if (area_ha >= 1.0 and delta_lum >= 0.05) else "裸土/建设扰动"
        confidence = (
            0.62
            + 0.22 * min(1.0, (delta_lum - suppress) / 0.15)
            + 0.10 * min(1.0, len(pixels) / 2000)
            + 0.06 * min(1.0, mean_score / 0.8)
        )
        rows.append(
            {
                "bbox": [int(min(xs)), int(min(ys)), int(max(xs) - min(xs) + 1), int(max(ys) - min(ys) + 1)],
                "area_ha": round(area_ha, 2),
                "confidence": round(_clamp(confidence, 0.60, 0.96), 2),
                "class": change_class,
                "score": round(mean_score, 3),
                "delta_lum": round(delta_lum, 3),
                "delta_exg": round(delta_exg, 3),
                "_pixels": pixels,
            }
        )

    rows.sort(key=lambda item: (item["area_ha"], item["confidence"]), reverse=True)
    return {
        "rows": rows,
        "score": score,
        "mask": mask,
        "threshold": round(threshold, 3),
        "suppressed": suppressed,
        "candidate_count": len(candidates),
    }


def _detect_demo() -> dict[str, Any]:
    meta = ensure_demo()
    before_path = DEMO / "before.png"
    after_path = DEMO / "after.png"
    key = (_sha256_file(before_path)[:16], _sha256_file(after_path)[:16], meta.get("version"))
    with _LOCK:
        if _CACHE["key"] == key and _CACHE["value"] is not None:
            return _CACHE["value"]

    before = np.asarray(Image.open(before_path).convert("RGB"), dtype=np.float32) / 255.0
    after = np.asarray(Image.open(after_path).convert("RGB"), dtype=np.float32) / 255.0
    area_per_px = _box_area_ha([0, 0, IMG_W, IMG_H]) / (IMG_W * IMG_H)
    detection = _detect_arrays(
        before,
        after,
        min_px=150,
        suppress=0.015,
        area_ha_per_px=area_per_px,
        quantile=0.93,
    )
    patches = []
    for index, row in enumerate(detection["rows"], 1):
        x, y, w, h = row["bbox"]
        center_lon = _px_to_lon(x + w / 2)
        center_lat = _px_to_lat(y + h / 2)
        patches.append(
            {
                "id": f"P-{index:02d}",
                "bbox": row["bbox"],
                "area_ha": row["area_ha"],
                "confidence": row["confidence"],
                "class": row["class"],
                "score": row["score"],
                "delta_lum": row["delta_lum"],
                "delta_exg": row["delta_exg"],
                "change": f"亮度 {row['delta_lum']:+.3f} / 植被 {row['delta_exg']:+.3f}",
                "center_lonlat": [round(center_lon, 6), round(center_lat, 6)],
                "bbox_lonlat": _lonlat_box([x, y, x + w, y + h]),
            }
        )
    patches.sort(key=lambda item: item["area_ha"], reverse=True)
    for index, patch in enumerate(patches, 1):
        patch["id"] = f"P-{index:02d}"

    value = {
        "generated_at": now_iso(),
        "images": {
            "before": {"date": "2023-10-08", "sensor": "Sentinel-2 L2A 样例", "note": "真实基线影像"},
            "after": {"date": "模拟复拍", "sensor": "同机位模拟时相", "note": "用于演示，不是真实无人机照片"},
        },
        "bbox": list(DEMO_BBOX),
        "size": [IMG_W, IMG_H],
        "patches": patches,
        "metrics": {
            "patches": len(patches),
            "total_area_ha": round(sum(item["area_ha"] for item in patches), 2),
            "mean_confidence": round(sum(item["confidence"] for item in patches) / max(1, len(patches)), 2),
            "high_confidence": sum(1 for item in patches if item["confidence"] >= 0.8),
            "threshold": detection["threshold"],
            "suppressed": detection["suppressed"],
            "gsd_m": round(_square_gsd_m([0, 0, IMG_W, IMG_H], IMG_W, IMG_H), 2),
        },
        "method": "多特征融合（纹理/高通/色彩/植被指数）+ 光照不变抑制 + 形态学后处理",
        "disclaimer": DISCLAIMER,
    }
    with _LOCK:
        _CACHE["key"] = key
        _CACHE["value"] = value
    return value


def _patch_by_id(patch_id: str) -> dict[str, Any]:
    data = _detect_demo()
    for patch in data["patches"]:
        if patch["id"].upper() == patch_id.strip().upper():
            return patch
    raise KeyError(f"未找到图斑 {patch_id}")


# ---------------------------------------------------------------------------
# 任务：航线、裁剪、同机位签名
# ---------------------------------------------------------------------------
def _new_sortie_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"AC-{stamp}-{secrets.token_hex(2).upper()}"


def _route_size(patch: dict[str, Any], mode: str) -> float:
    area_km2 = max(0.01, float(patch["area_ha"]) / 100.0)
    if mode == "scene":
        return round(_clamp(2 * math.sqrt(area_km2) + 0.5, 0.8, 1.6), 2)
    return round(_clamp(2 * math.sqrt(area_km2) + 0.3, 0.5, 1.2), 2)


def _plan_route(patch: dict[str, Any], mode: str, rel_height_m: float) -> dict[str, Any]:
    lon, lat = patch["center_lonlat"]
    size_km = _route_size(patch, mode)
    pattern = "perimeter" if mode == "scene" else "lawnmower"
    heading = 0.0 if mode == "scene" else 35.0
    return drone.plan_drone_route(
        lon,
        lat,
        pattern=pattern,
        size_km=size_km,
        spacing_m=80.0,
        rel_height_m=float(rel_height_m),
        speed_mps=10.0,
        direction_deg=heading,
        sample_m=60.0,
        title="现场方位图航线" if mode == "scene" else "同机位精查航线",
    )


def _pose_signature(route: dict[str, Any], rel_height_m: float, heading_deg: float) -> str:
    payload = {
        "waypoints": [
            [round(point["lon"], 6), round(point["lat"], 6), round(point["alt_m"], 1)]
            for point in route["waypoints"]
        ],
        "rel_height_m": round(float(rel_height_m), 1),
        "gimbal_pitch": GIMBAL_PITCH,
        "heading_deg": round(float(heading_deg), 1),
        "spacing_m": route.get("spacing_m"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(raw.encode("utf-8"))


def _pose_offset_m(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_points = left["waypoints"]
    right_points = right["waypoints"]
    if len(left_points) != len(right_points):
        return 9999.0
    worst = 0.0
    for a, b in zip(left_points, right_points):
        worst = max(worst, _haversine_m(a["lon"], a["lat"], b["lon"], b["lat"]))
    return round(worst, 2)


def _crop_box(patch: dict[str, Any], mode: str) -> list[int]:
    x, y, w, h = patch["bbox"]
    pad = (0.55 * max(w, h) + 24) if mode == "precise" else (1.35 * max(w, h) + 50)
    pad = int(round(pad))
    return [
        max(0, int(x) - pad),
        max(0, int(y) - pad),
        min(IMG_W, int(x + w) + pad),
        min(IMG_H, int(y + h) + pad),
    ]


def _crop_pair(before: Image.Image, after: Image.Image, box: list[int], target_w: int) -> tuple[Image.Image, Image.Image]:
    x0, y0, x1, y1 = box
    left = before.crop((x0, y0, x1, y1))
    right = after.crop((x0, y0, x1, y1))
    scale = target_w / max(1, left.width)
    size = (target_w, max(1, int(round(left.height * scale))))
    left = left.resize(size, Image.Resampling.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1.6, percent=95, threshold=3))
    right = right.resize(size, Image.Resampling.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1.4, percent=85, threshold=3))
    left = ImageEnhance.Contrast(left).enhance(1.03)
    right = ImageEnhance.Contrast(right).enhance(1.03)
    return left, right


def create_sortie(patch_id: str, mode: str = "precise", rel_height_m: float = 120.0) -> dict[str, Any]:
    if mode not in {"precise", "scene"}:
        raise ValueError("mode 只能是 precise 或 scene")
    rel_height_m = _clamp(float(rel_height_m), 40.0, 300.0)
    with _LOCK:
        patch = _patch_by_id(patch_id)
        sortie_id = _new_sortie_id()
        out_dir = OUT / sortie_id
        out_dir.mkdir(parents=True, exist_ok=True)

        route = _plan_route(patch, mode, rel_height_m)
        repeat = _plan_route(patch, mode, rel_height_m)
        heading = float(route.get("direction_deg") or 0.0)
        signature = _pose_signature(route, rel_height_m, heading)
        repeat_signature = _pose_signature(repeat, rel_height_m, heading)
        pose_match = signature == repeat_signature
        offset = _pose_offset_m(route, repeat)

        crop = _crop_box(patch, mode)
        before_full = Image.open(DEMO / "before.png").convert("RGB")
        after_full = Image.open(DEMO / "after.png").convert("RGB")
        before_crop, after_crop = _crop_pair(before_full, after_full, crop, 720 if mode == "precise" else 900)
        before_crop.save(out_dir / "before_uav.png")
        after_crop.save(out_dir / "after_uav.png")
        (out_dir / "route.kml").write_text(route["kml"], encoding="utf-8")
        (out_dir / "route.csv").write_text(route["csv"], encoding="utf-8")
        _write_json(out_dir / "route.geojson", route["geojson"])

        sortie = {
            "id": sortie_id,
            "patch_id": patch["id"],
            "mode": mode,
            "status": "ready",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "patch": patch,
            "route": {
                "pattern": route.get("pattern"),
                "size_km": route.get("size_km"),
                "spacing_m": route.get("spacing_m"),
                "rel_height_m": round(rel_height_m, 1),
                "speed_mps": route.get("speed_mps"),
                "gimbal_pitch": GIMBAL_PITCH,
                "heading_deg": heading,
                "route_signature": signature,
                "repeat_signature": repeat_signature,
                "pose_match": pose_match,
                "pose_max_offset_m": offset,
                "stats": route.get("stats"),
                "center": route.get("center"),
                "degraded": route.get("degraded"),
                "reason": route.get("reason"),
                "waypoints": route.get("waypoints"),
            },
            "crop": {
                "box": crop,
                "lonlat": _lonlat_box(crop),
                "area_ha": round(_box_area_ha(crop), 2),
                "size": [before_crop.width, before_crop.height],
                "gsd_m": round(_square_gsd_m(crop, crop[2] - crop[0], crop[3] - crop[1]), 2),
            },
            "files": {
                "before": "before_uav.png",
                "after": "after_uav.png",
                "route_kml": "route.kml",
                "route_csv": "route.csv",
            },
            "results": None,
            "evidence": None,
            "disclaimer": DISCLAIMER,
        }
        _write_json(SORTIES / f"{sortie_id}.json", sortie)
        _audit(
            "sortie_created",
            sortie_id,
            {"patch": patch["id"], "mode": mode, "pose_match": pose_match, "waypoints": len(route["waypoints"])},
        )
        return sortie


def _load_sortie(sortie_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"AC-[0-9]{8}-[0-9]{6}-[0-9A-F]{4}", sortie_id.upper()):
        raise KeyError("任务编号格式不正确")
    path = SORTIES / f"{sortie_id}.json"
    if not path.is_file():
        raise KeyError(f"未找到任务 {sortie_id}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 渲染：差异图、热力图、现场方位图
# ---------------------------------------------------------------------------
def _heat_color(value: float) -> tuple[int, int, int]:
    stops = [
        (0.00, (5, 15, 25)),
        (0.35, (22, 82, 140)),
        (0.60, (40, 201, 202)),
        (0.80, (255, 190, 70)),
        (1.00, (255, 90, 70)),
    ]
    value = _clamp(value, 0.0, 1.0)
    for index in range(1, len(stops)):
        left, right = stops[index - 1], stops[index]
        if value <= right[0]:
            ratio = (value - left[0]) / max(1e-6, right[0] - left[0])
            return tuple(int(left[1][i] + (right[1][i] - left[1][i]) * ratio) for i in range(3))
    return stops[-1][1]


def _render_overlay(after_image: Image.Image, mask: np.ndarray, main_bbox: list[int], path: Path, label: str) -> None:
    array = np.asarray(after_image).astype(np.float32)
    changed = mask.astype(bool)
    if changed.any():
        tint = np.array([255, 176, 64], dtype=np.float32)
        array[changed] = array[changed] * 0.32 + tint * 0.68
        edge = _dilate(changed, 2) & ~changed
        array[edge] = np.array([255, 236, 180], dtype=np.float32)
    canvas = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    x, y, w, h = main_bbox
    draw.rectangle([x, y, x + w, y + h], outline=(255, 190, 80, 255), width=2)
    font = _font(13, True)
    text = label
    tw, th = _text_size(draw, text, font)
    bx, by = min(x, canvas.width - tw - 16), max(6, y - th - 10)
    draw.rectangle([bx, by, bx + tw + 12, by + th + 8], fill=(5, 15, 25, 210), outline=(255, 190, 80, 180))
    draw.text((bx + 6, by + 3), text, font=font, fill=(255, 226, 178))
    canvas.save(path)


def _render_heatmap(score: np.ndarray, main_bbox: list[int], path: Path) -> None:
    normalized = _normalize(score)
    height, width = normalized.shape
    flat = normalized.reshape(-1)
    rgb = np.zeros((flat.size, 3), dtype=np.uint8)
    for index, value in enumerate(flat):
        rgb[index] = _heat_color(float(value))
    canvas = Image.fromarray(rgb.reshape(height, width, 3))
    canvas = canvas.filter(ImageFilter.GaussianBlur(0.8))
    draw = ImageDraw.Draw(canvas, "RGBA")
    x, y, w, h = main_bbox
    draw.rectangle([x, y, x + w, y + h], outline=(255, 255, 255, 200), width=1)
    canvas.save(path)


def _map_box(patch: dict[str, Any], mode: str) -> list[int]:
    x, y, w, h = patch["bbox"]
    pad = (2.0 * max(w, h) + 80) if mode == "precise" else (3.0 * max(w, h) + 120)
    pad = int(round(pad))
    return [
        max(0, int(x) - pad),
        max(0, int(y) - pad),
        min(IMG_W, int(x + w) + pad),
        min(IMG_H, int(y + h) + pad),
    ]


def _render_scene_map(sortie: dict[str, Any], result: dict[str, Any], path: Path) -> None:
    patch = sortie["patch"]
    box = _map_box(patch, sortie["mode"])
    source = Image.open(DEMO / "after.png").convert("RGB").crop(tuple(box))
    target_w = 960
    source = source.resize((target_w, max(1, int(source.height * target_w / source.width))), Image.Resampling.LANCZOS)
    source = source.filter(ImageFilter.UnsharpMask(radius=1.2, percent=70, threshold=3))
    canvas = source.convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    width, height = canvas.size
    x0, y0, x1, y1 = box
    lon_left, lon_right = _px_to_lon(x0), _px_to_lon(x1)
    lat_top, lat_bottom = _px_to_lat(y0), _px_to_lat(y1)
    span_m = _haversine_m(lon_left, (lat_top + lat_bottom) / 2, lon_right, (lat_top + lat_bottom) / 2)
    px_per_m = width / max(1.0, span_m)

    def map_xy(px: float, py: float) -> tuple[float, float]:
        return (px - x0) / max(1, x1 - x0) * width, (py - y0) / max(1, y1 - y0) * height

    # 300 m 网格
    step_m = 300
    step_px = max(46, int(round(step_m * px_per_m)))
    for gx in range(0, width, step_px):
        draw.line([(gx, 0), (gx, height)], fill=(120, 200, 230, 18), width=1)
    for gy in range(0, height, step_px):
        draw.line([(0, gy), (width, gy)], fill=(120, 200, 230, 18), width=1)

    # 精查范围
    crop = sortie["crop"]["box"]
    cx0, cy0 = map_xy(crop[0], crop[1])
    cx1, cy1 = map_xy(crop[2], crop[3])
    draw.rectangle([cx0, cy0, cx1, cy1], outline=(56, 225, 255, 220), width=2)
    if cy0 > 118:
        crop_label = "精查范围"
        crop_font = _font(12, True)
        crop_tw, crop_th = _text_size(draw, crop_label, crop_font)
        draw.rectangle([cx0 + 4, cy0 + 4, cx0 + crop_tw + 14, cy0 + crop_th + 12], fill=(5, 15, 25, 190))
        draw.text((cx0 + 8, cy0 + 7), crop_label, font=crop_font, fill=(180, 243, 255))

    # 全部候选图斑
    all_patches = _detect_demo()["patches"]
    for item in all_patches:
        bx, by, bw, bh = item["bbox"]
        if bx + bw < x0 or bx > x1 or by + bh < y0 or by > y1:
            continue
        mx0, my0 = map_xy(bx, by)
        mx1, my1 = map_xy(bx + bw, by + bh)
        color = (255, 190, 80, 230) if item["id"] != patch["id"] else (255, 120, 90, 240)
        draw.rectangle([mx0, my0, mx1, my1], outline=color, width=2)
        label = f"{item['id']} {item['area_ha']:.1f} ha"
        font = _font(12, True)
        tw, th = _text_size(draw, label, font)
        draw.rectangle([mx0, max(0, my0 - th - 5), mx0 + tw + 8, my0], fill=(5, 15, 25, 205))
        draw.text((mx0 + 4, max(0, my0 - th - 4)), label, font=font, fill=(255, 226, 178))

    # 图斑中心
    mx, my = map_xy(patch["bbox"][0] + patch["bbox"][2] / 2, patch["bbox"][1] + patch["bbox"][3] / 2)
    draw.line([(mx - 14, my), (mx + 14, my)], fill=(5, 15, 25, 200), width=5)
    draw.line([(mx, my - 14), (mx, my + 14)], fill=(5, 15, 25, 200), width=5)
    draw.line([(mx - 14, my), (mx + 14, my)], fill=(56, 225, 255, 255), width=2)
    draw.line([(mx, my - 14), (mx, my + 14)], fill=(56, 225, 255, 255), width=2)
    draw.ellipse([mx - 4, my - 4, mx + 4, my + 4], outline=(56, 225, 255, 255), width=2)
    center_label = "图斑中心"
    center_font = _font(12, True)
    center_tw, center_th = _text_size(draw, center_label, center_font)
    draw.rectangle([mx + 8, my + 7, mx + center_tw + 18, my + center_th + 15], fill=(5, 15, 25, 185))
    draw.text((mx + 12, my + 10), center_label, font=center_font, fill=(190, 245, 255))

    # 标题块
    panel = [18, 18, 546, 108]
    draw.rectangle(panel, fill=(5, 15, 25, 218), outline=(56, 225, 255, 170), width=1)
    draw.rectangle([18, 18, 22, 108], fill=(56, 225, 255, 220))
    draw.text((34, 26), "现场方位图（演示）", font=_font(24, True), fill=(230, 247, 255))
    draw.text(
        (34, 60),
        f"{patch['id']} · {patch['class']} · {result['changed_area_ha']:.1f} 公顷",
        font=_font(15, True),
        fill=(255, 206, 128),
    )
    draw.text(
        (34, 84),
        f"{sortie['id']} · {sortie['created_at'][:10]} · 底图：2023 基线 / 模拟复拍",
        font=_font(11),
        fill=(150, 190, 215),
    )

    # 指北针
    nx, ny = width - 54, 54
    draw.ellipse([nx - 28, ny - 28, nx + 28, ny + 28], fill=(5, 15, 25, 190), outline=(170, 220, 240, 150))
    draw.polygon([(nx, ny - 20), (nx - 8, ny + 8), (nx, ny + 2), (nx + 8, ny + 8)], fill=(255, 190, 80, 240))
    draw.text((nx - 5, ny + 12), "N", font=_font(12, True), fill=(240, 250, 255))

    # 比例尺
    candidates = [50, 100, 200, 250, 500, 1000, 2000]
    scale_m = min(candidates, key=lambda value: abs(value * px_per_m - 120))
    bar_w = scale_m * px_per_m
    bx, by = 24, height - 66
    draw.rectangle([bx - 8, by - 24, bx + bar_w + 70, by + 26], fill=(5, 15, 25, 200))
    draw.rectangle([bx, by, bx + bar_w / 2, by + 8], fill=(240, 246, 250))
    draw.rectangle([bx + bar_w / 2, by, bx + bar_w, by + 8], fill=(20, 40, 58))
    draw.rectangle([bx, by, bx + bar_w, by + 8], outline=(240, 246, 250), width=1)
    draw.text((bx, by - 20), f"0", font=_font(10), fill=(210, 228, 240))
    draw.text((bx + bar_w / 2 - 12, by - 20), f"{scale_m / 2:.0f}", font=_font(10), fill=(210, 228, 240))
    draw.text((bx + bar_w - 16, by - 20), f"{scale_m} m", font=_font(10), fill=(210, 228, 240))

    # 坐标角标
    coord_font = _font(10)
    draw.text((10, height - 18), f"{lon_left:.5f}°E", font=coord_font, fill=(170, 205, 225))
    draw.text((width - 96, height - 18), f"{lon_right:.5f}°E", font=coord_font, fill=(170, 205, 225))
    draw.text((10, 118), f"{lat_top:.5f}°N", font=coord_font, fill=(170, 205, 225))
    draw.text((10, height - 36), f"{lat_bottom:.5f}°N", font=coord_font, fill=(170, 205, 225))

    # 图例
    legend = [width - 268, height - 126, width - 18, height - 44]
    draw.rectangle(legend, fill=(5, 15, 25, 210), outline=(70, 110, 140, 150))
    draw.text((legend[0] + 10, legend[1] + 7), "图例", font=_font(12, True), fill=(210, 235, 250))
    draw.rectangle([legend[0] + 12, legend[1] + 30, legend[0] + 34, legend[1] + 40], outline=(255, 190, 80), width=2)
    draw.text((legend[0] + 42, legend[1] + 28), "候选图斑", font=_font(11), fill=(190, 215, 230))
    draw.line([(legend[0] + 12, legend[1] + 56), (legend[0] + 34, legend[1] + 56)], fill=(56, 225, 255), width=2)
    draw.text((legend[0] + 42, legend[1] + 49), "图斑中心 / 精查范围", font=_font(11), fill=(190, 215, 230))
    draw.text((legend[0] + 12, legend[1] + 66), "示意图，非测绘成果", font=_font(10), fill=(140, 175, 195))

    # 页脚
    footer = "天枢·空中办案 · 样例影像生成 · 非测绘成果 · " + sortie["created_at"][:10]
    font = _font(10)
    tw, _ = _text_size(draw, footer, font)
    draw.rectangle([0, height - 26, width, height], fill=(5, 15, 25, 180))
    draw.text(((width - tw) / 2, height - 20), footer, font=font, fill=(135, 170, 190))

    canvas.convert("RGB").save(path)


# ---------------------------------------------------------------------------
# 测量意见
# ---------------------------------------------------------------------------
def _crop_px_to_lonlat(crop: list[int], bbox: list[int]) -> list[float]:
    x0, y0 = crop[0], crop[1]
    x, y, w, h = bbox
    return _lonlat_box([x0 + x, y0 + y, x0 + x + w, y0 + y + h])


def _build_result(sortie: dict[str, Any], detection: dict[str, Any]) -> dict[str, Any]:
    crop = sortie["crop"]
    crop_box = crop["box"]
    width, height = crop["size"]
    area_per_px = crop["area_ha"] / max(1, width * height)
    accepted = []
    combined = np.zeros((height, width), dtype=bool)
    for row in detection["rows"]:
        x, y, w, h = row["bbox"]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + w), min(height, y + h)
        if x1 <= x0 or y1 <= y0:
            continue
        combined[y0:y1, x0:x1] = True
        accepted.append(
            {
                "bbox_px": row["bbox"],
                "bbox_lonlat": _crop_px_to_lonlat(crop_box, row["bbox"]),
                "area_ha": row["area_ha"],
                "class": row["class"],
                "confidence": row["confidence"],
                "delta_lum": row["delta_lum"],
                "delta_exg": row["delta_exg"],
                "score": row["score"],
            }
        )
    # 用连通域的实际像元，而不是外接矩形，避免面积被矩形放大。
    if detection["rows"]:
        exact = np.zeros((height, width), dtype=bool)
        for row in detection["rows"]:
            for py, px in row["_pixels"]:
                if 0 <= py < height and 0 <= px < width:
                    exact[py, px] = True
        combined = exact
        changed_px = int(exact.sum())
    else:
        changed_px = 0

    changed_area = round(changed_px * area_per_px, 2)
    if accepted:
        main = accepted[0]
        confidence = main["confidence"]
        change_class = main["class"]
        delta_lum = sum(item["delta_lum"] * item["area_ha"] for item in accepted) / max(1e-6, sum(item["area_ha"] for item in accepted))
        delta_exg = sum(item["delta_exg"] * item["area_ha"] for item in accepted) / max(1e-6, sum(item["area_ha"] for item in accepted))
        mean_score = sum(item["score"] * item["area_ha"] for item in accepted) / max(1e-6, sum(item["area_ha"] for item in accepted))
        main_bbox = main["bbox_px"]
        main_bbox_lonlat = main["bbox_lonlat"]
        center = [
            round((main_bbox_lonlat[0] + main_bbox_lonlat[2]) / 2, 6),
            round((main_bbox_lonlat[1] + main_bbox_lonlat[3]) / 2, 6),
        ]
    else:
        confidence = 0.0
        change_class = "未检出明显变化"
        delta_lum = 0.0
        delta_exg = 0.0
        mean_score = 0.0
        x, y, w, h = sortie["patch"]["bbox"]
        main_bbox = [max(0, x - crop_box[0]), max(0, y - crop_box[1]), w, h]
        main_bbox_lonlat = sortie["patch"]["bbox_lonlat"]
        center = sortie["patch"]["center_lonlat"]

    for item in accepted:
        item.pop("_pixels", None)

    return {
        "changed_area_ha": changed_area,
        "confidence": round(confidence, 2),
        "change_class": change_class,
        "delta_lum": round(float(delta_lum), 3),
        "delta_exg": round(float(delta_exg), 3),
        "mean_score": round(float(mean_score), 3),
        "components": accepted,
        "main_bbox_px": [int(value) for value in main_bbox],
        "main_bbox_lonlat": main_bbox_lonlat,
        "center_lonlat": center,
        "crop_area_ha": crop["area_ha"],
        "changed_ratio": round(changed_px / max(1, width * height), 4),
        "threshold": detection["threshold"],
        "suppressed": detection["suppressed"],
        "detected_at": now_iso(),
        "overlay_url": f"/api/aircase/asset/{sortie['id']}/diff_overlay.png",
        "heatmap_url": f"/api/aircase/asset/{sortie['id']}/diff_heatmap.png",
        "scene_map_url": f"/api/aircase/asset/{sortie['id']}/scene_map.png",
        "_mask": combined,
    }


def _conclusion(result: dict[str, Any], patch: dict[str, Any], mode: str = "precise") -> str:
    scene = mode == "scene"
    action = "现场方位图核查" if scene else "精查"
    scope = f"在 {patch['id']} 图斑及周边范围内" if scene else f"在 {patch['id']} 图斑范围内"
    if result["changed_area_ha"] <= 0:
        return (
            f"本次{action}未{scope}检出达到阈值的明显新增扰动。"
            "该结果只说明本次演示影像与参数下未见异常，不排除小范围、短时或阴影遮挡下的变化，建议结合现场核查。"
        )
    return (
        f"本次{action}{scope}检出新增扰动 {result['changed_area_ha']:.1f} 公顷，"
        f"主要变化为亮度升高、植被指数下降，形态呈连片开挖/堆料特征，综合置信度 {result['confidence']:.2f}。"
        "与卫星初筛图斑相比，本次量算范围更集中，建议以本范围组织现场核查、权属查询和用地审批核对；"
        "是否构成违法及面积认定，应由具备资质的机构测绘并结合鉴定意见确定。"
    )


def _build_measure(sortie: dict[str, Any], result: dict[str, Any], legal_result: dict[str, Any]) -> dict[str, Any]:
    patch = sortie["patch"]
    route = sortie["route"]
    crop = sortie["crop"]
    legal_items = []
    for item in (legal_result.get("items") or [])[:4]:
        legal_items.append(
            {
                "id": item.get("id"),
                "law": item.get("law"),
                "article": item.get("article"),
                "heading": item.get("heading"),
                "summary": item.get("summary"),
                "evidence": (item.get("evidence") or [])[:4],
                "notes": (item.get("notes") or [])[:2],
            }
        )
    return {
        "sortie_id": sortie["id"],
        "title": "变化测量意见（演示）",
        "created_at": now_iso(),
        "mode": sortie["mode"],
        "patch": patch,
        "image": {
            "baseline": "2023-10-08 · Sentinel-2 L2A 样例",
            "after": "模拟复拍时相 · 同机位同范围",
            "crop_lonlat": crop["lonlat"],
            "crop_area_ha": crop["area_ha"],
            "crop_size": crop["size"],
            "gsd_m": crop["gsd_m"],
        },
        "flight": {
            "pattern": route["pattern"],
            "size_km": route["size_km"],
            "spacing_m": route["spacing_m"],
            "rel_height_m": route["rel_height_m"],
            "speed_mps": route["speed_mps"],
            "gimbal_pitch": route["gimbal_pitch"],
            "heading_deg": route["heading_deg"],
            "waypoints": (route.get("stats") or {}).get("waypoints"),
            "distance_km": (route.get("stats") or {}).get("distance_km"),
            "flight_min": (route.get("stats") or {}).get("flight_min"),
            "terrain_min_m": (route.get("stats") or {}).get("terrain_min_m"),
            "terrain_max_m": (route.get("stats") or {}).get("terrain_max_m"),
            "pose_match": route["pose_match"],
            "pose_max_offset_m": route["pose_max_offset_m"],
            "route_signature": route["route_signature"],
        },
        "detection": {
            "changed_area_ha": result["changed_area_ha"],
            "confidence": result["confidence"],
            "class": result["change_class"],
            "delta_lum": result["delta_lum"],
            "delta_exg": result["delta_exg"],
            "mean_score": result["mean_score"],
            "components": result["components"],
            "main_bbox_lonlat": result["main_bbox_lonlat"],
            "center_lonlat": result["center_lonlat"],
            "changed_ratio": result["changed_ratio"],
            "threshold": result["threshold"],
            "suppressed": result["suppressed"],
        },
        "legal": {
            "query": legal_result.get("query"),
            "inferred_hazard": legal_result.get("inferred_hazard"),
            "inferred_hazard_label": legal_result.get("inferred_hazard_label"),
            "items": legal_items,
        },
        "conclusion": _conclusion(result, patch, sortie.get("mode", "precise")),
        "limits": [
            "本意见基于演示影像与同机位模拟复拍生成，仅用于流程演示，不作为鉴定意见或处罚依据。",
            "面积按影像像元统计，受 GSD、配准误差、阴影、云影和地物混合像元影响；正式案件应由有资质机构测绘。",
            "同机位一致性只校验航线、高度、云台和航向参数，不替代现场像控、空域审批与飞行安全检查。",
            "法条为知识库检索参考，条文适用、数量标准与构罪条件需结合鉴定意见和最新司法解释复核。",
            "6G/边缘时延与回传指标为数字孪生仿真，不代表现网实测值。",
        ],
        "evidence": [
            "route.kml",
            "route.csv",
            "route.geojson",
            "before_uav.png",
            "after_uav.png",
            "diff_overlay.png",
            "diff_heatmap.png",
            "scene_map.png",
            "measure.md",
            "measure.html",
            "measure.json",
        ],
        "disclaimer": DISCLAIMER,
    }


def _measure_markdown(measure: dict[str, Any]) -> str:
    image = measure["image"]
    flight = measure["flight"]
    detection = measure["detection"]
    patch = measure["patch"]
    lines = [
        "# 变化测量意见（演示）",
        "",
        f"**编号**：{measure['sortie_id']}  ",
        f"**生成时间**：{measure['created_at']}  ",
        f"**任务类型**：卫星初筛 → 无人机同机位精查  ",
        f"**图斑**：{patch['id']} · {patch['class']}  ",
        f"**结论性质**：供核查参考，不作为鉴定结论  ",
        "",
        "> " + measure["disclaimer"],
        "",
        "## 一、任务来源",
        "",
        f"卫星初筛在演示 AOI 内发现 {patch['id']} 号候选图斑，估算面积 {patch['area_ha']:.1f} 公顷，"
        f"置信度 {patch['confidence']:.2f}。为核实变化范围、现状和地表扰动类型，"
        f"按同一机位规划无人机精查航线，对图斑核心区实施复拍与面积量算。",
        "",
        "## 二、影像与飞行参数",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 基线影像 | {image['baseline']} |",
        f"| 复拍影像 | {image['after']} |",
        f"| 精查范围 | {image['crop_lonlat'][0]:.5f}–{image['crop_lonlat'][2]:.5f}°E，"
        f"{image['crop_lonlat'][1]:.5f}–{image['crop_lonlat'][3]:.5f}°N |",
        f"| 精查面积 | {image['crop_area_ha']:.1f} 公顷 |",
        f"| 影像尺寸 | {image['crop_size'][0]} × {image['crop_size'][1]} 像素 |",
        f"| 影像 GSD | 约 {image['gsd_m']:.2f} 米/像素（演示） |",
        f"| 航线 | {flight['pattern']}，{flight['size_km']} km，间距 {flight['spacing_m']} m |",
        f"| 相对航高 | {flight['rel_height_m']} m，云台俯仰 {flight['gimbal_pitch']}° |",
        f"| 航向 / 速度 | {flight['heading_deg']}° / {flight['speed_mps']} m/s |",
        f"| 航点 / 航程 | {flight['waypoints']} 个 / {flight['distance_km']} km |",
        f"| 预计飞行 | {flight['flight_min']} 分钟 |",
        "",
        "## 三、同机位一致性",
        "",
        f"两次独立生成航线并比对航点坐标、相对航高、云台俯仰和航向："
        f"航线签名 `{flight['route_signature'][:16]}…`，"
        f"重复签名 `{'一致' if flight['pose_match'] else '不一致'}`，"
        f"最大平面偏差 {flight['pose_max_offset_m']:.2f} 米。"
        "该检查用于说明两次复拍使用同一机位参数，不替代现场像控。",
        "",
        "## 四、变化检测",
        "",
        f"- 检出变化面积：**{detection['changed_area_ha']:.2f} 公顷**",
        f"- 变化类型：**{detection['class']}**",
        f"- 综合置信度：**{detection['confidence']:.2f}**",
        f"- 平均亮度变化：{detection['delta_lum']:+.3f}；平均植被指数变化：{detection['delta_exg']:+.3f}",
        f"- 变化像元占比：{detection['changed_ratio'] * 100:.1f}%",
        f"- 检测阈值：{detection['threshold']:.3f}；抑制候选：{detection['suppressed']} 个",
        "",
        "| 序号 | 面积（公顷） | 类型 | 置信度 | 中心经纬度 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(detection["components"][:6], 1):
        center_lon = (item["bbox_lonlat"][0] + item["bbox_lonlat"][2]) / 2
        center_lat = (item["bbox_lonlat"][1] + item["bbox_lonlat"][3]) / 2
        lines.append(
            f"| {index} | {item['area_ha']:.2f} | {item['class']} | {item['confidence']:.2f} | "
            f"{center_lon:.5f}, {center_lat:.5f} |"
        )
    lines += [
        "",
        "## 五、面积量算",
        "",
        f"以精查范围 {image['crop_area_ha']:.1f} 公顷为控制面积，按变化像元数占比计算：",
        f"**{detection['changed_area_ha']:.2f} 公顷**。"
        "面积未扣除道路、阴影和既有裸地，属于影像估算值；现场核查时应以测绘坐标和权属资料为准。",
        "",
        "## 六、法条参考",
        "",
        "> 以下为知识库检索结果，条文适用与数量标准需结合鉴定意见和最新司法解释复核。",
        "",
    ]
    for index, item in enumerate(measure["legal"]["items"], 1):
        lines += [
            f"### {index}. {item['heading']}",
            "",
            f"**{item['law']} {item['article']}**",
            "",
            item["summary"] or "",
            "",
        ]
        if item["evidence"]:
            lines.append("取证要点：")
            for point in item["evidence"]:
                lines.append(f"- {point}")
            lines.append("")
    lines += [
        "## 七、结论",
        "",
        measure["conclusion"],
        "",
        "## 八、限制",
        "",
    ]
    for limit in measure["limits"]:
        lines.append(f"- {limit}")
    lines += [
        "",
        "## 九、证据清单",
        "",
        "逐文件 SHA-256、清单根哈希与 HMAC 签名见 `manifest.json`；校验接口为 "
        f"`POST /api/aircase/sortie/{measure['sortie_id']}/verify`。",
        "",
        "| 文件 | 说明 |",
        "| --- | --- |",
        "| route.kml / route.csv / route.geojson | 航线与航点 |",
        "| before_uav.png / after_uav.png | 同机位基线/复拍影像 |",
        "| diff_overlay.png / diff_heatmap.png | 变化叠加与热力图 |",
        "| scene_map.png | 现场方位图（演示） |",
        "| measure.md / measure.html / measure.json | 本意见三种格式 |",
        "",
        f"<!-- {EVIDENCE_NOTE} -->",
        "",
    ]
    return "\n".join(lines)


def _measure_html(measure: dict[str, Any]) -> str:
    image = measure["image"]
    flight = measure["flight"]
    detection = measure["detection"]
    patch = measure["patch"]

    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    components = "".join(
        f"<tr><td>{index}</td><td>{item['area_ha']:.2f}</td><td>{esc(item['class'])}</td>"
        f"<td>{item['confidence']:.2f}</td>"
        f"<td>{(item['bbox_lonlat'][0] + item['bbox_lonlat'][2]) / 2:.5f}, "
        f"{(item['bbox_lonlat'][1] + item['bbox_lonlat'][3]) / 2:.5f}</td></tr>"
        for index, item in enumerate(detection["components"][:6], 1)
    )
    legal_html = "".join(
        f"<section class='law'><h3>{index}. {esc(item['heading'])}</h3>"
        f"<p class='cite'>{esc(item['law'])} {esc(item['article'])}</p>"
        f"<p>{esc(item['summary'])}</p>"
        + ("<ul>" + "".join(f"<li>{esc(point)}</li>" for point in item["evidence"]) + "</ul>" if item["evidence"] else "")
        + "</section>"
        for index, item in enumerate(measure["legal"]["items"], 1)
    )
    limits = "".join(f"<li>{esc(item)}</li>" for item in measure["limits"])

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{esc(measure['sortie_id'])} 变化测量意见（演示）</title>
<style>
:root{{--ink:#12212c;--muted:#5c7280;--line:#d8e2e8;--cy:#0d7f96;--am:#b26a12;--bg:#f6f8f9}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 "Microsoft YaHei","PingFang SC",sans-serif}}
.wrap{{max-width:880px;margin:0 auto;padding:44px 46px 60px;background:#fff;box-shadow:0 10px 40px rgba(10,30,50,.08)}}
.kicker{{color:var(--cy);font:12px/1.4 Consolas,monospace;letter-spacing:.18em;text-transform:uppercase}}
h1{{font-size:30px;margin:8px 0 6px;letter-spacing:.04em}}h2{{font-size:19px;margin:34px 0 10px;padding-left:10px;border-left:3px solid var(--cy)}}
h3{{font-size:15px;margin:18px 0 4px}}.meta{{color:var(--muted);font-size:13px}}
.badge{{display:inline-block;margin:16px 0 4px;padding:6px 12px;border:1px solid #e2b27a;background:#fdf3e7;color:#8a4d0a;border-radius:4px;font-size:12px}}
.lead{{font-size:16px;background:#f0f7f9;border:1px solid #cfe4ea;padding:14px 16px;border-radius:6px}}
table{{width:100%;border-collapse:collapse;margin:10px 0 6px;font-size:13px}}
th,td{{border:1px solid var(--line);padding:7px 9px;text-align:left}}th{{background:#eef4f7;font-weight:600}}
.law{{border-top:1px dashed var(--line);padding-top:6px}}.cite{{color:var(--am);font-size:13px;margin:2px 0 6px}}
ul{{margin:6px 0 10px 20px;padding:0}}li{{margin:3px 0}}
.foot{{margin-top:38px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}}
code{{font-family:Consolas,monospace;background:#eef4f7;padding:1px 5px;border-radius:3px;font-size:12px}}
@media print{{body{{background:#fff}}.wrap{{box-shadow:none;max-width:none;padding:20mm}}}}
</style></head><body><div class="wrap">
<div class="kicker">TIANSHU · AIRCASE / 空中办案</div>
<h1>变化测量意见</h1>
<div class="meta">编号 {esc(measure['sortie_id'])} · 生成时间 {esc(measure['created_at'])} · 任务类型：卫星初筛 → 无人机同机位精查</div>
<div class="badge">演示件 · 不作为鉴定意见、测绘成果或执法依据</div>
<p class="lead">图斑 {esc(patch['id'])} · {esc(patch['class'])}；精查检出变化 <b>{detection['changed_area_ha']:.2f} 公顷</b>，置信度 <b>{detection['confidence']:.2f}</b>。</p>

<h2>一、任务来源</h2>
<p>卫星初筛在演示 AOI 内发现 {esc(patch['id'])} 号候选图斑，估算面积 {patch['area_ha']:.1f} 公顷，置信度 {patch['confidence']:.2f}。为核实变化范围、现状和地表扰动类型，按同一机位规划无人机精查航线，对图斑核心区实施复拍与面积量算。</p>

<h2>二、影像与飞行参数</h2>
<table>
<tr><th>项目</th><th>内容</th></tr>
<tr><td>基线影像</td><td>{esc(image['baseline'])}</td></tr>
<tr><td>复拍影像</td><td>{esc(image['after'])}</td></tr>
<tr><td>精查范围</td><td>{image['crop_lonlat'][0]:.5f}–{image['crop_lonlat'][2]:.5f}°E，{image['crop_lonlat'][1]:.5f}–{image['crop_lonlat'][3]:.5f}°N</td></tr>
<tr><td>精查面积</td><td>{image['crop_area_ha']:.1f} 公顷</td></tr>
<tr><td>影像尺寸 / GSD</td><td>{image['crop_size'][0]} × {image['crop_size'][1]} 像素 / 约 {image['gsd_m']:.2f} 米</td></tr>
<tr><td>航线</td><td>{esc(flight['pattern'])}，{flight['size_km']} km，间距 {flight['spacing_m']} m</td></tr>
<tr><td>相对航高 / 云台</td><td>{flight['rel_height_m']} m / {flight['gimbal_pitch']}°</td></tr>
<tr><td>航向 / 速度</td><td>{flight['heading_deg']}° / {flight['speed_mps']} m/s</td></tr>
<tr><td>航点 / 航程</td><td>{flight['waypoints']} 个 / {flight['distance_km']} km</td></tr>
<tr><td>预计飞行</td><td>{flight['flight_min']} 分钟</td></tr>
</table>

<h2>三、同机位一致性</h2>
<p>两次独立生成航线并比对航点坐标、相对航高、云台俯仰和航向：航线签名 <code>{esc(flight['route_signature'][:16])}…</code>，重复签名{'一致' if flight['pose_match'] else '不一致'}，最大平面偏差 {flight['pose_max_offset_m']:.2f} 米。该检查用于说明两次复拍使用同一机位参数，不替代现场像控。</p>

<h2>四、变化检测</h2>
<p>检出变化面积 <b>{detection['changed_area_ha']:.2f} 公顷</b>，类型 <b>{esc(detection['class'])}</b>，综合置信度 <b>{detection['confidence']:.2f}</b>；平均亮度变化 {detection['delta_lum']:+.3f}，平均植被指数变化 {detection['delta_exg']:+.3f}，变化像元占比 {detection['changed_ratio'] * 100:.1f}%。</p>
<table><tr><th>序号</th><th>面积（公顷）</th><th>类型</th><th>置信度</th><th>中心经纬度</th></tr>{components}</table>

<h2>五、面积量算</h2>
<p>以精查范围 {image['crop_area_ha']:.1f} 公顷为控制面积，按变化像元数占比计算为 <b>{detection['changed_area_ha']:.2f} 公顷</b>。面积未扣除道路、阴影和既有裸地，属于影像估算值；现场核查时应以测绘坐标和权属资料为准。</p>

<h2>六、法条参考</h2>
<p class="meta">以下为知识库检索结果，条文适用与数量标准需结合鉴定意见和最新司法解释复核。</p>
{legal_html}

<h2>七、结论</h2>
<p>{esc(measure['conclusion'])}</p>

<h2>八、限制</h2>
<ul>{limits}</ul>

<h2>九、证据清单</h2>
<p>逐文件 SHA-256、清单根哈希与 HMAC 签名见 <code>manifest.json</code>；校验接口为 <code>POST /api/aircase/sortie/{esc(measure['sortie_id'])}/verify</code>。</p>
<table><tr><th>文件</th><th>说明</th></tr>
<tr><td>route.kml / route.csv / route.geojson</td><td>航线与航点</td></tr>
<tr><td>before_uav.png / after_uav.png</td><td>同机位基线/复拍影像</td></tr>
<tr><td>diff_overlay.png / diff_heatmap.png</td><td>变化叠加与热力图</td></tr>
<tr><td>scene_map.png</td><td>现场方位图（演示）</td></tr>
<tr><td>measure.md / measure.html / measure.json</td><td>本意见三种格式</td></tr>
</table>

<div class="foot">{esc(measure['disclaimer'])}<br>{esc(EVIDENCE_NOTE)}</div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# 证据清单与校验
# ---------------------------------------------------------------------------
def _write_manifest(sortie_id: str, names: list[str]) -> dict[str, Any]:
    directory = OUT / sortie_id
    files = []
    for name in sorted(names):
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f"证据文件缺失：{name}")
        files.append({"name": name, "bytes": path.stat().st_size, "sha256": _sha256_file(path)})
    root = _root_hash(files)
    key = case_store._evidence_key()
    signature = hmac.new(key, root.encode("utf-8"), hashlib.sha256).hexdigest()
    manifest = {
        "sortie": sortie_id,
        "algorithm": "SHA-256 + HMAC-SHA256",
        "created_at": now_iso(),
        "files": files,
        "root_sha256": root,
        "signature": signature,
        "key_id": hashlib.sha256(key).hexdigest()[:16],
        "note": EVIDENCE_NOTE,
        "disclaimer": DISCLAIMER,
    }
    _write_json(directory / "manifest.json", manifest)
    return manifest


def run_sortie(sortie_id: str) -> dict[str, Any]:
    with _LOCK:
        sortie = _load_sortie(sortie_id)
        if sortie.get("status") == "done" and sortie.get("results"):
            return sortie

        directory = OUT / sortie_id
        before_image = Image.open(directory / "before_uav.png").convert("RGB")
        after_image = Image.open(directory / "after_uav.png").convert("RGB")
        width, height = before_image.size
        before = np.asarray(before_image, dtype=np.float32) / 255.0
        after = np.asarray(after_image, dtype=np.float32) / 255.0
        area_per_px = sortie["crop"]["area_ha"] / max(1, width * height)
        detection = _detect_arrays(
            before,
            after,
            min_px=max(120, int(width * height * 0.0008)),
            suppress=0.012,
            area_ha_per_px=area_per_px,
            quantile=0.90,
        )
        result = _build_result(sortie, detection)
        mask = result.pop("_mask")
        label = f"{result['change_class']} {result['changed_area_ha']:.1f} ha"
        _render_overlay(after_image, mask, result["main_bbox_px"], directory / "diff_overlay.png", label)
        _render_heatmap(detection["score"], result["main_bbox_px"], directory / "diff_heatmap.png")
        _render_scene_map(sortie, result, directory / "scene_map.png")

        query = f"{sortie['patch']['class']} 非法占用农用地 非法采矿 取土 建设扰动"
        legal_result = legal.checklist(query=query, limit=6)
        measure = _build_measure(sortie, result, legal_result)
        _write_json(directory / "measure.json", measure)
        (directory / "measure.md").write_text(_measure_markdown(measure), encoding="utf-8")
        (directory / "measure.html").write_text(_measure_html(measure), encoding="utf-8")

        evidence_files = [
            "route.kml",
            "route.csv",
            "route.geojson",
            "before_uav.png",
            "after_uav.png",
            "diff_overlay.png",
            "diff_heatmap.png",
            "scene_map.png",
            "measure.md",
            "measure.html",
            "measure.json",
        ]
        manifest = _write_manifest(sortie_id, evidence_files)

        sortie["status"] = "done"
        sortie["updated_at"] = now_iso()
        sortie["results"] = {key: value for key, value in result.items() if not key.startswith("_")}
        sortie["results"]["measure_url"] = f"/api/aircase/sortie/{sortie_id}/measure"
        sortie["results"]["scene_map_url"] = f"/api/aircase/asset/{sortie_id}/scene_map.png"
        sortie["evidence"] = {
            "root_sha256": manifest["root_sha256"],
            "signature": manifest["signature"],
            "key_id": manifest["key_id"],
            "algorithm": manifest["algorithm"],
            "files": manifest["files"],
            "verified": None,
        }
        sortie["measure"] = {
            "conclusion": measure["conclusion"],
            "legal": measure["legal"],
            "limits": measure["limits"],
        }
        _write_json(SORTIES / f"{sortie_id}.json", sortie)
        _audit(
            "sortie_run",
            sortie_id,
            {
                "changed_area_ha": result["changed_area_ha"],
                "confidence": result["confidence"],
                "components": len(result["components"]),
                "root_sha256": manifest["root_sha256"],
            },
        )
        return sortie


def verify_sortie(sortie_id: str) -> dict[str, Any]:
    sortie = _load_sortie(sortie_id)
    directory = OUT / sortie_id
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("该任务尚未生成证据清单，请先执行精查")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = []
    all_ok = True
    for item in manifest.get("files", []):
        path = directory / str(item.get("name"))
        actual = _sha256_file(path) if path.is_file() else None
        ok = actual == item.get("sha256")
        all_ok = all_ok and ok
        files.append(
            {
                "name": item.get("name"),
                "expected": item.get("sha256"),
                "actual": actual,
                "bytes": item.get("bytes"),
                "ok": ok,
            }
        )
    root_ok = _root_hash(manifest.get("files", [])) == manifest.get("root_sha256")
    key = case_store._evidence_key()
    expected_signature = hmac.new(key, str(manifest.get("root_sha256", "")).encode("utf-8"), hashlib.sha256).hexdigest()
    signature_ok = hmac.compare_digest(expected_signature, str(manifest.get("signature", "")))
    verified = bool(all_ok and root_ok and signature_ok)
    result = {
        "sortie": sortie_id,
        "verified": verified,
        "files_ok": all_ok,
        "root_ok": root_ok,
        "signature_ok": signature_ok,
        "checked_at": now_iso(),
        "root_sha256": manifest.get("root_sha256"),
        "key_id": manifest.get("key_id"),
        "files": files,
        "note": EVIDENCE_NOTE,
    }
    sortie["evidence"] = dict(sortie.get("evidence") or {})
    sortie["evidence"]["verified"] = verified
    sortie["evidence"]["checked_at"] = result["checked_at"]
    _write_json(SORTIES / f"{sortie_id}.json", sortie)
    _audit("evidence_verified", sortie_id, {"verified": verified, "files_ok": all_ok, "signature_ok": signature_ok})
    return result


# ---------------------------------------------------------------------------
# 对外查询
# ---------------------------------------------------------------------------
def overview() -> dict[str, Any]:
    detection = _detect_demo()
    recent = []
    if SORTIES.is_dir():
        for path in sorted(SORTIES.glob("AC-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:8]:
            try:
                sortie = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            recent.append(
                {
                    "id": sortie.get("id"),
                    "patch_id": sortie.get("patch_id"),
                    "mode": sortie.get("mode"),
                    "status": sortie.get("status"),
                    "created_at": sortie.get("created_at"),
                    "area_ha": (sortie.get("results") or {}).get("changed_area_ha"),
                    "confidence": (sortie.get("results") or {}).get("confidence"),
                }
            )
    return {
        "aoi": {
            "name": "大青山南麓矿区（演示）",
            "bbox": list(DEMO_BBOX),
            "center": [round((DEMO_BBOX[0] + DEMO_BBOX[2]) / 2, 6), round((DEMO_BBOX[1] + DEMO_BBOX[3]) / 2, 6)],
            "size_km": [
                round(_haversine_m(DEMO_BBOX[0], 41.959, DEMO_BBOX[2], 41.959) / 1000, 2),
                round(_haversine_m(DEMO_BBOX[0], DEMO_BBOX[1], DEMO_BBOX[0], DEMO_BBOX[3]) / 1000, 2),
            ],
        },
        "images": detection["images"],
        "patches": detection["patches"],
        "metrics": detection["metrics"],
        "method": detection["method"],
        "recent": recent,
        "disclaimer": DISCLAIMER,
        "evidence_note": EVIDENCE_NOTE,
    }


def get_sortie(sortie_id: str) -> dict[str, Any]:
    return _load_sortie(sortie_id)


def measure_content(sortie_id: str, fmt: str = "md") -> tuple[Path, str]:
    sortie = _load_sortie(sortie_id)
    if sortie.get("status") != "done":
        raise FileNotFoundError("该任务尚未执行，不能查看测量意见")
    fmt = fmt.lower()
    if fmt not in {"md", "html", "json"}:
        raise ValueError("format 只能是 md、html 或 json")
    path = OUT / sortie_id / f"measure.{fmt}"
    if not path.is_file():
        raise FileNotFoundError("测量意见文件不存在")
    media = {"md": "text/markdown; charset=utf-8", "html": "text/html; charset=utf-8", "json": "application/json"}[fmt]
    return path, media


def asset_path(sortie_id: str, name: str) -> Path:
    _load_sortie(sortie_id)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.startswith("."):
        raise ValueError("文件名不合法")
    directory = (OUT / sortie_id).resolve()
    path = (directory / name).resolve()
    if directory not in path.parents or not path.is_file():
        raise FileNotFoundError("证据文件不存在")
    return path
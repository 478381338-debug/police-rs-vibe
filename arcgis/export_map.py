"""用 ArcGIS Pro 3.5 / ArcPy 导出“生态警务风险专题图”。

流程：
  GeoJSON → FileGDB → ArcGIS 地图（天地图影像底图 + 异常/高发点/警务设施）
  → 1800×1050 PNG → 用 Pillow 叠加标题与图例
输出：web/assets/arcgis_risk_map.png
"""
from __future__ import annotations

import json
import os
import shutil
import traceback
from datetime import datetime as _dt
from pathlib import Path

import arcpy

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARC = ROOT / "arcgis"
TMP = ARC / "_tmp"
ASSETS = ROOT / "web" / "assets"
GDB = ARC / "eco.gdb"
APRX = ARC / "eco_police.aprx"
BLANK = Path(r"C:/Program Files/ArcGIS/Pro/Resources/ArcToolBox/Services/routingservices/data/Blank.aprx")
OUT_PNG = ASSETS / "arcgis_risk_map.png"

LEVEL_COLORS = {
    "red": [255, 59, 48, 95],
    "orange": [255, 149, 0, 90],
    "yellow": [255, 214, 10, 80],
    "blue": [10, 132, 255, 80],
}


def read_json(name: str) -> dict:
    p = DATA / name
    if not p.exists():
        return {"type": "FeatureCollection", "features": []}
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(name: str, obj: dict):
    p = TMP / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def split_police():
    d = read_json("police.geojson")
    pts, lines = [], []
    for f in d.get("features", []):
        t = (f.get("geometry") or {}).get("type")
        if t == "Point":
            pts.append(f)
        elif t == "LineString":
            lines.append(f)
    write_json("police_points.geojson", {"type": "FeatureCollection", "features": pts})
    write_json("police_routes.geojson", {"type": "FeatureCollection", "features": lines})
    return len(pts), len(lines)


def split_hotspots():
    d = read_json("hotspots.geojson")
    buckets = {"red": [], "orange": [], "yellow": [], "blue": []}
    for f in d.get("features", []):
        lv = (f.get("properties") or {}).get("danger_level", "blue")
        buckets.setdefault(lv, []).append(f)
    out = {}
    for lv, feats in buckets.items():
        p = write_json(f"hotspots_{lv}.geojson", {"type": "FeatureCollection", "features": feats})
        out[lv] = p
    return out


def to_fc(geojson: Path, name: str) -> str | None:
    if not geojson.exists():
        return None
    try:
        d = json.loads(geojson.read_text(encoding="utf-8"))
        if not d.get("features"):
            return None
    except Exception:
        return None
    out = os.path.join(GDB, name)
    if arcpy.Exists(out):
        arcpy.management.Delete(out)
    arcpy.conversion.JSONToFeatures(str(geojson), out)
    return out


def set_polygon_style(layer, color, outline=None, width=0.6):
    """用 CIM 设置面符号：填充色 + 描边色/线宽（ArcPy 的 updateRenderer 有 bug）。"""
    try:
        cim = layer.getDefinition("V2")
        sym = cim.renderer.symbol.symbol
        for sl in sym.symbolLayers:
            n = type(sl).__name__
            if n == "CIMSolidFill" and hasattr(sl, "color"):
                sl.color.values = list(color)
            elif n == "CIMSolidStroke" and hasattr(sl, "color"):
                sl.color.values = list(outline or [255, 255, 255, 120])
                sl.width = width
        layer.setDefinition(cim)
    except Exception as e:
        print(f"[warn] 设置 {layer.name} 符号失败: {e}", flush=True)


def set_point_style(layer, color, size=8):
    try:
        sym = layer.symbology
        r = sym.renderer
        s = r.symbol
        s.color = {"RGB": color}
        s.outlineColor = {"RGB": [255, 255, 255, 230]}
        s.outlineWidth = 0.8
        s.size = size
        sym.updateRenderer(r)
    except Exception as e:
        print(f"[warn] 设置 {layer.name} 符号失败: {e}", flush=True)


def set_line_style(layer, color, width=1.2):
    try:
        cim = layer.getDefinition("V2")
        sym = cim.renderer.symbol.symbol
        for sl in sym.symbolLayers:
            if type(sl).__name__ == "CIMSolidStroke" and hasattr(sl, "color"):
                sl.color.values = list(color)
                sl.width = width
        layer.setDefinition(cim)
    except Exception as e:
        print(f"[warn] 设置 {layer.name} 符号失败: {e}", flush=True)


def add_title(img_path: Path, summary: dict):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        print("[info] 无 Pillow，跳过标题叠加", flush=True)
        return
    im = Image.open(img_path).convert("RGBA")
    W, H = im.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    font_paths = [
        r"C:/Windows/Fonts/msyh.ttc",
        r"C:/Windows/Fonts/msyhbd.ttc",
        r"C:/Windows/Fonts/simhei.ttf",
    ]
    def font(size):
        for p in font_paths:
            if Path(p).exists():
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    # 顶部标题带
    d.rectangle([0, 0, W, 92], fill=(7, 12, 18, 225))
    d.text((34, 16), "警务遥感 · 生态安全风险专题图", font=font(34), fill=(231, 238, 247, 255))
    ts = summary.get("updated_at", _dt.now().isoformat())
    d.text((36, 60), f"大青山自然保护区 · 高发地点危险等级 · 数据更新 {ts[:19].replace('T', ' ')}", font=font(17), fill=(132, 150, 168, 255))
    # 右上角数据源
    d.text((W - 500, 24), "ArcGIS Pro 3.5", font=font(20), fill=(56, 189, 248, 255))
    d.text((W - 500, 54), "Microsoft Planetary Computer · Open-Meteo", font=font(15), fill=(132, 150, 168, 255))

    # 左下角图例
    legend = [("一级 · 红色", (255, 59, 48)), ("二级 · 橙色", (255, 149, 0)),
              ("三级 · 黄色", (255, 214, 10)), ("四级 · 蓝色", (10, 132, 255))]
    bx, by = 30, H - 168
    d.rectangle([bx - 10, by - 12, bx + 250, by + 128], fill=(7, 12, 18, 205), outline=(34, 54, 74, 255))
    d.text((bx, by - 6), "危险等级", font=font(18), fill=(231, 238, 247, 255))
    for i, (label, color) in enumerate(legend):
        yy = by + 26 + i * 24
        d.rectangle([bx, yy, bx + 16, yy + 12], fill=(*color, 230))
        d.text((bx + 26, yy - 3), label, font=font(15), fill=(200, 214, 229, 255))
    # 右下角统计
    dc = summary.get("danger_counts", {})
    stats = f"高发点 {summary.get('hotspots', 0)} 处 · 红 {dc.get('red', 0)} / 橙 {dc.get('orange', 0)} / 黄 {dc.get('yellow', 0)} / 蓝 {dc.get('blue', 0)}"
    d.rectangle([W - 520, H - 62, W - 24, H - 20], fill=(7, 12, 18, 205), outline=(34, 54, 74, 255))
    d.text((W - 504, H - 52), stats, font=font(16), fill=(200, 214, 229, 255))

    out = Image.alpha_composite(im, overlay).convert("RGB")
    out.save(img_path, "PNG", optimize=True)
    print("[ok] 标题/图例已叠加", flush=True)


def main():
    TMP.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    arcpy.env.overwriteOutput = True

    n_pts, n_lines = split_police()
    print(f"[1/5] 警务点/路线拆分：{n_pts} 点 / {n_lines} 线", flush=True)
    hot = split_hotspots()

    if arcpy.Exists(str(GDB)):
        arcpy.management.Delete(str(GDB))
    arcpy.management.CreateFileGDB(str(ARC), "eco.gdb")
    print("[2/5] FileGDB 已创建", flush=True)

    fcs = {}
    for name, fn in [
        ("aoi", "aoi.geojson"),
        ("incidents", "incidents.geojson"),
        ("hotspots_red", "hotspots_red.geojson"),
        ("hotspots_orange", "hotspots_orange.geojson"),
        ("hotspots_yellow", "hotspots_yellow.geojson"),
        ("hotspots_blue", "hotspots_blue.geojson"),
        ("police_points", "police_points.geojson"),
        ("police_routes", "police_routes.geojson"),
    ]:
        fc = to_fc(TMP / fn, name)
        if fc:
            fcs[name] = fc
    print(f"[3/5] 要素类：{', '.join(fcs.keys())}", flush=True)

    if APRX.exists():
        APRX.unlink()
    shutil.copy(BLANK, APRX)
    prj = arcpy.mp.ArcGISProject(str(APRX))
    m = prj.createMap("EcoPolice")

    # 底图：ArcGIS Pro 已配置天地图影像；失败则退回 Topographic
    for bm in ("天地图-影像（球面墨卡托）", "天地图-影像（CGCS2000）", "Topographic"):
        try:
            m.addBasemap(bm)
            print(f"[4/5] 底图：{bm}", flush=True)
            break
        except Exception as e:
            print(f"[warn] 底图 {bm} 失败: {str(e)[:100]}", flush=True)

    # 先加 AOI（最底层）
    if "aoi" in fcs:
        layer = m.addDataFromPath(fcs["aoi"])
        layer.name = "研究区边界"
        set_polygon_style(layer, [34, 211, 238, 15], outline=[34, 211, 238, 220], width=1.0)
        layer.transparency = 70

    if "incidents" in fcs:
        layer = m.addDataFromPath(fcs["incidents"])
        layer.name = "历史异常图斑"
        set_polygon_style(layer, [255, 149, 0, 55], outline=[255, 149, 0, 150], width=0.5)
        layer.transparency = 35

    level_names = {"red": "一级红色", "orange": "二级橙色", "yellow": "三级黄色", "blue": "四级蓝色"}
    for lv in ("blue", "yellow", "orange", "red"):
        key = f"hotspots_{lv}"
        if key in fcs:
            layer = m.addDataFromPath(fcs[key])
            layer.name = f"高发点 · {level_names[lv]}"
            set_polygon_style(layer, LEVEL_COLORS[lv], outline=[255, 255, 255, 170], width=0.9)
            layer.transparency = 15

    if "police_points" in fcs:
        layer = m.addDataFromPath(fcs["police_points"])
        layer.name = "警务设施（示意）"
        try:
            set_point_style(layer, [56, 189, 248, 255], size=8)
        except Exception:
            pass
        layer.transparency = 0

    if "police_routes" in fcs:
        layer = m.addDataFromPath(fcs["police_routes"])
        layer.name = "巡护路线（示意）"
        set_line_style(layer, [56, 189, 248, 180], width=1.2)

    # 设置相机范围
    try:
        ext = arcpy.Extent(108.9, 39.95, 112.8, 41.58, spatial_reference=arcpy.SpatialReference(4326))
        m.defaultCamera.setExtent(ext)
    except Exception as e:
        print(f"[warn] 设置范围失败: {e}", flush=True)

    prj.save()
    print("[5/6] 导出专题图 PNG…", flush=True)
    m.defaultView.exportToPNG(str(OUT_PNG), 1800, 1050, 150)
    if not OUT_PNG.exists():
        raise RuntimeError("PNG 未生成")
    print(f"[ok] ArcGIS 专题图：{OUT_PNG}（{OUT_PNG.stat().st_size} B）", flush=True)

    # 纯底图（天地图影像，无标题/图例/业务图层），供网页作为静态遥感底图。
    # 复用已定位好的主地图：移除业务图层，保留底图，再导出。这样相机范围不会丢。
    try:
        print("[6/6] 导出纯底图（天地图影像）…", flush=True)
        for lyr in list(m.listLayers()):
            try:
                if not lyr.isBasemapLayer:
                    m.removeLayer(lyr)
            except Exception:
                pass
        try:
            m.defaultCamera.setExtent(ext)
        except Exception:
            pass
        raw = ASSETS / "arcgis_basemap.png"
        m.defaultView.exportToPNG(str(raw), 1800, 1050, 150)
        try:
            from PIL import Image as _Image
            _im = _Image.open(raw).convert("RGB")
            _im.thumbnail((1100, 1100), _Image.LANCZOS)
            _im.save(ASSETS / "arcgis_basemap.jpg", "JPEG", quality=80, optimize=True)
        except Exception as e:
            print(f"[warn] 底图转 JPG 失败: {e}", flush=True)
        print(f"[ok] ArcGIS 纯底图：{raw}（{raw.stat().st_size} B）", flush=True)
    except Exception as e:
        print(f"[warn] 纯底图导出失败: {e}", flush=True)

    summary = {}
    sp = DATA / "summary.json"
    if sp.exists():
        try:
            summary = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            summary = {}
    add_title(OUT_PNG, summary)
    print(f"[done] {OUT_PNG}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise

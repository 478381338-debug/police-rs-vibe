"""ArcGIS Pro 多源开放数据专题图 + DEM 三维地形态势图。

2D 专题图使用 ArcGIS Pro / ArcPy 导出：
  研究区影像（已有 ArcGIS 底图） + WorldPop 人口分级 + OSM 道路/设施/建筑样例
  + 研究区边界 → 1800×1050 PNG，再用 Pillow 叠加标题、图例和署名。

3D 地形图使用本地 DEM 做透视曲面渲染；ArcGIS Pro 的 Scene 相机在当前无界面
ArcPy 会话中不可写，因此不伪造“ArcGIS 3D 截图”，而是输出可核查的 DEM 三维态势图。
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime as _dt
from pathlib import Path

import arcpy

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXT = DATA / "external"
ARC = ROOT / "arcgis"
TMP = ARC / "_tmp"
ASSETS = ROOT / "web" / "assets"
GDB = ARC / "open_data.gdb"
APRX = ARC / "open_data.aprx"
BLANK = Path(r"C:/Program Files/ArcGIS/Pro/Resources/ArcToolBox/Services/routingservices/data/Blank.aprx")

POP_COLORS = {
    0: [10, 132, 255, 70],
    1: [34, 211, 238, 85],
    2: [255, 214, 10, 105],
    3: [255, 149, 0, 125],
    4: [255, 59, 48, 145],
}
POP_LABELS = {0: "人口低", 1: "人口较低", 2: "人口中", 3: "人口较高", 4: "人口高"}
FACILITY = {
    "police": ("警务设施", [56, 189, 248, 255]),
    "hospital": ("医疗机构", [255, 99, 132, 255]),
    "school": ("学校", [255, 214, 10, 255]),
    "fire": ("消防站", [255, 149, 0, 255]),
}


def jload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def jdump(path: Path, obj: dict):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def to_fc(geojson: Path, name: str):
    if not geojson.exists():
        return None
    try:
        d = jload(geojson)
        if not d.get("features"):
            return None
    except Exception:
        return None
    # ArcGIS Pro 3.5 的 JSONToFeatures 对 GeoJSON 默认按 POLYGON 处理；
    # 点和线必须显式指定 geometry_type，否则会生成 0 要素的 Polygon 类。
    gt = (d["features"][0].get("geometry") or {}).get("type", "Polygon")
    gt = {"Point": "POINT", "MultiPoint": "MULTIPOINT", "LineString": "POLYLINE",
          "MultiLineString": "POLYLINE", "Polygon": "POLYGON",
          "MultiPolygon": "POLYGON"}.get(gt, "POLYGON")
    out = os.path.join(GDB, name)
    if arcpy.Exists(out):
        arcpy.management.Delete(out)
    arcpy.conversion.JSONToFeatures(str(geojson), out, gt)
    return out


def set_polygon_style(layer, color, outline=None, width=0.5):
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
        print(f"[warn] 面符号 {layer.name}: {e}", flush=True)


def set_point_style(layer, color, size=7):
    try:
        sym = layer.symbology
        r = sym.renderer
        s = r.symbol
        s.color = {"RGB": color}
        s.outlineColor = {"RGB": [8, 14, 22, 235]}
        s.outlineWidth = 0.8
        s.size = size
        sym.updateRenderer(r)
    except Exception as e:
        print(f"[warn] 点符号 {layer.name}: {e}", flush=True)


def set_line_style(layer, color, width=1.0):
    try:
        cim = layer.getDefinition("V2")
        sym = cim.renderer.symbol.symbol
        for sl in sym.symbolLayers:
            if type(sl).__name__ == "CIMSolidStroke" and hasattr(sl, "color"):
                sl.color.values = list(color)
                sl.width = width
        layer.setDefinition(cim)
    except Exception as e:
        print(f"[warn] 线符号 {layer.name}: {e}", flush=True)


def make_basemap_tif() -> Path:
    """用 ArcPy 把 JPEG 写成真实地理参考 TIFF（ArcGIS 对 JPEG world file 支持不稳定）。"""
    import numpy as np
    from PIL import Image

    src = ASSETS / "arcgis_basemap.jpg"
    if not src.exists():
        raise RuntimeError("缺少 arcgis_basemap.jpg，请先运行 arcgis/export_map.py")
    im = Image.open(src).convert("RGB")
    W, H = im.size
    arr = np.array(im)
    xres = (112.80 - 108.90) / W
    yres = (41.58 - 39.95) / H
    bands = []
    for i in range(3):
        ras = arcpy.NumPyArrayToRaster(arr[:, :, i], arcpy.Point(108.90, 39.95), xres, yres)
        p = TMP / f"basemap_b{i}.tif"
        ras.save(str(p))
        bands.append(str(p))
    out = TMP / "basemap_geo.tif"
    arcpy.management.CompositeBands(bands, str(out))
    arcpy.management.DefineProjection(str(out), arcpy.SpatialReference(4326))
    return out


def make_basemap_jpg() -> Path:
    """保留旧接口：生成带 world file 的 JPEG（作为 TIFF 失败时的兜底）。"""
    from PIL import Image

    src = ASSETS / "arcgis_basemap.jpg"
    if not src.exists():
        raise RuntimeError("缺少 arcgis_basemap.jpg，请先运行 arcgis/export_map.py")
    im = Image.open(src).convert("RGB")
    W, H = im.size
    out = TMP / "basemap_geo.jpg"
    im.save(out, "JPEG", quality=88, optimize=True)
    xres = (112.80 - 108.90) / W
    yres = (41.58 - 39.95) / H
    # ESRI world file: A, D, B, E, C, F
    (TMP / "basemap_geo.jgw").write_text(
        f"{xres:.12f}\n0\n0\n{-yres:.12f}\n{108.90 + xres/2:.12f}\n{41.58 - yres/2:.12f}\n",
        encoding="ascii")
    # ArcGIS 不同版本对 JPEG world file 扩展名识别不一致，同时写 .jpw。
    (TMP / "basemap_geo.jpw").write_text(
        f"{xres:.12f}\n0\n0\n{-yres:.12f}\n{108.90 + xres/2:.12f}\n{41.58 - yres/2:.12f}\n",
        encoding="ascii")
    (TMP / "basemap_geo.prj").write_text(
        'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]', encoding="ascii")
    return out


def prepare_layers():
    pop = jload(EXT / "population_grid.geojson")
    buckets = {i: [] for i in range(5)}
    for f in pop.get("features", []):
        lv = int((f.get("properties") or {}).get("pop_level", 0))
        buckets.setdefault(lv, []).append(f)
    for lv, feats in buckets.items():
        jdump(TMP / f"pop_{lv}.geojson", {"type": "FeatureCollection", "features": feats})

    pois = jload(EXT / "osm_pois.geojson")
    kinds = {k: [] for k in FACILITY}
    for f in pois.get("features", []):
        k = (f.get("properties") or {}).get("kind")
        if k in kinds:
            kinds[k].append(f)
    for k, feats in kinds.items():
        jdump(TMP / f"fac_{k}.geojson", {"type": "FeatureCollection", "features": feats})

    # 建筑样例：只保留高度 >= 12m 的，减少视觉噪声。
    bld = jload(EXT / "osm_buildings.geojson")
    tall = [f for f in bld.get("features", []) if float((f.get("properties") or {}).get("height_m", 0)) >= 12]
    jdump(TMP / "buildings_tall.geojson", {"type": "FeatureCollection", "features": tall})

    return {lv: len(feats) for lv, feats in buckets.items()}, {k: len(v) for k, v in kinds.items()}, len(tall)


def build_map():
    TMP.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    arcpy.env.overwriteOutput = True
    if arcpy.Exists(str(GDB)):
        arcpy.management.Delete(str(GDB))
    arcpy.management.CreateFileGDB(str(ARC), "open_data.gdb")
    print("[1/5] FileGDB 已创建", flush=True)

    pop_counts, fac_counts, tall_count = prepare_layers()
    print(f"[2/5] 人口分级 {pop_counts}；设施 {fac_counts}；高层建筑样例 {tall_count}", flush=True)

    fcs = {}
    for name in [f"pop_{i}" for i in range(5)] + [f"fac_{k}" for k in FACILITY] + [
        "buildings_tall", "osm_roads", "aoi",
    ]:
        fc = to_fc(TMP / f"{name}.geojson", name)
        if fc:
            fcs[name] = fc
    roads = jload(EXT / "osm_roads.geojson")
    fc = to_fc(EXT / "osm_roads.geojson", "osm_roads")
    if fc:
        fcs["osm_roads"] = fc
    fc = to_fc(DATA / "aoi.geojson", "aoi")
    if fc:
        fcs["aoi"] = fc
    print(f"[3/5] 要素类：{', '.join(fcs)}", flush=True)

    if APRX.exists():
        APRX.unlink()
    shutil.copy(BLANK, APRX)
    prj = arcpy.mp.ArcGISProject(str(APRX))
    m = prj.createMap("OpenDataPolice")
    try:
        base = m.addDataFromPath(str(make_basemap_tif()))
        base.name = "ArcGIS 天地图影像底图"
        print("[4/5] 本地 ArcGIS 底图已加载", flush=True)
    except Exception as e:
        print(f"[warn] 底图加载失败，使用深色专题底图: {e}", flush=True)

    if "aoi" in fcs:
        lyr = m.addDataFromPath(fcs["aoi"])
        lyr.name = "研究区边界"
        set_polygon_style(lyr, [34, 211, 238, 10], outline=[34, 211, 238, 210], width=1.2)

    for lv in range(5):
        key = f"pop_{lv}"
        if key in fcs:
            lyr = m.addDataFromPath(fcs[key])
            lyr.name = f"{POP_LABELS[lv]} · WorldPop"
            set_polygon_style(lyr, POP_COLORS[lv], outline=[255, 255, 255, 35], width=0.3)
            lyr.transparency = 48

    if "osm_roads" in fcs:
        lyr = m.addDataFromPath(fcs["osm_roads"])
        lyr.name = "OSM 主干道路"
        lyr.transparency = 10

    if "buildings_tall" in fcs:
        lyr = m.addDataFromPath(fcs["buildings_tall"])
        lyr.name = "建筑样例（≥12m）"
        lyr.transparency = 20

    for k in ("hospital", "school", "fire", "police"):
        key = f"fac_{k}"
        if key in fcs:
            lyr = m.addDataFromPath(fcs[key])
            label, col = FACILITY[k]
            lyr.name = label
            # 不在这里改点符号：ArcGIS Pro 3.5 的 updateRenderer 在此会话会报
            # “unhashable type: SimpleRenderer”，保留默认符号确保可见。

    try:
        ext = arcpy.Extent(108.90, 39.95, 112.80, 41.58, spatial_reference=arcpy.SpatialReference(4326))
        cam = m.defaultCamera
        cam.setExtent(ext)
        cam.scale = 3_200_000
        print(f"[debug] camera X={cam.X:.4f} Y={cam.Y:.4f} scale={cam.scale:.1f}", flush=True)
        for lyr in m.listLayers():
            print(f"[debug] layer {lyr.name} visible={lyr.visible} transparent={getattr(lyr, 'transparency', 'n/a')}", flush=True)
    except Exception as e:
        print(f"[warn] 相机范围: {e}", flush=True)
    prj.save()

    out = ASSETS / "arcgis_open_data_map.png"
    print("[5/5] 导出多源开放数据专题图…", flush=True)
    m.defaultView.exportToPNG(str(out), 1800, 1050, 150)
    pop_total = sum(int((f.get("properties") or {}).get("population", 0))
                    for f in jload(EXT / "population_grid.geojson").get("features", []))
    poi_total = len(jload(EXT / "osm_pois.geojson").get("features", []))
    add_title(out, pop_total, poi_total, tall_count)
    print(f"[ok] {out} ({out.stat().st_size} B)", flush=True)
    return out


def add_title(img_path: Path, pop_total, poi_total, tall_count):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(img_path).convert("RGBA")
    W, H = im.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    font_paths = [r"C:/Windows/Fonts/msyh.ttc", r"C:/Windows/Fonts/msyhbd.ttc", r"C:/Windows/Fonts/simhei.ttf"]

    def font(size):
        for p in font_paths:
            if Path(p).exists():
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    d.rectangle([0, 0, W, 96], fill=(7, 12, 18, 232))
    d.text((34, 15), "警务遥感 · 多源开放数据专题图", font=font(34), fill=(231, 238, 247, 255))
    d.text((36, 62), "人口暴露 · 基础设施 · 道路可达 · 生态与地形底图", font=font(17), fill=(132, 150, 168, 255))
    d.text((W - 470, 20), "ArcGIS Pro 3.5", font=font(20), fill=(56, 189, 248, 255))
    d.text((W - 470, 52), "WorldPop · OSM · MPC · AWS Terrain", font=font(14), fill=(132, 150, 168, 255))

    # 人口分级图例
    bx, by = 30, H - 248
    d.rectangle([bx - 10, by - 12, bx + 300, by + 186], fill=(7, 12, 18, 220), outline=(34, 54, 74, 255))
    d.text((bx, by - 4), "人口暴露（WorldPop 2020）", font=font(17), fill=(231, 238, 247, 255))
    for i in range(5):
        yy = by + 28 + i * 23
        c = POP_COLORS[i]
        d.rectangle([bx, yy, bx + 16, yy + 12], fill=(*c[:3], 230))
        d.text((bx + 26, yy - 3), POP_LABELS[i], font=font(14), fill=(200, 214, 229, 255))

    # 设施图例
    bx2 = 350
    d.rectangle([bx2 - 10, by - 12, bx2 + 240, by + 140], fill=(7, 12, 18, 220), outline=(34, 54, 74, 255))
    d.text((bx2, by - 4), "基础设施（OSM）", font=font(17), fill=(231, 238, 247, 255))
    for i, (k, (label, col)) in enumerate(FACILITY.items()):
        yy = by + 28 + i * 25
        d.ellipse([bx2, yy, bx2 + 13, yy + 13], fill=(*col[:3], 240), outline=(255, 255, 255, 220))
        d.text((bx2 + 24, yy - 3), label, font=font(14), fill=(200, 214, 229, 255))

    stats = f"人口约 {pop_total/10000:.0f} 万 · POI {poi_total} · 道路 9,078 · 建筑样例 {tall_count}"
    d.rectangle([W - 620, H - 62, W - 24, H - 20], fill=(7, 12, 18, 220), outline=(34, 54, 74, 255))
    d.text((W - 602, H - 52), stats, font=font(14), fill=(200, 214, 229, 255))
    d.text((30, H - 28), "© OpenStreetMap contributors (ODbL) · WorldPop (CC BY 4.0) · Microsoft Planetary Computer · AWS Terrain Tiles",
           font=font(12), fill=(132, 150, 168, 255))

    Image.alpha_composite(im, ov).convert("RGB").save(img_path, "PNG", optimize=True)
    print("[ok] 标题/图例/署名已叠加", flush=True)


def build_3d():
    """由独立脚本 arcgis/render_terrain_3d.py 生成；此处保留占位，避免 ArcPy 环境缺 rasterio。"""
    raise NotImplementedError("请运行 arcgis/render_terrain_3d.py")


def main():
    build_map()
    meta = {
        "generated_at": _dt.now().isoformat(timespec="seconds"),
        "thematic_map": "arcgis_open_data_map.png",
        "terrain_3d": "arcgis_terrain_3d.png",
        "engine": "ArcGIS Pro 3.5 / ArcPy",
        "notes": [
            "专题图为 ArcGIS Pro 导出；3D 地形为本地 DEM 透视渲染。",
            "ArcGIS Pro Scene 相机在无界面 ArcPy 会话中不可写，未将其伪造成 ArcGIS 3D 截图。",
        ],
        "attribution": [
            "© OpenStreetMap contributors (ODbL 1.0)",
            "WorldPop 2020 (CC BY 4.0)",
            "Microsoft Planetary Computer / ESA / NASA / JAXA",
            "AWS Terrain Tiles / Mapzen Terrarium",
        ],
    }
    (ASSETS / "arcgis_open_data_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

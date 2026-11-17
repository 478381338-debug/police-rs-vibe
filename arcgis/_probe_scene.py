from pathlib import Path
import shutil
import arcpy

ROOT = Path(__file__).resolve().parents[1]
ARC = ROOT / "arcgis"
TMP = ARC / "_tmp"
TMP.mkdir(parents=True, exist_ok=True)
blank = Path(r"C:/Program Files/ArcGIS/Pro/Resources/ArcToolBox/Services/routingservices/data/Blank.aprx")
aprx_path = TMP / "probe_scene.aprx"
if aprx_path.exists():
    aprx_path.unlink()
shutil.copy(blank, aprx_path)
prj = arcpy.mp.ArcGISProject(str(aprx_path))
print("project", prj)
print("maps before", [m.name for m in prj.listMaps()])
m = prj.createMap("ProbeScene", map_type="SCENE")
print("scene", m, type(m))
print("scene attrs", [a for a in dir(m) if not a.startswith("_")])
print("elev surfaces", m.listElevationSurfaces())
print("ground", getattr(m, "ground", None))
print("default camera", m.defaultCamera)
cam = m.defaultCamera
print("camera attrs", [a for a in dir(cam) if not a.startswith("_")])
print("camera props", {k: getattr(cam, k) for k in ("X", "Y", "Z", "heading", "pitch", "roll", "scale") if hasattr(cam, k)})
dem = ROOT / "data/external/dem_aoi.tif"
try:
    lyr = m.addDataFromPath(str(dem))
    print("dem layer", lyr, lyr.name, lyr.isRasterLayer)
    print("layer attrs", [a for a in dir(lyr) if not a.startswith("_")])
except Exception as e:
    print("add dem error", type(e).__name__, e)
print("elev surfaces after", m.listElevationSurfaces())
for es in m.listElevationSurfaces():
    print("es", es, type(es), [a for a in dir(es) if not a.startswith("_")])
    for k in ("name", "isEnabled", "dataSource", "elevationSourceType"):
        try:
            print("  ", k, getattr(es, k))
        except Exception as e:
            print("  ", k, "ERR", e)
try:
    ext = arcpy.Extent(108.9, 39.95, 112.8, 41.58, spatial_reference=arcpy.SpatialReference(4326))
    m.defaultCamera.setExtent(ext)
    print("camera after extent", {k: getattr(cam, k) for k in ("X", "Y", "Z", "heading", "pitch", "roll", "scale") if hasattr(cam, k)})
except Exception as e:
    print("set extent error", e)
prj.save()
print("saved", aprx_path)
PY
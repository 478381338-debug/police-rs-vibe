"""On-demand GMT 3D perspective rendering.

GMT is still a batch cartography engine, but the web page can now request a
fresh perspective image for a chosen azimuth/elevation instead of only showing
two checked-in JPGs.  This is not an interactive WebGL renderer; the live 3D
map remains MapLibre.  The endpoint is intentionally server-side and cached.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app import config

GMT = Path(r"D:\数学建模工具\GMT\gmt6\bin\gmt.exe")
DEM = config.DATA / "external" / "china_land_0p1.tif"
OUTLINE = config.DATA / "external" / "china_outline.gmt"
PROVINCES = config.DATA / "external" / "china_provinces.gmt"
OUT_DIR = config.ASSETS / "gmt_render"
REGION = "70/140/15/55"


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def available() -> bool:
    return GMT.exists() and DEM.exists() and OUTLINE.exists() and PROVINCES.exists()


def render(az: int = 140, el: int = 24) -> Path:
    """Render a China 3D perspective PNG and return its path.

    Parameters are intentionally bounded so arbitrary query strings cannot
    create pathological GMT jobs.
    """
    az = _clamp_int(az, 0, 359)
    el = _clamp_int(el, 8, 60)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUT_DIR / f"china_3d_{az:03d}_{el:02d}"
    out = base.with_suffix(".png")
    if out.exists() and out.stat().st_size > 20_000:
        return out

    if not available():
        raise RuntimeError("GMT 或中国 DEM/边界文件不可用")

    env = os.environ.copy()
    env["PATH"] = str(GMT.parent) + os.pathsep + env.get("PATH", "")
    env["GMT_END_SHOW"] = "off"
    # GMT on Windows is sensitive to non-ASCII absolute paths; run relative to ROOT.
    rel_dem = DEM.relative_to(config.ROOT).as_posix()
    rel_outline = OUTLINE.relative_to(config.ROOT).as_posix()
    rel_provinces = PROVINCES.relative_to(config.ROOT).as_posix()
    rel_base = base.relative_to(config.ROOT).as_posix()
    cmds = [
        [str(GMT), "begin", rel_base, "png"],
        [str(GMT), "set", "PS_PAGE_COLOR", "black"],
        [str(GMT), "grdview", rel_dem, "-R" + REGION,
         "-JL105/35/25/47/18c", "-JZ2.8c", f"-p{az}/{el}", "-Qc",
         "-Cgeo", "-I+d", "-Bafg", "-BWSne"],
        [str(GMT), "plot", rel_outline, "-R" + REGION,
         "-JL105/35/25/47/18c", "-JZ2.8c", f"-p{az}/{el}", "-W0.8p,#f5d76e"],
        [str(GMT), "plot", rel_provinces, "-R" + REGION,
         "-JL105/35/25/47/18c", "-JZ2.8c", f"-p{az}/{el}", "-W0.12p,#5f9bb8"],
        [str(GMT), "end"],
    ]
    for cmd in cmds:
        p = subprocess.run(cmd, cwd=str(config.ROOT), env=env,
                           capture_output=True, timeout=120)
        if p.returncode != 0:
            err = (p.stderr or p.stdout or b"").decode("utf-8", "replace")
            raise RuntimeError(f"GMT 渲染失败：{err[-500:]}")
    if not out.exists():
        raise RuntimeError("GMT 未生成输出文件")
    return out

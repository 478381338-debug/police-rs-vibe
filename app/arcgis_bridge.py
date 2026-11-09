"""ArcGIS Pro 专题图桥：调用 ArcGIS Pro Python 运行 arcgis/export_map.py。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app import config

ARCGIS_PY = Path(r"C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe")


def _decode(bs: bytes) -> str:
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return bs.decode(enc)
        except Exception:
            continue
    return repr(bs)


def run_arcgis_export() -> dict:
    if not ARCGIS_PY.exists():
        return {"ok": False, "error": f"未找到 ArcGIS Pro Python：{ARCGIS_PY}"}
    script = config.ARCGIS_DIR / "export_map.py"
    if not script.exists():
        return {"ok": False, "error": f"未找到导出脚本：{script}"}
    try:
        p = subprocess.run(
            [str(ARCGIS_PY), str(script)],
            cwd=str(config.ROOT),
            capture_output=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ArcGIS 导出超时（300s）"}
    out = _decode(p.stdout or b"")
    err = _decode(p.stderr or b"")
    png = config.ASSETS / "arcgis_risk_map.png"
    if p.returncode != 0 or not png.exists():
        return {"ok": False, "error": (err or out or f"ArcGIS 退出码 {p.returncode}")[-3000:], "output": out[-3000:]}
    return {
        "ok": True,
        "url": "/assets/arcgis_risk_map.png",
        "file": str(png),
        "size": png.stat().st_size,
        "output": out[-3000:],
    }

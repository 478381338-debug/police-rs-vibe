"""MATLAB 风险模型桥：运行 risk_model.m，然后用新权重重算全部高发点。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from app import config, risk


def _decode(bs: bytes) -> str:
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return bs.decode(enc)
        except Exception:
            continue
    return repr(bs)


def _matlab_exe() -> str | None:
    env = os.environ.get("MATLAB_EXE")
    if env and Path(env).exists():
        return env
    found = shutil.which("matlab")
    if found:
        return found
    for p in (r"C:\Program Files\MATLAB\R2025b\bin\matlab.exe",):
        if Path(p).exists():
            return p
    return None


def run_matlab_and_rebuild() -> dict:
    exe = _matlab_exe()
    if not exe:
        return {"ok": False, "error": "未找到 MATLAB 可执行文件"}
    script_dir = config.MATLAB_DIR
    cmd = [exe, "-sd", str(script_dir), "-batch", "risk_model"]
    try:
        p = subprocess.run(
            cmd,
            cwd=str(config.ROOT),
            capture_output=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "MATLAB 运行超时（300s）"}
    out = _decode(p.stdout or b"")
    err = _decode(p.stderr or b"")
    if p.returncode != 0:
        return {"ok": False, "error": (err or out or f"MATLAB 退出码 {p.returncode}")[-3000:], "output": out[-3000:]}

    weights_path = config.DATA / "model_weights.json"
    if not weights_path.exists():
        return {"ok": False, "error": "MATLAB 未生成 model_weights.json", "output": out[-3000:]}
    try:
        wj = json.loads(weights_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"权重文件解析失败：{e}", "output": out[-3000:]}

    # 用 MATLAB 产出的权重重新计算全部高发点与格网
    s = risk.run()
    return {
        "ok": True,
        "message": f"MATLAB AHP 重算完成：CR={wj.get('consistency_ratio', '--')}，{s.get('hotspots', 0)} 个高发点",
        "weights": wj.get("weights"),
        "consistency_ratio": wj.get("consistency_ratio"),
        "summary": s,
        "output": out[-3000:],
    }

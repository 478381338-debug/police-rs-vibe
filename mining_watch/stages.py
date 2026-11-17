"""可选阶段：设备目标检测（YOLO）与 InSAR 形变。

这两个阶段依赖较重，且 InSAR 在 Windows 原生跑不起来。本模块只做
「环境检查 + 明确指引 + 能跑就跑」，绝不假装跑过。
"""
from __future__ import annotations

import shutil
import subprocess
import sys


def check_yolo() -> tuple[bool, str]:
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        return False, (
            "未安装 ultralytics，设备目标检测不可用。\n"
            "安装：\n"
            f'  "{sys.executable}" -m pip install ultralytics -i '
            "https://pypi.tuna.tsinghua.edu.cn/simple\n"
            "用途：检测影像中的挖掘机、自卸卡车、采砂船、传送带。\n"
            "注意：COCO 预训练模型没有『挖掘机』类，需要用矿业影像自建数据集微调，"
            "或先用 open-vocabulary 模型（YOLO-World / GroundingDINO）做零样本。"
        )
    import ultralytics

    return True, f"ultralytics {ultralytics.__version__} 可用"


def check_insar() -> tuple[bool, str]:
    """检查 InSAR 运行条件。Windows 原生不可行，必须有 WSL2 或 Docker。"""
    wsl = shutil.which("wsl")
    docker = shutil.which("docker")
    lines = []

    if wsl:
        try:
            r = subprocess.run(
                ["wsl", "-l", "-q"], capture_output=True, text=True, timeout=20
            )
            distros = [d for d in r.stdout.replace("\x00", "").split() if d]
            lines.append(f"WSL 已安装，发行版：{', '.join(distros) or '（无）'}")
        except Exception as e:
            lines.append(f"WSL 检测失败：{e}")
    else:
        lines.append("未检测到 WSL")

    lines.append(f"Docker：{docker or '未安装'}")

    if not wsl and not docker:
        lines.append(
            "结论：InSAR 不可用。Windows 原生无法安装 ISCE2/GMTSAR/MintPy 依赖链。\n"
            "建议：wsl --install -d Ubuntu，然后装 PyGMTSAR（有官方 Docker 镜像）。\n"
            "PyGMTSAR 一条命令出形变：\n"
            "  docker run -it -v /data:/data alexeypechnikov/pygmtsar\n"
            "  python -c \"from pygmtsar import S1, Stack; ...\""
        )
        return False, "\n".join(lines)

    lines.append(
        "结论：具备 InSAR 运行基础。推荐顺序：\n"
        "  1) PyGMTSAR（最省事，自带 SBAS/PSI，Docker 镜像）\n"
        "  2) MintPy（业界标准时序分析，配 ISCE2/Hyp3 产品）\n"
        "  3) Dolphin（ISCE3 系，新，速度快）\n"
        "数据：Sentinel-1 IW SLC，ASF 免费下载（需 Earthdata 账号）。\n"
        "烟台注意：C 波段在果园/林地失相干，需按地类分层设相干阈值（裸岩 0.3，"
        "植被 0.5 以上才可信），并优先用冬季影像。"
    )
    return True, "\n".join(lines)


def run_yolo(aoi_bbox, out_dir) -> None:
    ok, msg = check_yolo()
    print(f"[yolo] {msg}")
    if not ok:
        return
    print(
        "[yolo] 已具备推理环境。零样本可先用 YOLO-World：\n"
        "  from ultralytics import YOLOWorld\n"
        "  m = YOLOWorld('yolov8x-worldv2.pt')\n"
        "  m.set_classes(['excavator','dump truck','sand dredger','conveyor belt'])\n"
        "  m.predict(source='quicklook.png', save=True)"
    )


def run_insar(aoi_bbox, out_dir) -> None:
    ok, msg = check_insar()
    print(f"[insar] {msg}")

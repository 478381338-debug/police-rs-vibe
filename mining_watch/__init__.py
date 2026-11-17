"""mining_watch —— 非法采矿/采砂/采石 遥感预警流水线。

阶段划分（可用 --stage 单独执行）：
    fetch      STAC 检索 + COG 窗口读取
    indices    光谱指数（BSI / NDVI / NDBI / MNDWI / 铁染 / 羟基）
    detect     基线合成 + 时序变化检测 + 图斑矢量化
    report     输出 GeoJSON / CSV / PNG / 摘要
    yolo       设备目标检测（需 ultralytics，未安装时给出提示）
    insar      InSAR 形变（需 WSL/Linux + PyGMTSAR 或 MintPy）
"""

__version__ = "0.1.0"

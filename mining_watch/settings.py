"""配置加载与数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config_yantai.yaml"


@dataclass
class Thresholds:
    delta_bsi: float = 0.15
    ndvi_drop: float = 0.15
    bsi_abs: float = 0.35
    z: float = 3.0
    min_area_m2: float = 3000.0
    max_mndwi: float = 0.0


@dataclass
class Aoi:
    key: str
    name: str
    bbox: tuple[float, float, float, float]


@dataclass
class Config:
    stac_endpoint: str
    collection: str
    max_cloud: float
    max_scenes: int
    target: str
    baseline: str
    resolution: int
    out_dir: Path
    thresholds: Thresholds
    aois: dict[str, Aoi] = field(default_factory=dict)
    climate: dict[str, Any] = field(default_factory=dict)
    aoi_types: dict[str, str] = field(default_factory=dict)

    def aoi(self, key: str) -> Aoi:
        if key not in self.aois:
            raise KeyError(f"未知区域 '{key}'，可选：{', '.join(self.aois)}")
        return self.aois[key]


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8"))

    stac = raw.get("stac", {})
    dates = raw.get("dates", {})
    cfg = Config(
        stac_endpoint=stac.get("endpoint", "https://earth-search.aws.element84.com/v1"),
        collection=stac.get("collection", "sentinel-2-l2a"),
        max_cloud=float(stac.get("max_cloud", 20)),
        max_scenes=int(stac.get("max_scenes", 3)),
        target=dates["target"],
        baseline=dates["baseline"],
        resolution=int(raw.get("resolution", 20)),
        out_dir=(ROOT / raw.get("out_dir", "out")).resolve(),
        thresholds=Thresholds(**raw.get("thresholds", {})),
        climate=dict(raw.get("climate") or {}),
        aoi_types={str(k): str(v) for k, v in (raw.get("aoi_types") or {}).items()},
    )
    for key, spec in raw.get("aois", {}).items():
        cfg.aois[key] = Aoi(key=key, name=spec.get("name", key), bbox=tuple(spec["bbox"]))
    return cfg

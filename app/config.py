"""全局配置：区域、路径、危险等级、灾害目录。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB = ROOT / "web"
THUMBS = DATA / "thumbs"
ASSETS = WEB / "assets"
MATLAB_DIR = ROOT / "matlab"
ARCGIS_DIR = ROOT / "arcgis"

for p in (DATA, WEB, THUMBS, ASSETS):
    p.mkdir(parents=True, exist_ok=True)

APP_TITLE = "警务遥感 · 生态安全与灾害风险遥感地图"
APP_SUBTITLE = "生态安全与灾害风险遥感地图 · 卫星发现 → 警务核查 → 证据闭环"

# 研究区：由 53 个历史异常图斑的外接范围确定，略向外扩
AOI_BBOX = [108.90, 39.95, 112.80, 41.58]
AOI_CENTER = [111.05, 40.90]
AOI_ZOOM = 8
AOI_NAME = "大青山自然保护区"

# 历史检测结果随仓库提供，保证工程自包含。
SOURCE_INCIDENTS = DATA / "source_task2_pred.geojson"

# 注意：这里的 red/orange/yellow/blue 是“核查优先级”的配色键，不是灾害预警颜色。
# 一级/二级/三级只表示历史异常线索的处置先后，不表示灾害发生概率。
DANGER_LEVELS = [
    {"key": "red", "label": "一级核查", "min": 70, "color": "#ff3b30", "advice": "优先安排核查"},
    {"key": "orange", "label": "二级核查", "min": 60, "color": "#ff9500", "advice": "近期安排核查"},
    {"key": "yellow", "label": "三级核查", "min": 50, "color": "#ffd60a", "advice": "结合巡护计划核查"},
    {"key": "blue", "label": "常态关注", "min": 0, "color": "#0a84ff", "advice": "纳入日常巡查"},
]


def level_for_score(score: float) -> dict:
    for lv in DANGER_LEVELS:
        if score >= lv["min"]:
            return lv
    return DANGER_LEVELS[-1]


# 灾害/风险目录：5 个已验证类别 + 12 个扩展筛查/风险类别
HAZARDS = [
    {
        "id": "fire_scar",
        "name": "火烧迹地",
        "short": "火烧",
        "category": "criminal",
        "status": "validated",
        "color": "#ff4d4f",
        "severity": 0.90,
        "police": "森林火灾 / 纵火线索",
        "desc": "过火后近红外反射率骤降，dNBR 与 B08 降幅可定位火场边界。",
        "source": "青山卫士双通道检测（官方口径 19.34/25）",
        "indicators": ["dNBR", "ΔB08", "SCL 云掩膜"],
    },
    {
        "id": "mining",
        "name": "盗采痕迹",
        "short": "盗采",
        "category": "criminal",
        "status": "validated",
        "color": "#a50026",
        "severity": 1.00,
        "police": "非法采矿 / 破坏性开采线索",
        "desc": "开挖裸土使红光与近红外同时升高，平坦地形是与滑坡的物理分界。",
        "source": "青山卫士双通道检测（事件级 F1 1.000）",
        "indicators": ["ΔB04", "ΔB08", "坡度 ≤10°"],
    },
    {
        "id": "landslide",
        "name": "滑坡",
        "short": "滑坡",
        "category": "disaster",
        "status": "validated",
        "color": "#f46d43",
        "severity": 0.70,
        "police": "地质灾害 / 道路阻断 / 人员被困",
        "desc": "陡坡裸土扰动，RX 多波段异常 + 坡度 ≥18° + 滞回生长。",
        "source": "青山卫士双通道检测（事件级 F1 1.000）",
        "indicators": ["RX d²", "坡度 ≥18°", "NDVI"],
    },
    {
        "id": "debris_flow",
        "name": "泥石流",
        "short": "泥石流",
        "category": "disaster",
        "status": "validated",
        "color": "#e8a33d",
        "severity": 0.70,
        "police": "地质灾害 / 沟道下游安全",
        "desc": "沟道型大范围扰动，RX + 局部高差 ≥5 m + 坡度 0.5°~18°。",
        "source": "青山卫士双通道检测（事件级 F1 1.000）",
        "indicators": ["RX d²", "沟道高差", "坡度带"],
    },
    {
        "id": "snowline",
        "name": "雪线变化",
        "short": "雪线",
        "category": "ecology",
        "status": "validated",
        "color": "#74add1",
        "severity": 0.40,
        "police": "生态趋势 / 边境通行 / 气候变化",
        "desc": "积雪→裸地变化，高程 ≥900 m，跨瓦片归并后输出。",
        "source": "青山卫士双通道检测（IoU 0.450 / F1 0.571）",
        "indicators": ["NDSI", "SCL", "高程 ≥900 m"],
    },
    {
        "id": "construction",
        "name": "违规建设",
        "short": "违建",
        "category": "screening",
        "status": "screening",
        "color": "#c084fc",
        "severity": 0.75,
        "police": "违建 / 非法占地线索",
        "desc": "建筑指数升高 + 平坦地形 + 靠近道路/村庄，作为待核查筛查层。",
        "source": "扩展规则筛查（待样本验证）",
        "indicators": ["NDBI", "坡度", "路网距离"],
    },
    {
        "id": "dumping",
        "name": "非法倾倒",
        "short": "倾倒",
        "category": "screening",
        "status": "screening",
        "color": "#fb7185",
        "severity": 0.75,
        "police": "固废/危废非法倾倒线索",
        "desc": "裸土/低植被 + 平坦 + 靠近道路，作为待核查筛查层。",
        "source": "扩展规则筛查（待样本验证）",
        "indicators": ["BSI", "NDVI", "路网距离"],
    },
    {
        "id": "water_anomaly",
        "name": "水体异常",
        "short": "水体",
        "category": "screening",
        "status": "screening",
        "color": "#22d3ee",
        "severity": 0.70,
        "police": "水污染 / 非法排污线索",
        "desc": "水体指数与色度异常，结合低洼地形与人类活动密度筛查。",
        "source": "扩展规则筛查（待样本验证）",
        "indicators": ["NDWI", "水体色度", "低洼"],
    },
    {
        "id": "wetland",
        "name": "湿地侵占",
        "short": "湿地",
        "category": "screening",
        "status": "screening",
        "color": "#34d399",
        "severity": 0.65,
        "police": "湿地/水域侵占线索",
        "desc": "水体/湿地范围收缩，结合低洼与平坦地形筛查。",
        "source": "扩展规则筛查（待样本验证）",
        "indicators": ["NDWI 变化", "土地覆盖", "低洼"],
    },
    {
        "id": "veg_degradation",
        "name": "植被退化",
        "short": "植被",
        "category": "ecology",
        "status": "screening",
        "color": "#a3e635",
        "severity": 0.55,
        "police": "生态破坏 / 非法砍伐线索",
        "desc": "NDVI 趋势下降，结合干旱与坡面位置筛查。",
        "source": "扩展规则筛查（待样本验证）",
        "indicators": ["NDVI 趋势", "LST", "降水"],
    },
    {
        "id": "bare_soil",
        "name": "裸土扩张",
        "short": "裸土",
        "category": "screening",
        "status": "screening",
        "color": "#fbbf24",
        "severity": 0.60,
        "police": "生态破坏 / 非法占地线索",
        "desc": "裸土指数升高，结合干旱、坡面与道路可达性筛查。",
        "source": "扩展规则筛查（待样本验证）",
        "indicators": ["BSI", "NDVI", "路网距离"],
    },
    {
        "id": "road_intrusion",
        "name": "道路侵入",
        "short": "道路",
        "category": "screening",
        "status": "screening",
        "color": "#94a3b8",
        "severity": 0.55,
        "police": "林区道路 / 非法通行线索",
        "desc": "新增道路或车辆可达性异常，结合路网与坡面筛查。",
        "source": "扩展规则筛查（待样本验证）",
        "indicators": ["路网距离", "坡度", "NDVI"],
    },
    {
        "id": "mining_damage",
        "name": "矿山生态破坏",
        "short": "矿山",
        "category": "criminal",
        "status": "screening",
        "color": "#f97316",
        "severity": 0.85,
        "police": "矿山生态破坏 / 非法开采线索",
        "desc": "裸土扩张 + 道路可达 + 历史盗采密度，扩展盗采的外围影响。",
        "source": "扩展规则筛查（待样本验证）",
        "indicators": ["BSI", "历史盗采", "路网"],
    },
    {
        "id": "fire_risk",
        "name": "火灾风险",
        "short": "火险",
        "category": "risk",
        "status": "risk",
        "color": "#ef4444",
        "severity": 0.80,
        "police": "森林火灾预防 / 火源管控",
        "desc": "高温、低湿、大风、少雨叠加，输出火险等级供巡逻布防。",
        "source": "Open-Meteo 实时气象 + 地形 + 历史火点",
        "indicators": ["温度", "湿度", "风速", "降水"],
    },
    {
        "id": "flood_risk",
        "name": "洪涝风险",
        "short": "洪涝",
        "category": "risk",
        "status": "risk",
        "color": "#3b82f6",
        "severity": 0.70,
        "police": "防汛应急 / 道路管控 / 疏散",
        "desc": "低洼、平坦、强降水叠加，输出洪涝风险格网。",
        "source": "Open-Meteo 实时气象 + 地形",
        "indicators": ["降水", "土壤湿度", "高程", "坡度"],
    },
    {
        "id": "drought_risk",
        "name": "干旱风险",
        "short": "干旱",
        "category": "risk",
        "status": "risk",
        "color": "#eab308",
        "severity": 0.55,
        "police": "干旱应急 / 生态风险预警",
        "desc": "高温、低湿、少雨、低土壤湿度叠加。",
        "source": "Open-Meteo 实时气象",
        "indicators": ["温度", "降水", "土壤湿度"],
    },
    {
        "id": "geohazard_risk",
        "name": "地质灾害隐患",
        "short": "地灾",
        "category": "risk",
        "status": "risk",
        "color": "#8b5cf6",
        "severity": 0.75,
        "police": "地质灾害防范 / 道路与人员安全",
        "desc": "陡坡、强降水、历史灾害叠加，输出隐患格网。",
        "source": "Open-Meteo 实时气象 + 地形 + 历史灾害",
        "indicators": ["坡度", "降水", "历史灾害"],
    },
]

HAZARD_BY_ID = {h["id"]: h for h in HAZARDS}
VALIDATED_HAZARDS = [h for h in HAZARDS if h["status"] == "validated"]

# 默认 AHP 权重（MATLAB 会覆盖；作为 MATLAB 不可用时的兜底）
DEFAULT_WEIGHTS = {
    "severity": 0.22,
    "count": 0.16,
    "area": 0.12,
    "recurrence": 0.12,
    "terrain": 0.12,
    "access": 0.12,
    "environment": 0.12,
    "confidence": 0.02,
}

# 气象采样点（覆盖保护区西/中/东段）
WEATHER_POINTS = [
    {"name": "西段", "lon": 110.20, "lat": 40.80},
    {"name": "中西段", "lon": 110.80, "lat": 40.90},
    {"name": "中段", "lon": 111.20, "lat": 40.90},
    {"name": "中东段", "lon": 111.80, "lat": 41.00},
    {"name": "东段", "lon": 112.40, "lat": 41.00},
]

WEATHER_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "soil_moisture_0_to_10cm",
]


def data_url(name: str) -> str:
    """把 data/ 下的文件映射成 FastAPI /data 静态路径。"""
    return f"/data/{name}"

# 警务遥感 · UI 与开源参考（真实查证）

> 查证日期：2026-11-06。星数来自 GitHub API，会随时间变化。
> 原则：**优先使用高星、维护活跃的通用库；警务/应急领域参考开源项目的交互模式，不直接复制代码。**

## 1. 实际使用的项目

| 项目 | 星数（查证时） | 在本项目中的用途 |
|---|---:|---|
| [Leaflet/Leaflet](https://github.com/Leaflet/Leaflet) | 45,865 | 交互地图、图层、标注、弹窗（本地 vendored，无 CDN） |
| [apache/echarts](https://github.com/apache/echarts) | 67,689 | 自然条件推演的“未来 14 天危险系数”时间轴图（平滑曲线、阈值线、峰值标记、tooltip） |
| 自研 Canvas 兜底 | — | ECharts 不可用时仍能画时间轴 |

## 2. 警务/应急领域参考

| 项目 | 星数（查证时） | 学习到的模式 | 已落地的设计 |
|---|---:|---|---|
| [Chicago/opengrid](https://github.com/Chicago/opengrid) | 268 | 城市态势感知、事件监控/响应、历史数据检索、实时分析；图层与事件联动 | 顶部警务状态条、今日重点警情、图层开关、异常/预警分栏 |
| [krrishrastogi05/Argus.AI](https://github.com/krrishrastogi05/Argus.AI) | 未标注 | 指挥中心、实时事件地图、资产跟踪、智能派单 | 高发点风险排序、就近警务点、生成核查任务 |
| [t474-r0b07/SCCP-DTEX](https://github.com/t474-r0b07/SCCP-DTEX) | 未标注 | 战术级实时执法监控、深色地图 | 深蓝/黑底、红/金警务配色、地图优先布局 |
| [Varil426/AGH-Police-Support-System](https://github.com/Varil426/AGH-Police-Support-System) | 未标注 | PostGIS/OSM、警务单位管理 | 空间库、警务设施/巡护路线图层 |
| [jonasCTDOL/fronteira-digital](https://github.com/jonasCTDOL/fronteira-digital) | 未标注 | 战术警务地图、GeoJSON 交换 | 标准 GeoJSON/PostGIS 数据链路 |

> 说明：警务领域的开源项目普遍星数不高（很多是课程/论文/黑客松项目），所以“高星优先”主要用在通用基础设施（Leaflet、ECharts、Tabler 等）；领域项目用于学习业务流程和交互模式，不直接照搬代码。

## 3. 高星仪表盘/可视化参考（设计模式）

| 项目 | 星数（查证时） | 学习到的模式 | 是否直接使用 |
|---|---:|---|---|
| [tabler/tabler](https://github.com/tabler/tabler) | 42,015 | 卡片、状态徽章、侧栏、数据密度、深浅色设计 | 借鉴布局/状态样式，未引入框架 |
| [ColorlibHQ/AdminLTE](https://github.com/ColorlibHQ/AdminLTE) | 45,675 | 管理后台信息层级、KPI 卡片、状态色 | 借鉴信息层级 |
| [visgl/deck.gl](https://github.com/visgl/deck.gl) | 14,738 | 大规模地理图层、GPU 聚合、3D | 未引入（需要 Node 构建，后续可选） |
| [keplergl/kepler.gl](https://github.com/keplergl/kepler.gl) | 12,082 | 时空过滤、图层控制、动画 | 借鉴时间轴/图层交互；未引入 |

## 4. 模型方法参考（详见 docs/MODEL.md）

| 项目 | 用途 |
|---|---|
| [cffdrs](https://github.com/gagreene/cffdrs) | 加拿大 FWI 火险因子 |
| [cffdrs-ng](https://github.com/nrcan-cfs-fire/cffdrs-ng) | 官方下一代 CFFDRS/FWI2025 |
| [climate_indices](https://github.com/monocongo/climate_indices) | SPI/SPEI 干旱指数 |
| [PyLandslide](https://github.com/WRHGroup/PyLandslide) | 滑坡敏感性 + 不确定性 |
| [LSAT](https://github.com/BGR-EGHA/LSAT) | 滑坡敏感性评估工具套件 |
| [pysheds](https://github.com/mdbartos/pysheds) | DEM 水文/汇流 |
| [torchgeo](https://github.com/microsoft/torchgeo) | 遥感深度学习 |

## 5. 本次实际落地的 UI 改动

1. **警务品牌**：页面标题改为“警务遥感 · 生态安全与灾害风险一张图”，头部/启动页/推演抽屉加入自绘警务风格盾形徽章（**不是官方警徽，不使用官方标志**）。
2. **指挥中心配色**：深海军蓝底 + 金色/警徽红点缀；顶部红金线、金色描边、状态徽章。
3. **警务状态条**：实时时钟、红色预警数、高发点数。
4. **今日重点警情**：自动取最高风险高发点，显示“名称 · 等级 · 风险分”，点击定位并打开详情。
5. **ECharts 时间轴**：危险系数平滑曲线、50/60/70 阈值虚线、峰值标记、tooltip、文字颜色随等级变化。
6. **推演抽屉**：深蓝/红金配色、盾形徽章、输入表单、仪表盘、时间轴、播放/暂停/速度/滑块、地图逐日格网动画。
7. **保留高密度信息**：左侧态势/图层/高发点，右侧详情/自然数据/气象/MATLAB 模型，底部数据源说明。

## 6. 未采用/后续可升级

- **deck.gl / kepler.gl**：需要 Node 构建、体积较大；当前用 Leaflet + ECharts 已能满足演示。若后续要做 GPU 级十万级图元/3D 热力，再引入。
- **Tabler / AdminLTE**：整框架会替换现有 UI；只借鉴设计模式，避免破坏已完成的警务配色与地图布局。
- **机器学习模型**：当前是物理经验 + AHP；若要用 scikit-learn 做本地概率校准，需要足够历史样本并做空间交叉验证，不能只用 53 个点硬训练后宣称泛化精度。

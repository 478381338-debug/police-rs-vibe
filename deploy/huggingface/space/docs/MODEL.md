# 自然条件推演模型说明（MODEL）

> 对应网页右上角“自然条件推演”和 API `/api/scenario/assess`、`/api/scenario/timeline`。
> 代码：`app/scenario.py`；前端：`web/scenario.js` + `web/scenario.css`。

## 1. 模型定位

这是一个**早期预警指数**，不是确定性的灾害发生预报，也不是经过本地历史样本校准的概率模型。

- 输出 0~100 的“危险系数”和红/橙/黄/蓝等级；
- 未来 14 天逐日推演，给出“首次达到橙色/红色”的日期；
- 研究区 180 个格网逐日重算，形成时空动画；
- 所有因子、权重、公式可解释，便于民警/评审追问“为什么是这个分数”。

## 2. 输入

| 输入 | 含义 | 默认来源 |
|---|---|---|
| 点位 lon/lat | 推演位置 | 地图中心或用户点选 |
| 灾种 | 火灾/滑坡/泥石流/洪涝/干旱/地灾/盗采矿山 | 用户选择 |
| 气温 °C | 当前/未来日气温 | Open-Meteo 当前/逐小时预报 |
| 相对湿度 % | 当前/未来日湿度 | Open-Meteo |
| 风速 km/h | 当前/未来日最大风速 | Open-Meteo |
| 当日降雨 mm | 当前/未来日降水 | Open-Meteo |
| 土壤湿度 m³/m³ | 0~10 cm 土壤含水量 | Open-Meteo |
| 前 3/7 天累计降雨 mm | 前期降雨（用户可改） | 用户输入，默认 0 |
| 坡度 ° | 地形坡度 | 最近的 0.2° 风险格网 |
| 高程 m | 海拔 | 风险格网 / Open-Meteo elevation |
| NDVI 0~1 | 植被/可燃物代理 | 默认 0.45，可改 |
| 距道路 km | 人类活动/可达性 | 风险格网 |
| 历史异常数 | 最近高发点的事件数 | 高发点数据 |
| 降雨倍率 | 情景：未来降雨 × 倍率 | 滑块 0.5~3.0 |
| 温度偏移 | 情景：未来气温 + 偏移 | 滑块 -8~+12°C |
| 风速倍率 | 情景：未来风速 × 倍率 | 滑块 0.5~2.0 |
| 推演天数 | 7 或 14 天 | 用户选择 |

## 3. 隶属函数

把每个因子映射到 0~1：

- **sigmoid**：`m = 1 / (1 + exp(-k(x - x0)))`
  - 用于“越高越危险”的连续变量，如气温、坡度。
- **线性/梯形**：`m = clamp(x / threshold)` 或 `1 - clamp(x / threshold)`。
  - 用于累计降雨、土壤湿度、NDVI、道路距离等。

## 4. 危险系数

```
score = 100 × Σ (w_i × m_i) / Σ w_i
```

`w_i` 为灾种专属权重，`m_i` 为隶属度。输出四舍五入到 0.1。

### 4.1 火灾风险（fire_risk）

| 因子 | 隶属度 | 权重 |
|---|---|---:|
| 高温 | `sigmoid(T, 0.12, 22)` | 0.20 |
| 低湿 | `1 - sigmoid(RH, 0.10, 35)` | 0.18 |
| 大风 | `clamp(W/40)` | 0.14 |
| 当日少雨 | `1 - clamp(P/10)` | 0.08 |
| 3 天累计少雨 | `1 - clamp(R3/20)` | 0.10 |
| 7 天累计少雨 | `1 - clamp(R7/40)` | 0.05 |
| 植被/可燃物 | `clamp(NDVI/0.65)` | 0.08 |
| 坡度 | `clamp(slope/30)` | 0.05 |
| 道路可达（人为火源） | `clamp(1 - road/10)` | 0.12 |

### 4.2 滑坡（landslide）

| 因子 | 隶属度 | 权重 |
|---|---|---:|
| 坡度 | `sigmoid(slope, 0.18, 16)` | 0.28 |
| 3 天累计降雨 | `clamp(R3/100)` | 0.24 |
| 短时雨强（max(当日, R3/3)） | `clamp(I/40)` | 0.14 |
| 7 天累计降雨 | `clamp(R7/180)` | 0.08 |
| 土壤湿度 | `clamp(SM/0.35)` | 0.12 |
| 历史异常密度 | `clamp(hist/5)` | 0.08 |
| 高程 | `clamp((elev-900)/1200)` | 0.03 |
| 道路可达 | `clamp(1 - road/20)` | 0.03 |

### 4.3 泥石流（debris_flow）

| 因子 | 隶属度 | 权重 |
|---|---|---:|
| 坡度 | `sigmoid(slope, 0.16, 14)` | 0.22 |
| 3 天累计降雨 | `clamp(R3/100)` | 0.25 |
| 雨强 | `clamp(max(P, R3/3)/40)` | 0.15 |
| 7 天累计降雨 | `clamp(R7/180)` | 0.06 |
| 土壤湿度 | `clamp(SM/0.35)` | 0.12 |
| 历史异常密度 | `clamp(hist/5)` | 0.08 |
| 沟谷/低洼 | `1 - clamp((elev-900)/1500)` | 0.07 |
| 道路可达 | `clamp(1 - road/20)` | 0.05 |

### 4.4 洪涝风险（flood_risk）

| 因子 | 隶属度 | 权重 |
|---|---|---:|
| 3 天累计降雨 | `clamp(R3/120)` | 0.30 |
| 雨强 | `clamp(max(P, R3/3)/50)` | 0.18 |
| 土壤湿度 | `clamp(SM/0.40)` | 0.15 |
| 低洼 | `1 - clamp((elev-800)/1200)` | 0.15 |
| 平坦 | `1 - clamp(slope/15)` | 0.10 |
| 历史异常密度 | `clamp(hist/5)` | 0.07 |
| 道路可达 | `clamp(1 - road/25)` | 0.05 |

### 4.5 干旱风险（drought_risk）

| 因子 | 隶属度 | 权重 |
|---|---|---:|
| 高温 | `sigmoid(T, 0.10, 25)` | 0.20 |
| 低湿 | `1 - clamp(RH/100)` | 0.15 |
| 7 天少雨 | `1 - clamp(R7/30)` | 0.20 |
| 土壤干燥 | `1 - clamp(SM/0.30)` | 0.18 |
| NDVI 低 | `1 - clamp(NDVI/0.60)` | 0.14 |
| 大风 | `clamp(W/40)` | 0.08 |
| 3 天少雨 | `1 - clamp(R3/20)` | 0.05 |

### 4.6 地质灾害隐患（geohazard_risk）

| 因子 | 隶属度 | 权重 |
|---|---|---:|
| 坡度 | `sigmoid(slope, 0.15, 18)` | 0.30 |
| 3 天累计降雨 | `clamp(R3/100)` | 0.24 |
| 土壤湿度 | `clamp(SM/0.35)` | 0.15 |
| 历史异常密度 | `clamp(hist/5)` | 0.15 |
| 高程 | `clamp((elev-900)/1200)` | 0.08 |
| 道路可达 | `clamp(1 - road/20)` | 0.08 |

### 4.7 盗采/矿山风险（mining，静态）

| 因子 | 隶属度 | 权重 |
|---|---|---:|
| 平坦 | `1 - clamp(slope/20)` | 0.25 |
| 道路可达 | `clamp(1 - road/10)` | 0.25 |
| 干燥 | `1 - clamp(SM/0.30)` | 0.15 |
| 历史盗采密度 | `clamp(hist/5)` | 0.20 |
| NDVI 低 | `1 - clamp(NDVI/0.60)` | 0.15 |

盗采是静态风险，时间推演只展示未来气象对周边环境的影响，不表示“哪天一定发生盗采”。

## 5. 未来 14 天时间推演

### 5.1 气象

调用 Open-Meteo：

```
hourly = temperature_2m, relative_humidity_2m, precipitation,
         wind_speed_10m, soil_moisture_0_to_10cm
forecast_days = 天数（≤14）
timezone = Asia/Shanghai
```

逐小时聚合成逐日：

- 气温：日均；
- 湿度：日均；
- 风速：日最大；
- 降雨：日累计；
- 土壤湿度：日均。

### 5.2 前期降雨

用户输入的“前 3 天/前 7 天累计降雨”作为初始条件，按天衰减加入：

```
R3(day) = R3_input × rain_factor × 0.70^day + 未来 3 天滚动降雨
R7(day) = R7_input × rain_factor × 0.85^day + 未来 7 天滚动降雨
```

这模拟前期降雨的排水/蒸散衰减，避免“今天没雨就完全没有滑坡风险”。

### 5.3 空间格网

研究区 180 个 0.2° 格网，逐格网做高程订正：

```
T_cell  = T_day - 0.0065 × (elev_cell - elev_point)
RH_cell = clamp(RH_day + (elev_cell - elev_point) × 0.008, 5, 100)
P_cell  = P_day × max(0.2, 1 + (elev_cell - elev_point)/2500)
SM_cell = clamp(SM_day + (elev_cell - elev_point) × 0.00003, 0.02, 0.5)
wind_cell = W_day × max(0.5, 1 + (elev_cell - elev_point)/3000)
```

坡度、高程、历史密度、道路距离取该格网的静态属性。
每个格网用同一套隶属度和权重计算危险系数，形成逐日 `frames`。

### 5.4 输出

- `point_series`：点位逐日危险系数；
- `frames`：逐日格网分数（180 格网/天）；
- `first_orange` / `first_red`：首次达到 60 / 70 的日期；
- `peak`：峰值分数与日期；
- `message`：中文结论；
- `warning`：气象接口降级提示。

网页把 `frames` 逐日渲染到 Leaflet 格网，形成播放/暂停/滑块的时空动画；顶部徽标显示日期和点位分数。

## 6. 参考的开源项目与方法

以下项目用于学习方法和模型结构；本实现是简化重写，未直接复制代码。使用前请逐一核对许可证。

| 项目 | 用途 | 链接 |
|---|---|---|
| cffdrs | 加拿大森林火险 FWI 计算（Python） | https://github.com/gagreene/cffdrs |
| cffdrs-ng | 官方下一代 CFFDRS/FWI2025（C/Python/R） | https://github.com/nrcan-cfs-fire/cffdrs-ng |
| climate_indices | SPI/SPEI 等干旱指数 | https://github.com/monocongo/climate_indices |
| PyLandslide | 滑坡敏感性制图 + 不确定性 | https://github.com/WRHGroup/PyLandslide |
| LSAT | 滑坡敏感性评估工具套件 | https://github.com/BGR-EGHA/LSAT |
| landslide_susceptibility | 坡度单元 + 机器学习滑坡敏感性 | https://github.com/iamtekson/landslide_susceptibility |
| pysheds | DEM 水文/汇流分析 | https://github.com/mdbartos/pysheds |
| Leaflet.TimeDimension | Leaflet 时间轴动画插件 | https://github.com/socib/Leaflet.TimeDimension |
| deck.gl | 大规模地理可视化 | https://github.com/visgl/deck.gl |
| kepler.gl | 高性能时空可视化 | https://github.com/keplergl/kepler.gl |
| torchgeo | 遥感深度学习数据集/模型 | https://github.com/microsoft/torchgeo |

### 本项目借鉴了什么

- **FWI 思路**：高温、低湿、大风、降水亏缺、可燃物共同决定火险；
- **ID 阈值思路**：滑坡/泥石流用“降雨强度 + 历时/累计降雨”触发；
- **SPI/SPEI 思路**：干旱用“降水亏缺 + 蒸散/高温”；
- **AHP/模糊综合评判**：多因子权重 + 隶属度；
- **时间轴动画**：Leaflet + 自研 Canvas 图表（未引入 TimeDimension，减少依赖）。

## 7. 已知边界

1. **不是确定性预报**：系数是风险指数，阈值 50/60/70 是预警分级，不是“发生率 70%”。
2. **没有本地历史率定**：53 个历史异常不足以训练可靠的滑坡/火险概率模型；当前权重是经验/AHP 权重。
3. **气象空间分辨率有限**：未来天气来自点位预报，格网按高程/坡度做订正，不是真正的分布式气象场。
4. **NDVI 未逐格网取数**：当前使用用户输入/默认值，后续可接 MODIS/Sentinel-2 逐格网 NDVI。
5. **盗采是静态风险**：时间轴只反映周边气象环境，不表示盗采发生时间。
6. **没有不确定性区间**：后续可加蒙特卡洛/贝叶斯区间或机器学习概率校准。

## 8. 下一步可升级

1. 用本地历史灾害清单做 **逻辑回归/GBM** 概率校准（scikit-learn，需交叉验证；不要把训练集精度当泛化精度）。
2. 接 **cffdrs** 的完整 FWI（FFMC/DMC/DC/ISI/BUI/FWI）替代简化火险。
3. 接 **climate_indices** 的 SPI/SPEI 替代简化干旱。
4. 用 **pysheds** 计算汇流/沟道，替代当前的“低洼/沟谷”代理。
5. 用 **ID 阈值** + 前期有效降雨（API）替换滑坡/泥石流经验权重。
6. 加入 **不确定性/置信度**：气象集合预报、参数扰动、格网尺度敏感性。
7. 用 **deck.gl/kepler.gl** 做更高级的 3D/粒子动画（需 Node 构建，体积较大）。

## 9. 复现

```bash
# 单点危险系数
curl -X POST http://127.0.0.1:8080/api/scenario/assess \
  -H "Content-Type: application/json" \
  -d '{"hazard":"landslide","lon":111.05,"lat":40.9,"rain_3d":80,"rain_7d":120,"rain_factor":2.5,"slope_deg":25,"soil_moisture":0.4}'

# 14 天时空帧
curl -X POST http://127.0.0.1:8080/api/scenario/timeline \
  -H "Content-Type: application/json" \
  -d '{"hazard":"landslide","lon":111.05,"lat":40.9,"days":14,"rain_3d":80,"rain_7d":120,"rain_factor":2.5,"slope_deg":25,"soil_moisture":0.4}'
```

也可以在网页右上角“自然条件推演”中直接操作。

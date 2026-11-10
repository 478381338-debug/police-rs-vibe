# 当前危险指数与多灾种方法（2026-11-07）

> 目标：回答“高风险模型能否根据现有地理数据判断当前危险指数、结合自然因素做预测”。
> 代码：`app/current_risk.py`；数据：Open-Meteo 气象 + 本地 DEM/坡度 + 人口暴露；不混入历史异常密度。

## 1. 现在的做法

### 1.1 与核查优先级分开

| 分数 | 回答的问题 | 输入 | 不是 |
|---|---|---|---|
| 核查优先级 `priority` | 历史线索先核查哪一处 | 53 个历史异常聚类、严重度、数量、面积、复发性、地形、可达性、置信度 | 不是灾害概率 |
| 当前危险指数 `current_danger` | 此时此刻自然条件 + 人口暴露有多危险 | 气温、湿度、风、短时雨、3/7 天雨、土壤湿度、坡度、高程、道路距离、MODIS NDVI、人口暴露 | 不是确定性预报/官方预警 |

### 1.2 六类自然致灾因子

`app/current_risk.py` 对每个 0.2° 格网调用 `scenario._score`，覆盖：

1. `fire_risk` 火灾风险（高温、低湿、大风、少雨、NDVI、坡度、道路可达）
2. `flood_risk` 洪涝风险（短时雨、3/7 天雨、低洼、土壤湿度）
3. `drought_risk` 干旱风险（少雨、高温、低湿、土壤湿度）
4. `geohazard_risk` 地质灾害隐患（坡度、雨、土壤湿度、高程）
5. `landslide` 滑坡（坡度、累计雨、短时雨强、土壤湿度）
6. `debris_flow` 泥石流（坡度、沟谷/高程、雨、土壤）

> 雪线变化不进入当前危险指数：它是生态趋势，不是急性灾害。违规建设/非法倾倒等也不进入自然危险指数，仍作为历史核查类别。

### 1.3 多灾种耦合与暴露修正

```text
composite = max(hazard_scores) + 0.25 * second_highest(hazard_scores)   # 上限 100
exposure_factor = 0.60 + 0.40 * max(exposure, log1p(population)/log1p(max_pop))
current_danger = composite/100 * exposure_factor * 100
```

- 最高灾种是主导灾种，次高灾种提供 25% 的耦合贡献，避免“只看一个灾种”；
- 人口暴露使用 WorldPop 2020 1 km 格网按 `cell_id` 聚合；
- 未来 14 天用 Open-Meteo 逐日预报重复上述计算，输出 `forecast_peak`、`forecast_peak_day`、`forecast_peak_hazard`。

### 1.4 当前结果（2026-11-07）

- 180 个格网：最近一次刷新后当前危险指数约 42.6–65.6；46 个“较高”（橙）、129 个“中等”（黄）、5 个“低”（蓝），无“高”（红）。该分布随 Open-Meteo 刷新变化。
- 14 天峰值约 36.3–72.8；峰值日示例 2026-11-10，主导火灾风险。
- 每个格网保留 `current_components`（六类分数）、`current_exposure`、`current_population`、`current_weather_time/point/source`；逐格网 NDVI 来自 MODIS MCD43A4 500m 每日 NBAR（16 天滚动反演窗口；180/180 有效，0 回退）。
- 高发点通过最近格网继承当前危险指数；历史核查优先级不被改写。
- `app/samples.py` 生成 83 个历史/背景核查样本（53 + 30），全部 `verified=false`，只用于弱标签回溯检查；Top4 回溯覆盖 22.6%，不能当作预测准确率。
- 气象集合：`scenario.fetch_ensemble()` 调用 Open-Meteo GFS `gfs_seamless`，当前实测返回 31 个成员；逐成员计算 14 天危险序列，输出 P10/P50/P90、`current_prob_high`、`current_prob_elevated` 与 `forecast_peak_p90`。这是**集合情景频率**，不是校准概率。集合不可用时退回 `_ensemble()` 的 40 次参数扰动，并在 `probability.type`/`ensemble_source` 中注明。
- 暴雨量级对照：`_daily_from_hourly()` 保留日内最大 3h/6h/12h/24h 滚动雨量；`RAINSTORM_LEVELS` 按 CMA 公开阈值（红 3h≥100mm、橙 3h≥50mm、黄 6h≥50mm、蓝 12h≥50mm）标记 `current_rainstorm_*`/`forecast_rainstorm_*`。这是量级对照，不是官方预警。
- 现场核查：把 `data/samples/verification_records.csv` 按 `sample_id` 填入 `field_verified/verified/confirmed` 后重跑 `app.samples`，才会把样本计入 `verified_count`；没有现场记录时保持未核实，不伪造准确率。

## 2. GitHub 方法调研（2026-11-07）

| 项目 | Stars | 许可 | 最近推送 | 可复用点 | 结论 |
|---|---:|---|---|---|---|
| `CLIMADA-project/climada_python` | 491 | GPL-3.0 | 2026-11-06 | 气候风险框架：hazard × exposure × vulnerability、影响函数、情景分析 | **方法参考**；框架重、GPL，不直接嵌入 |
| `cffdrs/cffdrs_py` | 17 | GPL-2.0 | 2026-10-27 | 加拿大森林火险 FWI（FFMC/DMC/DC/ISI/BUI/FWI） | 火险公式参考；需本地校准，GPL |
| `pysheds/pysheds` | 915 | GPL-3.0 | 2026-10-26 | DEM 汇流、流域、洼地填充 | 若以后做洪涝汇流可离线用；当前不引入 |
| `landlab/landlab` | 450 | MIT | 2026-11-06 | 地表过程、水文/侵蚀建模 | 过重；不引入 |
| `gee-community/geemap` | 4,067 | MIT | 2026-11-05 | GEE 遥感分析、NDVI/水体/火点 | 依赖 Google Earth Engine 账号，不适合内网 |
| `r-barnes/richdem` | 326 | GPL-3.0 | 2024-06-24 | 地形/水文分析 | 维护放缓；不引入 |
| `pysal/esda` | 263 | BSD-3-Clause | 2026-11-05 | Moran's I、LISA 空间聚集检验 | **离线验证**核查点/风险格网是否真的聚集 |
| `WRHGroup/PyLandslide` | 34 | GPL-3.0 | 2026-10-02 | ML 滑坡易发性 | 无本地标注，不能直接套用 |
| `BGR-EGHA/LSAT` | 45 | GPL-3.0 | 2025-09-29 | 滑坡易发性工具链 | 仅方法参考 |

## 3. 结论：直接使用 / 二次开发 / 自研

**当前阶段：保留自研的轻量可解释模型，不整体替换。**

理由：

1. 现有数据是**格网 + 气象 + 人口**，不是完整的灾害灾损样本；CLIMADA/PyLandslide 需要暴露资产、脆弱性曲线或标注样本，直接套用会产生“看起来科学、实际未校准”的分数。
2. CLIMADA 是 GPL-3.0，pysheds/cffdrs 也是 GPL；嵌入闭源警务产品前必须做许可合规审查。当前只复用**方法论**：`hazard × exposure × vulnerability`、多灾种耦合、情景/峰值分析。
3. `geemap` 依赖 Google Earth Engine，不适合公安内网/离线环境；`richdem` 维护放缓；`PyLandslide/LSAT` 没有本地训练标签。
4. 当前模型的所有因子、权重、时间戳、气象点都可追溯，便于民警质疑和复核；这是警务场景比“黑箱高精度”更重要的属性。

**建议的演化路线：**

- 短期（已完成）：六类自然因子 + 多灾种耦合 + 人口暴露 + 14 天峰值；
- 中期：用 `pysal/esda` 离线做 Moran's I/LISA，验证核查点与风险格网的空间聚集显著性；用 `cffdrs_py` 的 FWI 公式做火险对照实验（不直接依赖 GPL 代码）；
- 长期：有本地灾情/核查样本后，再评估 CLIMADA 或 PyLandslide 做概率/易发性模型，并做独立验证集；
- 洪涝专题：若要做汇流/淹没，优先用 `pysheds` 离线计算汇流累积量，再进入当前危险指数。

## 4. 边界

- 当前危险指数是**筛查值/情景估计**，不是灾害发生概率，不是官方预警；GFS 集合频率也未做本地校准；
- 14 天预报来自 Open-Meteo，存在模式误差；
- NDVI 为 MODIS MCD43A4 500m 每日 NBAR，但反演使用 16 天滚动窗口；城市/云污染格网仍有质量限制；
- 人口暴露是 2020 年 WorldPop 1 km 聚合，不代表实时人流；
- 没有本地历史灾情率定，阈值 75/60/45 是工程阈值，应随业务复核调整；`app/samples.py` 的 83 个样本是弱标签，尚不足以训练/验证概率模型。

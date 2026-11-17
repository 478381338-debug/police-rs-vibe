# GitHub 开源项目复用评估（2026-11-07）

> 目的：回答“哪些开源项目能直接用/二次开发、是否维护、部署难不难、哪些功能可复用”，并给出本项目的取舍。
> 数据来自 GitHub API（`api.github.com/repos/...`，2026-11-07 查询）；star 数会变化，部署难度是工程判断，不是仓库官方承诺。

## 1. 结论先行

**不要整体换底座。** 当前 `FastAPI + Leaflet + ECharts + 本地数据快照` 已跑通，换成 MapStore2/deck.gl 会重做前端；正确做法是“保留底座 + 只复用成熟组件”：

| 需求 | 建议 | 直接使用 / 二次开发 / 自研 |
|---|---|---|
| 公网访问控制 | Authelia（简单门禁）或 Authentik（完整身份/RBAC） | **直接使用**，不要自研登录 |
| 24/7 部署 | 云主机 + Docker Compose + Caddy/Nginx 反代 | **基于模板改**，不自研部署框架 |
| 公网演示 | 静态快照 + GitHub Pages（不含刷新/推演 API） | 直接托管 |
| 空间统计验证 | GeoPandas + PySAL/esda 离线做 Moran's I / LISA | 复用库，自研评分逻辑 |
| 栅格瓦片 | Titiler（需要时） | 直接使用；当前 MPC tilejson 代理够用 |
| 3D/大规模前端 | MapLibre GL JS / deck.gl | 已引入 MapLibre GL JS 做交互式 3D 中国地形；deck.gl 暂不引入 |
| 灾害模型 | PyLandslide / LSAT 仅作参考/离线验证 | 不直接替换现有可解释 AHP |

## 2. 已验证项目

| 项目 | Stars | 最近推送 | 许可 | 维护/部署判断 | 可复用功能 | 本项目结论 |
|---|---:|---|---|---|---|---|
| `Leaflet/Leaflet` | 45,874 | 2026-11-06 | BSD-2-Clause | 活跃；前端库，零后端部署 | 交互地图、图层、弹窗 | **已使用**，保留 |
| `apache/echarts` | 67,689 | 2026-10-13 | Apache-2.0 | 活跃；纯前端 | 时间轴、阈值线、图表 | **已使用**，保留 |
| `authelia/authelia` | 29,580 | 2026-11-06 | Apache-2.0 | 活跃；Docker/二进制/K8s；需反代配置，约 0.5–1 天 | SSO、TOTP/WebAuthn、按路径/角色访问规则 | **推荐直接使用**（简单门禁） |
| `goauthentik/authentik` | 26,260 | 2026-11-07 | 核心开源（仓库许可字段为 NOASSERTION，企业功能另授权） | 活跃；Docker Compose/K8s；约 1–2 天 | OIDC/SAML/LDAP、RBAC、管理后台、审计 | **推荐直接使用**（需要账号/角色/审计时） |
| `pocketbase/pocketbase` | 61,606 | 2026-10-26 | MIT | **已归档** | 单文件后端、SQLite、管理 UI | **不要用于新项目** |
| `geopandas/geopandas` | 5,294 | 2026-10-31 | BSD-3-Clause | 活跃；`pip install` 即可 | 矢量读写、空间连接、裁剪、缓冲区 | **离线分析复用**，不放进公网运行时 |
| `pysal/esda` | 263 | 2026-11-05 | BSD-3-Clause | 活跃；Python 库 | Moran's I、LISA、热点/冷点显著性 | **用于验证聚类**，不替代业务评分 |
| `developmentseed/titiler` | 1,206 | 2026-11-02 | MIT | 活跃；Docker/FastAPI | COG/STAC 动态栅格瓦片 | 当前不需要；数据量上来再用 |
| `maplibre/maplibre-gl-js` | 12,036 | 2026-11-06 | BSD-3-Clause（NOASSERTION 字段） | 活跃；前端库 | 矢量瓦片、3D 地形、GPU 渲染 | 暂不迁移；作为后续 3D 选项 |
| `visgl/deck.gl` | 14,741 | 2026-11-07 | MIT | 活跃；React/JS | 大规模点/热力/3D 图层 | 暂不迁移；数据量>10 万再评估 |
| `geosolutions-it/MapStore2` | 669 | 2026-11-06 | 开源（许可字段 NOASSERTION，需核对具体模块） | 活跃；Java/Tomcat + 前端，部署重 | 地图/仪表盘/GeoStory/3D | 不作为底座；只参考交互 |
| `WRHGroup/PyLandslide` | 34 | 2026-10-02 | GPL-3.0 | 活跃但生态小；桌面/离线 | ML 滑坡易发性 + 不确定性 | 只做离线参考；GPL 传染性需注意 |
| `BGR-EGHA/LSAT` | 45 | 2025-09-29 | GPL-3.0 | 维护中；QGIS/桌面工具链 | 滑坡易发性评估 | 只做方法参考，不嵌入网页 |

## 3. 为什么不全量采用这些项目

1. **MapStore2** 功能强，但需要 Java 容器、GeoServer/空间数据库和前端构建；对当前单机演示是过度工程。
2. **MapLibre** 已作为独立 3D 视图引入，不改写 Leaflet 2D 图层；deck.gl 与现有联动改造成本高，暂不引入。
3. **PyLandslide / LSAT** 需要滑坡样本、栅格因子和训练/验证流程；本项目没有本地标注样本，直接套用会产生“看起来科学但未校准”的分数。
4. **GeoPandas/esda** 很适合做离线验证：用 Moran's I/LISA 检查 31 个高发点是否真的空间聚集，而不是把“聚类半径 13 km”当作结论。它们不应进入公网请求路径。

## 4. 推荐落地架构

### A. 公网演示（无需登录、无需本机常开）

- 生成静态快照：预计算 `data/*.json/geojson`、PNG、Scenario 结果；
- 部署到 GitHub Pages/对象存储；
- 明确标注“OpenStreetMap 公开数据、非内部警力部署、非实时警情”；
- 刷新、MATLAB、ArcGIS 导出按钮改为“离线预生成”。

### B. 真实警务系统（推荐）

```
浏览器
  → Caddy/Nginx（TLS、限流、审计日志）
  → Authelia 或 Authentik（登录、MFA、RBAC）
  → FastAPI（本项目）
  → PostgreSQL/PostGIS + 对象存储（业务数据）
  → 定时任务（MPC/气象抓取、风险重算）
```

- 部署在单位内网或私有云；公网只暴露登录入口；
- Authelia 适合“只加一道登录门”；Authentik 适合“部门/角色/审计/多应用”；
- 不要把真实警力点位、案件坐标、人员信息放到当前无鉴权的 Tailscale 页面。

## 5. 当前项目的取舍

- 保留：FastAPI、Leaflet、ECharts、AHP 可解释评分、MPC/Open-Meteo 抓取、ArcGIS 2D 导出、DEM 透视。
- 新增（已完成）：历史核查优先级与当前自然风险分离；自然风险不再混入历史密度；雪线趋势不进入急性灾害分数；天气点/时间可追溯。
- 已引入：MapLibre GL JS（交互式 3D）。暂不引入：MapStore2、deck.gl、Titiler、PyLandslide/LSAT；等数据量或样本条件满足再评估。
- 认证：按 Authelia/Authentik 直接部署，不自研账号密码体系。

## 6. 自然因素情景推演：实际使用与仅参考

| 来源/方法 | 在本项目中的处理 | 说明 |
|---|---|---|
| Open-Meteo Forecast / GFS Ensemble | **实际调用** | 真实预报基线与集合成员；`app/scenario.py`、`app/current_risk.py` |
| 中国气象局暴雨量级（3h/6h/12h 50/100 mm 等） | **量级对照** | 只用于提示情景雨量级别，不冒充官方预警 |
| 中国降雨型滑坡/泥石流经验阈值 | **评分因子参考** | 短时雨强、前期降雨、土壤含水量进入透明评分 |
| `cffdrs` / `cffdrs_py`（FWI） | **方法参考，未运行** | 火险天气指数思路；本项目使用简化可解释因子 |
| SPI / SPEI | **方法参考，未运行** | 需要长序列降水/蒸散；当前只有短期预报，不做伪 SPI |
| CLIMADA | **框架参考，未运行** | 采用“致灾因子 × 暴露”表达；不引入 GPL 重型依赖 |
| rainfall ID / 阈值判别 | **方法参考** | 用于“触发条件扫描”的产品化表达；扫描结果是敏感性，不是官方阈值 |

结论：情景页显示的是“如果自然条件这样变化”的模型敏感性结果；真实基线来自 Open-Meteo，其他方法是评分/表达参考。没有把未运行的模型包装成已运行结果。

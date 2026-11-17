# 警务遥感 · 生态安全与灾害风险一张图

> 基于“青山卫士——大青山自然保护区生态异常智能监测预警系统”的警务化版本，页面品牌为“警务遥感”。
> 用遥感地图直观展示地理环境、历史异常、高发地点与危险等级；接入 Microsoft Planetary Computer 最新遥感/自然数据与 Open-Meteo 实时气象；每次点击“刷新最新地理数据”都会重新抓取数据并重算危险等级。
>
![首页效果](web/assets/screenshot_home.png)

> 目录：`E:\警务遥感`
> 入口：`http://127.0.0.1:8080`（本机）/ `http://<本机局域网IP>:8080`（局域网）

---

## 1. 它是什么

一个面向生态警务（食药环侦 / 森林公安 / 保护区派出所 / 情指中心）的 Web 态势平台：

- **遥感一张图**：Esri 卫星影像底图 + 历史异常图斑 + 高发点 + 研究区边界。
- **高发地点与危险等级**：把 53 个历史异常聚成 31 个高发点，按红/橙/黄/蓝四级标注，给出风险分、判据和处置建议。
- **最新地理数据**：实时查询 Microsoft Planetary Computer 的 Sentinel-2 / Landsat / MODIS / DEM / 土地覆盖 / 地表水等产品，叠加显示；Open-Meteo 提供实时温度、湿度、风、降水、土壤湿度。
- **刷新重算**：右上角“刷新最新地理数据”会重新抓取 MPC 与气象数据，并用 MATLAB AHP 权重重新计算危险等级；如果等级发生变化，会提示变化点。
- **警务协同**：警务设施/巡护路线（示意）、就近派出所、生成核查任务（演示）。
- **扩展危险类型**：除 5 个已验证类别外，目录中还包含违规建设、非法倾倒、水体异常、湿地侵占、植被退化、裸土扩张、道路侵入、矿山生态破坏、火灾风险、洪涝风险、干旱风险、地质灾害隐患等扩展筛查/风险类别。
- **ArcGIS 专题图**：用 ArcGIS Pro 3.5 导出带天地图影像底图、危险等级配色、图例和统计的专题图。
- **MATLAB 风险模型**：AHP 计算 8 个风险因子的权重（CR=0.0026），输出权重图、等级分布图、Top 10 图和风险气泡图。

---

## 2. 技术栈与数据源

| 层 | 技术/来源 |
|---|---|
| 前端 | 原生 HTML/CSS/JS + Leaflet 1.9.4 + Apache ECharts 5.5（本地 vendored，无构建步骤） |
| 后端 | FastAPI + Uvicorn（Python 3.12） |
| 遥感/自然数据 | Microsoft Planetary Computer STAC API（Sentinel-2 L2A、Landsat C2 L2、MODIS NDVI/LST/GPP/过火面积、Copernicus DEM、IO LULC、ESA WorldCover、JRC 地表水） |
| 实时气象 | Open-Meteo（温度、湿度、风、降水、土壤湿度、高程） |
| 历史异常 | 青山卫士任务二检测结果（53 图斑，5 类：火烧迹地/滑坡/泥石流/雪线变化/盗采痕迹） |
| 风险模型 | MATLAB R2025b AHP + Python 加权/规则筛查兜底 |
| 专题图 | ArcGIS Pro 3.5 / ArcPy，天地图影像底图 |
| 底图 | Esri World Imagery / Esri Topo / OpenStreetMap（在线） |

---

## 3. 目录结构

```
E:\警务遥感\
├─ app\                     FastAPI 后端
│  ├─ main.py               API、刷新、任务、静态文件
│  ├─ config.py             区域、危险等级、17 类灾害目录
│  ├─ risk.py               高发点聚类、危险等级、风险格网
│  ├─ scenario.py           自然条件 → 危险系数 → 14 天时空推演
│  ├─ matlab_bridge.py      MATLAB 重算桥
│  └─ arcgis_bridge.py      ArcGIS 导出桥
├─ crawler\
│  ├─ build_data.py         53 异常 → incidents.geojson / AOI / 警务点位
│  └─ mpc_crawler.py        MPC STAC + Open-Meteo 爬虫
├─ data\                    运行数据（JSON/GeoJSON/缩略图/缓存）
├─ web\                     前端页面与静态资源
│  ├─ index.html
│  ├─ app.js
│  ├─ style.css
│  ├─ scenario.js           推演抽屉、仪表盘、时间轴、格网动画
│  ├─ scenario.css          推演界面高端样式
│  ├─ police.css            公安指挥中心风格皮肤（深蓝/金/警徽红）
│  ├─ assets/police-badge.svg  自绘警务风格徽章（非官方警徽）
│  ├─ vendor\leaflet\       本地 Leaflet + 插件
│  ├─ vendor/echarts.min.js 本地 ECharts
│  └─ assets\               MATLAB 图表、ArcGIS 专题图
├─ matlab\risk_model.m      AHP 风险模型与图表
├─ arcgis\export_map.py     ArcGIS 专题图导出
├─ docs\MODEL.md            推演模型公式、权重、开源参考、边界
├─ docs/UI_REFERENCES.md    GitHub 高星 UI/警务参考与实际落地
├─ fixed_domain\            固定域名：Cloudflare / ngrok / Tailscale 脚本
├─ scripts\get_public_url.ps1
├─ start_server.bat         一键启动本地网站
├─ start_public.bat         一键启动公网隧道（Cloudflare quick tunnel）
├─ install_autostart.bat    登录 Windows 自动启动
├─ stop_server.bat          停止网站与隧道
├─ Dockerfile / docker-compose.yml
├─ requirements.txt
└─ README.md
```

---

## 4. 快速开始（Windows）

### 4.1 本机运行

双击：

```
start_server.bat
```

脚本会：

1. 首次运行时创建 `.venv` 并安装 `requirements.txt`；
2. 启动 Uvicorn（监听 `0.0.0.0:8080`）；
3. 自动打开 `http://127.0.0.1:8080`。

浏览器打开后，点击右上角 **“刷新最新地理数据”** 即可重新抓取 MPC/气象并重算危险等级。

### 4.2 局域网访问

同一 Wi-Fi/局域网内的手机、平板、其他电脑打开：

```
http://<运行本程序的电脑局域网IP>:8080
```

`start_server.bat` 启动时会打印本机局域网 IP。Windows 防火墙首次可能询问是否允许 Python 访问网络，请选择“允许”。

### 4.3 公网访问（任何人可打开）

双击：

```
start_public.bat
```

脚本会：

1. 检查/启动本地网站；
2. 首次运行时下载 `cloudflared` 到 `tools\cloudflared.exe`；
3. 建立 Cloudflare quick tunnel；
4. 把公网地址写入 `public_url.txt` 并自动打开。

公网地址形如 `https://xxxx-xxxx.trycloudflare.com`。把它发给任何人都可以打开。

> 注意：
> - 也可以直接运行 `start_all.bat`：静默启动本地服务 + 公网隧道，并把地址写入 `public_url.txt`。
> - quick tunnel 的地址**每次重启都会变化**；需要固定域名请使用 Cloudflare 命名隧道，或把项目部署到云服务器（见 §4.4）。
> - 运行 `start_public.bat` 的电脑必须保持开机并联网；关闭窗口/电脑后公网地址失效。
> - 网站当前没有登录鉴权，且包含演示用的警务点位/任务数据。**不要把真实敏感警务数据放到公网隧道上。**

### 4.4 24 小时公网部署（云服务器 / Docker）

项目已带 `Dockerfile` 与 `docker-compose.yml`。在有公网 IP 的 Linux 云服务器上：

```bash
# 把整个 E:\警务遥感 目录上传到服务器
cd 警务遥感
docker compose up -d --build
# 访问 http://<服务器IP>:8080
```

建议再配 Nginx 反向代理 + HTTPS 域名。若需要 24/7 稳定访问，推荐这种方式，而不是依赖个人电脑 + quick tunnel。

> Docker 容器内只保证网站、MPC 爬虫和 Python 风险重算；"重算风险模型（MATLAB）"与"导出 ArcGIS 专题图"两个按钮需要宿主机安装 MATLAB/ArcGIS Pro，容器内会返回错误。

### 4.5 登录自动启动（可选）

双击（无需管理员）：

```
install_autostart.bat
```

会在当前用户的“启动”文件夹创建 `EcoPoliceWeb.lnk`，登录 Windows 后自动运行 `start_server_silent.bat`，只启动本地网站，不自动开公网。
取消：删除 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\EcoPoliceWeb.lnk`。

如果希望**登录后连公网隧道一起自动启动**，双击：

```
install_autostart_public.bat
```

会创建 `EcoPoliceWebPublic.lnk`，运行 `start_all.bat`（本地服务 + Cloudflare quick tunnel）。
注意：这会把网站暴露到公网，且当前站点没有登录鉴权，请勿放真实敏感数据；公网地址每次重启可能变化，见 `public_url.txt`。
取消：删除 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\EcoPoliceWebPublic.lnk`。

---

## 5. “刷新最新地理数据”做了什么

点击右上角按钮后，后端依次执行：

1. **Sentinel-2 / Landsat**：查询研究区最新场景（含云量、缩略图、MPC 直接可用瓦片 URL）。
2. **Open-Meteo**：5 个气象点的实时温度/湿度/风/降水/土壤湿度 + 高程。
3. **自然数据**：MODIS NDVI/LST/GPP/过火面积、Copernicus DEM、IO LULC、ESA WorldCover、JRC 地表水（静态产品带预览；MODIS 产品当前以元数据为主）。
4. **风险重算**：
   - 31 个高发点：类别严重度、异常数量、累计面积、复发性、坡度/高程、警务可达性、当前环境、数据置信度；
   - 用 MATLAB AHP 权重（`data/model_weights.json`）计算 0~100 风险分；
   - 红 ≥70、橙 ≥60、黄 ≥50、蓝 <50；
   - 生成 180 个 0.2° 风险格网（17 类扩展风险）。
5. **返回变化**：如果高发点等级变化，页面会提示“X 个高发点等级变化”。

刷新通常需要 **60~120 秒**（取决于 MPC 响应和限流）。如果 MPC 返回 429，爬虫会自动退避重试；仍失败时保留本地缓存并提示“部分数据源降级”，不会让整站不可用。

---

## 5.1 自然条件推演（新增）

![自然条件推演](web/assets/screenshot_scenario.png)

右上角 **“自然条件推演”** 打开右侧玻璃抽屉：

1. **输入自然数据**：点位、灾种、气温、湿度、风速、当日降雨、土壤湿度、前 3/7 天累计降雨、坡度、高程、NDVI、距道路、历史异常数。
2. **计算危险系数**：`/api/scenario/assess` 返回 0~100 系数、红/橙/黄/蓝等级、8 个因子的贡献和中文判据。
3. **开始时空推演**：`/api/scenario/timeline` 读取 Open-Meteo 未来 7/14 天逐小时预报，聚合成逐日气象；点位逐日评分 + 研究区 180 个格网逐日风险帧。
4. **动画**：播放/暂停、时间滑块、1×/2×/4× 速度；地图格网按天变色，顶部显示日期与点位分数；图表显示未来曲线和 50/60/70 阈值线。
5. **高风险窗口**：直接给出“预计哪天达到橙色/红色”，以及峰值分数与日期。
6. **图表**：使用本地 ECharts 5.5（67.7k★）绘制未来曲线、50/60/70 阈值线、峰值标记和 tooltip；ECharts 不可用时自动退回 Canvas。

模型是**物理经验隶属度 + AHP 权重**的早期预警指数，不是确定性灾害预报。公式、权重、时间推演、开源参考和边界见 `docs/MODEL.md`。

## 5.2 警务化 UI 与开源参考

页面已切换为公安指挥中心风格：深海军蓝 + 金色 + 警徽红，头部自绘盾形警务徽章、实时时钟、红色预警/高发点状态、今日重点警情，推演抽屉加入盾形徽章和警务配色。

- 徽章是**自绘的警务风格标识**，不是官方警徽，不使用官方标志；
- 参考并实际使用了 Leaflet（45.9k★）、Apache ECharts（67.7k★）；
- 学习 Chicago/opengrid（态势感知/事件联动）、Argus.AI（指挥中心/派单）、SCCP-DTEX（战术监控）、Tabler/AdminLTE（高星仪表盘布局）等项目的交互模式；
- 完整来源、星数和“哪些用了/哪些只借鉴”见 `docs/UI_REFERENCES.md`。

## 5.3 已安装的提效插件

| 插件 | 状态 | 位置/说明 |
|---|---|---|
| billion-context（4.2k★） | **已安装** | npm 全局 + `~/.zcode/cli/config.json` 已写入 SessionStart hook/MCP；**新开 ZCode 会话后生效**，当前会话不立即压缩 |
| dsh-ops（5.4k★） | **npm CLI 已安装，完整插件未启用** | 完整插件要求 DSH profile `~/.dsh/profiles/desktop`；本机没有 DSH，ZCode 不是 DSH 宿主。没有伪造空 profile；CLI 可 `dsh-ops help`，详见技能卡 |

技能使用卡：`D:/ZCodeSkillTools/skills/billion-context/SKILL.md`、`D:/ZCodeSkillTools/skills/dsh-ops/SKILL.md`（技能目录，不是插件本体）。

---

## 6. 风险模型（MATLAB AHP）

文件：`matlab/risk_model.m`

- 8 个因子：类别严重度、异常数量、累计面积、复发性、地形、可达性、当前环境、数据置信度。
- AHP 判断矩阵 + 特征向量权重；一致性比例 **CR=0.0026 < 0.1**（通过）。
- 权重写入 `data/model_weights.json`；Python 风险模型读取同一份权重，保证 MATLAB 与网页结果一致。
- 输出图表：
  - `web/assets/matlab_weights.png`：权重图
  - `web/assets/matlab_levels.png`：危险等级分布
  - `web/assets/matlab_top10.png`：Top 10 风险
  - `web/assets/matlab_map.png`：风险气泡图

页面右栏“风险模型 · MATLAB AHP”可点击 **“重算风险模型（MATLAB）”** 重新运行 MATLAB 并刷新结果。

手动运行：

```bash
"D:\MATLAB\bin\matlab.exe" -sd "E:\警务遥感\matlab" -batch "risk_model"
```

---

## 7. ArcGIS 专题图

文件：`arcgis/export_map.py`

- 使用 ArcGIS Pro 3.5 Advanced / ArcPy；
- GeoJSON → FileGDB → ArcGIS 地图；
- 底图：ArcGIS Pro 已配置的 **天地图-影像（球面墨卡托）**；
- 图层：研究区边界、历史异常、高发点（按红/橙/黄/蓝拆层着色）、警务设施、巡护路线；
- 导出 1800×1050 PNG，并用 Pillow 叠加标题、图例、统计；
- 输出：`web/assets/arcgis_risk_map.png`（带标题/图例的专题图）；
- 同时导出 **纯底图** `web/assets/arcgis_basemap.png` / `.jpg`（研究区天地图影像，无业务图层），网页用它作为研究区静态遥感底图。
- 网页底图策略：Esri 瓦片铺满全图 + ArcGIS 纯底图覆盖研究区；Esri/OSM 瓦片通过同源代理 `/api/tile/{style}/{z}/{x}/{y}` 获取，避免公网域名下第三方瓦片来源限制。

手动运行：

```bat
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" arcgis\export_map.py
```

页面右栏“风险模型”下方可点击 **“导出 ArcGIS 专题图”**。

---

## 8. 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/config` | 区域、危险等级、灾害目录、数据源 |
| GET | `/api/summary` | 总览统计、危险等级计数、变化、MPC/气象状态 |
| GET | `/api/hotspots` | 高发点 GeoJSON（含风险分/等级/因子/判据） |
| GET | `/api/incidents` | 53 个历史异常 GeoJSON |
| GET | `/api/risk-grid` | 180 个风险格网（含 17 类风险分） |
| GET | `/api/scenes` | 最新 MPC 场景与自然数据 |
| GET | `/api/weather` | 5 个气象点实时数据 |
| GET | `/api/police` | 警务设施/巡护路线（示意） |
| GET | `/api/catalog` | 17 类灾害/风险目录 |
| POST | `/api/refresh` | **刷新最新地理数据 + 重算危险等级** |
| GET | `/api/scenario/defaults` | 推演默认输入（nearest 气象/坡度/高程/历史） |
| POST | `/api/scenario/assess` | 自然条件 → 危险系数 + 因子贡献 |
| POST | `/api/scenario/timeline` | 未来 7/14 天逐日点位曲线 + 研究区格网帧 |
| GET | `/api/refresh/status` | 刷新状态 |
| GET/POST | `/api/tasks` | 警务核查任务（演示） |
| POST | `/api/model/recompute` | 运行 MATLAB 重算 AHP 权重 |
| POST | `/api/export/arcgis` | 导出 ArcGIS 专题图 |
| GET | `/api/tile/{style}/{z}/{x}/{y}` | 同源底图瓦片代理（esri / esri_topo / osm） |
| GET | `/api/docs` | FastAPI 交互式接口文档 |

---

## 9. 数据说明与免责声明

1. **5 个已验证类别**（火烧迹地、盗采痕迹、滑坡、泥石流、雪线变化）来自“青山卫士”任务二的实测检测结果（官方口径 19.34/25，53 个图斑）。
2. **扩展类别**（违规建设、非法倾倒、水体异常、湿地侵占、植被退化、裸土扩张、道路侵入、矿山生态破坏、火灾风险、洪涝风险、干旱风险、地质灾害隐患）是规则筛查/气象风险，**不是已验证的检测结论**，必须人工核查。
3. **警务设施、巡护路线、任务**是演示示意数据，**不是真实警务布点**；页面与接口中均有标注。
4. MPC 数据来自 Microsoft Planetary Computer 公开 STAC API；气象来自 Open-Meteo；底图来自 Esri / OpenStreetMap。请遵守各自的使用条款。
5. 网站当前**没有登录/权限/审计**，只适合演示与内部试用。用于真实警务前，必须补：身份认证、RBAC、数据分级、操作审计、HTTPS、内网部署、等保/密码合规。
6. 不要把真实案件、人员、车辆、警务部署等敏感数据放到公网隧道或未加固的服务器上。

---

## 10. 常见问题

**Q1：页面打开是空白/一直转圈？**
先确认 `start_server.bat` 窗口还在运行；访问 `http://127.0.0.1:8080/api/health` 应返回 `{"status":"ok"...}`。如果 8080 被占用，修改 `start_server.bat` 里的端口（同时修改前端不需要，前端走同源 `/api`）。

**Q2：刷新很久/提示“部分数据源降级”？**
MPC 对高频访问会限流（429）。爬虫会退避重试；仍失败时用缓存数据继续重算，页面会提示警告。等几分钟再点一次即可。

**Q3：MODIS NDVI/LST 没有图片？**
当前版本为了刷新速度，MODIS HDF 产品只取元数据（名称/日期/资产），没有做服务端渲染；DEM/土地覆盖/地表水等静态产品有预览。后续可加 MODIS 的通用渲染端点。

**Q4：公网地址每次都不一样？**
`start_public.bat` 用的是 Cloudflare quick tunnel，地址是临时的。需要固定地址请用命名隧道或云服务器部署。

**Q5：ArcGIS 导出失败？**
确认 ArcGIS Pro 3.5 已安装并至少能正常启动；确认 `C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe` 存在。底图使用 ArcGIS Pro 已配置的天地图影像；如果底图加载失败，脚本会退回 Topographic。

**Q6：MATLAB 重算失败？**
确认 `D:\MATLAB\bin\matlab.exe` 存在；首次启动 MATLAB 可能较慢。失败时页面会显示错误信息，Python 仍可用已保存的权重继续运行。

**Q7：能不能完全离线？**
当前底图/影像/气象需要联网。项目已 vendored Leaflet，前端本身无外部 CDN；如果需要全离线，需要把 Esri/MPC 瓦片预下载到本地，并把 Open-Meteo 替换为本地气象数据源。

---

## 11. 当前完成度

- ✅ 53 个历史异常 → 31 个高发点，红/橙/黄/蓝四级
- ✅ 17 类灾害/风险目录，5 类已验证 + 12 类扩展
- ✅ MPC 最新 Sentinel-2/Landsat/MODIS/DEM/土地覆盖/地表水抓取
- ✅ Open-Meteo 实时气象 + 高程
- ✅ 刷新按钮：重新抓取 + 重算危险等级 + 变化提示
- ✅ 自然条件输入 → 危险系数 → 14 天逐日曲线 + 研究区格网动画
- ✅ 固定域名配置脚本（Cloudflare / ngrok / Tailscale，见 fixed_domain/）
- ✅ 警务化品牌：盾形徽章、警务遥感标题、深蓝/金/警徽红指挥中心风格
- ✅ ECharts 时间轴（67.7k★）+ Canvas 兜底
- ✅ 已安装 billion-context；dsh-ops CLI 已装（完整插件需 DSH profile，已如实说明）
- ✅ MATLAB AHP 风险模型（CR=0.0026）与 4 张图表
- ✅ ArcGIS Pro 专题图（天地图影像底图 + 图例/统计）
- ✅ 警务设施/巡护路线/核查任务（演示）
- ✅ 公网隧道（已实测通过 `public_url.txt`）
- ✅ 本机、局域网、公网隧道、Docker 部署脚本
- ✅ 一键/静默/登录自启动（本地与公网两种）
- ⏳ 后续：登录/RBAC/审计、MODIS 预览、GPM 降水、移动端 PWA、与警综/PGIS 对接

---

## 12. 一句话介绍

> **把卫星发现变成民警今天就能执行的核查任务，把核查结果沉淀为可复核的案件材料。**

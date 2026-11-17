# 警务遥感 · 生态安全与灾害风险一张图

> 卫星发现 → 警务核查 → 证据闭环。一个面向基层警务的垂直领域全栈 MVP。
>
> 参赛赛道：2026 第九届传智杯 · AI 创新应用挑战赛（Vibe Coding，个人赛）
>
> 作品形态：可本地一键启动的 Web 应用；含遥感分析、案件工作台、移动端 H5、6G 空天地演示与证据链。

---

## 1. 这是什么

基层警务在生态安全与灾害风险场景里有一个很具体的断点：**遥感能发现疑似图斑，但发现之后没人把“核查—取证—复核—归档”串起来。** 图斑停留在 GIS 图层里，案件停留在纸质/聊天记录里，证据散落在个人手机里，最后很难形成可复核的闭环。

本项目把这个断点补上：

- **发现**：Sentinel-2 / Landsat / MODIS 公开影像，规则 + 分位数阈值 + 形态学后处理，输出疑似图斑与核查优先级；不冒充“训练好的 AI 模型”。
- **研判**：31 个历史高发点、180 个 0.2° 风险格网、14 天风险推演；MATLAB AHP 八因子权重（CR=0.0026）可复算。
- **核查**：案件工作台四角色（指挥员/民警/复核员/管理员）、五步流转、现场任务、无人机航线、到达圈、签到。
- **取证**：证据 SHA-256 + 案件级 HMAC 链；移动端 IndexedDB 离线队列，弱网先存后传。
- **呈现**：一张图（Leaflet/MapLibre/ECharts）、烟台采矿试点、6G 空天地演示、移动端 PWA。
- **辅助**：17 条法条草稿与取证方向匹配；明确标注“辅助草稿，不是法律意见”。

一句话：**把“遥感图斑”变成“可核查、可取证、可复核、可归档的案件”。**

---

## 2. 10 分钟复现

### 2.1 环境

- Windows 10/11（也支持 Linux/macOS；本文以 Windows 为例）
- Python 3.10+（开发环境 3.12）
- 不需要 Node.js、不需要前端构建、不需要数据库服务
- 不需要任何云端 API Key；演示数据已随仓库提供

### 2.2 启动

```bash
git clone https://github.com/<your-account>/police-remote-sensing.git
cd police-remote-sensing
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Windows 也可以直接双击 `start_server.bat`：首次自动创建 `.venv`、安装依赖并启动 8080。

打开 <http://127.0.0.1:8080>。

首次启动会初始化 `instance/police.db`；演示库已随仓库提供，通常 5–10 秒内可用。健康检查：

```bash
curl http://127.0.0.1:8080/api/health
```

期望看到 `"status":"ok"`、`"data_ready":true`、`"db":"ok"`。

### 2.3 演示账号

案件工作台 <http://127.0.0.1:8080/workspace>：

| 账号 | 密码 | 角色 |
|---|---|---|
| `commander` | `Commander@12345` | 指挥员 |
| `officer` | `Officer@12345` | 现场民警 |
| `reviewer` | `Reviewer@12345` | 复核员 |
| `admin` | `Admin@12345` | 管理员 |

> 演示账号仅用于本地演示；生产部署必须改密，并替换 `instance/.evidence_key`。

### 2.4 10 分钟演示路线

| 时间 | 页面 | 看什么 |
|---|---|---|
| 0:00–2:00 | `/` 一张图 | 31 个高发点、180 个风险格网、灾害目录、图斑详情、风险推演 |
| 2:00–5:00 | `/yantai` 烟台试点 | 真实 Sentinel-2 双时相、时长对比、地点变化图、3D 地质面板、底图代理 |
| 5:00–7:00 | `/sixg` 6G 演示 | 空天地链路、切片编排、边缘 AI、语义回传、线索一键转办 |
| 7:00–9:00 | `/workspace` 案件工作台 | 登录 → 案件 → 任务 → 证据 → 复核 → 归档；证据链校验 |
| 9:00–10:00 | `/m` 移动端 | 现场签到、拍照/文字证据、离线队列、同步 |

### 2.5 演示视频

- 文件：[`docs/video/警务遥感_Vibe_Coding_演示.mp4`](docs/video/警务遥感_Vibe_Coding_演示.mp4)（5 分 41 秒，1920×1080，30fps，H.264/AAC）
- 字幕：[`docs/video/警务遥感_Vibe_Coding_演示.srt`](docs/video/警务遥感_Vibe_Coding_演示.srt)
- 章节：0:00 封面 / 0:13 真实断点 / 0:42 一张图 / 1:03 烟台试点 / 1:27 6G 天枢 / 1:50 案件闭环 / 2:10 移动端 / 2:24 AI 协作方法 / 2:52 法条检索 / 3:32 6G 主链 / 4:06 烟台主链 / 4:44 Git 与复现 / 5:11 评分与边界 / 5:32 结束
- AI 协同段落为 2:24–4:44（约 2 分 20 秒），满足“至少 2 分钟”的要求；旁白由 Windows 中文语音合成生成，画面来自本地 8091 的真实页面截图。

---

## 3. 页面与接口

### 3.1 页面

| 路径 | 说明 |
|---|---|
| `/` | 生态安全与灾害风险一张图 |
| `/yantai` | 烟台采矿遥感试点（真实影像 + 规则检测 + 3D 地质面板） |
| `/sixg` | 6G 空天地一体公共安全智能体演示 |
| `/aircase` | 6G 线索 → 案件转办演示 |
| `/workspace` | 案件工作台（登录） |
| `/m` | 移动端 H5 / PWA |
| `/open-data` | 开放数据目录 |
| `/operations` | 运维与任务面板 |
| `/docs` | 项目文档入口 |

### 3.2 主要接口

- `GET /api/health` 健康检查
- `GET /api/hotspots` 高发点
- `GET /api/risk-grid` 风险格网
- `GET /api/risk/forecast` 14 天风险推演
- `GET /api/yantai/regions` 烟台样区
- `GET /api/yantai/analysis` 双时相分析
- `GET /api/tile/{style}/{z}/{x}/{y}` 底图同源代理
- `POST /api/auth/login` 案件工作台登录
- `GET /api/cases` 案件列表
- `POST /api/cases/{id}/evidence` 上传证据
- `POST /api/cases/{id}/review` 复核
- `POST /api/cases/{id}/archive` 归档

完整 OpenAPI：启动后访问 <http://127.0.0.1:8080/docs>。

---

## 4. AI 协作方式（重点）

### 4.1 边界

- **选题、业务判断、验收标准由作者提出**：要解决什么警务断点、哪些页面必须存在、什么算“能用”，不是 AI 替作者想出来的。
- **AI 负责“改程序”**：定位代码、给出修改、跑验证、修回归。本文档与 `docs/ai-iterations/` 只收录这类链路，**不收录用 AI 想思路的对话**。
- 使用的工具：**ZCode（Agentic IDE，底层模型 deepseek-v4.1-flash）**；会话记录来自 ZCode 本地 SQLite（`message` / `part` 表），按真实时间线导出，未手工编造。

### 4.2 三条核心迭代（本次提交重点）

| # | 用户 Prompt（原文） | AI 做了什么 | 可核对的验证 |
|---|---|---|---|
| 01 | “时间长度对比要用真实数据，上面那个黑色的是啥，同时把每个分析的地点都加入这个变化图” | 在 `app/yantai_api.py` 增加 `_days_between` / `_span` / `_analysis_sites`；在 `web/yantai-workspace.js` 增加 `daysBetween` / `spanText`；把写死文案改成按观测日期计算，缺日期返回 `None`，并把所有样区加入变化图 | `python -m py_compile`；Playwright 打开 `/yantai` 检查 750 天 / 2.05 年、355 天 / 0.97 年与三个样区 |
| 02 | （实现阶段）按给定的 Three.js 规范重写 3D 地质面板：DEM 512×512、垂向放大 1.8、卫星正射顶面、5 层 `ExtrudeGeometry`、噪声不整合面、`OrbitControls`、HTML 叠层 UI | 新建 `web/yantai-geo3d.js`，新增 `scripts/bake_basemap.py`，改 `scripts/bake_terrain.py`；地区切换时按 key 重建地形与地质体 | Playwright 切换 P07/P01、缩放/旋转、检查控制台无报错；3D 面板不再停留在“正在拼接真实影像” |
| 03 | “检查一下背景底图，不要出现加载不了的情况” | 在 `app/main.py` 增加 `TILE_SOURCES` / `TILE_CHAINS` 与 `/api/tile/{style}/{z}/{x}/{y}` 同源代理：按上游分键缓存、全失败回退本地占位瓦片；前端各页 `tileLayer` 改同源地址并设 `errorTileUrl` | 正常网络、错误上游、完全断网三种场景页面都不白屏；检查代理缓存命中 |

补充案例：左侧区域滚动修复（`web/yantai-workspace.css` 的 flex 高度链 + 内层滚动），见 `docs/ai-iterations/04_补充_左侧区域滚动修复.md`。

### 4.3 完整 Prompt 链

- [01 烟台时长对比与地点变化图](docs/ai-iterations/01_烟台时长对比与地点变化图.md)
- [02 3D 地质面板地区切换](docs/ai-iterations/02_3D地质面板地区切换.md)
- [03 底图加载失败与同源代理](docs/ai-iterations/03_底图加载失败与同源代理.md)
- [04 补充：左侧区域滚动修复](docs/ai-iterations/04_补充_左侧区域滚动修复.md)

每份文档包含 Prompt 原文、Edit/Write 前后对照、关键验证命令与 AI 最终说明；已清洗本机路径与个人信息。

### 4.4 仓库历史说明

项目最初在 ZCode 中迭代，未从一开始启用 Git；仓库历史按功能模块与真实开发顺序重建，不是伪造的“每日提交”。三个核心迭代的原始会话与代码改动见 `docs/ai-iterations/`；最终 `main` 是经过冒烟与回归验证的可运行状态。


---

## 5. 目录结构

```text
app/                 FastAPI 后端（主应用、案件、遥感、6G、法条、移动端）
web/                 原生 HTML/CSS/JS 前端（无构建步骤）
  assets/            地图、图标、第三方库
  yantai*.js/css     烟台试点与 3D 面板
scripts/             地形烘焙、底图烘焙、变化检测、InSAR 等脚本
crawler/             公开数据抓取与数据构建
mining_watch/        采矿遥感规则检测流水线
tests/               单元/接口测试
matlab/              AHP 权重与风险模型
deploy/              Docker 与部署脚本
data/                演示数据（精选，非全量遥感缓存）
instance/            演示案件库与证据（SQLite）
docs/                设计、方法、验证与 AI 迭代记录
```

---

## 6. 关键设计

### 6.1 证据链

- 每个证据文件计算 SHA-256；
- 案件级 HMAC 链：`prev_hash + evidence_hash + case_id + timestamp`，密钥来自 `instance/.evidence_key`；
- 提供“校验证据链”接口/页面；篡改任一环会失败；
- 演示密钥随仓库提供，生产必须更换。

### 6.2 移动端离线

- `/m` 使用 IndexedDB 保存待传证据；
- 在线时先写本地队列，再按顺序同步；
- 弱网/断网不丢证据；同步失败可重试。

### 6.3 底图同源代理

外链瓦片在校园网/内网经常超时。`/api/tile/{style}/{z}/{x}/{y}` 做同源代理，按上游分键缓存；全部上游失败时回退本地占位瓦片，保证页面不白屏。

### 6.4 6G 演示的边界

`/sixg` 与 `/aircase` 是**数字孪生仿真**：时延、带宽、丢包、切片利用率是仿真值，用于演示“网络感知编排 + 边缘 AI + 语义回传”的流程，不是真实 6G 实测。页面上有明确标注。

---

## 7. 验证

```bash
# 语法检查
python -m compileall app crawler mining_watch

# 接口冒烟
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/hotspots
curl http://127.0.0.1:8080/api/yantai/regions

# 案件闭环（命令行）
curl -c cookies.txt -H "Content-Type: application/json" \
  -d "{\"username\":\"commander\",\"password\":\"Commander@12345\"}" \
  http://127.0.0.1:8080/api/auth/login
curl -b cookies.txt http://127.0.0.1:8080/api/cases
```

已做的验证：

- 首页、烟台、6G、案件工作台、移动端均返回 200；
- 案件登录、案件列表、证据上传/下载、复核/归档可走通；
- 证据链校验通过；篡改测试失败；
- 底图代理在断网/错误上游下回退占位瓦片；
- 移动端离线队列在断网时保留、恢复后同步。

未做的验证（如实说明）：

- 没有真实警务数据，案件均为演示数据；
- 没有做遥感检测精度评估（没有标注真值）；
- 无人机航线是模拟复拍，不是实际飞控；
- 法条为摘要草稿，未经法制部门审核；
- 未做等保测评、渗透测试、压力测试。

---

## 8. 边界与合规

- 本项目是**技术演示与办案辅助原型**，不构成执法结论、法律意见或预警承诺。
- 所有遥感数据来自公开源（Sentinel-2/Landsat/MODIS/Open-Meteo/OSM 等），遵循各自许可；仓库内演示数据已脱敏。
- 6G 指标为仿真；页面与文档均已标注。
- 证据链密钥、演示账号仅用于本地演示；生产部署必须替换。
- 如果复用本项目代码，请保留出处与许可说明；不得把演示数据当作真实案件数据使用。

---

## 9. 许可

代码用于参赛与学习交流。第三方库、地图瓦片、遥感影像遵循各自许可；详见 `docs/OPEN_DATA.md` 与各库 LICENSE。
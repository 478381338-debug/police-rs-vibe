# 空中办案 · AIRCASE

卫星初筛 → 无人机同机位精查 → 变化测量意见 → 可信证据包。页面入口：`/aircase`。

本模块是在现有“6G + 人工智能”演示系统上**增量添加**的办案场景；`/sixg` 旧入口保留为完整 6G 推演大屏（`/sixg-lab` 307 到 `/sixg`），`/workspace`、`/operations`、`/api/drone` 等既有页面与接口未改动：

- **方案 2（主线）**：对卫星初筛图斑做同机位无人机复拍，输出变化面积、置信度、变化类型和《变化测量意见》。
- **方案 1（第二场景）**：现场方位图模式，用环飞航线生成带指北针、比例尺、坐标、图斑标注的方位图。
- **方案 3（底座）**：每个架次生成 11 个证据文件，逐文件 SHA-256、根哈希、HMAC 签名，可一键校验。

> 演示口径：`data/aircase/demo/before.png` 是真实 Sentinel-2 预览影像；`after.png` 是按同一机位、同一范围生成的**模拟复拍时相**。现有 `data/sixg_demo/after.png` 与 before 并非同一地点（归一化互相关约 9.29e-06），直接做同机位比对会穿帮，因此本模块单独生成演示复拍，并在页面、报告、方位图中明确标注“演示件 / 模拟复拍 / 非鉴定结论”。面积、置信度和证据链在演示数据上自洽，但不代表真实案件测绘成果。

## 快速开始

```bash
# 在项目根目录
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

打开 `http://127.0.0.1:8080/aircase`。若 8080 上已有旧进程，先停止旧进程再启动，否则 `/aircase` 可能仍是旧代码的 404。

演示数据随仓库放在 `data/aircase/demo/`，首次调用会自动生成 `before.png/after.png/meta.json`；也可直接使用已保留的两个演示架次：

- `AC-20261111-193401-D8FB`：P-01 同机位精查，30.4 公顷，置信度 0.93，351 航点。
- `AC-20261111-195800-8D54`：P-01 现场方位图，48.9 公顷，置信度 0.87，109 航点。

## 页面操作

1. 左侧选择候选图斑（P-01/P-02/P-03）。
2. 选择“精查”或“方位图”，调整相对航高（60–200 m，默认 120 m）。
3. 点击“开始精查/开始方位图”。前端同时调用创建/运行接口与 Three.js 推演动画。
4. 六个阶段：卫星侦察 → 任务规划 → 同机位复拍 → 变化检测 → 测量意见 → 证据存证。
5. 中央可切换“三维态势 / 同机位对比 / 现场方位图”；右侧查看面积、置信度、法条参考、证据清单，并可校验哈希与签名。
6. 下载 Markdown、HTML 测量意见和方位图。

页面已合并为三块：左侧“任务控制”（候选图斑、参数、演示架次），中间全高 3D 舞台（三视图、迷你指标、六步时间线），右侧“办案结论”（面积/置信度/结论、证据包与校验、法条、数据来源与口径），重复的独立 KPI 行、作业日志、架次参数块和证据双栏已移除。

Three.js 场景使用 4096×2731 地表贴图、260×174 段地形、HD 演示影像（`after_hd.png`，2048×1024）、亮度凹凸贴图、天空穹顶与远山剪影；AOI 影像四边渐变羽化并叠加战术网格/角标，变化区有橙色辉光与双环；图斑周边有深色料堆/工棚、卫星太阳能板与信标、无人机航行灯/光晕、蛇形航线与流动粒子、扫描带与证据回传弧线。支持 ORB/FLW/TOP/SCN 四种相机。WebGL 不可用时显示降级提示。

## 计算链路

- **图斑初筛**：复用 `app/sixg_api.py::_detect()` 的缓存结果（3 个图斑，合计 48.47 公顷，GSD 约 4.04 m）。
- **航线**：调用 `app/drone.py::plan_drone_route()`。精查为 lawnmower，按图斑面积推导范围（0.35–1.6 km），间距 60 m、相对航高 120 m、速度 10 m/s、方向 35°；方位图为 perimeter，约 1.2 km。航点上限沿用 800。
- **裁剪**：在图斑 bbox 外扩 `0.55 × max(w,h) + 24` 像素；精查约 468×363，方位图约 840×512。
- **同机位校验**：对航线坐标、相对航高、云台 -90°、航向做规范化 SHA-256，得到 `route_signature` 与 `repeat_signature`；两者一致时 `pose_match=true`，并给出 `pose_max_offset_m`。
- **局部变化检测**：亮度/植被指数/梯度特征，差分后阈值 `max(quantile(0.90), 0.12)`，形态学去噪，连通域 ≥ 150 像素，抑制 `delta_lum ≤ 0.015`；面积 = 变化像元占比 × 裁剪区 haversine 面积；置信度 0.60–0.96。
- **测量意见**：Markdown + HTML，含任务来源、影像与飞行参数、同机位一致性、变化检测、面积量算、法条参考、结论、限制、证据清单。法条通过 `app/legal.py::checklist()` 检索，`review_status=draft`，不输出构罪/处罚结论。
- **方位图**：PIL + 微软雅黑，含标题块、指北针、比例尺（50–2000 m 取整）、300 m 网格、经纬度角标、候选图斑框/编号/面积、中心十字、图例、页脚。

## API

前缀 `/api/aircase`：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/overview` | AOI、影像元数据、图斑、指标、最近架次 |
| GET | `/demo/{name}` | 演示影像，白名单 `before.png`/`after.png`/`before_hd.png`/`after_hd.png` |
| POST | `/sortie` | 创建架次，body `{patch_id, mode, rel_height_m}` |
| GET | `/sortie/{id}` | 架次详情 |
| POST | `/sortie/{id}/run` | 执行检测并生成意见/方位图/证据包 |
| GET | `/sortie/{id}/measure?format=md\|html\|json` | 测量意见 |
| GET | `/sortie/{id}/scene-map` | 方位图 PNG |
| POST | `/sortie/{id}/verify` | 重算逐文件哈希、根哈希、HMAC |
| GET | `/asset/{id}/{name}` | 证据资产（白名单） |

`mode` 只接受 `precise` / `scene`；`rel_height_m` 40–300（前端默认 120）。创建架次约 2–5 s（两次航线规划），运行约 1 s，证据校验为毫秒级。

## 证据包

每个架次写入 `data/aircase/out/{id}/`，共 11 个文件：

`before_uav.png`、`after_uav.png`、`diff_overlay.png`、`diff_heatmap.png`、`scene_map.png`、`measure.md`、`measure.html`、`measure.json`、`route.kml`、`route.csv`、`route.geojson`。

- `evidence.files[]`：逐文件 `name/bytes/sha256`。
- `evidence.root_sha256`：按文件名排序后拼接哈希再 SHA-256。
- `evidence.signature`：HMAC-SHA256(root_sha256, key)，密钥复用 `app/case_store.py::_evidence_key()`（优先 `POLICE_EVIDENCE_KEY`，否则 `EVIDENCE_KEY_PATH`）。
- `data/aircase/audit.jsonl`：创建、运行、校验的追加审计。

校验接口返回 `verified/files_ok/root_ok/signature_ok` 和每个文件的 expected/actual。哈希与 HMAC 只能证明证据包在签发后未被改动，**不证明拍摄行为真实发生**，也不替代电子数据检查笔录、原始载体封存和鉴定意见。

## 数据与配置

- `app/aircase.py`：航线、复拍、检测、报告、方位图、证据。
- `app/aircase_api.py`：FastAPI 路由。
- `app/case_store.py`：`import_sixg_lead()`、`sixg_lead_status()`、6G 线索转正式案件与证据链。
- `app/sixg_api.py`：6G 场景推演、线索落盘、查询与转办接口。
- `app/main.py`：`/aircase` 页面、`/sixg` 307 重定向、`include_router`。
- `web/aircase.html`、`web/aircase.css`、`web/aircase.js`、`web/aircase-3d.js`：页面与控制台；`web/sixg-panel.js`：6G 链路弹窗。
- `data/aircase/demo/`：演示影像与 meta（`after_hd.png` 为 3D 初始底图）。
- `data/aircase/sorties/`：架次 JSON；`data/aircase/out/`：输出与证据。

## 已验证结果

- 初筛：P-01 30.69 ha / 0.86；P-02 13.51 ha / 0.79；P-03 4.27 ha / 0.85；合计 48.47 ha。
- 精查 `D8FB`：351 航点、12.6 km、36.8 min、30.4 ha、置信度 0.93、同机位偏移 0.0 m。
- 方位图 `8D54`：109 航点、48.9 ha、置信度 0.87。
- 证据校验：两个架次均 11/11 文件通过，根哈希与 HMAC 一致。
- Playwright 1440×900 验收：合并后的三列布局、三维态势、同机位对比、方位图（含图斑量算表）、法条、证据清单、校验、下载均可用；无 console error。
- 2026-11-12 视觉改版：3D 提升到 4096 贴图 / 260×174 地形，新增天空、远山、AOI 羽化与角标、变化辉光、场地料堆、无人机光晕；页面合并重复信息，初始页与结果页均已截图复核。

## 6G 线索转办（单网址闭环）

`/sixg` 是完整 6G 推演大屏，`/sixg-lab` 307 到 `/sixg`；`/aircase` 顶部“6G 链路”直链 `/sixg`。需要把演示线索转成正式案件时，从 6G 页导航“线索转办”回到 `/aircase?panel=sixg`，弹窗可在“常态巡航 / 基站中断 / 卫星切换 / 切片拥塞”四个场景间切换；每次推演生成 `SIXG-YYYYMMDD-XXX` 演示线索，并保存到 `data/sixg_demo/leads/`。

“一键转正式线索”会调用 `app/case_store.py::import_sixg_lead()`：

1. 以 `source_type='sixg'`、`source_id=SIXG-...` 幂等查重；重复转办返回已有案件，不重复写证据。
2. 写入正式 `cases` 表，生成 `CASE-YYYYMMDD-NNN`、`status='核查中'`、`due_at=now+72h`、`priority_score/current_danger=confidence×100`。
3. 创建“现场核查 …”任务，默认指派 `officer`，状态“待接收”。
4. 写入 6 份证据：`sixg_lead_report.md`、`sixg_lead.json`、`after_overlay.png`、`diff_heatmap.png`、`before.png`、`after.png`；逐文件 SHA-256、案件证据链和审计。
5. 返回 `workspace_url=/workspace?case={case_id}`；工作台读取深链后自动选中该案。

相关 API：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/sixg/overview` | 场景与影像元数据 |
| POST | `/api/sixg/demo` | 生成演示线索并落盘 |
| GET | `/api/sixg/leads/{case_no}` | 查询是否已转办 |
| POST | `/api/sixg/leads/{case_no}/convert` | 转正式案件，body 可带 `assignee_id`、`due_hours` |

已验证：`SIXG-20261111-OUT` → `CASE-20261111-001` 的转换链路可用，期限 2026-11-14，6 份证据，任务 1 个；重复转办返回 `duplicate=true/evidence_added=0`；工作台深链选中案件，显示“线索 6G 演示线索 · SIXG-…”、优先级 85.0/100、证据链和 `convert_sixg_lead` 审计。当前保留的演示数据为：`SIXG-20261111-NOR` 已转为 `CASE-20261111-001`，`SIXG-20261111-OUT` 保持“待转办”供现场演示。口径仍是演示件：6G 时延、卫星切换、边缘推理均为仿真，不代表现网实测；转办只证明“从演示线索生成了一条可办理的正式记录”，不证明线索本身真实。

## 边界与后续

- 面积精度取决于 GSD、配准、阴影和混合像元；报告已写明“影像估算值”，正式案件应由有资质机构测绘并出具鉴定意见。
- 航线未接入空域审批、风场、电池余量、避障和实时链路；6G/边缘时延为数字孪生仿真，不代表现网实测。
- 当前同机位复拍为演示合成；接入真实无人机影像后，只需替换 `before_uav/after_uav` 输入并保留同一套检测、报告和证据链。
- 法条为知识库检索参考，条文适用、数量标准和构罪条件需结合鉴定意见与最新司法解释复核。
## 公网统一入口

统一登录入口为 `http://47.95.141.225:8080/`。登录后首页顶部“空中办案”进入 `/aircase`；`/sixg` 保留为完整 6G 推演大屏，`/sixg-lab` 307 到 `/sixg`；`/aircase` 顶部“6G 链路”直链 `/sixg`，6G 弹窗仍可通过 `/aircase?panel=sixg` 或 6G 页“线索转办”打开。访问口令使用 `police_gate` Cookie（`Path=/`），因此根页面登录一次即可覆盖 `/aircase`、`/workspace`、`/operations`，不需要在 `/aircase` 再登录一次。未登录访问 `/aircase` 会 303 回根页面登录，登录后回到原页面。

公网构建更新包：`dist/aircase_public_bundle.zip`；部署步骤见 `dist/DEPLOY_PUBLIC.md`。当前公网实例仍是旧构建（登录后 `/aircase` 返回 404、首页仍显示“天枢 6G”），需要解压该包并重启 `police-web` 后生效。

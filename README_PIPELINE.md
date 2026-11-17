# 烟台非法采矿遥感预警流水线（mining-watch）

用免费 Sentinel-2 做「上年同期 vs 当期」的裸地指数时序比对，自动输出疑似图斑矢量。
配套 ZCode Skill：`C:\Users\lenovo\.zcode\skills\mining-watch\SKILL.md`

## 环境

```bash
E:\警务遥感\.venv_mw\Scripts\python.exe     # Python 3.12 + rasterio 1.5.2
```

已装：rasterio / numpy / scipy / shapely / pyproj / matplotlib / pandas / geopandas / pyyaml。
数据源：AWS Earth Search（`https://earth-search.aws.element84.com/v1`），免密钥、免登录、HTTP Range 直读 COG。

## 用法

```bash
cd /e/警务遥感

# 环境自检（YOLO / InSAR 是否可用）
.venv_mw/Scripts/python.exe run.py --check

# 全流程
.venv_mw/Scripts/python.exe run.py --aoi zhaoyuan
.venv_mw/Scripts/python.exe run.py --aoi all

# 分阶段（调阈值时不必重复下载）
.venv_mw/Scripts/python.exe run.py --aoi zhaoyuan --stage fetch
.venv_mw/Scripts/python.exe run.py --aoi zhaoyuan --stage detect
.venv_mw/Scripts/python.exe run.py --aoi zhaoyuan --stage report

# 气候增强：观测窗口 / 探测能力 / 类型化校正
.venv_mw/Scripts/python.exe run.py --aoi zhaoyuan --stage climate
.venv_mw/Scripts/python.exe scripts/yantai_climate_pipeline.py --stage all --scl
```

冒烟测试（小范围单景，约 2 分钟）：
```bash
.venv_mw/Scripts/python.exe run.py --aoi zhaoyuan --config config_smoke.yaml
```

## 目录结构

```
E:\警务遥感\
├── config_yantai.yaml        正式配置：时段、阈值、7 个重点区域
├── config_smoke.yaml         冒烟测试配置：小范围、单景
├── run.py                    一键入口 / 分阶段执行
├── mining_watch\
│   ├── settings.py           配置与数据结构
│   ├── fetch.py              STAC 检索 + COG 窗口读取（跨瓦片对齐）
│   ├── indices.py            BSI/NDVI/NDBI/MNDWI/铁染/羟基 + SCL 云掩膜
│   ├── anomalies.py          中值合成、稳健 z、连通域、图斑矢量化
│   ├── climate.py            云/雨/雪特征、观测适用性、探测能力、类型化天气校正
│   ├── report.py             GeoTIFF/GeoJSON/CSV/PNG/摘要
│   └── stages.py             YOLO 与 InSAR 环境检查与指引
├── scripts\
│   └── yantai_climate_pipeline.py  烟台气候增强全链路（含 SCL、模型、评估、报告）
└── out\<aoi>\                每个区域的输出
```

## 判定算法

```
ΔBSI     = BSI_当期 − BSI_上年同期        > 0.15
NDVI跌幅 = NDVI_基线 − NDVI_当期          > 0.15
BSI_当期                                  > 0.35
MNDWI_当期                                ≤ 0
稳健 z(ΔBSI) = (x − median) / (1.4826·MAD) > 3
图斑面积（8 邻域连通）                    ≥ 3000 ㎡
```

指数定义：
- `BSI = ((B11+B04) − (B08+B02)) / ((B11+B04) + (B08+B02))` —— 裸地最敏感
- `NDVI = (B08−B04)/(B08+B04)`
- `NDBI = (B11−B08)/(B11+B08)`
- `MNDWI = (B03−B11)/(B03+B11)`
- `铁染 = B04/B02`，`羟基 = B11/B12`

云掩膜用 SCL 分类，剔除 0/1/3/8/9/10/11。反射率自适应归一化（>1.5 时 /10000）。

## 气候增强（云 / 雨 / 雪）

`mining_watch/climate.py` 把天气从“背景噪声”变成三类可用信息，完整方法见
`docs/气候因素增强分析链路.md`。

1. **观测适用性**：按类型画像对云量、积雪、前期降水、土壤湿度、冻融做加权几何平均；
   积雪是硬约束。输出 0–1，`usable_optical` 取 ≥0.5。
2. **探测能力**：`1 − exp(−n_usable/clear_min)`，超过类型允许间隔再乘 gap 惩罚；
   分为 `adequate/limited/insufficient`，避免把“没观测”说成“没变化”。
3. **天气归一化**：逐景 AOI 稳定像元中值建序列，拟合
   “年/半年谐波 + 前期降水 + 土壤湿度 + 雪深 + 云量”（Huber IRLS），
   LOO-CV 改进 ≥2% 才启用；候选按告警日相对基线同季的天气偏差校正 ΔBSI/NDVI。
4. **类型化排序**：六类画像（露天采坑、河道采砂、井工、堆场、复采、运输）各自设定
   主/次方法、观测门槛、天气敏感权重、混淆解释和等级封顶。

```bash
# 轻量：只取气候，输出 out/<aoi>/climate.json 与 climate_monthly.csv
.venv_mw/Scripts/python.exe run.py --aoi zhaoyuan --stage climate

# 完整：读 81 景缓存 + 既有候选，输出 data/yantai_climate/
.venv_mw/Scripts/python.exe scripts/yantai_climate_pipeline.py --stage all --scl
```

候选输出在原有 GeoJSON 上追加 `climate_delta_bsi`、`climate_adjusted_score`、
`climate_observation_level`、`climate_flags`、`climate_explanations`、`climate_tier` 等字段。
排序分不是概率；没有独立标签时不报告精度/F1，只报告 LOO-CV、已知点排名变化、
负对照降级和候选-降雨相关性。

平台入口：启动 `start_server.bat` 后打开 `/yantai`，点击导航栏“气候增强”。
前端只读取结果、不在浏览器重算：`GET /api/yantai/climate` 汇总
`data/yantai_climate/summary.json`、`type_methods.json` 与两个 AOI 的
`candidates_climate.geojson`（几何转为 WGS84），`GET /api/yantai/climate/report`
返回 `summary.md`。若接口返回 `status=missing`，先跑上面的完整链路。

## 工程要点

**跨瓦片对齐**：基线期与目标期可能落在不同 MGRS 瓦片（甚至不同 UTM 带）。做法是先用基线期第一景确立输出网格 `(crs, transform, H, W)`，目标期强制沿用同一网格读取，保证逐像元可比。不这样做，两期差值会出现整幅错位。

**单景窗口读取**：只读 AOI 对应的窗口，不下载整景。20 km × 20 km 的 AOI、7 个波段，单景约 15–30 MB。

**缓存**：`out/<aoi>/cache.npz` 保存基线/目标期全部波段，`--stage detect` 直接复用，调阈值不重复下载。

## 已知限制

1. **20 m 分辨率看不到零星盗采**。小于 2000 ㎡（约 3 亩）的采坑会漏。要抓小图斑必须高分二号（0.8 m）或无人机。
2. **看不到地下开采**。地表无扰动的地下采空、巷道掘进，Sentinel-2 完全无感，只能靠 InSAR 形变。
3. **无法判断合法性**。必须叠加矿业权边界（`kyqgs.mnr.gov.cn`）才能区分越界与有证开采。
4. **季节性假阳性**。果园清园、农地翻耕在指数上与采石高度相似。基线取「上年同季」并加 `bsi_abs` 约束就是为了压这个。
5. **烟台植被区限制**。C 波段 InSAR 在果园/林地失相干严重，形变结果需按地类分层设阈值，优先冬季影像。
6. **面积精度 ±20 m 边界**。可用于排序和初筛，不能作为处罚依据。
7. **天气校正是场景级近似**。用 AOI 稳定像元中值与告警日/基线同季天气差，不能替代逐像元辐射归一化；LOO-CV 不达标时链路会不启用天气项。
8. **冬季短波红外与积雪**。积雪覆盖下光学指数不可比，链路会把相关候选封顶到 D 级；这不等于排除，只表示当前证据不足。

## 传输层与性能（实测，2026-11）

本机 GDAL 的 HTTPS 栈不可用：`rasterio.open("https://…")` 会在 1 秒内失败，报
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xbe …`（真实错误是 GBK 编码的
网络错误，被 rasterio 的日志解码掩盖）。同一 URL 换 `http://` 可正常打开，Python
自己的 urllib 走 HTTPS 也正常，说明问题只在 GDAL 的 TLS 实现。

`mining_watch/tunnel.py` 因此在进程内起一个本地反向代理：GDAL 请求
`http://127.0.0.1:<随机端口>/…`，代理用 Python(OpenSSL) 转发到 `https://` 上游，
只做传输、不改内容。`fetch.configure_transport()` 会自动探测，**仅在探测失败时**
启用隧道，正常机器上零开销。

三个必须知道的实现约束：

1. **本机 GDAL 忽略 `CPL_CURL_PROXY`**，只认 `http_proxy` / `HTTP_PROXY` 环境变量。
2. **波段并发上限 2**（`read_stack(workers=2)`）。4 路并发会复用到对端已关闭的
   空闲连接，隧道返回 502，GDAL 报 `TIFFFillTile: … got 0 bytes`。
3. 双层重试：GDAL `GDAL_HTTP_MAX_RETRY=5`，`_read_band` 再退避重试 3 次，单波段
   失败不会拖垮整景。

实测速度（招远蚕庄 459×460 像元 @20m，两期各 1 景、共 14 个波段）：取数 **145 秒**，
全流程 **147 秒**。瓶颈是跨境带宽（约 165 KB/s），不是隧道本身——隧道内 4 次
Range 请求共 4.37 MB 用时 26 秒，与直连 `http://` 同量级。

只调阈值、不重新下载：`--stage detect` 会复用 `out/<aoi>/cache.npz`，秒级完成。
`--aoi all` 对每个区域独立 try/except，单个区域失败不影响其余，结束时打印失败区域清单。

## 扩展点

| 需求 | 做法 |
|---|---|
| 更细的分辨率 | 换高分二号/北京三号（需商业采购），改 `fetch.py` 的 asset 映射 |
| 设备检测 | `stages.check_yolo()` → YOLO-World 零样本，或自建挖掘机数据集微调 |
| 形变监测 | `stages.check_insar()` → WSL2/Docker 装 PyGMTSAR 或 MintPy |
| 矿业权叠加 | 下载矿权边界 → `geopandas.sjoin(patches, mining_rights)` |
| 更多指标 | `indices.py` 加函数，`compute_all` 注册，`anomalies.py` 加判定条件 |
| 自动预警 | `out/<aoi>/suspects.geojson` 接定时任务 + 阈值告警 |

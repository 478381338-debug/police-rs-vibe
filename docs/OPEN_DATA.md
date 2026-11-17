# 多源开放数据说明

本项目在原有遥感/警务闭环数据上，接入了四组可公开核查的开放数据。所有原始文件、处理脚本和网页接口都在本机可追溯；没有把在线底图或商业数据包装成“自有数据”。

## 1. 已接入数据

| 组 | 数据 | 来源 | 许可/署名 | 本项目用途 |
|---|---|---|---|---|
| 基础设施/警务资源 | OSM POI（警务、医院、学校、消防） | OpenStreetMap / Overpass API | ODbL 1.0 · © OpenStreetMap contributors | 565 个设施点，网页可按类别开关；警务点位为 OpenStreetMap 公开众包点位，非内部警力部署 |
| 基础设施/警务资源 | OSM 主干道路 | OpenStreetMap / Overpass API | ODbL 1.0 · © OpenStreetMap contributors | 9,078 条 motorway/trunk/primary/secondary 道路 |
| 人口/建筑暴露 | WorldPop 2020 1km 人口 | WorldPop | CC BY 4.0 · WorldPop (www.worldpop.org) | 180 个风险格网人口汇总，研究区裁剪后约 766.6 万人 |
| 人口/建筑暴露 | OSM 建筑轮廓（呼和浩特城区样例） | OpenStreetMap / Overpass API | ODbL 1.0 · © OpenStreetMap contributors | 1,613 栋建筑；网页只默认展示高度 ≥12m 的样例 |
| 地形/灾害 | AWS Terrain Tiles / Mapzen Terrarium DEM | AWS Open Data / Mapzen | CC-BY 4.0 / Mapzen | 研究区 DEM（约 48.7 m/px）、山体阴影、三维地形渲染 |
| 生态执法线索 | Sentinel-1 RTC | Microsoft Planetary Computer | CC-BY-4.0（collection 标注） | 雷达影像瓦片，网页按需叠加 |
| 生态执法线索 | IO LULC 2023 / 2017 | Microsoft Planetary Computer | CC-BY-4.0（collection 标注） | 10m 土地覆盖专题预览/瓦片 |
| 生态执法线索 | ALOS World 3D-30m | Microsoft Planetary Computer | “other” / JAXA 署名 | 地形预览，辅助 DEM 核查 |
| 生态执法线索 | MODIS LST（MYD11A1） | Microsoft Planetary Computer | NASA / collection 标注 | 地表温度预览，辅助热异常筛查 |

## 2. 未能直接接入的数据（如实说明）

- **Sentinel-5P SO2**：MPC 只提供原始 HDF 资产，`rendered_preview`/tilejson 返回 `No valid asset found`；网页不展示无法核验的伪彩色。
- **MODIS 积雪（MOD10A2）**：有 HDF 资产，但无服务端渲染预览；未强行本地读取 COG。
- **Microsoft Building Footprints**：MPC 的 tile/preview 对 `data` 资产返回 424；没有用在线预览冒充建筑数据。建筑 3D 样例使用 OSM 轮廓。
- **GPM IMERG**：在 AOI 上查询返回 0 features；不把“全球有数据”写成“研究区可用”。
- **HLS2**：collection 许可字段为 proprietary，未纳入公开页面。
- **Chloris Biomass**：CC-BY-NC-SA-4.0，非商业限制，未用于运营展示。
- **ArcGIS Pro Scene 3D**：无界面 ArcPy 会话中 `defaultCamera` 的 pitch/heading/Z 不可写，`Ground` 无法可靠挂载本地 DEM；因此 **没有伪造 ArcGIS 3D 截图**。2D 专题图由 ArcGIS Pro 导出，3D 地形由同一份 DEM 在本地做透视渲染，并在页面标注。

## 3. 生成与复现

```bat
cd /d <repo>
.venv\Scripts\python.exe crawler\open_data.py
.venv\Scripts\python.exe crawler\clip_population.py
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" arcgis\export_open_data.py
.venv\Scripts\python.exe arcgis\render_terrain_3d.py
```

输出：

```text
data/external/
  osm_pois.geojson
  osm_roads.geojson
  osm_buildings.geojson
  population_grid.geojson
  mpc_open_data.json
  sentinel1_preview.jpg / lulc_2023_preview.jpg / lulc_2017_preview.jpg
  alos_dem_preview.jpg / modis_lst_preview.jpg
  dem_aoi.tif / hillshade.png / terrain_color.jpg
  dem_bounds.json / dem_meta.json / sources.json
web/assets/
  arcgis_open_data_map.png    (ArcGIS Pro 2D 专题图)
  arcgis_terrain_3d.png       (DEM 三维透视)
  arcgis_open_data_meta.json
```

## 4. 网页接口

- `GET /api/open-data`：人口/设施/道路/建筑统计、数据源、许可、MPC/DEM/ArcGIS 元数据。
- `GET /api/mpc-tile/{key}/{z}/{x}/{y}`：同源代理 MPC 服务端瓦片；`key` 可取 `sentinel1`、`lulc_2023`、`alos_dem`、`modis_lst`。

## 5. 使用边界

- 本页是**公开演示**，没有登录/权限/审计；只展示公开众包点位，不展示真实警务布点、案件、人员、车辆等敏感数据。
- OSM 点位可能缺失、滞后或位置不精确；WorldPop 是 2020 年 1km 网格估计，不是实时人口。
- 人口格网与风险格网同为 180 格，便于联动，但不是官方统计口径。
- 任何用于真实警务的结论都必须经过内网授权数据复核和人工核查。


## 6. 中国省界、GMT 地形与 2D/3D

- 中国省界：`data/china_provinces.json`，35 个要素（34 个省级单元 + `100000_JD` 南海断续线），来自公开 DataV 边界数据；正式发布应以自然资源部门标准地图为准。
- 中国 DEM：`crawler/china_dem.py` 从 AWS Terrarium z6 拼接、重投影，产物 `data/external/china_dem.tif`、`china_dem_3857.tif`、`china_land_0p1.tif`。
- GMT 6.7.0（通过 PATH 或环境变量 `GMT_EXE` 指定可执行文件）：生成 `web/assets/gmt_china_2d.jpg`、`gmt_china_3d.jpg`、`gmt_china_mercator.jpg`；未安装 GMT 时相关入口自动隐藏。
- 网页：全国尺度用 OpenTopoMap + 真实省界；`2D 地图 / 3D 中国` 切换；3D 用 MapLibre GL JS（BSD-3-Clause）交互，ECharts GL 兜底；GMT 是静态制图，两者不要混淆。
- 研究区清晰度：zoom ≥ 6 自动隐藏 GMT 全国底图，显示 ArcGIS 高分辨率研究区底图；AOI 金色框与风险格网保持可见。

# GMT 中国 2D/3D 制图（GMT 6.7.0）

> 目的：用本机已安装的 GMT 生成高分辨率中国地形静态专题图和透视渲染，放入网页画廊；网页 2D 交互底图使用 OpenTopoMap，3D 使用 MapLibre 地形。
> 说明：GMT 用于**静态制图**；3D 画廊可拖拽改变方位/俯角并由服务端重新渲染 PNG。网页里的原生交互式中国三维使用 MapLibre GL JS（BSD-3-Clause），ECharts GL 作为兜底。

## 1. 环境与限制

- GMT 实际路径：`D:\数学建模工具\GMT\gmt6\bin\gmt.exe`，版本 **6.7.0**。
- 未加入系统 PATH；脚本中可临时：
  ```bash
  export PATH="/d/数学建模工具/GMT/gmt6/bin:$PATH"
  ```
- 本机 GMT 的远程数据下载被关闭（`grdcut @earth_relief_10m` 返回 `Remote download is currently deactivated`），GSHHG 海岸线也未安装成功，因此**没有使用 `pscoast`**。
- 中国 DEM 由 `crawler/china_dem.py` 从 AWS Terrain Tiles / Mapzen Terrarium z6 本地拼接、重投影得到；不依赖 GMT 远端数据。

## 2. 数据产物

| 文件 | 内容 |
|---|---|
| `data/external/china_dem_3857.tif` | 拼接后的 EPSG:3857 中国 DEM（约 10.6 MB） |
| `data/external/china_dem.tif` | 重投影到 EPSG:4326，2800×1600，0.025°（约 13 MB） |
| `data/external/china_land_0p1.tif` | 0.1° 平滑陆地 DEM，用于 3D 透视 |
| `data/external/china_provinces.gmt` | 省界 GMT 多段线 |
| `data/external/china_outline.gmt` | 国界外轮廓（shapely `buffer(0)` 修复拓扑后取外环） |
| `web/assets/gmt_china_2d.jpg` | GMT `grdimage` 中国 2D 地形图（1800×1139） |
| `web/assets/gmt_china_3d.jpg` | GMT `grdview` 中国 3D 透视（1800×966，静态） |
| `web/assets/gmt_render/china_3d_{az}_{el}.png` | 按方位/俯角动态渲染的 GMT 3D PNG（`/api/gmt/render`） |
| `web/assets/gmt_china_mercator.jpg` | Web Mercator 中国地形底图（2200×1595），供 Leaflet `imageOverlay` |

## 3. 复现命令

### 3.1 2D（Lambert 方位投影，适合汇报出图）

```bash
export PATH="/d/数学建模工具/GMT/gmt6/bin:$PATH"
export GMT_SESSION_NAME=china2d$$
gmt begin _tmp/gmt/china_2d_final png
gmt set PS_PAGE_COLOR black FONT_ANNOT_PRIMARY 8p,Helvetica,white FONT_LABEL 8p,Helvetica,white
gmt grdimage data/external/china_dem.tif -R70/140/15/55 -JL105/35/25/47/18c -I+d -Cgeo -Bafg -BWSne
gmt plot data/external/china_outline.gmt -R70/140/15/55 -JL105/35/25/47/18c -W1.0p,#f5d76e
gmt plot data/external/china_provinces.gmt -R70/140/15/55 -JL105/35/25/47/18c -W0.14p,#5f9bb8
printf '108.9 39.95\n112.8 39.95\n112.8 41.58\n108.9 41.58\n108.9 39.95\n' | \
  gmt plot -R70/140/15/55 -JL105/35/25/47/18c -W1.0p,#ff5a5f
gmt end
```

### 3.2 3D（grdview 透视）

```bash
gmt begin _tmp/gmt/china_3d_final png
gmt set PS_PAGE_COLOR black
gmt grdview data/external/china_land_0p1.tif -R70/140/15/55 -JL105/35/25/47/18c \
  -JZ2.8c -p140/24 -Qc -Cgeo -I+d -Bafg -BWSne
gmt plot data/external/china_outline.gmt -R70/140/15/55 -JL105/35/25/47/18c \
  -JZ2.8c -p140/24 -W0.8p,#f5d76e
gmt plot data/external/china_provinces.gmt -R70/140/15/55 -JL105/35/25/47/18c \
  -JZ2.8c -p140/24 -W0.12p,#5f9bb8
gmt end
```

### 3.3 Web Mercator 底图（给 Leaflet 叠加）

```bash
gmt begin _tmp/gmt/china_merc png
gmt set PS_PAGE_COLOR black
gmt grdimage data/external/china_dem.tif -R70/140/15/55 -JM20c -I+d -Cgeo
gmt plot data/external/china_outline.gmt -R70/140/15/55 -JM20c -W0.8p,#f5d76e
gmt plot data/external/china_provinces.gmt -R70/140/15/55 -JM20c -W0.12p,#8fd3f4
gmt end
```

## 4. 网页中的用法

- `web/app.js` 的 `buildChina()` 仍保留 GMT `imageOverlay` 作为离线备选，但当前默认透明度为 0；全国 2D 视图使用 OpenTopoMap 地形瓦片 + 真实中国省界/国界。
- “多源开放数据”画廊里有 `GMT 中国 2D`、`GMT 中国 3D` 两个标签。3D 标签内可直接拖拽图片：横向改变方位、纵向改变俯角，松手后调用 `/api/gmt/render` 重新渲染；也可用滑杆微调。
- 网页 3D 中国使用 MapLibre GL JS（`initTerrain3D()`）：Terrarium 地形 + `/api/terrain-color` 彩色晕渲 + 中国省界/国界 + AOI；ECharts GL `geo3D` 仅作为 WebGL/依赖失败时的兜底。研究区用金色标记显示。

## 5. 限制与合规说明

1. **不是原生 WebGL 地图**：GMT 3D 支持拖拽换视角，但每次松手后由服务端重新渲染 PNG；它仍不是浏览器的 WebGL 场景。原生可旋转/缩放的三维中国由 MapLibre 提供。
2. **不是测绘成果**：中国省界来自公开 GeoJSON（DataV 边界），仅用于态势展示；正式警务/政务发布应以自然资源部门标准地图为准。
3. **3D 是地形透视，不是实景三维**：GMT `grdview` 是静态 DEM 透视；网页交互式 3D 是 MapLibre 地形（无建筑白模）；ArcGIS Scene 的相机/高程源限制已在 README 说明。
4. **GMT 远程底图未使用**：GSHHG/远程高程不可用，当前 DEM 来自 AWS Terrarium z6，分辨率约 2.5 km；城市级细节需更高等级 DEM。
5. **许可**：GMT 为开源软件；DEM 来自 AWS Terrain Tiles/Mapzen Terrarium；使用时保留来源说明。ArcGIS Pro 只用于导出 2D 专题图，不在网页中运行。

## 6. 后续可选改进

- 用 `gmt grdfilter` 或更高等级 Terrarium 瓦片提高 3D 平滑度/分辨率；
- 安装 GSHHG 后补海岸线/湖泊；
- 用 `gmt pslegend` 生成正式图例；
- 服务端动态端点：`GET /api/gmt/render?az=150&el=30`；`app/gmt_render.py` 以相对路径调用 GMT，避免 Windows 中文绝对路径导致只输出边界黑图；结果缓存在 `web/assets/gmt_render/`。
- 把 GMT 制图接入刷新任务，生成带时间戳的每日专题图。

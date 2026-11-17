'use strict';

/* 多源开放数据：OSM / WorldPop / MPC / DEM。
   面板是独立模块，避免把 9k 条道路一次性塞进首屏加载。 */

const OD = {
  info: null,
  inited: false,
  groups: {},
  loaded: { pop: false, poi: false, roads: false, buildings: false, hillshade: false, sentinel1: false, lulc: false },
  view: 'map',
};

const OD_POP_COLORS = ['#0a84ff', '#22d3ee', '#ffd60a', '#ff9500', '#ff3b30'];
const OD_POI_COLORS = { police: '#38bdf8', hospital: '#ff6384', school: '#ffd60a', fire: '#ff9500' };
const OD_POI_LABELS = { police: '警务设施', hospital: '医疗机构', school: '学校', fire: '消防站' };

function odFmt(n) {
  const x = Number(n || 0);
  if (x >= 10000) return `${(x / 10000).toFixed(x >= 1000000 ? 1 : 0)}万`;
  return x.toLocaleString('zh-CN');
}

function odPanes(map) {
  const add = (name, z, pointer = 'auto') => {
    if (!map.getPane(name)) {
      const p = map.createPane(name);
      p.style.zIndex = String(z);
      p.style.pointerEvents = pointer;
    }
  };
  add('odRaster', 325, 'none');
  add('odTerrain', 335, 'none');
  add('odPop', 345, 'none');
  add('odRoads', 355, 'none');
  add('odBuilding', 365, 'none');
  add('odFacility', 650);
}

function odGroup(key) {
  if (!OD.groups[key]) {
    OD.groups[key] = L.layerGroup().addTo(state.map);
  }
  return OD.groups[key];
}

async function odJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function loadOdInfo() {
  try {
    const d = await odJSON('/api/open-data');
    OD.info = d;
    const stats = document.querySelectorAll('#od-stats b');
    if (stats.length >= 4) {
      stats[0].textContent = odFmt(d.population?.total);
      stats[1].textContent = odFmt(d.counts?.pois);
      stats[2].textContent = odFmt(d.counts?.roads);
      stats[3].textContent = odFmt(d.counts?.buildings);
    }
    const note = document.getElementById('od-note');
    if (note) {
      const src = (d.sources || []).length;
      note.textContent = `已接入 ${src} 类开源数据；许可与署名见“数据与许可”。人口统计：${odFmt(d.population?.total)} 人。`;
    }
  } catch (e) {
    const note = document.getElementById('od-note');
    if (note) note.textContent = '开放数据清单读取失败：' + e.message;
  }
}

async function buildPopulation() {
  if (OD.loaded.pop) return;
  const d = await odJSON('/data/external/population_grid.geojson');
  odGroup('pop').clearLayers();
  L.geoJSON(d, {
    pane: 'odPop',
    style: (f) => {
      const lv = Math.max(0, Math.min(4, Number((f.properties || {}).pop_level || 0)));
      return { color: 'rgba(231,242,255,.18)', weight: 0.4, fillColor: OD_POP_COLORS[lv],
        fillOpacity: lv === 0 ? 0.10 : 0.34 };
    },
  }).addTo(odGroup('pop'));
  OD.loaded.pop = true;
}

async function buildPoi() {
  if (OD.loaded.poi) return;
  const d = await odJSON('/data/external/osm_pois.geojson');
  odGroup('poi').clearLayers();
  L.geoJSON(d, {
    pane: 'odFacility',
    pointToLayer: (f, latlng) => {
      const kind = (f.properties || {}).kind || 'school';
      return L.circleMarker(latlng, {
        pane: 'odFacility', radius: kind === 'school' ? 3.2 : 4.6, weight: 1,
        color: 'rgba(2,7,13,.85)', fillColor: OD_POI_COLORS[kind] || '#38bdf8', fillOpacity: 0.9,
      });
    },
    onEachFeature: (f, layer) => {
      const p = f.properties || {};
      layer.bindPopup(`<div class="od-popup"><b>${p.name || '未命名'}</b><br>${OD_POI_LABELS[p.kind] || p.kind || '开放设施'}<br><span>${p.address || 'OSM 开放数据'}</span></div>`);
    },
  }).addTo(odGroup('poi'));
  OD.loaded.poi = true;
}

async function buildRoads() {
  if (OD.loaded.roads) return;
  const d = await odJSON('/data/external/osm_roads.geojson');
  odGroup('roads').clearLayers();
  const color = { motorway: '#f1d98b', trunk: '#d4af37', primary: '#7dd3fc', secondary: '#5b8fb9' };
  L.geoJSON(d, {
    pane: 'odRoads',
    style: (f) => {
      const h = (f.properties || {}).highway || 'secondary';
      return { pane: 'odRoads', color: color[h] || '#5b8fb9', weight: h === 'motorway' ? 1.7 : h === 'trunk' ? 1.4 : 0.9,
        opacity: 0.72 };
    },
  }).addTo(odGroup('roads'));
  OD.loaded.roads = true;
}

async function buildBuildings() {
  if (OD.loaded.buildings) return;
  const d = await odJSON('/data/external/osm_buildings.geojson');
  odGroup('buildings').clearLayers();
  L.geoJSON(d, {
    pane: 'odBuilding',
    style: () => ({ pane: 'odBuilding', color: 'rgba(226,240,255,.42)', weight: 0.5,
      fillColor: '#b7c9dd', fillOpacity: 0.16 }),
  }).addTo(odGroup('buildings'));
  OD.loaded.buildings = true;
}

function buildHillshade() {
  if (OD.loaded.hillshade) return;
  L.imageOverlay('/data/external/terrain_color.jpg', [[39.95, 108.9], [41.58, 112.8]], {
    pane: 'odTerrain', opacity: 0.55, interactive: false,
    attribution: 'DEM: AWS Terrain Tiles / Mapzen Terrarium',
  }).addTo(odGroup('hillshade'));
  OD.loaded.hillshade = true;
}

function buildMpcTiles() {
  if (OD.loaded.sentinel1) return;
  L.tileLayer('/api/mpc-tile/sentinel1/{z}/{x}/{y}', {
    pane: 'odRaster', opacity: 0.68, maxZoom: 18, maxNativeZoom: 18,
    attribution: 'Sentinel-1 RTC · Microsoft Planetary Computer',
  }).addTo(odGroup('sentinel1'));
  OD.loaded.sentinel1 = true;
}

function buildLulc() {
  if (OD.loaded.lulc) return;
  L.tileLayer('/api/mpc-tile/lulc_2023/{z}/{x}/{y}', {
    pane: 'odRaster', opacity: 0.52, maxZoom: 18, maxNativeZoom: 18,
    attribution: 'IO LULC 2023 · Microsoft Planetary Computer',
  }).addTo(odGroup('lulc'));
  OD.loaded.lulc = true;
}

async function ensureOdLayer(key) {
  try {
    if (key === 'pop') await buildPopulation();
    else if (key === 'poi') await buildPoi();
    else if (key === 'roads') await buildRoads();
    else if (key === 'buildings') await buildBuildings();
    else if (key === 'hillshade') buildHillshade();
    else if (key === 'sentinel1') buildMpcTiles();
    else if (key === 'lulc') buildLulc();
    const g = odGroup(key);
    if (!state.map.hasLayer(g)) g.addTo(state.map);
  } catch (e) {
    toast(`开放数据图层加载失败：${e.message}`, 'error');
  }
}

function odToggle(key, id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('change', async (e) => {
    if (e.target.checked) await ensureOdLayer(key);
    else {
      const g = OD.groups[key];
      if (g && state.map.hasLayer(g)) state.map.removeLayer(g);
    }
  });
}

function gmtUrl(az, el) {
  return `/api/gmt/render?az=${encodeURIComponent(az)}&el=${encodeURIComponent(el)}&v=${Date.now()}`;
}

function gmtRefresh() {
  const azEl = document.getElementById('gmt-az');
  const elEl = document.getElementById('gmt-el');
  const img = document.getElementById('od-modal-img');
  const open = document.getElementById('od-modal-open');
  const status = document.getElementById('gmt-status');
  if (!azEl || !elEl || !img) return;
  const az = Number(azEl.value), el = Number(elEl.value);
  document.getElementById('gmt-az-v').textContent = `${az}°`;
  document.getElementById('gmt-el-v').textContent = `${el}°`;
  const url = gmtUrl(az, el);
  if (status) status.textContent = 'GMT 正在渲染…';
  img.onload = () => { if (status) status.textContent = `已渲染 ${az}° / ${el}°`; };
  img.onerror = () => { if (status) status.textContent = '渲染失败，请稍后重试'; };
  img.src = url;
  if (open) open.href = url;
}

function gmtBindDrag(img) {
  if (!img || img.dataset.gmtDragBound === '1') return;
  img.dataset.gmtDragBound = '1';
  img.draggable = false;
  let drag = null;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  img.addEventListener('pointerdown', (e) => {
    if (OD.view !== 'gmt3d') return;
    const azEl = document.getElementById('gmt-az');
    const elEl = document.getElementById('gmt-el');
    if (!azEl || !elEl) return;
    e.preventDefault();
    drag = { x: e.clientX, y: e.clientY, az: Number(azEl.value), el: Number(elEl.value) };
    try { img.setPointerCapture?.(e.pointerId); } catch (_) {}
    img.style.cursor = 'grabbing';
    const status = document.getElementById('gmt-status');
    if (status) status.textContent = '拖拽中…松手自动重渲染';
  });
  img.addEventListener('pointermove', (e) => {
    if (!drag || OD.view !== 'gmt3d') return;
    e.preventDefault();
    const azEl = document.getElementById('gmt-az');
    const elEl = document.getElementById('gmt-el');
    if (!azEl || !elEl) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    const az = Math.round((drag.az + dx * 0.55 + 360) % 360);
    const el = Math.round(clamp(drag.el - dy * 0.30, 8, 60));
    azEl.value = String(az);
    elEl.value = String(el);
    document.getElementById('gmt-az-v').textContent = `${az}°`;
    document.getElementById('gmt-el-v').textContent = `${el}°`;
  });
  const finish = (e) => {
    if (!drag) return;
    drag = null;
    img.style.cursor = 'grab';
    try { img.releasePointerCapture?.(e.pointerId); } catch (_) {}
    gmtRefresh();
  };
  img.addEventListener('pointerup', finish);
  img.addEventListener('pointercancel', finish);
}

function odOpenModal(view = 'map') {
  const modal = document.getElementById('od-modal');
  if (!modal) return;
  OD.view = view;
  document.querySelectorAll('.od-tab').forEach((b) => b.classList.toggle('active', b.dataset.odView === view));
  const img = document.getElementById('od-modal-img');
  const desc = document.getElementById('od-modal-desc');
  const open = document.getElementById('od-modal-open');
  const meta = document.getElementById('od-modal-meta');
  if (view === 'gmt2d' || view === 'gmt3d') {
    const is3d = view === 'gmt3d';
    const controls = document.getElementById('gmt-controls');
    if (controls) controls.classList.toggle('hidden', !is3d);
    const file = is3d ? '/assets/gmt_china_3d.jpg' : '/assets/gmt_china_2d.jpg';
    img.alt = is3d ? 'GMT 中国三维地势图' : 'GMT 中国二维地势图';
    if (is3d) {
      const az = Number(document.getElementById('gmt-az')?.value || 150);
      const el = Number(document.getElementById('gmt-el')?.value || 30);
      const url = gmtUrl(az, el);
      img.onload = () => {
        const status = document.getElementById('gmt-status');
        if (status) status.textContent = `已渲染 ${az}° / ${el}°`;
      };
      img.src = url;
      open.href = url;
      img.style.cursor = 'grab';
      gmtBindDrag(img);
      desc.textContent = 'GMT 6.7.0 grdview 服务端按所选方位/俯角渲染中国地形、省界与研究区；可直接拖拽图片旋转，松手后自动重渲染。';
      meta.innerHTML = `
        <div><b>工具</b><span>GMT 6.7.0 · grdview · 服务端按视角渲染</span></div>
        <div><b>地形</b><span>AWS Terrarium z6 → 0.1° 平滑中国 DEM</span></div>
        <div><b>边界</b><span>省界 GeoJSON + 国界外轮廓 · 研究区 108.9–112.8°E / 39.95–41.58°N</span></div>
        <div><b>说明</b><span>拖拽旋转，松手重渲染；GMT 输出静态 PNG，网页内 WebGL 3D 使用 MapLibre 地形。</span></div>`;
    } else {
      img.style.cursor = 'default';
      img.src = `${file}?v=20261107gmt1`;
      open.href = `${file}?v=20261107gmt1`;
      desc.textContent = 'GMT 6.7.0 grdimage 渲染：中国地形、省界、经纬网与研究区红框；适合出图和态势汇报。';
      meta.innerHTML = `
        <div><b>工具</b><span>GMT 6.7.0 · grdimage</span></div>
        <div><b>地形</b><span>AWS Terrarium z6 → 0.025° 中国 DEM</span></div>
        <div><b>边界</b><span>省界 GeoJSON + 国界外轮廓 · 研究区 108.9–112.8°E / 39.95–41.58°N</span></div>
        <div><b>说明</b><span>静态制图；交互 3D 使用 MapLibre。</span></div>`;
    }
  } else if (view === 'terrain') {
    img.style.cursor = 'zoom-in';
    img.src = '/assets/arcgis_terrain_3d.png?v=20261107d';
    img.alt = 'DEM 三维地形态势';
    desc.textContent = '基于本地 DEM 的真实高程透视渲染，叠加高发点危险等级。ArcGIS Pro 的 Scene 相机在无界面 ArcPy 会话中不可写，因此不冒充 ArcGIS 3D 截图。';
    open.href = '/assets/arcgis_terrain_3d.png?v=20261107d';
    meta.innerHTML = `
      <div><b>数据</b><span>AWS Terrain Tiles / Mapzen Terrarium，3072×1792 px，约 48.7 m/px</span></div>
      <div><b>叠加</b><span>31 个高发点（按红/橙/黄/蓝危险等级着色）</span></div>
      <div><b>说明</b><span>3D 曲面为本地 DEM 渲染；2D 专题图由 ArcGIS Pro 导出。</span></div>`;
  } else {
    img.style.cursor = 'default';
    img.src = '/assets/arcgis_open_data_map.png?v=20261107d';
    img.alt = 'ArcGIS 多源开放数据专题图';
    desc.textContent = 'WorldPop 人口暴露、OSM 警务/医疗/学校/消防设施、主干道路、建筑样例叠加在 ArcGIS Pro 本地底图上。';
    open.href = '/assets/arcgis_open_data_map.png?v=20261107d';
    const arc = OD.info?.arcgis || {};
    const counts = OD.info?.counts || {};
    const cells = OD.info?.population?.cells || 180;
    meta.innerHTML = `
      <div><b>生成时间</b><span>${arc.generated_at || '—'}</span></div>
      <div><b>要素</b><span>人口 ${cells} 格 · POI ${counts.pois || 565} · 道路 ${odFmt(counts.roads || 9078)} · 建筑样例 40</span></div>
      <div><b>许可</b><span>© OpenStreetMap contributors (ODbL 1.0) · WorldPop (CC BY 4.0)</span></div>
      <div><b>底图</b><span>ArcGIS Pro 3.5 · 天地图影像（仅展示）</span></div>`;
  }
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
}

function odCloseModal() {
  const modal = document.getElementById('od-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
}

function initOpenData() {
  if (typeof state === 'undefined' || !state.map || !window.L) return false;
  if (OD.inited) return true;
  OD.inited = true;
  odPanes(state.map);
  loadOdInfo();

  odToggle('pop', 'od-pop');
  odToggle('poi', 'od-poi');
  odToggle('roads', 'od-roads');
  odToggle('buildings', 'od-buildings');
  odToggle('hillshade', 'od-hillshade');
  odToggle('sentinel1', 'od-sentinel1');
  odToggle('lulc', 'od-lulc');

  // 默认不加载大量点/面，避免遮住核查点；用户按需开启。

  const btn = document.getElementById('od-gallery-btn');
  if (btn) btn.addEventListener('click', () => odOpenModal('map'));
  const gmtBtn = document.getElementById('gmt-render-btn');
  if (gmtBtn) gmtBtn.addEventListener('click', gmtRefresh);
  ['gmt-az','gmt-el'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => {
      const v = document.getElementById(id + '-v');
      if (v) v.textContent = `${el.value}°`;
    });
  });
  document.querySelectorAll('.od-tab').forEach((b) => {
    b.addEventListener('click', () => odOpenModal(b.dataset.odView || 'map'));
  });
  const close = document.getElementById('od-close');
  if (close) close.addEventListener('click', odCloseModal);
  const modal = document.getElementById('od-modal');
  if (modal) {
    modal.addEventListener('click', (e) => { if (e.target === modal) odCloseModal(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') odCloseModal(); });
  }
  return true;
}

window.addEventListener('app-ready', () => initOpenData());
// app.js 的初始化是异步的；脚本加载顺序之外再做一次兜底。
(function odBoot() {
  if (initOpenData()) return;
  let n = 0;
  const t = setInterval(() => {
    if (initOpenData() || ++n > 50) clearInterval(t);
  }, 200);
})();

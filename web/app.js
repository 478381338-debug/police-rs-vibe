'use strict';

const API = '/api';
const $ = (id) => document.getElementById(id);
const BOOT_STARTED = performance.now();
const TERRAIN3D_VIEW = { center: [113.25, 38.85], zoom: 4.35, pitch: 55, bearing: -18 };

const state = {
  config: null,
  summary: null,
  hotspots: null,
  incidents: null,
  riskGrid: null,
  police: null,
  places: null,
  roads: null,
  aoi: null,
  china: null,
  chinaOutline: null,
  ndvi: null,
  samples: null,
  zq: { boundary: null, riskGrid: null, pois: null, roads: null, waterways: null, buildings: null },
  scenes: null,
  weather: null,
  tasks: [],
  catalog: null,
  selectedHazard: 'all',
  selectedHotspot: null,
  map: null,
  baseLayers: {},
  overlays: {},
  layersOn: {
    arcgisBase: true, china: true, s2: false, incidents: true, hotspots: true, grid: false, heat: false,
    weather: false, natural: true, samples: false, police: true, routes: true, places: false, aoi: true, zq: true,
  },
  china3d: null,
  terrain3d: null,
  terrain3dTried: false,
  chinaFitted: false,
  mapMode: '2d',
  refreshing: false,
  scenario: null,
  scenarioFrame: 0,
  scenarioTimer: null,
  scenarioPicking: false,
  scenarioPoint: null,
  scenarioCellLayers: {},
  scenarioGeo: null,
  scenarioMarker: null,
  scenarioAssess: null,
  scenarioFormLoaded: false,
  scenarioEditSeq: 0,
};

const FACTOR_LABELS = {
  severity: '类别严重度', count: '异常数量', area: '累计面积', recurrence: '复发性',
  terrain: '地形', access: '可达性', environment: '当前环境', confidence: '数据置信度',
};
const HAZARD_STATUS_LABELS = { validated: '已验证', screening: '扩展筛查', risk: '风险预警' };
const TYPE_LABELS = {
  vegetation: '植被', temperature: '地表温度', productivity: '生产力', fire: '过火',
  terrain: '地形', landcover: '土地覆盖', water: '水体', precipitation: '降水',
};

/* ------------------------------------------------------------------ */
/* 工具                                                               */
/* ------------------------------------------------------------------ */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtDate(iso, withTime = true) {
  if (!iso) return '--';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso).slice(0, 16);
  const p = (n) => String(n).padStart(2, '0');
  const base = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return withTime ? `${base} ${p(d.getHours())}:${p(d.getMinutes())}` : base;
}
function relTime(iso) {
  if (!iso) return '--';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '--';
  const mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins} 分钟前`;
  const h = Math.round(mins / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.round(h / 24)} 天前`;
}
async function fetchJSON(url, opts = {}) {
  const sep = url.includes('?') ? '&' : '?';
  const r = await fetch(url + (opts.noCache ? `${sep}_t=${Date.now()}` : ''), { cache: 'no-store', ...opts });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
let toastTimer = null;
function toast(msg, type = '') {
  const el = $('toast');
  el.textContent = msg;
  el.className = `toast show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast hidden'; }, type === 'warn' ? 8000 : 4200);
}
function colorForLevel(level) {
  const lv = (state.config?.danger_levels || []).find((x) => x.key === level);
  return lv ? lv.color : '#38bdf8';
}
function labelForLevel(level) {
  const lv = (state.config?.danger_levels || []).find((x) => x.key === level);
  return lv ? lv.label : level;
}
function levelForValue(v) {
  const levels = state.config?.danger_levels || [];
  const found = levels.find((x) => v >= x.min);
  return found ? found.key : 'blue';
}
function levelForNatural(v) {
  if (v >= 75) return 'red';
  if (v >= 60) return 'orange';
  if (v >= 45) return 'yellow';
  return 'blue';
}
function badge(level) {
  const short = { red: '一级', orange: '二级', yellow: '三级', blue: '常态' }[level] || labelForLevel(level);
  return `<span class="badge ${level}">${short}</span>`;
}
function hazardById(id) {
  return (state.catalog?.hazards || state.config?.hazards || []).find((h) => h.id === id);
}
function selectedHazardObj() {
  return state.selectedHazard === 'all' ? null : hazardById(state.selectedHazard);
}
function gridValue(props) {
  if (!props) return 0;
  if (state.selectedHazard === 'all') return Number(props.priority ?? props.risk ?? 0);
  const h = hazardById(state.selectedHazard);
  if (h?.status === 'risk') return Number(props[`cur_${state.selectedHazard}`] ?? props[`risk_${state.selectedHazard}`] ?? 0);
  return Number(props[`risk_${state.selectedHazard}`] ?? props.priority ?? props.risk ?? 0);
}

/* ------------------------------------------------------------------ */
/* 初始化                                                             */
/* ------------------------------------------------------------------ */
async function init() {
  const bootGuard = setTimeout(() => {
    if (!state.map) {
      hideBoot();
      toast('数据加载较慢，已先显示界面；可点击刷新重试', 'warn');
    }
  }, 18000);
  try {
    await loadAll();
    initMap();
    refreshOverlays();
    hideBoot();
    clearTimeout(bootGuard);
    // 道路数据较大，不阻塞首屏；首屏完成后再补载。
    setTimeout(() => {
      ensureRoads().then(() => {
        if (state.roads && state.layersOn.routes) buildPolice();
      });
    }, 900);
    window.dispatchEvent(new Event('app-ready'));
  } catch (e) {
    console.error(e);
    clearTimeout(bootGuard);
    hideBoot();
    toast('初始化失败：' + e.message, 'error');
  }
}

function hideBoot() {
  const b = $('boot-screen');
  if (!b || b.dataset.leaving === '1') return;
  b.dataset.leaving = '1';
  const wait = Math.max(0, 1600 - (performance.now() - BOOT_STARTED));
  setTimeout(() => {
    b.classList.add('boot-leaving');
    setTimeout(() => b.remove(), 900);
  }, wait);
}

let roadsPromise = null;
let zqDetailPromise = null;

async function ensureRoads() {
  if (state.roads?.features?.length) return state.roads;
  if (!roadsPromise) {
    roadsPromise = fetchJSON(`${API}/roads`)
      .then((data) => {
        state.roads = data;
        return data;
      })
      .catch((err) => {
        console.warn('roads lazy load failed', err);
        return null;
      })
      .finally(() => { roadsPromise = null; });
  }
  return roadsPromise;
}

async function ensureZqDetail() {
  if (state.zq.roads?.features?.length) return;
  if (!zqDetailPromise) {
    zqDetailPromise = Promise.all([
      fetchJSON('/data/zhangqiu/osm_roads.geojson').catch(() => null),
      fetchJSON('/data/zhangqiu/osm_waterways.geojson').catch(() => null),
      fetchJSON('/data/zhangqiu/osm_buildings.geojson').catch(() => null),
    ]).then(([roads, waterways, buildings]) => {
      if (!state.zq) return;
      state.zq.roads = roads;
      state.zq.waterways = waterways;
      state.zq.buildings = buildings;
    }).finally(() => { zqDetailPromise = null; });
  }
  return zqDetailPromise;
}

async function loadAll() {
  const safe = (p, d) => p.catch(() => d);
  const [cfg, summary, hotspots, incidents, grid, police, places, aoi, china, chinaOutline, scenes, weather, catalog, ndvi, samples, zqBoundary, zqRisk, zqPois, zqBuildings] = await Promise.all([
    fetchJSON(`${API}/config`),
    safe(fetchJSON(`${API}/summary`), {}),
    safe(fetchJSON(`${API}/hotspots`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/incidents`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/risk-grid`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/police`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/places`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/aoi`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON('/data/china_provinces.json'), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON('/data/china_outline.geojson'), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/scenes`), {}),
    safe(fetchJSON(`${API}/weather`), {}),
    safe(fetchJSON(`${API}/catalog`), { hazards: [] }),
    safe(fetchJSON(`${API}/ndvi`), {}),
    safe(fetchJSON(`${API}/verification-samples`), { summary: {}, features: [] }),
    safe(fetchJSON('/data/zhangqiu/boundary.geojson'), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON('/data/zhangqiu/risk_grid.geojson'), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON('/data/zhangqiu/osm_pois.geojson'), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON('/data/zhangqiu/osm_buildings.geojson'), { type: 'FeatureCollection', features: [] }),
  ]);
  state.config = cfg;
  state.summary = summary;
  state.hotspots = hotspots;
  state.incidents = incidents;
  state.riskGrid = grid;
  state.police = police;
  state.places = places;
  state.roads = null;
  state.aoi = aoi;
  state.china = china;
  state.chinaOutline = chinaOutline;
  state.scenes = scenes;
  state.weather = weather;
  state.tasks = [];
  state.catalog = catalog;
  state.ndvi = ndvi;
  state.samples = samples;
  state.zq = { boundary: zqBoundary, riskGrid: zqRisk, pois: zqPois, roads: null, waterways: null, buildings: zqBuildings };

  renderHeader();
  renderKpis();
  renderHazardOptions();
  renderCatalog();
  renderHotspotList();
  renderNaturalList();
  renderWeather();
  renderModel();
  renderQuality();
  renderTasks();
}

/* ------------------------------------------------------------------ */
/* 地图                                                               */
/* ------------------------------------------------------------------ */
function initMap() {
  const center = state.config?.aoi?.center || [111.05, 40.90];
  const zoom = state.config?.aoi?.zoom || 8;
  const map = L.map('map', {
    center: [center[1], center[0]],
    zoom,
    zoomControl: true,
    attributionControl: true,
    preferCanvas: true,
  });
  state.map = map;

  // ArcGIS 静态底图放在独立低层 pane，开放数据（人口/山影/影像）才能叠在底图之上，
  // 同时仍位于风险格网、热点标记之下。
  if (!map.getPane('arcgisBase')) {
    const p = map.createPane('arcgisBase');
    p.style.zIndex = '300';
    p.style.pointerEvents = 'none';
  }
  if (!map.getPane('chinaBase')) {
    const p = map.createPane('chinaBase');
    p.style.zIndex = '315';
    p.style.pointerEvents = 'none';
  }
  // 开放数据层可能晚于 app.js 初始化；先建 pane，避免 Canvas/SVG 渲染器找不到容器。
  [['odRaster', 325, 'none'], ['odTerrain', 335, 'none'], ['odPop', 345, 'none'],
   ['odRoads', 355, 'none'], ['odBuilding', 365, 'none'], ['odFacility', 650, 'auto']]
    .forEach(([name, z, pointer]) => {
      if (!map.getPane(name)) {
        const p = map.createPane(name);
        p.style.zIndex = String(z);
        p.style.pointerEvents = pointer;
      }
    });

  // 服务器可直接访问 Esri，全国视图用 World Imagery，研究区叠加 ArcGIS 高清影像。
  const esriImg = L.tileLayer('/api/tile/esri/{z}/{x}/{y}', {
    maxZoom: 17, maxNativeZoom: 17,
    errorTileUrl: '/static/assets/tile-fallback.png',
    attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics',
  });
  const osm = L.tileLayer('/api/tile/osm/{z}/{x}/{y}', {
    maxZoom: 18, maxNativeZoom: 17,
    errorTileUrl: '/static/assets/tile-fallback.png',
    attribution: '&copy; OpenStreetMap contributors',
  });
  state.baseLayers = { esriImg, osm };
  esriImg.addTo(map);

  state.overlays = {
    arcgisBase: L.layerGroup(),
    china: L.featureGroup(),
    aoi: L.layerGroup(),
    incidents: L.layerGroup(),
    hotspots: L.layerGroup(),
    grid: L.layerGroup(),
    heat: L.layerGroup(),
    weather: L.layerGroup(),
    natural: L.layerGroup(),
    samples: L.layerGroup(),
    police: L.layerGroup(),
    routes: L.layerGroup(),
    places: L.layerGroup(),
    s2: L.layerGroup(),
    zq: L.featureGroup(),
    scenario: L.layerGroup(),
  };
  // 默认叠加
  state.zqDetail = L.layerGroup();
  map.on('zoomend', () => { syncZqDetail(); syncChinaBaseOpacity(); });

  ['arcgisBase', 'china', 'incidents', 'hotspots', 'natural', 'police', 'routes', 'aoi', 'zq'].forEach((k) => state.overlays[k].addTo(map));
  ['s2', 'grid', 'heat', 'weather', 'places', 'samples'].forEach((k) => state.overlays[k].remove());

  // 底图切换（双击地图空白处循环？不，用右上角简易按钮）
  const BaseControl = L.Control.extend({
    options: { position: 'topright' },
    onAdd() {
      const div = L.DomUtil.create('div', 'leaflet-bar map-tool');
      const mk = (label, fn) => {
        const b = document.createElement('button');
        b.textContent = label;
        b.className = 'map-tool-btn';
        b.onmouseenter = () => (b.style.background = '#ecfdf5');
        b.onmouseleave = () => (b.style.background = 'transparent');
        b.onclick = fn;
        div.appendChild(b);
      };
      const is3d = () => state.mapMode === '3d' && state.terrain3d;
      mk('地形底图', () => { if (state.mapMode === '3d') setMapMode('2d'); map.removeLayer(esriImg); map.removeLayer(osm); esriImg.addTo(map); });
      mk('街道图', () => { if (state.mapMode === '3d') setMapMode('2d'); map.removeLayer(esriImg); map.removeLayer(osm); osm.addTo(map); });
      mk('双区域全图', () => is3d() ? resetTerrain3DView(true) : map.fitBounds(allRegionBounds(), { padding: [18,18], maxZoom: 6 }));
      mk('研究区影像', () => {
        if (is3d()) {
          state.terrain3d.easeTo({ center: [111.05, 40.90], zoom: 6.6, pitch: 55, bearing: 0 }, { duration: 700 });
          return;
        }
        map.fitBounds([[39.95,108.9],[41.58,112.8]], { padding: [20,20], maxZoom: 9 });
      });
      mk('复位视野', () => is3d() ? resetTerrain3DView(true) : map.setView([center[1], center[0]], zoom, { animate: true }));
      L.DomEvent.disableClickPropagation(div);
      return div;
    },
  });
  map.addControl(new BaseControl());

  // 初始视野：固定到研究区中心/缩放。
  // 不在初始化时 fitBounds：如果容器尚未完成布局，Leaflet 会把缩放推到 18 级。
  map.setView([center[1], center[0]], zoom, { animate: false });
  setupMapModes();
}

function refreshOverlays() {
  buildArcgisBase();
  buildChina();
  syncChinaBaseOpacity();
  if (state.map && !state.chinaZoomBound) {
    state.map.on('zoomend', syncChinaBaseOpacity);
    state.chinaZoomBound = true;
  }
  if (!state.chinaFitted) {
    const b = allRegionBounds();
    if (b && b.isValid() && state.map) {
      state.map.fitBounds(b, { padding: [18, 18], maxZoom: 6, animate: false });
      state.chinaFitted = true;
    }
  }
  buildZhangqiu();
  buildAoi();
  buildIncidents();
  buildHotspots();
  buildGrid();
  buildNatural();
  buildSamples();
  buildHeat();
  buildWeather();
  buildPolice();
  buildPlaces();
  buildS2Layers();
  // natural 叠加在切换时单独处理
  applyLayerState();
  updateLegendNote();
  // 仅当用户已经切到 3D 时初始化，避免在隐藏容器里创建 0 尺寸地图。
  if (state.mapMode === '3d') {
    setTimeout(() => {
      if (!state.terrain3d) initTerrain3D();
      state.terrain3d?.resize?.();
      state.map?.invalidateSize();
      if (!state.terrain3d) $('china3d-error')?.classList.remove('hidden');
    }, 220);
  }
}

function buildSamples() {
  clearGroup('samples');
  const feats = state.samples?.features || [];
  if (!feats.length) return;
  L.geoJSON({ type: 'FeatureCollection', features: feats }, {
    pointToLayer: (f, latlng) => {
      const p = f.properties || {};
      const positive = Number(p.label) === 1;
      return L.circleMarker(latlng, {
        radius: positive ? 4 : 3,
        color: positive ? '#b45309' : '#64748b',
        weight: 1,
        fillColor: positive ? '#f59e0b' : '#94a3b8',
        fillOpacity: positive ? 0.82 : 0.55,
      });
    },
    onEachFeature: (f, layer) => {
      const p = f.properties || {};
      layer.bindPopup(
        `<div class="pop-title">${esc(p.sample_id)}</div>
         <div class="pop-row"><span>类型</span><b>${p.label === 1 ? '历史异常' : '背景对照'}</b></div>
         <div class="pop-row"><span>已核实</span><b>${p.verified ? '是' : '否'}</b></div>
         <div class="pop-row"><span>质量</span><b>${esc(p.quality || '--')}</b></div>
         <div class="pop-row"><span>说明</span><b>${esc(p.note || '')}</b></div>`,
        { className: 'eco-popup' }
      );
    },
  }).addTo(state.overlays.samples);
}

function applyLayerState() {
  Object.entries(state.layersOn).forEach(([k, on]) => {
    const g = state.overlays[k];
    if (!g) return;
    if (on) { if (!state.map.hasLayer(g)) g.addTo(state.map); }
    else if (state.map.hasLayer(g)) state.map.removeLayer(g);
  });
  syncZqDetail();
}

function clearGroup(name) {
  const g = state.overlays[name];
  if (!g) return;
  g.clearLayers();
}

function buildArcgisBase() {
  clearGroup('arcgisBase');
  state.arcgisBaseOverlay = L.imageOverlay('/assets/arcgis_basemap_hd.jpg?t=' + Date.now(), [[39.95, 108.9], [41.58, 112.8]], {
    opacity: 1, interactive: false, pane: 'arcgisBase', className: 'arcgis-base',
    attribution: 'ArcGIS Pro · 天地图影像',
  }).addTo(state.overlays.arcgisBase);
}

function buildChina() {
  clearGroup('china');
  if (!state.china?.features?.length) return;
  state.gmtChinaOverlay = L.imageOverlay('/assets/gmt_china_mercator.jpg?v=20261108perf1', [[15, 70], [55, 140]], {
    opacity: 0.82, interactive: false, pane: 'chinaBase', className: 'gmt-china-base',
  }).addTo(state.overlays.china);
  L.geoJSON(state.china, {
    pane: 'chinaBase',
    renderer: L.svg({ pane: 'chinaBase' }),
    interactive: false,
    style: (f) => {
      const p = f.properties || {};
      const dashed = String(p.adcode || '').includes('_JD') || !p.name;
      return dashed
        ? { color: '#d4af37', weight: 1.7, opacity: 0.95, dashArray: '6 4', fill: false }
        : { color: '#2f7d8a', weight: 0.85, opacity: 0.62, fillColor: '#0f766e', fillOpacity: 0.035 };
    },
  }).addTo(state.overlays.china);
  if (state.chinaOutline?.features?.length) {
    L.geoJSON(state.chinaOutline, {
      pane: 'chinaBase',
      renderer: L.svg({ pane: 'chinaBase' }),
      interactive: false,
      style: { color: '#f5b301', weight: 2.3, opacity: 1, fill: false },
    }).addTo(state.overlays.china);
  }
}

function allRegionBounds() {
  return L.latLngBounds([[36.30, 108.70], [41.72, 117.95]]);
}

function zqLevelColor(level, risk) {
  if (level === '高' || risk >= 70) return '#dc2626';
  if (level === '较高' || risk >= 55) return '#f59e0b';
  if (level === '中' || risk >= 40) return '#facc15';
  return '#38bdf8';
}

function areaTagIcon(title, sub, cls) {
  return L.divIcon({
    className: 'dual-area-marker ' + (cls || ''),
    html: '<div class="dual-area-pin"><b>' + esc(title) + '</b><span>' + esc(sub) + '</span></div>',
    iconSize: [190, 46],
    iconAnchor: [95, 46],
  });
}

function buildZhangqiu() {
  clearGroup('zq');
  if (state.zqDetail) state.zqDetail.clearLayers();
  const zq = state.zq || {};
  if (!zq.boundary?.features?.length) return;

  // 章丘真实边界与内蒙古研究区同图显示，不再使用区域切换页。
  const boundaryLayer = L.geoJSON(zq.boundary, {
    style: { color: '#0f766e', weight: 2.4, opacity: .95, fillColor: '#0f766e', fillOpacity: .045, dashArray: '5 3' },
  }).bindPopup('<div class="pop-title">济南市章丘区</div><div class="pop-row"><span>边界</span><b>DataV 370114 真实边界</b></div><div class="pop-row"><span>说明</span><b>公开行政边界，不是警情</b></div>', { className: 'eco-popup' });
  boundaryLayer.addTo(state.overlays.zq);

  const risks = zq.riskGrid?.features || [];
  if (risks.length) {
    L.geoJSON(zq.riskGrid, {
      style: (f) => {
        const p = f.properties || {};
        const v = Number(p.risk) || 0;
        const c = zqLevelColor(p.level, v);
        return { color: c, weight: .35, opacity: .55, fillColor: c, fillOpacity: .34 };
      },
      onEachFeature: (f, layer) => {
        const p = f.properties || {};
        layer.bindPopup(
          '<div class="pop-title">章丘格网 · ' + esc(p.cell_id || '') + '</div>' +
          '<div class="pop-row"><span>关注指数</span><b>' + Number(p.risk || 0).toFixed(1) + ' / 100</b></div>' +
          '<div class="pop-row"><span>等级</span><b>' + esc(p.level || '--') + '</b></div>' +
          '<div class="pop-row"><span>主因</span><b>' + esc(p.dominant || '--') + '</b></div>' +
          '<div class="pop-row"><span>人口</span><b>' + Number(p.population || 0).toLocaleString() + '</b></div>' +
          '<div class="pop-row"><span>说明</span><b>公开数据筛查，不是警情或官方预警</b></div>',
          { className: 'eco-popup' }
        );
      },
    }).addTo(state.overlays.zq);
  }

  // 章丘真实 OSM 设施（警务设施突出显示）。
  if (zq.pois?.features?.length) {
    L.geoJSON(zq.pois, {
      pointToLayer: (f, latlng) => {
        const p = f.properties || {};
        const police = String(p.kind || '').includes('police');
        return L.circleMarker(latlng, {
          radius: police ? 4.2 : 2.2,
          color: police ? '#0f766e' : '#64748b',
          weight: police ? 1.8 : 0.8,
          fillColor: police ? '#14b8a6' : '#cbd5e1',
          fillOpacity: police ? .95 : .58,
        });
      },
      onEachFeature: (f, layer) => {
        const p = f.properties || {};
        layer.bindPopup(
          '<div class="pop-title">' + esc(p.name || p.kind || '公开设施') + '</div>' +
          '<div class="pop-row"><span>类型</span><b>' + esc(p.kind || '--') + '</b></div>' +
          '<div class="pop-row"><span>来源</span><b>OpenStreetMap · ODbL</b></div>' +
          '<div class="pop-row"><span>说明</span><b>公开设施点，不是警力部署</b></div>',
          { className: 'eco-popup' }
        );
      },
    }).addTo(state.overlays.zq);
  }

  const collegeIcon = L.divIcon({
    className: 'dual-college-marker',
    html: '<div class="dual-college-pin"><span>警</span><b>山东警察学院</b><small>明水校区</small></div>',
    iconSize: [168, 40],
    iconAnchor: [20, 38],
  });
  L.marker([36.6522767, 117.520686], { icon: collegeIcon, zIndexOffset: 1300, keyboard: false })
    .addTo(state.overlays.zq)
    .bindPopup('<div class="pop-title">山东警察学院（明水校区）</div><div class="pop-row"><span>类型</span><b>公安院校</b></div><div class="pop-row"><span>坐标</span><b>117.520686°E, 36.6522767°N</b></div><div class="pop-row"><span>来源</span><b>OpenStreetMap · ODbL</b></div><div class="pop-row"><span>说明</span><b>校园设施点位，不是警情或校区边界</b></div>', { className: 'eco-popup' });

  const nmLabel = L.marker([40.90, 111.05], {
    icon: areaTagIcon('内蒙古 · 大青山研究区', (state.summary?.hotspot_count ?? 31) + ' 个核查点 · 历史异常筛查', 'nm'),
    zIndexOffset: 1200, keyboard: false,
  }).addTo(state.overlays.zq);
  nmLabel.on('click', () => state.map.fitBounds([[39.95, 108.90], [41.58, 112.80]], { padding: [20, 20], maxZoom: 9, animate: true }));

  const zqBound = boundaryLayer.getBounds();
  const zqCount = risks.filter((f) => String(f.properties?.level || '').includes('较高')).length;
  const zqLabel = L.marker([36.76, 117.54], {
    icon: areaTagIcon('济南 · 章丘区', (risks.length || 430) + ' 格网 · ' + zqCount + ' 个较高关注', 'zq'),
    zIndexOffset: 1200, keyboard: false,
  }).addTo(state.overlays.zq);
  zqLabel.on('click', () => state.map.fitBounds(zqBound, { padding: [20, 20], maxZoom: 10, animate: true }));

  // 高倍缩放才加载道路、水系、建筑，避免全国视图被上万线面淹没。
  const detail = state.zqDetail;
  if (zq.roads?.features?.length) {
    L.geoJSON(zq.roads, { interactive: false, style: { color: '#b45309', weight: 1.1, opacity: .48 } }).addTo(detail);
  }
  if (zq.waterways?.features?.length) {
    L.geoJSON(zq.waterways, { interactive: false, style: { color: '#0284c7', weight: 1.2, opacity: .62 } }).addTo(detail);
  }
  if (zq.buildings?.features?.length) {
    L.geoJSON(zq.buildings, { interactive: false, style: { color: '#64748b', weight: .4, fillColor: '#94a3b8', fillOpacity: .25 } }).addTo(detail);
  }
  syncZqDetail();
}

function syncZqDetail() {
  if (!state.map || !state.zqDetail) return;
  const on = state.layersOn.zq && state.map.getZoom() >= 8;
  if (on) {
    if (!state.zq.roads) {
      ensureZqDetail().then(() => {
        if (state.map && state.layersOn.zq && state.map.getZoom() >= 8) {
          buildZhangqiu();
          if (state.zqDetail) state.zqDetail.addTo(state.map);
        }
      });
      return;
    }
    if (!state.map.hasLayer(state.zqDetail)) state.zqDetail.addTo(state.map);
  } else if (state.map.hasLayer(state.zqDetail)) {
    state.map.removeLayer(state.zqDetail);
  }
}

function syncChinaBaseOpacity() {
  if (!state.map) return;
  const zoom = state.map.getZoom();
  const c = state.map.getCenter();
  const national = zoom < 6;
  const inNm = zoom >= 6 && c.lng >= 108.4 && c.lng <= 113.2 && c.lat >= 39.5 && c.lat <= 42.0;
  // 双区域全图用一张本地 GMT 中国地形，避免全国尺度开放瓦片被限流产生黑块；
  // 放大到章丘/内蒙古后，再切换为开放底图与高分辨率研究区影像。
  state.gmtChinaOverlay?.setOpacity(national ? 1 : 0);
  state.arcgisBaseOverlay?.setOpacity(inNm ? 1 : 0);
  state.baseLayers?.esriImg?.setOpacity(national ? 0 : (inNm ? 0.32 : 1));
}

function chinaBounds() {
  const g = state.overlays.china;
  if (!g || !g.getLayers().length) return null;
  return g.getBounds();
}

function setupMapModes() {
  const map = state.map;
  if (!map) return;
  document.querySelectorAll('[data-map-mode]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mapMode === state.mapMode);
    btn.onclick = (e) => {
      e.preventDefault();
      setMapMode(btn.dataset.mapMode);
    };
  });
  const chinaBounds = L.latLngBounds([[17.5, 72.0], [54.5, 136.0]]);
  const fitChina = $('fit-china');
  if (fitChina) fitChina.onclick = () => {
    if (state.mapMode === '3d') setMapMode('2d');
    map.fitBounds(chinaBounds, { padding: [12, 12], maxZoom: 5, animate: true });
  };
  const fitAoi = $('fit-aoi');
  if (fitAoi) fitAoi.onclick = () => {
    if (state.mapMode === '3d') setMapMode('2d');
    map.fitBounds(allRegionBounds(), { padding: [18, 18], maxZoom: 6, animate: true });
  };
}

function setMapMode(mode) {
  const target = mode === '3d' ? '3d' : '2d';
  state.mapMode = target;
  document.querySelectorAll('[data-map-mode]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mapMode === target);
  });
  const box = $('china3d');
  if (!box) return;
  if (target === '3d') {
    box.classList.remove('hidden');
    $('china3d-error')?.classList.add('hidden');
    const terrainOk = initTerrain3D();
    if (terrainOk) {
      $('china3d-chart')?.classList.add('hidden');
      state.terrain3d?.resize?.();
      resetTerrain3DView(false);
      return;
    }
    $('china3d-chart')?.classList.remove('hidden');
    if (!initChina3D()) $('china3d-error')?.classList.remove('hidden');
    else state.china3d?.resize?.();
  } else {
    box.classList.add('hidden');
    state.map?.invalidateSize();
  }
}

function china3dGeo() {
  const src = state.china;
  if (!src?.features) return null;
  const ringArea = (ring) => {
    let a = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      a += (ring[j][0] * ring[i][1]) - (ring[i][0] * ring[j][1]);
    }
    return Math.abs(a / 2);
  };
  const clean = (f) => {
    if (String(f.properties?.adcode || '').includes('_JD')) return null;
    const g = f.geometry;
    if (!g) return null;
    const polys = g.type === 'MultiPolygon' ? g.coordinates
      : (g.type === 'Polygon' ? [g.coordinates] : []);
    if (!polys.length) return null;
    const ranked = polys
      .map((coords) => ({ coords, area: ringArea(coords[0] || []) }))
      .sort((a, b) => b.area - a.area);
    // 仅保留每个省级单元的主多边形；小岛在 geo3D 中会被拉成竖条，视觉上像异常柱体。
    return { ...f, geometry: { type: 'MultiPolygon', coordinates: [ranked[0].coords] } };
  };
  return { ...src, features: src.features.map(clean).filter(Boolean) };
}

function resetTerrain3DView(animate = false) {
  const map = state.terrain3d;
  if (!map) return;
  try {
    if (animate) map.easeTo({ ...TERRAIN3D_VIEW, duration: 650 });
    else map.jumpTo(TERRAIN3D_VIEW);
  } catch (e) {
    console.warn('terrain camera reset failed', e);
  }
}

function initTerrain3D() {
  if (state.terrain3d) return true;
  if (state.terrain3dTried && !state.terrain3d) return false;
  state.terrain3dTried = true;
  if (typeof maplibregl === 'undefined' || !state.china?.features?.length) return false;
  const el = $('terrain3d-map');
  if (!el) return false;
  try {
    const chinaData = state.china;
    const aoiData = state.aoi || {
      type: 'FeatureCollection',
      features: [],
    };
    const map = new maplibregl.Map({
      container: el,
      style: {
        version: 8,
        sky: {
          'sky-color': '#d7eef5',
          'horizon-color': '#edf8fa',
          'fog-color': '#edf8fa',
          'sky-horizon-blend': 0.55,
          'horizon-fog-blend': 0.55,
          'fog-ground-blend': 0.5,
        },
        sources: {
          terrain: {
            type: 'raster-dem',
            tiles: ['/api/terrain/{z}/{x}/{y}.png'],
            encoding: 'terrarium',
            tileSize: 256,
            maxzoom: 14,
          },
          china: { type: 'geojson', data: chinaData },
          outline: { type: 'geojson', data: state.chinaOutline || { type: 'FeatureCollection', features: [] } },
          relief: {
            type: 'raster',
            tiles: ['/api/terrain-color/{z}/{x}/{y}.png'],
            tileSize: 256,
            maxzoom: 14,
          },
          aoi: { type: 'geojson', data: aoiData },
          zqBoundary: { type: 'geojson', data: state.zq?.boundary || { type: 'FeatureCollection', features: [] } },
          zqRisk: { type: 'geojson', data: state.zq?.riskGrid || { type: 'FeatureCollection', features: [] } },
          zqBuildings: { type: 'geojson', data: state.zq?.buildings || { type: 'FeatureCollection', features: [] } },
          hotspots: { type: 'geojson', data: state.hotspots || { type: 'FeatureCollection', features: [] } },
          fallback: {
            type: 'image',
            url: '/assets/gmt_china_mercator_fast.jpg',
            coordinates: [[70, 55], [140, 55], [140, 15], [70, 15]],
          },
          hillshadeDem: {
            type: 'raster-dem',
            tiles: ['/api/terrain/{z}/{x}/{y}.png'],
            encoding: 'terrarium',
            tileSize: 256,
            maxzoom: 14,
          },
        },
        layers: [
          { id: 'bg', type: 'background', paint: { 'background-color': '#cfe8f2' } },
          {
            id: 'fallback', type: 'raster', source: 'fallback',
            paint: { 'raster-opacity': 0.72, 'raster-fade-duration': 0 },
          },
          {
            id: 'relief', type: 'raster', source: 'relief',
            paint: { 'raster-opacity': 0.88, 'raster-fade-duration': 0 },
          },
          {
            id: 'hillshade', type: 'hillshade', source: 'hillshadeDem',
            paint: { 'hillshade-exaggeration': 0.65, 'hillshade-shadow-color': '#06121c', 'hillshade-highlight-color': '#f8fafc' },
          },
          {
            id: 'china-fill', type: 'fill', source: 'china',
            paint: { 'fill-color': '#0d3550', 'fill-opacity': 0.16 },
          },
          {
            id: 'china-line', type: 'line', source: 'china',
            paint: { 'line-color': '#7dd3fc', 'line-width': 0.7, 'line-opacity': 0.85 },
          },
          {
            id: 'china-outline', type: 'line', source: 'outline',
            paint: { 'line-color': '#fbbf24', 'line-width': 2, 'line-opacity': 0.95 },
          },
          {
            id: 'aoi-fill', type: 'fill', source: 'aoi',
            paint: { 'fill-color': '#fbbf24', 'fill-opacity': 0.08 },
          },
          {
            id: 'aoi-line', type: 'line', source: 'aoi',
            paint: { 'line-color': '#fbbf24', 'line-width': 2, 'line-dasharray': [2, 2] },
          },
          {
            id: 'zq-risk', type: 'fill', source: 'zqRisk', minzoom: 7,
            paint: {
              'fill-color': ['interpolate', ['linear'], ['get', 'risk'], 40, '#facc15', 48, '#fb923c', 56, '#f97316', 65, '#dc2626'],
              'fill-opacity': .24,
            },
          },
          {
            id: 'zq-fill', type: 'fill', source: 'zqBoundary',
            paint: { 'fill-color': '#2dd4bf', 'fill-opacity': .18 },
          },
          {
            id: 'zq-boundary', type: 'line', source: 'zqBoundary',
            paint: { 'line-color': '#2dd4bf', 'line-width': 3, 'line-dasharray': [2, 1.5] },
          },
          {
            id: 'zq-buildings', type: 'fill-extrusion', source: 'zqBuildings', minzoom: 11.5,
            paint: {
              'fill-extrusion-color': '#64748b',
              'fill-extrusion-height': 18,
              'fill-extrusion-base': 6,
              'fill-extrusion-opacity': .48,
            },
          },
        ],
      },
      center: TERRAIN3D_VIEW.center,
      zoom: TERRAIN3D_VIEW.zoom,
      pitch: TERRAIN3D_VIEW.pitch,
      bearing: TERRAIN3D_VIEW.bearing,
      attributionControl: false,
      maxPitch: 75,
      preserveDrawingBuffer: true,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    try { map.addControl(new maplibregl.TerrainControl({ source: 'terrain', exaggeration: 2.2 }), 'bottom-left'); } catch (e) { console.warn('TerrainControl unavailable', e); }
    const enableTerrain = () => {
      if (map.getTerrain()) return;
      try {
        map.setTerrain({ source: 'terrain', exaggeration: 2.2 });
      } catch (e) {
        console.warn('terrain set failed', e);
      }
    };
    let initialViewDone = false;
    const applyView = () => {
      if (initialViewDone) return;
      initialViewDone = true;
      try {
        map.resize();
        map.jumpTo(TERRAIN3D_VIEW);
      } catch (e) {
        console.warn('terrain initial view failed', e);
      }
    };
    // style.load 比 load 更早触发；不要等所有栅格瓦片下载完才开启地形。
    map.once('style.load', enableTerrain);
    map.once('load', enableTerrain);
    map.once('style.load', applyView);
    map.once('load', applyView);
    setTimeout(enableTerrain, 800);
    setTimeout(applyView, 900);
    setTimeout(() => { try { map.triggerRepaint(); } catch (e) {} }, 1600);
    // MapLibre #8929: late 404s can leave load/idle pending; nudge a few repaints.
    let repaintTries = 0;
    const nudgeRepaint = () => {
      if (!map || repaintTries++ > 12 || map.loaded()) return;
      try { map.triggerRepaint(); } catch (e) {}
      setTimeout(nudgeRepaint, 500);
    };
    setTimeout(nudgeRepaint, 1200);
    map.on('error', (e) => console.warn('MapLibre terrain warning', e?.error?.message || e));
    new maplibregl.Marker({ color: '#fbbf24', scale: 0.85 })
      .setLngLat([111.05, 40.90])
      .setPopup(new maplibregl.Popup({ offset: 18 }).setText('内蒙古 · 大青山研究区'))
      .addTo(map);
    const zqEl = document.createElement('div');
    zqEl.className = 'zq-3d-region';
    zqEl.innerHTML = '<span>章丘区</span><small>430 格网</small>';
    new maplibregl.Marker({ element: zqEl, anchor: 'bottom', offset: [0, -26] })
      .setLngLat([117.54069, 36.71209])
      .setPopup(new maplibregl.Popup({ offset: 18 }).setHTML('<b>济南 · 章丘区</b><br>430 个公开数据格网'))
      .addTo(map);
    new maplibregl.Marker({ color: '#2dd4bf', scale: 0.7 })
      .setLngLat([117.520686, 36.6522767])
      .setPopup(new maplibregl.Popup({ offset: 14 }).setHTML('<b>山东警察学院（明水校区）</b><br>OSM 公开设施点'))
      .addTo(map);
    const collegeEl = document.createElement('div');
    collegeEl.className = 'dual-college-3d';
    collegeEl.innerHTML = '<div class="dual-college-pin"><span>警</span><b>山东警察学院</b><small>明水校区</small></div>';
    collegeEl.addEventListener('click', (e) => {
      e.stopPropagation();
      new maplibregl.Popup({ offset: 20, maxWidth: '330px' })
        .setLngLat([117.520686, 36.6522767])
        .setHTML('<b>山东警察学院（明水校区）</b><br>OSM 公开设施点 · ODbL<br><small>校园点位，不是警情或校区边界</small>')
        .addTo(map);
    });
    new maplibregl.Marker({ element: collegeEl, anchor: 'bottom' })
      .setLngLat([117.520686, 36.6522767])
      .addTo(map);
    state.terrain3d = map;
    window.addEventListener('resize', () => state.terrain3d?.resize?.());
    return true;
  } catch (e) {
    console.warn('MapLibre terrain init failed, fallback to ECharts GL', e);
    state.terrain3d = null;
    return false;
  }
}

function initChina3D() {
  if (state.china3d) return true;
  if (typeof echarts === 'undefined' || !state.china?.features?.length) return false;
  try {
    echarts.registerMap('china', china3dGeo());
    const chart = echarts.init($('china3d-chart'), null, { renderer: 'canvas' });
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { show: false },
      geo3D: {
        map: 'china',
        boxWidth: 100,
        boxHeight: 14,
        boxDepth: 72,
        regionHeight: 2.6,
        shading: 'realistic',
        itemStyle: { color: '#103b58', opacity: 0.96, borderWidth: 0.65, borderColor: '#79c7e8' },
        emphasis: { itemStyle: { color: '#d4af37' }, label: { show: false } },
        label: { show: false },
        light: {
          main: { intensity: 1.35, shadow: true, alpha: 38, beta: 28 },
          ambient: { intensity: 0.38 },
        },
        viewControl: {
          projection: 'perspective', autoRotate: false, distance: 92,
          alpha: 42, beta: 8, center: [0, 0, 0],
          minDistance: 45, maxDistance: 180,
          panSensitivity: 0.8, zoomSensitivity: 1.1, rotateSensitivity: 1,
        },
        postEffect: { enable: false },
        groundPlane: { show: false },
      },
      series: [{
        type: 'scatter3D',
        coordinateSystem: 'geo3D',
        data: [{ name: '研究区', value: [111.05, 40.90, 2600] }],
        symbolSize: 13,
        itemStyle: { color: '#ffd166', borderColor: '#fff3c4', borderWidth: 1.2 },
        label: { show: true, formatter: '研究区', color: '#ffe8a3', fontSize: 10, distance: 4 },
      }],
    });
    state.china3d = chart;
    window.addEventListener('resize', () => state.china3d?.resize());
    return true;
  } catch (e) {
    console.error('3D China init failed', e);
    state.china3d = null;
    return false;
  }
}

function buildAoi() {
  clearGroup('aoi');
  if (!state.aoi) return;
  L.geoJSON(state.aoi, {
    style: { color: '#22d3ee', weight: 1.4, dashArray: '7,7', fillColor: '#22d3ee', fillOpacity: 0.025 },
    interactive: false,
  }).addTo(state.overlays.aoi);
}

function buildIncidents() {
  clearGroup('incidents');
  if (!state.incidents) return;
  L.geoJSON(state.incidents, {
    style: (f) => ({
      color: f.properties.color || '#ff4d4f',
      weight: 1.2,
      fillColor: f.properties.color || '#ff4d4f',
      fillOpacity: 0.22,
    }),
    onEachFeature: (f, layer) => {
      const p = f.properties;
      layer.bindPopup(
        `<div class="pop-title">${esc(p.class)} #${esc(p.id)}</div>
         <div class="pop-row"><span>面积</span><b>${Number(p.area_ha || 0).toFixed(2)} ha</b></div>
         <div class="pop-row"><span>期次</span><b>${esc(p.period_label || '')}</b></div>
         <div class="pop-row"><span>来源</span><b>青山卫士检测结果</b></div>`,
        { className: 'eco-popup' }
      );
    },
  }).addTo(state.overlays.incidents);
}

function hotspotIcon(level) {
  const color = colorForLevel(level);
  return L.divIcon({
    className: '',
    html: `<div class="hs-marker" style="--c:${color}"><span></span><i></i></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

function buildHotspots() {
  clearGroup('hotspots');
  const feats = state.hotspots?.features || [];
  if (!feats.length) return;

  // 高发区范围（淡色）
  L.geoJSON(state.hotspots, {
    style: (f) => ({
      color: colorForLevel(f.properties.danger_level),
      weight: 1,
      fillColor: colorForLevel(f.properties.danger_level),
      fillOpacity: 0.06,
      dashArray: '3,5',
    }),
    interactive: false,
  }).addTo(state.overlays.hotspots);

  feats.forEach((f) => {
    const p = f.properties;
    const c = p.centroid || [0, 0];
    const marker = L.marker([c[1], c[0]], { icon: hotspotIcon(p.danger_level), riseOnHover: true });
    marker.bindPopup(
      `<div class="pop-title">${esc(p.name)} ${badge(p.danger_level)}</div>
       <div class="pop-row"><span>风险分</span><b style="color:${colorForLevel(p.danger_level)}">${p.risk_score}</b></div>
       <div class="pop-row"><span>主导类别</span><b>${esc(p.dominant_class)}</b></div>
       <div class="pop-row"><span>异常图斑</span><b>${p.incident_count} 个</b></div>
       <div class="pop-row"><span>累计面积</span><b>${Number(p.area_ha || 0).toFixed(1)} ha</b></div>
       <div class="pop-row"><span>最近警务点</span><b>${esc(p.nearest_police || '--')}</b></div>`,
      { className: 'eco-popup' }
    );
    marker.on('click', () => showHotspotDetail(p));
    marker.addTo(state.overlays.hotspots);
  });
}

function gridStyle(feature) {
  const v = gridValue(feature.properties);
  const lv = levelForValue(v);
  return {
    color: colorForLevel(lv),
    weight: 0.35,
    fillColor: colorForLevel(lv),
    fillOpacity: 0.22,
  };
}

function buildGrid() {
  clearGroup('grid');
  if (!state.riskGrid) return;
  L.geoJSON(state.riskGrid, {
    style: gridStyle,
    onEachFeature: (f, layer) => {
      layer.on('click', () => showGridDetail(f.properties));
      layer.bindTooltip(`${hazardById(f.properties.dominant_hazard)?.name || ''} ${gridValue(f.properties).toFixed(1)} 分`, { sticky: true });
    },
  }).addTo(state.overlays.grid);
}

function updateGridStyle() {
  if (!state.overlays.grid) return;
  state.overlays.grid.eachLayer((layer) => {
    if (layer.setStyle && layer.feature) layer.setStyle(gridStyle(layer.feature));
  });
  updateLegendNote();
}

function buildNatural() {
  clearGroup('natural');
  const data = state.riskGrid;
  if (!data) return;
  L.geoJSON(data, {
    pane: 'odTerrain',
    style: (f) => {
      const v = Number(f.properties?.current_danger ?? f.properties?.natural_risk ?? 0);
      const lv = f.properties?.current_danger_level || levelForNatural(v);
      return { color: 'rgba(8,20,35,.20)', weight: 0.3, fillColor: colorForLevel(lv), fillOpacity: 0.18 };
    },
    onEachFeature: (f, layer) => {
      const p = f.properties || {};
      const v = Number(p.current_danger ?? p.natural_risk ?? 0);
      layer.bindPopup(`<div class="pop-title">当前危险指数 · ${esc(p.cell_id)}</div>
        <div class="pop-row"><span>综合</span><b>${v.toFixed(1)} / 100</b></div>
        <div class="pop-row"><span>等级</span><b>${esc(p.current_danger_label || p.natural_label || '')}</b></div>
        <div class="pop-row"><span>主导</span><b>${esc(p.current_hazard_name || p.natural_hazard_name || '--')}</b></div>
        <div class="pop-row"><span>14 天峰值</span><b>${Number(p.forecast_peak || 0).toFixed(1)} · ${esc(p.forecast_peak_day || '--')}</b></div>
        <div class="pop-row"><span>人口暴露</span><b>${Number(p.current_population || 0).toLocaleString()} 人</b></div>
        <div class="pop-row"><span>气象点</span><b>${esc(p.current_weather_point || p.weather_point || '--')}</b></div>
        <div class="pop-row"><span>时间</span><b>${esc(fmtDate(p.current_weather_time || p.weather_time))}</b></div>`,
        { className: 'eco-popup' });
      layer.on('click', () => showGridDetail(p));
    },
  }).addTo(state.overlays.natural);
}

function buildHeat() {
  clearGroup('heat');
  const feats = state.incidents?.features || [];
  if (!feats.length || typeof L.heatLayer !== 'function') return;
  const pts = feats.map((f) => {
    const c = f.properties.centroid || [0, 0];
    return [c[1], c[0], Number(f.properties.severity || 0.5)];
  });
  L.heatLayer(pts, {
    radius: 30, blur: 26, maxZoom: 11, minOpacity: 0.18,
    gradient: { 0.2: '#0a84ff', 0.45: '#ffd60a', 0.7: '#ff9500', 1: '#ff3b30' },
  }).addTo(state.overlays.heat);
}

function buildWeather() {
  clearGroup('weather');
  const pts = state.weather?.points || [];
  pts.forEach((p) => {
    if (p.lon == null || p.lat == null) return;
    const c = p.current || {};
    const color = Number(c.temperature_2m) <= 0 ? '#7dd3fc' : '#fbbf24';
    L.circleMarker([p.lat, p.lon], {
      radius: 6, color, weight: 1.5, fillColor: color, fillOpacity: 0.35,
    }).bindPopup(`<div class="pop-title">气象点 · ${esc(p.name)}</div>
      <div class="pop-row"><span>气温</span><b>${c.temperature_2m != null ? Number(c.temperature_2m).toFixed(1) + ' °C' : '--'}</b></div>
      <div class="pop-row"><span>湿度</span><b>${c.relative_humidity_2m != null ? Number(c.relative_humidity_2m).toFixed(0) + '%' : '--'}</b></div>
      <div class="pop-row"><span>风速</span><b>${c.wind_speed_10m != null ? Number(c.wind_speed_10m).toFixed(1) + ' m/s' : '--'}</b></div>
      <div class="pop-row"><span>降水</span><b>${c.precipitation != null ? Number(c.precipitation).toFixed(1) + ' mm' : '--'}</b></div>
      <div class="pop-row"><span>来源/时间</span><b>Open-Meteo · ${esc(fmtDate(state.weather?.fetched_at))}</b></div>`,
      { className: 'eco-popup' }).addTo(state.overlays.weather);
  });
}

function policeIcon(type) {
  const sym = type.includes('检查') ? '检' : type.includes('巡护') ? '巡' : type.includes('派出所') ? '所' : '警';
  return L.divIcon({
    className: '',
    html: `<div class="police-marker">${sym}</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

function buildPolice() {
  clearGroup('police');
  clearGroup('routes');
  if (!state.police) return;
  L.geoJSON(state.police, {
    pointToLayer: (f, latlng) => L.marker(latlng, { icon: policeIcon(f.properties.type || '') }),
    onEachFeature: (f, layer) => {
      const p = f.properties;
      if (p.type === '巡护路线') return;
      layer.bindPopup(
        `<div class="pop-title">${esc(p.name)}</div>
         <div class="pop-row"><span>类型</span><b>${esc(p.type)}</b></div>
         <div class="pop-row"><span>地址</span><b>${esc(p.address || '--')}</b></div>
         <div class="pop-note">${esc(p.note || '')}</div>`,
        { className: 'eco-popup' }
      );
    },
  }).addTo(state.overlays.police);

  L.geoJSON(state.police, {
    filter: (f) => f.properties.type === '巡护路线',
    style: { color: '#38bdf8', weight: 2, opacity: 0.55, dashArray: '5,6' },
    interactive: false,
  }).addTo(state.overlays.routes);

  // 道路（细线）
  if (state.roads) {
    L.geoJSON(state.roads, {
      style: { color: '#94a3b8', weight: 1, opacity: 0.35 },
      interactive: false,
    }).addTo(state.overlays.routes);
  }
}

function buildPlaces() {
  clearGroup('places');
  if (!state.places) return;
  L.geoJSON(state.places, {
    pointToLayer: (f, latlng) => L.marker(latlng, {
      icon: L.divIcon({
        className: '',
        html: `<div class="place-marker">${esc(f.properties.name)}</div>`,
        iconSize: [1, 1],
        iconAnchor: [0, 0],
      }),
      interactive: false,
    }),
  }).addTo(state.overlays.places);
}

function buildS2Layers() {
  clearGroup('s2');
  const s2 = state.scenes?.sentinel2 || [];
  let added = 0;
  s2.forEach((s) => {
    if (s.tile_url) {
      const opts = {
        opacity: 0.72, maxZoom: 18,
        attribution: 'Sentinel-2 &copy; Microsoft Planetary Computer',
      };
      if (s.bbox && s.bbox.length >= 4) {
        opts.bounds = [[s.bbox[1], s.bbox[0]], [s.bbox[3], s.bbox[2]]];
      }
      L.tileLayer(s.tile_url, opts).addTo(state.overlays.s2);
      added++;
    } else if (s.preview && s.bbox && s.bbox.length >= 4) {
      const [x0, y0, x1, y1] = s.bbox;
      L.imageOverlay(s.preview, [[y0, x0], [y1, x1]], { opacity: 0.75 })
        .addTo(state.overlays.s2);
      added++;
    }
  });
  if (!added) {
    // 没有最新 S2 时，用第一张自然数据预览兜底
    const nat = (state.scenes?.natural || []).find((n) => n.preview && n.bbox?.length >= 4);
    if (nat) {
      const [x0, y0, x1, y1] = nat.bbox;
      L.imageOverlay(nat.preview, [[y0, x0], [y1, x1]], { opacity: 0.6 }).addTo(state.overlays.s2);
    }
  }
}

/* ------------------------------------------------------------------ */
/* 顶部/左栏渲染                                                       */
/* ------------------------------------------------------------------ */
function renderHeader() {
  const s = state.summary || {};
  const latest = s.mpc?.latest_s2 ? fmtDate(s.mpc.latest_s2, false) : '--';
  const weatherAt = s.weather?.fetched_at ? fmtDate(s.weather.fetched_at) : '--';
  $('last-update').textContent = s.updated_at
    ? `数据 ${relTime(s.updated_at)} · 影像 ${latest} · 气象 ${weatherAt}`
    : '等待数据';
  const dot = $('status-dot');
  dot.className = 'status-dot' + (s.updated_at ? '' : ' error');
  $('data-status').textContent = s.updated_at ? '在线 · 可核查' : '等待数据';
}

function renderKpis() {
  const s = state.summary || {};
  const incidents = s.incidents ?? (state.incidents?.features?.length || 0);
  const hotspots = s.hotspots ?? (state.hotspots?.features?.length || 0);
  const red = s.danger_counts?.red ?? 0;
  $('kpi-incidents').textContent = incidents;
  $('kpi-hotspots').textContent = hotspots;
  $('kpi-red').textContent = red;
  $('kpi-latest').textContent = s.mpc?.latest_s2 ? fmtDate(s.mpc.latest_s2, false) : '--';
  const chipRed = $('police-red'); if (chipRed) chipRed.textContent = red;
  const chipHotspots = $('police-hotspots'); if (chipHotspots) chipHotspots.textContent = hotspots;

  // 当前危险指数独立于历史核查优先级：自然致灾因子 × 人口暴露。
  const grid = state.riskGrid?.features || [];
  const currentVal = (f) => Number(f.properties?.current_danger ?? f.properties?.natural_risk);
  const vals = grid.map(currentVal).filter((v) => Number.isFinite(v));
  const maxN = vals.length ? Math.max(...vals) : null;
  const highN = grid.filter((f) => (f.properties?.current_danger_level || f.properties?.natural_level) === 'red').length;
  const elevatedN = grid.filter((f) => (f.properties?.current_danger_level || f.properties?.natural_level) === 'orange').length;
  const mediumN = grid.filter((f) => (f.properties?.current_danger_level || f.properties?.natural_level) === 'yellow').length;
  const topNatural = grid
    .filter((f) => Number.isFinite(currentVal(f)))
    .sort((a, b) => currentVal(b) - currentVal(a))[0]?.properties;
  const topEl = $('natural-risk-top');
  if (topEl) {
    topEl.textContent = maxN == null
      ? '--'
      : `${maxN.toFixed(0)} 分 · ${topNatural?.current_hazard_name || topNatural?.natural_hazard_name || '--'}`;
  }
  const descEl = $('natural-risk-desc');
  if (descEl) {
    const peak = grid
      .map((f) => f.properties || {})
      .filter((p) => Number.isFinite(Number(p.forecast_peak)))
      .sort((a, b) => Number(b.forecast_peak) - Number(a.forecast_peak))[0];
    const peakText = peak
      ? ` · 14天峰值 ${Number(peak.forecast_peak).toFixed(0)}（${peak.forecast_peak_day || '—'}）`
      : '';
    const p90s = grid.map((f) => Number(f.properties?.current_p90)).filter(Number.isFinite);
    const probs = grid.map((f) => Number(f.properties?.current_prob_high)).filter(Number.isFinite);
    const members = grid.map((f) => Number(f.properties?.current_ensemble_n)).filter(Number.isFinite);
    const p90Text = p90s.length ? ` · P90 ${Math.max(...p90s).toFixed(0)}` : '';
    const probText = probs.length ? ` · 集合 P(≥75) 最高 ${(Math.max(...probs) * 100).toFixed(0)}%` : '';
    const memberText = members.length ? ` · GFS ${Math.max(...members).toFixed(0)} 成员` : '';
    descEl.textContent = maxN == null
      ? '暂无当前危险指数数据'
      : `高风险 ${highN} 格 · 较高 ${elevatedN} 格 · 中等 ${mediumN} 格 · ${state.weather?.points?.length || 0} 个气象点 + DEM + 人口暴露${peakText}${p90Text}${probText}${memberText}`;
  }

  const top = (s.top_hotspots || [])[0];
  const focus = $('police-focus');
  const focusText = $('police-focus-text');
  if (top && focusText) {
    const nat = top.current_natural_risk == null
      ? '当前危险指数待算'
      : `当前危险指数 ${Number(top.current_natural_risk).toFixed(0)} · ${top.current_natural_label || ''}`;
    focusText.textContent = `${top.name} · ${labelForLevel(top.danger_level)} ${top.priority_score ?? top.risk_score} 分 · ${nat}`;
  } else if (focusText) {
    focusText.textContent = '暂无核查点数据';
  }
  if (focus) {
    focus.onclick = () => {
      if (!top) return;
      const f = (state.hotspots?.features || []).find((x) => String(x.properties.id) === String(top.id));
      if (!f) return;
      const p = f.properties;
      state.map.flyTo([p.centroid[1], p.centroid[0]], 11, { duration: 0.7 });
      showHotspotDetail(p);
    };
  }
}

function tickPoliceClock() {
  const el = $('police-clock');
  if (!el) return;
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  el.textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
setInterval(tickPoliceClock, 1000);
tickPoliceClock();

function renderHazardOptions() {
  const sel = $('hazard-filter');
  const hazards = state.catalog?.hazards || state.config?.hazards || [];
  const groups = [
    ['validated', '已验证类别'],
    ['screening', '扩展筛查类别'],
    ['risk', '气象/风险类别'],
  ];
  sel.innerHTML = '<option value="all">全部（核查优先级）</option>';
  groups.forEach(([status, label]) => {
    const hs = hazards.filter((h) => h.status === status);
    if (!hs.length) return;
    const og = document.createElement('optgroup');
    og.label = label;
    hs.forEach((h) => {
      const o = document.createElement('option');
      o.value = h.id;
      o.textContent = h.name;
      og.appendChild(o);
    });
    sel.appendChild(og);
  });
  sel.value = state.selectedHazard;
}

function renderCatalog() {
  const el = $('catalog-list');
  const hazards = state.catalog?.hazards || state.config?.hazards || [];
  if (!hazards.length) { el.innerHTML = '<div class="empty">目录为空</div>'; return; }
  el.innerHTML = hazards.map((h) => `
    <span class="catalog-chip ${h.status}" data-id="${esc(h.id)}" title="${esc(h.desc)}">
      ${esc(h.name)} · ${HAZARD_STATUS_LABELS[h.status] || h.status}
    </span>`).join('');
  el.querySelectorAll('.catalog-chip').forEach((chip) => {
    chip.onclick = () => {
      state.selectedHazard = chip.dataset.id;
      $('hazard-filter').value = chip.dataset.id;
      onHazardChange();
    };
  });
}

function onHazardChange() {
  state.selectedHazard = $('hazard-filter').value || 'all';
  renderHotspotList();
  updateGridStyle();
  if (state.selectedHazard !== 'all') {
    const h = selectedHazardObj();
    if (h) toast(`已切换到：${h.name}（${HAZARD_STATUS_LABELS[h.status]}）`);
  }
}

function renderHotspotList() {
  const el = $('hotspot-list');
  const sel = selectedHazardObj();
  const title = $('list-title');
  const countEl = $('list-count');

  if (sel && sel.status !== 'validated') {
    // 扩展类型：用风险格网 Top 10
    const feats = (state.riskGrid?.features || [])
      .map((f) => ({ props: f.properties, geom: f.geometry }))
      .sort((a, b) => Number(b.props[`risk_${sel.id}`] ?? 0) - Number(a.props[`risk_${sel.id}`] ?? 0))
      .slice(0, 10);
    title.textContent = `${sel.name} · 风险格网 Top 10`;
    countEl.textContent = `${feats.length} 格`;
    if (!feats.length) { el.innerHTML = '<li class="empty">暂无格网数据</li>'; return; }
    el.innerHTML = feats.map((f, i) => {
      const v = Number(f.props[`risk_${sel.id}`] ?? 0);
      const lv = levelForValue(v);
      const c = centerOfGeom(f.geom);
      return `<li class="hs-item" data-cell="${esc(f.props.cell_id)}" data-lon="${c[0]}" data-lat="${c[1]}">
        <div class="hs-rank">${String(i + 1).padStart(2, '0')}</div>
        <div class="hs-main">
          <div class="hs-name">格网 ${esc(f.props.cell_id)} ${badge(lv)}</div>
          <div class="hs-meta">主导：${esc(f.props.dominant_hazard_name || '')} · 综合 ${Number(f.props.risk || 0).toFixed(0)}</div>
        </div>
        <div class="hs-score"><b style="color:${colorForLevel(lv)}">${v.toFixed(0)}</b><span>风险分</span></div>
      </li>`;
    }).join('');
    el.querySelectorAll('.hs-item').forEach((li) => {
      li.onclick = () => {
        const feat = (state.riskGrid?.features || []).find((x) => x.properties.cell_id === li.dataset.cell);
        if (feat) {
          state.map.flyTo([Number(li.dataset.lat), Number(li.dataset.lon)], 10, { duration: 0.6 });
          showGridDetail(feat.properties);
        }
      };
    });
    return;
  }

  // 已验证类别或全部：高发点
  let feats = state.hotspots?.features || [];
  if (sel) feats = feats.filter((f) => f.properties.dominant_class === sel.name);
  feats = [...feats].sort((a, b) => b.properties.risk_score - a.properties.risk_score).slice(0, 10);
  title.textContent = sel ? `${sel.name} · 核查点 Top 10` : '核查点 Top 10';
  countEl.textContent = `${feats.length} 处`;
  if (!feats.length) { el.innerHTML = '<li class="empty">该类型暂无高发点</li>'; return; }
  el.innerHTML = feats.map((f, i) => {
    const p = f.properties;
    const lv = p.danger_level;
    return `<li class="hs-item" data-id="${esc(p.id)}">
      <div class="hs-rank">${String(i + 1).padStart(2, '0')}</div>
      <div class="hs-main">
        <div class="hs-name">${esc(p.name)} ${badge(lv)}</div>
        <div class="hs-meta">${esc(p.dominant_class)} · ${p.incident_count} 图斑 · 坡度 ${p.slope_deg}° · 距警务点 ${p.dist_police_km}km<br>当前危险指数：${p.current_natural_risk == null ? '不适用' : Number(p.current_natural_risk).toFixed(0) + ' · ' + esc(p.current_natural_label || '')}</div>
      </div>
      <div class="hs-score"><b style="color:${colorForLevel(lv)}">${Number(p.priority_score ?? p.risk_score).toFixed(0)}</b><span>${esc(p.danger_label || '')}</span></div>
    </li>`;
  }).join('');
  el.querySelectorAll('.hs-item').forEach((li) => {
    li.onclick = () => {
      const feat = (state.hotspots?.features || []).find((x) => String(x.properties.id) === li.dataset.id);
      if (feat) {
        const p = feat.properties;
        state.map.flyTo([p.centroid[1], p.centroid[0]], 11, { duration: 0.6 });
        showHotspotDetail(p);
      }
    };
  });
}

function centerOfGeom(geom) {
  if (!geom) return [111.05, 40.9];
  let xs = [], ys = [];
  const walk = (c) => {
    if (Array.isArray(c) && typeof c[0] === 'number') { xs.push(c[0]); ys.push(c[1]); return; }
    if (Array.isArray(c)) c.forEach(walk);
  };
  walk(geom.coordinates || []);
  return [xs.reduce((a, b) => a + b, 0) / xs.length, ys.reduce((a, b) => a + b, 0) / ys.length];
}

/* ------------------------------------------------------------------ */
/* 右栏渲染                                                           */
/* ------------------------------------------------------------------ */
function showHotspotDetail(p) {
  state.selectedHotspot = p.id;
  const lv = p.danger_level;
  const color = colorForLevel(lv);
  const factors = p.factors || {};
  const weights = p.priority_weights || p.weights || {};
  const priorityKeys = ['severity', 'count', 'area', 'recurrence', 'terrain', 'access', 'confidence'];
  const factorRows = priorityKeys
    .filter((k) => k in factors)
    .map((k) => {
      const v = Number(factors[k] || 0);
      return `<div class="factor-row" title="权重 ${weights[k] ?? '-'}">
        <span>${FACTOR_LABELS[k]}</span>
        <div class="bar"><i style="width:${Math.max(2, v * 100)}%"></i></div>
        <b>${(v * 100).toFixed(0)}</b>
      </div>`;
    }).join('');
  const score = Number(p.priority_score ?? p.risk_score);
  const nat = p.current_natural_risk;
  const natColor = colorForLevel(p.current_natural_level || 'blue');
  const natText = nat == null ? '不适用' : Number(nat).toFixed(1);
  const weatherLine = `${esc(p.weather_point || '--')} · ${esc(fmtDate(p.weather_time))} · Open-Meteo`;

  $('detail-body').innerHTML = `
    <div class="detail-title">
      <div><h3>${esc(p.name)} ${badge(lv)}</h3></div>
      <div class="score" style="color:${color}">${score.toFixed(1)}</div>
    </div>
    <div class="detail-sub">历史核查优先级 · 排名 ${p.rank ?? '-'} · 主导类别 ${esc(p.dominant_class)} · ${p.incident_count} 个图斑 · 累计 ${Number(p.area_ha).toFixed(1)} ha</div>
    <div class="detail-grid">
      <div class="detail-cell"><span>核查优先级</span><b style="color:${color}">${esc(p.danger_label || labelForLevel(lv))}</b></div>
      <div class="detail-cell"><span>当前危险指数</span><b style="color:${natColor}">${natText}${nat == null ? '' : ' / 100'}</b></div>
      <div class="detail-cell"><span>复发性</span><b>${p.recurrence} 类/期</b></div>
      <div class="detail-cell"><span>坡度 / 高程</span><b>${p.slope_deg}° / ${Math.round(p.elev_m)} m</b></div>
      <div class="detail-cell"><span>距最近警务点</span><b>${p.dist_police_km} km</b></div>
      <div class="detail-cell"><span>最近警务点</span><b>${esc(p.nearest_police || '--')}</b></div>
      <div class="detail-cell"><span>距道路 / 村庄</span><b>${p.dist_road_km} / ${p.dist_place_km} km</b></div>
      <div class="detail-cell"><span>气象点 / 数据</span><b>${weatherLine}</b></div>
      <div class="detail-cell"><span>集合 P10–P90</span><b>${Number(p.current_p10 ?? 0).toFixed(1)}–${Number(p.current_p90 ?? 0).toFixed(1)} · ${Number(p.current_ensemble_n || 0)} 成员</b></div>
      <div class="detail-cell"><span>暴雨量级对照</span><b>${esc(p.current_rainstorm_label || '—')} / 预测 ${esc(p.forecast_rainstorm_label || '—')}</b></div>
    </div>
    <div class="priority-callout">一级/二级/三级核查只表示历史异常线索的处置先后，不是灾害发生概率。</div>
    <div class="section-label">历史核查因子（不含当前天气）</div>
    ${factorRows}
    <div class="section-label">当前危险指数（自然因子 × 暴露）</div>
    <div class="natural-risk-box">
      <div class="nr-line"><span>${esc(p.current_natural_label || '当前自然风险')}</span><b style="color:${natColor}">${natText}</b></div>
      <p>当前危险指数使用气象、GFS 集合、NDVI、地形与人口暴露；历史异常不参与该分数。</p>
      <p>${Number(p.current_ensemble_n || 0)} 个 GFS 成员 · P(≥75) ${(Number(p.current_prob_high || 0) * 100).toFixed(0)}% · P(≥60) ${(Number(p.current_prob_elevated || 0) * 100).toFixed(0)}% · 非校准概率</p>
      <p>气象点：${weatherLine}</p>
    </div>
    <div class="section-label">历史异常证据</div>
    <ul class="reason-list">${(p.reasons || []).map((r) => `<li>${esc(r)}</li>`).join('') || '<li>暂无</li>'}</ul>
    <div class="action-box"><b>处置建议：</b>${esc(p.recommended_action || p.advice || '')}</div>
    <div class="detail-actions">
      <button class="mini-btn" id="btn-locate">地图定位</button>
      <button class="mini-btn primary" id="btn-task">生成核查任务</button>
    </div>
  `;
  $('btn-locate').onclick = () => state.map.flyTo([p.centroid[1], p.centroid[0]], 12, { duration: 0.7 });
  $('btn-task').onclick = () => createTask(p);
}

function showGridDetail(p) {
  const sel = selectedHazardObj();
  const naturalMode = sel?.status === 'risk';
  const current = Number(p.current_danger ?? p.natural_risk ?? 0);
  const currentLv = p.current_danger_level || p.natural_level || levelForNatural(current);
  const currentColor = colorForLevel(currentLv);
  const selectedValue = naturalMode
    ? Number(p[`cur_${sel.id}`] ?? current)
    : Number(p.priority ?? p.risk ?? 0);
  const lv = naturalMode ? levelForNatural(selectedValue) : levelForValue(selectedValue);
  const color = colorForLevel(lv);
  const hazardName = naturalMode ? sel.name : (p.current_hazard_name || p.dominant_hazard_name || p.dominant_hazard);
  const rowsObj = naturalMode
    ? Object.fromEntries(Object.entries(p).filter(([k]) => k.startsWith('cur_')))
    : (p.current_components || {});
  const riskRows = Object.keys(rowsObj)
    .filter((k) => typeof rowsObj[k] === 'number')
    .sort((a, b) => rowsObj[b] - rowsObj[a])
    .slice(0, 6)
    .map((k) => {
      const id = k.replace(/^(cur_|risk_)/, '');
      const nm = hazardById(id)?.name || { fire_risk: '火灾风险', flood_risk: '洪涝风险', drought_risk: '干旱风险', geohazard_risk: '地质灾害', landslide: '滑坡', debris_flow: '泥石流' }[id] || id;
      return `<div class="factor-row"><span>${esc(nm)}</span><div class="bar"><i style="width:${Math.max(2, rowsObj[k])}%"></i></div><b>${rowsObj[k].toFixed(0)}</b></div>`;
    }).join('');
  const peak = Number(p.forecast_peak ?? 0);
  const peakDay = p.forecast_peak_day ? fmtDate(p.forecast_peak_day, false) : '--';
  const weatherLine = `${esc(p.current_weather_point || p.weather_point || '--')} · ${esc(fmtDate(p.current_weather_time || p.weather_time))} · Open-Meteo`;

  $('detail-body').innerHTML = `
    <div class="detail-title">
      <div><h3>格网 ${esc(p.cell_id)} · ${esc(hazardName)} ${badge(lv)}</h3></div>
      <div class="score" style="color:${color}">${selectedValue.toFixed(1)}</div>
    </div>
    <div class="detail-sub">${naturalMode ? '当前危险指数 · 自然致灾因子 × 人口暴露' : '历史异常核查优先级'} · 历史密度 ${Number(p.hist || 0).toFixed(2)}</div>
    <div class="detail-grid">
      <div class="detail-cell"><span>当前危险指数</span><b style="color:${currentColor}">${current.toFixed(1)} / 100</b></div>
      <div class="detail-cell"><span>14 天预测峰值</span><b>${peak.toFixed(1)} · ${esc(peakDay)}</b></div>
      <div class="detail-cell"><span>峰值主导</span><b>${esc(p.forecast_peak_hazard_name || p.current_hazard_name || '--')}</b></div>
      <div class="detail-cell"><span>情景区间 P10–P90</span><b>${Number(p.current_p10 ?? 0).toFixed(1)}–${Number(p.current_p90 ?? 0).toFixed(1)}</b></div>
      <div class="detail-cell"><span>超阈值情景</span><b>P(≥75) ${(Number(p.current_prob_high || 0) * 100).toFixed(1)}% · P(≥60) ${(Number(p.current_prob_elevated || 0) * 100).toFixed(1)}%</b></div>
      <div class="detail-cell"><span>历史核查优先级</span><b>${Number(p.priority ?? p.risk ?? 0).toFixed(1)}</b></div>
      <div class="detail-cell"><span>人口暴露</span><b>${Number(p.current_population || 0).toLocaleString()} 人 · ${esc(p.current_pop_label || '--')}</b></div>
      <div class="detail-cell"><span>暴露 / 高程</span><b>${Number(p.current_exposure || 0).toFixed(2)} / ${Math.round(p.elev_m)} m</b></div>
      <div class="detail-cell"><span>坡度</span><b>${p.slope_deg}°</b></div>
      <div class="detail-cell"><span>气象点 / 数据</span><b>${weatherLine}</b></div>
      <div class="detail-cell"><span>集合成员</span><b>${Number(p.current_ensemble_n || 0)} · ${esc(p.current_probability_type || '—')}</b></div>
      <div class="detail-cell"><span>暴雨量级对照</span><b>${esc(p.current_rainstorm_label || '—')} / 预测 ${esc(p.forecast_rainstorm_label || '—')}</b></div>
      <div class="detail-cell"><span>受影响人口（P90 情景）</span><b>${Number(p.current_affected_population || 0).toLocaleString()} 人</b></div>
    </div>
    <div class="priority-callout">当前危险指数是自然致灾因子与人口暴露的情景估计；P10–P90 来自 GFS 集合成员，不是校准概率或确定性预报。暴雨等级仅作公开预警量级对照。</div>
    <div class="section-label">当前危险构成（0-100）</div>
    ${riskRows || '<div class="empty">暂无细分危险数据</div>'}
    <div class="action-box"><b>说明：</b>当前 + 14 天预报；集合频率是情景分布，暴雨等级不是官方预警；需结合现场核查。</div>
    <div class="detail-actions">
      <button class="mini-btn" id="btn-locate-cell">地图定位</button>
      <button class="mini-btn primary" id="btn-task-cell">生成核查任务</button>
    </div>
  `;
  const c = centerOfGeomFromProps(p);
  $('btn-locate-cell').onclick = () => state.map.flyTo([c[1], c[0]], 11, { duration: 0.7 });
  $('btn-task-cell').onclick = () => createTask({
    id: p.cell_id, name: `格网 ${p.cell_id}（${hazardName}）`,
    danger_label: currentLv, centroid: c,
  });
}

function centerOfGeomFromProps(p) {
  // 格网中心：从当前选中的格网 feature 找；找不到就用研究区中心
  const feat = (state.riskGrid?.features || []).find((f) => f.properties.cell_id === p.cell_id);
  if (feat) return centerOfGeom(feat.geometry);
  return [111.05, 40.9];
}

function createTask(h) {
  const isGrid = Boolean(h.cell_id);
  const sourceType = isGrid ? 'grid' : 'hotspot';
  const sourceId = isGrid ? h.cell_id : h.id;
  const url = `/workspace?source_type=${encodeURIComponent(sourceType)}&source_id=${encodeURIComponent(sourceId)}`;
  toast('已打开案件工作台：登录后核对线索并建案，不在此处直接生成任务。');
  window.open(url, '_blank', 'noopener');
}

function renderTasks() {
  const el = $('task-list');
  $('task-count').textContent = '0';
  el.innerHTML = '<li class="empty">案件已统一进入<a href="/workspace" target="_blank" rel="noopener">案件工作台</a>；建案、派单、证据、复核和归档都在那里完成。</li>';
}

/* ------------------------------------------------------------------ */
/* 自然数据 / 气象 / 模型                                              */
/* ------------------------------------------------------------------ */
function renderNaturalList() {
  const el = $('natural-list');
  const s2 = state.scenes?.sentinel2 || [];
  const landsat = state.scenes?.landsat || [];
  const natural = (state.scenes?.natural || []).filter((n) => n.ok);
  const cards = [];

  if (s2[0]) {
    cards.push({
      title: 'Sentinel-2 最新影像',
      sub: `${fmtDate(s2[0].datetime, false)} · 云量 ${s2[0].cloud_cover != null ? Number(s2[0].cloud_cover).toFixed(1) + '%' : '--'}`,
      img: s2[0].preview,
      tag: '10 m 光学',
      kind: 's2',
      payload: s2[0],
    });
  }
  if (landsat[0]) {
    cards.push({
      title: 'Landsat 最新影像',
      sub: `${fmtDate(landsat[0].datetime, false)} · 云量 ${landsat[0].cloud_cover != null ? Number(landsat[0].cloud_cover).toFixed(1) + '%' : '--'}`,
      img: landsat[0].preview,
      tag: '30 m 光学',
      kind: 'landsat',
      payload: landsat[0],
    });
  }
  natural.slice(0, 7).forEach((n) => {
    cards.push({
      title: n.name || n.collection,
      sub: `${fmtDate(n.datetime, false)} · ${TYPE_LABELS[n.type] || n.type || ''}`,
      img: n.preview,
      tag: TYPE_LABELS[n.type] || n.type || '产品',
      kind: 'natural',
      payload: n,
    });
  });

  if (!cards.length) { el.innerHTML = '<div class="empty">暂无自然数据，点击刷新</div>'; return; }
  el.innerHTML = cards.map((c, i) => `
    <div class="nat-card" data-i="${i}">
      ${c.img ? `<img src="${esc(c.img)}?t=${Date.now()}" loading="lazy" onerror="this.style.display='none'">` : '<div class="ph">◈</div>'}
      <div>
        <h4>${esc(c.title)}</h4>
        <p>${esc(c.sub)}</p>
        <div class="tags"><span class="tag">${esc(c.tag)}</span></div>
      </div>
    </div>`).join('');
  el.querySelectorAll('.nat-card').forEach((card) => {
    card.onclick = () => {
      const c = cards[Number(card.dataset.i)];
      if (c.kind === 's2' || c.kind === 'landsat') {
        if (c.payload.tile_url) {
          state.map.addLayer(L.tileLayer(c.payload.tile_url, { opacity: 0.9, maxZoom: 18 }).addTo(state.overlays.s2));
          if (c.payload.bbox?.length >= 4) {
            const [x0, y0, x1, y1] = c.payload.bbox;
            state.map.fitBounds([[y0, x0], [y1, x1]], { padding: [20, 20] });
          }
          state.layersOn.s2 = true; $('chk-s2').checked = true;
          if (!state.map.hasLayer(state.overlays.s2)) state.overlays.s2.addTo(state.map);
          toast(`已叠加 ${c.title}`);
        } else {
          toast('该场景暂无可用切片', 'warn');
        }
      } else {
        const sel = $('natural-select');
        const val = `${c.payload.collection}::${c.payload.id}`;
        if (![...sel.options].some((o) => o.value === val)) {
          const o = document.createElement('option'); o.value = val; o.textContent = c.title; sel.appendChild(o);
        }
        sel.value = val;
        showNaturalOverlay(val);
      }
    };
  });

  // 自然数据下拉
  const sel = $('natural-select');
  const keep = sel.value;
  sel.innerHTML = '<option value="">不叠加</option>';
  natural.forEach((n) => {
    const o = document.createElement('option');
    o.value = `${n.collection}::${n.id}`;
    o.textContent = `${n.name}（${fmtDate(n.datetime, false)}）`;
    sel.appendChild(o);
  });
  if (keep) sel.value = keep;
}

function showNaturalOverlay(val) {
  clearGroup('natural');
  if (!val) { state.map.removeLayer(state.overlays.natural); return; }
  const [collection, id] = val.split('::');
  const n = (state.scenes?.natural || []).find((x) => x.collection === collection && x.id === id);
  if (!n) { toast('未找到该自然数据产品', 'warn'); return; }
  if (n.tile_url) {
    L.tileLayer(n.tile_url, { opacity: 0.78, maxZoom: 18, attribution: 'Microsoft Planetary Computer' })
      .addTo(state.overlays.natural);
  } else if (n.preview && n.bbox?.length >= 4) {
    const [x0, y0, x1, y1] = n.bbox;
    L.imageOverlay(n.preview, [[y0, x0], [y1, x1]], { opacity: 0.72 }).addTo(state.overlays.natural);
  } else {
    toast('该产品暂无预览/tile 服务', 'warn'); return;
  }
  state.overlays.natural.addTo(state.map);
  updateLegendNote();
  toast(`已叠加自然数据：${n.name}`);
}

function renderWeather() {
  const el = $('weather-panel');
  const w = state.weather || {};
  const pts = w.points || [];
  $('weather-time').textContent = w.fetched_at ? relTime(w.fetched_at) : '--';
  if (!pts.length) { el.innerHTML = '<div class="empty">暂无气象数据，点击刷新</div>'; return; }
  const rows = pts.map((p) => {
    const c = p.current || {};
    const temp = c.temperature_2m;
    const hum = c.relative_humidity_2m;
    const wind = c.wind_speed_10m;
    return `<div class="weather-row">
      <span class="w-name">${esc(p.name)}</span>
      <b class="w-val ${temp >= 25 ? 'hot' : ''}">${temp != null ? Number(temp).toFixed(0) + '°' : '--'}</b>
      <b class="w-val">${hum != null ? Number(hum).toFixed(0) + '%' : '--'}</b>
      <b class="w-val">${wind != null ? Number(wind).toFixed(1) : '--'}</b>
    </div>`;
  }).join('');
  const first = pts[0]?.current || {};
  const foot = `降水 ${first.precipitation != null ? Number(first.precipitation).toFixed(1) : '--'} mm · 土壤湿度 ${first.soil_moisture_0_to_10cm != null ? Number(first.soil_moisture_0_to_10cm).toFixed(2) : '--'} m³/m³（${esc(pts[0].name)}）`;
  el.innerHTML = `<div class="weather-row head"><span>点位</span><span>温度</span><span>湿度</span><span>风速</span></div>${rows}
    <p class="hint" style="margin-top:4px">${foot}<br>来源：Open-Meteo · ${esc(w.fetched_at ? fmtDate(w.fetched_at) : '--')}</p>`;
}

function renderQuality() {
  const n = state.ndvi || {};
  const nd = $('ndvi-meta');
  if (nd) {
    const dates = (n.composite_dates || []).map((d) => fmtDate(d, false)).join(' / ') || '--';
    const fallback = n.fallback_count != null ? `${n.fallback_count} 个回退` : '--';
    nd.innerHTML = `<span>NDVI</span><b>${esc(n.source || '未接入')} · ${n.valid_count ?? '--'}/${n.grid_count ?? '--'} 格网 · ${esc(dates)} · ${fallback}</b>`;
  }
  const sm = state.samples?.summary || {};
  const el = $('sample-meta');
  if (el) {
    const c = sm.counts || {};
    const hs = sm.historical_screening || {};
    el.innerHTML = `<span>核查样本</span><b>历史 ${c.incidents ?? '--'} + 背景 ${c.background_controls ?? '--'} · 已核实 ${sm.verified_count ?? 0} · Top4 回溯覆盖 ${hs.recall_at_top4 != null ? (hs.recall_at_top4 * 100).toFixed(1) + '%' : '--'}</b>`;
  }
}

function renderModel() {
  const el = $('model-panel');
  const m = state.summary?.model || {};
  const weights = m.priority_weights || m.weights || {};
  const names = {
    severity: '类别严重度', count: '异常数量', area: '累计面积', recurrence: '复发性',
    terrain: '地形', access: '可达性', environment: '当前环境', confidence: '数据置信度',
  };
  const rows = Object.entries(weights).map(([k, v]) => `
    <div class="mw-row"><span>${names[k] || k}</span><div class="bar"><i style="width:${Math.max(2, Number(v) * 100)}%"></i></div><b>${(Number(v) * 100).toFixed(1)}%</b></div>
  `).join('');
  const meta = m.meta || {};
  const cr = meta.consistency_ratio != null ? `一致性比例 CR=${Number(meta.consistency_ratio).toFixed(4)}` : '';
  el.innerHTML = `
    <div class="model-method">${esc(m.method || '加权模型')} ${cr ? '· ' + cr : ''} · 引擎 ${esc(meta.engine || 'Python 兜底')}</div>
    <div class="model-note">AHP 权重用于历史异常核查排序；“当前环境”另行计算当前危险指数，不参与这里的核查优先级。</div>
    <div class="model-weights">${rows || '<div class="empty">暂无权重</div>'}</div>
    <img class="model-chart" src="/assets/matlab_weights.png?t=${Date.now()}" alt="MATLAB AHP 权重图（原始图含环境权重）" onerror="this.style.display='none'">
  `;
}

/* ------------------------------------------------------------------ */
/* 刷新 / 模型重算 / ArcGIS                                            */
/* ------------------------------------------------------------------ */
const REFRESH_STAGES = [
  { test: /\[1\/4\]/, progress: 10 },
  { test: /\[2\/4\]/, progress: 22 },
  { test: /\[3\/4\]/, progress: 34 },
  { test: /\[4\/4\]/, progress: 46 },
];

async function doRefresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  const btn = $('refresh-btn');
  btn.disabled = true;
  btn.classList.add('loading');
  $('refresh-overlay').classList.remove('hidden');
  $('refresh-bar').style.width = '2%';
  $('refresh-step').textContent = '正在启动刷新任务…';
  $('status-dot').className = 'status-dot busy';
  $('data-status').textContent = '刷新中';

  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const startedAt = Date.now();
  let started = null;
  let finalStatus = null;

  try {
    // 只发一个短请求启动后台线程，避免公网隧道长连接卡住浏览器
    const startRes = await fetch(`${API}/refresh/start`, { method: 'POST', cache: 'no-store' });
    if (startRes.status === 409) {
      const j = await startRes.json().catch(() => ({}));
      toast(j.detail || '已有刷新任务在进行', 'warn');
      return;
    }
    if (!startRes.ok) throw new Error(`启动刷新失败：HTTP ${startRes.status}`);
    started = await startRes.json();
    const runId = started.started_at || '';

    for (;;) {
      await pause(900);
      const stRes = await fetch(`${API}/refresh/status`, { cache: 'no-store' });
      if (!stRes.ok) throw new Error(`读取刷新状态失败：HTTP ${stRes.status}`);
      const st = await stRes.json();
      const elapsed = Number(st.elapsed_sec) || (Date.now() - startedAt) / 1000;

      if (st.status === 'running') {
        let pct = Number(st.progress);
        if (!Number.isFinite(pct) || pct <= 0) {
          pct = Math.min(88, 4 + elapsed * 1.6);
          for (const stage of REFRESH_STAGES) {
            if (stage.test.test(st.message || '')) pct = Math.max(pct, stage.progress);
          }
        }
        $('refresh-bar').style.width = `${Math.max(2, Math.min(97, pct))}%`;
        const msg = String(st.message || '正在抓取最新数据…')
          .replace(/^=+\s*/, '')
          .replace(/\s*=+$/, '');
        $('refresh-step').textContent = `${msg}（已用 ${elapsed.toFixed(0)}s）`;
      } else if (st.status === 'done') {
        // 只接受本次启动对应的结果，避免读到上一次的旧状态
        if (runId && st.started_at && st.started_at !== runId) continue;
        finalStatus = st;
        break;
      } else if (st.status === 'error') {
        if (runId && st.started_at && st.started_at !== runId) continue;
        throw new Error(st.message || st.error || '刷新失败');
      } else if (st.status === 'idle') {
        $('refresh-step').textContent = `后台任务启动中…（已用 ${elapsed.toFixed(0)}s）`;
      }

      if (elapsed > 300) throw new Error('刷新超过 5 分钟仍未完成，请检查网络后重试');
    }

    $('refresh-bar').style.width = '100%';
    $('refresh-step').textContent = `刷新完成，正在更新地图…（用时 ${finalStatus.elapsed_sec ?? Math.round((Date.now() - startedAt) / 1000)}s）`;
    await loadAll();
    refreshOverlays();
    renderHeader();

    const changes = finalStatus.summary?.changes || state.summary?.changes || [];
    const changeText = changes.length
      ? `，${changes.length} 个高发点等级变化：` + changes.slice(0, 3).map((c) => `${c.name} ${c.old}→${c.new}`).join('；')
      : '，核查优先级未发生跨级变化';
    toast(`刷新完成（${finalStatus.elapsed_sec ?? Math.round((Date.now() - startedAt) / 1000)}s）${changeText}`,
      finalStatus.warnings?.length ? 'warn' : '');
    if (finalStatus.warnings?.length) {
      setTimeout(() => toast('部分数据源降级：' + finalStatus.warnings.slice(0, 2).join('；'), 'warn'), 4200);
    }
  } catch (e) {
    $('refresh-step').textContent = '刷新失败：' + e.message;
    toast('刷新失败：' + e.message, 'error');
  } finally {
    state.refreshing = false;
    btn.disabled = false;
    btn.classList.remove('loading');
    setTimeout(() => $('refresh-overlay').classList.add('hidden'), 650);
    $('status-dot').className = 'status-dot';
    $('data-status').textContent = '数据就绪';
  }
}

async function recomputeModel() {
  const btn = $('btn-model');
  btn.disabled = true;
  $('model-output').textContent = 'MATLAB 正在计算 AHP 权重与核查优先级…';
  try {
    const r = await fetch(`${API}/model/recompute`, { method: 'POST' });
    const j = await r.json();
    if (j.ok) {
      $('model-output').innerHTML = `MATLAB 重算完成：${esc(j.message || '权重已更新')}`;
      await loadAll();
      refreshOverlays();
      toast('MATLAB 风险模型已重算');
    } else {
      $('model-output').textContent = '重算失败：' + (j.error || '未知错误');
      toast('MATLAB 重算失败：' + (j.error || ''), 'error');
    }
  } catch (e) {
    $('model-output').textContent = '重算失败：' + e.message;
    toast('MATLAB 重算失败：' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function exportArcgis() {
  const btn = $('btn-arcgis');
  btn.disabled = true;
  $('model-output').textContent = 'ArcGIS Pro 正在导出专题图…（首次可能 30~90 秒）';
  try {
    const r = await fetch(`${API}/export/arcgis`, { method: 'POST' });
    const j = await r.json();
    if (j.ok) {
      $('model-output').innerHTML = `ArcGIS 专题图已生成：<a href="${esc(j.url)}" target="_blank">${esc(j.url)}</a>`;
      toast('ArcGIS 专题图已生成');
    } else {
      $('model-output').textContent = '导出失败：' + (j.error || '未知错误');
      toast('ArcGIS 导出失败：' + (j.error || ''), 'error');
    }
  } catch (e) {
    $('model-output').textContent = '导出失败：' + e.message;
    toast('ArcGIS 导出失败：' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------------------------------ */
/* 图例/事件绑定                                                       */
/* ------------------------------------------------------------------ */
function updateLegendNote() {
  const sel = selectedHazardObj();
  let note = '核查优先级 · 历史线索排序，不是灾害预报';
  if (sel?.status === 'risk') note = `${sel.name} · 当前危险指数（自然因子 × 暴露）`;
  else if (sel) note = `${sel.name} · ${HAZARD_STATUS_LABELS[sel.status]} · 核查优先级`;
  $('legend-note').textContent = note;
}

function bindUI() {
  $('refresh-btn').onclick = doRefresh;
  $('hazard-filter').onchange = onHazardChange;
  $('natural-select').onchange = (e) => showNaturalOverlay(e.target.value);
  $('btn-model').onclick = recomputeModel;
  $('btn-arcgis').onclick = exportArcgis;

  const layerMap = {
    'chk-arcgis-base': 'arcgisBase', 'chk-china': 'china', 'chk-s2': 's2', 'chk-incidents': 'incidents', 'chk-hotspots': 'hotspots',
    'chk-grid': 'grid', 'chk-natural': 'natural', 'chk-samples': 'samples', 'chk-weather': 'weather', 'chk-heat': 'heat', 'chk-police': 'police',
    'chk-routes': 'routes', 'chk-places': 'places', 'chk-aoi': 'aoi', 'chk-zq': 'zq',
  };
  Object.entries(layerMap).forEach(([id, key]) => {
    $(id).onchange = (e) => {
      state.layersOn[key] = e.target.checked;
      applyLayerState();
    };
  });
  const routesToggle = $('chk-routes');
  if (routesToggle) {
    routesToggle.addEventListener('change', (e) => {
      if (!e.target.checked) return;
      ensureRoads().then(() => {
        if (state.roads && state.layersOn.routes) buildPolice();
      });
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  bindUI();
  init();
  const bootParams = new URLSearchParams(location.search);
  if (bootParams.get('mode') === '3d') {
    window.addEventListener('app-ready', () => setTimeout(() => {
      setMapMode('3d');
      if (bootParams.get('focus') === 'zq') {
        setTimeout(() => {
          try { state.terrain3d?.jumpTo({ center: [117.54, 36.71], zoom: 8.8, pitch: 62, bearing: -22 }); } catch (e) {}
        }, 1200);
      }
    }, 250), { once: true });
  }
});

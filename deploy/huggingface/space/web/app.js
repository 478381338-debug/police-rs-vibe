'use strict';

const API = '/api';
const $ = (id) => document.getElementById(id);

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
    arcgisBase: true, s2: false, incidents: true, hotspots: true, grid: false, heat: false,
    police: true, routes: true, places: false, aoi: true,
  },
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
function badge(level) {
  const short = { red: '红色', orange: '橙色', yellow: '黄色', blue: '蓝色' }[level] || labelForLevel(level);
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
  if (state.selectedHazard === 'all') return Number(props.risk || 0);
  return Number(props[`risk_${state.selectedHazard}`] ?? props.risk ?? 0);
}

/* ------------------------------------------------------------------ */
/* 初始化                                                             */
/* ------------------------------------------------------------------ */
async function init() {
  try {
    await loadAll();
    initMap();
    refreshOverlays();
    hideBoot();
    window.dispatchEvent(new Event('app-ready'));
  } catch (e) {
    console.error(e);
    hideBoot();
    toast('初始化失败：' + e.message, 'error');
  }
}

function hideBoot() {
  const b = $('boot-screen');
  if (b) { b.classList.add('hidden'); b.style.display = 'none'; }
}

async function loadAll() {
  const safe = (p, d) => p.catch(() => d);
  const [cfg, summary, hotspots, incidents, grid, police, places, roads, aoi, scenes, weather, tasks, catalog] = await Promise.all([
    fetchJSON(`${API}/config`),
    safe(fetchJSON(`${API}/summary`), {}),
    safe(fetchJSON(`${API}/hotspots`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/incidents`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/risk-grid`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/police`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/places`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/roads`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/aoi`), { type: 'FeatureCollection', features: [] }),
    safe(fetchJSON(`${API}/scenes`), {}),
    safe(fetchJSON(`${API}/weather`), {}),
    safe(fetchJSON(`${API}/tasks`), []),
    safe(fetchJSON(`${API}/catalog`), { hazards: [] }),
  ]);
  state.config = cfg;
  state.summary = summary;
  state.hotspots = hotspots;
  state.incidents = incidents;
  state.riskGrid = grid;
  state.police = police;
  state.places = places;
  state.roads = roads;
  state.aoi = aoi;
  state.scenes = scenes;
  state.weather = weather;
  state.tasks = tasks;
  state.catalog = catalog;

  renderHeader();
  renderKpis();
  renderHazardOptions();
  renderCatalog();
  renderHotspotList();
  renderNaturalList();
  renderWeather();
  renderModel();
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

  const esriImg = L.tileLayer('/api/tile/esri/{z}/{x}/{y}', {
    maxZoom: 18, maxNativeZoom: 18,
    attribution: 'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics',
  });
  const esriTopo = L.tileLayer('/api/tile/esri_topo/{z}/{x}/{y}', {
    maxZoom: 18, maxNativeZoom: 18,
    attribution: 'Tiles &copy; Esri',
  });
  const osm = L.tileLayer('/api/tile/osm/{z}/{x}/{y}', {
    maxZoom: 18, attribution: '&copy; OpenStreetMap contributors',
  });
  state.baseLayers = { esriImg, esriTopo, osm };
  // Esri 瓦片作为底层填充研究区外；研究区内由 ArcGIS 静态影像底图覆盖。
  esriImg.addTo(map);

  state.overlays = {
    arcgisBase: L.layerGroup(),
    aoi: L.layerGroup(),
    incidents: L.layerGroup(),
    hotspots: L.layerGroup(),
    grid: L.layerGroup(),
    heat: L.layerGroup(),
    police: L.layerGroup(),
    routes: L.layerGroup(),
    places: L.layerGroup(),
    s2: L.layerGroup(),
    natural: L.layerGroup(),
    scenario: L.layerGroup(),
  };
  // 默认叠加
  ['arcgisBase', 'incidents', 'hotspots', 'police', 'routes', 'aoi'].forEach((k) => state.overlays[k].addTo(map));
  ['s2', 'grid', 'heat', 'places', 'natural'].forEach((k) => state.overlays[k].remove());

  // 底图切换（双击地图空白处循环？不，用右上角简易按钮）
  const BaseControl = L.Control.extend({
    options: { position: 'topright' },
    onAdd() {
      const div = L.DomUtil.create('div', 'leaflet-bar');
      div.style.cssText = 'background:rgba(10,16,24,.9);border:1px solid #22364a;border-radius:8px;padding:4px;display:flex;flex-direction:column;gap:3px;';
      const mk = (label, fn) => {
        const b = document.createElement('button');
        b.textContent = label;
        b.style.cssText = 'background:transparent;border:0;color:#9fb1c2;font-size:10px;padding:4px 6px;cursor:pointer;border-radius:5px;text-align:left;';
        b.onmouseenter = () => (b.style.background = 'rgba(56,189,248,.1)');
        b.onmouseleave = () => (b.style.background = 'transparent');
        b.onclick = fn;
        div.appendChild(b);
      };
      mk('卫星影像', () => { map.removeLayer(esriImg); map.removeLayer(esriTopo); map.removeLayer(osm); esriImg.addTo(map); });
      mk('地形图', () => { map.removeLayer(esriImg); map.removeLayer(esriTopo); map.removeLayer(osm); esriTopo.addTo(map); });
      mk('街道图', () => { map.removeLayer(esriImg); map.removeLayer(esriTopo); map.removeLayer(osm); osm.addTo(map); });
      mk('复位视野', () => map.setView([center[1], center[0]], zoom, { animate: true }));
      L.DomEvent.disableClickPropagation(div);
      return div;
    },
  });
  map.addControl(new BaseControl());

  // 初始视野：固定到研究区中心/缩放。
  // 不在初始化时 fitBounds：如果容器尚未完成布局，Leaflet 会把缩放推到 18 级。
  map.setView([center[1], center[0]], zoom, { animate: false });
}

function refreshOverlays() {
  buildArcgisBase();
  buildAoi();
  buildIncidents();
  buildHotspots();
  buildGrid();
  buildHeat();
  buildPolice();
  buildPlaces();
  buildS2Layers();
  // natural 叠加在切换时单独处理
  applyLayerState();
  updateLegendNote();
}

function applyLayerState() {
  Object.entries(state.layersOn).forEach(([k, on]) => {
    const g = state.overlays[k];
    if (!g) return;
    if (on) { if (!state.map.hasLayer(g)) g.addTo(state.map); }
    else if (state.map.hasLayer(g)) state.map.removeLayer(g);
  });
}

function clearGroup(name) {
  const g = state.overlays[name];
  if (!g) return;
  g.clearLayers();
}

function buildArcgisBase() {
  clearGroup('arcgisBase');
  L.imageOverlay('/assets/arcgis_basemap.jpg?t=' + Date.now(), [[39.95, 108.9], [41.58, 112.8]], {
    opacity: 1, interactive: false, className: 'arcgis-base',
    attribution: 'ArcGIS Pro · 天地图影像',
  }).addTo(state.overlays.arcgisBase);
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
  $('last-update').textContent = s.updated_at ? `更新 ${relTime(s.updated_at)} · 最新影像 ${latest}` : '等待数据';
  const dot = $('status-dot');
  dot.className = 'status-dot' + (s.updated_at ? '' : ' error');
  $('data-status').textContent = s.updated_at ? '系统在线' : '等待数据';
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
  const top = (s.top_hotspots || [])[0];
  const focus = $('police-focus');
  const focusText = $('police-focus-text');
  if (top && focusText) {
    focusText.textContent = `${top.name} · ${labelForLevel(top.danger_level)} · 风险 ${top.risk_score} 分`;
  } else if (focusText) {
    focusText.textContent = '暂无高发点数据';
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
  sel.innerHTML = '<option value="all">全部（综合风险）</option>';
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
  title.textContent = sel ? `${sel.name} · 高发点 Top 10` : '高发地点 Top 10';
  countEl.textContent = `${feats.length} 处`;
  if (!feats.length) { el.innerHTML = '<li class="empty">该类型暂无高发点</li>'; return; }
  el.innerHTML = feats.map((f, i) => {
    const p = f.properties;
    const lv = p.danger_level;
    return `<li class="hs-item" data-id="${esc(p.id)}">
      <div class="hs-rank">${String(i + 1).padStart(2, '0')}</div>
      <div class="hs-main">
        <div class="hs-name">${esc(p.name)} ${badge(lv)}</div>
        <div class="hs-meta">${esc(p.dominant_class)} · ${p.incident_count} 图斑 · 坡度 ${p.slope_deg}° · 距警务点 ${p.dist_police_km}km</div>
      </div>
      <div class="hs-score"><b style="color:${colorForLevel(lv)}">${Number(p.risk_score).toFixed(0)}</b><span>${esc(p.danger_label || '')}</span></div>
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
  const weights = p.weights || {};
  const factorRows = Object.keys(FACTOR_LABELS)
    .filter((k) => k in factors)
    .map((k) => {
      const v = Number(factors[k] || 0);
      return `<div class="factor-row" title="权重 ${weights[k] ?? '-'}">
        <span>${FACTOR_LABELS[k]}</span>
        <div class="bar"><i style="width:${Math.max(2, v * 100)}%"></i></div>
        <b>${(v * 100).toFixed(0)}</b>
      </div>`;
    }).join('');

  $('detail-body').innerHTML = `
    <div class="detail-title">
      <div><h3>${esc(p.name)} ${badge(lv)}</h3></div>
      <div class="score" style="color:${color}">${Number(p.risk_score).toFixed(1)}</div>
    </div>
    <div class="detail-sub">排名 ${p.rank ?? '-'} · 主导类别 ${esc(p.dominant_class)} · ${p.incident_count} 个图斑 · 累计 ${Number(p.area_ha).toFixed(1)} ha</div>
    <div class="detail-grid">
      <div class="detail-cell"><span>等级</span><b style="color:${color}">${esc(p.danger_label || labelForLevel(lv))}</b></div>
      <div class="detail-cell"><span>复发性</span><b>${p.recurrence} 类/期</b></div>
      <div class="detail-cell"><span>坡度 / 高程</span><b>${p.slope_deg}° / ${Math.round(p.elev_m)} m</b></div>
      <div class="detail-cell"><span>距最近警务点</span><b>${p.dist_police_km} km</b></div>
      <div class="detail-cell"><span>最近警务点</span><b>${esc(p.nearest_police || '--')}</b></div>
      <div class="detail-cell"><span>距道路 / 村庄</span><b>${p.dist_road_km} / ${p.dist_place_km} km</b></div>
    </div>
    <div class="section-label">风险因子（归一化 0-100）</div>
    ${factorRows}
    <div class="section-label">判定依据</div>
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
  const v = gridValue(p);
  const lv = levelForValue(v);
  const color = colorForLevel(lv);
  const hazardName = sel ? sel.name : (p.dominant_hazard_name || p.dominant_hazard);
  const riskRows = Object.keys(p)
    .filter((k) => k.startsWith('risk_') && typeof p[k] === 'number')
    .sort((a, b) => p[b] - p[a])
    .slice(0, 6)
    .map((k) => {
      const id = k.replace('risk_', '');
      const nm = hazardById(id)?.name || id;
      return `<div class="factor-row"><span>${esc(nm)}</span><div class="bar"><i style="width:${Math.max(2, p[k])}%"></i></div><b>${p[k].toFixed(0)}</b></div>`;
    }).join('');

  $('detail-body').innerHTML = `
    <div class="detail-title">
      <div><h3>格网 ${esc(p.cell_id)} · ${esc(hazardName)} ${badge(lv)}</h3></div>
      <div class="score" style="color:${color}">${v.toFixed(1)}</div>
    </div>
    <div class="detail-sub">扩展风险筛查 · 主导风险 ${esc(p.dominant_hazard_name || '')} · 综合风险 ${Number(p.risk || 0).toFixed(1)}</div>
    <div class="detail-grid">
      <div class="detail-cell"><span>当前筛选</span><b>${esc(hazardName)}</b></div>
      <div class="detail-cell"><span>综合风险</span><b>${Number(p.risk || 0).toFixed(1)}</b></div>
      <div class="detail-cell"><span>坡度 / 高程</span><b>${p.slope_deg}° / ${Math.round(p.elev_m)} m</b></div>
      <div class="detail-cell"><span>火险 / 湿润</span><b>${Number(p.fire_wx || 0).toFixed(2)} / ${Number(p.wet || 0).toFixed(2)}</b></div>
    </div>
    <div class="section-label">主要风险类型（0-100）</div>
    ${riskRows || '<div class="empty">暂无细分风险</div>'}
    <div class="action-box"><b>说明：</b>扩展类型为规则筛查/气象风险，需人工核查，不作为定性结论。</div>
    <div class="detail-actions">
      <button class="mini-btn" id="btn-locate-cell">地图定位</button>
      <button class="mini-btn primary" id="btn-task-cell">生成核查任务</button>
    </div>
  `;
  const c = centerOfGeomFromProps(p);
  $('btn-locate-cell').onclick = () => state.map.flyTo([c[1], c[0]], 11, { duration: 0.7 });
  $('btn-task-cell').onclick = () => createTask({
    id: p.cell_id, name: `格网 ${p.cell_id}（${hazardName}）`,
    danger_label: labelForLevel(lv), centroid: c,
  });
}

function centerOfGeomFromProps(p) {
  // 格网中心：从当前选中的格网 feature 找；找不到就用研究区中心
  const feat = (state.riskGrid?.features || []).find((f) => f.properties.cell_id === p.cell_id);
  if (feat) return centerOfGeom(feat.geometry);
  return [111.05, 40.9];
}

async function createTask(h) {
  try {
    const task = await fetchJSON(`${API}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hotspot_id: h.id,
        hotspot_name: h.name,
        priority: h.danger_label || h.priority || '按危险等级',
        note: h.recommended_action || '由生态警务平台生成（演示任务）',
      }),
    });
    state.tasks.push(task);
    renderTasks();
    toast(`已生成核查任务 ${task.id}：${task.hotspot_name}`);
  } catch (e) {
    toast('生成任务失败：' + e.message, 'error');
  }
}

function renderTasks() {
  const el = $('task-list');
  $('task-count').textContent = String(state.tasks.length);
  if (!state.tasks.length) { el.innerHTML = '<li class="empty">暂无任务</li>'; return; }
  el.innerHTML = state.tasks.slice().reverse().map((t) => `
    <li class="task-item">
      <h4>${esc(t.hotspot_name || t.hotspot_id)} <span class="task-status">${esc(t.status)}</span></h4>
      <p>${esc(t.id)} · ${esc(t.assignee || '待指派')} · ${esc(t.priority || '')}<br>${fmtDate(t.created_at)}</p>
    </li>`).join('');
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

function renderModel() {
  const el = $('model-panel');
  const m = state.summary?.model || {};
  const weights = m.weights || {};
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
    <div class="model-weights">${rows || '<div class="empty">暂无权重</div>'}</div>
    <img class="model-chart" src="/assets/matlab_weights.png?t=${Date.now()}" alt="MATLAB 权重图" onerror="this.style.display='none'">
    <img class="model-chart" src="/assets/matlab_levels.png?t=${Date.now()}" alt="危险等级分布" onerror="this.style.display='none'">
  `;
}

/* ------------------------------------------------------------------ */
/* 刷新 / 模型重算 / ArcGIS                                            */
/* ------------------------------------------------------------------ */
const REFRESH_STEPS = [
  '连接 Microsoft Planetary Computer…',
  '抓取最新 Sentinel-2 / Landsat 场景…',
  '读取 MODIS NDVI / 地表温度 / 土地覆盖 / DEM…',
  '拉取 Open-Meteo 实时气象与高程…',
  '重算高发点危险等级与风险格网…',
  '更新地图与警务态势…',
];

async function doRefresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  const btn = $('refresh-btn');
  btn.disabled = true;
  btn.classList.add('loading');
  $('refresh-overlay').classList.remove('hidden');
  $('refresh-bar').style.width = '6%';
  $('status-dot').className = 'status-dot busy';
  $('data-status').textContent = '刷新中';

  let step = 0;
  const stepTimer = setInterval(() => {
    step = (step + 1) % REFRESH_STEPS.length;
    $('refresh-step').textContent = REFRESH_STEPS[step];
    const pct = Math.min(90, 8 + step * 14);
    $('refresh-bar').style.width = pct + '%';
  }, 2600);

  try {
    const r = await fetch(`${API}/refresh`, { method: 'POST' });
    if (r.status === 409) {
      const j = await r.json();
      toast(j.detail || '已有刷新任务在进行', 'warn');
      return;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    $('refresh-bar').style.width = '100%';
    $('refresh-step').textContent = '刷新完成，正在更新地图…';
    await loadAll();
    refreshOverlays();
    renderHeader();
    const changes = data.summary?.changes || [];
    const changeText = changes.length
      ? `，${changes.length} 个高发点等级变化：` + changes.slice(0, 3).map((c) => `${c.name} ${c.old}→${c.new}`).join('；')
      : '，危险等级未发生跨级变化';
    toast(`刷新完成（${data.elapsed_sec}s）${changeText}`, data.warnings?.length ? 'warn' : '');
    if (data.warnings?.length) {
      setTimeout(() => toast('部分数据源降级：' + data.warnings.slice(0, 2).join('；'), 'warn'), 4200);
    }
  } catch (e) {
    toast('刷新失败：' + e.message, 'error');
  } finally {
    clearInterval(stepTimer);
    state.refreshing = false;
    btn.disabled = false;
    btn.classList.remove('loading');
    setTimeout(() => $('refresh-overlay').classList.add('hidden'), 450);
    $('status-dot').className = 'status-dot';
    $('data-status').textContent = '数据就绪';
    $('refresh-bar').style.width = '8%';
  }
}

async function recomputeModel() {
  const btn = $('btn-model');
  btn.disabled = true;
  $('model-output').textContent = 'MATLAB 正在计算 AHP 权重与危险等级…';
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
  const note = sel ? `${sel.name}（${HAZARD_STATUS_LABELS[sel.status]}）` : '综合风险 · 历史异常 + 高发点';
  $('legend-note').textContent = note;
}

function bindUI() {
  $('refresh-btn').onclick = doRefresh;
  $('hazard-filter').onchange = onHazardChange;
  $('natural-select').onchange = (e) => showNaturalOverlay(e.target.value);
  $('btn-model').onclick = recomputeModel;
  $('btn-arcgis').onclick = exportArcgis;

  const layerMap = {
    'chk-arcgis-base': 'arcgisBase', 'chk-s2': 's2', 'chk-incidents': 'incidents', 'chk-hotspots': 'hotspots',
    'chk-grid': 'grid', 'chk-heat': 'heat', 'chk-police': 'police',
    'chk-routes': 'routes', 'chk-places': 'places', 'chk-aoi': 'aoi',
  };
  Object.entries(layerMap).forEach(([id, key]) => {
    $(id).onchange = (e) => {
      state.layersOn[key] = e.target.checked;
      applyLayerState();
    };
  });
}

document.addEventListener('DOMContentLoaded', () => {
  bindUI();
  init();
});

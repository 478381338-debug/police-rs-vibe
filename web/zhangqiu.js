/* Zhangqiu public-data screening map. No police incident data is used. */
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { boundary: null, grid: null, pois: null, roads: null, water: null, buildings: null, weather: null, summary: null, lulc: null };
  const layers = {};
  const colors = { 高: '#e03131', 较高: '#f08c00', 中: '#f2c94c', 低: '#74b9ff' };
  const kindColor = { police: '#0f766e', hospital: '#e03131', school: '#d97706', fire: '#dc2626', government: '#7c3aed', station: '#475569', culture: '#0891b2', transport: '#64748b' };
  // OSM 公开点位：山东警察学院（明水校区），章丘区内；坐标为校园设施点，不是警情。
  const POLICE_COLLEGE = { name: '山东警察学院', campus: '明水校区', lon: 117.520686, lat: 36.6522767, source: 'OpenStreetMap' };
  let map = null;
  let map3d = null;
  let mode = '2d';

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n, d = 0) => Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: d });
  const km = (n) => `${Number(n || 0).toFixed(1)} km`;

  async function loadJSON(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(`${url} HTTP ${r.status}`);
    return r.json();
  }

  function levelStyle(feature) {
    const p = feature?.properties || feature || {};
    return { color: '#fff', weight: .5, opacity: .55, fillColor: colors[p.level] || '#74b9ff', fillOpacity: p.level === '中' ? .38 : .62 };
  }

  function popupHtml(p) {
    return `<div class="zq-popup"><b>${esc(p.cell_id)}</b><br>
      关注指数 <b>${p.risk}</b> · ${esc(p.level)}<br>
      主导因子：${esc(p.dominant)}<br>
      人口 ${fmt(p.population)} · 高程 ${p.elev_m} m · 坡度 ${p.slope_deg}°</div>`;
  }

  function policeCollegePopup() {
    return `<div class="zq-popup"><b>${POLICE_COLLEGE.name}（${POLICE_COLLEGE.campus}）</b><br>
      公安院校 · 公开设施点<br>
      坐标：${POLICE_COLLEGE.lat.toFixed(6)}°N, ${POLICE_COLLEGE.lon.toFixed(6)}°E<br>
      <span class="zq-popup-note">来源：OpenStreetMap，ODbL；这是校园设施点位，不是警情或警力部署。</span></div>`;
  }

  function policeCollegeIcon() {
    return `<div class="zq-college-pin"><span class="zq-college-badge">警</span><span class="zq-college-name">${POLICE_COLLEGE.name}<small>${POLICE_COLLEGE.campus}</small></span></div>`;
  }

  function showDetail(p) {
    const d = $('zq-detail');
    if (!d) return;
    d.innerHTML = `
      <h3>${esc(p.cell_id)} <span style="color:${colors[p.level] || '#64748b'};font-size:11px">${esc(p.level)}</span></h3>
      <div class="zq-sub">${p.lat.toFixed(4)}°N, ${p.lon.toFixed(4)}°E · 排名 ${p.rank}</div>
      <div class="zq-big">${p.risk}<small>巡查关注指数</small></div>
      <div class="zq-rows">
        <div><b>主导因子</b><span>${esc(p.dominant)}</span></div>
        <div><b>人口暴露</b><span>${fmt(p.population)} 人</span></div>
        <div><b>高程 / 坡度</b><span>${p.elev_m} m / ${p.slope_deg}°</span></div>
        <div><b>警务设施距离</b><span>${km(p.dist_police_km)}</span></div>
        <div><b>医疗设施距离</b><span>${km(p.dist_hospital_km)}</span></div>
        <div><b>道路距离</b><span>${km(p.dist_road_km)}</span></div>
        <div><b>水系距离</b><span>${km(p.dist_water_km)}</span></div>
        <div><b>3h / 7d 雨量</b><span>${p.rain3_mm} / ${p.rain7_mm} mm</span></div>
        <div><b>气温 / 湿度</b><span>${p.temp_c}°C / ${p.humidity}%</span></div>
        <div><b>气象采样点</b><span>${esc(p.weather_point)}</span></div>
      </div>
      <p class="zq-note">因子为公开数据估算；“主导因子”是模型归因，不是已发生灾情。</p>`;
  }

  function initMap() {
    map = L.map('zq-map', { zoomControl: true, attributionControl: true }).setView([36.735, 117.48], 10);
    const opentopo = L.tileLayer('/api/tile/opentopo/{z}/{x}/{y}', { maxZoom: 17, attribution: '© OpenStreetMap © OpenTopoMap (CC-BY-SA)' });
    const osm = L.tileLayer('/api/tile/osm/{z}/{x}/{y}', { maxZoom: 18, attribution: '© OpenStreetMap (ODbL)' });
    opentopo.addTo(map);
    L.control.layers({ '地形底图': opentopo, '街道图': osm }, null, { position: 'topright', collapsed: true }).addTo(map);

    layers.boundary = L.geoJSON(state.boundary, {
      style: { color: '#0f766e', weight: 2.4, opacity: .95, fillColor: '#0f766e', fillOpacity: .035 },
    }).addTo(map);

    const collegeIcon = L.divIcon({
      className: 'zq-college-marker',
      html: policeCollegeIcon(),
      iconSize: [188, 44],
      iconAnchor: [22, 42],
    });
    L.marker([POLICE_COLLEGE.lat, POLICE_COLLEGE.lon], { icon: collegeIcon, zIndexOffset: 1200, keyboard: false })
      .addTo(map)
      .bindPopup(policeCollegePopup(), { maxWidth: 330 });

    layers.risk = L.geoJSON(state.grid, {
      style: levelStyle,
      onEachFeature: (f, l) => {
        l.bindPopup(popupHtml(f.properties), { maxWidth: 300 });
        l.on('click', () => showDetail(f.properties));
        l.on('mouseover', () => l.setStyle({ weight: 1.8, color: '#0f172a' }));
        l.on('mouseout', () => l.setStyle({ weight: .5, color: '#fff' }));
      },
    }).addTo(map);

    const police = { type: 'FeatureCollection', features: [] };
    const facility = { type: 'FeatureCollection', features: [] };
    state.pois.features.forEach((f) => (f.properties.kind === 'police' ? police : facility).features.push(f));
    layers.police = L.geoJSON(police, {
      pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 6, color: '#fff', weight: 1.5, fillColor: kindColor.police, fillOpacity: 1 }),
      onEachFeature: (f, l) => l.bindPopup(`<b>${esc(f.properties.name)}</b><br>公安设施 · OpenStreetMap`),
    }).addTo(map);
    layers.facility = L.geoJSON(facility, {
      pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 4.5, color: '#fff', weight: 1, fillColor: kindColor[f.properties.kind] || '#64748b', fillOpacity: .92 }),
      onEachFeature: (f, l) => l.bindPopup(`<b>${esc(f.properties.name)}</b><br>${esc(f.properties.kind)} · OpenStreetMap`),
    }).addTo(map);

    layers.roads = L.geoJSON(state.roads, {
      style: (f) => ({ color: f.properties.highway === 'motorway' || f.properties.highway === 'trunk' ? '#f59e0b' : '#64748b', weight: f.properties.highway === 'tertiary' ? .6 : 1.1, opacity: .55 }),
    }).addTo(map);
    layers.water = L.geoJSON(state.water, { style: { color: '#0284c7', weight: 1.2, opacity: .65 } }).addTo(map);
    layers.buildings = L.geoJSON(state.buildings, { style: { color: '#0f766e', weight: .3, fillColor: '#5eead4', fillOpacity: .25 } });

    const b = layers.boundary.getBounds();
    map.fitBounds(b, { padding: [14, 14] });
    state.bounds = b;

    document.querySelectorAll('[data-layer]').forEach((el) => {
      el.addEventListener('change', () => {
        const key = el.dataset.layer;
        const layer = layers[key === 'facility' ? 'facility' : key];
        if (!layer) return;
        if (el.checked) layer.addTo(map); else map.removeLayer(layer);
      });
    });
    $('zq-fit').onclick = () => map.fitBounds(b, { padding: [14, 14] });
    $('zq-fit-top').onclick = () => {
      const p = state.grid.features[0]?.properties;
      if (p) { map.flyTo([p.lat, p.lon], 13, { duration: .7 }); showDetail(p); }
    };
    document.querySelectorAll('.zq-mode button').forEach((btn) => btn.addEventListener('click', () => setMode(btn.dataset.mode)));
  }

  function build3d() {
    if (map3d) return;
    const el = $('zq3d-map');
    try {
      map3d = new maplibregl.Map({
        container: el,
        center: [117.48, 36.735], zoom: 9.5, pitch: 50, bearing: -18,
        style: {
          version: 8,
          sources: {},
          layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#dbeafe' } }],
        },
      });
      map3d.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');

      const collegeEl = document.createElement('div');
      collegeEl.className = 'zq-college-3d';
      collegeEl.innerHTML = policeCollegeIcon();
      collegeEl.addEventListener('click', (e) => {
        e.stopPropagation();
        new maplibregl.Popup({ closeButton: true, maxWidth: '330px' })
          .setLngLat([POLICE_COLLEGE.lon, POLICE_COLLEGE.lat])
          .setHTML(policeCollegePopup())
          .addTo(map3d);
      });
      new maplibregl.Marker({ element: collegeEl, anchor: 'bottom' })
        .setLngLat([POLICE_COLLEGE.lon, POLICE_COLLEGE.lat])
        .addTo(map3d);
      map3d.on('load', () => {
        map3d.addSource('terrain', { type: 'raster-dem', tiles: ['/api/terrain/{z}/{x}/{y}.png'], encoding: 'terrarium', tileSize: 256, maxzoom: 12 });
        map3d.addSource('relief', { type: 'raster', tiles: ['/api/terrain-color/{z}/{x}/{y}.png'], tileSize: 256, maxzoom: 12 });
        map3d.addLayer({ id: 'relief', type: 'raster', source: 'relief', paint: { 'raster-opacity': .84 } });
        map3d.addLayer({ id: 'hillshade', type: 'hillshade', source: 'terrain', paint: { 'hillshade-exaggeration': .45 } });
        map3d.addSource('zq-boundary', { type: 'geojson', data: state.boundary });
        map3d.addSource('zq-grid', { type: 'geojson', data: state.grid });
        map3d.addSource('zq-pois', { type: 'geojson', data: state.pois });
        map3d.addSource('zq-roads', { type: 'geojson', data: state.roads });
        map3d.addSource('zq-water', { type: 'geojson', data: state.water });
        map3d.addLayer({
          id: 'zq-grid-3d', type: 'fill-extrusion', source: 'zq-grid',
          paint: {
            'fill-extrusion-color': ['match', ['get', 'level'], '高', '#e03131', '较高', '#f08c00', '中', '#f2c94c', '#74b9ff'],
            'fill-extrusion-height': ['*', ['max', 0, ['-', ['get', 'risk'], 35]], 10],
            'fill-extrusion-opacity': .82,
          },
        });
        map3d.addLayer({ id: 'zq-boundary-line', type: 'line', source: 'zq-boundary', paint: { 'line-color': '#f5b301', 'line-width': 2.2 } });
        map3d.addLayer({ id: 'zq-roads-line', type: 'line', source: 'zq-roads', paint: { 'line-color': '#f59e0b', 'line-width': .8, 'line-opacity': .55 } });
        map3d.addLayer({ id: 'zq-water-line', type: 'line', source: 'zq-water', paint: { 'line-color': '#0284c7', 'line-width': 1.3, 'line-opacity': .7 } });
        map3d.addLayer({
          id: 'zq-pois-circle', type: 'circle', source: 'zq-pois',
          paint: { 'circle-radius': 5, 'circle-color': ['match', ['get', 'kind'], 'police', '#0f766e', 'hospital', '#e03131', 'school', '#d97706', '#7c3aed'], 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 },
        });
        map3d.on('click', 'zq-grid-3d', (e) => {
          const p = e.features?.[0]?.properties;
          if (p) new maplibregl.Popup({ closeButton: true }).setLngLat(e.lngLat).setHTML(popupHtml(p)).addTo(map3d);
        });
        map3d.setTerrain({ source: 'terrain', exaggeration: 1.35 });
      });
    } catch (err) {
      $('zq3d-error').classList.remove('hidden');
      $('zq3d-error').textContent = `3D 初始化失败：${err.message}`;
    }
  }

  function setMode(next) {
    mode = next;
    document.querySelectorAll('.zq-mode button').forEach((b) => b.classList.toggle('active', b.dataset.mode === next));
    if (next === '3d') {
      $('zq3d').classList.remove('hidden');
      build3d();
      setTimeout(() => map3d?.resize(), 80);
      $('zq-map-hint').textContent = 'MapLibre 3D：拖拽旋转，滚轮缩放';
    } else {
      $('zq3d').classList.add('hidden');
      setTimeout(() => map?.invalidateSize(), 60);
      $('zq-map-hint').textContent = '点击格网查看因子；3D 使用 MapLibre 地形';
    }
  }

  function renderStats() {
    const s = state.summary;
    const levels = s.levels || {};
    $('zq-cells').textContent = fmt(s.cells);
    $('zq-pop').textContent = `${(s.population_sum / 10000).toFixed(1)}万`;
    $('zq-pop-src').textContent = `${(s.population_sum / 10000).toFixed(1)} 万`;
    $('zq-high').textContent = fmt(levels['较高'] || 0);
    $('zq-max').textContent = s.max_risk ?? '--';
    const box = $('zq-levels');
    box.innerHTML = ['高', '较高', '中', '低'].map((k) => `<div class="zq-level"><i style="background:${colors[k]}"></i>${k}<b>${fmt(levels[k] || 0)}</b></div>`).join('');
    const list = $('zq-top-list');
    list.innerHTML = '';
    const top = s.top || [];
    top.forEach((t) => {
      const row = document.createElement('div');
      row.className = 'zq-row';
      row.innerHTML = `<span class="zq-rank">${t.rank}</span><div><b>${esc(t.dominant)} · ${t.level}</b><small>${t.lon.toFixed(3)}, ${t.lat.toFixed(3)} · 人口 ${fmt(t.population)}</small></div><em>${t.risk}</em>`;
      row.onclick = () => {
        if (mode !== '2d') setMode('2d');
        map.flyTo([t.lat, t.lon], 13, { duration: .7 });
        const f = state.grid.features.find((x) => x.properties.rank === t.rank);
        if (f) showDetail(f.properties);
      };
      list.appendChild(row);
    });
    const wx = $('zq-weather');
    wx.innerHTML = (state.weather.points || []).map((p) => {
      const c = p.current || {};
      return `<div class="zq-wx"><b>${esc(p.name)}</b><span>${c.temperature_2m ?? '--'}°C · 湿度 ${c.relative_humidity_2m ?? '--'}%</span><em>${c.wind_speed_10m ?? '--'} km/h</em></div>`;
    }).join('') || '<p class="zq-empty">暂无气象数据</p>';
    if (state.lulc?.crop) $('zq-lulc').src = `${state.lulc.crop}?v=20261107zq1`;
  }

  function tick() {
    const now = new Date();
    $('zq-clock').textContent = now.toLocaleTimeString('zh-CN', { hour12: false });
  }

  async function init() {
    tick();
    setInterval(tick, 1000);
    try {
      const [boundary, grid, pois, roads, water, buildings, weather, summary, lulc] = await Promise.all([
        loadJSON('/data/zhangqiu/boundary.geojson'),
        loadJSON('/data/zhangqiu/risk_grid.geojson'),
        loadJSON('/data/zhangqiu/osm_pois.geojson'),
        loadJSON('/data/zhangqiu/osm_roads.geojson'),
        loadJSON('/data/zhangqiu/osm_waterways.geojson'),
        loadJSON('/data/zhangqiu/osm_buildings.geojson'),
        loadJSON('/data/zhangqiu/weather.json'),
        loadJSON('/data/zhangqiu/summary.json'),
        loadJSON('/data/zhangqiu/lulc.json').catch(() => null),
      ]);
      Object.assign(state, { boundary, grid, pois, roads, water, buildings, weather, summary, lulc });
      initMap();
      renderStats();
      if (new URLSearchParams(location.search).get('mode') === '3d' || location.hash === '#3d') {
        setTimeout(() => setMode('3d'), 400);
      }
    } catch (err) {
      console.error(err);
      $('zq-map-hint').textContent = `数据加载失败：${err.message}`;
      $('zq-detail').innerHTML = `<p class="zq-warning">章丘数据加载失败：${esc(err.message)}。请确认 /data/zhangqiu/ 已生成。</p>`;
    }
  }

  window.addEventListener('resize', () => {
    if (mode === '2d') map?.invalidateSize();
    else map3d?.resize();
  });

  init();
})();

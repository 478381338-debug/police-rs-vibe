'use strict';

/* ------------------------------------------------------------------
   自然条件推演：输入 → 危险系数 → 未来 14 天时空动画
   依赖 app.js 暴露的全局 state / fetchJSON / toast / colorForLevel / esc
------------------------------------------------------------------- */

let scEcharts = null;

const SC_HAZARDS = [
  { id: 'fire_risk', name: '火灾风险' },
  { id: 'landslide', name: '滑坡' },
  { id: 'debris_flow', name: '泥石流' },
  { id: 'flood_risk', name: '洪涝风险' },
  { id: 'drought_risk', name: '干旱风险' },
  { id: 'geohazard_risk', name: '地质灾害隐患' },
  { id: 'mining', name: '盗采 / 矿山风险' },
];

const SC_LEVELS = [
  { key: 'red', min: 75, label: '高风险' },
  { key: 'orange', min: 60, label: '较高风险' },
  { key: 'yellow', min: 45, label: '中等风险' },
  { key: 'blue', min: 0, label: '低风险' },
];

function scEl(id) { return document.getElementById(id); }
function scNum(id, fallback) {
  const v = parseFloat(scEl(id)?.value);
  return Number.isFinite(v) ? v : fallback;
}
function scLevel(score) {
  return SC_LEVELS.find((x) => score >= x.min) || SC_LEVELS[SC_LEVELS.length - 1];
}
function scColor(score) { return colorForLevel(scLevel(score).key); }
function scStyle(score) {
  const c = scColor(score);
  return { color: c, weight: 0.45, fillColor: c, fillOpacity: 0.44 };
}

function setupScenario() {
  if (window.__scenarioReady) return;
  if (!scEl('scenario-drawer')) return;
  window.__scenarioReady = true;
  bindScenarioUI();
  fillScenarioHazards();
  state.map.on('click', (evt) => {
    if (state.scenarioPicking) handleScenarioPick(evt.latlng);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !scEl('scenario-drawer').classList.contains('hidden')) closeScenario();
  });
  window.addEventListener('resize', () => resizeScenarioChart());
  const q = new URLSearchParams(location.search);
  if (q.has('scenario')) {
    setTimeout(() => {
      openScenario();
      if (q.has('run')) setTimeout(() => runScenarioTimeline(), 1400);
    }, 1200);
  }
}

function bindScenarioUI() {
  const on = (id, fn) => { const el = scEl(id); if (el) el.onclick = fn; };
  on('scenario-btn', openScenario);
  on('scenario-close', closeScenario);
  on('scenario-backdrop', closeScenario);
  on('scenario-pick', startScenarioPick);
  on('sc-assess', assessScenario);
  on('sc-run', runScenarioTimeline);
  on('sc-play', toggleScenarioPlay);

  const slider = scEl('sc-slider');
  if (slider) slider.oninput = () => {
    stopScenarioPlay();
    applyScenarioFrame(Number(slider.value));
  };
  const speed = scEl('sc-speed');
  if (speed) speed.onchange = () => { if (state.scenarioTimer) startScenarioPlay(); };

  document.querySelectorAll('#sc-presets .scenario-preset').forEach((btn) => {
    btn.onclick = () => applyScenarioPreset(btn.dataset.preset, btn);
  });

  ['sc-rain-factor', 'sc-temp-offset', 'sc-wind-factor'].forEach((id) => {
    const el = scEl(id);
    if (el) el.oninput = updateSliderLabels;
  });
  document.querySelectorAll('#scenario-drawer .field input, #scenario-drawer .field select, #scenario-drawer .slider-row input').forEach((el) => {
    const bump = () => { state.scenarioEditSeq = (state.scenarioEditSeq || 0) + 1; };
    el.addEventListener('input', bump);
    el.addEventListener('change', bump);
  });
  const hz = scEl('sc-hazard');
  if (hz) hz.onchange = () => {
    scEl('sc-hazard-title').textContent = SC_HAZARDS.find((x) => x.id === hz.value)?.name || '灾害风险';
    scEl('sc-message').textContent = '灾种已切换，点击“计算当前危险系数”或“开始时空推演”。';
    scEl('sc-result-tag').textContent = 'READY';
  };
}

function fillScenarioHazards() {
  const sel = scEl('sc-hazard');
  if (!sel || sel.options.length) return;
  sel.innerHTML = SC_HAZARDS.map((h) => `<option value="${h.id}">${h.name}</option>`).join('');
  sel.value = 'fire_risk';
  scEl('sc-hazard-title').textContent = '火灾风险';
}

function openScenario() {
  const drawer = scEl('scenario-drawer');
  const backdrop = scEl('scenario-backdrop');
  drawer.classList.remove('hidden');
  backdrop.classList.remove('hidden');
  requestAnimationFrame(() => drawer.classList.add('open'));
  fillScenarioHazards();
  updateSliderLabels();
  setTimeout(() => resizeScenarioChart(), 350);
  const c = state.config?.aoi?.center || [111.05, 40.9];
  const p = state.scenarioPoint || c;
  state.scenarioPoint = p;
  updateScenarioLocation(p[0], p[1]);
  setScenarioMarker(p[0], p[1]);
  if (!state.scenarioFormLoaded) {
    loadScenarioDefaults(p[0], p[1]);
  }
}

function closeScenario() {
  stopScenarioPlay();
  const drawer = scEl('scenario-drawer');
  if (!drawer) return;
  if (state.scenarioMarker && state.map) {
    state.map.removeLayer(state.scenarioMarker);
    state.scenarioMarker = null;
  }
  if (state.overlays?.scenario && state.map) {
    state.map.removeLayer(state.overlays.scenario);
    state.overlays.scenario.clearLayers();
  }
  state.scenarioCellLayers = {};
  state.scenarioGeo = null;
  state.scenario = null;
  if (scEcharts) { scEcharts.dispose(); scEcharts = null; }
  state.scenarioPicking = false;
  scEl('map').style.cursor = '';
  scEl('scenario-map-badge').classList.add('hidden');
  drawer.classList.remove('open');
  scEl('scenario-backdrop').classList.add('hidden');
  setTimeout(() => {
    if (!drawer.classList.contains('open')) drawer.classList.add('hidden');
  }, 380);
}

function updateScenarioLocation(lon, lat) {
  const el = scEl('scenario-location');
  if (el) el.textContent = `${Number(lon).toFixed(4)}, ${Number(lat).toFixed(4)}`;
}

function loadScenarioDefaults(lon, lat) {
  state.scenarioPoint = [Number(lon), Number(lat)];
  updateScenarioLocation(lon, lat);
  setScenarioMarker(lon, lat);
  const seq = state.scenarioEditSeq || 0;
  fetchJSON(`${API}/scenario/defaults?lon=${encodeURIComponent(lon)}&lat=${encodeURIComponent(lat)}`)
    .then((d) => {
      if (d?.ok && d.inputs) {
        // 用户已经开始编辑时，不用异步返回的默认值覆盖
        if ((state.scenarioEditSeq || 0) === seq) fillScenarioForm(d.inputs);
        state.scenarioFormLoaded = true;
        setScenarioMarker(lon, lat);
      }
    })
    .catch((e) => toast('推演默认值获取失败，可手动输入：' + e.message, 'warn'));
}

function fillScenarioForm(v) {
  const map = {
    'sc-temp': 'temperature', 'sc-humidity': 'humidity', 'sc-wind': 'wind',
    'sc-precip': 'precipitation', 'sc-soil': 'soil_moisture',
    'sc-rain3': 'rain_3d', 'sc-rain7': 'rain_7d',
    'sc-slope': 'slope_deg', 'sc-elev': 'elev_m', 'sc-ndvi': 'ndvi',
    'sc-road': 'dist_road_km', 'sc-hist': 'hist_count',
    'sc-humidity-delta': 'humidity_delta', 'sc-soil-delta': 'soil_delta',
    'sc-ndvi-delta': 'ndvi_delta', 'sc-precip-add': 'precip_add',
  };
  Object.entries(map).forEach(([id, key]) => {
    const el = scEl(id);
    if (el && v[key] != null) el.value = String(v[key]);
  });
  const hz = scEl('sc-hazard');
  if (hz && v.hazard) hz.value = v.hazard;
  const days = scEl('sc-days');
  if (days && v.days) days.value = String(v.days);
  const rf = scEl('sc-rain-factor'); if (rf && v.rain_factor != null) rf.value = String(v.rain_factor);
  const to = scEl('sc-temp-offset'); if (to && v.temp_offset != null) to.value = String(v.temp_offset);
  const wf = scEl('sc-wind-factor'); if (wf && v.wind_factor != null) wf.value = String(v.wind_factor);
  if (v.lon != null && v.lat != null) {
    state.scenarioPoint = [Number(v.lon), Number(v.lat)];
    updateScenarioLocation(v.lon, v.lat);
  }
  updateSliderLabels();
  if (hz) scEl('sc-hazard-title').textContent = SC_HAZARDS.find((x) => x.id === hz.value)?.name || '灾害风险';
}

function readScenarioForm() {
  return {
    hazard: scEl('sc-hazard')?.value || 'fire_risk',
    days: Number(scEl('sc-days')?.value || 14),
    lon: state.scenarioPoint?.[0] ?? (state.config?.aoi?.center?.[0] ?? 111.05),
    lat: state.scenarioPoint?.[1] ?? (state.config?.aoi?.center?.[1] ?? 40.9),
    temperature: scNum('sc-temp', 5),
    humidity: scNum('sc-humidity', 35),
    wind: scNum('sc-wind', 10),
    precipitation: scNum('sc-precip', 0),
    soil_moisture: scNum('sc-soil', 0.15),
    rain_3d: scNum('sc-rain3', 0),
    rain_7d: scNum('sc-rain7', 0),
    slope_deg: scNum('sc-slope', 8),
    elev_m: scNum('sc-elev', 1600),
    ndvi: scNum('sc-ndvi', 0.45),
    dist_road_km: scNum('sc-road', 3),
    hist_count: scNum('sc-hist', 1),
    rain_factor: scNum('sc-rain-factor', 1),
    temp_offset: scNum('sc-temp-offset', 0),
    wind_factor: scNum('sc-wind-factor', 1),
    humidity_delta: scNum('sc-humidity-delta', 0),
    soil_delta: scNum('sc-soil-delta', 0),
    ndvi_delta: scNum('sc-ndvi-delta', 0),
    precip_add: scNum('sc-precip-add', 0),
  };
}

function updateSliderLabels() {
  const rf = scNum('sc-rain-factor', 1);
  const to = scNum('sc-temp-offset', 0);
  const wf = scNum('sc-wind-factor', 1);
  scEl('sc-rain-factor-label').textContent = `${rf.toFixed(1)}×`;
  scEl('sc-temp-offset-label').textContent = `${to > 0 ? '+' : ''}${to.toFixed(0)}°C`;
  scEl('sc-wind-factor-label').textContent = `${wf.toFixed(1)}×`;
}

function applyScenarioPreset(name, btn = null) {
  const presets = {
    rainstorm: { rf: 2.2, temp: 0, wind: 1.1, humidity: 15, soil: 0.10, ndvi: 0.0, add: 35, hazard: 'flood_risk' },
    longrain: { rf: 1.8, temp: -1, wind: 1.0, humidity: 15, soil: 0.12, ndvi: -0.02, add: 12, hazard: 'landslide' },
    hotdry: { rf: 0.35, temp: 6, wind: 1.1, humidity: -20, soil: -0.10, ndvi: -0.08, add: 0, hazard: 'drought_risk' },
    winddry: { rf: 0.55, temp: 3, wind: 1.8, humidity: -20, soil: -0.06, ndvi: -0.05, add: 0, hazard: 'fire_risk' },
    reset: { rf: 1, temp: 0, wind: 1, humidity: 0, soil: 0, ndvi: 0, add: 0, hazard: null },
  };
  const p = presets[name] || presets.reset;
  const set = (id, value) => { const el = scEl(id); if (el) el.value = String(value); };
  const hz = scEl('sc-hazard');
  if (hz && p.hazard) {
    hz.value = p.hazard;
    scEl('sc-hazard-title').textContent = SC_HAZARDS.find((x) => x.id === p.hazard)?.name || '灾害风险';
  }
  set('sc-rain-factor', p.rf);
  set('sc-temp-offset', p.temp);
  set('sc-wind-factor', p.wind);
  set('sc-humidity-delta', p.humidity);
  set('sc-soil-delta', p.soil);
  set('sc-ndvi-delta', p.ndvi);
  set('sc-precip-add', p.add);
  updateSliderLabels();
  document.querySelectorAll('#sc-presets .scenario-preset').forEach((b) => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  state.scenarioEditSeq = (state.scenarioEditSeq || 0) + 1;
  scEl('sc-message').textContent = `已载入“${btn?.textContent || '情景'}”，可继续微调，再点击“开始情景推演”。`;
  scEl('sc-result-tag').textContent = 'READY';
}

function startScenarioPick() {
  state.scenarioPicking = true;
  scEl('scenario-backdrop').classList.add('hidden');
  scEl('map').style.cursor = 'crosshair';
  toast('点击地图选择推演点位');
}

function handleScenarioPick(latlng) {
  state.scenarioPicking = false;
  scEl('map').style.cursor = '';
  scEl('scenario-backdrop').classList.remove('hidden');
  loadScenarioDefaults(latlng.lng, latlng.lat);
  toast(`已选点位 ${latlng.lng.toFixed(4)}, ${latlng.lat.toFixed(4)}`);
}

function setScenarioMarker(lon, lat, score = null, level = null) {
  if (!state.map) return;
  if (state.scenarioMarker) state.map.removeLayer(state.scenarioMarker);
  const color = level ? colorForLevel(level) : '#22d3ee';
  const icon = L.divIcon({
    className: '',
    html: `<div class="scenario-marker" style="--c:${color}"><span></span><i></i></div>`,
    iconSize: [20, 20], iconAnchor: [10, 10],
  });
  state.scenarioMarker = L.marker([lat, lon], { icon, zIndexOffset: 900 }).addTo(state.map);
  state.scenarioMarker.bindPopup(
    `<div class="pop-title">推演点位</div>
     <div class="pop-row"><span>坐标</span><b>${Number(lon).toFixed(4)}, ${Number(lat).toFixed(4)}</b></div>
     ${score != null ? `<div class="pop-row"><span>风险系数</span><b style="color:${color}">${Number(score).toFixed(0)}</b></div>` : ''}`,
    { className: 'eco-popup' }
  );
}

async function assessScenario() {
  const payload = readScenarioForm();
  const btn = scEl('sc-assess');
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = '计算中…';
  scEl('sc-result-tag').textContent = 'RUNNING';
  try {
    const d = await fetchJSON(`${API}/scenario/assess`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    if (!d?.ok) throw new Error(d?.error || '计算失败');
    showScenarioResult(d);
    toast(`危险系数 ${d.score} · ${d.level_label}`);
  } catch (e) {
    toast('危险系数计算失败：' + e.message, 'error');
    scEl('sc-result-tag').textContent = 'ERROR';
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

function showScenarioResult(d) {
  state.scenarioAssess = d;
  setGauge(d.score, d.level, d.level_label);
  scEl('sc-hazard-title').textContent = d.hazard_label || '灾害风险';
  scEl('sc-message').textContent = (d.reasons || []).slice(0, 3).join('；') || `当前危险系数 ${d.score}。`;
  const badges = [];
  badges.push(`<span class="sc-badge">${esc(d.level_label || '')}</span>`);
  if (d.nearest_hotspot) badges.push(`<span class="sc-badge">最近高发点：${esc(d.nearest_hotspot)}</span>`);
  if (d.nearest_cell) badges.push(`<span class="sc-badge">格网：${esc(d.nearest_cell)}</span>`);
  if (d.model?.static) badges.push('<span class="sc-badge">静态风险</span>');
  scEl('sc-badges').innerHTML = badges.join('');
  const rows = (d.factors || []).map((f) => `
    <div class="scenario-factor">
      <span title="${esc(f.label)}">${esc(f.label)}</span>
      <div class="bar"><i style="width:${Math.max(2, Number(f.value) * 100)}%"></i></div>
      <b>${Number(f.contribution).toFixed(0)}%</b>
    </div>`).join('');
  scEl('sc-factors').innerHTML = rows || '<div class="empty">暂无因子</div>';
  scEl('sc-result-tag').textContent = 'ASSESSED';
  scEl('sc-trigger')?.classList.add('hidden');
  setScenarioMarker(state.scenarioPoint[0], state.scenarioPoint[1], d.score, d.level);
}

function renderScenarioTrigger(trigger) {
  const box = scEl('sc-trigger');
  if (!box) return;
  if (!trigger?.available) {
    box.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  const unit = trigger.unit || '';
  const label = esc(trigger.label || trigger.variable || '驱动因子');
  const dir = trigger.direction === 'below' ? '低于' : '达到';
  const m = trigger.medium != null ? `${dir} <b>${trigger.medium}${unit}</b> 进入较高风险` : '未找到较高风险触发值';
  const h = trigger.high != null ? `${dir} <b>${trigger.high}${unit}</b> 进入高风险` : '未找到高风险触发值';
  box.innerHTML = `<b>模型触发条件（单因子敏感性）</b>
    <div>${label}：${m}；${h}。</div>
    <div class="trig-note">${esc(trigger.note || '')}</div>`;
  box.classList.remove('hidden');
}

function setGauge(score, level, label) {
  const color = colorForLevel(level);
  const g = scEl('sc-gauge');
  if (!g) return;
  g.style.setProperty('--p', String(Math.max(0, Math.min(100, Number(score) || 0))));
  g.style.setProperty('--c', color);
  scEl('sc-score').textContent = Math.round(Number(score) || 0);
  scEl('sc-level').textContent = label || '';
}

async function runScenarioTimeline() {
  const payload = readScenarioForm();
  const runBtn = scEl('sc-run');
  const assessBtn = scEl('sc-assess');
  runBtn.disabled = true;
  assessBtn.disabled = true;
  const old = runBtn.textContent;
  runBtn.textContent = '生成时空帧…';
  scEl('sc-result-tag').textContent = 'SIMULATING';
  scEl('sc-message').textContent = '正在读取未来 14 天气象并生成研究区逐日风险帧…';
  try {
    let assess = null;
    try {
      assess = await fetchJSON(`${API}/scenario/assess`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
    } catch (e) { /* 允许只出时间轴 */ }
    const t = await fetchJSON(`${API}/scenario/simulate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    if (!t?.ok) throw new Error(t?.error || '情景推演失败');
    state.scenario = t;
    state.scenarioFrame = 0;
    const slider = scEl('sc-slider');
    if (slider) { slider.min = '0'; slider.max = String(t.frames.length - 1); slider.value = '0'; }
    if (assess?.ok) showScenarioResult(assess);
    buildScenarioLayer(t);
    applyScenarioFrame(0);
    drawScenarioChart(t, 0);
    scEl('sc-message').textContent = t.message;
    const extra = [];
    extra.push(`<span class="sc-badge">基线峰值 ${Number(t.baseline_peak?.score || 0).toFixed(0)}</span>`);
    extra.push(`<span class="sc-badge">情景峰值 ${Number(t.scenario_peak?.score || 0).toFixed(0)} (${t.delta_peak >= 0 ? '+' : ''}${Number(t.delta_peak).toFixed(0)})</span>`);
    if (t.first_orange) extra.push(`<span class="sc-badge">首次较高：${esc(t.first_orange)}</span>`);
    if (t.first_red) extra.push(`<span class="sc-badge">首次高：${esc(t.first_red)}</span>`);
    if (extra.length) scEl('sc-badges').innerHTML += extra.join('');
    renderScenarioTrigger(t.trigger);
    scEl('sc-map-note').textContent = `${t.hazard_label} · ${t.days} 天 · ${t.grid_cells} 格网 · 基线 → 情景`;
    scEl('sc-result-tag').textContent = 'SCENARIO';
    startScenarioPlay();
    setTimeout(() => {
      const sec = document.querySelector('.result-section');
      if (sec) sec.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }, 350);
    if (t.warning) toast(t.warning, 'warn');
    else toast('时空推演已生成，正在播放逐日动画');
  } catch (e) {
    toast('时空推演失败：' + e.message, 'error');
    scEl('sc-result-tag').textContent = 'ERROR';
    scEl('sc-message').textContent = '推演失败：' + e.message;
  } finally {
    runBtn.disabled = false;
    assessBtn.disabled = false;
    runBtn.textContent = old;
  }
}

function buildScenarioLayer(t) {
  if (!state.riskGrid?.features?.length) {
    toast('风险格网尚未加载，先点击刷新', 'warn');
    return;
  }
  state.overlays.scenario.clearLayers();
  state.scenarioCellLayers = {};
  const first = t.frames?.[0]?.cells || {};
  state.scenarioGeo = L.geoJSON(state.riskGrid, {
    style: (f) => scStyle(first[f.properties.cell_id] ?? 0),
    onEachFeature: (f, layer) => {
      const id = f.properties.cell_id;
      state.scenarioCellLayers[id] = layer;
      layer.bindTooltip(`${id} · 点击查看`, { sticky: true });
      layer.on('click', () => {
        const fr = state.scenario?.frames?.[state.scenarioFrame];
        const score = fr?.cells?.[id];
        if (score == null) return;
        scEl('sc-map-note').textContent = `${id} · ${score.toFixed(0)} 分 · ${fr.date}`;
      });
    },
  }).addTo(state.overlays.scenario);
  state.overlays.scenario.addTo(state.map);
}

function applyScenarioFrame(i) {
  const t = state.scenario;
  if (!t) return;
  i = Math.max(0, Math.min(t.frames.length - 1, Number(i) || 0));
  state.scenarioFrame = i;
  const fr = t.frames[i];
  Object.entries(state.scenarioCellLayers).forEach(([id, layer]) => {
    layer.setStyle(scStyle(fr.cells[id] ?? 0));
  });
  scEl('sc-slider').value = String(i);
  scEl('sc-date').textContent = fr.date;
  scEl('scenario-map-badge').classList.remove('hidden');
  scEl('scenario-map-date').textContent = fr.date;
  scEl('scenario-map-score').textContent = `${Number(fr.point_score).toFixed(0)} 分`;
  const lv = scLevel(fr.point_score);
  setGauge(fr.point_score, lv.key, lv.label);
  scEl('sc-result-tag').textContent = `DAY ${i + 1}`;
  scEl('sc-map-note').textContent = `${t.hazard_label} · 区域最高 ${Number(fr.max).toFixed(0)} · 点位 ${Number(fr.point_score).toFixed(0)}`;
  setScenarioMarker(state.scenarioPoint[0], state.scenarioPoint[1], fr.point_score, fr.level);
  drawScenarioChart(t, i);
}

function toggleScenarioPlay() {
  if (state.scenarioTimer) {
    stopScenarioPlay();
  } else {
    if (!state.scenario) { toast('先点击“开始时空推演”', 'warn'); return; }
    if (state.scenarioFrame >= state.scenario.frames.length - 1) applyScenarioFrame(0);
    startScenarioPlay();
  }
}

function startScenarioPlay() {
  stopScenarioPlay();
  if (!state.scenario) return;
  const speed = Number(scEl('sc-speed')?.value || 1200);
  scEl('sc-play').textContent = '❚❚';
  state.scenarioTimer = setInterval(() => {
    const next = state.scenarioFrame + 1;
    if (next >= state.scenario.frames.length) {
      stopScenarioPlay();
      return;
    }
    applyScenarioFrame(next);
  }, speed);
}

function stopScenarioPlay() {
  if (state.scenarioTimer) {
    clearInterval(state.scenarioTimer);
    state.scenarioTimer = null;
  }
  const b = scEl('sc-play');
  if (b) b.textContent = '▶';
}

function resizeScenarioChart() {
  if (!scEcharts) return;
  const el = scEl('sc-chart');
  const w = Math.max(280, (el?.parentElement?.clientWidth || 600) - 12);
  scEcharts.resize({ width: w, height: 200 });
}

function drawScenarioChart(t, cursor) {
  const canvas = scEl('sc-chart');
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth || 620;
  const H = 200;
  canvas.width = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const series = t.scenario || t.point_series || [];
  const baseline = t.baseline || [];
  if (!series.length) {
    ctx.fillStyle = '#66788c';
    ctx.font = '11px "Microsoft YaHei", sans-serif';
    ctx.fillText('暂无推演数据', 20, H / 2);
    return;
  }
  if (window.echarts) {
    try {
    canvas.style.height = '200px';
    if (!scEcharts) scEcharts = echarts.init(canvas, null, { renderer: 'canvas' });
    const dates = series.map((p) => String(p.date).slice(5));
    const values = series.map((p) => Number(p.score));
    const baseValues = dates.map((d, i) => {
      const found = baseline.find((p) => String(p.date).slice(5) === d);
      return found ? Number(found.score) : null;
    });
    const cur = Math.max(0, Math.min(cursor, values.length - 1));
    const option = {
      backgroundColor: 'transparent',
      animationDuration: 500,
      grid: { left: 34, right: 14, top: 18, bottom: 24 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(8,16,26,.96)',
        borderColor: 'rgba(212,175,55,.35)',
        textStyle: { color: '#f6ead0', fontSize: 10 },
        formatter: (ps) => {
          const base = ps.find((p) => p.seriesName === '真实预报基线');
          const scen = ps.find((p) => p.seriesName === '设定情景');
          return `${ps[0].axisValue}<br/>基线 <b>${base?.data ?? '—'}</b><br/>情景 <b>${scen?.data ?? '—'}</b>`;
        },
      },
      xAxis: {
        type: 'category', data: dates, boundaryGap: false,
        axisLine: { lineStyle: { color: '#2a3544' } },
        axisTick: { show: false },
        axisLabel: { color: '#7f8b99', fontSize: 9, interval: Math.max(0, Math.ceil(dates.length / 6) - 1) },
      },
      yAxis: {
        type: 'value', min: 0, max: 100, splitNumber: 4,
        axisLabel: { color: '#7f8b99', fontSize: 9 },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,.05)' } },
        axisLine: { show: false },
      },
      series: [{
        type: 'line', name: '真实预报基线', data: baseValues, smooth: true,
        symbol: 'none', lineStyle: { width: 1.5, color: '#94a3b8', type: 'dashed' },
        z: 1,
        markLine: {
          silent: true, symbol: 'none', label: { show: false },
          lineStyle: { type: 'dashed', width: 1 },
          data: [
            { yAxis: 45, lineStyle: { color: 'rgba(255,214,10,.30)' } },
            { yAxis: 60, lineStyle: { color: 'rgba(255,149,0,.30)' } },
            { yAxis: 75, lineStyle: { color: 'rgba(255,59,48,.30)' } },
          ],
        },
      }, {
        type: 'line', name: '设定情景', data: values, smooth: true,
        symbol: 'circle', symbolSize: 4,
        lineStyle: { width: 2.2, color: '#d4af37' },
        itemStyle: { borderColor: '#fff', borderWidth: 1, color: '#d4af37' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(212,175,55,.24)' },
            { offset: 1, color: 'rgba(200,16,46,.02)' },
          ]),
        },
        markPoint: {
          symbolSize: 28,
          data: [{ type: 'max', name: '情景峰值' }],
          label: { color: '#12070a', fontSize: 9, fontWeight: 700 },
          itemStyle: { color: '#d4af37' },
        },
      }],
    };
    scEcharts.setOption(option, true);
    resizeScenarioChart();
    try {
      scEcharts.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: cur });
    } catch (e) { /* ignore */ }
    return;
    } catch (e) {
      /* ECharts 不可用时退回 Canvas */
    }
  }

  const L = 34, R = 14, T = 14, B = 24;
  const pw = Math.max(10, W - L - R), ph = H - T - B;
  const x = (i) => L + (series.length <= 1 ? 0 : (i / (series.length - 1)) * pw);
  const y = (v) => T + (1 - Math.max(0, Math.min(100, v)) / 100) * ph;

  ctx.font = '9px "Microsoft YaHei", sans-serif';
  ctx.textBaseline = 'middle';
  [0, 25, 45, 60, 75, 100].forEach((v) => {
    const yy = y(v);
    ctx.strokeStyle = 'rgba(255,255,255,.045)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(W - R, yy); ctx.stroke();
    ctx.fillStyle = '#66788c';
    ctx.textAlign = 'right';
    ctx.fillText(String(v), L - 6, yy);
  });
  [45, 60, 75].forEach((v, idx) => {
    const yy = y(v);
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = idx === 0 ? 'rgba(255,214,10,.35)' : idx === 1 ? 'rgba(255,149,0,.35)' : 'rgba(255,59,48,.35)';
    ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(W - R, yy); ctx.stroke();
    ctx.setLineDash([]);
  });

  // 基线（灰色虚线）
  if (baseline.length) {
    ctx.beginPath();
    baseline.forEach((p, i) => {
      if (i >= series.length) return;
      i === 0 ? ctx.moveTo(x(i), y(p.score)) : ctx.lineTo(x(i), y(p.score));
    });
    ctx.strokeStyle = 'rgba(148,163,184,.75)';
    ctx.lineWidth = 1.2;
    ctx.setLineDash([4, 4]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // 面积
  const grad = ctx.createLinearGradient(0, T, 0, T + ph);
  grad.addColorStop(0, 'rgba(34,211,238,.28)');
  grad.addColorStop(1, 'rgba(124,58,237,.02)');
  ctx.beginPath();
  series.forEach((p, i) => { i === 0 ? ctx.moveTo(x(i), y(p.score)) : ctx.lineTo(x(i), y(p.score)); });
  ctx.lineTo(x(series.length - 1), T + ph);
  ctx.lineTo(x(0), T + ph);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // 折线
  ctx.beginPath();
  series.forEach((p, i) => { i === 0 ? ctx.moveTo(x(i), y(p.score)) : ctx.lineTo(x(i), y(p.score)); });
  ctx.strokeStyle = '#22d3ee';
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.stroke();

  // 数据点
  series.forEach((p, i) => {
    ctx.beginPath();
    ctx.arc(x(i), y(p.score), i === cursor ? 4 : 2.2, 0, Math.PI * 2);
    ctx.fillStyle = i === cursor ? '#fff' : colorForLevel(p.level);
    ctx.fill();
    if (i === cursor) {
      ctx.strokeStyle = 'rgba(255,255,255,.55)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x(i), T); ctx.lineTo(x(i), T + ph); ctx.stroke();
      ctx.beginPath(); ctx.arc(x(i), y(p.score), 7, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(255,255,255,.25)';
      ctx.stroke();
    }
  });

  // x 轴日期
  ctx.fillStyle = '#66788c';
  ctx.textAlign = 'center';
  const step = Math.max(1, Math.ceil(series.length / 6));
  series.forEach((p, i) => {
    if (i % step === 0 || i === series.length - 1) ctx.fillText(String(p.date).slice(5), x(i), H - 10);
  });
}


window.addEventListener('app-ready', setupScenario);
if (typeof state !== 'undefined' && state.map) setupScenario();

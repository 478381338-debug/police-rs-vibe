/* 气候增强层：云/雨/雪对观测与候选排序的影响。独立 overlay，不改变主工作区。 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const TIER = {
    A: { label: "A 证据较全", color: "#027a48", fill: "#32d583" },
    B: { label: "B 多族支持", color: "#157f74", fill: "#2e90fa" },
    C: { label: "C 单源/受限", color: "#b54708", fill: "#fdb022" },
    D: { label: "D 证据不足", color: "#667085", fill: "#d0d5dd" },
  };
  const LEVEL = {
    adequate: { label: "可用", color: "#027a48" },
    limited: { label: "受限", color: "#b54708" },
    insufficient: { label: "不足", color: "#b42318" },
  };

  let data = null;
  let map = null;
  let candLayer = null;
  let aoiLayer = null;
  let layers = [];
  let filter = "all";
  let selected = -1;

  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function n(v, d = 2) {
    return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d);
  }
  function pct(v) { return v == null ? "—" : (Number(v) * 100).toFixed(0) + "%"; }
  function tierOf(p) { return TIER[p && p.climate_tier] || TIER.D; }
  function levelOf(p) {
    return LEVEL[p && p.climate_observation_level] || LEVEL.insufficient;
  }
  function inFilter(p) {
    if (filter === "above") return Number(p.climate_adjusted_score || 0) >= Number(data.threshold || 25);
    if (filter === "weather") {
      return (p.climate_flags || []).includes("weather_explained") ||
        Number(p.climate_confounder_combined || p.climate_confounder_strength || 0) >= 0.65;
    }
    if (filter === "blind") return (p.climate_observation_level || "adequate") !== "adequate";
    if (filter === "mining_belt" || filter === "A_control") return p.aoi === filter;
    return true;
  }

  function fmtTime(v) {
    if (!v) return "—";
    return String(v).replace("T", " ").slice(0, 16);
  }

  function statCards(d) {
    const cards = [
      [d.candidate_count, "候选图斑", ""],
      [d.candidate_area_ha, "候选面积 ha · 非违法", ""],
      [`${d.above_threshold}`, `校正后 ≥${n(d.threshold, 0)} 分`, "warn"],
      [`${d.weather_explained}`, "天气解释/降级", ""],
      [`${d.observation_blind}`, "观测不足候选", d.observation_blind ? "warn" : ""],
      [`${d.model_enabled}/${d.model_total}`, "天气模型启用", ""],
    ];
    return cards.map(([v, s, cls]) =>
      `<div class="ywa-card ${cls}"><b>${esc(v)}</b><span>${esc(s)}</span></div>`).join("");
  }

  function modelRow(name, m) {
    const ok = !!m.applied;
    return `<div class="ywc-model-row">
      <span class="ywc-model-name">${esc(name)}</span>
      <span class="ywc-pill ${ok ? "ok" : "off"}">${ok ? "启用" : "回退"}</span>
      <span>${m.improvement_pct == null ? "—" : n(m.improvement_pct, 2) + "%"}</span>
      <span class="ywc-muted">LOO-RMSE 改进 · R² ${n(m.harmonic_r2, 2)} → ${n(m.full_r2, 2)}</span>
    </div>`;
  }

  function aoiCard(a) {
    const obs = a.observation || {};
    const known = a.known_sites || {};
    const nc = a.negative_control || {};
    const rain = a.rain_correlation || {};
    const rows = Object.entries(a.model || {}).map(([k, m]) => modelRow(k, m)).join("");
    const knownText = known.n_sites_matched
      ? `已知点 ${known.n_sites_matched} 个匹配 · 平均排名变化 ${known.mean_rank_change == null ? "—" : (known.mean_rank_change > 0 ? "+" : "") + n(known.mean_rank_change, 2)}（正=提前）`
      : "3 km 内无已知点匹配（负对照）";
    return `<article class="ywc-aoi">
      <div class="ywc-aoi-head">
        <div><b>${esc(a.label || a.name)}</b><span class="ywc-muted"> ${esc(a.name)}</span></div>
        <span class="ywc-type">${esc(a.type || "—")}</span>
      </div>
      <div class="ywc-aoi-meta">${a.n_scenes} 景 · ${a.n_candidates} 候选</div>
      <div class="ywc-kv"><span>观测</span><b>${obs.n_usable ?? "—"}/${obs.n_scenes ?? "—"} 可用 · 最大空窗 ${obs.max_gap_days ?? "—"} 天</b></div>
      <div class="ywc-kv"><span>探测能力</span><b style="color:${(LEVEL[obs.level] || LEVEL.insufficient).color}">${esc(obs.level || "—")} · power ${n(obs.power, 3)}</b></div>
      <div class="ywc-kv"><span>已知点/负对照</span><b>${esc(knownText)}</b></div>
      <div class="ywc-kv"><span>候选降级</span><b>低于阈值 ${nc.n_below_threshold_after ?? "—"}/${nc.n_candidates ?? "—"} · 天气解释 ${nc.n_weather_explained ?? 0}</b></div>
      <div class="ywc-kv"><span>降雨相关</span><b>${rain.spearman_before == null ? "—" : n(rain.spearman_before, 3)} → ${rain.spearman_after == null ? "—" : n(rain.spearman_after, 3)}</b></div>
      <div class="ywc-models">${rows || '<span class="ywc-muted">无模型</span>'}</div>
    </article>`;
  }

  function weatherTable(a) {
    const tw = a.target_weather || {};
    const rows = (tw.monthly || []).map((m) => `<tr>
      <td>${esc(String(m.month || "").slice(5))}</td>
      <td>${n(m.precip_mm, 1)}</td>
      <td>${n(m.snowfall_cm, 1)}</td>
      <td>${n(m.snow_depth_max_cm, 1)}</td>
      <td>${n(m.cloud_cover_mean_pct, 0)}</td>
      <td>${n(m.temp_mean_c, 1)}</td>
    </tr>`).join("");
    const scl = tw.scl || {};
    return `<div class="ywc-weather">
      <div class="ywc-weather-head"><b>${esc(a.label || a.name)}</b>
        <span class="ywc-muted">SCL 云 ${pct(scl.cloud_frac_mean)} · 雪 ${pct(scl.snow_frac_mean)} · 有效 ${pct(scl.valid_frac_mean)}（${scl.n_scenes ?? "—"} 景）</span>
      </div>
      <div class="ywa-table-wrap"><table class="ywa-table ywc-table"><thead><tr>
        <th>月</th><th>降水 mm</th><th>降雪 cm</th><th>雪深 cm</th><th>云量 %</th><th>均温 ℃</th>
      </tr></thead><tbody>${rows}</tbody></table></div>
    </div>`;
  }

  function typeTable(types) {
    const names = { open_pit: "露天采坑/采石", river_sand: "河道采砂", underground: "硐采/井工", stockpile: "堆场/排土", remining: "废弃矿复采", transport: "运输/外运" };
    return `<div class="ywa-table-wrap"><table class="ywa-table ywc-types"><thead><tr>
      <th>类型</th><th>主方法</th><th>辅助方法</th><th>主要天气混淆</th><th>最少清晰观测</th><th>最大空窗</th>
    </tr></thead><tbody>${Object.entries(types || {}).map(([k, t]) => `<tr>
      <td><b>${esc(t.label || names[k] || k)}</b><div class="ywc-muted">${esc(k)}</div></td>
      <td class="ywc-wrap">${(t.primary || []).map((x) => `<span class="ywc-chip">${esc(x)}</span>`).join("")}</td>
      <td class="ywc-wrap">${(t.secondary || []).map((x) => `<span class="ywc-chip">${esc(x)}</span>`).join("")}</td>
      <td class="ywc-wrap">${(t.confounders || []).slice(0, 4).map((x) => `<span class="ywc-chip warn">${esc(x)}</span>`).join("")}</td>
      <td>${t.clear_min ?? "—"}</td><td>${t.max_gap_days ?? "—"} d</td>
    </tr>`).join("")}</tbody></table></div>`;
  }

  function filters() {
    const items = [
      ["all", "全部"], ["above", "校正后 ≥阈值"], ["weather", "天气降级"],
      ["blind", "观测不足"], ["mining_belt", "采石带"], ["A_control", "农田负对照"],
    ];
    return items.map(([k, label]) =>
      `<button type="button" data-f="${k}" class="${k === filter ? "active" : ""}">${esc(label)}</button>`).join("");
  }

  function card(p, i) {
    const t = tierOf(p);
    const lv = levelOf(p);
    const raw = p.climate_delta_bsi_raw;
    const adj = p.climate_delta_bsi;
    const delta = raw == null && adj == null
      ? (p.persistent_bare ? "持续裸土 · 无告警日" : "ΔBSI 无值")
      : `ΔBSI ${n(raw, 3)} → ${n(adj, 3)}`;
    const flags = (p.climate_flags || []).map((f) => `<span class="ywc-chip warn">${esc(f)}</span>`).join("");
    const reasons = (p.reason || []).map((r) =>
      `<div class="ywc-reason-row"><span>${esc(r.label)}</span><b>${esc(r.value)}</b></div>`).join("");
    return `<div class="ywc-item ${selected === i ? "selected" : ""}" data-i="${i}">
      <div class="ywc-item-head">
        <span class="ywc-rank">#${i + 1}</span>
        <span class="ywc-muted">本区 #${p.climate_rank ?? "—"}</span>
        <span class="ywc-aoi-tag">${esc(p.aoi_label || p.aoi || "—")}</span>
        <span class="ywc-pill" style="color:${t.color};border-color:${t.color}55;background:${t.color}12">${esc(t.label)}</span>
      </div>
      <div class="ywc-item-main">${n(p.area_ha)} ha · ${esc(p.layer || "未分层")} · ${esc(delta)}</div>
      <div class="ywc-item-sub">
        <span style="color:${lv.color}">${esc(lv.label)} ${n(p.climate_detection_power, 2)}</span>
        · 云 ${pct(p.climate_cloud_frac)} · 雪 ${pct(p.climate_snow_frac)} · 7 日雨 ${n(p.climate_rain_7d_mm, 1)} mm
      </div>
      <div class="ywc-item-score">证据 ${esc(p.evidence_tier || "—")}/${esc(p.evidence_priority || "Q")}
        · 校正前 ${n(p.climate_pre_score)} → <b>${n(p.climate_adjusted_score)}</b></div>
      ${flags ? `<div class="ywc-chips">${flags}</div>` : ""}
      <div class="ywc-reason" hidden><div class="ywc-reason-title">气候校正依据</div>${reasons}</div>
    </div>`;
  }

  function renderList() {
    const list = $("ywc-list");
    if (!list) return;
    const feats = (data.candidates && data.candidates.features) || [];
    const visible = [];
    feats.forEach((f, i) => { if (inFilter(f.properties || {})) visible.push([f, i]); });
    list.innerHTML = visible.length
      ? visible.map(([f, i]) => card(f.properties || {}, i)).join("")
      : '<p class="ywc-empty">该筛选下没有候选。</p>';
    list.querySelectorAll(".ywc-item").forEach((el) => {
      el.addEventListener("click", () => select(Number(el.dataset.i), true));
    });
    const count = $("ywc-count");
    if (count) count.textContent = `显示 ${visible.length} / ${feats.length}`;
  }

  function styleFor(p, active) {
    const t = tierOf(p);
    return {
      color: t.color, weight: active ? 3 : 1.2,
      fillColor: t.fill, fillOpacity: active ? 0.4 : 0.18,
    };
  }

  function drawMap() {
    if (!map) return;
    candLayer.clearLayers();
    aoiLayer.clearLayers();
    layers = [];
    const bounds = [];
    (data.aois || []).forEach((a) => {
      if (!a.bbox || a.bbox.length !== 4) return;
      const [w, s, e, nn] = a.bbox;
      const rect = L.rectangle([[s, w], [nn, e]], {
        color: "#157f74", weight: 1, dashArray: "6 4", fill: false,
      }).bindPopup(`<b>${esc(a.label || a.name)}</b><br>${esc(a.type || "")} · ${a.n_scenes} 景 · ${a.n_candidates} 候选`);
      rect.addTo(aoiLayer);
      bounds.push(rect.getBounds());
    });
    const feats = (data.candidates && data.candidates.features) || [];
    feats.forEach((f, i) => {
      if (!f.geometry) return;
      const p = f.properties || {};
      if (!inFilter(p)) { layers[i] = null; return; }
      const layer = L.geoJSON(f, { style: styleFor(p, false) });
      layer.bindPopup(`<b>${esc(p.aoi_label || p.aoi)} · ${n(p.area_ha)} ha</b><br>` +
        `${esc(tierOf(p).label)} · 校正后 ${n(p.climate_adjusted_score)}<br>` +
        `观测 ${esc(p.climate_observation_level || "—")} · 云 ${pct(p.climate_cloud_frac)} · 雪 ${pct(p.climate_snow_frac)}<br>` +
        `<span style="color:#667085">排序分，不是概率或违法认定</span>`);
      layer.on("click", () => select(i, false));
      layer.addTo(candLayer);
      layers[i] = layer;
      bounds.push(layer.getBounds());
    });
    if (bounds.length) {
      const b = bounds.reduce((acc, x) => acc.extend(x), bounds[0]);
      map.fitBounds(b.pad(0.08), { maxZoom: 12 });
    }
  }

  function select(i, fromList) {
    selected = i;
    const f = (data.candidates.features || [])[i];
    if (!f) return;
    const p = f.properties || {};
    if (layers[i]) {
      layers[i].setStyle(styleFor(p, true));
      layers[i].bringToFront();
      if (!fromList && map) {
        const b = layers[i].getBounds();
        if (b && b.isValid()) map.fitBounds(b.pad(0.6), { maxZoom: 14 });
      }
    }
    document.querySelectorAll(".ywc-item").forEach((el) => {
      const on = Number(el.dataset.i) === i;
      el.classList.toggle("selected", on);
      const reason = el.querySelector(".ywc-reason");
      if (reason) reason.hidden = !on;
      if (on) el.scrollIntoView({ block: "nearest" });
    });
  }

  function initMap() {
    map = L.map("ywc-map", { zoomControl: true, attributionControl: true })
      .setView([37.27, 120.15], 10);
    L.tileLayer("/api/tile/osm/{z}/{x}/{y}", {
      maxZoom: 18, maxNativeZoom: 17,
      errorTileUrl: "/static/assets/tile-fallback.png",
      attribution: "本地代理瓦片 · 非正式边界",
    }).addTo(map);
    aoiLayer = L.layerGroup().addTo(map);
    candLayer = L.layerGroup().addTo(map);
  }

  function render() {
    const d = data;
    map = null;
    $("ywc-sub").textContent =
      `目标年 ${d.target_year} · 生成 ${fmtTime(d.generated_at)} · ${d.candidate_count} 候选 / ${n(d.candidate_area_ha)} ha · 排序分不是概率`;
    $("ywc-body").innerHTML = `
      <div class="ywa-cards">${statCards(d)}</div>
      <div class="ywa-banner"><strong>${esc(d.disclaimer || "排序分不是概率。")}</strong>
        <div class="ywc-muted">天气：${esc((d.data_sources || {}).weather || "—")} · SCL：${esc((d.data_sources || {}).scl || "—")}</div>
      </div>
      <section class="ywa-section"><h3>区域、天气模型与观测能力</h3>
        <div class="ywc-aoi-grid">${(d.aois || []).map(aoiCard).join("")}</div>
      </section>
      <section class="ywa-section"><h3>目标年云 / 雨 / 雪</h3>
        <div class="ywc-weather-grid">${(d.aois || []).map(weatherTable).join("")}</div>
      </section>
      <section class="ywa-section"><h3>六类采矿画像 · 差异化方法</h3>${typeTable(d.types)}</section>
      <section class="ywa-section"><h3>候选气候校正 <span class="ywc-muted" id="ywc-count">—</span></h3>
        <p class="ywa-note">天气校正改变的是 ΔBSI/NDVI 与观测可信度，不改变证据事实；点击图斑或条目查看逐项依据。</p>
        <div class="ywa25-filters ywc-filters">${filters()}</div>
        <div class="ywc-split">
          <div id="ywc-map" class="ywc-map"></div>
          <div id="ywc-list" class="ywc-list"></div>
        </div>
        <div class="ywc-legend">
          ${Object.entries(TIER).map(([, t]) => `<span><i style="background:${t.fill};border:1px solid ${t.color}"></i>${esc(t.label)}</span>`).join("")}
        </div>
      </section>
      <div class="ywa-foot">
        <a href="/api/yantai/climate/report" target="_blank" rel="noopener">查看完整气候增强报告（Markdown）</a>
        <span>${(d.caveats || []).map((x) => esc(x)).join(" · ")}</span>
      </div>`;
    $("ywc-body").querySelectorAll(".ywc-filters button").forEach((btn) => {
      btn.addEventListener("click", () => {
        filter = btn.dataset.f;
        $("ywc-body").querySelectorAll(".ywc-filters button")
          .forEach((b) => b.classList.toggle("active", b === btn));
        selected = -1;
        renderList();
        drawMap();
      });
    });
    setTimeout(() => {
      initMap();
      drawMap();
      renderList();
      if (map) map.invalidateSize();
    }, 60);
  }

  async function open() {
    $("ywc-overlay").hidden = false;
    document.documentElement.style.overflow = "hidden";
    if (!data) {
      $("ywc-body").innerHTML = '<p class="ywa-error">读取气候增强结果…</p>';
      try {
        const r = await fetch("/api/yantai/climate");
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d = await r.json();
        if (d.status !== "ok") throw new Error(d.note || d.status);
        data = d;
      } catch (err) {
        $("ywc-body").innerHTML = `<p class="ywa-error">读取失败：${esc(err.message)}</p>`;
        return;
      }
    }
    render();
    setTimeout(() => { if (map) map.invalidateSize(); }, 120);
  }

  function close() {
    $("ywc-overlay").hidden = true;
    document.documentElement.style.overflow = "";
  }

  function bind() {
    const openBtn = $("yw-climate-open");
    if (openBtn) openBtn.addEventListener("click", open);
    const closeBtn = $("ywc-close");
    if (closeBtn) closeBtn.addEventListener("click", close);
    const overlay = $("ywc-overlay");
    if (overlay) overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && overlay && !overlay.hidden) close();
    });
  }

  bind();
})();
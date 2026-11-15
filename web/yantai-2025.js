/* 2025 全年结果（现行 v2.1）：地图定位 + 侧边栏计算原因。不改动主工作区状态。 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const LAYER = {
    mine_candidate: { label: "矿区候选", color: "#d92d20", fill: "#f79009", order: 0 },
    farmland_destruction_suspect: { label: "毁田采矿疑似", color: "#b54708", fill: "#fdb022", order: 1 },
    uncertain: { label: "不确定", color: "#6941c6", fill: "#d6bbfb", order: 2 },
    other_bare: { label: "其他裸地", color: "#475467", fill: "#d0d5dd", order: 3 },
    farmland_management: { label: "农田管理", color: "#157f74", fill: "#2e90fa", order: 4 },
    unclassified: { label: "未分层", color: "#667085", fill: "#98a2b3", order: 5 },
  };
  let data = null;
  let map = null;
  let aoiLayer = null;
  let candLayer = null;
  let layers = [];
  let markers = [];
  let filter = "all";
  let selected = -1;

  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function n(v, d = 2) { return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d); }
  function layerOf(p) { return LAYER[p && p.layer] || LAYER.unclassified; }
  function pct(v) { return v == null ? "—" : (Number(v) * 100).toFixed(0) + "%"; }
  function isPb(p) { return p.delta_bsi == null && !!p.persistent_bare; }
  function km(m) { return m == null ? "—" : (Number(m) / 1000).toFixed(1) + " km"; }

  function initMap() {
    if (map) return;
    map = L.map("ywa25-map", { zoomControl: true, attributionControl: true })
      .setView([37.27, 120.15], 10);
    L.tileLayer("/api/tile/osm/{z}/{x}/{y}", {
      maxZoom: 18, maxNativeZoom: 17,
      errorTileUrl: "/static/assets/tile-fallback.png",
      attribution: "本地代理瓦片 · 非正式边界",
    }).addTo(map);
    aoiLayer = L.layerGroup().addTo(map);
    candLayer = L.layerGroup().addTo(map);
  }

  function stats() {
    const d = data;
    $("ywa25-stats").innerHTML = `
      <div class="ywa25-stat"><b>${d.candidate_count}</b><span>候选图斑</span></div>
      <div class="ywa25-stat"><b>${d.mine_candidate_count == null ? "—" : d.mine_candidate_count}</b><span>矿区候选 · ${n(d.mine_candidate_area_ha)} ha</span></div>
      <div class="ywa25-stat"><b>${n(d.candidate_area_ha)}</b><span>候选面积 ha · 非违法</span></div>
      <div class="ywa25-stat ${d.dispatchable_count ? "warn" : ""}"><b>${d.dispatchable_count == null ? "—" : d.dispatchable_count}</b><span>可派单 P1+P2</span></div>
      <div class="ywa25-stat"><b>${d.scene_count}</b><span>目标景（两样区共用）</span></div>
      <div class="ywa25-stat"><b>${esc(d.level || "F1")}</b><span>阶段层级 · 不是分数</span></div>`;
    const note = $("ywa25-hp-note");
    if (note) {
      const ev = d.evidence_counts || {};
      const evText = ev.A == null ? "" :
        `证据等级 A${ev.A} · B${ev.B} · C${ev.C} · D${ev.D}（完备度，不是概率）。`;
      note.textContent = (evText + (d.high_priority_note || "")).trim();
      note.hidden = !note.textContent;
    }
    $("ywa25-foot").innerHTML = `
      <div class="ywa25-caveats">${(d.caveats || []).map((x) => "<span>" + esc(x) + "</span>").join("")}</div>
      <div class="ywa25-meta"><span>生成：${esc(d.generated_at || "—")}</span>
      <a href="#" id="ywa25-to-assess">查看修订清单与原版对照</a></div>`;
    const link = $("ywa25-to-assess");
    if (link) link.addEventListener("click", (e) => {
      e.preventDefault();
      close();
      const btn = $("yw-assessment-open");
      if (btn) btn.click();
    });
  }

  function inFilter(p) {
    if (filter === "high") return !!p.high;
    if (filter === "mine") return p.layer === "mine_candidate";
    if (filter === "farmland") return p.layer === "farmland_management";
    if (filter === "other") return ["other_bare", "uncertain", "farmland_destruction_suspect"].includes(p.layer);
    if (filter === "mining_belt" || filter === "A_control") return p.aoi === filter;
    return true;
  }

  function styleFor(p, active) {
    const cfg = layerOf(p);
    if (active) return { color: "#1d4ed8", weight: 3, fillColor: cfg.fill, fillOpacity: 0.5 };
    return {
      color: cfg.color,
      weight: p.layer === "mine_candidate" ? 2 : 1.2,
      fillColor: cfg.fill,
      fillOpacity: p.layer === "mine_candidate" ? 0.32 : 0.18,
    };
  }

  function drawMap() {
    initMap();
    aoiLayer.clearLayers();
    candLayer.clearLayers();
    layers = [];
    markers = [];
    const bounds = [];
    (data.aois || []).forEach((a) => {
      if (!a.bbox || a.bbox.length !== 4) return;
      const [w, s, e, nn] = a.bbox;
      const rect = L.rectangle([[s, w], [nn, e]], {
        color: "#157f74", weight: 1, dashArray: "6 4", fill: false,
      }).bindPopup(`<b>${esc(a.name)}</b><br>${(a.bbox || []).map((v) => n(v, 3)).join(", ")}<br>` +
        `${a.target_scenes} 景 · 候选 ${a.candidates} 个 / ${n(a.candidate_area_ha)} ha · h=${n(a.h, 2)}` +
        (a.h_at_upper_bound ? "（触上限）" : ""));
      rect.addTo(aoiLayer);
      bounds.push(rect.getBounds());
    });
    const feats = (data.candidates && data.candidates.features) || [];
    feats.forEach((f, i) => {
      if (!f.geometry) return;
      const p = f.properties || {};
      if (!inFilter(p)) { layers[i] = null; return; }
      const cfg = layerOf(p);
      const layer = L.geoJSON(f, { style: styleFor(p, false) });
      const pb = isPb(p);
      layer.bindPopup(`<b>${esc(p.aoi)} · ${n(p.area_ha)} ha</b><br>` +
        `${esc(cfg.label)} · 证据 ${esc(p.evidence_tier || "—")} / ${esc(p.evidence_priority || "Q")}<br>` +
        (pb
          ? `持续裸土规则补全 · 基线 ${p.baseline_summer_bare_years ?? "—"} 年夏季裸 · 距已知矿区 ${km(p.distance_known_m)}<br>`
          : `ΔBSI ${n(p.delta_bsi, 3)} · max CUSUM ${n(p.max_cusum, 1)}<br>` +
            `报警 ${esc(p.first_alarm || "—")} — ${esc(p.last_alarm || "—")}<br>`) +
        `<span style="color:#667085">线索，不是违法认定</span>`);
      layer.on("click", () => select(i, true));
      layer.addTo(candLayer);
      layers[i] = layer;
      bounds.push(layer.getBounds());
      if (p.centroid_lonlat && p.centroid_lonlat.length === 2) {
        const mk = L.circleMarker([p.centroid_lonlat[1], p.centroid_lonlat[0]], {
          radius: p.layer === "mine_candidate" ? 5 : 4,
          color: cfg.color,
          weight: 1.2,
          fillColor: cfg.fill,
          fillOpacity: 0.9,
        });
        mk.on("click", () => select(i, true));
        mk.addTo(candLayer);
        markers[i] = mk;
      }
    });
    if (bounds.length) {
      const b = bounds.reduce((acc, x) => acc.extend(x), bounds[0]);
      map.fitBounds(b.pad(0.08), { maxZoom: 12 });
    }
  }

  function card(p, i) {
    const cfg = layerOf(p);
    const reasons = (p.reason || []).map((r) =>
      `<div class="ywa25-reason-row"><span>${esc(r.label)}</span><b>${esc(r.value)}</b></div>`).join("");
    const ev = p.evidence_tier
      ? `<span class="ywa25-ev">证据 ${esc(p.evidence_tier)} · ${esc(p.evidence_priority || "Q")}</span>` : "";
    return `<div class="ywa25-item ${p.layer === "mine_candidate" ? "high" : ""}" data-i="${i}">
      <div class="ywa25-item-head">
        <span class="ywa25-idx">#${i + 1}</span>
        <span class="ywa25-aoi">${esc(p.aoi)}</span>
        <span class="ywa25-chips">
          <span class="ywa25-layer" style="color:${cfg.color};border-color:${cfg.color}55;background:${cfg.color}12">${esc(cfg.label)}</span>
          ${ev}
        </span>
      </div>
      ${isPb(p)
        ? `<div class="ywa25-item-meta">${n(p.area_ha)} ha · 规则补全（持续裸土）</div>
      <div class="ywa25-item-meta sub">基线 ${p.baseline_summer_bare_years ?? "—"} 年夏季裸 · 距已知矿区 ${km(p.distance_known_m)}</div>`
        : `<div class="ywa25-item-meta">${n(p.area_ha)} ha · ΔBSI ${n(p.delta_bsi, 3)} · CUSUM ${n(p.max_cusum, 1)}</div>
      <div class="ywa25-item-meta sub">${esc(p.first_alarm || "—")} — ${esc(p.last_alarm || "—")} · 有效 ${pct(p.valid_frac)}</div>`}
      <div class="ywa25-item-meta sub">夏季裸 ${pct(p.summer_bare_frac)} · ${p.persistent_bare ? "持续裸土" : "非持续"}</div>
      <div class="ywa25-item-reason" hidden>
        <div class="ywa25-reason-title">计算原因</div>
        ${reasons}
        <div class="ywa25-reason-note">面积是像元聚类面积；报警窗口是影像日期，不是活动起始时间。</div>
      </div>
    </div>`;
  }

  function methodBlock() {
    const m = data.method || {};
    return `<div class="ywa25-method">
      <div class="ywa25-reason-title">整体怎么算的</div>
      <p class="ywa25-method-name">${esc(m.name || "")}</p>
      <ol>${(m.steps || []).map((s) => "<li>" + esc(s) + "</li>").join("")}</ol>
      <div class="ywa25-formula">${(m.formula || []).map((f) => "<code>" + esc(f) + "</code>").join("")}</div>
      ${m.note ? `<p class="ywa25-reason-note">${esc(m.note)}</p>` : ""}
    </div>`;
  }

  function renderList() {
    const feats = (data.candidates && data.candidates.features) || [];
    const idx = feats.map((f, i) => i).filter((i) => inFilter(feats[i].properties || {}))
      .sort((a, b) => {
        const pa = feats[a].properties || {}, pb = feats[b].properties || {};
        return (layerOf(pa).order - layerOf(pb).order) ||
          (Number(pb.area_ha || 0) - Number(pa.area_ha || 0));
      });
    $("ywa25-list").innerHTML = idx.map((i) => card(feats[i].properties, i)).join("") ||
      '<p class="ywa25-empty">当前筛选没有候选。</p>';
    $("ywa25-list").insertAdjacentHTML("beforeend", methodBlock());
    $("ywa25-list").querySelectorAll(".ywa25-item").forEach((el) => {
      el.addEventListener("click", () => select(Number(el.dataset.i)));
    });
    if (selected >= 0 && idx.includes(selected)) {
      const el = $("ywa25-list").querySelector(`[data-i="${selected}"]`);
      if (el) el.classList.add("active");
    }
  }

  function select(i, fromMap) {
    const feats = (data.candidates && data.candidates.features) || [];
    if (selected >= 0) {
      const prev = $("ywa25-list").querySelector(`[data-i="${selected}"]`);
      if (prev) {
        prev.classList.remove("active");
        const r = prev.querySelector(".ywa25-item-reason");
        if (r) r.hidden = true;
      }
    }
    selected = i;
    const el = $("ywa25-list").querySelector(`[data-i="${i}"]`);
    if (el) {
      el.classList.add("active");
      const r = el.querySelector(".ywa25-item-reason");
      if (r) r.hidden = false;
      if (fromMap) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    layers.forEach((l, k) => {
      if (!l) return;
      l.setStyle(styleFor(feats[k].properties || {}, k === i));
      if (k === i) l.bringToFront();
    });
    markers.forEach((m, k) => {
      if (!m) return;
      const p = feats[k].properties || {};
      const cfg = layerOf(p);
      m.setStyle({
        radius: k === i ? 8 : (p.layer === "mine_candidate" ? 5 : 4),
        weight: k === i ? 2.5 : 1.2,
        color: k === i ? "#1d4ed8" : cfg.color,
        fillColor: k === i ? "#1d4ed8" : cfg.fill,
      });
      if (k === i) m.bringToFront();
    });
    const layer = layers[i];
    if (layer && !fromMap) map.fitBounds(layer.getBounds().pad(2), { maxZoom: 13 });
  }

  function render() {
    stats();
    renderList();
    drawMap();
  }

  async function open() {
    $("ywa25-overlay").hidden = false;
    document.documentElement.style.overflow = "hidden";
    if (!data) {
      $("ywa25-list").innerHTML = '<p class="ywa25-empty">正在读取 2025 全年分析（v2.1）…</p>';
      try {
        const r = await fetch("/api/yantai/analysis2025", { credentials: "same-origin" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        data = await r.json();
        if (data.status !== "ok") throw new Error(data.note || data.status);
        render();
      } catch (err) {
        $("ywa25-list").innerHTML = `<p class="ywa25-empty">读取失败：${esc(err.message)}</p>`;
        return;
      }
    }
    setTimeout(() => { if (map) map.invalidateSize(); }, 80);
  }

  function close() {
    $("ywa25-overlay").hidden = true;
    document.documentElement.style.overflow = "";
  }

  function bind() {
    const openBtn = $("yw-2025-open");
    if (openBtn) openBtn.addEventListener("click", open);
    const closeBtn = $("ywa25-close");
    if (closeBtn) closeBtn.addEventListener("click", close);
    const overlay = $("ywa25-overlay");
    if (overlay) overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && overlay && !overlay.hidden) close();
    });
    document.querySelectorAll(".ywa25-filters button").forEach((btn) => {
      btn.addEventListener("click", () => {
        filter = btn.dataset.f;
        document.querySelectorAll(".ywa25-filters button").forEach((b) => b.classList.toggle("active", b === btn));
        selected = -1;
        renderList();
        drawMap();
      });
    });
  }

  bind();
})();
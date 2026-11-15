/* 烟台专题浮窗：独立 Leaflet 小地图 + 资源/河段/事件列表。
   数据全部来自 /api/yantai；页面不生成“违法”结论，只展示候选与待核状态。 */
(function () {
  "use strict";

  const panel = document.getElementById("yantai-panel");
  const entry = document.getElementById("fit-yantai");
  if (!panel || !entry) return;

  const $ = (id) => document.getElementById(id);
  const state = {
    open: false,
    map: null,
    layers: null,
    loaded: false,
    tab: "events",
    data: { overview: null, resources: [], rivers: [], events: [] },
    selected: null,
  };
  const PRIORITY = { P1: "一级", P2: "二级", P3: "观察", Q: "资料不足" };
  const PRIORITY_COLOR = { P1: "#dc2626", P2: "#ea580c", P3: "#ca8a04", Q: "#64748b" };

  const esc = (value) => String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const fmt = (value, digits) => {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits == null ? 1 : digits) : "--";
  };
  const coords = (lonlat) => Array.isArray(lonlat) && lonlat.length === 2
    ? `${Number(lonlat[1]).toFixed(4)}, ${Number(lonlat[0]).toFixed(4)}`
    : "未取得";

  async function api(path, options) {
    const res = await fetch(path, Object.assign({
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }, options || {}));
    if (!res.ok) {
      let detail = "请求失败";
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      if (res.status === 401) detail = "请先登录案件工作台后再查看专题";
      throw new Error(detail);
    }
    return res.json();
  }

  function toast(message) {
    let el = panel.querySelector(".yt-toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "yt-toast";
      panel.appendChild(el);
    }
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  function initMap() {
    if (state.map) return;
    const map = L.map("yantai-map", {
      zoomControl: true,
      attributionControl: true,
      preferCanvas: true,
      minZoom: 7,
      maxZoom: 16,
    }).setView([37.35, 120.35], 9);
    state.map = map;
    L.tileLayer("/api/tile/osm/{z}/{x}/{y}", {
      maxZoom: 18, maxNativeZoom: 17,
      errorTileUrl: "/static/assets/tile-fallback.png",
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);

    // 工作范围只是示意，不冒充烟台行政边界。
    L.rectangle([[36.95, 119.85], [37.72, 121.30]], {
      color: "#0f766e", weight: 1.2, dashArray: "6 6", fill: false, opacity: 0.65,
    }).addTo(map).bindTooltip("工作范围（示意，非行政边界）", { sticky: true });

    state.layers = {
      rivers: L.layerGroup().addTo(map),
      resources: L.layerGroup().addTo(map),
      events: L.layerGroup().addTo(map),
    };
  }

  function renderStatus() {
    const o = state.data.overview;
    if (!o) return;
    const cov = o.coverage || {};
    const age = Number(cov.observation_age_days);
    const ageText = Number.isFinite(age) ? `${age} 天前` : "未知";
    $("yantai-status").innerHTML = `
      <span><b>${esc(o.events_count)}</b> 条线索</span>
      <span>观测 <b>${esc(cov.latest_observation || "未取得")}</b>（${esc(ageText)}）</span>
      <span>监测 <b>${esc(cov.monitored_area || "未取得")}</b></span>
      <span class="yt-badge">${esc(o.data_status)}</span>`;
    $("yantai-foot").textContent = `检查 ${o.monitor?.checked_at || "未检查"} · ${o.monitor?.mode || "本地数据"}`;
    $("yt-count-events").textContent = o.events_count;
    $("yt-count-resources").textContent = o.resources_count;
    $("yt-count-rivers").textContent = o.rivers_count;
  }

  function clearLayers() {
    if (!state.layers) return;
    state.layers.rivers.clearLayers();
    state.layers.resources.clearLayers();
    state.layers.events.clearLayers();
  }

  function drawLayers() {
    if (!state.map || !state.layers) return;
    clearLayers();

    state.data.rivers.forEach((river) => {
      if (!Array.isArray(river.coords) || river.coords.length < 2) return;
      const line = L.polyline(river.coords, {
        color: "#0284c7", weight: 2.5, opacity: 0.68,
      }).addTo(state.layers.rivers);
      line.bindTooltip(`${river.name} · ${fmt(river.length_km)} km · 待水利确认`, { sticky: true });
      line.on("click", () => showRiver(river));
    });

    state.data.resources.forEach((res) => {
      const isRs = res.type === "quarry_rs";
      const marker = L.circleMarker([res.lat, res.lon], {
        radius: isRs ? 7 : 5,
        color: isRs ? "#c2410c" : "#0f766e",
        weight: 1.5,
        fillColor: isRs ? "#fb923c" : "#2dd4bf",
        fillOpacity: 0.82,
      }).addTo(state.layers.resources);
      marker.bindPopup(`<b>${esc(res.name)}</b><br>${esc(res.source)}<br>${esc(res.status)}`);
      marker.on("click", () => showResource(res));
    });

    state.data.events.forEach((event) => {
      const color = PRIORITY_COLOR[event.priority] || "#64748b";
      if (event.geometry) {
        L.geoJSON(event.geometry, {
          style: { color, weight: 1.6, fillColor: color, fillOpacity: 0.16, dashArray: "4 3" },
        }).addTo(state.layers.events).on("click", () => selectEvent(event.id));
      }
      if (Array.isArray(event.center_lonlat)) {
        const marker = L.circleMarker([event.center_lonlat[1], event.center_lonlat[0]], {
          radius: 8, color, weight: 2, fillColor: color, fillOpacity: 0.9,
        }).addTo(state.layers.events);
        marker.bindTooltip(`${event.priority} · ${event.title}`, { direction: "top" });
        marker.on("click", () => selectEvent(event.id));
      }
    });

    const bounds = [];
    state.data.resources.forEach((r) => bounds.push([r.lat, r.lon]));
    state.data.events.forEach((e) => {
      if (Array.isArray(e.center_lonlat)) bounds.push([e.center_lonlat[1], e.center_lonlat[0]]);
    });
    if (bounds.length) state.map.fitBounds(bounds, { padding: [18, 18], maxZoom: 11 });
  }

  function renderTabs() {
    document.querySelectorAll("[data-yt-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.ytTab === state.tab);
    });
  }

  function renderList() {
    panel.classList.remove("detail-open");
    renderTabs();
    const list = $("yantai-list");
    $("yantai-detail").hidden = true;
    list.hidden = false;
    $("yantai-detail").innerHTML = "";
    if (state.tab === "events") {
      const items = state.data.events;
      list.innerHTML = items.length ? items.map((e) => `
        <article class="yt-card" data-yt-event="${esc(e.id)}">
          <div class="yt-card-top">
            <span class="yt-pri ${esc(e.priority)}">${esc(e.priority)} · ${esc(PRIORITY[e.priority] || "")}</span>
            <span class="yt-card-title">${esc(e.title)}</span>
          </div>
          <div class="yt-card-sub">${esc(e.subtitle)}</div>
          <div class="yt-card-row">
            <span class="yt-chip">${esc(e.activity_status)}</span>
            <span class="yt-chip warn">${esc(e.compliance_status)}</span>
            <span class="yt-chip">${esc(e.lifecycle)}</span>
          </div>
        </article>`).join("") : `<div class="yt-empty">尚无已入库事件</div>`;
    } else if (state.tab === "resources") {
      const items = state.data.resources;
      list.innerHTML = items.length ? items.map((r) => `
        <article class="yt-card" data-yt-resource="${esc(r.id)}">
          <div class="yt-card-top">
            <span class="yt-pri ${r.type === "quarry_rs" ? "P2" : "P3"}">${r.type === "quarry_rs" ? "遥感点" : "采石场"}</span>
            <span class="yt-card-title">${esc(r.name)}</span>
          </div>
          <div class="yt-card-sub">${coords([r.lon, r.lat])} · ${esc(r.accuracy)}</div>
          <div class="yt-card-row"><span class="yt-chip warn">${esc(r.status)}</span><span class="yt-chip">${esc(r.source)}</span></div>
        </article>`).join("") : `<div class="yt-empty">尚无资源点</div>`;
    } else {
      const items = state.data.rivers;
      list.innerHTML = items.length ? items.map((r) => `
        <article class="yt-card" data-yt-river="${esc(r.id)}">
          <div class="yt-card-top">
            <span class="yt-pri Q">河段</span>
            <span class="yt-card-title">${esc(r.name)}</span>
          </div>
          <div class="yt-card-sub">${fmt(r.length_km)} km · ${esc(r.source)}</div>
          <div class="yt-card-row"><span class="yt-chip warn">${esc(r.status)}</span></div>
        </article>`).join("") : `<div class="yt-empty">尚无河段</div>`;
    }
  }

  function showDetail(html) {
    panel.classList.add("detail-open");
    $("yantai-list").hidden = true;
    const detail = $("yantai-detail");
    detail.hidden = false;
    detail.innerHTML = `<div class="yt-detail-head"><button class="yt-back" id="yt-back">← 返回</button></div>${html}`;
    $("yt-back").onclick = () => renderList();
    detail.scrollTop = 0;
  }

  function eventFacts(e) {
    return `
      <div class="yt-facts">
        <div class="yt-fact"><span>活动判断</span><b>${esc(e.activity_status)}</b></div>
        <div class="yt-fact"><span>合规判断</span><b>${esc(e.compliance_status)}</b></div>
        <div class="yt-fact"><span>优先级</span><b>${esc(e.priority)} · ${esc(PRIORITY[e.priority] || "")}</b></div>
        <div class="yt-fact"><span>处置状态</span><b>${esc(e.lifecycle)}</b></div>
        <div class="yt-fact"><span>面积</span><b>${e.area_ha == null ? "未取得" : `${fmt(e.area_ha, 2)} 公顷`}</b></div>
        <div class="yt-fact"><span>影像时相</span><b>${esc(e.window)}</b></div>
        <div class="yt-fact"><span>中心坐标</span><b>${coords(e.center_lonlat)}</b></div>
        <div class="yt-fact"><span>置信度</span><b>${e.confidence == null ? "未取得" : fmt(e.confidence, 2)}</b></div>
      </div>`;
  }

  function showEvent(event) {
    state.selected = event;
    const imgs = event.images || {};
    const imageHtml = Object.keys(imgs).length ? `
      <div class="yt-images">
        ${imgs.before ? `<figure><img src="${esc(imgs.before)}" alt="基线影像"><figcaption>基线 · ${esc(event.baseline_at || "未取得")}</figcaption></figure>` : ""}
        ${imgs.after ? `<figure><img src="${esc(imgs.after)}" alt="当期影像"><figcaption>当期 · ${esc(event.observed_at || "未取得")}</figcaption></figure>` : ""}
        ${imgs.overlay ? `<figure class="yt-single"><img src="${esc(imgs.overlay)}" alt="检测叠加"><figcaption>检测叠加 · 候选框为算法结果，需人工复核</figcaption></figure>` : ""}
      </div>` : "";
    const review = event.review ? `
      <div class="yt-reviewed">已记录：${esc(event.review.decision)} · ${esc(event.review.reviewer)} · ${esc(event.review.reviewed_at)}
        ${event.review.note ? `<br>${esc(event.review.note)}` : ""}</div>` : "";
    showDetail(`
      <h3>${esc(event.title)}</h3>
      <p class="yt-detail-sub">${esc(event.id)} · ${esc(event.domain_label)} · ${esc(event.subtitle)}</p>
      ${eventFacts(event)}
      <div class="yt-note"><strong>触发规则：</strong>${esc(event.rule)}</div>
      <div class="yt-note"><strong>活动解释：</strong>${esc(event.activity_note)}</div>
      <div class="yt-warnbox"><strong>合规边界：</strong>${esc(event.compliance_note)}</div>
      ${imageHtml}
      <div class="yt-note"><strong>限制：</strong>${(event.limitations || []).map(esc).join("；")}</div>
      ${review}
      <div class="yt-note"><strong>研判记录（写入本地）：</strong></div>
      <div class="yt-review"><input id="yt-review-note" maxlength="1000" placeholder="填写依据，可留空"></div>
      <div class="yt-actions">
        ${["需补许可", "需高分复核", "需现场核查", "非采掘变化"].map((d) =>
          `<button data-yt-review="${esc(event.id)}" data-decision="${esc(d)}">${esc(d)}</button>`).join("")}
      </div>`);
    document.querySelectorAll("[data-yt-review]").forEach((btn) => {
      btn.onclick = () => submitReview(btn.dataset.ytReview, btn.dataset.decision);
    });
    if (Array.isArray(event.center_lonlat) && state.map) {
      state.map.setView([event.center_lonlat[1], event.center_lonlat[0]], Math.max(state.map.getZoom(), 12));
    }
  }

  function showResource(res) {
    state.selected = res;
    showDetail(`
      <h3>${esc(res.name)}</h3>
      <p class="yt-detail-sub">${esc(res.type === "quarry_rs" ? "遥感识别点" : "OSM 采石场")} · ${esc(res.id)}</p>
      <div class="yt-facts">
        <div class="yt-fact"><span>位置</span><b>${coords([res.lon, res.lat])}</b></div>
        <div class="yt-fact"><span>状态</span><b>${esc(res.status)}</b></div>
        <div class="yt-fact"><span>来源</span><b>${esc(res.source)}</b></div>
        <div class="yt-fact"><span>资料日期</span><b>${esc(res.source_date)}</b></div>
      </div>
      <div class="yt-note"><strong>精度：</strong>${esc(res.accuracy)}</div>
      <div class="yt-warnbox">${esc(res.note)}</div>
      <div class="yt-note">资源点只表示位置背景，不代表采矿权、许可范围或违法状态。</div>`);
    if (state.map) state.map.setView([res.lat, res.lon], 13);
  }

  function showRiver(river) {
    state.selected = river;
    showDetail(`
      <h3>${esc(river.name)}</h3>
      <p class="yt-detail-sub">${esc(river.id)} · ${fmt(river.length_km)} km · ${esc(river.segments)} 段</p>
      <div class="yt-facts">
        <div class="yt-fact"><span>来源</span><b>${esc(river.source)}</b></div>
        <div class="yt-fact"><span>资料日期</span><b>${esc(river.source_date)}</b></div>
        <div class="yt-fact"><span>许可状态</span><b>${esc(river.status)}</b></div>
        <div class="yt-fact"><span>变化检测</span><b>未开展</b></div>
      </div>
      <div class="yt-warnbox">${esc(river.note)}</div>
      <div class="yt-note">没有许可、水文和有效影像时，不能判断是否存在采砂活动；没有告警不等于没有活动。</div>`);
    if (state.map && Array.isArray(river.coords) && river.coords.length) {
      state.map.fitBounds(river.coords, { padding: [20, 20], maxZoom: 12 });
    }
  }

  async function selectEvent(id) {
    try {
      const event = await api(`/api/yantai/events/${encodeURIComponent(id)}`);
      showEvent(event);
    } catch (err) { toast(err.message); }
  }

  async function submitReview(id, decision) {
    const note = ($("yt-review-note") || {}).value || "";
    try {
      const result = await api(`/api/yantai/events/${encodeURIComponent(id)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ decision, note }),
      });
      showEvent(result.event);
      toast("已记录研判，事件状态已更新");
      await loadAll(false);
    } catch (err) { toast(err.message); }
  }

  async function loadAll(renderMap) {
    const [overview, resources, rivers, events] = await Promise.all([
      api("/api/yantai/overview"),
      api("/api/yantai/resources"),
      api("/api/yantai/rivers"),
      api("/api/yantai/events"),
    ]);
    state.data = {
      overview,
      resources: resources.items || [],
      rivers: rivers.items || [],
      events: events.items || [],
    };
    state.loaded = true;
    renderStatus();
    renderList();
    if (renderMap !== false) drawLayers();
  }

  async function checkNow() {
    const btn = $("yantai-check");
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = "检查中…";
    try {
      const result = await api("/api/yantai/check", { method: "POST" });
      toast(result.note || "检查完成");
      await loadAll(false);
      $("yantai-foot").textContent = `检查 ${result.checked_at} · 新增 ${result.new_events || 0} 条`;
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "检查更新";
    }
  }

  function switchTab(tab) {
    state.tab = tab;
    renderList();
  }

  function openPanel() {
    if (state.open) return;
    state.open = true;
    panel.hidden = false;
    entry.classList.add("active");
    initMap();
    setTimeout(() => state.map && state.map.invalidateSize(), 60);
    const loading = $("yantai-list");
    loading.innerHTML = `<div class="yt-empty">正在加载烟台试点数据…</div>`;
    loadAll(true).catch((err) => {
      loading.innerHTML = `<div class="yt-empty">${esc(err.message)}</div>`;
    });
  }

  function closePanel() {
    state.open = false;
    panel.hidden = true;
    entry.classList.remove("active");
    try { sessionStorage.setItem("yt-dismissed", "1"); } catch (err) { /* 隐私模式下忽略 */ }
  }

  entry.addEventListener("click", () => (state.open ? closePanel() : openPanel()));
  $("yantai-close").addEventListener("click", closePanel);
  $("yantai-check").addEventListener("click", checkNow);
  document.querySelectorAll("[data-yt-tab]").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.ytTab));
  });
  $("yantai-list").addEventListener("click", (event) => {
    const card = event.target.closest("[data-yt-event], [data-yt-resource], [data-yt-river]");
    if (!card) return;
    if (card.dataset.ytEvent) selectEvent(card.dataset.ytEvent);
    else if (card.dataset.ytResource) {
      const item = state.data.resources.find((r) => r.id === card.dataset.ytResource);
      if (item) showResource(item);
    } else if (card.dataset.ytRiver) {
      const item = state.data.rivers.find((r) => r.id === card.dataset.ytRiver);
      if (item) showRiver(item);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.open) closePanel();
  });
  const forceOpen = new URLSearchParams(location.search).get("yantai") === "1";
  let dismissed = false;
  try { dismissed = sessionStorage.getItem("yt-dismissed") === "1"; } catch (err) { dismissed = false; }
  if (forceOpen || !dismissed) {
    setTimeout(openPanel, 500);
  }
  // 只刷新状态，不把本地检查说成实时卫星推送。
  setInterval(() => {
    if (state.open && state.loaded) {
      api("/api/yantai/overview").then((overview) => {
        state.data.overview = overview;
        renderStatus();
      }).catch(() => {});
    }
  }, 300000);
})();
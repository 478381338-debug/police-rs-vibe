(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const BOOT_STARTED = performance.now();
  const state = {
    data: null,
    event: null,
    events: [],
    user: null,
    map: null,
    layers: {},
    eventLayers: new Map(),
    split: 50,
    compareMode: "swipe",
    blinkTimer: null,
    filter: { domain: "", priority: "" },
    regions: [],
    region: null,
    regionId: null,
    regionError: null,
    focusPatches: [],
    selectedPatch: null,
    showAllLabels: false,
  };

  const DECISIONS = ["需补许可", "需高分复核", "需现场核查", "非采掘变化"];
  const PRIORITY_LABEL = { P1: "P1 一级", P2: "P2 二级", P3: "P3 三级", Q: "Q 资料缺口" };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function num(value, digits) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    return Number(value).toFixed(digits == null ? 2 : digits);
  }

  function pct(value) {
    if (value == null) return "—";
    return (Number(value) * 100).toFixed(1) + "%";
  }

  function toast(message) {
    const el = $("yw-toast");
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"), 3200);
  }

  function api(path, options) {
    const opt = Object.assign({ credentials: "same-origin", headers: {} }, options || {});
    opt.headers = Object.assign({ Accept: "application/json" }, opt.headers || {});
    if (opt.body && typeof opt.body !== "string") {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(opt.body);
    }
    return fetch(path, opt).then(async (res) => {
      let data = null;
      try { data = await res.json(); } catch (_) { /* ignore */ }
      if (!res.ok) {
        const msg = (data && (data.detail || data.message)) || (res.status === 401 ? "请先登录案件工作台" : "请求失败 " + res.status);
        const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
        err.status = res.status;
        throw err;
      }
      return data;
    });
  }

  function priorityClass(p) {
    return String(p || "Q").toLowerCase();
  }

  function stateTag(text, kind) {
    return '<span class="yw-tag ' + (kind || "") + '">' + esc(text) + "</span>";
  }

  function initMap() {
    const map = L.map("yw-map", {
      zoomControl: true,
      attributionControl: true,
      preferCanvas: true,
    }).setView([37.18, 120.32], 10);
    L.tileLayer("/api/tile/osm/{z}/{x}/{y}", {
      maxZoom: 18, maxNativeZoom: 17,
      errorTileUrl: "/static/assets/tile-fallback.png",
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);
    state.map = map;
    state.layers = {
      events: L.layerGroup().addTo(map),
      resources: L.layerGroup().addTo(map),
      rivers: L.layerGroup().addTo(map),
      sites: L.layerGroup().addTo(map),
    };
    $("yw-layer-events").addEventListener("change", (e) => toggleLayer("events", e.target.checked));
    $("yw-layer-resources").addEventListener("change", (e) => toggleLayer("resources", e.target.checked));
    $("yw-layer-rivers").addEventListener("change", (e) => toggleLayer("rivers", e.target.checked));
    $("yw-layer-sites").addEventListener("change", (e) => toggleLayer("sites", e.target.checked));
    $("yw-fit").addEventListener("click", fitAll);
  }

  function toggleLayer(name, visible) {
    if (!state.map || !state.layers[name]) return;
    if (visible) state.layers[name].addTo(state.map);
    else state.map.removeLayer(state.layers[name]);
  }

  function eventColor(priority) {
    if (priority === "P1") return "#b42318";
    if (priority === "P2") return "#b54708";
    if (priority === "P3") return "#175cd3";
    return "#667085";
  }

  function renderMap() {
    const map = state.map;
    if (!map) return;
    state.layers.events.clearLayers();
    state.layers.resources.clearLayers();
    state.layers.rivers.clearLayers();
    state.layers.sites.clearLayers();
    state.eventLayers.clear();

    const siteKeys = new Set();
    (state.data.analysis_sites || []).forEach((site) => {
      const b = site.bbox_wgs84;
      if (!b || b.length !== 4) return;
      const key = b.map((v) => Number(v).toFixed(3)).join(",");
      if (siteKeys.has(key)) return;
      siteKeys.add(key);
      const annual = !site.region_id;
      L.rectangle([[b[1], b[0]], [b[3], b[2]]], {
        color: annual ? "#7a5af8" : "#0f766e",
        weight: 1.5,
        fill: false,
        dashArray: annual ? "6 4" : "2 3",
      }).bindTooltip(site.place + " · " + site.method + " · " + (site.date_from || "?") + "→" + (site.date_to || "?"), { sticky: true })
        .addTo(state.layers.sites);
    });

    (state.data.rivers || []).forEach((river) => {
      const coords = (river.coords || []).map((p) => [p[0], p[1]]);
      if (coords.length > 1) {
        L.polyline(coords, { color: "#2e90fa", weight: 2, opacity: 0.7 })
          .bindTooltip(river.name + " · " + num(river.length_km, 2) + " km · OSM 中心线", { sticky: true })
          .addTo(state.layers.rivers);
      }
    });

    (state.data.resources || []).forEach((res) => {
      const icon = L.divIcon({
        className: "",
        html: '<span class="yw-res-icon ' + (res.id === "RS-Q-001" ? "rs" : "quarry") + '"></span>',
        iconSize: [10, 10], iconAnchor: [5, 5],
      });
      L.marker([res.lat, res.lon], { icon: icon })
        .bindPopup("<b>" + esc(res.name || res.id) + "</b><br>" + esc(res.type || "") + "<br>" + esc(res.status || "") + "<br><span style='color:#667085'>" + esc(res.note || "") + "</span>")
        .addTo(state.layers.resources);
    });

    (state.data.events || []).forEach((event) => {
      let layer = null;
      const color = eventColor(event.priority);
      if (event.geometry && event.geometry.type) {
        layer = L.geoJSON(event.geometry, {
          style: { color: color, weight: 2, fillColor: color, fillOpacity: 0.15, dashArray: event.priority === "Q" ? "4 3" : null },
        });
      } else if (event.center_lonlat) {
        const icon = L.divIcon({
          className: "",
          html: '<span class="yw-event-icon" style="background:' + color + '"></span>',
          iconSize: [18, 18], iconAnchor: [9, 9],
        });
        layer = L.marker([event.center_lonlat[1], event.center_lonlat[0]], { icon: icon });
      }
      if (!layer) return;
      layer.bindTooltip(event.title + " · " + PRIORITY_LABEL[event.priority], { sticky: true });
      layer.on("click", () => selectEvent(event.id, false));
      layer.addTo(state.layers.events);
      state.eventLayers.set(event.id, layer);
      if (event.center_lonlat) {
        const marker = L.circleMarker([event.center_lonlat[1], event.center_lonlat[0]], {
          radius: 6, color: "#fff", weight: 2, fillColor: color, fillOpacity: 0.95,
        }).addTo(state.layers.events);
        marker.bindTooltip(event.priority + " · " + event.title, { permanent: true, direction: "right", offset: [7, 0], className: "yw-map-label" });
        marker.on("click", () => selectEvent(event.id, false));
      }
    });
  }

  function fitAll() {
    if (!state.map) return;
    const bounds = L.latLngBounds([]);
    (state.data.events || []).forEach((event) => {
      if (event.bbox) bounds.extend([[event.bbox[1], event.bbox[0]], [event.bbox[3], event.bbox[2]]]);
      else if (event.center_lonlat) bounds.extend([event.center_lonlat[1], event.center_lonlat[0]]);
    });
    (state.data.resources || []).forEach((res) => bounds.extend([res.lat, res.lon]));
    (state.data.analysis_sites || []).forEach((site) => {
      const b = site.bbox_wgs84;
      if (b && b.length === 4) bounds.extend([[b[1], b[0]], [b[3], b[2]]]);
    });
    if (bounds.isValid()) state.map.fitBounds(bounds, { padding: [20, 20], maxZoom: 12 });
    else state.map.setView([37.18, 120.32], 9);
  }

  function selectEvent(id, fromMap, initial) {
    const event = state.events.find((e) => e.id === id);
    if (!event) return;
    state.event = event;
    document.querySelectorAll(".yw-event-card").forEach((el) => {
      el.classList.toggle("selected", el.dataset.id === id);
    });
    const layer = state.eventLayers.get(id);
    if (layer) {
      try {
        const maxZoom = initial ? 12 : 15;
        if (layer.getBounds) state.map.fitBounds(layer.getBounds(), { padding: [26, 26], maxZoom: maxZoom });
        else if (layer.getLatLng) state.map.setView(layer.getLatLng(), initial ? 12 : 14);
      } catch (_) { /* ignore */ }
    }
    renderDetail();
    if (event.region_id && state.regions.length) {
      setRegion(event.region_id, { focus: event.focus_patches || [] });
    } else if (state.regions.length && !state.regionId) {
      setRegion(state.regions[0].id);
    } else {
      state.focusPatches = [];
      if (state.region) renderRegionEvidence();
    }
    if (!fromMap && window.innerWidth <= 900) switchView("detail");
  }

  function renderQueue() {
    const list = $("yw-event-list");
    const domain = state.filter.domain;
    const priority = state.filter.priority;
    const events = (state.data.events || []).filter((e) =>
      (!domain || e.domain === domain) && (!priority || e.priority === priority));
    state.events = events;
    $("yw-event-count").textContent = String(events.length);
    $("yw-queue-sub").textContent = events.length + " 条 · 按业务优先级排序";
    if (!events.length) {
      list.innerHTML = '<p style="padding:12px;color:#667085;font-size:11px">当前筛选没有事件；不把空列表当作“无异常”。</p>';
      return;
    }
    list.innerHTML = events.map((event) => {
      const tags = [
        stateTag(event.activity_status || "—", event.activity_status === "无法判断" ? "gray" : "warn"),
        stateTag(event.compliance_status || "—", event.compliance_status === "许可待核" ? "warn" : "gray"),
        stateTag(event.lifecycle || "—", event.lifecycle === "已排除" ? "ok" : "gray"),
        event.evidence_tier ? stateTag("证据 " + event.evidence_tier, evidenceTierKind(event.evidence_tier)) : "",
      ].join("");
      return '<button type="button" class="yw-event-card ' + priorityClass(event.priority) + '" data-id="' + esc(event.id) + '">' +
        '<div class="yw-event-top"><span class="yw-priority ' + priorityClass(event.priority) + '">' + esc(event.priority) + '</span>' +
        '<span class="yw-tag">' + esc(event.domain_label || "") + '</span></div>' +
        '<h3>' + esc(event.title) + '</h3>' +
        '<p>' + esc(event.subtitle || "") + '</p>' +
        '<div class="yw-event-tags">' + tags + '</div></button>';
    }).join("");
    list.querySelectorAll(".yw-event-card").forEach((el) => {
      el.addEventListener("click", () => selectEvent(el.dataset.id, false));
    });
  }

  function renderCoverage() {
    const cov = state.data.overview.coverage || {};
    const bcov = state.data.overview.behavior_coverage || null;
    const gaps = (cov.gaps || []).map((g) =>
      '<span>' + esc(g.area) + "：" + esc(g.status) + "（" + esc(g.reason) + "）</span>").join("");
    $("yw-covered").textContent = num(cov.monitored_area_km2, 1) + " km²";
    $("yw-latest").textContent = cov.latest_observation || "—";
    const lifecycle = {};
    (state.data.events || []).forEach((e) => { lifecycle[e.lifecycle || "—"] = (lifecycle[e.lifecycle || "—"] || 0) + 1; });
    const disposition = Object.keys(lifecycle).map((k) => "<span>" + esc(k) + " <b>" + lifecycle[k] + "</b></span>").join("");
    $("yw-coverage").innerHTML =
      "<p><b>" + esc(cov.monitored_area || "") + "</b></p>" +
      "<p>有效覆盖 <b>" + num(cov.valid_area_km2, 1) + " km²</b>（" + pct(cov.valid_ratio) + "）；有效像元 " + (cov.valid_pixels || 0).toLocaleString() + "</p>" +
      "<p>最近有效观测 <b>" + esc(cov.latest_observation || "—") + "</b>，" + (cov.observation_age_days == null ? "年龄未知" : cov.observation_age_days + " 天前") + "；基线 " + esc(cov.baseline || "—") + "</p>" +
      "<p>数据台账 <b>" + ((state.data.catalog && state.data.catalog.scenes) || []).length + " 景</b> · 配准 " + esc(((state.data.catalog || {}).quality || {}).registration_error || "未评估") + " · 来源 STAC</p>" +
      "<p>规则候选 <b>" + (cov.rule_candidates || 43) + "</b> 个；异常像元面积 " + num(cov.rule_candidate_area_ha, 1) + " ha，图斑合计 " + (cov.patch_area_ha == null ? "—" : num(cov.patch_area_ha, 1)) + " ha；仅 2 条进入事件队列，未独立验证</p>" +
      (cov.scope_note ? '<p style="color:#b54708">' + esc(cov.scope_note) + "</p>" : "") +
      (cov.threshold_note ? '<p style="color:#b54708">' + esc(cov.threshold_note) + "</p>" : "") +
      (bcov ? "<p>行为类别覆盖 <b>" + bcov.runnable + " / " + bcov.total + "</b> 类可运行；其余未建模或无数据 <button type=\"button\" id=\"yw-plan-inline\" style=\"border:0;background:none;padding:0;color:#157f74;font:inherit;text-decoration:underline;cursor:pointer\">看分类型方法</button></p>" : "") +
      '<div class="yw-disp"><span style="color:#344054;font-weight:700">处置分布</span>' + disposition + "</div>" +
      '<div class="yw-gap"><span style="color:#344054;font-weight:700">观测与资料缺口</span>' + gaps + "</div>";
  }

  function daysBetween(from, to) {
    if (!from || !to) return null;
    const a = Date.parse(from + "T00:00:00Z");
    const b = Date.parse(to + "T00:00:00Z");
    if (Number.isNaN(a) || Number.isNaN(b)) return null;
    return Math.round((b - a) / 86400000);
  }

  function spanText(days) {
    if (days == null) return "间隔未知";
    return days + " 天 · " + (days / 365.25).toFixed(2) + " 年";
  }

  function renderObsSpan(items) {
    const el = $("yw-obs-span");
    if (!el) return;
    const obs = (items || []).filter((o) => o && o.acquired_at);
    if (obs.length < 2) {
      el.innerHTML = '<p class="yw-span-total">有效观测不足两期，算不出真实间隔；不填默认天数。</p>';
      return;
    }
    const segs = [];
    for (let i = 1; i < obs.length; i += 1) {
      const days = daysBetween(obs[i - 1].acquired_at, obs[i].acquired_at);
      if (days == null || days <= 0) continue;
      segs.push({ days, to: obs[i].acquired_at });
    }
    if (!segs.length) {
      el.innerHTML = '<p class="yw-span-total">观测日期无法解析，未计算间隔。</p>';
      return;
    }
    const first = obs[0].acquired_at;
    const last = obs[obs.length - 1].acquired_at;
    const total = daysBetween(first, last);
    const parts = ['<div class="yw-span-row"><span class="yw-span-node"><i></i>' + esc(first) + "</span>"];
    segs.forEach((s) => {
      parts.push('<span class="yw-span-seg" style="flex:' + s.days + '" title="' + esc(spanText(s.days)) + '"><b>' + esc(spanText(s.days)) + "</b></span>");
      parts.push('<span class="yw-span-node"><i></i>' + esc(s.to) + "</span>");
    });
    parts.push("</div>");
    parts.push('<p class="yw-span-total">共 <b>' + obs.length + "</b> 期有效观测，总跨度 <b>" + esc(spanText(total)) +
      "</b>；按真实观测日期逐段计算，下一期未排期时不推算天数。</p>");
    el.innerHTML = parts.join("");
  }

  function renderObservations() {
    const track = $("yw-obs-track");
    const matrix = $("yw-matrix");
    const span = $("yw-obs-span");
    const isRiver = state.event && state.event.domain === "river";
    matrix.hidden = !isRiver;
    if (isRiver) {
      if (span) span.innerHTML = "";
      const dates = ["2023-11-02", "2025-11-21", "2026-11-11"];
      const rows = (state.data.rivers || []).map((river) =>
        "<tr><th>" + esc(river.name) + "</th>" +
        dates.map(() => '<td class="gap" title="未接入逐景观测">缺测</td>').join("") + "</tr>").join("");
      $("yw-matrix-body").innerHTML =
        '<table class="yw-matrix-table"><thead><tr><th>河段</th>' +
        dates.map((d) => "<th>" + esc(d) + "</th>").join("") + "</tr></thead><tbody>" + rows + "</tbody></table>" +
        '<p class="yw-note">矩阵只表达观测有无；大沽夹河尚未接入许可、水位与逐景影像，因此全部为缺测，不作为“无采砂”。</p>';
      track.innerHTML = '<div class="yw-obs gap"><b>未建立有效观测</b><span>大沽夹河试点段</span><em>缺许可、水文与逐景影像；不计入采砂告警</em></div>' +
        '<div class="yw-obs gap"><b>缺水位/流量</b><span>无法排除洪水冲淤</span><em>资料待核</em></div>' +
        '<div class="yw-obs gap"><b>缺管理范围</b><span>OSM 中心线非法定边界</span><em>不生成违法结论</em></div>' +
        '<div class="yw-obs gap"><b>能力边界</b><span>10 m 光学</span><em>窄河段/小船不可靠</em></div>';
      return;
    }
    const items = state.data.observations || [];
    track.innerHTML = items.map((obs, i) => {
      const cls = i === 2 ? "valid" : (i === 1 ? "mid" : "valid");
      const tag = i === 0 ? " · 基线" : (i === 1 ? " · 严格阈值未检出" : " · 当前期");
      return '<div class="yw-obs ' + cls + '"><b>' + esc(obs.acquired_at) + tag + '</b>' +
        '<span>' + esc(obs.sensor) + " · " + esc(obs.status) + '</span>' +
        '<em>' + (obs.quality_note || "") + '</em></div>';
    }).join("");
    renderObsSpan(items);
  }

  function setCompareMode(mode) {
    state.compareMode = mode === "blink" ? "blink" : "swipe";
    document.querySelectorAll("[data-cmode]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.cmode === state.compareMode);
    });
    const box = $("yw-compare");
    if (!box) return;
    if (state.blinkTimer) { clearInterval(state.blinkTimer); state.blinkTimer = null; }
    box.classList.toggle("blink", state.compareMode === "blink");
    box.classList.remove("after-off");
    if (state.compareMode === "blink") {
      let on = true;
      state.blinkTimer = setInterval(() => {
        on = !on;
        box.classList.toggle("after-off", !on);
      }, 760);
    }
  }

  function setSplit(value) {
    state.split = Math.max(0, Math.min(100, Number(value)));
    const compare = $("yw-compare");
    compare.style.setProperty("--split", state.split + "%");
    $("yw-split").value = String(state.split);
    $("yw-split-handle").style.left = state.split + "%";
  }

  function signed(value, digits) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    const n = Number(value);
    return (n > 0 ? "+" : "") + n.toFixed(digits == null ? 3 : digits);
  }

  function isPriority(patch) {
    return Number(patch.rule_score) >= 0.7 || Number(patch.area_ha) >= 2;
  }

  function ringsToPath(rings) {
    return (rings || []).map((ring) => {
      if (!ring || !ring.length) return "";
      return "M" + ring.map((pt) => pt[0] + " " + pt[1]).join("L") + "Z";
    }).join("");
  }

  function clearPatchOverlay() {
    ["yw-patch-shapes", "yw-patch-leaders", "yw-patch-labels"].forEach((id) => {
      const el = $(id);
      if (el) el.innerHTML = "";
    });
  }

  function renderRegionOptions() {
    const sel = $("yw-region");
    if (!sel) return;
    if (!state.regions.length) {
      sel.innerHTML = '<option value="">无可用样区</option>';
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    sel.innerHTML = state.regions.map((region) => {
      const dates = region.dates || {};
      const range = dates.base && dates.now ? dates.base + "→" + dates.now : "日期未知";
      const span = region.span && region.span.days != null ? " · " + region.span.days + " 天" : "";
      return '<option value="' + esc(region.id) + '">' + esc(region.label) + " · " +
        esc(range) + span + "（" + (region.patch_count || 0) + " 图斑）</option>";
    }).join("");
    if (state.regionId) sel.value = state.regionId;
    sel.title = "只列出本地同时具备基线与当前影像的样区";
    renderSiteChips();
  }

  function renderSiteChips() {
    const box = $("yw-sites");
    if (!box) return;
    const sites = (state.data && state.data.analysis_sites) || [];
    if (!sites.length) { box.innerHTML = ""; return; }
    box.innerHTML = '<span class="yw-sites-label">已分析地点</span>' + sites.map((site) => {
      const active = site.region_id && site.region_id === state.regionId;
      const count = site.result_count == null ? "—" : site.result_count;
      const period = site.date_from && site.date_to
        ? site.date_from.slice(0, 7) + "→" + site.date_to.slice(0, 7)
        : "日期未知";
      const title = [site.place, site.role, site.method, period,
        site.span_days == null ? "间隔未知" : site.span_days + " 天",
        site.result_count + " 个候选",
        site.mine_candidate_count == null ? "" : "mine_candidate " + site.mine_candidate_count + " 个 / " + num(site.mine_candidate_area_ha, 2) + " ha",
        site.note || ""].filter(Boolean).join(" · ");
      return '<button type="button" class="yw-site-chip' + (active ? " active" : "") +
        (site.region_id ? "" : " annual") + '" data-site="' + esc(site.id) + '" title="' + esc(title) + '">' +
        esc(site.place) +
        (site.span_days == null ? "" : ' <span class="yw-chip-span">' + site.span_days + " 天</span>") +
        ' <b>' + esc(count) + "</b>" +
        (site.region_id ? "" : ' <i>年度</i>') + "</button>";
    }).join("");
    box.querySelectorAll(".yw-site-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        const site = sites.find((s) => s.id === btn.dataset.site);
        if (!site) return;
        if (site.region_id) {
          setRegion(site.region_id);
          return;
        }
        const open2025 = $("yw-2025-open");
        if (open2025) open2025.click();
        toast(site.place + " 为 2025 全年时序分析，没有成对真彩影像；已在“2025 全年分析”中打开候选。");
      });
    });
  }

  function setRegion(id, options) {
    const opts = options || {};
    let region = state.regions.find((r) => r.id === id);
    if (!region) region = state.regions[0];
    if (!region) return;
    state.region = region;
    state.regionId = region.id;
    state.focusPatches = (opts.focus || []).filter((pid) =>
      (region.patches || []).some((p) => p.id === pid));
    state.selectedPatch = null;
    state.showAllLabels = false;
    const sel = $("yw-region");
    if (sel) sel.value = region.id;
    const showAll = $("yw-show-all");
    if (showAll) showAll.checked = false;
    togglePatchPanel(false);
    hidePatchPopover();
    renderRegionEvidence();
    renderSiteChips();
  }

  function togglePatchPanel(force) {
    const panel = $("yw-patch-panel");
    const btn = $("yw-toggle-list");
    if (!panel || !btn) return;
    const show = force == null ? panel.hidden : !!force;
    panel.hidden = !show;
    btn.setAttribute("aria-expanded", show ? "true" : "false");
    if (show) updatePatchRows();
  }

  function renderRegionEvidence() {
    const region = state.region;
    const compare = $("yw-compare");
    if (!region) {
      compare.classList.add("no-image");
      $("yw-img-after").removeAttribute("src");
      $("yw-img-before").removeAttribute("src");
      $("yw-patch-summary").textContent = "";
      $("yw-img-meta").innerHTML = '<span class="yw-caveat">样区影像未加载；缺测不显示“0 异常”。</span>';
      const empty = $("yw-patch-empty");
      if (empty) { empty.hidden = true; empty.textContent = ""; }
      clearPatchOverlay();
      renderPatchPanel(null);
      return;
    }
    const before = $("yw-img-before"), after = $("yw-img-after");
    if (region.images && region.images.before && region.images.after) {
      compare.classList.remove("no-image");
      before.src = region.images.before;
      after.src = region.images.after;
      before.onload = layoutCompare;
      after.onload = layoutCompare;
    } else {
      compare.classList.add("no-image");
      before.removeAttribute("src");
      after.removeAttribute("src");
    }
    const dates = region.dates || {};
    $("yw-tag-before").textContent = "基线 " + (dates.base || "日期未知");
    $("yw-tag-after").textContent = "当前 " + (dates.now || "日期未知");
    $("yw-tag-before").title = "影像日期 " + (dates.base || "未知") + " · " + (region.resolution_m || 10) + " m";
    $("yw-tag-after").title = "影像日期 " + (dates.now || "未知") + " · " + (region.resolution_m || 10) + " m";
    const patchCount = region.patch_count || 0;
    const empty = $("yw-patch-empty");
    if (empty) {
      empty.hidden = patchCount > 0;
      empty.textContent = patchCount > 0 ? "" :
        (region.zero_note || "本次规则阈值下 0 个规则候选；0 不等于无采矿。");
    }
    $("yw-patch-summary").innerHTML = patchCount
      ? "<b>" + patchCount + "</b> 个规则候选 / " + num(region.patch_area_ha, 1) + " ha"
      : "<b>0</b> 个规则候选";
    const toggle = $("yw-toggle-list");
    toggle.textContent = "图斑清单 " + patchCount;
    toggle.disabled = patchCount === 0;
    renderPatchOverlay(region);
    renderPatchPanel(region);
    const event = state.event;
    let eventNote = "";
    if (event && !event.region_id) {
      eventNote = "当前事件没有可用影像对；此处为样区影像浏览，不代表该事件。";
    } else if (event && event.region_id && event.region_id !== region.id) {
      eventNote = "当前事件影像不属于该样区；此处为样区影像浏览。";
    } else if (state.focusPatches.length) {
      eventNote = "已关联事件图斑：" + state.focusPatches.join("、") + "。";
    }
    $("yw-img-meta").innerHTML =
      "<span>范围：" + esc(region.label) + " · " + region.width + "×" + region.height +
      " 像元 / " + num(region.window_km2, 1) + " km²</span>" +
      "<span>观测间隔：" + (region.span && region.span.days != null
        ? region.span.days + " 天（" + num(region.span.years, 2) + " 年）"
        : "日期未知") + "</span>" +
      "<span>分辨率：" + (region.resolution_m || 10) + " m（SWIR 原生 " + (region.swir_m || 20) + " m）</span>" +
      "<span>有效像元：" + pct(region.valid_ratio) + "（" + Number(region.valid_pixels || 0).toLocaleString() + "）</span>" +
      "<span>图斑合计：" + patchCount + " 个 / " + num(region.patch_area_ha, 1) + " ha</span>" +
      "<span>配准：未评估</span>" +
      '<span class="yw-caveat">' + esc([eventNote, region.zero_note, region.caveat,
        "规则评分未校准，不是违法认定。"].filter(Boolean).join(" ")) + "</span>";
    requestAnimationFrame(layoutCompare);
    setSplit(state.split);
    setCompareMode(state.compareMode);
  }

  function renderPatchOverlay(region) {
    renderPatchShapes(region);
    renderPatchLabels(region);
  }

  function renderPatchShapes(region) {
    const g = $("yw-patch-shapes");
    if (!g) return;
    g.innerHTML = "";
    if (!region || !region.patches) return;
    region.patches.forEach((patch) => {
      if (!patch.rings || !patch.rings.length) return;
      const focus = state.focusPatches.includes(patch.id);
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", ringsToPath(patch.rings));
      path.setAttribute("fill-rule", "evenodd");
      path.setAttribute("class", "yw-patch" +
        (isPriority(patch) ? " priority" : "") +
        (focus ? " focus" : "") +
        (state.selectedPatch === patch.id ? " selected" : ""));
      path.addEventListener("click", (e) => { e.stopPropagation(); togglePatch(patch.id); });
      g.appendChild(path);
      if (patch.center_norm) {
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("cx", patch.center_norm[0]);
        dot.setAttribute("cy", patch.center_norm[1]);
        dot.setAttribute("r", "0.0045");
        dot.setAttribute("class", "yw-patch-dot" + (isPriority(patch) ? " priority" : ""));
        g.appendChild(dot);
        if (focus) {
          const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          ring.setAttribute("cx", patch.center_norm[0]);
          ring.setAttribute("cy", patch.center_norm[1]);
          ring.setAttribute("r", "0.018");
          ring.setAttribute("class", "yw-patch-focus-ring");
          g.appendChild(ring);
        }
      }
    });
  }

  function renderPatchLabels(region) {
    const layer = $("yw-patch-labels");
    const leaders = $("yw-patch-leaders");
    const stage = $("yw-compare-stage");
    if (!layer || !leaders || !stage) return;
    layer.innerHTML = "";
    leaders.innerHTML = "";
    if (!region || !region.patches || !region.patches.length) return;
    const w = stage.clientWidth;
    const h = stage.clientHeight;
    if (!w || !h) return;
    const items = region.patches.filter((p) =>
      state.showAllLabels || isPriority(p) || state.focusPatches.includes(p.id) ||
      state.selectedPatch === p.id);
    const placed = [];
    const candidates = [
      [10, -9], [10, 9], [-10, -9], [-10, 9],
      [0, -16], [0, 16], [26, 0], [-26, 0],
      [26, -18], [-26, -18], [26, 18], [-26, 18],
      [42, -9], [-42, -9], [42, 9], [-42, 9],
      [0, -30], [0, 30],
    ];
    const overlaps = (a, b) =>
      !(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y);
    items.forEach((patch) => {
      if (!patch.center_norm) return;
      const label = document.createElement("button");
      label.type = "button";
      label.className = "yw-patch-label" +
        (isPriority(patch) ? " priority" : "") +
        (state.focusPatches.includes(patch.id) ? " focus" : "") +
        (state.selectedPatch === patch.id ? " selected" : "");
      label.textContent = patch.id;
      label.dataset.pid = patch.id;
      label.addEventListener("click", (e) => { e.stopPropagation(); togglePatch(patch.id); });
      layer.appendChild(label);
      const lw = label.offsetWidth || 44;
      const lh = label.offsetHeight || 15;
      const cx = patch.center_norm[0] * w;
      const cy = patch.center_norm[1] * h;
      const top = 22;
      const bottom = h - 30 - lh;
      let box = null;
      for (let i = 0; i < candidates.length; i += 1) {
        const dx = candidates[i][0];
        const dy = candidates[i][1];
        const x = Math.max(2, Math.min(cx + dx - (dx < 0 ? lw : 0), w - lw - 2));
        const y = Math.max(top, Math.min(cy + dy - lh / 2, bottom));
        const candidate = { x, y, w: lw, h: lh };
        if (!placed.some((b) => overlaps(b, candidate))) { box = candidate; break; }
      }
      if (!box) {
        for (let row = 0; row < 24 && !box; row += 1) {
          const x = Math.max(2, Math.min(w - lw - 2, w - lw - 6));
          const y = Math.min(bottom, top + row * (lh + 2));
          const candidate = { x, y, w: lw, h: lh };
          if (!placed.some((b) => overlaps(b, candidate))) box = candidate;
        }
      }
      if (!box) {
        for (let row = 0; row < 24 && !box; row += 1) {
          const x = Math.max(2, w - lw * 2 - 12);
          const y = Math.min(bottom, top + row * (lh + 2));
          const candidate = { x, y, w: lw, h: lh };
          if (!placed.some((b) => overlaps(b, candidate))) box = candidate;
        }
      }
      if (!box) box = { x: Math.max(2, w - lw - 2), y: Math.max(top, Math.min(bottom, cy - lh / 2)), w: lw, h: lh };
      placed.push(box);
      label.style.left = box.x + "px";
      label.style.top = box.y + "px";
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", patch.center_norm[0]);
      line.setAttribute("y1", patch.center_norm[1]);
      line.setAttribute("x2", (box.x + lw / 2) / w);
      line.setAttribute("y2", (box.y + lh / 2) / h);
      line.setAttribute("class", "yw-leader");
      leaders.appendChild(line);
    });
  }

  function renderPatchPanel(region) {
    const rows = $("yw-patch-rows");
    const title = $("yw-patch-panel-title");
    const legend = $("yw-patch-legend");
    const showAll = $("yw-show-all");
    if (!rows || !title || !legend) return;
    const patches = (region && region.patches) || [];
    title.textContent = "图斑清单" + (patches.length ? " · " + patches.length : "");
    if (showAll) {
      showAll.checked = state.showAllLabels;
      showAll.disabled = !patches.length;
    }
    if (!patches.length) {
      legend.innerHTML = "";
      rows.innerHTML = '<p class="yw-patch-empty">' + esc(
        (region && region.zero_note) ||
        "本次规则阈值下 0 个规则候选；0 不等于无采矿，未接入的观测不能当作排除依据。") + "</p>";
      return;
    }
    legend.innerHTML = '<span><i style="background:#ff6b3d"></i>优先查看（评分≥0.70 或 ≥2 ha）</span>' +
      '<span><i style="background:#ffd166"></i>其他规则候选</span>';
    rows.innerHTML = patches.map((patch) => {
      const focus = state.focusPatches.includes(patch.id);
      return '<button type="button" class="yw-patch-row' +
        (isPriority(patch) ? " priority" : "") +
        (focus ? " focus" : "") +
        (state.selectedPatch === patch.id ? " selected" : "") +
        '" data-pid="' + esc(patch.id) + '">' +
        "<b>" + esc(patch.id) + (focus ? ' <i class="yw-focus-tag">事件</i>' : "") + "</b>" +
        "<span>" + num(patch.area_ha, 2) + " ha · 评分 " + (patch.rule_score == null ? "—" : num(patch.rule_score, 2)) + "</span>" +
        '<em>' + (patch.center_lonlat ? patch.center_lonlat.map((v) => num(v, 5)).join(", ") : "中心坐标缺失") + "</em>" +
        "</button>";
    }).join("");
    rows.querySelectorAll(".yw-patch-row").forEach((row) => {
      row.addEventListener("click", () => togglePatch(row.dataset.pid));
    });
  }

  function togglePatch(id) {
    const region = state.region;
    if (!region) return;
    const patch = (region.patches || []).find((p) => p.id === id);
    if (!patch) return;
    state.selectedPatch = state.selectedPatch === id ? null : id;
    renderPatchShapes(region);
    renderPatchLabels(region);
    updatePatchRows();
    if (state.selectedPatch) showPatchPopover(id);
    else hidePatchPopover();
  }

  function updatePatchRows() {
    const rows = $("yw-patch-rows");
    if (!rows) return;
    rows.querySelectorAll(".yw-patch-row").forEach((row) => {
      row.classList.toggle("selected", row.dataset.pid === state.selectedPatch);
    });
    const active = rows.querySelector(".yw-patch-row.selected");
    if (active) {
      try { active.scrollIntoView({ block: "nearest" }); } catch (_) { /* ignore */ }
    }
  }

  function showPatchPopover(id) {
    const region = state.region;
    const patch = region && (region.patches || []).find((p) => p.id === id);
    const pop = $("yw-patch-pop");
    if (!patch || !pop || !patch.center_norm) { hidePatchPopover(); return; }
    pop.hidden = false;
    pop.innerHTML =
      '<div class="yw-pop-head"><b>' + esc(patch.id) + " · " + num(patch.area_ha, 2) + ' ha</b>' +
      '<button type="button" class="yw-pop-close" id="yw-pop-close" aria-label="关闭">×</button></div>' +
      "<dl>" +
      "<dt>中心</dt><dd>" + (patch.center_lonlat ? patch.center_lonlat.map((v) => num(v, 5)).join(", ") : "—") + "</dd>" +
      "<dt>dBSI</dt><dd>" + signed(patch.d_bsi, 3) + "</dd>" +
      "<dt>dNDVI</dt><dd>" + signed(patch.d_ndvi, 3) + "</dd>" +
      "<dt>规则评分</dt><dd>" + (patch.rule_score == null ? "—" : num(patch.rule_score, 2) + "（未校准）") + "</dd>" +
      "</dl>" +
      "<p>疑似地表扰动图斑，仅用于排查；不是违法认定，也不能仅凭 10 m 影像区分合法采掘、修复或施工。</p>";
    const close = $("yw-pop-close");
    if (close) close.addEventListener("click", (e) => { e.stopPropagation(); togglePatch(id); });
    positionPatchPopover(patch);
  }

  function positionPatchPopover(patch) {
    const compare = $("yw-compare");
    const stage = $("yw-compare-stage");
    const pop = $("yw-patch-pop");
    const panel = $("yw-patch-panel");
    if (!compare || !stage || !pop || !patch || !patch.center_norm) return;
    const cr = compare.getBoundingClientRect();
    const sr = stage.getBoundingClientRect();
    const cw = cr.width;
    const ch = cr.height;
    const iw = sr.width;
    const ih = sr.height;
    const left = sr.left - cr.left;
    const top = sr.top - cr.top;
    const pw = pop.offsetWidth;
    const ph = pop.offsetHeight;
    if (!cw || !ch || !pw) return;
    const cx = left + patch.center_norm[0] * iw;
    const cy = top + patch.center_norm[1] * ih;
    const gap = 8;
    const rightRoom = cw - (left + iw);
    const leftRoom = left;
    const panelOpen = panel && !panel.hidden;
    let x = null;
    if (!panelOpen && rightRoom >= pw + gap) x = left + iw + gap;
    else if (leftRoom >= pw + gap) x = left - pw - gap;
    else if (rightRoom >= pw + gap) x = left + iw + gap;
    if (x == null) {
      x = cx + 12;
      if (x + pw > cw - 6) x = cx - pw - 12;
      x = Math.max(6, Math.min(x, cw - pw - 6));
    }
    const y = Math.max(6, Math.min(cy - ph / 2, ch - ph - 34));
    pop.style.left = Math.round(x) + "px";
    pop.style.top = Math.round(y) + "px";
  }

  function hidePatchPopover() {
    const pop = $("yw-patch-pop");
    if (!pop) return;
    pop.hidden = true;
    pop.innerHTML = "";
  }

  function layoutCompare() {
    const compare = $("yw-compare");
    const stage = $("yw-compare-stage");
    const after = $("yw-img-after");
    const region = state.region;
    if (!compare || !stage) return;
    const nw = (after && after.naturalWidth) || (region && region.width) || 549;
    const nh = (after && after.naturalHeight) || (region && region.height) || 571;
    const cw = compare.clientWidth;
    const ch = compare.clientHeight;
    if (!cw || !ch) return;
    const scale = Math.min(cw / nw, ch / nh);
    const w = Math.max(1, Math.round(nw * scale));
    const h = Math.max(1, Math.round(nh * scale));
    stage.style.width = w + "px";
    stage.style.height = h + "px";
    stage.style.left = Math.round((cw - w) / 2) + "px";
    stage.style.top = Math.round((ch - h) / 2) + "px";
    renderPatchLabels(region);
    if (state.selectedPatch && region) {
      const patch = (region.patches || []).find((p) => p.id === state.selectedPatch);
      if (patch) positionPatchPopover(patch);
    }
  }

  const EVIDENCE_FAMILIES = [
    ["O", "光学", "光学变化"], ["S", "结构", "结构与设施"], ["L", "物流", "物流与目标"],
    ["D", "形变", "形变/SAR"], ["T", "夜间", "时序/夜间"], ["C", "合规", "合规与行政"],
    ["H", "人工", "人工核验"],
  ];

  // 情景权重镜像自 config_yantai_evidence.yaml；改配置时这里要同步。
  const SCENARIO_WEIGHTS = {
    S1: { O: 0.30, S: 0.30, L: 0.10, D: 0.05, T: 0.03, C: 0.17, H: 0.05 },
    S2: { O: 0.10, S: 0.25, L: 0.05, D: 0.30, T: 0.05, C: 0.20, H: 0.05 },
    S3: { O: 0.10, S: 0.45, L: 0.05, D: 0.00, T: 0.02, C: 0.28, H: 0.10 },
    S4: { O: 0.05, S: 0.15, L: 0.40, D: 0.00, T: 0.05, C: 0.25, H: 0.10 },
    S5: { O: 0.25, S: 0.25, L: 0.10, D: 0.00, T: 0.03, C: 0.27, H: 0.10 },
    S6: { O: 0.15, S: 0.25, L: 0.05, D: 0.25, T: 0.02, C: 0.18, H: 0.10 },
    S7: { O: 0.20, S: 0.25, L: 0.10, D: 0.10, T: 0.05, C: 0.20, H: 0.10 },
  };

  const FACTOR_FAMILY = {
    F01: "O", F02: "S", F03: "S", F04: "S", F05: "S", F06: "S", F07: "L", F08: "L",
    F09: "T", F10: "D", F11: "O", F12: "O", F13: "C", F14: "C", F15: "C", F16: "C",
    F17: "H", F18: "L",
  };

  function evidenceTierKind(tier) {
    if (tier === "A") return "ok";
    if (tier === "B") return "warn";
    return "gray";
  }

  function familyBarClass(value) {
    if (value >= 0.67) return "is-strong";
    if (value >= 0.33) return "is-mid";
    if (value > 0) return "is-low";
    return "is-missing";
  }

  // "N01 物候解释（0.33）" → {id, name, strength}
  function parseCounterToken(text) {
    const m = String(text || "").match(/^(N\d{2})\s*(.+?)（([\d.]+)）\s*$/);
    if (!m) return null;
    return { id: m[1], name: m[2], strength: Number(m[3]) };
  }

  function scenarioWeights(event) {
    const id = (event.scenario && event.scenario.primary) || "S1";
    return SCENARIO_WEIGHTS[id] || SCENARIO_WEIGHTS.S1;
  }

  function renderFamilyMatrix(corr) {
    const scores = corr.family_scores || {};
    const weights = scenarioWeights(state.event || {});
    const rows = EVIDENCE_FAMILIES.map(([id, short, full]) => {
      const value = Number(scores[id] || 0);
      const cls = familyBarClass(value);
      const pctWidth = Math.max(0, Math.min(100, value * 100)).toFixed(1);
      const w = weights[id] == null ? null : Number(weights[id]);
      return '<div class="yw-mx-row ' + cls + '" title="' + esc(id + " " + full + " · 得分 " + value.toFixed(2) + (w == null ? "" : " · 情景权重 " + w.toFixed(2))) + '">' +
        '<span class="yw-mx-k">' + esc(id) + "</span>" +
        '<span class="yw-mx-name">' + esc(short) + "</span>" +
        '<span class="yw-mx-track">' + (value > 0
          ? '<i style="width:' + pctWidth + '%"></i>'
          : '<i class="yw-mx-none">缺</i>') + "</span>" +
        '<b class="yw-mx-v">' + (value > 0 ? num(value, 2) : "—") + "</b>" +
        '<span class="yw-mx-w">' + (w == null ? "—" : w.toFixed(2)) + "</span>" +
        "</div>";
    }).join("");
    return '<div class="yw-mx"><div class="yw-mx-head"><span>族</span><span></span><span>归一化证据</span><span>分</span><span>权重</span></div>' +
      rows + "</div>";
  }

  function renderCompeting(event, corr) {
    const primary = event.scenario || {};
    const scores = corr.family_scores || {};
    const topFamily = Math.max.apply(null, Object.keys(scores).map((k) => Number(scores[k] || 0)).concat([0]));
    const supportN = EVIDENCE_FAMILIES.filter(([id]) => Number(scores[id] || 0) > 0).length;
    const alts = [];
    (corr.vetoes || []).forEach((v) => {
      alts.push({ id: v.id, name: v.name, strength: Number(v.strength || 0), note: v.note || "", veto: true });
    });
    (corr.top_counter || []).forEach((token) => {
      const parsed = parseCounterToken(token);
      if (!parsed) {
        alts.push({ id: "", name: String(token), strength: null, note: "", veto: false });
        return;
      }
      if (!alts.some((a) => a.id === parsed.id)) {
        alts.push({ id: parsed.id, name: parsed.name, strength: parsed.strength, note: "", veto: false });
      }
    });
    const vetoOn = alts.some((a) => a.veto && a.strength >= 1);
    const gate = corr.quality_gate;

    const primaryState = gate ? "未成立 · 无观测" : (vetoOn ? "被否决 · 保留为负样本" : "待核 · 不构成认定");
    const rows = [];
    rows.push('<div class="yw-alt-row is-primary">' +
      '<div class="yw-alt-name"><span class="yw-alt-tag">本假设</span>' + esc((primary.primary || "") + " " + (primary.label || "")) + "</div>" +
      '<div class="yw-alt-meter"><i style="width:' + Math.min(100, topFamily * 100).toFixed(0) + '%"></i></div>' +
      '<div class="yw-alt-state">' + esc(primaryState) + "</div>" +
      '<div class="yw-alt-sub">' + (gate ? "质量门 " + esc(gate.id || "") + " " + esc(gate.name || "") : supportN + " 族有信号 · 最高 " + num(topFamily, 2)) + "</div>" +
      "</div>");

    alts.forEach((a) => {
      const isVeto = a.veto;
      const state = a.strength == null ? "待核" : (a.strength >= 1 ? "优先按此解释" : a.strength >= 0.67 ? "已降级" : "未排除");
      rows.push('<div class="yw-alt-row ' + (isVeto ? "is-veto" : "") + '">' +
        '<div class="yw-alt-name"><span class="yw-alt-tag ' + (isVeto ? "warn" : "") + '">竞争</span>' +
        (a.id ? esc(a.id) + " " : "") + esc(a.name) + "</div>" +
        '<div class="yw-alt-meter"><i style="width:' + (a.strength == null ? 0 : Math.min(100, a.strength * 100).toFixed(0)) + '%"></i></div>' +
        '<div class="yw-alt-state">' + esc(state) + "</div>" +
        '<div class="yw-alt-sub">' + (a.strength == null ? "未量化，按反向证据处理" : "反证强度 " + num(a.strength, 2)) + "</div>" +
        "</div>");
    });

    // 未量化的反向证据：只保留与“活动解释”直接相关的句子
    const plain = (event.counter_evidence || []).filter((x) => !/^N\d{2}\s/.test(String(x))).slice(0, 4);
    const plainHtml = plain.length
      ? '<ul class="yw-alt-plain">' + plain.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul>"
      : "";
    return '<div class="yw-alt">' + rows.join("") + "</div>" + plainHtml;
  }

  function renderGates(event) {
    const items = [
      ["合规边界", event.compliance_status || "待核", "unknown",
        event.compliance_note || "未取得许可与边界图层，标记待核。"],
      ["时间语义", event.window || "未建立", "warn",
        event.time_note || "影像日期不是活动起始时间。"],
      ["统计口径", event.score_label || "未校准", "",
        (event.score_note || "") + (event.area_ha == null ? "" : " 面积 " + num(event.area_ha) + " ha 为变化像元聚类面积，不是测绘面积。")],
    ];
    return '<div class="yw-gates">' + items.map(([k, tag, cls, note]) =>
      '<div class="yw-gate ' + (cls || "") + '"><div class="yw-gate-top"><span>' + esc(k) +
      '</span><b class="' + esc(cls) + '">' + esc(tag) + "</b></div><p>" + esc(note) + "</p></div>"
    ).join("") + "</div>";
  }

  function renderNextSteps(event, corr) {
    const checks = event.next_checks || [];
    const missing = (corr.missing_notes && corr.missing_notes.length ? corr.missing_notes : event.missing || []);
    const groups = {};
    missing.forEach((text) => {
      const m = String(text).match(/^(F\d{2})\s*(.*)$/);
      if (!m) return;
      const fam = FACTOR_FAMILY[m[1]] || "?";
      (groups[fam] = groups[fam] || []).push(m[1] + " " + (m[2] || ""));
    });
    const chips = Object.keys(groups).map((fam) => {
      const label = (EVIDENCE_FAMILIES.find(([id]) => id === fam) || [fam, fam])[1];
      return '<span class="yw-gap-chip"><b>' + esc(fam) + " " + esc(label) + "</b>" +
        groups[fam].map((x) => "<em>" + esc(x) + "</em>").join("") + "</span>";
    }).join("");
    const actions = checks.length ? checks : (event.recommended_actions || []);
    return '<div class="yw-gap-groups">' + (chips || '<span class="yw-gap-chip"><b>关键因子已填</b><em>仍以现场核查为准</em></span>') + "</div>" +
      '<ol class="yw-next">' + actions.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ol>";
  }

  function renderGeoBlock(event) {
    const isRiver = event.domain === "river";
    const region = isRiver ? "大沽夹河试点段" : (event.region_id === "p07_2023b" ? "P07 样区" : "样区");
    return '<div class="yw-block yw-geo">' +
      '<div class="yw-geo-head"><div><h3>地质结构三维</h3>' +
      '<p>512 网格真实 DEM · 卫星正射贴地 · 五层岩性剖切（垂向放大按地区自适应）</p></div>' +
      '<div class="yw-geo-btns">' +
      '<button type="button" class="yw-geo-btn" data-geo-expand>放大</button>' +
      "</div></div>" +
      '<div class="yw-geo-stage is-loading" id="yw-geo-stage">' +
      '<div class="yw-geo-hud"><span class="yw-geo-dot"></span><span data-geo-hud>' + esc(region) + " · 载入中</span></div>" +
      '<div class="yw-geo-sites" data-geo-sites></div>' +
      '<div class="yw-geo-labels" data-geo-labels></div>' +
      '<div class="yw-geo-ctrl">' +
      '<button type="button" class="yw-geo-btn is-on" data-geo-preset="oblique">剖视</button>' +
      '<button type="button" class="yw-geo-btn" data-geo-preset="top">俯视</button>' +
      '<button type="button" class="yw-geo-btn" data-geo-spin>自转</button>' +
      "</div>" +
      '<div class="yw-geo-imagery" data-geo-imagery></div>' +
      '<div class="yw-geo-north">N</div>' +
      '<div class="yw-geo-tip" data-geo-tip></div>' +
      '<div class="yw-geo-loading">正在载入真实地形与影像…</div>' +
      '<button type="button" class="yw-geo-close" data-geo-close aria-label="关闭放大">×</button>' +
      '<div class="yw-geo-hint" title="拖拽旋转 · 滚轮缩放 · 双击复位">拖拽旋转 · 滚轮缩放</div>' +
      "</div>" +
      '<div class="yw-geo-legend" data-geo-legend>' +
      '<span><i style="background:#5c4030"></i>第四系</span>' +
      '<span><i style="background:#9a7a52"></i>强风化</span>' +
      '<span><i style="background:#7f838d"></i>花岗岩</span>' +
      '<span><i style="background:#6a5d68"></i>破碎带</span>' +
      '<span><i style="background:#ffb020"></i>' + (isRiver ? "缺测范围" : "变化图斑") + "</span>" +
      "</div></div>";
  }

  function renderCorroboration(event) {
    if (event.evidence_error) {
      return '<div class="yw-block"><h3>多维佐证</h3><p style="color:#b54708">' +
        esc(event.evidence_error) + "</p></div>";
    }
    const corr = event.corroboration;
    if (!corr) return "";
    const scores = corr.family_scores || {};
    const active = EVIDENCE_FAMILIES.filter(([id]) => Number(scores[id] || 0) > 0).length;
    const direct = EVIDENCE_FAMILIES.filter(([id]) =>
      ["O", "S", "L", "D", "T"].indexOf(id) >= 0 && Number(scores[id] || 0) >= 0.67).length;
    const missingCount = (corr.missing || []).length;
    const gate = corr.quality_gate;
    const veto = (corr.vetoes || []).find((v) => Number(v.strength) >= 1);

    let verdict;
    if (gate) verdict = "质量门 " + (gate.id || "") + " " + (gate.name || "") + " 未通过：没有观测就不生成变化结论，本事件只登记资料缺口。";
    else if (veto) verdict = "否决已生效（" + (veto.id || "") + " " + (veto.name || "") + "）：本线索保留为困难负样本，不作为派单依据。";
    else if (corr.evidence_tier === "C") verdict = "等级 C 只表示存在信号，不构成活动或违法认定；补齐合规与物理证据后才具备复核 B 级的条件。";
    else verdict = "证据等级 " + (corr.evidence_tier || "—") + "；排序分只用于排序，不是概率。";

    const metrics = parseRuleMetrics(event.rule);
    const metricHtml = metrics.length
      ? '<div class="yw-metrics">' + metrics.map((m) =>
        '<div class="yw-metric"><span>' + esc(m.label) + "</span><b>" + esc(m.value) + "</b></div>").join("") + "</div>"
      : "";

    const chain = (event.evidence_chain || []).map((row) =>
      '<div class="yw-ev-chain"><b>' + esc(row.family) + " " + esc(row.label) +
      ' <span>' + num(row.score, 2) + "</span></b><p>" + esc(row.summary || "—") + "</p></div>"
    ).join("");
    const factors = (event.evidence_factors || []).map((f) =>
      "<tr><td>" + esc(f.id) + "</td><td>" + esc(f.strength) + "</td><td>" + esc(f.reliability) +
      "</td><td>" + esc(f.adjusted) + "</td><td>" + esc(f.source || "—") + "</td><td>" + esc(f.note || "—") + "</td></tr>"
    ).join("");

    return '<div class="yw-block yw-corro">' +
      '<div class="yw-corro-head"><div><h3>多维佐证</h3>' +
      '<p>' + esc((event.scenario && event.scenario.primary) || "") + " " + esc((event.scenario && event.scenario.label) || "") + "</p></div>" +
      '<div class="yw-corro-score"><b>' + (corr.score == null ? "—" : num(corr.score, 1)) + "</b><span>排序分 · 未校准</span></div></div>" +
      metricHtml +
      '<div class="yw-corro-stats">' +
      '<span>证据族 <b>' + active + "/7</b></span>" +
      '<span>直接族 ≥0.67 <b>' + direct + "/5</b></span>" +
      '<span>缺口 <b>' + missingCount + "</b> 项</span>" +
      '<span class="' + evidenceTierKind(corr.evidence_tier) + '">等级 <b>' + esc(corr.evidence_tier || "—") + "</b></span>" +
      "</div>" +
      renderFamilyMatrix(corr) +
      '<p class="yw-mx-note">缺失按 0 计分，仅表示“没有这一族的证据”，不等于确认为 0；权重来自 ' + esc((event.scenario && event.scenario.primary) || "S1") + " 情景。</p>" +
      '<div class="yw-verdict"><span>结论边界</span><p>' + esc(verdict) + "</p></div>" +
      '<h4 class="yw-sub">竞争解释对照</h4>' +
      renderCompeting(event, corr) +
      '<h4 class="yw-sub">约束闸门</h4>' +
      renderGates(event) +
      '<h4 class="yw-sub">补证路径</h4>' +
      renderNextSteps(event, corr) +
      (chain ? '<details class="yw-ev-detail" open><summary>逐族证据链（' + (event.evidence_chain || []).length + "）</summary>" +
        '<div class="yw-ev-chain-list">' + chain + "</div></details>" : "") +
      (event.evidence_correction ? '<div class="yw-ev-correction">' + esc(event.evidence_correction) + "</div>" : "") +
      (factors ? '<details class="yw-ev-detail"><summary>因子明细（' + (event.evidence_factors || []).length + "）</summary>" +
        "<table><thead><tr><th>因子</th><th>强度</th><th>可靠性</th><th>调整值</th><th>来源</th><th>说明</th></tr></thead><tbody>" +
        factors + "</tbody></table></details>" : "") +
      '<details class="yw-ev-detail"><summary>原始说明（监管口径全文）</summary>' +
      '<div class="yw-raw"><b>为什么提示</b><p>' + esc(event.rule || "—") + "</p></div>" +
      '<div class="yw-raw"><b>活动解释</b><p>' + esc(event.activity_note || "—") + "</p></div>" +
      (event.recommended_actions && event.recommended_actions.length
        ? '<div class="yw-raw"><b>建议动作</b><p>' + esc(event.recommended_actions.join("；")) + "</p></div>" : "") +
      "</details>" +
      '<p class="yw-ev-note">' + esc(corr.note || "排序分不是概率；缺失族按 0 计分，只表示证据不足。") + "</p>" +
      "</div>";
  }

  // 从规则句里抽出可直接展示的指标，抽不到就不编
  function parseRuleMetrics(rule) {
    const text = String(rule || "");
    const out = [];
    // 方向词（上升/下降）决定正负，数字本身不带符号
    const signed = (re) => {
      const m = text.match(re);
      if (!m) return null;
      let v = Number(m[2]);
      if (!/^[+\-]/.test(m[2]) && /下降|减少|降低|缩小/.test(m[1] || "")) v = -Math.abs(v);
      return (v > 0 ? "+" : "") + v.toFixed(3);
    };
    const bsiVal = signed(/BSI([^0-9+\-]*)([+\-]?\d*\.?\d+)/i);
    if (bsiVal) out.push({ label: "ΔBSI", value: bsiVal });
    const ndviVal = signed(/NDVI([^0-9+\-]*)([+\-]?\d*\.?\d+)/i);
    if (ndviVal) out.push({ label: "ΔNDVI", value: ndviVal });
    const area = text.match(/([\d.]+)\s*公顷/);
    if (area) out.push({ label: "变化面积", value: Number(area[1]).toFixed(2) + " ha" });
    if (/坑缘/.test(text)) out.push({ label: "空间关系", value: "紧邻坑缘" });
    return out.slice(0, 4);
  }

  let geoSpinning = false;

  function toggleGeoExpand(force) {
    const stage = $("yw-geo-stage");
    if (!stage) return;
    const open = force === undefined ? !stage.classList.contains("is-expanded") : !!force;
    stage.classList.toggle("is-expanded", open);
    document.body.classList.toggle("yw-geo-expanded", open);
    let veil = $("yw-geo-veil");
    if (open && !veil) {
      veil = document.createElement("div");
      veil.id = "yw-geo-veil";
      veil.className = "yw-geo-veil";
      document.body.appendChild(veil);
      veil.addEventListener("click", () => toggleGeoExpand(false));
    } else if (!open && veil) {
      veil.remove();
    }
  }

  function mountGeoBlock(event) {
    const stage = $("yw-geo-stage");
    if (!stage) return;
    const btns = document.querySelectorAll("[data-geo-preset]");
    btns.forEach((btn) => btn.addEventListener("click", () => {
      btns.forEach((b) => b.classList.toggle("is-on", b === btn));
      if (window.YTGeo3D) window.YTGeo3D.setPreset(btn.dataset.geoPreset);
    }));
    const spinBtn = document.querySelector("[data-geo-spin]");
    if (spinBtn) spinBtn.addEventListener("click", () => {
      geoSpinning = !geoSpinning;
      spinBtn.classList.toggle("is-on", geoSpinning);
      if (window.YTGeo3D) window.YTGeo3D.setSpin(geoSpinning);
    });
    const expandBtn = document.querySelector("[data-geo-expand]");
    if (expandBtn) expandBtn.addEventListener("click", () => toggleGeoExpand(true));
    const closeBtn = document.querySelector("[data-geo-close]");
    if (closeBtn) closeBtn.addEventListener("click", () => toggleGeoExpand(false));
    if (!window.YTGeo3D) {
      stage.classList.remove("is-loading");
      stage.classList.add("is-error");
      const tip = document.createElement("div");
      tip.className = "yw-geo-fallback";
      tip.textContent = "三维组件未加载：请检查 /vendor/three.min.js 与 /static/yantai-geo3d.js";
      stage.appendChild(tip);
      return;
    }
    window.YTGeo3D.setSpin(geoSpinning);
    window.YTGeo3D.mount(stage, event);
  }

  function renderDetail() {
    const event = state.event;
    if (!event) return;
    // 换事件前先释放上一块三维画布，避免 WebGL 上下文累积
    toggleGeoExpand(false);
    if (window.YTGeo3D) window.YTGeo3D.unmount();
    const q = event.quality || {};
    const facts = [
      ["面积", event.area_ha == null ? "—" : num(event.area_ha) + " ha"],
      ["中心", event.center_lonlat ? event.center_lonlat.map((v) => num(v, 5)).join(", ") : "—"],
      ["观测窗口", event.window || "—"],
      ["规则评分", event.rule_score == null ? "不评分" : num(event.rule_score) + "（未校准）"],
      ["影像", "Sentinel-2 10 m"],
      ["有效像元", q.valid_ratio ? pct(q.valid_ratio) : "—"],
    ].map(([k, v]) => '<div class="yw-fact"><span>' + esc(k) + '</span><b>' + esc(v) + '</b></div>').join("");

    const reviewButtons = DECISIONS.map((d) =>
      '<button type="button" class="yw-btn" data-decision="' + esc(d) + '">' + esc(d) + '</button>').join("");
    const history = (event.review_history || []).slice().reverse().map((r) =>
      '<div class="yw-history-item"><b>' + esc(r.decision) + "</b> · " + esc(r.reviewer || "—") +
      '<span>' + esc((r.reviewed_at || "").replace("T", " ").slice(0, 19)) + (r.note ? " · " + esc(r.note) : "") + "</span></div>").join("");

    $("yw-detail").innerHTML =
      '<div class="yw-detail-head"><h2>' + esc(event.title) + '</h2><p>' + esc(event.subtitle || "") + "</p>" +
      '<div class="yw-detail-tags">' + stateTag(event.domain_label || "", "") +
      stateTag(PRIORITY_LABEL[event.priority] || event.priority, event.priority === "P2" ? "warn" : "") +
      stateTag(event.lifecycle || "", event.lifecycle === "已排除" ? "ok" : "gray") + "</div></div>" +
      '<div class="yw-detail-body">' +
      '<div class="yw-block"><h3>四类状态</h3><div class="yw-state-grid">' +
      '<div class="yw-state"><span>观测质量</span><b>' + (q.valid_ratio ? pct(q.valid_ratio) + " 有效" : "无有效观测") + '</b></div>' +
      '<div class="yw-state"><span>活动判断</span><b>' + esc(event.activity_status) + '</b></div>' +
      '<div class="yw-state"><span>合规判断</span><b>' + esc(event.compliance_status) + '</b></div>' +
      '<div class="yw-state"><span>处置状态</span><b>' + esc(event.disposition_status || event.lifecycle) + '</b></div>' +
      "</div></div>" +
      renderGeoBlock(event) +
      renderCorroboration(event) +
      '<div class="yw-block"><h3>事实</h3><div class="yw-facts">' + facts + "</div></div>" +
      '<div class="yw-block"><h3>统计口径</h3><ul class="yw-list">' +
      "<li>面积为变化像元聚类面积，不是现场测绘面积，也不是违法认定面积。</li>" +
      "<li>规则评分未校准，不能当作概率、置信度或 F1 分数。</li>" +
      "<li>观测日期是影像成像日期，不是活动起始时间；缺测不等于无异常。</li>" +
      "<li>当前可运行方法只对应 BC1（露天坡面裸化）；BC2—BC6（堆场、硐口、河道采砂、厂区、便道）未建模，不能用同一阈值解释。</li>" +
      "</ul></div>" +
      '<div class="yw-review"><h3>人工研判</h3>' +
      (state.user ? '<p style="font-size:10px;color:#667085">当前：' + esc(state.user.display_name || state.user.username) + "（" + esc(state.user.role) + '）</p>' :
        '<p style="font-size:10px;color:#b54708">研判需案件工作台账号（管理员/指挥员/审核员）。<a href="/workspace">去登录</a></p>') +
      '<textarea id="yw-review-note" placeholder="补充依据、核查建议或排除理由（可选）"></textarea>' +
      '<textarea id="yw-review-basis" placeholder="依据（文号/影像日期/现场记录，可选）" style="min-height:34px"></textarea>' +
      '<div class="yw-review-actions">' + reviewButtons + "</div>" +
      (history ? '<div class="yw-history"><h3>历史意见</h3>' + history + "</div>" : "") +
      "</div></div>";

    const buttons = $("yw-detail").querySelectorAll("[data-decision]");
    buttons.forEach((btn) => {
      btn.disabled = !state.user;
      btn.addEventListener("click", () => submitReview(event.id, btn.dataset.decision));
    });
    renderObservations();
    mountGeoBlock(event);
  }

  async function submitReview(eventId, decision) {
    if (!state.user) { toast("请先在案件工作台登录，再写入研判"); return; }
    const note = ($("yw-review-note") || {}).value || "";
    const basis = ($("yw-review-basis") || {}).value || "";
    try {
      const res = await api("/api/yantai/events/" + encodeURIComponent(eventId) + "/review", {
        method: "POST", body: { decision, note, basis },
      });
      const index = state.data.events.findIndex((e) => e.id === eventId);
      if (index >= 0) state.data.events[index] = res.event;
      state.event = res.event;
      renderQueue();
      renderDetail();
      toast("研判已写入：" + decision + "；历史意见保留");
    } catch (err) {
      toast(err.message + (err.status === 403 ? "（当前角色无权限）" : ""));
    }
  }

  async function checkLocal() {
    try {
      const res = await api("/api/yantai/check", { method: "POST" });
      toast("已检查本地试点文件；数据指纹变化：" + (res.data_changed ? "是" : "否") + "；未查询卫星新影像，新增事件固定为 0，不能读作“无异常”");
      await load();
    } catch (err) {
      toast(err.message);
    }
  }

  function switchView(view) {
    document.querySelectorAll(".yw-mobile-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    document.querySelectorAll("[data-view]").forEach((el) => {
      if (["queue", "map", "detail"].includes(el.dataset.view) && el.classList.contains("yw-queue") || el.classList.contains("yw-main") || el.classList.contains("yw-detail")) {
        el.classList.toggle("active", el.dataset.view === view);
      }
    });
    if (view === "map" && state.map) setTimeout(() => { state.map.invalidateSize(); layoutCompare(); }, 60);
    if (view === "detail") setTimeout(layoutCompare, 60);
  }

  function bind() {
    let resizeTimer = null;
    $("yw-filter-domain").addEventListener("change", (e) => { state.filter.domain = e.target.value; renderQueue(); });
    $("yw-filter-priority").addEventListener("change", (e) => { state.filter.priority = e.target.value; renderQueue(); });
    $("yw-split").addEventListener("input", (e) => setSplit(e.target.value));
    const regionSel = $("yw-region");
    if (regionSel) regionSel.addEventListener("change", (e) => setRegion(e.target.value));
    const patchesToggle = $("yw-show-patches");
    if (patchesToggle) patchesToggle.addEventListener("change", (e) => {
      $("yw-compare").classList.toggle("patches-off", !e.target.checked);
    });
    const listToggle = $("yw-toggle-list");
    if (listToggle) listToggle.addEventListener("click", () => togglePatchPanel());
    const showAll = $("yw-show-all");
    if (showAll) showAll.addEventListener("change", (e) => {
      state.showAllLabels = e.target.checked;
      renderPatchLabels(state.region);
    });
    const panelClose = $("yw-patch-close");
    if (panelClose) panelClose.addEventListener("click", () => togglePatchPanel(false));
    const stage = $("yw-compare-stage");
    if (stage) stage.addEventListener("click", (e) => {
      const target = e.target;
      if (!target || !target.closest) return;
      if (target.closest(".yw-patch-label, .yw-patch-pop, .yw-patch-panel, input[type=range]")) return;
      if (target.closest(".yw-patch")) return;
      if (state.selectedPatch) togglePatch(state.selectedPatch);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (document.body.classList.contains("yw-geo-expanded")) { toggleGeoExpand(false); return; }
      if (state.selectedPatch) togglePatch(state.selectedPatch);
    });
    document.querySelectorAll("[data-cmode]").forEach((btn) => {
      btn.addEventListener("click", () => setCompareMode(btn.dataset.cmode));
    });
    $("yw-check").addEventListener("click", checkLocal);
    const planOpen = $("yw-plan-open");
    const planEl = $("ywp-overlay");
    if (planOpen && planEl) {
      const closePlan = () => { planEl.hidden = true; };
      planOpen.addEventListener("click", () => { planEl.hidden = false; });
      $("ywp-close").addEventListener("click", closePlan);
      planEl.addEventListener("click", (e) => { if (e.target === planEl) closePlan(); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !planEl.hidden) closePlan(); });
    }
    const covEl = $("yw-coverage");
    if (covEl) {
      covEl.addEventListener("click", (e) => {
        if (e.target && e.target.id === "yw-plan-inline" && planEl) planEl.hidden = false;
      });
    }
    document.querySelectorAll(".yw-mobile-tabs button").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.view));
    });
    window.addEventListener("resize", () => {
      if (state.map) state.map.invalidateSize();
      if (window.innerWidth > 900) {
        document.querySelectorAll(".yw-queue, .yw-main, .yw-detail").forEach((el) => el.classList.remove("active"));
      }
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(layoutCompare, 90);
    });
  }

  async function load() {
    const [data, me, regionData] = await Promise.all([
      api("/api/yantai/workspace"),
      api("/api/auth/me").catch(() => ({ authenticated: false, user: null })),
      api("/api/yantai/regions").catch((err) => ({ items: [], error: err.message })),
    ]);
    state.data = data;
    state.user = me.authenticated ? me.user : null;
    state.regions = (regionData && regionData.items) || [];
    state.regionError = (regionData && regionData.error) || null;
    renderRegionOptions();
    renderQueue();
    renderMap();
    renderCoverage();
    if (!state.regions.length) {
      $("yw-patch-summary").textContent = "";
      $("yw-toggle-list").disabled = true;
      $("yw-img-meta").innerHTML = '<span class="yw-caveat">样区影像列表加载失败：' +
        esc(state.regionError || "无可用样区") + "。缺测不显示“0 异常”。</span>";
    }
    if (!state.event && state.data.events.length) selectEvent(state.data.events[0].id, true, true);
    else if (state.event) {
      renderDetail();
      if (state.region) renderRegionEvidence();
    } else if (state.regions.length) setRegion(state.regions[0].id);
    else fitAll();
  }

  async function boot() {
    const bootGuard = setTimeout(() => {
      hideBoot();
    }, 18000);
    initMap();
    bind();
    setSplit(50);
    try {
      await load();
    } catch (err) {
      $("yw-event-list").innerHTML = '<p style="padding:12px;color:#b42318;font-size:11px">加载失败：' + esc(err.message) + "。加载失败不显示“零异常”。</p>";
      $("yw-coverage").innerHTML = '<p style="color:#b42318">覆盖数据加载失败。</p>';
      toast(err.message);
    } finally {
      clearTimeout(bootGuard);
      hideBoot();
    }
  }

  function hideBoot() {
    const b = $("boot-screen");
    if (!b || b.dataset.leaving === "1") return;
    b.dataset.leaving = "1";
    const wait = Math.max(0, 1600 - (performance.now() - BOOT_STARTED));
    setTimeout(() => {
      b.classList.add("boot-leaving");
      setTimeout(() => b.remove(), 900);
    }, wait);
  }

  boot();
})();
/* 多场景研判：五类扩展场景的前端编排。 */
(function () {
  "use strict";

  var HAZARD_NAMES = {
    fire_risk: "森林火险",
    flood_risk: "洪涝风险",
    drought_risk: "干旱风险",
    geohazard_risk: "地质灾害风险"
  };
  var HAZARD_COLORS = {
    fire_risk: "#ff5b52",
    flood_risk: "#5aa9ff",
    drought_risk: "#ffa726",
    geohazard_risk: "#c084fc"
  };
  var STATUS_COLORS = {
    "待接收": "#ffa726",
    "已接收": "#5aa9ff",
    "已反馈": "#34d399",
    "已退回": "#ff5b52"
  };

  var state = {
    user: null,
    tab: "eco",
    map: null,
    layers: null,
    hotspots: null,
    grid: null,
    cases: null,
    options: null,
    eco: null,
    emergency: null,
    event: null,
    linear: null,
    toastTimer: null
  };

  function el(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function num(v, digits) {
    if (v == null || v === "" || isNaN(Number(v))) return "--";
    return Number(v).toFixed(digits == null ? 1 : digits);
  }
  function pct(v) {
    if (v == null || isNaN(Number(v))) return "--";
    return (Number(v) * 100).toFixed(1) + "%";
  }
  function fmtTime(v) {
    if (!v) return "--";
    return String(v).replace("T", " ").replace("Z", "").slice(0, 16);
  }
  function toast(message) {
    var box = el("ops-toast");
    if (!box) return;
    box.textContent = message;
    box.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(function () { box.hidden = true; }, 3600);
  }
  function showGate() {
    el("ops-gate").hidden = false;
    el("ops-app").hidden = true;
  }
  function showApp() {
    el("ops-gate").hidden = true;
    el("ops-app").hidden = false;
  }

  async function api(path, options) {
    var opts = Object.assign({ credentials: "same-origin" }, options || {});
    opts.headers = Object.assign({ Accept: "application/json" }, options && options.headers ? options.headers : {});
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
      opts.headers["Content-Type"] = "application/json";
    }
    var res = await fetch(path, opts);
    var data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (res.status === 401) {
      showGate();
      throw new Error("登录已失效，请重新登录");
    }
    if (!res.ok) {
      throw new Error((data && data.detail) || ("请求失败：" + res.status));
    }
    return data;
  }

  function initMap() {
    if (state.map) return;
    state.map = L.map("ops-map", { zoomControl: true, attributionControl: true }).setView([40.9, 111.05], 8);
    // ?basemap=esri 可切影像底图：地形底图在受限网络下解析不动会把同源连接占满，拖慢整页。
    var base = (new URLSearchParams(location.search).get("basemap") || "").toLowerCase();
    var img = base === "esri" || base === "sat";
    L.tileLayer(img ? "/api/tile/esri/{z}/{x}/{y}" : "/api/tile/opentopo/{z}/{x}/{y}", {
      maxZoom: 17,
      errorTileUrl: "/static/assets/tile-fallback.png",
      attribution: img ? "底图：Esri World Imagery · 数据：公开遥感/OSM/WorldPop"
                       : "底图：OpenTopoMap · 数据：公开遥感/OSM/WorldPop"
    }).addTo(state.map);
    state.layers = L.layerGroup().addTo(state.map);
  }

  function clearLayers() {
    if (state.layers) state.layers.clearLayers();
    if (state.map) state.map.closePopup();
  }

  function featureCenter(feature) {
    var g = feature && feature.geometry;
    var c = g && g.coordinates;
    if (!c || !c.length) return [null, null];
    if (g.type === "Point") return [c[0], c[1]];
    var ring = g.type === "Polygon" ? c[0] : (g.type === "MultiPolygon" ? c[0][0] : null);
    if (!ring || !ring.length) return [null, null];
    var sx = 0, sy = 0;
    ring.forEach(function (p) { sx += p[0]; sy += p[1]; });
    return [sx / ring.length, sy / ring.length];
  }

  function fitPoints(points) {
    if (!state.map || !points.length) return;
    var bounds = L.latLngBounds(points);
    state.map.fitBounds(bounds.pad(0.25), { maxZoom: 13 });
  }

  async function ensureHotspots() {
    if (state.hotspots) return state.hotspots;
    var data = await api("/api/hotspots");
    state.hotspots = (data.features || []).map(function (f) {
      var p = f.properties || {};
      var c = featureCenter(f);
      return {
        id: p.id, name: p.name, lon: c[0], lat: c[1],
        score: p.priority_score, label: p.priority_label, hazard: p.dominant_class,
        danger: p.current_danger, police: p.nearest_police, dist: p.dist_police_km
      };
    }).filter(function (x) { return x.lon != null; });
    return state.hotspots;
  }

  async function ensureGrid() {
    if (state.grid) return state.grid;
    var data = await api("/api/risk-grid");
    state.grid = (data.features || []).map(function (f) {
      var p = f.properties || {};
      var c = featureCenter(f);
      return {
        cell_id: p.cell_id, lon: c[0], lat: c[1],
        hazard: p.current_hazard || p.dominant_hazard,
        hazard_name: p.current_hazard_name,
        score: p.current_danger != null ? p.current_danger : p.natural_risk,
        level: p.current_danger_label || p.natural_label
      };
    }).filter(function (x) { return x.lon != null; });
    return state.grid;
  }

  function plotHotspots(rows, limit) {
    rows = (rows || []).slice(0, limit || 40);
    var points = [];
    rows.forEach(function (h) {
      if (h.lon == null) return;
      var color = Number(h.score) >= 70 ? "#ff5b52" : Number(h.score) >= 60 ? "#ffa726" : "#ffd60a";
      var marker = L.circleMarker([h.lat, h.lon], {
        radius: 5, color: color, weight: 1.5, fillColor: color, fillOpacity: 0.55
      });
      marker.bindPopup(
        "<b>" + esc(h.name || h.id) + "</b><br>" +
        "核查优先级：" + esc(num(h.score)) + " · " + esc(h.label || "") + "<br>" +
        "主导线索：" + esc(h.hazard || "--") + "<br>" +
        "最近警务点：" + esc(h.police || "--") + " · 直线 " + esc(num(h.dist)) + " km"
      );
      marker.addTo(state.layers);
      points.push([h.lat, h.lon]);
    });
    return points;
  }

  function plotGridCells(rows) {
    var points = [];
    (rows || []).forEach(function (c) {
      if (c.lon == null) return;
      var color = HAZARD_COLORS[c.hazard] || "#ffa726";
      var score = Number(c.score) || 0;
      var marker = L.circleMarker([c.lat, c.lon], {
        radius: 4 + Math.max(0, Math.min(8, (score - 40) / 5)),
        color: color, weight: 1.2, fillColor: color, fillOpacity: 0.16
      });
      marker.bindPopup(
        "<b>" + esc(c.cell_id) + "</b><br>" +
        "灾种：" + esc(c.hazard_name || HAZARD_NAMES[c.hazard] || c.hazard) + "<br>" +
        "当前危险：" + esc(num(score)) + " · " + esc(c.level || "") + "<br>" +
        "最近气象点：" + esc(c.weather_point || "--")
      );
      marker.addTo(state.layers);
      points.push([c.lat, c.lon]);
    });
    return points;
  }

  function renderEco(data) {
    var stats = data.stats || {};
    var by = stats.by_status || {};
    var verification = data.verification || {};
    el("eco-kpis").innerHTML = [
      ["案件总量", stats.total || 0, "全部建成案件", "accent"],
      ["核查中", (by["核查中"] || 0) + (by["待复核"] || 0), "含待复核", "orange"],
      ["已归档", by["已归档"] || 0, "完成闭环", "green"],
      ["确认率", pct(verification.confirmed_rate), "confirmed/(confirmed+excluded)", ""]
    ].map(function (row) {
      return '<div class="kpi ' + row[3] + '"><div class="kpi-label">' + row[0] +
        '</div><div class="kpi-value">' + esc(row[1]) + '</div><div class="kpi-sub">' + esc(row[2]) + "</div></div>";
    }).join("");

    var cases = data.recent_cases || [];
    el("eco-cases").innerHTML = cases.length ? cases.map(function (c) {
      return '<div class="ops-item click" data-case="' + esc(c.id) + '">' +
        '<div class="ops-item-top"><span class="ops-item-title">' + esc(c.case_no) + " · " + esc(c.title) + "</span>" +
        '<span class="ops-item-score">' + esc(c.priority_label || "") + "</span></div>" +
        '<div class="ops-item-meta">' + esc(c.status) + " · " + esc(c.assignee_name || "未派单") +
        " · 更新 " + esc(fmtTime(c.updated_at)) + "</div></div>";
    }).join("") : '<div class="ops-note">暂无案件。可先到案件工作台从高发点或风险格网建案。</div>';

    var hot = data.top_hotspots || [];
    el("eco-hotspots").innerHTML = hot.map(function (h) {
      return '<div class="ops-item"><div class="ops-item-top"><span class="ops-item-title">' + esc(h.name || h.id) + "</span>" +
        '<span class="ops-item-score">' + esc(num(h.priority_score)) + "</span></div>" +
        '<div class="ops-item-meta">' + esc(h.dominant_class || "--") + " · " + esc(h.priority_label || "") +
        " · 当前危险 " + esc(num(h.current_danger)) + "</div></div>";
    }).join("") || '<div class="ops-note">暂无高发点。</div>';

    var points = plotHotspots(hot, 8).concat(plotCases(cases));
    if (points.length) fitPoints(points);
  }

  function plotCases(rows) {
    var points = [];
    (rows || []).forEach(function (c) {
      if (c.lon == null || c.lat == null) return;
      var color = c.status === "已归档" ? "#34d399" : c.status === "已复核" ? "#5aa9ff" : "#ffa726";
      L.circleMarker([c.lat, c.lon], {
        radius: 7, color: color, weight: 2, fillColor: color, fillOpacity: 0.7
      }).bindPopup(
        "<b>" + esc(c.case_no) + "</b><br>" + esc(c.title) + "<br>" +
        "状态：" + esc(c.status) + "<br><a href='/workspace?case=" + encodeURIComponent(c.id) + "'>打开案件</a>"
      ).addTo(state.layers);
      points.push([c.lat, c.lon]);
    });
    return points;
  }

  async function loadEco() {
    try {
      var data = await api("/api/operations/eco");
      if (state.tab !== "eco") return;
      state.eco = data;
      clearLayers();
      renderEco(data);
    } catch (err) {
      toast(err.message);
    }
  }

  function renderEmergency(data) {
    var s = data.summary || {};
    var by = s.by_level || {};
    var cards = [
      ["参与研判格网", s.cells || 0],
      ["最高危险分", num(s.max_score)],
      ["一级/二级", (by["一级"] || 0) + " / " + (by["二级"] || 0)],
      ["三级/常态", (by["三级"] || 0) + " / " + (by["常态"] || 0)]
    ];
    el("em-summary").innerHTML = cards.map(function (x) {
      return '<div class="summary-card"><div class="l">' + x[0] + '</div><div class="v">' + esc(x[1]) + "</div></div>";
    }).join("") + '<div class="level-pills">' + (data.hazards || []).map(function (h) {
      return '<span class="pill blue">' + esc(h.name) + " · " + esc(h.count) + " 格 · 峰值 " + esc(num(h.max_score)) + "</span>";
    }).join("") + "</div>";

    var alerts = data.alerts || [];
    el("em-alerts").innerHTML = alerts.map(function (a) {
      return '<div class="ops-item"><div class="ops-item-top"><span class="ops-item-title">' +
        esc(a.cell_id) + " · " + esc(a.hazard_name) + "</span>" +
        '<span class="ops-item-score">' + esc(num(a.score)) + "</span></div>" +
        '<div class="ops-item-meta">' + esc(a.level) + " · 最近警务点 " + esc(a.nearest_police || "--") +
        " · " + esc(num(a.dist_police_km)) + " km</div>" +
        '<div class="ops-item-desc">' + esc(a.advice || "") + "</div></div>";
    }).join("") || '<div class="ops-note">当前筛选下没有风险格网。</div>';

    var weather = data.weather || {};
    el("em-weather").textContent = weather.fetched_at
      ? "气象缓存 " + fmtTime(weather.fetched_at) + " · " + (weather.points || []).length + " 个点位 · " + (weather.source || "")
      : "暂无气象缓存；风险格网仍按已有自然因子计算。";
  }

  async function loadEmergency() {
    try {
      var hazard = el("em-hazard").value;
      var data = await api("/api/operations/emergency" + (hazard ? "?hazard=" + encodeURIComponent(hazard) : ""));
      if (state.tab !== "emergency") return;
      state.emergency = data;
      renderEmergency(data);
      var grid = await ensureGrid();
      if (state.tab !== "emergency") return;
      clearLayers();
      var rows = grid.filter(function (c) { return !hazard || c.hazard === hazard; });
      var points = plotGridCells(rows);
      if (points.length) fitPoints(points);
      else toast("该灾种暂无格网数据");
    } catch (err) {
      toast(err.message);
    }
  }

  function renderEvent(data) {
    var s = data.summary || {};
    var ev = data.event || {};
    var rings = data.rings || [];
    el("event-result").innerHTML =
      '<div class="result-card"><h4>' + esc(ev.name) + " · " + esc(ev.level) + "安保</h4>" +
      "<p>外围半径 " + esc(num(ev.radius_km, 1)) + " km · 预计 " + esc(ev.people || 0) + " 人</p>" +
      rings.map(function (r) {
        return "<p>" + esc(r.name) + "：" + esc(num(r.radius_km, 2)) + " km</p>";
      }).join("") + "</div>" +
      '<div class="result-card"><h4>周边要素</h4>' +
      "<p>警务点 " + esc(s.police_within_outer || 0) + " 个（展示前 8 个）</p>" +
      "<p>高发点 " + esc(s.hotspots_within || 0) + " 个 · 风险格网 " + esc(s.risk_cells_within || 0) + " 个</p>" +
      "<p>建议岗位：核心 " + esc(s.suggested_core_posts || 0) + " · 交通 " + esc(s.suggested_traffic_posts || 0) +
      " · 合计 " + esc(s.suggested_total_posts || 0) + "</p>" +
      '<p style="color:#ffc06a">' + esc(data.note || "") + "</p></div>";
  }

  function drawEvent(data) {
    var points = [];
    (data.rings || []).forEach(function (ring) {
      L.geoJSON(ring.geojson, {
        style: { color: ring.color, weight: 2, dashArray: ring.name === "核心圈" ? null : "6 5", fillOpacity: 0.06 }
      }).bindPopup("<b>" + esc(ring.name) + "</b><br>半径 " + esc(num(ring.radius_km, 2)) + " km").addTo(state.layers);
    });
    (data.police || []).forEach(function (p) {
      L.circleMarker([p.lat, p.lon], { radius: 5, color: "#5aa9ff", fillColor: "#5aa9ff", fillOpacity: 0.8, weight: 1.5 })
        .bindPopup("<b>" + esc(p.name) + "</b><br>距离 " + esc(num(p.dist_km, 2)) + " km").addTo(state.layers);
      points.push([p.lat, p.lon]);
    });
    (data.hotspots || []).forEach(function (h) {
      L.circleMarker([h.lat, h.lon], { radius: 6, color: "#ff5b52", fillColor: "#ff5b52", fillOpacity: 0.65, weight: 1.5 })
        .bindPopup("<b>" + esc(h.name || h.id) + "</b><br>优先级 " + esc(num(h.priority_score))).addTo(state.layers);
      points.push([h.lat, h.lon]);
    });
    (data.risk_cells || []).forEach(function (c) {
      L.circleMarker([c.lat, c.lon], { radius: 4, color: "#c084fc", fillColor: "#c084fc", fillOpacity: 0.4, weight: 1 })
        .bindPopup("<b>" + esc(c.cell_id) + "</b><br>" + esc(c.hazard_name) + " · " + esc(num(c.score))).addTo(state.layers);
      points.push([c.lat, c.lon]);
    });
    var ev = data.event || {};
    L.circleMarker([ev.lat, ev.lon], { radius: 7, color: "#ffffff", fillColor: "#38bdf8", fillOpacity: 0.9, weight: 2 })
      .bindPopup("<b>" + esc(ev.name) + "</b><br>活动中心").addTo(state.layers);
    points.push([ev.lat, ev.lon]);
    fitPoints(points);
  }

  async function submitEvent(evt) {
    evt.preventDefault();
    var form = evt.currentTarget;
    var body = {
      name: form.name.value.trim(),
      lon: Number(form.lon.value),
      lat: Number(form.lat.value),
      radius_km: Number(form.radius_km.value),
      people: Number(form.people.value),
      level: form.level.value
    };
    try {
      var data = await api("/api/operations/event", { method: "POST", body: body });
      state.event = data;
      renderEvent(data);
      clearLayers();
      drawEvent(data);
    } catch (err) {
      toast(err.message);
    }
  }

  function renderLinear(data) {
    var s = data.stats || {};
    el("linear-result").innerHTML =
      '<div class="result-card"><h4>' + esc(data.title) + "</h4>" +
      "<p>路径来源：" + esc(data.mode === "osrm" ? "OSRM 车行路网" : "直线降级") +
      (data.mode === "straight" ? " · " + esc(data.mode_note) : "") + "</p>" +
      "<p>路线长度 " + esc(num(s.route_km, 2)) + " km · 采样 " + esc(s.samples || 0) + " 点</p>" +
      "<p>高程 " + esc(num(s.terrain_min, 0)) + "–" + esc(num(s.terrain_max, 0)) + " m</p></div>" +
      '<div class="result-card"><h4>沿线要素</h4>' +
      "<p>最近警务点 " + esc(num(s.police_min_km)) + " km · 10 km 内 " + esc(s.police_within_10km || 0) + " 个</p>" +
      "<p>走廊内高发点 " + esc(s.hotspots_near || 0) + " 个 · 风险格网 " + esc(s.risk_cells_near || 0) + " 个</p></div>";
    el("linear-kml").disabled = !data.kml;
    el("linear-csv").disabled = !data.csv;
  }

  function drawLinear(data) {
    var points = [];
    (data.geojson && data.geojson.features || []).forEach(function (f) {
      if (f.geometry && f.geometry.type === "LineString") {
        L.geoJSON(f, { style: { color: "#38bdf8", weight: 3, opacity: 0.9 } })
          .bindPopup("<b>" + esc(data.title) + "</b>").addTo(state.layers);
        f.geometry.coordinates.forEach(function (c) { points.push([c[1], c[0]]); });
      }
    });
    (data.samples || []).forEach(function (s) {
      L.circleMarker([s.lat, s.lon], { radius: 4, color: "#22d3ee", fillColor: "#22d3ee", fillOpacity: 0.65, weight: 1 })
        .bindPopup("<b>采样 " + esc(s.seq) + "</b><br>高程 " + esc(num(s.terrain_m, 0)) + " m<br>最近警务点 " +
          esc(s.nearest_police || "--") + " · " + esc(num(s.dist_police_km)) + " km").addTo(state.layers);
    });
    (data.hotspots || []).forEach(function (h) {
      L.circleMarker([h.lat, h.lon], { radius: 6, color: "#ff5b52", fillColor: "#ff5b52", fillOpacity: 0.7, weight: 1.5 })
        .bindPopup("<b>" + esc(h.name || h.id) + "</b><br>距走廊 " + esc(num(h.dist_km, 2)) + " km").addTo(state.layers);
      points.push([h.lat, h.lon]);
    });
    fitPoints(points);
  }

  async function submitLinear(evt) {
    evt.preventDefault();
    var form = evt.currentTarget;
    var body = {
      start_lon: Number(form.start_lon.value), start_lat: Number(form.start_lat.value),
      end_lon: Number(form.end_lon.value), end_lat: Number(form.end_lat.value),
      sample_km: Number(form.sample_km.value), buffer_km: Number(form.buffer_km.value),
      title: form.title.value.trim()
    };
    try {
      var data = await api("/api/operations/linear", { method: "POST", body: body });
      state.linear = data;
      renderLinear(data);
      clearLayers();
      drawLinear(data);
    } catch (err) {
      toast(err.message);
    }
  }

  function downloadText(name, text, mime) {
    var blob = new Blob([text], { type: mime || "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 800);
  }

  function downloadLinear(kind) {
    if (!state.linear) return;
    var base = (state.linear.title || "线性巡护").replace(/[\\/:*?"<>|]/g, "_");
    if (kind === "kml") downloadText(base + ".kml", state.linear.kml, "application/vnd.google-earth.kml+xml");
    if (kind === "csv") downloadText(base + ".csv", state.linear.csv, "text/csv;charset=utf-8");
  }

  async function loadCases() {
    if (state.cases) return state.cases;
    var data = await api("/api/cases");
    state.cases = Array.isArray(data) ? data : (data.items || []);
    return state.cases;
  }

  function renderReferrals(items) {
    el("referral-list").innerHTML = (items || []).map(function (r) {
      var canEdit = state.user && (state.user.role === "admin" || state.user.role === "commander");
      var color = STATUS_COLORS[r.status] || "#8496a8";
      return '<div class="ops-item">' +
        '<div class="ops-item-top"><span class="ops-item-title">' + esc(r.case_no) + " → " + esc(r.department) + "</span>" +
        '<span class="ops-item-score" style="color:' + color + '">' + esc(r.status) + "</span></div>" +
        '<div class="ops-item-meta">' + esc(r.case_title || "") + " · " + esc(r.created_by_name || "") +
        " · 期限 " + esc(r.due_at || "未设") + "</div>" +
        (r.note ? '<div class="ops-item-desc">' + esc(r.note) + "</div>" : "") +
        (r.feedback ? '<div class="ops-item-desc" style="color:#9fd6b8">反馈：' + esc(r.feedback) + "</div>" : "") +
        (canEdit ? '<div style="display:flex;gap:6px;margin-top:8px">' +
          '<select data-ref-status="' + esc(r.id) + '" style="flex:1;padding:6px;border-radius:7px;border:1px solid #1b2a3a;background:#0a141d;color:#e7eef7">' +
          ["待接收", "已接收", "已反馈", "已退回"].map(function (s) {
            return '<option' + (s === r.status ? " selected" : "") + ">" + s + "</option>";
          }).join("") + "</select>" +
          '<button class="btn btn-sm" type="button" data-ref-update="' + esc(r.id) + '">更新</button></div>' : "") +
        "</div>";
    }).join("") || '<div class="ops-note">暂无移送记录。</div>';
  }

  async function loadReferrals() {
    try {
      var cases = await loadCases();
      var select = el("referral-case");
      select.innerHTML = cases.length ? cases.map(function (c) {
        return '<option value="' + esc(c.id) + '">' + esc(c.case_no) + " · " + esc(c.title) + " · " + esc(c.status) + "</option>";
      }).join("") : '<option value="">暂无可移送案件</option>';
      var data = await api("/api/operations/referrals");
      renderReferrals(data.items || []);
      clearLayers();
      var points = plotCases(cases.slice(0, 30));
      if (points.length) fitPoints(points);
    } catch (err) {
      toast(err.message);
    }
  }

  async function submitReferral(evt) {
    evt.preventDefault();
    var form = evt.currentTarget;
    var caseId = form.case_id.value;
    if (!caseId) { toast("请先选择案件"); return; }
    var body = {
      department: form.department.value,
      contact: form.contact.value.trim() || null,
      note: form.note.value.trim() || null,
      due_at: form.due_at.value || null
    };
    try {
      var data = await api("/api/operations/cases/" + encodeURIComponent(caseId) + "/referrals", { method: "POST", body: body });
      el("referral-result").innerHTML = '<div class="result-card"><h4>已建立移送台账</h4><p>' +
        esc(data.id) + " · " + esc(data.department) + " · " + esc(data.status) + "</p></div>";
      toast("移送台账已建立");
      await loadReferrals();
    } catch (err) {
      toast(err.message);
    }
  }

  async function updateReferral(id) {
    var select = document.querySelector('[data-ref-status="' + id + '"]');
    if (!select) return;
    var status = select.value;
    var feedback = null;
    if (status === "已反馈" || status === "已退回") {
      feedback = window.prompt("请填写反馈/退回原因（必填）", "");
      if (feedback == null) return;
      if (!feedback.trim()) { toast("反馈/退回原因不能为空"); return; }
    }
    try {
      await api("/api/operations/referrals/" + encodeURIComponent(id), {
        method: "PATCH", body: { status: status, feedback: feedback }
      });
      toast("移送状态已更新");
      await loadReferrals();
    } catch (err) {
      toast(err.message);
    }
  }

  function switchTab(tab) {
    state.tab = tab;
    Array.prototype.forEach.call(document.querySelectorAll("#ops-tabs .tab"), function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-tab") === tab);
    });
    Array.prototype.forEach.call(document.querySelectorAll(".ops-panel .panel"), function (panel) {
      panel.hidden = panel.id !== "panel-" + tab;
    });
    clearLayers();
    if (tab === "eco") loadEco();
    if (tab === "emergency") loadEmergency();
    if (tab === "event" && state.event) drawEvent(state.event);
    if (tab === "linear" && state.linear) drawLinear(state.linear);
    if (tab === "referral") loadReferrals();
    if (state.map) setTimeout(function () { state.map.invalidateSize(); }, 160);
  }

  function bind() {
    Array.prototype.forEach.call(document.querySelectorAll("#ops-tabs .tab"), function (btn) {
      btn.addEventListener("click", function () { switchTab(btn.getAttribute("data-tab")); });
    });
    el("em-refresh").addEventListener("click", loadEmergency);
    el("event-form").addEventListener("submit", submitEvent);
    el("linear-form").addEventListener("submit", submitLinear);
    el("linear-kml").addEventListener("click", function () { downloadLinear("kml"); });
    el("linear-csv").addEventListener("click", function () { downloadLinear("csv"); });
    el("referral-form").addEventListener("submit", submitReferral);
    el("referral-list").addEventListener("click", function (evt) {
      var btn = evt.target.closest("[data-ref-update]");
      if (btn) updateReferral(btn.getAttribute("data-ref-update"));
    });
    el("eco-cases").addEventListener("click", function (evt) {
      var item = evt.target.closest("[data-case]");
      if (item) window.location.href = "/workspace?case=" + encodeURIComponent(item.getAttribute("data-case"));
    });
    el("ops-logout").addEventListener("click", async function () {
      try { await api("/api/auth/logout", { method: "POST" }); } catch (err) { /* ignore */ }
      showGate();
    });
  }

  async function boot() {
    try {
      var me = await api("/api/auth/me");
      state.user = me.user || me;
      el("ops-user").textContent = (state.user.display_name || state.user.username || "用户") +
        " · " + (state.user.role || "");
      showApp();
      initMap();
      bind();
      var options = await api("/api/operations/options");
      state.options = options;
      el("referral-department").innerHTML = (options.departments || []).map(function (d) {
        return '<option value="' + esc(d) + '">' + esc(d) + "</option>";
      }).join("");
      await switchTab("eco");
    } catch (err) {
      showGate();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
/* 空中办案控制台：图斑选择 → 同机位精查 → 推演 → 测量意见 / 证据链。 */
(function () {
  "use strict";

  var API = "/api/aircase";
  var app = {
    overview: null,
    patches: [],
    selected: null,
    mode: "precise",
    height: 120,
    sortie: null,
    running: false,
    view: "3d",
    phase: -1,
    webgl: false
  };

  function $(id) { return document.getElementById(id); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function fmt(v, d) {
    if (v === null || v === undefined || isNaN(Number(v))) return "--";
    return Number(v).toFixed(d === undefined ? 1 : d);
  }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtBytes(n) {
    if (!n && n !== 0) return "--";
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
    return Math.round(n / 1024) + " KB";
  }
  function asset(sortie, name) { return API + "/asset/" + encodeURIComponent(sortie.id) + "/" + encodeURIComponent(name); }
  function modeLabel(mode) { return mode === "scene" ? "现场方位图" : "同机位精查"; }

  async function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || "GET", cache: "no-store", headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    var res = await fetch(API + path, init);
    if (!res.ok) {
      var text = "";
      try { text = await res.text(); } catch (e) { /* ignore */ }
      throw new Error("HTTP " + res.status + (text ? " · " + text.slice(0, 180) : ""));
    }
    return res.json();
  }

  // ------------------------------------------------------------------ log / toast
  function log(message, kind) {
    var el = $("log");
    if (!el) return;
    var now = new Date();
    var time = [now.getHours(), now.getMinutes(), now.getSeconds()].map(function (v) {
      return String(v).padStart(2, "0");
    }).join(":");
    var row = document.createElement("div");
    var span = document.createElement("span");
    if (kind) span.className = kind;
    span.textContent = message;
    var t = document.createElement("time");
    t.textContent = time;
    row.appendChild(t);
    row.appendChild(span);
    el.appendChild(row);
    while (el.children.length > 90) el.removeChild(el.firstChild);
    el.scrollTop = el.scrollHeight;
  }

  var toastTimer = null;
  function toast(message, bad) {
    var el = $("toast");
    if (!el) return;
    el.textContent = message;
    el.className = "toast on" + (bad ? " bad" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.className = "toast"; }, 3300);
  }

  // ------------------------------------------------------------------ clock
  function tickClock() {
    var el = $("clock");
    if (!el) return;
    el.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  }

  // ------------------------------------------------------------------ phases
  var PHASE_TEXT = ["待命", "规划中", "飞行中", "检测中", "回传中", "出具中", "完成"];
  var PHASE_LOG = {
    1: "任务下发：同机位航线与云台参数锁定",
    2: "无人机起飞：沿航线执行同机位复拍",
    3: "悬停比对：变化检测与面积量算",
    4: "证据回传：逐文件 SHA-256 + HMAC 签名",
    5: "测量意见生成：法条检索与限制说明",
    6: "推演完成：证据包可校验"
  };
  function setPhase(phase) {
    var p = clamp(phase, 0, 6);
    var top = document.querySelectorAll("#top-steps span");
    var steps = document.querySelectorAll("#timeline .step");
    for (var i = 0; i < top.length; i++) {
      top[i].classList.toggle("done", i < p);
      top[i].classList.toggle("on", i === p && p < 6);
    }
    for (i = 0; i < steps.length; i++) {
      steps[i].classList.toggle("done", i < p);
      steps[i].classList.toggle("on", i === p && p < 6);
    }
    var state = $("log-state");
    if (state) state.textContent = PHASE_TEXT[clamp(p, 0, 6)];
    var phaseEl = $("stage-phase");
    if (phaseEl) phaseEl.textContent = PHASE_TEXT[clamp(p, 0, 6)];
  }
  function onPhase(phase) {
    if (phase === app.phase) return;
    app.phase = phase;
    setPhase(phase);
    if (PHASE_LOG[phase]) log(PHASE_LOG[phase], phase >= 5 ? "ok" : "");
    if (phase === 4) $("hud-link").textContent = "6G NTN 回传中";
    if (phase >= 5) $("hud-link").textContent = "证据包已签名";
    if (phase === 2 && app.sortie) $("hud-task").textContent = app.sortie.id;
  }

  // ------------------------------------------------------------------ numbers
  function setNum(id, value, digits) {
    var el = $(id);
    if (!el) return;
    var to = Number(value);
    if (!isFinite(to)) { el.textContent = "--"; return; }
    var from = parseFloat(el.textContent);
    if (!isFinite(from)) from = 0;
    var start = performance.now();
    function tick(now) {
      var t = clamp((now - start) / 620, 0, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      var v = from + (to - from) * eased;
      el.textContent = digits === 0 ? String(Math.round(v)) : v.toFixed(digits);
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  // ------------------------------------------------------------------ patches
  function renderPatches() {
    var el = $("patch-list");
    if (!app.patches.length) {
      el.innerHTML = '<div class="empty">未检出候选图斑</div>';
      return;
    }
    el.innerHTML = app.patches.map(function (p) {
      return '<div class="patch" data-id="' + esc(p.id) + '">' +
        '<div class="p-top"><span class="p-id">' + esc(p.id) + '</span>' +
        '<span class="p-area">' + fmt(p.area_ha) + '<small>ha</small></span></div>' +
        '<div class="p-meta"><span class="p-class">' + esc(p.class) + '</span>' +
        '<span class="p-conf"><i><b style="width:' + Math.round((p.confidence || 0) * 100) + '%"></b></i>' +
        fmt(p.confidence, 2) + '</span></div></div>';
    }).join("");
    Array.prototype.forEach.call(el.querySelectorAll(".patch"), function (node) {
      node.addEventListener("click", function () { selectPatch(node.getAttribute("data-id")); });
    });
    renderMapTable();
  }

  function renderMapTable() {
    var box = $("map-table");
    if (!box) return;
    if (!app.patches.length) {
      box.innerHTML = '<div class="empty">未检出候选图斑</div>';
      return;
    }
    var rows = ['<div class="map-row head"><span>编号</span><span>类型</span><span>面积</span><span>置信</span><span>中心坐标</span></div>'];
    app.patches.forEach(function (p) {
      var c = p.center_lonlat || [0, 0];
      rows.push('<div class="map-row ' + (app.selected && app.selected.id === p.id ? "on" : "") + '">' +
        '<b>' + esc(p.id) + '</b><span>' + esc(p.class) + '</span>' +
        '<span class="am">' + fmt(p.area_ha) + ' ha</span><span>' + fmt(p.confidence, 2) + '</span>' +
        '<span>' + Number(c[0]).toFixed(4) + 'E / ' + Number(c[1]).toFixed(4) + 'N</span></div>');
    });
    box.innerHTML = rows.join("");
  }

  function markPatch(id) {
    Array.prototype.forEach.call(document.querySelectorAll("#patch-list .patch"), function (node) {
      node.classList.toggle("on", node.getAttribute("data-id") === id);
    });
  }

  function selectPatch(id, initial) {
    if (app.running) return;
    var patch = null;
    for (var i = 0; i < app.patches.length; i++) if (app.patches[i].id === id) patch = app.patches[i];
    if (!patch) return;
    app.selected = patch;
    markPatch(id);
    var keep = app.sortie && app.sortie.patch_id === id && sortieMatches(app.sortie);
    if (!keep) {
      app.sortie = null;
      resetResultPanels();
    }
    sync3D(keep ? app.sortie : null);
    updateKpisForPatch(patch);
    updateParamPanel(keep ? app.sortie : null);
    $("stage-title").textContent = patch.id + " · " + patch.class;
    $("stage-sub").textContent = keep ? stageSub(app.sortie) : ("卫星初筛 " + fmt(patch.area_ha) + " 公顷 · 待精查");
    $("hud-task").textContent = keep ? app.sortie.id : "STANDBY";
    $("hud-mode").textContent = modeLabel(app.mode);
    $("hud-aoi").textContent = patch.center_lonlat ? (patch.center_lonlat[0].toFixed(4) + "°E / " + patch.center_lonlat[1].toFixed(4) + "°N") : "AOI";
    $("status-left").textContent = "PATCH " + patch.id + " / " + patch.class + " / " + fmt(patch.area_ha) + " ha";
    renderMapTable();
    if (!initial) log("选择图斑 " + patch.id + "（" + patch.class + "，" + fmt(patch.area_ha) + " 公顷）");
    syncButton();
  }

  function updateKpisForPatch(patch) {
    setNum("kpi-area", patch.area_ha, 1);
    setNum("kpi-conf", patch.confidence, 2);
    setNum("kpi-gsd", app.overview && app.overview.metrics ? app.overview.metrics.gsd_m : 4.04, 2);
    $("kpi-pose").textContent = "--";
  }

  function updateParamPanel(s) {
    var route = s && s.route;
    var stats = route && route.stats;
    var gsd = s && s.crop ? s.crop.gsd_m : (app.overview && app.overview.metrics ? app.overview.metrics.gsd_m : 4.04);
    $("param-state").textContent = s ? (s.status === "done" ? "已执行" : "已规划") : "待规划";
    $("p-route").textContent = route ? (route.pattern === "perimeter" ? "环绕" : "往复") + " / " + route.spacing_m + " m" : "正射往复";
    $("p-wp").textContent = stats ? stats.waypoints + " 点" : "--";
    $("p-dist").textContent = stats ? fmt(stats.distance_km, 2) + " km" : "--";
    $("p-time").textContent = stats ? fmt(stats.flight_min, 1) + " min" : "--";
    $("p-alt").textContent = route ? Math.round(route.rel_height_m) + " m" : app.height + " m";
    $("src-gsd").textContent = fmt(gsd, 2);
    $("p-note").textContent = s && s.status === "done"
      ? "同机位复拍已完成；面积按 " + fmt(s.crop ? s.crop.gsd_m : 0, 2) + " m GSD 估算。"
      : "面积按影像 GSD 估算，不作为鉴定结论。";
  }

  function sortieMatches(s) {
    return !!s && !!app.selected && s.patch_id === app.selected.id &&
      s.mode === app.mode && Math.round(s.route.rel_height_m) === Math.round(app.height);
  }

  function stageSub(s) {
    if (!s || !s.route) return "等待任务";
    return modeLabel(s.mode) + " · " + s.route.stats.waypoints + " 航点 · 偏差 " +
      fmt(s.route.pose_max_offset_m, 1) + " m";
  }

  // ------------------------------------------------------------------ 3d bridge
  function sync3D(s) {
    if (!app.webgl || !window.AirCase3D) return;
    var patch = s ? s.patch : app.selected;
    var center = patch && patch.center_lonlat ? patch.center_lonlat :
      (app.overview ? app.overview.aoi.center : [109.1, 41.9593]);
    var textureUrl = "/api/aircase/demo/after_hd.png";
    window.AirCase3D.setData({
      center: center,
      patch: patch,
      route: s ? s.route : null,
      textureUrl: textureUrl,
      mode: s ? s.mode : app.mode,
      changedArea: s && s.results ? s.results.changed_area_ha : null,
      confidence: s && s.results ? s.results.confidence : null
    });
  }

  function init3D() {
    if (!window.AirCase3D) {
      show3dFallback("Three.js 未加载");
      return;
    }
    try {
      window.AirCase3D.init($("scene"));
      window.AirCase3D.setOnPhase(onPhase);
      app.webgl = true;
      sync3D(null);
      log("三维态势就绪：程序化地形 + 同机位航线推演", "ok");
    } catch (e) {
      app.webgl = false;
      show3dFallback(e.message);
      log("三维态势不可用，已降级：" + e.message, "warn");
    }
  }

  function show3dFallback(message) {
    var canvas = $("scene");
    if (canvas) canvas.style.display = "none";
    var fallback = $("scene-fallback");
    if (fallback) {
      fallback.classList.remove("hidden");
      fallback.innerHTML = "<div><b>当前浏览器未启用 WebGL</b><br>三维态势不可用，可继续使用同机位对比与现场方位图。<br>" +
        esc(message || "") + "<br>接口与测量意见不受影响。</div>";
    }
  }

  // ------------------------------------------------------------------ run
  function syncModeButtons() {
    Array.prototype.forEach.call(document.querySelectorAll("#mode-seg button"), function (b) {
      b.classList.toggle("on", b.getAttribute("data-mode") === app.mode);
    });
  }

  function updateRouteHint() {
    var text = app.mode === "precise" ? "正射往复 · 80 m 间距" : "环绕 perimeter · 1.2 km";
    $("route-hint").textContent = text + " · " + app.height + " m";
  }

  function syncButton() {
    if (app.running) return;
    var btn = $("run-btn");
    if (!btn) return;
    var s = app.sortie;
    var match = s && sortieMatches(s);
    btn.disabled = !app.selected;
    var verb = app.mode === "scene" ? "生成方位图" : "开始精查";
    if (!app.selected) btn.innerHTML = "请选择图斑 <span>→</span>";
    else if (match && s.status === "done") btn.innerHTML = "重新推演 <span>↻</span>";
    else if (match && s.status !== "done") btn.innerHTML = "继续执行 <span>→</span>";
    else btn.innerHTML = verb + " <span>→</span>";
  }

  function setBusy(text) {
    app.running = true;
    var btn = $("run-btn");
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = esc(text) + ' <span>…</span>';
    }
  }

  function fail(error) {
    app.running = false;
    log("任务失败：" + error.message, "warn");
    toast(error.message, true);
    syncButton();
  }

  async function runFlow() {
    if (app.running || !app.selected) return;
    var s = app.sortie;
    if (s && sortieMatches(s)) {
      if (s.status === "done") { await replay(s); return; }
      await execute(s);
      return;
    }
    setBusy("规划航线…");
    log("创建 " + app.selected.id + " " + modeLabel(app.mode) + "任务（相对航高 " + app.height + " m）");
    try {
      var created = await api("/sortie", {
        method: "POST",
        body: { patch_id: app.selected.id, mode: app.mode, rel_height_m: app.height }
      });
      app.sortie = created;
      log("任务 " + created.id + " 已创建：" + created.route.stats.waypoints + " 航点 / " +
        fmt(created.route.stats.distance_km, 2) + " km / " + fmt(created.route.stats.flight_min, 1) + " min", "ok");
      await execute(created);
    } catch (e) {
      fail(e);
    }
  }

  async function execute(s) {
    app.running = true;
    app.sortie = s;
    var btn = $("run-btn");
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '执行变化检测 <span>…</span>';
    }
    sync3D(s);
    prepareCompare(s);
    updateStage(s);
    updateKpisForSortie(s);
    setPhase(1);
    try {
      var animation = playCinematic();
      var runPromise = api("/sortie/" + s.id + "/run", { method: "POST" });
      var pair = await Promise.all([runPromise, animation]);
      var done = pair[0];
      app.sortie = done;
      applyResults(done);
      toast("精查完成：" + fmt(done.results.changed_area_ha) + " 公顷 · 置信度 " + fmt(done.results.confidence, 2));
    } catch (e) {
      fail(e);
      return;
    }
    app.running = false;
    syncButton();
  }

  async function replay(s) {
    setBusy("推演中…");
    sync3D(s);
    prepareCompare(s);
    updateStage(s);
    updateKpisForSortie(s);
    app.phase = -1;
    setPhase(1);
    await playCinematic();
    applyResults(s);
    app.running = false;
    syncButton();
  }

  function playCinematic() {
    app.phase = -1;
    if (app.webgl && window.AirCase3D && window.AirCase3D.playRun(8400)) {
      return new Promise(function (resolve) { setTimeout(resolve, 8450); });
    }
    return new Promise(function (resolve) {
      var seq = [[1, 0], [2, 1500], [3, 3000], [4, 5600], [5, 7000], [6, 8200]];
      seq.forEach(function (item) {
        setTimeout(function () { onPhase(item[0]); }, item[1]);
      });
      setTimeout(resolve, 8400);
    });
  }

  function updateStage(s) {
    if (!s) return;
    $("stage-title").textContent = s.patch.id + " · " + s.patch.class;
    $("stage-sub").textContent = stageSub(s);
    $("hud-task").textContent = s.id;
    $("hud-mode").textContent = modeLabel(s.mode);
    $("hud-alt").textContent = Math.round(s.route.rel_height_m) + " m";
    $("hud-aoi").textContent = s.patch.center_lonlat[0].toFixed(4) + "°E / " +
      s.patch.center_lonlat[1].toFixed(4) + "°N";
    $("status-task").textContent = s.id;
    $("status-left").textContent = "PATCH " + s.patch.id + " / " + modeLabel(s.mode) + " / " +
      s.route.stats.waypoints + " WP";
    updateParamPanel(s);
  }

  function updateKpisForSortie(s) {
    setNum("kpi-area", s.results ? s.results.changed_area_ha : s.patch.area_ha, 1);
    setNum("kpi-conf", s.results ? s.results.confidence : s.patch.confidence, 2);
    setNum("kpi-gsd", s.crop ? s.crop.gsd_m : 0, 2);
    $("kpi-pose").textContent = s.route ? fmt(s.route.pose_max_offset_m, 1) : "--";
  }

  // ------------------------------------------------------------------ results
  function applyResults(s) {
    if (!s || !s.results) return;
    app.sortie = s;
    renderMapTable();
    var r = s.results;
    setNum("kpi-area", r.changed_area_ha, 1);
    setNum("kpi-conf", r.confidence, 2);
    setNum("kpi-gsd", s.crop ? s.crop.gsd_m : 0, 2);
    $("kpi-pose").textContent = s.route ? fmt(s.route.pose_max_offset_m, 1) : "--";

    $("m-area").textContent = fmt(r.changed_area_ha);
    $("m-conf").textContent = "置信度 " + fmt(r.confidence, 2);
    $("m-conclusion").textContent = s.measure ? s.measure.conclusion : "测量意见已生成。";
    $("m-class").textContent = r.change_class || "--";
    $("m-dl").textContent = (r.delta_lum >= 0 ? "+" : "") + fmt(r.delta_lum, 3);
    $("m-de").textContent = (r.delta_exg >= 0 ? "+" : "") + fmt(r.delta_exg, 3);
    $("m-ratio").textContent = fmt((r.changed_ratio || 0) * 100, 1) + "%";
    $("measure-tag").textContent = "已生成";
    updateParamPanel(s);
    if (app.webgl && window.AirCase3D && window.AirCase3D.setResult) {
      window.AirCase3D.setResult({
        changedArea: r.changed_area_ha,
        confidence: r.confidence,
        mode: s.mode
      });
    }

    renderLegal(s.measure ? s.measure.legal : null);
    renderEvidence(s.evidence);
    renderChain(s.evidence);
    setDownloads(s);

    $("diff-layer").src = r.overlay_url || asset(s, "diff_overlay.png");
    $("diff-toggle").checked = false;
    $("diff-layer").classList.remove("on");

    $("map-img").src = r.scene_map_url || asset(s, "scene_map.png");
    $("map-empty").classList.add("hidden");
    $("map-scroll").classList.remove("hidden");
    $("map-link").href = $("map-img").src;
    $("map-caption").textContent = "现场方位图 · " + s.patch.id + " · 演示制图 · 非测绘成果";

    $("verify-btn").disabled = false;
    $("verify-btn").textContent = "校验哈希与签名";
    $("verify-result").textContent = "";
    $("verify-result").className = "verify-result";
    $("stage-sub").textContent = s.patch.id + " · " + modeLabel(s.mode) + "完成 · " +
      fmt(r.changed_area_ha) + " 公顷 / 置信度 " + fmt(r.confidence, 2);
    $("hud-task").textContent = "DONE";
    $("hud-link").textContent = "证据包已签名";
    $("status-task").textContent = s.id;
    setPhase(6);
    app.phase = 6;
    syncButton();
  }

  function renderLegal(legal) {
    var el = $("legal-list");
    var items = legal && legal.items ? legal.items : [];
    if (!items.length) {
      el.innerHTML = '<div class="empty">生成意见后检索知识库</div>';
      return;
    }
    el.innerHTML = items.slice(0, 2).map(function (item) {
      return '<div class="legal"><b>' + esc(item.heading || item.article || "") + '</b>' +
        '<small>' + esc(item.law || "") + ' · ' + esc(item.article || "") + '</small>' +
        '<p>' + esc(item.summary || "") + '</p></div>';
    }).join("") + (items.length > 2 ? '<div class="legal-more">另有 ' + (items.length - 2) + ' 条见测量意见</div>' : "");
  }

  function renderEvidence(evidence) {
    var el = $("evidence-list");
    var files = evidence && evidence.files ? evidence.files : [];
    if (!files.length) {
      el.innerHTML = '<div class="empty">尚未生成</div>';
      $("evidence-count").textContent = "--";
      return;
    }
    $("evidence-count").textContent = files.length + " FILES";
    el.innerHTML = files.map(function (file) {
      return '<div class="ev" data-name="' + esc(file.name) + '"><i></i><span>' +
        esc(file.name) + '</span><em>' + fmtBytes(file.bytes) + '</em></div>';
    }).join("");
  }

  function renderChain(evidence) {
    var el = $("chain");
    if (!evidence) {
      el.innerHTML = '<div class="empty">执行精查后生成逐文件哈希与 HMAC 签名</div>';
      $("chain-state").textContent = "未签名";
      return;
    }
    var rows = [
      ["root", evidence.root_sha256],
      ["HMAC", evidence.signature],
      ["key", evidence.key_id]
    ];
    el.innerHTML = rows.map(function (row) {
      return '<div class="chain-row"><i></i><span>' + esc(row[0]) + '</span><b>' +
        esc(String(row[1] || "").slice(0, 22)) + '…</b></div>';
    }).join("");
    $("chain-state").textContent = "已签名";
  }

  function setDownloads(s) {
    var base = API + "/sortie/" + s.id;
    var md = $("dl-md");
    md.href = base + "/measure?format=md";
    md.setAttribute("download", "measure_" + s.id + ".md");
    md.target = "_blank";
    md.classList.remove("disabled");
    var html = $("dl-html");
    html.href = base + "/measure?format=html";
    html.target = "_blank";
    html.classList.remove("disabled");
    var map = $("dl-map");
    map.href = s.results.scene_map_url || asset(s, "scene_map.png");
    map.target = "_blank";
    map.classList.remove("disabled");
  }

  function resetResultPanels() {
    $("m-area").textContent = "--";
    $("m-conf").textContent = "置信度 --";
    $("m-conclusion").textContent = "选择图斑并开始精查，生成《变化测量意见》。";
    $("m-class").textContent = "--";
    $("m-dl").textContent = "--";
    $("m-de").textContent = "--";
    $("m-ratio").textContent = "--";
    $("measure-tag").textContent = "待生成";
    $("legal-list").innerHTML = '<div class="empty">生成意见后检索知识库</div>';
    $("evidence-list").innerHTML = '<div class="empty">尚未生成</div>';
    $("evidence-count").textContent = "--";
    $("chain").innerHTML = '<div class="empty">待生成根哈希与签名</div>';
    $("chain-state").textContent = "未签名";
    $("verify-btn").disabled = true;
    $("verify-result").textContent = "";
    $("verify-result").className = "verify-result";
    ["dl-md", "dl-html", "dl-map"].forEach(function (id) {
      var a = $(id);
      a.classList.add("disabled");
      a.removeAttribute("href");
      a.removeAttribute("download");
      a.removeAttribute("target");
    });
    $("compare-before").removeAttribute("src");
    $("compare-after").removeAttribute("src");
    $("diff-layer").removeAttribute("src");
    $("diff-layer").classList.remove("on");
    $("diff-toggle").checked = false;
    $("compare-empty").classList.remove("hidden");
    setCompare(50);
    $("compare-range").value = 50;
    $("map-img").removeAttribute("src");
    $("map-scroll").classList.add("hidden");
    $("map-empty").classList.remove("hidden");
    $("map-link").removeAttribute("href");
    $("map-caption").textContent = "演示制图 · 非测绘成果";
    $("hud-task").textContent = "STANDBY";
    $("hud-link").textContent = "链路待命";
    $("status-task").textContent = "NO TASK";
    updateParamPanel(null);
  }

  // ------------------------------------------------------------------ compare / map
  function setCompare(pos) {
    var p = clamp(pos, 0, 100);
    var after = $("compare-after");
    var divider = $("compare-divider");
    if (after) after.style.clipPath = "inset(0 " + (100 - p) + "% 0 0)";
    if (divider) divider.style.left = p + "%";
  }

  function prepareCompare(s) {
    if (!s || !s.files) return;
    $("compare-before").src = asset(s, s.files.before || "before_uav.png");
    $("compare-after").src = asset(s, s.files.after || "after_uav.png");
    $("compare-empty").classList.add("hidden");
    var images = (app.overview && app.overview.images) || {};
    $("tag-before-date").textContent = "基线 · " + (images.before ? images.before.date : "2023-10-08");
    $("tag-after-date").textContent = "复拍 · " + (images.after ? images.after.date : "模拟时相");
    $("compare-range").value = 50;
    setCompare(50);
  }

  // ------------------------------------------------------------------ verify
  async function verifyEvidence() {
    var s = app.sortie;
    if (!s || s.status !== "done" || app.running) return;
    var btn = $("verify-btn");
    btn.disabled = true;
    btn.textContent = "校验中…";
    $("verify-result").textContent = "";
    var ok = false;
    var fileCount = 0;
    try {
      var r = await api("/sortie/" + s.id + "/verify", { method: "POST" });
      ok = !!r.verified;
      fileCount = (r.files || []).length;
      var result = $("verify-result");
      result.className = "verify-result " + (ok ? "ok" : "bad");
      result.textContent = ok
        ? "校验通过 · " + (r.files || []).length + " 个文件 · root " + String(r.root_sha256).slice(0, 10) + "… · key " + r.key_id
        : "校验失败：文件、根哈希或签名不一致";
      Array.prototype.forEach.call(document.querySelectorAll("#evidence-list .ev"), function (el) {
        var file = findFile(r.files, el.getAttribute("data-name"));
        el.classList.toggle("ok", !!(file && file.ok));
        el.classList.toggle("bad", !!(file && !file.ok));
      });
      Array.prototype.forEach.call(document.querySelectorAll("#chain .chain-row[data-file]"), function (el) {
        var file = findFile(r.files, el.getAttribute("data-file"));
        el.classList.toggle("ok", !!(file && file.ok));
        el.classList.toggle("bad", !!(file && !file.ok));
      });
      log(ok ? "证据校验通过：文件、根哈希与 HMAC 签名一致" : "证据校验失败", ok ? "ok" : "warn");
      toast(ok ? "证据链校验通过" : "证据链校验失败", !ok);
    } catch (e) {
      $("verify-result").className = "verify-result bad";
      $("verify-result").textContent = "校验失败：" + e.message;
      log("证据校验失败：" + e.message, "warn");
      toast("校验失败：" + e.message, true);
    }
    btn.disabled = false;
    btn.textContent = ok ? "校验通过 · " + fileCount + " 文件" : "校验失败 · 请查看状态";
  }
  function findFile(files, name) {
    if (!files) return null;
    for (var i = 0; i < files.length; i++) if (files[i].name === name) return files[i];
    return null;
  }

  // ------------------------------------------------------------------ recent
  function renderRecent(recent) {
    var el = $("recent-list");
    if (!recent || !recent.length) {
      el.innerHTML = '<div class="empty">暂无任务</div>';
      return;
    }
    el.innerHTML = recent.slice(0, 8).map(function (item) {
      var time = "";
      try {
        time = new Date(item.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
      } catch (e) { /* ignore */ }
      return '<div class="recent" data-id="' + esc(item.id) + '"><b>' + esc(item.patch_id) + '</b>' +
        '<span>' + esc(modeLabel(item.mode)) + ' · ' + fmt(item.area_ha) + ' ha</span>' +
        '<em class="' + (item.status === "done" ? "ok" : "") + '">' + esc(item.status === "done" ? "DONE" : "READY") + ' ' + time + '</em></div>';
    }).join("");
    Array.prototype.forEach.call(el.querySelectorAll(".recent"), function (node) {
      node.addEventListener("click", function () { loadSortie(node.getAttribute("data-id")); });
    });
  }

  async function loadSortie(id) {
    if (app.running) return;
    try {
      var s = await api("/sortie/" + id);
      app.sortie = s;
      app.selected = null;
      for (var i = 0; i < app.patches.length; i++) if (app.patches[i].id === s.patch_id) app.selected = app.patches[i];
      if (app.selected) markPatch(app.selected.id);
      app.mode = s.mode;
      syncModeButtons();
      app.height = Math.round(s.route.rel_height_m);
      $("height").value = app.height;
      $("height-val").textContent = app.height + " m";
      updateRouteHint();
      resetResultPanels();
      sync3D(s);
      updateStage(s);
      prepareCompare(s);
      updateKpisForSortie(s);
      if (s.status === "done") {
        applyResults(s);
        log("载入已完成任务 " + s.id + "（" + s.patch_id + " / " + modeLabel(s.mode) + "）", "ok");
      } else {
        $("stage-sub").textContent = "任务已规划 · 等待执行";
        log("载入待执行任务 " + s.id + "（" + s.patch_id + "）");
        syncButton();
      }
    } catch (e) {
      toast("载入失败：" + e.message, true);
      log("载入失败：" + e.message, "warn");
    }
  }

  // ------------------------------------------------------------------ views
  function setView(view) {
    app.view = view;
    $("scene-wrap").classList.toggle("hidden", view !== "3d");
    $("compare-wrap").classList.toggle("hidden", view !== "compare");
    $("map-wrap").classList.toggle("hidden", view !== "map");
    Array.prototype.forEach.call(document.querySelectorAll("#view-tabs button"), function (b) {
      b.classList.toggle("on", b.getAttribute("data-view") === view);
    });
    if (view === "3d" && app.webgl && window.AirCase3D) {
      setTimeout(function () { window.AirCase3D.resize(); }, 40);
    }
  }

  function onParamsChanged() {
    if (app.sortie && app.selected && !sortieMatches(app.sortie)) {
      app.sortie = null;
      resetResultPanels();
      sync3D(null);
      updateKpisForPatch(app.selected);
      $("stage-sub").textContent = "参数已修改 · 等待重新执行";
    }
    $("hud-alt").textContent = app.height + " m";
    updateRouteHint();
    syncButton();
  }

  // ------------------------------------------------------------------ bind
  function bind() {
    $("run-btn").addEventListener("click", runFlow);
    Array.prototype.forEach.call(document.querySelectorAll("#mode-seg button"), function (btn) {
      btn.addEventListener("click", function () {
        if (app.running) return;
        var mode = btn.getAttribute("data-mode");
        if (mode === app.mode) return;
        app.mode = mode;
        syncModeButtons();
        onParamsChanged();
      });
    });
    $("height").addEventListener("input", function (event) {
      if (app.running) return;
      app.height = Number(event.target.value);
      $("height-val").textContent = app.height + " m";
      onParamsChanged();
    });
    Array.prototype.forEach.call(document.querySelectorAll("#view-tabs button"), function (btn) {
      btn.addEventListener("click", function () { setView(btn.getAttribute("data-view")); });
    });
    Array.prototype.forEach.call(document.querySelectorAll("#cam-btns button"), function (btn) {
      btn.addEventListener("click", function () {
        Array.prototype.forEach.call(document.querySelectorAll("#cam-btns button"), function (b) { b.classList.remove("on"); });
        btn.classList.add("on");
        if (app.webgl && window.AirCase3D) window.AirCase3D.setCamera(btn.getAttribute("data-cam"));
      });
    });
    $("compare-range").addEventListener("input", function (event) { setCompare(Number(event.target.value)); });
    $("diff-toggle").addEventListener("change", function (event) {
      $("diff-layer").classList.toggle("on", event.target.checked);
    });
    $("verify-btn").addEventListener("click", verifyEvidence);
    window.addEventListener("resize", function () {
      if (app.webgl && window.AirCase3D) window.AirCase3D.resize();
    });
  }

  // ------------------------------------------------------------------ init
  async function init() {
    bind();
    tickClock();
    setInterval(tickClock, 1000);
    updateRouteHint();
    syncButton();
    log("控制台初始化…");
    try {
      var overview = await api("/overview");
      app.overview = overview;
      app.patches = overview.patches || [];
      $("patch-count").textContent = app.patches.length + " PATCH";
      renderPatches();
      renderRecent(overview.recent || []);
      if (app.patches.length) selectPatch(app.patches[0].id, true);
      else resetResultPanels();
      log("AOI 加载完成：" + overview.aoi.name + "（" + overview.aoi.size_km.join(" × ") + " km）", "ok");
      log("卫星初筛 " + app.patches.length + " 个图斑，合计 " + fmt(overview.metrics.total_area_ha) + " 公顷");
      setPhase(0);
      init3D();
      var recentList = overview.recent || [];
      var latest = null;
      for (var ri = 0; ri < recentList.length; ri++) {
        if (recentList[ri].status === "done") { latest = recentList[ri]; break; }
      }
      if (!latest && recentList.length) latest = recentList[0];
      if (latest) setTimeout(function () { loadSortie(latest.id); }, 80);
    } catch (e) {
      log("初始化失败：" + e.message, "warn");
      toast("后端接口不可用：" + e.message, true);
      $("patch-list").innerHTML = '<div class="empty">接口不可用</div>';
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
/* 天枢 6G 演示页交互：原生 JS，无构建链 */
(function () {
  "use strict";

  var state = {
    scenario: "outage",
    overview: null,
    network: null,
    demo: null,
    chart: null,
    view: "overlay",
    selectedPatch: null,
    sandbox: { outage_at_s: 10, slice_quota_pct: 100, uav_enabled: true },
    toastTimer: 0,
  };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmt(value, digits) {
    var number = Number(value);
    if (!isFinite(number)) return "--";
    return number.toFixed(digits == null ? 1 : digits);
  }

  function sizeText(mb) {
    var number = Number(mb);
    if (!isFinite(number)) return "--";
    return number < 0.1 ? fmt(number * 1024, 1) + " KB" : fmt(number, 1) + " MB";
  }

  async function api(path, options) {
    var opts = Object.assign({
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
    }, options || {});
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    var response = await fetch(path, opts);
    var type = response.headers.get("content-type") || "";
    if (!response.ok) {
      var message = "请求失败（" + response.status + "）";
      if (type.indexOf("application/json") >= 0) {
        var detail = await response.json().catch(function () { return null; });
        if (detail && typeof detail.detail === "string") message = detail.detail;
      } else if (response.status === 401) {
        message = "访问口令已失效，请重新进入";
        window.setTimeout(function () { window.location.href = "/__gate?next=/sixg"; }, 900);
      }
      throw new Error(message);
    }
    if (type.indexOf("application/json") < 0) throw new Error("接口返回格式异常");
    return response.json();
  }

  function toast(text) {
    var node = $("toast");
    if (!node) return;
    node.textContent = text;
    node.hidden = false;
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(function () { node.hidden = true; }, 2600);
  }

  function loading(show) {
    var node = $("loading");
    if (node) node.hidden = !show;
  }

  function setSplit(value) {
    var compare = $("compare");
    if (!compare) return;
    var pct = Math.max(0, Math.min(100, Number(value) || 0));
    compare.style.setProperty("--split", pct + "%");
  }

  function initInfoTips() {
    var pop = document.createElement("div");
    pop.className = "info-pop";
    pop.id = "info-pop";
    pop.hidden = true;
    pop.setAttribute("role", "dialog");
    pop.innerHTML = '<div class="info-pop-head"><strong></strong><button class="info-pop-close" type="button" aria-label="关闭说明">×</button></div><p class="info-pop-body"></p>';
    document.body.appendChild(pop);

    var title = pop.querySelector("strong");
    var body = pop.querySelector(".info-pop-body");
    var closeBtn = pop.querySelector(".info-pop-close");
    var active = null;

    function close() {
      if (active) active.setAttribute("aria-expanded", "false");
      active = null;
      pop.hidden = true;
    }

    function place(clientX, clientY, fallback) {
      pop.style.left = "0px";
      pop.style.top = "0px";
      var rect = pop.getBoundingClientRect();
      var x = clientX || (fallback.left + fallback.width / 2);
      var y = clientY || fallback.bottom;
      var pad = 12;
      var left = x + 12;
      var top = y + 14;
      if (left + rect.width > window.innerWidth - pad) left = x - rect.width - 12;
      if (top + rect.height > window.innerHeight - pad) top = y - rect.height - 14;
      pop.style.left = Math.max(pad, left) + "px";
      pop.style.top = Math.max(pad, top) + "px";
    }

    Array.prototype.forEach.call(document.querySelectorAll("[data-info]"), function (btn) {
      btn.setAttribute("aria-expanded", "false");
    });

    document.addEventListener("click", function (event) {
      var btn = event.target.closest ? event.target.closest("[data-info]") : null;
      if (btn) {
        event.preventDefault();
        event.stopPropagation();
        if (active === btn) { close(); return; }
        close();
        active = btn;
        btn.setAttribute("aria-expanded", "true");
        title.textContent = btn.getAttribute("data-info-title") || "说明";
        body.textContent = btn.getAttribute("data-info") || "";
        pop.hidden = false;
        place(event.clientX, event.clientY, btn.getBoundingClientRect());
        return;
      }
      if (event.target.closest && event.target.closest(".info-pop")) return;
      close();
    }, true);

    closeBtn.addEventListener("click", function (event) {
      event.stopPropagation();
      close();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") close();
    });

    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
  }

  function sandboxValues() {
    return {
      outage_at_s: Number($("sb-outage") ? $("sb-outage").value : 10),
      slice_quota_pct: Number($("sb-quota") ? $("sb-quota").value : 100),
      uav_enabled: $("sb-uav") ? $("sb-uav").checked : true,
    };
  }

  function syncSandboxLabels() {
    var outage = $("sb-outage");
    var quota = $("sb-quota");
    var outageVal = $("sb-outage-val");
    var quotaVal = $("sb-quota-val");
    if (outage && outageVal) outageVal.textContent = "T+" + outage.value + "s";
    if (quota && quotaVal) quotaVal.textContent = quota.value + "%";
  }

  function openSandbox() {
    var modal = $("sandbox-modal");
    if (!modal) return;
    if ($("sb-outage")) $("sb-outage").value = state.sandbox.outage_at_s;
    if ($("sb-quota")) $("sb-quota").value = state.sandbox.slice_quota_pct;
    if ($("sb-uav")) $("sb-uav").checked = state.sandbox.uav_enabled !== false;
    syncSandboxLabels();
    modal.hidden = false;
  }

  function closeSandbox() {
    var modal = $("sandbox-modal");
    if (modal) modal.hidden = true;
  }

  function applySandbox() {
    state.sandbox = sandboxValues();
    closeSandbox();
    runDemo(state.scenario).catch(function (error) { toast(error.message); });
  }

  function bindUI() {
    var range = $("cmp-range");
    if (range) {
      range.addEventListener("input", function () { setSplit(range.value); });
      setSplit(range.value);
    }

    var grid = $("scenario-grid");
    if (grid) {
      grid.addEventListener("click", function (event) {
        var card = event.target.closest("[data-scenario]");
        if (!card) return;
        var id = card.getAttribute("data-scenario");
        if (!id || id === state.scenario) return;
        selectScenario(id);
        runDemo(id).catch(function (error) { toast(error.message); });
      });
    }

    var run = $("run-demo");
    if (run) {
      run.addEventListener("click", function () {
        runDemo(state.scenario).catch(function (error) { toast(error.message); });
      });
    }

    var openSb = $("open-sandbox");
    if (openSb) openSb.addEventListener("click", openSandbox);
    var closeSb = $("close-sandbox");
    if (closeSb) closeSb.addEventListener("click", closeSandbox);
    var applySb = $("apply-sandbox");
    if (applySb) applySb.addEventListener("click", applySandbox);
    var sbModal = $("sandbox-modal");
    if (sbModal) {
      sbModal.addEventListener("click", function (event) {
        if (event.target === sbModal) closeSandbox();
      });
    }
    ["sb-outage", "sb-quota"].forEach(function (id) {
      var input = $(id);
      if (input) input.addEventListener("input", syncSandboxLabels);
    });

    var tools = $("vision-tools");
    if (tools) {
      tools.addEventListener("click", function (event) {
        var chip = event.target.closest("[data-view]");
        if (!chip) return;
        setView(chip.getAttribute("data-view"));
      });
    }

    var patches = $("patch-list");
    if (patches) {
      patches.addEventListener("click", function (event) {
        var row = event.target.closest("[data-patch]");
        if (!row) return;
        selectPatch(row.getAttribute("data-patch"));
      });
    }

    var download = $("download-report");
    if (download) download.addEventListener("click", downloadReport);

    var view = $("view-report");
    if (view) view.addEventListener("click", openReport);

    var close = $("close-report");
    if (close) close.addEventListener("click", closeReport);

    var modal = $("report-modal");
    if (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) closeReport();
      });
    }

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { closeReport(); closeSandbox(); }
    });

    window.addEventListener("resize", function () {
      if (state.chart) state.chart.resize();
    });
  }

  function renderScenarios(list) {
    var grid = $("scenario-grid");
    if (!grid) return;
    grid.innerHTML = (list || []).map(function (item) {
      return '<button class="scenario-card' + (item.id === state.scenario ? " active" : "") +
        '" type="button" data-scenario="' + esc(item.id) + '">' +
        "<b>" + esc(item.label) + "</b>" +
        "<small>" + esc(item.code) + "</small></button>";
    }).join("");
    updateScenarioDesc();
  }

  function updateScenarioDesc() {
    var node = $("scenario-desc");
    if (!node || !state.overview) return;
    var list = state.overview.scenarios || [];
    var current = null;
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].id === state.scenario) { current = list[i]; break; }
    }
    node.textContent = current ? current.desc : "选择场景后启动全链路推演。";
    var cards = document.querySelectorAll("[data-scenario]");
    for (var j = 0; j < cards.length; j += 1) {
      cards[j].classList.toggle("active", cards[j].getAttribute("data-scenario") === state.scenario);
    }
  }

  function selectScenario(id) {
    state.scenario = id;
    updateScenarioDesc();
  }

  function renderNetwork(network) {
    state.network = network;
    var stateTag = $("net-state");
    if (stateTag) {
      stateTag.textContent = network.label + " · 仿真";
      stateTag.className = "panel-tag " + (network.scenario === "outage" ? "bad" : (network.scenario === "congestion" ? "warn" : "live"));
    }

    var kpi = network.kpi || {};
    var cards = [
      { label: "平均时延", value: fmt(kpi.avg_latency_ms, 1), unit: "ms", tone: kpi.avg_latency_ms > 120 ? "warn" : "" },
      { label: "峰值时延", value: fmt(kpi.peak_latency_ms, 1), unit: "ms", tone: kpi.peak_latency_ms > 220 ? "warn" : "" },
      { label: "下行带宽", value: fmt(kpi.avg_down_mbps, 1), unit: "Mbps", tone: "ok" },
      { label: "链路可用率", value: fmt(kpi.link_availability_pct, 2), unit: "%", tone: kpi.link_availability_pct < 99 ? "warn" : "ok" },
      { label: "地面网在线", value: fmt(kpi.ground_uptime_pct, 1), unit: "%", tone: kpi.ground_uptime_pct < 80 ? "warn" : "" },
      { label: "边缘负载", value: fmt(kpi.avg_edge_load_pct, 1), unit: "%", tone: kpi.avg_edge_load_pct > 75 ? "warn" : "" },
    ];
    var box = $("net-kpis");
    if (box) {
      box.innerHTML = cards.map(function (item) {
        return '<div class="kpi"><label>' + esc(item.label) + "</label><b class=\"" + item.tone + "\">" +
          esc(item.value) + "<em>" + esc(item.unit) + "</em></b></div>";
      }).join("");
    }

    renderChart(network.series || []);
    renderSlices(network.slices || {});
    renderTopology(network.topology || {}, network.scenario);
    var tag = $("timeline-scenario");
    if (tag) tag.textContent = network.label;
  }

  function renderChart(series) {
    var node = $("net-chart");
    if (!node || !window.echarts) return;
    if (!state.chart) state.chart = window.echarts.init(node, null, { renderer: "canvas" });
    var labels = series.map(function (row) { return "T+" + row.t + "s"; });
    state.chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 420,
      grid: { left: 34, right: 34, top: 18, bottom: 18 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(6, 18, 29, 0.94)",
        borderColor: "#2a5574",
        textStyle: { color: "#d9eefb", fontSize: 10 },
        axisPointer: { lineStyle: { color: "#2f6f92" } },
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: labels,
        axisLine: { lineStyle: { color: "#1d3b52" } },
        axisTick: { show: false },
        axisLabel: { color: "#5f8099", fontSize: 8, interval: 4 },
      },
      yAxis: [
        {
          type: "value",
          name: "ms",
          nameTextStyle: { color: "#557b96", fontSize: 8 },
          axisLabel: { color: "#557b96", fontSize: 8 },
          splitLine: { lineStyle: { color: "rgba(36, 80, 110, 0.28)" } },
        },
        {
          type: "value",
          name: "Mbps",
          nameTextStyle: { color: "#557b96", fontSize: 8 },
          axisLabel: { color: "#557b96", fontSize: 8 },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "时延",
          type: "line",
          smooth: true,
          symbol: "none",
          lineStyle: { width: 1.8, color: "#38e1ff" },
          areaStyle: {
            color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(56, 225, 255, 0.28)" },
              { offset: 1, color: "rgba(56, 225, 255, 0.01)" },
            ]),
          },
          data: series.map(function (row) { return row.latency_ms; }),
        },
        {
          name: "下行",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 1.4, color: "#38e6a4", type: "dashed" },
          data: series.map(function (row) { return row.down_mbps; }),
        },
      ],
    }, true);
    // 容器高度由 flex 与上方 KPI 共同决定，渲染完再对齐一次画布尺寸
    window.requestAnimationFrame(function () {
      if (state.chart) state.chart.resize();
    });
  }

  function renderSlices(slices) {
    var box = $("slice-bars");
    if (!box) return;
    var rows = Object.keys(slices).map(function (key) {
      var value = Number(slices[key]) || 0;
      return '<div class="slice-row"><span>' + esc(key) + '</span>' +
        '<div class="slice-track"><i style="width:' + Math.max(2, Math.min(100, value)) + '%"></i></div>' +
        "<em>" + value + "%</em></div>";
    });
    box.innerHTML = rows.join("");
  }

  function setNode(id, status) {
    var node = $(id);
    if (!node) return;
    node.classList.remove("active", "offline", "busy");
    if (status === "offline") node.classList.add("offline");
    else if (status === "busy") node.classList.add("busy");
    else if (status === "online" || status === "active") node.classList.add("active");
  }

  function setLink(id, status) {
    var link = $(id);
    if (!link) return;
    link.classList.remove("active", "offline", "standby");
    if (status === "offline") link.classList.add("offline");
    else if (status === "active" || status === "online") link.classList.add("active");
    else link.classList.add("standby");
  }

  function renderTopology(topology, scenario) {
    setNode("node-bs", topology.base_station);
    setNode("node-sat", topology.satellite);
    setNode("node-edge", topology.edge);
    setNode("node-uav", topology.uav);
    setNode("node-command", topology.command);

    setLink("link-bs", topology.base_station === "offline" ? "offline" : "active");
    setLink("link-sat", topology.satellite === "active" ? "active" : "standby");
    setLink("link-uav", topology.uav === "offline" ? "offline" : "active");
    setLink("link-command", topology.command === "offline" ? "offline" : "active");

    if (window.Tianshu3D && typeof window.Tianshu3D.update === "function") {
      window.Tianshu3D.update(topology, scenario);
    }

    var note = $("topo-note");
    if (!note) return;
    if (scenario === "outage") note.textContent = "地面 6G 中断，业务切换低轨卫星 NTN；边缘节点持续推理，指挥链路保持。";
    else if (scenario === "handover") note.textContent = "低轨卫星波束切换，URLLC 短时抖动；切换完成后主链路恢复。";
    else if (scenario === "congestion") note.textContent = "eMBB 拥塞，切片按优先级重调度；URLLC 指挥控制保底。";
    else note.textContent = "主链路：地面 6G；备份：低轨卫星 NTN；无人机与边缘节点协同。";
  }


  function renderPlan(plan) {
    if (!plan) return;
    var slice = $("plan-slice");
    var tiles = $("plan-tiles");
    var latency = $("plan-latency");
    var lead = $("plan-lead");
    var note = $("plan-note");
    var mode = plan.ai_mode || {};
    var feedback = plan.feedback || {};
    var payload = plan.payload || {};
    if (slice) slice.textContent = String(plan.slice || "--").replace(" + ", "+");
    if (tiles) {
      var extra = Number(plan.extra_tiles) || 0;
      tiles.innerHTML = esc(plan.tiles_sent) + (extra ? "+" + extra : "") + "<em>/" + esc(plan.tiles_total) + "</em>";
    }
    if (latency) latency.innerHTML = fmt(plan.end_to_end_ms, 0) + "<em>ms</em>";
    if (lead) lead.innerHTML = esc(plan.patch_count) + "<em>条</em>";

    var modeNode = $("ai-mode");
    if (modeNode) modeNode.textContent = "AI · " + (mode.short || mode.label || "--");
    var verifyNode = $("ai-verified");
    if (verifyNode) {
      if (feedback.granted) {
        verifyNode.textContent = "回馈 · 补传 " + feedback.extra_tiles + " 块";
        verifyNode.className = "ai-sub ok";
      } else {
        verifyNode.textContent = "复核 · " + fmt(plan.compute_ms, 0) + " ms";
        verifyNode.className = "ai-sub" + (feedback.status === "pending" ? " warn" : "");
      }
    }
    var semNode = $("semantic-note");
    if (semNode) {
      semNode.textContent = payload.mode === "semantic"
        ? "语义 " + fmt(payload.feature_kb, 1) + "KB · 1:" + payload.compression_ratio
        : "像素 " + fmt(plan.uplink_mb, 1) + "MB · 语义可压 " + fmt(payload.feature_kb, 1) + "KB";
      semNode.title = payload.note || "";
    }

    var saved = $("plan-saved");
    if (saved) {
      saved.textContent = "节省 " + fmt(plan.bandwidth_saved_pct, plan.bandwidth_saved_pct >= 99.5 ? 1 : 0) + "% 带宽";
      saved.classList.toggle("ok", plan.bandwidth_saved_pct > 0);
    }
    if (note) {
      note.textContent = plan.note + " 上行 " + sizeText(plan.uplink_mb) + "、推理 " +
        fmt(plan.compute_ms, 0) + " ms。";
      note.title = feedback.note || "";
      note.classList.toggle("warn", plan.missed_patches > 0 || feedback.status === "pending");
    }
  }

  function renderDetection(detection) {
    var metrics = detection.metrics || {};
    var box = $("vision-metrics");
    if (box) {
      box.innerHTML = [
        { label: "AI 图斑", value: metrics.ai_patches, unit: "个", tone: "cyan" },
        { label: "高置信", value: metrics.high_confidence, unit: "个", tone: "green" },
        { label: "ROI 复核", value: metrics.roi_verified == null ? "--" : metrics.roi_verified, unit: "个", tone: "amber" },
        { label: "推理耗时", value: metrics.inference_ms, unit: "ms", tone: "cyan" },
      ].map(function (item) {
        return '<div class="vm"><label>' + esc(item.label) + '</label><b class="' + item.tone + '">' +
          esc(item.value == null ? "--" : item.value) + "<em>" + esc(item.unit) + "</em></b></div>";
      }).join("");
    }

    var note = $("vision-note");
    if (note) {
      note.textContent = "规则基线 " + (metrics.baseline_patches == null ? "--" : metrics.baseline_patches) +
        " 个 → AI 保留 " + (metrics.ai_patches == null ? "--" : metrics.ai_patches) +
        " 个（ROI 复核 " + (metrics.roi_verified == null ? "--" : metrics.roi_verified) +
        " 个），抑制光照/物候疑似误报 " + (metrics.suppressed == null ? "--" : metrics.suppressed) +
        " 个；候选总面积 " + fmt(metrics.total_area_ha, 1) + " 公顷，阈值 " + fmt(metrics.threshold, 3) +
        "。图斑为算法候选，需人工复核。";
    }

    var overlay = $("img-overlay");
    if (overlay) {
      overlay.setAttribute("data-overlay", detection.overlay_url || "/api/sixg/asset/after_overlay.png");
      overlay.setAttribute("data-heatmap", detection.heatmap_url || "/api/sixg/asset/diff_heatmap.png");
    }
    setView(state.view);
    renderPatches(detection.patches || []);
  }

  function setView(view) {
    state.view = view || "overlay";
    var overlay = $("img-overlay");
    var chips = document.querySelectorAll("[data-view]");
    for (var i = 0; i < chips.length; i += 1) {
      chips[i].classList.toggle("active", chips[i].getAttribute("data-view") === state.view);
    }
    if (!overlay) return;
    if (state.view === "raw") {
      overlay.hidden = true;
      return;
    }
    var src = state.view === "heatmap" ? overlay.getAttribute("data-heatmap") : overlay.getAttribute("data-overlay");
    if (!src) return;
    overlay.hidden = false;
    overlay.src = src + (src.indexOf("?") >= 0 ? "&" : "?") + "t=" + Date.now();
  }

  function patchTone(name) {
    var text = String(name || "");
    if (text.indexOf("裸土") >= 0 || text.indexOf("建设") >= 0) return "bare";
    return "mine";
  }

  function renderPatches(patches) {
    var box = $("patch-list");
    var count = $("patch-count");
    if (count) count.textContent = (patches || []).length + " 个";
    if (!box) return;
    if (!patches || !patches.length) {
      box.innerHTML = '<p class="empty">未检出候选图斑</p>';
      return;
    }
    box.innerHTML = patches.map(function (row) {
      return '<div class="patch-row' + (state.selectedPatch === row.id ? " selected" : "") +
        '" data-patch="' + esc(row.id) + '">' +
        '<span class="patch-id">' + esc(row.id) + "</span>" +
        '<span class="patch-class ' + patchTone(row.class) + '"><i></i><span>' + esc(row.class) + "</span></span>" +
        '<span class="patch-conf">' + fmt(row.confidence, 2) + "</span>" +
        '<span class="patch-area">' + fmt(row.area_ha, 1) + " ha</span></div>";
    }).join("");
  }

  function selectPatch(id) {
    state.selectedPatch = id;
    var rows = document.querySelectorAll("[data-patch]");
    for (var i = 0; i < rows.length; i += 1) {
      rows[i].classList.toggle("selected", rows[i].getAttribute("data-patch") === id);
    }
    var patches = (state.demo && state.demo.detection && state.demo.detection.patches) ||
      (state.overview && state.overview.detection && state.overview.detection.patches) || [];
    var target = null;
    for (var j = 0; j < patches.length; j += 1) {
      if (patches[j].id === id) { target = patches[j]; break; }
    }
    if (!target) return;
    var note = $("vision-note");
    if (note) {
      note.textContent = target.id + " · " + target.class + " · 约 " + fmt(target.area_ha, 1) +
        " 公顷 · 置信度 " + fmt(target.confidence, 2) + " · " + (target.change || "") +
        " · 中心 " + fmt(target.center_lonlat && target.center_lonlat[0], 5) + ", " +
        fmt(target.center_lonlat && target.center_lonlat[1], 5) + "。";
    }
  }

  function renderTimeline(items) {
    var box = $("timeline");
    if (!box) return;
    var phaseClass = {
      "网络": "phase-network",
      "6G": "phase-network",
      "感知": "phase-vision",
      "AI": "phase-ai",
      "案件": "phase-case",
      "法条": "phase-case",
      "报告": "phase-report",
    };
    box.innerHTML = (items || []).map(function (item, index) {
      var delay = Math.min(index * 90, 900);
      return '<li class="ts-step ' + (phaseClass[item.phase] || "") + '" style="animation-delay:' + delay + 'ms">' +
        '<span class="t-time">T+' + esc(item.t) + "s · " + esc(item.phase) + "</span>" +
        '<span class="t-title">' + esc(item.title) + "</span>" +
        '<span class="t-desc">' + esc(item.detail) + "</span></li>";
    }).join("");
    box.scrollTop = 0;
  }

  function renderCase(data) {
    var box = $("case-box");
    var tag = $("case-status");
    if (!box) return;
    var item = data.case || {};
    var legal = (item.legal || []).slice(0, 3);
    var legalHtml = legal.map(function (law) {
      return "<li><b>" + esc(law.law || "") + esc(law.article || "") + "</b> " + esc(law.heading || "") + "</li>";
    }).join("");
    box.innerHTML =
      '<p class="case-no">' + esc(item.case_no || "SIXG-DEMO") + "</p>" +
      '<h3 class="case-title">' + esc(item.title || "待生成线索") + "</h3>" +
      '<div class="case-meta">' +
        '<span>线索类型<br><b>' + esc(item.hazard || "--") + "</b></span>" +
        '<span>估算面积<br><b>' + fmt(item.area_ha, 1) + " 公顷</b></span>" +
        '<span>置信度<br><b>' + fmt(item.confidence, 2) + "</b></span>" +
        '<span>中心坐标<br><b>' + fmt(item.center_lonlat && item.center_lonlat[0], 4) + ", " + fmt(item.center_lonlat && item.center_lonlat[1], 4) + "</b></span>" +
      "</div>" +
      (legalHtml ? '<ul class="case-legal">' + legalHtml + "</ul>" : "") +
      '<div class="case-tags"><span>' + esc(item.status || "待核查") + "</span><span>演示线索</span><span>不写入案件库</span></div>";

    if (tag) {
      tag.textContent = item.status || "待核查";
      tag.className = "panel-tag warn";
    }

    var download = $("download-report");
    var view = $("view-report");
    if (download) download.disabled = !data.report;
    if (view) view.disabled = !data.report;
  }

  function downloadReport() {
    var data = state.demo;
    if (!data || !data.report) { toast("暂无报告"); return; }
    var blob = new Blob([data.report], { type: "text/markdown;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "天枢6G核查报告-" + ((data.case && data.case.case_no) || "demo") + ".md";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
    toast("报告已导出");
  }

  function openReport() {
    var data = state.demo;
    if (!data || !data.report) { toast("暂无报告"); return; }
    var text = $("report-text");
    var modal = $("report-modal");
    if (text) text.textContent = data.report;
    if (modal) modal.hidden = false;
  }

  function closeReport() {
    var modal = $("report-modal");
    if (modal) modal.hidden = true;
  }

  function renderComparison(comparison) {
    var node = $("compare-badge");
    if (!node || !comparison) return;
    var trad = comparison.traditional || {};
    var sixg = comparison.sixg || {};
    var delta = comparison.delta || {};
    node.textContent = fmt(trad.detect_min, 0) + "min/" + fmt(trad.uplink_mb, 1) +
      "MB → " + fmt(Number(sixg.detect_ms) / 1000, 2) + "s/" + sizeText(sixg.uplink_mb) +
      " · 时延 -" + fmt(Math.min(99.99, Number(delta.latency_reduction_pct)), 2) + "%";
    node.title = (comparison.assumptions || []).join("\n");
  }

  async function runDemo(scenario) {
    var button = $("run-demo");
    if (button) { button.disabled = true; button.textContent = "推演中…"; }
    loading(true);
    try {
      var data = await api("/api/sixg/demo", { method: "POST", body: Object.assign({ scenario: scenario }, state.sandbox) });
      state.demo = data;
      if (data.sandbox) state.sandbox = Object.assign({}, state.sandbox, data.sandbox);
      state.selectedPatch = data.lead ? data.lead.id : null;
      renderNetwork(data.scenario);
      renderPlan(data.plan);
      renderDetection(data.detection);
      renderComparison(data.comparison);
      renderTimeline(data.timeline);
      renderCase(data);
      if (window.Tianshu3D && typeof window.Tianshu3D.pulse === "function") {
        window.Tianshu3D.pulse();
      }
      toast("全链路推演完成 · " + (data.case ? data.case.case_no : ""));
    } finally {
      loading(false);
      if (button) { button.disabled = false; button.textContent = "启动全链路推演"; }
    }
  }

  async function init() {
    initInfoTips();
    bindUI();
    try {
      var overview = await api("/api/sixg/overview");
      state.overview = overview;
      if (overview.sandbox) state.sandbox = Object.assign({}, state.sandbox, overview.sandbox);
      renderScenarios(overview.scenarios || []);
      renderDetection(overview.detection || { metrics: {}, patches: [] });
      await runDemo(state.scenario);
    } catch (error) {
      toast(error.message);
      var box = $("patch-list");
      if (box) box.innerHTML = '<p class="empty">' + esc(error.message) + "</p>";
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
/* 案件工作台：真实案件、任务、证据 SHA-256、复核、归档、报告。 */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const state = {
    user: null,
    cases: [],
    current: null,
    activeId: null,
    hotspots: null,
    grids: null,
    users: [],
    chart: null,
    imagery: null,
    imageryScenes: null,
    arrival: null,
    drone: null,
    legal: null,
    toastTimer: null,
  };

  const STATUS_ORDER = ["待派单", "核查中", "待复核", "已复核", "已归档"];
  const ACTION_LABELS = {
    create_case: "建案",
    create_task: "新增任务",
    update_task: "更新任务",
    upload_evidence: "上传证据",
    review_case: "提交复核",
    archive_case: "归档",
    export_offline_pack: "导出离线包",
    login: "登录",
    logout: "退出",
    change_password: "修改密码",
    create_user: "新增用户",
    issue_arrival_token: "生成到场码",
    revoke_arrival_token: "作废到场码",
    checkin: "现场签到",
    verification: "录入核查结论",
    verify_evidence_chain: "校验证据链",
    download_evidence: "下载证据",
    evidence_duplicate: "重复证据",
  };
  const VERDICT_LABELS = {
    confirmed: "确认问题",
    excluded: "排除",
    inconclusive: "无法判定",
  };
  const HAZARD_LABELS = {
    fire_risk: "火灾", flood: "洪涝", flood_risk: "洪涝", drought: "干旱",
    geohazard: "地质灾害", geohazard_risk: "地质灾害", landslide: "滑坡",
    debris_flow: "泥石流", fire: "火灾", drought_risk: "干旱",
  };
  const LEGAL_HAZARDS = {
    fire_scar: "火烧迹地", mining: "盗采/非法采矿", mining_damage: "矿山破坏",
    landslide: "滑坡", debris_flow: "泥石流", snowline: "雪线变化",
    construction: "违建/施工", dumping: "固废/倾倒", water_anomaly: "水体异常",
    wetland: "湿地占用", veg_degradation: "植被退化", bare_soil: "裸土/农用地",
    road_intrusion: "道路侵入", fire_risk: "火灾风险", flood_risk: "洪涝风险",
    drought_risk: "干旱风险", geohazard_risk: "地质灾害风险",
  };

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }
  function num(value, digits = 1) {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits) : "—";
  }
  function fmtTime(value) {
    if (!value) return "—";
    return String(value).replace("T", " ").slice(0, 19);
  }
  function fmtSize(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  }
  function roleLabel(role) {
    return { admin: "管理员", commander: "指挥员", officer: "民警", reviewer: "复核员" }[role] || role;
  }
  function userName(id) {
    const user = (state.users || []).find((item) => item.id === id);
    return user ? user.display_name : (id || "—");
  }
  function canManage() {
    return state.user && ["admin", "commander"].includes(state.user.role);
  }
  function canReview() {
    return state.user && ["admin", "reviewer"].includes(state.user.role);
  }
  function canUpload() {
    return state.user && ["admin", "commander", "officer"].includes(state.user.role);
  }
  function isClosed(c) {
    return c && ["已复核", "已归档"].includes(c.status);
  }
  function toast(message, isError = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("err", isError);
    el.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
  }

  async function api(path, options = {}) {
    const opts = { credentials: "same-origin", ...options };
    if (opts.body && !(opts.body instanceof FormData) && !opts.headers) {
      opts.headers = { "Content-Type": "application/json" };
    }
    const res = await fetch(path, opts);
    const type = res.headers.get("content-type") || "";
    let data = null;
    if (type.includes("application/json")) {
      data = await res.json().catch(() => null);
    }
    if (res.status === 401 && options.authRedirect !== false) {
      showLogin();
    }
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : `请求失败（${res.status}）`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function showLogin() {
    state.user = null;
    state.current = null;
    state.activeId = null;
    $("#app-view").hidden = true;
    $("#login-view").hidden = false;
    $("#login-password").value = "";
    setTimeout(() => $("#login-username").focus(), 30);
  }

  async function showApp() {
    $("#login-view").hidden = true;
    $("#app-view").hidden = false;
    $("#user-chip").textContent = `${state.user.display_name} · ${roleLabel(state.user.role)}`;
    await loadAll().catch((err) => toast(err.message, true));
    loadImageryWatch().catch(() => {});
    const deepCase = new URLSearchParams(location.search).get("case");
    if (deepCase) {
      history.replaceState(null, "", "/workspace");
      if (state.cases.some((c) => c.id === deepCase)) {
        await selectCase(deepCase, false).catch((err) => toast(err.message, true));
      } else {
        toast("案件不存在或无权访问", true);
      }
    }
    const pending = pendingSourceFromUrl();
    if (pending) {
      history.replaceState(null, "", "/workspace");
      if (canManage()) await openCaseModal(pending);
    }
  }

  function pendingSourceFromUrl() {
    const q = new URLSearchParams(location.search);
    const type = q.get("source_type");
    const id = q.get("source_id");
    return (type === "hotspot" || type === "grid" || type === "imagery") && id ? { type, id } : null;
  }

  async function loadAll() {
    await Promise.all([loadCases(), loadUsers()]);
    if (state.activeId) {
      await selectCase(state.activeId, false);
    } else if (state.cases.length) {
      await selectCase(state.cases[0].id, false);
    } else {
      renderEmpty();
    }
  }

  async function loadUsers() {
    state.users = [];
    if (!canManage()) return;
    try {
      state.users = await api("/api/users", { authRedirect: false });
    } catch (_) {
      state.users = [];
    }
  }

  async function loadCases() {
    const [stats, cases] = await Promise.all([
      api("/api/cases/stats", { authRedirect: false }),
      api("/api/cases", { authRedirect: false }),
    ]);
    state.cases = Array.isArray(cases) ? cases : [];
    renderStats(stats);
    renderCaseList();
    renderQuadrant();
  }

  async function loadImageryWatch(render = false) {
    try {
      const data = await api("/api/imagery/watch", { authRedirect: false });
      state.imagery = data;
      updateImageryBadge(data);
      if (render) renderImagery(data);
      return data;
    } catch (err) {
      if (render) {
        $("#imagery-status").textContent = `读取新影像状态失败：${err.message}`;
      }
      throw err;
    }
  }

  function updateImageryBadge(data) {
    const badge = $("#imagery-badge");
    const count = Number(data && data.new_count) || 0;
    badge.hidden = count <= 0;
    badge.textContent = count > 99 ? "99+" : String(count);
  }

  function renderImagery(data) {
    const checked = Boolean(data && data.checked);
    const newScenes = (data && data.new_scenes) || [];
    const latest = (data && data.latest) || [];
    const rows = newScenes.length ? newScenes : latest;
    $("#imagery-meta").textContent = checked
      ? `上次 ${fmtTime(data.last_checked_at)} · 近 ${data.window_days || 14} 天 · 云量≤${data.cloud_max ?? 60}%`
      : "尚未检查";
    $("#imagery-disclaimer").textContent = (data && data.disclaimer) ||
      "这里只报告“有新卫星影像到访”，不表示影像里一定存在异常，也不是火情/灾情预警。";
    if (!checked) {
      $("#imagery-status").textContent = "尚未建立基线。点击“检查最新 Sentinel-2”，系统会查询研究区近 14 天影像，并把结果作为下次比较的基线。";
      $("#imagery-list").innerHTML = `<p class="empty-small">还没有检查记录。</p>`;
      return;
    }
    const source = data.baseline_source === "data/mpc_scenes.json"
      ? "本地上次抓取缓存" : "上次检查状态";
    const sourceLabel = data.baseline_at
      ? `${source}（${fmtTime(data.baseline_at).slice(0, 16)}）` : source;
    const totalNew = Number(data.new_count) || newScenes.length;
    $("#imagery-status").innerHTML = newScenes.length
      ? `与<b>${esc(sourceLabel)}</b>对比，发现 <b>${totalNew}</b> 个新场景` +
        `${newScenes.length < totalNew ? `（列表显示前 ${newScenes.length} 个）` : ""}；` +
        `多图幅可能来自同一过境时刻，只代表影像到访，需人工打开比对。` +
        `${data.last_error ? `<br><span style="color:#ffc3bd">${esc(data.last_error)}</span>` : ""}`
      : `与<b>${esc(sourceLabel)}</b>对比，本次没有晚于基线的新场景。` +
        `${data.last_error ? `<br><span style="color:#ffc3bd">${esc(data.last_error)}</span>` : ""}`;
    if (!rows.length) {
      $("#imagery-list").innerHTML = `<p class="empty-small">STAC 未返回可用场景；请稍后重试，或检查网络。</p>`;
      return;
    }
    const heading = newScenes.length
      ? `<div class="imagery-group-title">新增场景 <span>共 ${totalNew} 景，列表显示 ${newScenes.length} 景 · 仅代表影像到访</span></div>`
      : `<div class="imagery-group-title">最近影像 <span>基线内，不是新增</span></div>`;
    $("#imagery-list").innerHTML = heading + rows.map((scene) => {
      const preview = scene.preview
        ? `<img class="imagery-thumb" loading="lazy" src="${esc(scene.preview)}" alt="Sentinel-2 预览">`
        : `<div class="imagery-thumb empty">无预览</div>`;
      const cloud = scene.cloud_cover === null || scene.cloud_cover === undefined
        ? "云量 —" : `云量 ${num(scene.cloud_cover, 2)}%`;
      const date = fmtTime(scene.datetime).slice(0, 16);
      return `<article class="imagery-item">
        ${preview}
        <div>
          <h4>${esc(date)} · ${esc(scene.tile || "未知图幅")} ${newScenes.length ? `<span class="new-dot">新</span>` : ""}</h4>
          <p>${esc(scene.platform || "Sentinel-2")} · ${esc(cloud)} · ${esc(scene.collection || "sentinel-2-l2a")}</p>
          <span class="scene-id" title="${esc(scene.id)}">${esc(scene.id)}</span>
        </div>
        <div class="item-side">
          <a href="${esc(scene.item_url)}" target="_blank" rel="noopener">STAC 元数据</a>
          ${scene.preview ? `<a href="${esc(scene.preview)}" target="_blank" rel="noopener">打开预览</a>` : ""}
        </div>
      </article>`;
    }).join("");
  }

  async function refreshImagery() {
    const btn = $("#imagery-refresh");
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "查询 MPC STAC…";
    $("#imagery-status").textContent = "正在查询 Microsoft Planetary Computer，通常需要几秒钟…";
    try {
      const data = await api("/api/imagery/watch/refresh?days=14&cloud_max=60", {
        method: "POST",
        body: JSON.stringify({}),
      });
      state.imagery = data;
      renderImagery(data);
      updateImageryBadge(data);
      toast(data.new_count ? `发现 ${data.new_count} 个新场景（只代表影像到访）` : "检查完成，暂无新增场景");
    } catch (err) {
      $("#imagery-status").textContent = `检查失败：${err.message}`;
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  }

  async function openImageryModal() {
    closeCaseModal();
    $("#modal-backdrop").hidden = false;
    $("#imagery-modal").hidden = false;
    await loadImageryWatch(true).catch(() => {});
  }

  function closeImageryModal() {
    $("#imagery-modal").hidden = true;
    if ($("#case-modal").hidden) $("#modal-backdrop").hidden = true;
  }

  function renderStats(stats) {
    const by = stats.by_status || {};
    const cells = [
      ["全部", stats.total || 0, ""],
      ["待派单", by["待派单"] || 0, "hot"],
      ["核查中", by["核查中"] || 0, "hot"],
      ["待复核", by["待复核"] || 0, "ok"],
    ];
    $("#case-stats").innerHTML = cells.map(([label, value, cls]) =>
      `<div class="stat-cell ${cls}"><b>${value}</b><span>${label}</span></div>`
    ).join("");
  }

  function filteredCases() {
    const q = ($("#case-search").value || "").trim().toLowerCase();
    const status = $("#case-filter").value;
    return state.cases.filter((c) => {
      if (status && c.status !== status) return false;
      if (!q) return true;
      return [c.case_no, c.title, c.hotspot_name, c.source_id, c.assignee_name]
        .some((v) => String(v || "").toLowerCase().includes(q));
    });
  }

  function renderCaseList() {
    const rows = filteredCases();
    const box = $("#case-list");
    if (!rows.length) {
      box.innerHTML = `<p class="empty">${state.cases.length ? "没有匹配的案件" : "还没有案件，点击“建案”开始"}</p>`;
      return;
    }
    box.innerHTML = rows.map((c) => `
      <article class="case-item ${c.id === state.activeId ? "active" : ""}" data-id="${esc(c.id)}" data-status="${esc(c.status)}">
        <div class="case-item-top">
          <span class="case-item-no">${esc(c.case_no)}</span>
          <span class="status-pill st-${esc(c.status)}">${esc(c.status)}</span>
        </div>
        <h3>${esc(c.title || c.hotspot_name || c.source_id)}</h3>
        <div class="case-item-meta">
          <span>优先级 <b>${num(c.priority_score)}</b> · 危险 <b>${num(c.current_danger)}</b></span>
          <span>${esc(c.assignee_name || "待指派")}</span>
        </div>
      </article>
    `).join("");
  }

  function renderQuadrant() {
    const el = $("#quadrant-chart");
    if (!window.echarts) return;
    if (!state.chart) {
      state.chart = echarts.init(el, null, { renderer: "canvas" });
      state.chart.on("click", (params) => {
        const id = params && params.data && params.data[3];
        if (id) selectCase(id);
      });
      window.addEventListener("resize", () => state.chart && state.chart.resize());
    }
    const colors = {
      "待派单": "#ffd60a", "核查中": "#ffa726", "待复核": "#5aa9ff",
      "已复核": "#34d399", "已归档": "#7b8fa1",
    };
    if (!state.cases.length) {
      state.chart.clear();
      state.chart.setOption({
        backgroundColor: "transparent",
        graphic: { type: "text", left: "center", top: "middle", style: { text: "建案后显示双分数分布", fill: "#5f7285", fontSize: 10 } },
      });
      return;
    }
    const data = state.cases.map((c) => ({
      value: [Number(c.priority_score) || 0, Number(c.current_danger) || 0],
      name: c.case_no,
      itemStyle: { color: colors[c.status] || "#8496a8" },
      caseId: c.id,
      caseTitle: c.title || c.hotspot_name,
      caseStatus: c.status,
    }));
    const seriesData = data.map((d) => ({
      value: [...d.value, d.caseId, d.caseTitle, d.caseStatus],
      name: d.name,
      itemStyle: d.itemStyle,
    }));
    state.chart.clear();
    state.chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 38, right: 12, top: 16, bottom: 26 },
      tooltip: {
        trigger: "item",
        backgroundColor: "rgba(8,18,27,.96)",
        borderColor: "#2a4c5e",
        textStyle: { color: "#d9edf7", fontSize: 10 },
        formatter: (p) => {
          const v = p.data.value;
          return `<b>${esc(v[4] || p.name)}</b><br/>优先级 ${num(v[0])} · 当前危险 ${num(v[1])}<br/>${esc(v[5] || "")}`;
        },
      },
      xAxis: {
        min: 0, max: 100, name: "历史优先级", nameTextStyle: { color: "#5f7285", fontSize: 9 },
        axisLabel: { color: "#5f7285", fontSize: 8 }, splitLine: { lineStyle: { color: "rgba(34,54,74,.35)" } },
      },
      yAxis: {
        min: 0, max: 100, name: "当前危险", nameTextStyle: { color: "#5f7285", fontSize: 9 },
        axisLabel: { color: "#5f7285", fontSize: 8 }, splitLine: { lineStyle: { color: "rgba(34,54,74,.35)" } },
      },
      series: [{
        type: "scatter",
        symbolSize: 8,
        data: seriesData,
        emphasis: { scale: 1.6, label: { show: false } },
        markLine: {
          silent: true,
          symbol: "none",
          label: { color: "#6f8798", fontSize: 8, formatter: "{c}" },
          lineStyle: { color: "rgba(132,150,168,.38)", type: "dashed", width: 1 },
          data: [{ xAxis: 70 }, { xAxis: 60 }, { yAxis: 70 }, { yAxis: 60 }],
        },
      }],
    }, true);
  }

  function renderEmpty() {
    state.current = null;
    $("#case-main").innerHTML = `
      <div class="empty-state">
        <img src="/assets/police-badge-learning.png" alt="">
        <h2>从左侧选择一个案件</h2>
        <p>或者点击“建案”，用真实高发点/风险格网生成一条可核查的线索。</p>
      </div>`;
  }

  async function selectCase(id, scroll = true) {
    if (!id) return;
    try {
      const detail = await api(`/api/cases/${encodeURIComponent(id)}`);
      state.current = detail;
      state.activeId = id;
      state.arrival = null;
      state.drone = null;
      state.legal = null;
      renderCaseList();
      renderCase(detail);
      if (scroll) $("#case-main").scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      toast(err.message, true);
    }
  }

  function statusPill(status) {
    return `<span class="status-pill st-${esc(status)}">${esc(status)}</span>`;
  }

  function renderProgress(c) {
    const steps = ["建案", "派单", "现场核查", "复核", "归档"];
    const active = { "待派单": 1, "核查中": 2, "待复核": 3, "已复核": 4, "已归档": 5 }[c.status] ?? 0;
    return `<div class="case-progress" aria-label="办理进度：${esc(c.status)}">
      ${steps.map((step, i) => {
        const cls = i < active ? "done" : i === active && active < steps.length ? "current" : "";
        return `${i ? `<i class="step-line" aria-hidden="true"></i>` : ""}<div class="step ${cls}"><span>${i < active ? "✓" : i + 1}</span><b>${step}</b></div>`;
      }).join("")}
    </div>`;
  }

  function renderCase(c) {
    const snap = c.data_snapshot || {};
    const props = snap.properties || {};
    const tasks = c.tasks || [];
    const evidence = c.evidence || [];
    const reviews = c.reviews || [];
    const audits = c.audit || [];
    const checkins = c.checkins || [];
    const chain = c.evidence_chain || {};
    const verification = c.verification || null;
    const closed = isClosed(c);
    const doneTasks = tasks.filter((t) => t.status === "已完成").length;
    const canAddTask = !closed && (canManage() || (state.user.role === "officer" &&
      (c.assignee_id === state.user.id || c.created_by === state.user.id)));
    const finalReport = ["已复核", "已归档"].includes(c.status);
    const reportHref = `/api/cases/${encodeURIComponent(c.id)}/report.pdf${finalReport ? "?final=1" : ""}`;
    const qrHref = `/api/cases/${encodeURIComponent(c.id)}/qrcode.png?t=${Date.now()}`;
    const offlineHref = `/api/cases/${encodeURIComponent(c.id)}/offline-pack.zip`;
    const priorityLabel = c.priority_label || props.danger_label || "—";
    const dangerLabel = c.current_danger_label || props.current_danger_label || "—";

    $("#case-main").innerHTML = `
      <div class="case-head">
        <div>
          <div class="crumbs"><span>案件管理</span><span>/</span><span>案件列表</span><span>/</span><b>${esc(c.case_no)}</b></div>
          <div class="case-sub" style="margin:0 0 5px">
            <span>${esc(c.case_no)}</span><span>${statusPill(c.status)}</span>
            <span>数据快照 ${fmtTime(snap.snapshot_at)}</span>
          </div>
          <h2>${esc(c.title || c.hotspot_name)}</h2>
          <div class="case-sub">
            <span>线索 <b>${esc(c.source_type === "grid" ? "当前危险格网" : c.source_type === "imagery" ? "新影像到访" : c.source_type === "sixg" ? "6G 演示线索" : "历史高发点")} · ${esc(c.source_id)}</b></span>
            <span>承办 <b>${esc(c.assignee_name || "待指派")}</b></span>
            <span>期限 <b>${fmtTime(c.due_at).slice(0, 10)}</b></span>
            <span>更新 <b>${fmtTime(c.updated_at)}</b></span>
          </div>
        </div>
        <div class="head-actions">
          <button class="btn btn-ghost btn-sm" data-action="refresh" type="button">刷新</button>
          <a class="btn btn-ghost btn-sm" href="${offlineHref}" title="案件快照、核查清单与 SHA-256；不含底图和证据原件">离线核查包</a>
          ${c.status === "已复核" && canManage() ? `<button class="btn btn-primary btn-sm" data-action="archive" type="button">归档案件</button>` : ""}
          <a class="btn btn-sm" href="${reportHref}" target="_blank" rel="noopener">${finalReport ? "正式报告 PDF" : "过程稿 PDF"}</a>
        </div>
      </div>

      <div class="score-grid">
        <div class="score-card priority">
          <div class="label">历史核查优先级</div>
          <div class="value"><b>${num(c.priority_score)}</b><em>/100</em></div>
          <div class="foot">${esc(priorityLabel)} · 回答“先核查哪一处”</div>
        </div>
        <div class="score-card danger">
          <div class="label">当前危险指数</div>
          <div class="value"><b>${num(c.current_danger)}</b><em>/100</em></div>
          <div class="foot">${esc(dangerLabel)} · 当前自然条件估计</div>
        </div>
        <div class="score-card">
          <div class="label">任务进度</div>
          <div class="value"><b>${doneTasks}</b><em>/ ${tasks.length}</em></div>
          <div class="foot">未完成任务 ${tasks.filter((t) => t.status !== "已完成").length} 个</div>
        </div>
        <div class="score-card">
          <div class="label">证据</div>
          <div class="value"><b>${evidence.length}</b><em>份</em></div>
          <div class="foot">SHA-256 完整性校验</div>
        </div>
      </div>

      ${renderProgress(c)}

      <div class="dual-note" id="dual-note">
        <span class="tag">双分数</span>
        <div><b>历史优先级</b>来自 53 个历史异常图斑的 AHP 加权排序，用来决定核查顺序，不是灾害概率；
        <b>当前危险指数</b>只使用当前/未来气象、NDVI、地形、土壤与人口暴露，历史异常不参与，也不是官方预警。
        两者分开显示，避免把“历史上出事多”误读成“现在一定会出事”。</div>
      </div>

      <section class="section" id="arrival-section">
        <div class="section-head"><h3>车行到达圈</h3><span class="hint">OSRM 路网采样近似；不含实时路况</span></div>
        <div class="section-body">
          <div class="arrival-controls">
            <label>车行时间
              <select id="arrival-minutes">
                <option value="15">15 分钟</option>
                <option value="30" selected>30 分钟</option>
                <option value="60">60 分钟</option>
                <option value="90">90 分钟</option>
              </select>
            </label>
            <button class="btn btn-primary btn-sm" type="button" data-action="arrival-circle">计算到达圈</button>
            <button class="btn btn-ghost btn-sm" type="button" data-action="arrival-geojson" disabled>下载 GeoJSON</button>
          </div>
          <div id="arrival-result" class="arrival-result">
            <p class="empty-small">按 15/30/60 分钟计算车行可达范围；偏远林区若道路吸附过远，会降级为直线估算并说明原因。</p>
          </div>
        </div>
      </section>

      <section class="section" id="drone-section">
        <div class="section-head"><h3>无人机核查航线</h3><span class="hint">DEM 跟随；不是适航审批</span></div>
        <div class="section-body">
          <div class="drone-controls">
            <label>模式
              <select id="drone-pattern">
                <option value="lawnmower" selected>往复扫描</option>
                <option value="perimeter">环绕</option>
              </select>
            </label>
            <label>范围 km<input id="drone-size" type="number" min="0.05" max="5" step="0.05" value="0.6"></label>
            <label>间距 m<input id="drone-spacing" type="number" min="10" max="1000" step="10" value="80"></label>
            <label>相对高度 m<input id="drone-height" type="number" min="20" max="500" step="10" value="80"></label>
            <label>速度 m/s<input id="drone-speed" type="number" min="2" max="25" step="1" value="8"></label>
            <label>方向 °<input id="drone-direction" type="number" min="0" max="359" step="5" value="0"></label>
          </div>
          <div class="drone-actions">
            <button class="btn btn-primary btn-sm" type="button" data-action="drone-plan">规划航线</button>
            <button class="btn btn-ghost btn-sm" type="button" data-action="drone-kml" disabled>下载 KML</button>
            <button class="btn btn-ghost btn-sm" type="button" data-action="drone-csv" disabled>下载 CSV</button>
          </div>
          <div id="drone-result" class="drone-result">
            <p class="empty-small">按案件点生成往复扫描或环绕航线，高度随 DEM 起伏；起飞前仍须人工核对空域、天气和障碍物。</p>
          </div>
        </div>
      </section>

      <section class="section" id="legal-section">
        <div class="section-head"><h3>法条与取证清单</h3><span class="hint">草稿库 · 须法制审核</span></div>
        <div class="section-body">
          <div class="legal-controls">
            <label>关键词<input id="legal-q" type="search" maxlength="120" placeholder="如：非法采矿、倾倒、占用农用地"></label>
            <label>灾种<select id="legal-hazard"><option value="">全部灾种</option></select></label>
            <button class="btn btn-ghost btn-sm" type="button" data-action="legal-search">检索</button>
            <button class="btn btn-ghost btn-sm" type="button" data-action="legal-txt" disabled>下载清单 TXT</button>
          </div>
          <div id="legal-result" class="legal-result">
            <p class="empty-small">正在按案件灾种加载法条与取证方向…</p>
          </div>
        </div>
      </section>

      <section class="section" id="snapshot-section">
        <div class="section-head"><h3>线索与数据快照</h3><span class="hint">建案时冻结，不随页面刷新改变</span></div>
        <div class="section-body">
          <div class="kv-grid">
            <div class="kv"><span>线索名称</span><b>${esc(c.hotspot_name || props.name || c.source_id)}</b></div>
            <div class="kv"><span>空间位置</span><b>${num(c.lon, 5)}°E, ${num(c.lat, 5)}°N</b></div>
            <div class="kv"><span>主导灾种</span><b>${esc(props.dominant_hazard_name || props.dominant_class || props.dominant_hazard || "—")}</b></div>
            <div class="kv"><span>数据来源</span><b>${esc(c.source || snap.source || "—")}</b></div>
            <div class="kv"><span>建案人</span><b>${esc(userName(c.created_by))}</b></div>
            <div class="kv"><span>建案时间</span><b>${fmtTime(c.created_at)}</b></div>
          </div>
          ${props.recommended_action ? `<div class="note-line"><b>处置建议：</b>${esc(props.recommended_action)}</div>` : ""}
          ${props.advice && props.advice !== props.recommended_action ? `<div class="note-line"><b>数据建议：</b>${esc(props.advice)}</div>` : ""}
          ${c.summary ? `<div class="note-line"><b>案情摘要：</b>${esc(c.summary)}</div>` : ""}
        </div>
      </section>

      <section class="section" id="factors-section">
        <div class="section-head"><h3>${props.factors && props.priority_weights ? "历史 AHP 因子贡献" : "当前危险分量"}</h3><span class="hint">分数由服务端数据快照给出</span></div>
        <div class="section-body">${renderFactors(props)}</div>
      </section>

      <section class="section" id="tasks-section">
        <div class="section-head"><h3>现场核查任务</h3><span class="hint">${closed ? "案件已关闭，不能新增任务" : "任务完成前必须至少上传一份证据"}</span></div>
        <div class="section-body">
          <div class="task-list">${tasks.length ? tasks.map(renderTask).join("") : `<p class="empty-small">尚未创建任务。</p>`}</div>
          ${canAddTask ? `
          <form id="task-form" class="inline-form ${canManage() ? "" : "two"}">
            <input id="task-title" type="text" maxlength="160" placeholder="任务标题，例如：现场核查盗采痕迹" required>
            ${canManage() ? `<select id="task-assignee"><option value="">暂不指派</option>${state.users.map((u) => `<option value="${u.id}">${esc(u.display_name)} · ${esc(u.role_label || roleLabel(u.role))}</option>`).join("")}</select>` : ""}
            <input id="task-due" type="date" title="办理期限">
            <button class="btn btn-primary btn-sm" type="submit">新增任务</button>
          </form>` : ""}
        </div>
      </section>

      <section class="section" id="evidence-section">
        <div class="section-head"><h3>证据与完整性</h3><span class="hint">SHA-256 只能证明文件未被改动，不能证明拍摄时间/地点真实</span></div>
        <div class="section-body">
          <div class="evidence-list">${evidence.length ? evidence.map(renderEvidence).join("") : `<p class="empty-small">尚未上传证据。没有证据不能把任务标记为“已完成”。</p>`}</div>
          ${!closed && canUpload() ? `
          <form id="evidence-form" class="inline-form">
            <input id="evidence-file" class="span2" type="file" accept=".jpg,.jpeg,.png,.webp,.pdf,.txt,.mp4,.mov" required>
            <select id="evidence-task"><option value="">不关联任务</option>${tasks.filter((t) => t.status !== "已完成").map((t) => `<option value="${esc(t.id)}">${esc(t.title)}</option>`).join("")}</select>
            <input id="evidence-captured" type="datetime-local" title="拍摄/形成时间">
            <input id="evidence-lon" type="number" step="0.00001" min="-180" max="180" placeholder="经度（可选）">
            <input id="evidence-lat" type="number" step="0.00001" min="-90" max="90" placeholder="纬度（可选）">
            <input id="evidence-note" class="span2" type="text" maxlength="500" placeholder="证据说明（可选）">
            <button class="btn btn-primary btn-sm" type="submit">上传并计算 SHA-256</button>
          </form>` : ""}
        </div>
      </section>

      <section class="section" id="chain-section">
        <div class="section-head"><h3>证据链与现场核查结论</h3><span class="hint">链头锚定 + 文件重算；不是法定电子签章</span></div>
        <div class="section-body">
          <div class="kv-grid">
            <div class="kv"><span>链状态</span><b class="${chain.valid ? "ok-text" : "bad-text"}">${chain.empty ? "空链" : chain.valid ? "链头一致" : "链头不一致"}</b></div>
            <div class="kv"><span>证据数</span><b>${Number.isFinite(Number(chain.count)) ? Math.round(Number(chain.count)) : "—"}</b></div>
            <div class="kv"><span>链头</span><b class="mono hash">${esc((chain.head || "—").slice(0, 24))}${chain.head ? "…" : ""}</b></div>
            <div class="kv"><span>锚定方式</span><b>${chain.backfilled ? "历史补链（不能证明补链前未改动）" : "逐条写入时锚定"}</b></div>
            <div class="kv"><span>到场签到</span><b>${checkins.length} 次</b></div>
            <div class="kv"><span>评测标签</span><b>${verification ? esc(VERDICT_LABELS[verification.verdict] || verification.verdict) : "未录入"}</b></div>
          </div>
          <div class="note-line">${esc(chain.note || "尚无链信息。")}</div>
          <div class="item-actions" style="margin-top:8px">
            <button class="btn btn-ghost btn-sm" data-action="verify-chain" type="button">深度校验文件 SHA-256</button>
          </div>
          <div id="chain-result" class="form-note" aria-live="polite"></div>
          ${checkins.length ? `<div class="checkin-list">${checkins.slice(0, 5).map((ch) => `
            <div class="item-card">
              <div class="item-top"><b>${esc(ch.actor_name || "现场人员")}</b><span class="muted" style="font-size:9px">${fmtTime(ch.captured_at || ch.created_at)}</span></div>
              <div class="item-meta">${ch.lon != null ? `<span>${num(ch.lon, 5)}°E, ${num(ch.lat, 5)}°N</span>` : "<span>无定位</span>"}<span>证据 ${(ch.evidence_ids || []).length} 份</span><span>${esc(ch.source || "到场码")}</span></div>
              ${ch.note ? `<div class="item-note">${esc(ch.note)}</div>` : ""}
            </div>`).join("")}</div>` : `<p class="empty-small">尚无现场签到。生成到场码后由现场民警扫码。</p>`}
          <hr class="sep">
          <h4>现场核查结论</h4>
          ${verification ? `<div class="item-card">
            <div class="item-top"><b>${esc(VERDICT_LABELS[verification.verdict] || verification.verdict)}</b><span class="muted" style="font-size:9px">${fmtTime(verification.verified_at)}</span></div>
            <div class="item-meta"><span>录入：${esc(verification.verified_by_name || "—")}</span>${verification.evidence_id ? `<span>支撑证据 ${esc(verification.evidence_id)}</span>` : ""}</div>
            ${verification.note ? `<div class="item-note">${esc(verification.note)}</div>` : ""}
          </div>` : `<p class="empty-small">尚未录入核查结论。</p>`}
          ${canReview() && !closed ? `
          <form id="verification-form" class="inline-form two" style="margin-top:10px">
            <select id="verification-verdict">
              <option value="confirmed">确认问题</option>
              <option value="excluded">排除</option>
              <option value="inconclusive">无法判定</option>
            </select>
            <select id="verification-evidence"><option value="">不关联证据</option>${evidence.map((e) => `<option value="${esc(e.id)}">${esc(e.filename)}</option>`).join("")}</select>
            <textarea id="verification-note" class="span2" maxlength="1000" rows="2" placeholder="结论依据；排除时写明排除理由"></textarea>
            <button class="btn btn-primary btn-sm" type="submit">保存核查结论</button>
          </form>` : ""}
          <p class="form-note">只有 confirmed / excluded 计入评测分母，inconclusive 不计入；样本不足时不解释为模型精度。录入会写审计，可在复核后修改直至归档。</p>
        </div>
      </section>

      <section class="section" id="review-section">
        <div class="section-head"><h3>复核意见</h3><span class="hint">${c.status === "待复核" ? "所有任务已完成，等待复核" : "只有“待复核”状态可以提交复核"}</span></div>
        <div class="section-body">
          <div class="review-list">${reviews.length ? reviews.map(renderReview).join("") : `<p class="empty-small">尚未提交复核。</p>`}</div>
          ${c.status === "待复核" && canReview() ? `
          <div class="review-box" style="margin-top:10px">
            <select id="review-decision"><option>通过</option><option>退回</option><option>补充核查</option></select>
            <textarea id="review-comment" maxlength="2000" placeholder="复核意见；退回或补充核查必须填写"></textarea>
            <button id="review-submit" class="btn btn-primary btn-sm" type="button">提交复核</button>
          </div>` : ""}
        </div>
      </section>

      <section class="section" id="report-section">
        <div class="section-head"><h3>报告与线索二维码</h3><span class="hint">二维码指向本机工作台，不是公网发布</span></div>
        <div class="section-body">
          <div class="report-strip">
            <div>
              <p style="font-size:11px;line-height:1.7;color:#b9d6e6">${finalReport ? "案件已复核/归档，可导出带复核意见的正式报告。" : "当前为过程稿；完成复核后可导出正式报告。"} 报告含双分数口径、AHP 因子、任务、证据 SHA-256、复核与审计，并附免责声明。</p>
              <div class="item-actions" style="margin-top:9px">
                <a class="btn btn-primary btn-sm" href="${reportHref}" target="_blank" rel="noopener">下载 PDF 报告</a>
                <a class="btn btn-ghost btn-sm" href="${offlineHref}">下载离线核查包</a>
                <a class="btn btn-sm" href="${qrHref}" target="_blank" rel="noopener">打开二维码</a>
              </div>
            </div>
            <div class="qr-box"><img src="${qrHref}" alt="案件线索二维码"></div>
          </div>
        </div>
      </section>

      <section class="section" id="token-section">
        <div class="section-head"><h3>现场到场码</h3><span class="hint">短期、可作废、只存哈希；扫码打开移动核查页</span></div>
        <div class="section-body">
          ${!closed && canUpload() ? `
          <form id="arrival-form" class="inline-form two">
            <label class="field-inline">有效期（小时）<input id="arrival-hours" type="number" min="1" max="720" value="24"></label>
            <label class="field-inline">可用次数（0=不限）<input id="arrival-max" type="number" min="0" max="10000" value="0"></label>
            <input id="arrival-note" class="span2" type="text" maxlength="300" placeholder="用途备注，例如：2026-11-10 上午现场核查">
            <button class="btn btn-primary btn-sm" type="submit">生成到场码</button>
          </form>` : `<p class="empty-small">当前状态或权限不能生成到场码。</p>`}
          <div id="arrival-token-result" aria-live="polite"></div>
          <div id="arrival-list" class="arrival-list"><p class="empty-small">加载中…</p></div>
          <p class="form-note">到场码放在链接片段里，不进入服务端访问日志；手机打开后由前端放进请求头。它只证明持码者到达，不证明身份，现场仍须核验证件。</p>
        </div>
      </section>

      <section class="section" id="audit-section">
        <div class="section-head"><h3>审计轨迹</h3><span class="hint">仅追加，不提供修改/删除接口</span></div>
        <div class="section-body">
          ${audits.length ? `<div class="audit-list">${audits.slice().reverse().map(renderAudit).join("")}</div>` : `<p class="empty-small">暂无审计记录。</p>`}
        </div>
      </section>
    `;
    layoutCase();
    bindCaseEvents(c);
  }

  function layoutCase() {
    const main = $("#case-main");
    const grid = document.createElement("div");
    grid.className = "case-grid";
    const left = document.createElement("div");
    left.className = "case-col case-col-main";
    const right = document.createElement("aside");
    right.className = "case-col case-col-side";
    ["snapshot-section", "factors-section", "tasks-section", "evidence-section",
      "chain-section", "review-section", "report-section"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) left.appendChild(node);
    });
    ["dual-note", "arrival-section", "drone-section"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) right.appendChild(node);
    });
    grid.appendChild(left);
    grid.appendChild(right);
    main.appendChild(grid);
    ["legal-section", "token-section", "audit-section"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) main.appendChild(node);
    });
  }

  function renderFactors(props) {
    const factors = props.factors || {};
    const weights = props.priority_weights || props.weights || {};
    let rows = [];
    if (factors && Object.keys(factors).length) {
      rows = Object.entries(factors)
        .filter(([key]) => key in weights)
        .map(([key, value]) => ({
          key,
          value: Number(value) || 0,
          weight: Number(weights[key]) || 0,
          contribution: (Number(value) || 0) * (Number(weights[key]) || 0) * 100,
        }))
        .sort((a, b) => b.contribution - a.contribution)
        .slice(0, 7);
    } else if (props.current_components && typeof props.current_components === "object") {
      rows = Object.entries(props.current_components)
        .map(([key, value]) => ({ key, value: Number(value) || 0, weight: null, contribution: Number(value) || 0 }))
        .sort((a, b) => b.contribution - a.contribution)
        .slice(0, 7);
    }
    if (!rows.length) return `<p class="empty-small">该数据源没有可展示的因子明细。</p>`;
    const max = Math.max(...rows.map((r) => r.contribution), 1);
    return `<div class="factor-bars">${rows.map((r) => `
      <div class="factor-row" title="归一化值 ${num(r.value, 3)}${r.weight !== null ? ` · 权重 ${num(r.weight, 4)}` : ""}">
        <span>${esc(r.key)}</span>
        <div class="factor-track"><i style="width:${Math.max(2, (r.contribution / max) * 100).toFixed(1)}%"></i></div>
        <b>${num(r.contribution, 2)}</b>
      </div>`).join("")}</div>
    <p class="form-note">数值为“归一化因子值 × 权重 × 100”的贡献；网页排序会剔除“当前环境”后重新归一化。总和与卡片分数可能因四舍五入略有差异。</p>`;
  }

  function renderTask(t) {
    const canUpdate = !isClosed(state.current) && (canManage() || t.assignee_id === state.user.id);
    const actions = [];
    if (canUpdate) {
      if (t.status === "待接收") actions.push(`<button class="mini-btn" data-action="task-status" data-task="${esc(t.id)}" data-status="核查中">开始核查</button>`);
      if (t.status === "核查中") {
        actions.push(`<button class="mini-btn ok" data-action="task-status" data-task="${esc(t.id)}" data-status="已完成">完成任务</button>`);
        actions.push(`<button class="mini-btn bad" data-action="task-status" data-task="${esc(t.id)}" data-status="已退回">退回</button>`);
      }
      if (t.status === "已退回") actions.push(`<button class="mini-btn" data-action="task-status" data-task="${esc(t.id)}" data-status="核查中">重新核查</button>`);
    }
    return `<div class="item-card">
      <div class="item-top"><b>${esc(t.title)}</b><span class="status-pill st-${esc(t.status)}">${esc(t.status)}</span></div>
      <div class="item-meta"><span>${esc(t.id)}</span><span>承办：${esc(t.assignee_name || "待指派")}</span><span>期限：${fmtTime(t.due_at).slice(0, 10)}</span></div>
      ${t.note ? `<div class="item-note">${esc(t.note)}</div>` : ""}
      ${actions.length ? `<div class="item-actions">${actions.join("")}</div>` : ""}
    </div>`;
  }

  function renderEvidence(e) {
    return `<div class="item-card">
      <div class="item-top">
        <b><a class="file-link" href="/api/evidence/${encodeURIComponent(e.id)}/download" target="_blank" rel="noopener">${esc(e.filename)}</a></b>
        <span class="muted" style="font-size:9px">${fmtSize(e.size)}</span>
      </div>
      <div class="item-meta"><span>${fmtTime(e.captured_at || e.created_at)}</span>${e.lon != null ? `<span>${num(e.lon, 5)}°E, ${num(e.lat, 5)}°N</span>` : ""}<span>${esc(e.mime || "文件")}</span></div>
      <div class="item-note mono hash">SHA-256 ${esc(e.sha256)}</div>
      ${e.note ? `<div class="item-note">${esc(e.note)}</div>` : ""}
    </div>`;
  }

  function renderReview(r) {
    return `<div class="item-card">
      <div class="item-top"><b>${esc(r.decision)}</b><span class="muted" style="font-size:9px">${fmtTime(r.created_at)}</span></div>
      <div class="item-meta"><span>复核人：${esc(r.reviewer_name || "—")}</span></div>
      ${r.comment ? `<div class="item-note">${esc(r.comment)}</div>` : ""}
    </div>`;
  }

  function renderAudit(a) {
    let detail = a.detail || "";
    if (detail.length > 120) detail = detail.slice(0, 120) + "…";
    return `<div class="audit-item">
      <span>${fmtTime(a.at)}</span>
      <b>${esc(a.actor_name || "—")}</b>
      <span>${esc(ACTION_LABELS[a.action] || a.action)}${detail ? ` · ${esc(detail)}` : ""}</span>
    </div>`;
  }

  function bindCaseEvents(c) {
    $$("[data-action]", $("#case-main")).forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = btn.dataset.action;
        if (action === "refresh") selectCase(c.id);
        if (action === "archive") archiveCase(c);
        if (action === "task-status") updateTask(btn.dataset.task, btn.dataset.status);
        if (action === "verify-chain") verifyChain(c.id);
        if (action === "revoke-arrival") revokeArrival(c.id, btn.dataset.token);
        if (action === "arrival-circle") loadArrivalCircle(c);
        if (action === "arrival-geojson") downloadArrivalGeoJSON(c);
        if (action === "drone-plan") loadDronePlan(c);
        if (action === "drone-kml") downloadDroneFile(c, "kml");
        if (action === "drone-csv") downloadDroneFile(c, "csv");
        if (action === "legal-search") searchLegal(c);
        if (action === "legal-txt") downloadLegalTxt(c);
      });
    });
    const taskForm = $("#task-form");
    if (taskForm) taskForm.addEventListener("submit", (ev) => createTask(ev, c));
    const evidenceForm = $("#evidence-form");
    if (evidenceForm) evidenceForm.addEventListener("submit", (ev) => uploadEvidence(ev, c));
    const reviewBtn = $("#review-submit");
    if (reviewBtn) reviewBtn.addEventListener("click", () => submitReview(c));
    const verificationForm = $("#verification-form");
    if (verificationForm) verificationForm.addEventListener("submit", (ev) => saveVerification(ev, c));
    const arrivalForm = $("#arrival-form");
    if (arrivalForm) arrivalForm.addEventListener("submit", (ev) => issueArrivalToken(ev, c));
    loadArrivalTokens(c.id);
    fillLegalHazards();
    const legalInput = $("#legal-q");
    if (legalInput && !legalInput.dataset.bound) {
      legalInput.dataset.bound = "1";
      legalInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          searchLegal(c);
        }
      });
      let legalTimer = 0;
      legalInput.addEventListener("input", () => {
        window.clearTimeout(legalTimer);
        if (!legalInput.value.trim()) return;
        legalTimer = window.setTimeout(() => searchLegal(c), 350);
      });
      const legalHazard = $("#legal-hazard");
      if (legalHazard) {
        legalHazard.addEventListener("change", () => {
          if (legalInput.value.trim()) searchLegal(c);
        });
      }
    }
    loadLegalChecklist(c);
  }

  async function loadArrivalCircle(c) {
    const minutes = Number($("#arrival-minutes").value) || 30;
    const button = $('[data-action="arrival-circle"]');
    const download = $('[data-action="arrival-geojson"]');
    button.disabled = true;
    button.textContent = "计算中…";
    try {
      const data = await api(`/api/cases/${encodeURIComponent(c.id)}/arrival-circle?minutes=${minutes}`);
      state.arrival = data;
      $("#arrival-result").innerHTML = renderArrival(data);
      if (download) download.disabled = false;
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "计算到达圈";
    }
  }

  function renderArrival(data) {
    const radius = data.radius_km || {};
    const degraded = !!data.degraded;
    const modeText = data.mode === "osrm" ? "OSRM 路网采样" : "直线降级";
    const validText = data.mode === "osrm" ? `${data.valid_bearings}/${data.bearings} 个方位` : "非路网结果";
    const cacheText = data.cache && data.cache.hit ? `缓存 ${Math.round(data.cache.age_s || 0)} 秒前` : "实时计算";
    return `
      <div class="arrival-summary">
        ${arrivalSvg(data)}
        <div class="arrival-stats">
          <div><span>模式</span><b class="${degraded ? "bad-text" : "ok-text"}">${modeText}</b></div>
          <div><span>有效采样</span><b>${validText}</b></div>
          <div><span>半径范围</span><b>${num(radius.min, 1)} – ${num(radius.max, 1)} km</b></div>
          <div><span>平均半径</span><b>${num(radius.mean, 1)} km</b></div>
          ${data.center_snap_m != null ? `<div><span>中心吸附道路</span><b>${num(Number(data.center_snap_m) / 1000, 1)} km</b></div>` : ""}
          <div><span>计算</span><b>${cacheText}</b></div>
        </div>
      </div>
      ${degraded ? `<p class="arrival-warning">${esc(data.reason || "路网结果不可用，已降级。")}</p>` : ""}
      <p class="form-note">${esc(data.disclaimer || "")}</p>
      <p class="form-note">来源：${esc(data.source || "—")}</p>`;
  }

  function arrivalSvg(data) {
    const ring = data.polygon && data.polygon.coordinates && data.polygon.coordinates[0];
    const points = (ring || []).slice(0, -1);
    if (points.length < 3) return "";
    const center = data.center;
    const cosLat = Math.cos(center.lat * Math.PI / 180);
    const projected = points.map(([lon, lat]) => ({
      x: (lon - center.lon) * cosLat,
      y: lat - center.lat,
    }));
    const xs = projected.map((p) => p.x);
    const ys = projected.map((p) => p.y);
    const spanX = Math.max(Math.max(...xs) - Math.min(...xs), 1e-6);
    const spanY = Math.max(Math.max(...ys) - Math.min(...ys), 1e-6);
    const scale = Math.min(230 / spanX, 160 / spanY);
    const cx = 140;
    const cy = 105;
    const screen = projected.map((p) => [cx + p.x * scale, cy - p.y * scale]);
    const path = `M${screen.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L")} Z`;
    const warn = data.degraded ? "arrival-area-warn" : "";
    return `<svg class="arrival-svg" viewBox="0 0 280 210" role="img" aria-label="车行到达圈示意">
      <circle cx="${cx}" cy="${cy}" r="88" class="arrival-ring"></circle>
      <path d="${path}" class="arrival-area ${warn}"></path>
      <circle cx="${cx}" cy="${cy}" r="4.5" class="arrival-center"></circle>
      <text x="${cx + 8}" y="${cy - 8}" class="arrival-label">案件点</text>
      <text x="258" y="24" class="arrival-north">N</text>
      <path d="M258 28 L254 38 L262 38 Z" class="arrival-north-arrow"></path>
      <text x="12" y="198" class="arrival-label">${num(data.radius_km && data.radius_km.max, 1)} km</text>
    </svg>`;
  }

  function downloadArrivalGeoJSON(c) {
    const data = state.arrival;
    if (!data || !data.geojson) return toast("请先计算到达圈", true);
    const blob = new Blob([JSON.stringify(data.geojson, null, 2)], { type: "application/geo+json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${c.case_no}_到达圈_${data.minutes}min.geojson`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("GeoJSON 已下载");
  }

  async function loadDronePlan(c) {
    const button = $('[data-action="drone-plan"]');
    const kmlButton = $('[data-action="drone-kml"]');
    const csvButton = $('[data-action="drone-csv"]');
    button.disabled = true;
    button.textContent = "规划中…";
    try {
      const body = {
        pattern: $("#drone-pattern").value,
        size_km: Number($("#drone-size").value) || 0.6,
        spacing_m: Number($("#drone-spacing").value) || 80,
        rel_height_m: Number($("#drone-height").value) || 80,
        speed_mps: Number($("#drone-speed").value) || 8,
        direction_deg: Number($("#drone-direction").value) || 0,
        sample_m: 50,
        title: `${c.case_no} 无人机核查航线`,
      };
      const data = await api(`/api/cases/${encodeURIComponent(c.id)}/drone-plan`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      state.drone = data;
      $("#drone-result").innerHTML = renderDrone(data);
      if (kmlButton) kmlButton.disabled = false;
      if (csvButton) csvButton.disabled = false;
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "规划航线";
    }
  }

  function renderDrone(data) {
    const stats = data.stats || {};
    const degraded = !!data.degraded;
    const modeText = data.pattern === "perimeter" ? "环绕" : "往复扫描";
    const terrain = stats.terrain_min_m == null
      ? "无 DEM"
      : `${num(stats.terrain_min_m, 0)} – ${num(stats.terrain_max_m, 0)} m`;
    return `
      <div class="drone-summary">
        ${droneSvg(data)}
        <div class="drone-stats">
          <div><span>模式</span><b>${modeText}</b></div>
          <div><span>航点</span><b>${num(stats.waypoints, 0)} 个 / ${num(stats.legs, 0)} 段</b></div>
          <div><span>航线距离</span><b>${num(stats.distance_km, 2)} km</b></div>
          <div><span>预计航时</span><b>${num(stats.flight_min, 1)} 分钟</b></div>
          <div><span>地形高程</span><b>${terrain}</b></div>
          <div><span>绝对高度</span><b>${num(stats.alt_min_m, 0)} – ${num(stats.alt_max_m, 0)} m</b></div>
          ${stats.missing_elevation ? `<div><span>缺失高程</span><b class="bad-text">${num(stats.missing_elevation, 0)} 点</b></div>` : ""}
        </div>
      </div>
      ${degraded ? `<p class="drone-warning">${esc(data.reason || "部分航点缺少 DEM 高程，已按相对高度估算。")}</p>` : ""}
      <p class="form-note">航时按直线距离、设定速度和每转弯 4 秒估算，不含爬升、风场和电池余量。</p>
      <p class="form-note">${esc(data.disclaimer || "")}</p>`;
  }

  function droneSvg(data) {
    const points = data.waypoints || [];
    if (points.length < 2) return "";
    const center = data.center || points[0];
    const cosLat = Math.cos(center.lat * Math.PI / 180);
    const projected = points.map((p) => ({
      x: (p.lon - center.lon) * cosLat,
      y: p.lat - center.lat,
    }));
    const xs = projected.map((p) => p.x);
    const ys = projected.map((p) => p.y);
    const spanX = Math.max(Math.max(...xs) - Math.min(...xs), 1e-7);
    const spanY = Math.max(Math.max(...ys) - Math.min(...ys), 1e-7);
    const scale = Math.min(230 / spanX, 155 / spanY);
    const cx = 140;
    const cy = 105;
    const screen = projected.map((p) => [cx + p.x * scale, cy - p.y * scale]);
    const path = `M${screen.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L")}`;
    const first = screen[0];
    const last = screen[screen.length - 1];
    return `<svg class="drone-svg" viewBox="0 0 280 210" role="img" aria-label="无人机航线示意">
      <path d="M140 24 L140 186 M20 105 L260 105" class="drone-grid"></path>
      <path d="${path}" class="drone-path"></path>
      <circle cx="${first[0].toFixed(1)}" cy="${first[1].toFixed(1)}" r="4.5" class="drone-start"></circle>
      <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4.5" class="drone-end"></circle>
      <text x="${first[0] + 7}" y="${first[1] - 7}" class="arrival-label">起</text>
      <text x="${last[0] + 7}" y="${last[1] + 14}" class="arrival-label">终</text>
      <text x="258" y="24" class="arrival-north">N</text>
      <path d="M258 28 L254 38 L262 38 Z" class="arrival-north-arrow"></path>
      <text x="12" y="198" class="arrival-label">${num(data.size_km, 2)} km 范围</text>
    </svg>`;
  }

  function downloadDroneFile(c, kind) {
    const data = state.drone;
    if (!data) return toast("请先规划航线", true);
    const content = kind === "kml" ? data.kml : data.csv;
    if (!content) return toast("没有可下载的航线文件", true);
    const mime = kind === "kml"
      ? "application/vnd.google-earth.kml+xml"
      : "text/csv;charset=utf-8";
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${c.case_no}_无人机航线.${kind}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast(`${kind.toUpperCase()} 已下载`);
  }

  function fillLegalHazards() {
    const select = $("#legal-hazard");
    if (!select || select.dataset.filled) return;
    Object.entries(LEGAL_HAZARDS).forEach(([code, label]) => {
      const option = document.createElement("option");
      option.value = code;
      option.textContent = label;
      select.appendChild(option);
    });
    select.dataset.filled = "1";
  }

  function renderLegalEntries(entries, primary, suggestions) {
    if (!entries || !entries.length) {
      const tips = (suggestions || [])
        .map((item) => `${esc(item.article || "")} ${esc(item.heading || "")}`.trim())
        .filter(Boolean)
        .join("、");
      return `<p class="empty-small">没有匹配的法条条目；${tips ? `相近条目：${tips}。` : "请换关键词或灾种。"}</p>`;
    }
    const primaryId = primary && primary.id;
    return entries.map((entry, index) => {
      const evidence = (entry.evidence || []).map((item) => `<li>${esc(item)}</li>`).join("");
      const notes = (entry.notes || []).map((item) => `<li>${esc(item)}</li>`).join("");
      const isPrimary = primaryId && entry.id === primaryId;
      return `<article class="legal-entry${isPrimary ? " primary" : ""}">
        <div class="legal-entry-head">
          <span class="legal-index">${index + 1}</span>
          <div><b>${esc(entry.law || "")} ${esc(entry.article || "")}</b><span>${esc(entry.heading || "")}</span>${isPrimary ? `<em class="legal-primary-badge">主要适用</em>` : ""}</div>
        </div>
        ${entry.match_reason ? `<p class="legal-match">${esc(entry.match_reason)}</p>` : ""}
        <p class="legal-summary-text">${esc(entry.summary || "")}</p>
        ${evidence ? `<details class="legal-details"><summary>取证方向（${(entry.evidence || []).length} 项）</summary><ul>${evidence}</ul></details>` : ""}
        ${notes ? `<details class="legal-details"><summary>适用提示</summary><ul>${notes}</ul></details>` : ""}
      </article>`;
    }).join("");
  }

  function renderLegal(data, mode) {
    const entries = data.entries || data.items || [];
    const checklist = data.checklist || [];
    const existing = data.existing_evidence || [];
    const primary = data.primary || null;
    const hazardLabel = data.inferred_hazard_label
      || (data.hazard ? (LEGAL_HAZARDS[data.hazard] || data.hazard) : "全部灾种");
    const checklistHtml = checklist.length
      ? `<details class="legal-checklist">
          <summary>取证清单（${checklist.length} 项，去重后）</summary>
          <ul>${checklist.map((item) => `<li>${esc(item.item)}</li>`).join("")}</ul>
        </details>`
      : "";
    const banner = mode === "search"
      ? `<div class="legal-banner">
          <div><span>主要法条</span><b>${primary ? esc(`${primary.article || ""} ${primary.heading || ""}`.trim()) : "未命中"}</b></div>
          <div><span>命中法条</span><b>${num(entries.length, 0)} 条</b></div>
          <div><span>匹配总数</span><b>${num(data.total, 0)} 条</b></div>
        </div>`
      : `<div class="legal-banner">
          <div><span>识别灾种</span><b>${esc(hazardLabel)}</b></div>
          <div><span>匹配法条</span><b>${num(entries.length, 0)} 条</b></div>
          <div><span>取证方向</span><b>${num(checklist.length, 0)} 项</b></div>
          <div><span>案件已有证据</span><b>${num(existing.length, 0)} 份</b></div>
        </div>`;
    const primaryHtml = primary
      ? `<div class="legal-primary">
          <span class="legal-primary-tag">主要法条</span>
          <div>
            <b>${esc(primary.law || "")} ${esc(primary.article || "")}</b>
            <span>${esc(primary.heading || "")}</span>
            ${primary.match_reason ? `<em>${esc(primary.match_reason)}</em>` : ""}
          </div>
        </div>`
      : "";
    return `
      ${banner}
      ${primaryHtml}
      ${data.note ? `<p class="note-line">${esc(data.note)}</p>` : ""}
      ${mode === "search" ? `<p class="form-note">按关键词匹配度排序，主要法条为检索推荐；结果仅供核查参考，未做法律适用判断。</p>` : ""}
      ${checklistHtml}
      <div class="legal-entries">${renderLegalEntries(entries, primary, data.suggestions)}</div>
      <p class="form-note">${esc(data.disclaimer || "草稿库，须由法制部门复核后引用。")}</p>
      <p class="form-note">库状态：${esc(data.review_status || "draft_requires_legal_review")}；本清单不构成构罪、处罚或移送结论。</p>`;
  }

  let legalSeq = 0;

  async function loadLegalChecklist(c) {
    const button = $('[data-action="legal-txt"]');
    const seq = legalSeq;
    try {
      const data = await api(`/api/cases/${encodeURIComponent(c.id)}/legal-checklist`);
      if (seq !== legalSeq) return;
      const input = $("#legal-q");
      if (input && input.value.trim()) return;
      state.legal = data;
      $("#legal-result").innerHTML = renderLegal(data, "case");
      if (button) button.disabled = false;
    } catch (err) {
      if (seq !== legalSeq) return;
      const input = $("#legal-q");
      if (input && input.value.trim()) return;
      $("#legal-result").innerHTML = `<p class="empty-small bad-text">${esc(err.message)}</p>`;
    }
  }

  async function searchLegal(c) {
    const input = $("#legal-q");
    const select = $("#legal-hazard");
    const query = input ? input.value.trim() : "";
    const hazard = select ? select.value : "";
    if (!query && !hazard) {
      if (c) loadLegalChecklist(c);
      return;
    }
    const seq = ++legalSeq;
    const button = $('[data-action="legal-search"]');
    if (button) {
      button.disabled = true;
      button.textContent = "检索中…";
    }
    try {
      const data = await api(`/api/legal/search?q=${encodeURIComponent(query)}&hazard=${encodeURIComponent(hazard)}&limit=30`);
      if (seq !== legalSeq) return;
      $("#legal-result").innerHTML = renderLegal(data, "search");
    } catch (err) {
      if (seq === legalSeq) toast(err.message, true);
    } finally {
      if (seq === legalSeq && button) {
        button.disabled = false;
        button.textContent = "检索";
      }
    }
  }

  function downloadLegalTxt(c) {
    const data = state.legal;
    if (!data) return toast("请先等待案件清单加载", true);
    const lines = [
      `案件号：${c.case_no || ""}`,
      `案件标题：${c.title || ""}`,
      `识别灾种：${data.hazard_label || "未识别"}`,
      `库状态：${data.review_status || ""}`,
      "",
      data.note || "",
      "",
      "一、法条与取证方向",
    ];
    (data.entries || []).forEach((entry, index) => {
      lines.push(`${index + 1}. ${entry.law || ""} ${entry.article || ""}（${entry.heading || ""}）`);
      lines.push(`   ${entry.summary || ""}`);
      (entry.evidence || []).forEach((item) => lines.push(`   - ${item}`));
      (entry.notes || []).forEach((item) => lines.push(`   注：${item}`));
      lines.push("");
    });
    lines.push("二、去重后的取证清单");
    (data.checklist || []).forEach((item, index) => lines.push(`${index + 1}. ${item.item}`));
    lines.push("", "三、边界", data.disclaimer || "草稿库，须由法制部门复核后引用。");
    const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${c.case_no}_法条与取证清单.txt`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("法条清单已下载");
  }

  async function refreshCurrent() {
    if (!state.current) return;
    const id = state.current.id;
    await loadCases();
    await selectCase(id, false);
  }

  async function createTask(ev, c) {
    ev.preventDefault();
    const title = $("#task-title").value.trim();
    if (!title) return toast("请填写任务标题", true);
    const body = {
      title,
      assignee_id: $("#task-assignee") ? (Number($("#task-assignee").value) || null) : null,
      due_at: $("#task-due").value || null,
      note: null,
    };
    const btn = ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api(`/api/cases/${encodeURIComponent(c.id)}/tasks`, { method: "POST", body: JSON.stringify(body) });
      toast("任务已创建");
      await refreshCurrent();
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  async function updateTask(taskId, status) {
    let note = null;
    if (status === "已退回") {
      note = window.prompt("请填写退回原因（必填）");
      if (note === null) return;
      if (!note.trim()) return toast("退回必须填写原因", true);
    }
    if (status === "已完成") {
      if (!window.confirm("确认完成任务？系统会检查案件是否已有至少一份证据。")) return;
    }
    try {
      await api(`/api/tasks/${encodeURIComponent(taskId)}`, { method: "PATCH", body: JSON.stringify({ status, note }) });
      toast(`任务状态已更新为“${status}”`);
      await refreshCurrent();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function uploadEvidence(ev, c) {
    ev.preventDefault();
    const fileInput = $("#evidence-file");
    if (!fileInput.files.length) return toast("请选择证据文件", true);
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    const taskId = $("#evidence-task").value;
    if (taskId) fd.append("task_id", taskId);
    const captured = $("#evidence-captured").value;
    if (captured) fd.append("captured_at", captured);
    const lon = $("#evidence-lon").value;
    const lat = $("#evidence-lat").value;
    if (lon) fd.append("lon", lon);
    if (lat) fd.append("lat", lat);
    const note = $("#evidence-note").value.trim();
    if (note) fd.append("note", note);
    const btn = ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    btn.textContent = "正在计算 SHA-256…";
    try {
      const result = await api(`/api/cases/${encodeURIComponent(c.id)}/evidence`, { method: "POST", body: fd });
      toast(result.duplicate ? "相同文件已存在，未重复保存" : "证据已上传并计算 SHA-256");
      await refreshCurrent();
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "上传并计算 SHA-256";
    }
  }

  async function submitReview(c) {
    const decision = $("#review-decision").value;
    const comment = $("#review-comment").value.trim();
    if (decision !== "通过" && !comment) return toast("退回/补充核查必须填写意见", true);
    try {
      await api(`/api/cases/${encodeURIComponent(c.id)}/review`, { method: "POST", body: JSON.stringify({ decision, comment }) });
      toast(`复核已提交：${decision}`);
      await refreshCurrent();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function archiveCase(c) {
    if (!window.confirm("归档后案件只读，确认归档？")) return;
    try {
      await api(`/api/cases/${encodeURIComponent(c.id)}/archive`, { method: "POST", body: JSON.stringify({}) });
      toast("案件已归档");
      await refreshCurrent();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function verifyChain(caseId) {
    const box = $("#chain-result");
    if (box) box.textContent = "正在重算每个证据文件的 SHA-256…";
    try {
      const r = await api(`/api/cases/${encodeURIComponent(caseId)}/evidence-chain?deep=1`);
      const bad = (r.items || []).filter((i) => i.file_ok === false);
      if (!box) return;
      box.innerHTML = r.valid
        ? `<span class="ok-text">校验通过</span>：${r.items.length} 份证据链头一致，文件字节与登记哈希一致。`
        : `<span class="bad-text">校验未通过</span>：${bad.length ? bad.map((i) => `${esc(i.filename)}（${esc(i.file_error || "哈希不一致")}）`).join("；") : esc(r.chain.note || "链头不一致")}`;
    } catch (err) {
      if (box) box.innerHTML = `<span class="bad-text">${esc(err.message)}</span>`;
    }
  }

  async function saveVerification(ev, c) {
    ev.preventDefault();
    const verdict = $("#verification-verdict").value;
    const note = $("#verification-note").value.trim();
    const evidenceId = $("#verification-evidence").value || null;
    const btn = ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api(`/api/cases/${encodeURIComponent(c.id)}/verification`, {
        method: "POST",
        body: JSON.stringify({ verdict, note: note || null, evidence_id: evidenceId }),
      });
      toast("核查结论已保存");
      await refreshCurrent();
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  async function issueArrivalToken(ev, c) {
    ev.preventDefault();
    const body = {
      hours: Number($("#arrival-hours").value) || 24,
      max_uses: Number($("#arrival-max").value) || 0,
      note: $("#arrival-note").value.trim() || null,
    };
    const btn = ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const r = await api(`/api/cases/${encodeURIComponent(c.id)}/arrival-tokens`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const box = $("#arrival-token-result");
      box.innerHTML = `<div class="arrival-card">
        <div class="item-top"><b>到场码已生成</b><span class="muted">${esc(r.expires_at)} 到期</span></div>
        <div class="arrival-flex">
          <div>
            <div class="mono token-text">${esc(r.token)}</div>
            <div class="item-actions" style="margin-top:8px">
              <button class="btn btn-ghost btn-sm" type="button" data-copy-token="${esc(r.token)}">复制到场码</button>
              <a class="btn btn-sm" href="${esc(r.mobile_url)}" target="_blank" rel="noopener">打开移动端</a>
            </div>
            <p class="form-note">只显示这一次。关闭后无法再次查看明文，只能重新生成。</p>
          </div>
          <img class="arrival-qr" src="${esc(r.qr_data_url)}" alt="到场码二维码">
        </div>
      </div>`;
      const copyBtn = $("[data-copy-token]", box);
      if (copyBtn) copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(copyBtn.dataset.copyToken).then(
          () => toast("到场码已复制"),
          () => toast("复制失败，请手动选择文本", true),
        );
      });
      $("#arrival-note").value = "";
      await loadArrivalTokens(c.id);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  async function loadArrivalTokens(caseId) {
    const list = $("#arrival-list");
    if (!list) return;
    try {
      const data = await api(`/api/cases/${encodeURIComponent(caseId)}/arrival-tokens`, { authRedirect: false });
      const items = data.items || [];
      list.innerHTML = items.length ? items.map((t) => `
        <div class="item-card">
          <div class="item-top"><b class="mono">${esc(t.token_hint)}</b><span class="status-pill ${t.revoked ? "st-已退回" : ""}">${t.revoked ? "已作废" : "有效"}</span></div>
          <div class="item-meta"><span>${esc(t.created_by_name || "—")} 生成</span><span>到期 ${fmtTime(t.expires_at)}</span><span>已用 ${t.uses}${t.max_uses ? ` / ${t.max_uses}` : ""} 次</span></div>
          ${t.note ? `<div class="item-note">${esc(t.note)}</div>` : ""}
          ${!t.revoked && !isClosed(state.current) ? `<div class="item-actions"><button class="mini-btn bad" data-action="revoke-arrival" data-token="${t.id}" type="button">作废</button></div>` : ""}
        </div>`).join("") : `<p class="empty-small">尚未生成到场码。</p>`;
    } catch (err) {
      list.innerHTML = `<p class="empty-small">读取到场码失败：${esc(err.message)}</p>`;
    }
  }

  async function revokeArrival(caseId, tokenId) {
    if (!window.confirm("作废后该到场码立即失效，确认？")) return;
    try {
      await api(`/api/cases/${encodeURIComponent(caseId)}/arrival-tokens/${encodeURIComponent(tokenId)}/revoke`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      toast("到场码已作废");
      await loadArrivalTokens(caseId);
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* ---------- 建案弹窗 ---------- */
  async function openCaseModal(prefill) {
    if (!$("#imagery-modal").hidden) closeImageryModal();
    $("#modal-backdrop").hidden = false;
    $("#case-modal").hidden = false;
    $("#case-form-error").textContent = "";
    $("#case-title").value = "";
    $("#case-summary").value = "";
    $("#case-due").value = "";
    if (!canManage()) $("#assignee-wrap").hidden = true;
    else {
      $("#assignee-wrap").hidden = false;
      $("#case-assignee").innerHTML = `<option value="">暂不指派</option>` +
        state.users.map((u) => `<option value="${u.id}">${esc(u.display_name)} · ${esc(u.role_label || roleLabel(u.role))}</option>`).join("");
    }
    try {
      await loadSources();
      if (prefill) {
        $("#case-source-type").value = prefill.type;
        populateSources();
        if ([...$("#case-source-id").options].some((o) => o.value === prefill.id)) {
          $("#case-source-id").value = prefill.id;
          updateSourcePreview();
        }
      } else {
        populateSources();
      }
      $("#case-source-id").focus();
    } catch (err) {
      $("#source-preview").textContent = `线索加载失败：${err.message}`;
    }
  }

  function closeCaseModal() {
    $("#modal-backdrop").hidden = true;
    $("#case-modal").hidden = true;
  }

  async function loadSources() {
    if (state.hotspots && state.grids && state.imageryScenes) return;
    const [h, g, watch] = await Promise.all([
      api("/api/hotspots"),
      api("/api/risk-grid"),
      api("/api/imagery/watch", { authRedirect: false }).catch(() => null),
    ]);
    state.hotspots = (h.features || []).map((f) => ({ id: f.properties.id, ...f.properties }))
      .sort((a, b) => (Number(b.priority_score) || 0) - (Number(a.priority_score) || 0));
    state.grids = (g.features || []).map((f) => ({ id: f.properties.cell_id, ...f.properties }))
      .sort((a, b) => (Number(b.current_danger) || 0) - (Number(a.current_danger) || 0));
    const seen = new Set();
    const scenes = [];
    for (const key of ["new_scenes", "latest"]) {
      for (const item of (watch && watch[key]) || []) {
        if (!item || !item.id || seen.has(item.id)) continue;
        seen.add(item.id);
        scenes.push({ ...item, name: `新影像 ${item.id}` });
      }
    }
    state.imageryScenes = scenes.sort((a, b) => String(b.datetime || "").localeCompare(String(a.datetime || "")));
  }

  function populateSources() {
    const type = $("#case-source-type").value;
    const rows = type === "grid" ? state.grids : type === "imagery" ? (state.imageryScenes || []) : state.hotspots;
    const sel = $("#case-source-id");
    sel.innerHTML = rows.map((p) => {
      const label = type === "grid"
        ? `${p.id} · ${p.dominant_hazard_name || p.dominant_hazard || "综合"} · 危险 ${num(p.current_danger)}`
        : type === "imagery"
          ? `${p.id} · ${String(p.datetime || "").slice(0, 16).replace("T", " ")} · 云 ${num(p.cloud_cover)}%`
          : `${p.id} · ${p.name || "未命名"} · 优先级 ${num(p.priority_score)}`;
      return `<option value="${esc(p.id)}">${esc(label)}</option>`;
    }).join("");
    updateSourcePreview();
  }

  function currentSource() {
    const type = $("#case-source-type").value;
    const id = $("#case-source-id").value;
    const rows = type === "grid" ? state.grids : type === "imagery" ? (state.imageryScenes || []) : state.hotspots;
    return (rows || []).find((p) => p.id === id);
  }

  function updateSourcePreview() {
    const p = currentSource();
    if (!p) {
      $("#source-preview").textContent = "选择线索后显示真实分数与因子。";
      return;
    }
    const type = $("#case-source-type").value;
    if (type === "imagery") {
      $("#source-preview").innerHTML = `<b>${esc(p.name || p.id)}</b><br/>
        成像 ${esc(String(p.datetime || "—").replace("T", " "))} · 云量 ${num(p.cloud_cover)}% · ${esc(p.platform || "Sentinel-2")}<br/>
        <span class="bad-text">影像到访不等于发现异常</span>：建案后到现场核查，不能直接把“有新影像”写成违法事实。`;
      if (!$("#case-title").value.trim()) $("#case-title").value = `新影像 ${p.id} 核查`;
      return;
    }
    const name = p.name || (type === "grid" ? `格网 ${p.cell_id}` : p.id);
    const score = type === "grid" ? p.priority : p.priority_score;
    $("#source-preview").innerHTML = `<b>${esc(name)}</b><br/>
      历史优先级 <b>${num(score)}</b> · 当前危险 <b>${num(p.current_danger)}</b> · 主导 ${esc(p.dominant_hazard_name || p.dominant_class || "综合风险")}<br/>
      ${p.current_danger_label ? esc(p.current_danger_label) + " · " : ""}${p.danger_label ? esc(p.danger_label) : ""}`;
    if (!$("#case-title").value.trim()) {
      $("#case-title").value = `${name}核查`;
    }
  }

  async function submitCase(ev) {
    ev.preventDefault();
    const sourceId = $("#case-source-id").value;
    if (!sourceId) return;
    const body = {
      source_type: $("#case-source-type").value,
      source_id: sourceId,
      title: $("#case-title").value.trim() || null,
      summary: $("#case-summary").value.trim() || null,
      assignee_id: $("#case-assignee") && !$("#assignee-wrap").hidden ? (Number($("#case-assignee").value) || null) : null,
      due_at: $("#case-due").value || null,
    };
    const btn = ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const created = await api("/api/cases", { method: "POST", body: JSON.stringify(body) });
      closeCaseModal();
      toast(`案件已创建：${created.case_no}`);
      await loadCases();
      await selectCase(created.id);
    } catch (err) {
      $("#case-form-error").textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  }

  /* ---------- 登录与启动 ---------- */
  function bindLogin() {
    $("#login-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      $("#login-error").textContent = "";
      const username = $("#login-username").value.trim();
      const password = $("#login-password").value;
      if (!username || !password) return;
      const btn = ev.target.querySelector("button[type=submit]");
      btn.disabled = true;
      btn.textContent = "正在验证…";
      try {
        const result = await api("/api/auth/login", {
          method: "POST", body: JSON.stringify({ username, password }), authRedirect: false,
        });
        state.user = result.user;
        showApp();
      } catch (err) {
        $("#login-error").textContent = err.message;
      } finally {
        btn.disabled = false;
        btn.textContent = "进入工作台";
      }
    });
    $$(".demo-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        $("#login-username").value = chip.dataset.user;
        $("#login-password").value = chip.dataset.pass;
        $("#login-username").focus();
      });
    });
  }

  function bindApp() {
    $("#logout-btn").addEventListener("click", async () => {
      try { await api("/api/auth/logout", { method: "POST", body: JSON.stringify({}) }); } catch (_) {}
      showLogin();
    });
    $("#new-case-btn").addEventListener("click", () => openCaseModal());
    $("#imagery-btn").addEventListener("click", openImageryModal);
    $("#imagery-close").addEventListener("click", closeImageryModal);
    $("#imagery-refresh").addEventListener("click", refreshImagery);
    $("#case-modal-close").addEventListener("click", closeCaseModal);
    $("#case-cancel").addEventListener("click", closeCaseModal);
    $("#modal-backdrop").addEventListener("click", () => {
      closeCaseModal();
      closeImageryModal();
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      if (!$("#case-modal").hidden) closeCaseModal();
      if (!$("#imagery-modal").hidden) closeImageryModal();
    });
    $("#case-form").addEventListener("submit", submitCase);
    $("#case-source-type").addEventListener("change", populateSources);
    $("#case-source-id").addEventListener("change", updateSourcePreview);
    $("#case-search").addEventListener("input", renderCaseList);
    $("#case-filter").addEventListener("change", renderCaseList);
    $("#case-list").addEventListener("click", (ev) => {
      const item = ev.target.closest(".case-item");
      if (item) selectCase(item.dataset.id);
    });
  }

  async function boot() {
    const started = performance.now();
    bindLogin();
    bindApp();
    try {
      const me = await api("/api/auth/me", { authRedirect: false });
      if (me.authenticated && me.user) {
        state.user = me.user;
        showApp();
      } else {
        showLogin();
      }
    } catch (_) {
      showLogin();
      $("#login-error").textContent = "无法连接服务，请确认后端已启动。";
    }
    // 保留与一张图一致的警徽入场节奏；加载完成后再显示登录/工作台。
    const remain = Math.max(0, 1150 - (performance.now() - started));
    await new Promise((resolve) => setTimeout(resolve, remain));
    const screen = $("#boot-screen");
    if (!screen) return;
    screen.classList.add("boot-leaving");
    setTimeout(() => { screen.hidden = true; }, 820);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();

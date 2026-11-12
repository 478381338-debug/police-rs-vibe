(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const TOKEN_KEY = "qs_arrival_token";
  const CONTEXT_KEY = "qs_mobile_context";
  const LAST_CHECKIN_PREFIX = "qs_last_checkin:";
  const DB_NAME = "qs-mobile";
  const DB_VERSION = 1;
  const MAX_FILES = 8;
  const MAX_TOTAL = 45 * 1024 * 1024;

  const state = {
    token: "",
    context: null,
    coords: null,
    files: [],
    queue: [],
    syncing: false,
    db: null,
    lastCheckinId: null,
  };

  /* ---------------- 基础工具 ---------------- */
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function toast(message, bad = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("bad", !!bad);
    el.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.remove("show"), 3600);
  }

  function fmtTime(value) {
    if (!value) return "未设置";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function fmtSize(bytes) {
    if (!bytes && bytes !== 0) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function fmtDist(meters) {
    if (meters == null || !Number.isFinite(meters)) return "—";
    if (meters < 1000) return `${Math.round(meters)} m`;
    return `${(meters / 1000).toFixed(1)} km`;
  }

  function haversine(lon1, lat1, lon2, lat2) {
    const R = 6371000;
    const rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad;
    const dLon = (lon2 - lon1) * rad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  function bearing(lon1, lat1, lon2, lat2) {
    const rad = Math.PI / 180;
    const y = Math.sin((lon2 - lon1) * rad) * Math.cos(lat2 * rad);
    const x = Math.cos(lat1 * rad) * Math.sin(lat2 * rad) -
      Math.sin(lat1 * rad) * Math.cos(lat2 * rad) * Math.cos((lon2 - lon1) * rad);
    return (Math.atan2(y, x) / rad + 360) % 360;
  }

  function deviceLabel() {
    const ua = String(navigator.userAgent || "").slice(0, 170);
    return ua;
  }

  async function sha256Hex(file) {
    if (!window.crypto || !crypto.subtle || file.size > 20 * 1024 * 1024) return "";
    const buf = await file.arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", buf);
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  /* ---------------- 网络与令牌 ---------------- */
  async function api(path, options = {}) {
    const headers = Object.assign({ "X-Arrival-Token": state.token }, options.headers || {});
    const res = await fetch(path, Object.assign({}, options, { headers, cache: "no-store" }));
    let data = null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      data = await res.json().catch(() => null);
    }
    if (!res.ok) {
      let msg = (data && (data.detail || data.message)) || `请求失败（HTTP ${res.status}）`;
      if (typeof msg !== "string") msg = JSON.stringify(msg);
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function tokenFromHash() {
    const m = String(location.hash || "").match(/(?:^#|&)t=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function rememberCheckin(id) {
    state.lastCheckinId = id;
    const caseId = state.context && state.context.case && state.context.case.id;
    if (caseId) localStorage.setItem(LAST_CHECKIN_PREFIX + caseId, id);
  }

  function recallCheckin() {
    const caseId = state.context && state.context.case && state.context.case.id;
    if (!caseId) return state.lastCheckinId;
    return state.lastCheckinId || localStorage.getItem(LAST_CHECKIN_PREFIX + caseId) || null;
  }

  function clearToken() {
    state.token = "";
    state.context = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(CONTEXT_KEY);
  }

  async function loadContext() {
    try {
      const data = await api("/api/mobile/context");
      state.context = data;
      localStorage.setItem(CONTEXT_KEY, JSON.stringify(data));
      state.lastCheckinId = recallCheckin();
      return data;
    } catch (err) {
      if (err.status === 401 || err.status === 403 || err.status === 404) {
        clearToken();
        throw err;
      }
      const cached = localStorage.getItem(CONTEXT_KEY);
      if (cached) {
        try {
          state.context = JSON.parse(cached);
          state.lastCheckinId = recallCheckin();
          toast("离线模式：显示上次缓存的案件信息", false);
          return state.context;
        } catch (_) {
          /* ignore */
        }
      }
      throw err;
    }
  }

  /* ---------------- 页面状态 ---------------- */
  function showGate(message) {
    $("#gate").hidden = false;
    $("#case-view").hidden = true;
    $("#gate-error").textContent = message || "";
  }

  function showCase() {
    $("#gate").hidden = true;
    $("#case-view").hidden = false;
    renderContext();
    refreshQueue();
  }

  function statusChip(status) {
    const el = $("#case-status");
    el.textContent = status || "—";
    el.className = "chip";
    if (status === "已归档") el.classList.add("ok");
    else if (status === "已复核" || status === "待复核") el.classList.add("warn");
    else if (status === "已退回") el.classList.add("bad");
  }

  function renderContext() {
    const ctx = state.context;
    if (!ctx) return;
    const c = ctx.case || {};
    statusChip(c.status);
    $("#case-no").textContent = c.case_no || "—";
    $("#case-title").textContent = c.title || "未命名案件";
    $("#case-due").textContent = c.due_at ? `截止 ${fmtTime(c.due_at)}` : "未设截止时间";
    const hazard = $("#case-hazard");
    if (c.dominant_hazard) {
      hazard.hidden = false;
      hazard.textContent = `主要疑似问题：${c.dominant_hazard}`;
    } else {
      hazard.hidden = true;
    }
    $("#m-priority").textContent = c.priority_score == null ? "—" : Number(c.priority_score).toFixed(1);
    $("#m-priority").title = c.priority_label || "";
    $("#m-danger").textContent = c.current_danger == null ? "—" : Number(c.current_danger).toFixed(1);
    $("#m-danger").title = c.current_danger_label || "";
    updateDistance();

    $("#case-summary").textContent = c.summary || "指挥中心未填写补充说明。";
    const advice = $("#case-advice");
    if (c.advice) {
      advice.hidden = false;
      advice.textContent = `处置建议：${c.advice}`;
    } else {
      advice.hidden = true;
    }
    $("#case-coords").textContent = (c.lon != null && c.lat != null)
      ? `${Number(c.lon).toFixed(6)}, ${Number(c.lat).toFixed(6)}` : "—";
    $("#case-police").textContent = c.nearest_police
      ? `${c.nearest_police}${c.dist_police_km != null ? ` · 直线 ${Number(c.dist_police_km).toFixed(1)} km` : ""}`
      : "未登记";
    $("#case-contact").textContent = ctx.contact ? `指挥中心 · ${ctx.contact}` : "—";

    const locked = $("#checkin-locked");
    if (ctx.can_checkin === false) {
      locked.hidden = false;
      locked.textContent = `案件当前为“${c.status || "未知"}”，已不能继续签到或上传证据。`;
      $("#checkin-btn").disabled = true;
    } else {
      locked.hidden = true;
      $("#checkin-btn").disabled = false;
    }
    $("#checkin-hint").textContent = ctx.remaining_uses == null
      ? "到场码不限次数；有网络时直接上传，无网络时先保存在本机。"
      : `到场码剩余 ${ctx.remaining_uses} 次；有网络时直接上传，无网络时先保存在本机。`;

    renderTasks(ctx.tasks || []);
    const last = recallCheckin();
    if (last) {
      $("#after-checkin").hidden = false;
      $("#last-checkin").textContent = last;
    } else {
      $("#after-checkin").hidden = true;
    }
    drawSketch();
  }

  function updateDistance() {
    const c = state.context && state.context.case;
    const coords = getCoords();
    if (!c || c.lon == null || c.lat == null || !coords) {
      $("#m-distance").textContent = "—";
      $("#m-distance").title = "";
      return;
    }
    const meters = haversine(coords.lon, coords.lat, Number(c.lon), Number(c.lat));
    $("#m-distance").textContent = fmtDist(meters);
    $("#m-distance").title = "现场 GPS 与案件点的直线距离，不是道路距离";
  }

  function renderTasks(tasks) {
    const list = $("#task-list");
    const select = $("#task-select");
    $("#task-count").textContent = tasks.length ? `${tasks.length} 项` : "无任务";
    list.innerHTML = tasks.length ? tasks.map((t) => `
      <div class="task">
        <div class="task-top"><b>${esc(t.title)}</b><span class="task-status ${esc(t.status)}">${esc(t.status)}</span></div>
        <div class="task-meta"><span class="mono">${esc(t.id)}</span>${t.due_at ? `<span>截止 ${esc(fmtTime(t.due_at))}</span>` : ""}</div>
      </div>`).join("") : `<p class="empty-small">指挥中心尚未派发核查任务，可直接签到并说明现场情况。</p>`;

    const current = select.value;
    select.innerHTML = `<option value="">不关联任务</option>` + tasks.map((t) => {
      const suffix = t.status === "已完成" ? "（已完成）" : t.status === "已退回" ? "（已退回）" : "";
      const disabled = t.status === "已完成" || t.status === "已退回" ? " disabled" : "";
      return `<option value="${esc(t.id)}"${disabled}>${esc(t.title)}${suffix}</option>`;
    }).join("");
    if (current && tasks.some((t) => t.id === current)) select.value = current;
    else {
      const open = tasks.find((t) => t.status === "待接收" || t.status === "核查中");
      if (open) select.value = open.id;
    }
  }

  /* ---------------- 示意 canvas ---------------- */
  function drawSketch() {
    const canvas = $("#sketch");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#08131d");
    bg.addColorStop(1, "#060d14");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = "rgba(56,189,248,.08)";
    ctx.lineWidth = 1;
    for (let x = 0; x <= W; x += 40) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    }
    for (let y = 0; y <= H; y += 40) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }

    const c = state.context && state.context.case;
    const cx = W / 2;
    const cy = H / 2 + 8;
    if (!c || c.lon == null || c.lat == null) {
      ctx.fillStyle = "#63788c";
      ctx.font = "16px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("案件缺坐标", cx, cy);
      return;
    }

    ctx.setLineDash([5, 5]);
    ctx.strokeStyle = "rgba(56,189,248,.22)";
    ctx.beginPath(); ctx.arc(cx, cy, 62, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.arc(cx, cy, 96, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "#38bdf8";
    ctx.beginPath(); ctx.arc(cx, cy, 7, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "rgba(56,189,248,.35)";
    ctx.lineWidth = 8;
    ctx.beginPath(); ctx.arc(cx, cy, 14, 0, Math.PI * 2); ctx.stroke();

    ctx.fillStyle = "#d9e8f5";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("案件点", cx, cy + 40);
    ctx.fillStyle = "#7d93a8";
    ctx.font = "11px sans-serif";
    ctx.fillText(`${Number(c.lon).toFixed(5)}, ${Number(c.lat).toFixed(5)}`, cx, cy + 56);

    const coords = getCoords();
    if (coords) {
      const meters = haversine(coords.lon, coords.lat, Number(c.lon), Number(c.lat));
      const deg = bearing(Number(c.lon), Number(c.lat), coords.lon, coords.lat);
      const scale = Math.min(105, Math.max(18, 110 * Math.min(1, meters / 2000)));
      const rad = (deg - 90) * Math.PI / 180;
      const px = cx + Math.cos(rad) * scale;
      const py = cy + Math.sin(rad) * scale;

      ctx.setLineDash([4, 5]);
      ctx.strokeStyle = "rgba(255,167,38,.6)";
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(px, py); ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = "#ffa726";
      ctx.beginPath(); ctx.arc(px, py, 6, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#ffd9a1";
      ctx.font = "12px sans-serif";
      ctx.textAlign = px < cx ? "right" : "left";
      ctx.fillText("我", px + (px < cx ? -10 : 10), py - 8);

      ctx.fillStyle = "#9db1c4";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(`直线 ${fmtDist(meters)} · 方位 ${Math.round(deg)}°`, 14, H - 14);
    } else {
      ctx.fillStyle = "#63788c";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText("点击“获取 GPS”后显示相对位置", 14, H - 14);
    }

    ctx.strokeStyle = "rgba(233,241,250,.5)";
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(W - 34, 34); ctx.lineTo(W - 34, 12); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(W - 34, 12); ctx.lineTo(W - 39, 21); ctx.lineTo(W - 29, 21); ctx.closePath();
    ctx.fillStyle = "rgba(233,241,250,.7)"; ctx.fill();
    ctx.fillStyle = "#8fa3b6"; ctx.font = "10px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("N", W - 34, 48);
    ctx.textAlign = "left";
    ctx.fillStyle = "#4f6478"; ctx.font = "10px sans-serif";
    ctx.fillText("方向示意 · 不按比例", 14, 20);
  }

  /* ---------------- GPS ---------------- */
  function setGpsStatus(kind, text) {
    const el = $("#gps-status");
    el.className = "gps-status" + (kind ? " " + kind : "");
    el.textContent = text;
  }

  function getCoords() {
    if (state.coords) return state.coords;
    const lon = Number($("#manual-lon").value);
    const lat = Number($("#manual-lat").value);
    if (Number.isFinite(lon) && Number.isFinite(lat) && lon >= -180 && lon <= 180 && lat >= -90 && lat <= 90 &&
        ($("#manual-lon").value !== "" && $("#manual-lat").value !== "")) {
      return { lon, lat, accuracy: null, manual: true };
    }
    return null;
  }

  function initGps() {
    $("#gps-btn").addEventListener("click", () => {
      if (!navigator.geolocation) {
        setGpsStatus("bad", "此设备/浏览器不支持定位，请手动填写经纬度。");
        return;
      }
      $("#gps-btn").disabled = true;
      setGpsStatus("", "正在定位…请保持页面打开，最多等待 20 秒。");
      navigator.geolocation.getCurrentPosition((pos) => {
        const c = pos.coords;
        state.coords = { lon: c.longitude, lat: c.latitude, accuracy: c.accuracy };
        $("#manual-lon").value = c.longitude.toFixed(6);
        $("#manual-lat").value = c.latitude.toFixed(6);
        const d = distanceToCase();
        const far = d != null && d > 2000;
        setGpsStatus(far ? "warn" : "ok",
          `已定位：${c.longitude.toFixed(6)}, ${c.latitude.toFixed(6)} · 精度 ±${Math.round(c.accuracy || 0)} m` +
          (d != null ? ` · 距案件点 ${fmtDist(d)}` : "") +
          (far ? "。当前位置距案件点较远，请确认是否已到场。" : ""));
        updateDistance();
        drawSketch();
      }, (err) => {
        const map = { 1: "定位权限被拒绝，请在浏览器设置中允许，或手动填写经纬度。", 2: "暂时无法获取位置，请到开阔处重试。", 3: "定位超时，请重试或手动填写。" };
        setGpsStatus("bad", map[err.code] || "定位失败，请手动填写经纬度。");
      }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
      $("#gps-btn").disabled = false;
    });

    $("#gps-clear").addEventListener("click", () => {
      state.coords = null;
      $("#manual-lon").value = "";
      $("#manual-lat").value = "";
      setGpsStatus("", "已清除定位。可重新获取，或手动填写经纬度。");
      updateDistance();
      drawSketch();
    });

    ["#manual-lon", "#manual-lat"].forEach((sel) => {
      $(sel).addEventListener("input", () => {
        state.coords = null;
        const c = getCoords();
        setGpsStatus(c ? "warn" : "", c
          ? `使用手动坐标：${c.lon.toFixed(6)}, ${c.lat.toFixed(6)}（无精度信息）`
          : "尚未定位。室内或林区可能失败，可手动填写经纬度。");
        updateDistance();
        drawSketch();
      });
    });
  }

  function distanceToCase() {
    const c = state.context && state.context.case;
    const coords = getCoords();
    if (!c || c.lon == null || c.lat == null || !coords) return null;
    return haversine(coords.lon, coords.lat, Number(c.lon), Number(c.lat));
  }

  /* ---------------- 照片选择 ---------------- */
  function addFiles(fileList) {
    const incoming = Array.from(fileList || []);
    if (!incoming.length) return;
    for (const file of incoming) {
      if (state.files.length >= MAX_FILES) {
        toast(`最多选择 ${MAX_FILES} 个文件`, true);
        break;
      }
      const total = state.files.reduce((sum, f) => sum + f.file.size, 0);
      if (total + file.size > MAX_TOTAL) {
        toast("所选文件总大小超过 45MB，请分批上传", true);
        break;
      }
      if ([...state.files].some((f) => f.file.name === file.name && f.file.size === file.size && f.file.lastModified === file.lastModified)) {
        continue;
      }
      state.files.push({ file, hash: "" });
    }
    renderFiles();
    state.files.forEach((item, index) => {
      if (item.hash || item.hashing) return;
      item.hashing = true;
      sha256Hex(item.file).then((h) => {
        item.hash = h;
        item.hashing = false;
        renderFiles();
      }).catch(() => { item.hashing = false; });
    });
  }

  function renderFiles() {
    const list = $("#photo-list");
    list.innerHTML = state.files.map((item, index) => `
      <div class="photo">
        <span class="photo-name">${esc(item.file.name)}</span>
        <span class="photo-size">${esc(fmtSize(item.file.size))}</span>
        <span class="photo-hash">${item.hash ? esc(item.hash.slice(0, 12)) : (item.hashing ? "计算中…" : "")}</span>
        <button class="q-del" type="button" data-photo="${index}" aria-label="移除">×</button>
      </div>`).join("");
    const total = state.files.reduce((sum, f) => sum + f.file.size, 0);
    $("#photo-total").textContent = state.files.length ? `共 ${state.files.length} 个文件，${fmtSize(total)}` : "";
    $$("[data-photo]", list).forEach((btn) => {
      btn.addEventListener("click", () => {
        state.files.splice(Number(btn.dataset.photo), 1);
        renderFiles();
      });
    });
  }

  /* ---------------- 上传与队列 ---------------- */
  function buildFields() {
    const c = getCoords();
    const fields = {
      actor_name: $("#actor-name").value.trim(),
      note: $("#checkin-note").value.trim(),
      device: deviceLabel(),
      captured_at: new Date().toISOString(),
    };
    if (c) {
      fields.lon = String(c.lon);
      fields.lat = String(c.lat);
      if (c.accuracy != null) fields.accuracy = String(c.accuracy);
    }
    const task = $("#task-select").value;
    if (task) fields.task_id = task;
    return fields;
  }

  async function sendCheckin(fields, files = []) {
    const fd = new FormData();
    ["actor_name", "note", "device", "captured_at", "lon", "lat", "accuracy", "task_id"].forEach((k) => {
      if (fields[k] !== undefined && fields[k] !== null && fields[k] !== "") fd.append(k, fields[k]);
    });
    files.forEach((f) => fd.append("files", f, f.name));
    return api("/api/mobile/checkin", { method: "POST", body: fd });
  }

  async function sendEvidence(checkinId, file, fields) {
    const fd = new FormData();
    fd.append("checkin_id", checkinId);
    fd.append("file", file, file.name);
    ["task_id", "captured_at", "lon", "lat", "note"].forEach((k) => {
      if (fields[k] !== undefined && fields[k] !== null && fields[k] !== "") fd.append(k, fields[k]);
    });
    return api("/api/mobile/evidence", { method: "POST", body: fd });
  }

  async function submitCheckin(ev) {
    ev.preventDefault();
    if (state.context && state.context.can_checkin === false) {
      return toast("案件已复核或归档，不能继续签到", true);
    }
    const name = $("#actor-name").value.trim();
    if (!name) {
      $("#actor-name").focus();
      return toast("请填写核查人姓名", true);
    }
    const fields = buildFields();
    const files = state.files.map((f) => f.file);
    const btn = $("#checkin-btn");
    btn.disabled = true;
    btn.textContent = files.length ? "正在签到并上传…" : "正在签到…";

    if (!navigator.onLine) {
      await enqueueCheckin(fields, files);
      toast("当前离线：已保存到本机，联网后同步");
      resetForm();
      btn.disabled = false;
      btn.textContent = "签到并保存证据";
      return;
    }

    let checkinId = null;
    let queued = 0;
    let failed = 0;
    try {
      const result = await sendCheckin(fields);
      checkinId = result.checkin_id;
      rememberCheckin(checkinId);
      for (let i = 0; i < files.length; i += 1) {
        btn.textContent = `正在上传证据 ${i + 1}/${files.length}…`;
        try {
          await sendEvidence(checkinId, files[i], fields);
        } catch (err) {
          if (!err.status || err.status >= 500) {
            await enqueueEvidence(checkinId, files[i], fields);
            queued += 1;
          } else {
            failed += 1;
            toast(`${files[i].name} 上传失败：${err.message}`, true);
          }
        }
      }
      toast(`签到成功${queued ? `，${queued} 个文件已转存离线队列` : ""}${failed ? `，${failed} 个文件失败` : ""}`, failed > 0);
      resetForm();
      await loadContext();
      renderContext();
      refreshQueue();
    } catch (err) {
      if (err.status && err.status < 500) {
        toast(err.message, true);
      } else {
        await enqueueCheckin(fields, files);
        toast("网络中断：签到与文件已保存到本机，联网后同步");
        resetForm();
      }
    } finally {
      btn.disabled = false;
      btn.textContent = "签到并保存证据";
    }
  }

  function resetForm() {
    state.files = [];
    state.coords = null;
    $("#checkin-note").value = "";
    $("#photo-input").value = "";
    $("#manual-lon").value = "";
    $("#manual-lat").value = "";
    setGpsStatus("", "已提交。下一次签到请重新定位。");
    renderFiles();
    updateDistance();
    drawSketch();
  }

  /* ---------------- IndexedDB 队列 ---------------- */
  function openDb() {
    if (state.db) return Promise.resolve(state.db);
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("queue")) db.createObjectStore("queue", { keyPath: "id" });
      };
      req.onsuccess = () => { state.db = req.result; resolve(state.db); };
      req.onerror = () => reject(req.error || new Error("无法打开本机数据库"));
    });
  }

  function dbTx(mode, fn) {
    return openDb().then((db) => new Promise((resolve, reject) => {
      const tx = db.transaction("queue", mode);
      const store = tx.objectStore("queue");
      let result;
      try { result = fn(store); } catch (err) { reject(err); return; }
      tx.oncomplete = () => resolve(result && result.result);
      tx.onerror = () => reject(tx.error || new Error("本机数据库写入失败"));
      tx.onabort = () => reject(tx.error || new Error("本机数据库事务中止"));
    }));
  }

  const dbPut = (item) => dbTx("readwrite", (s) => s.put(item));
  const dbDelete = (id) => dbTx("readwrite", (s) => s.delete(id));
  const dbAll = () => dbTx("readonly", (s) => s.getAll());
  const dbClear = () => dbTx("readwrite", (s) => s.clear());

  async function enqueueCheckin(fields, files) {
    const base = Date.now();
    const caseId = state.context && state.context.case ? state.context.case.id : "";
    await dbPut({ id: `q-${base}-0`, kind: "checkin", order: base, case_id: caseId, created_at: new Date().toISOString(), fields, status: "pending" });
    for (let i = 0; i < files.length; i += 1) {
      await dbPut({
        id: `q-${base}-${i + 1}`, kind: "evidence", order: base + i + 1, case_id: caseId,
        created_at: new Date().toISOString(), fields, status: "pending",
        blob: files[i], filename: files[i].name, mime: files[i].type,
      });
    }
    await refreshQueue();
  }

  async function enqueueEvidence(checkinId, file, fields) {
    const base = Date.now();
    const caseId = state.context && state.context.case ? state.context.case.id : "";
    await dbPut({
      id: `q-${base}-e`, kind: "evidence", order: base, case_id: caseId, created_at: new Date().toISOString(),
      fields, status: "pending", checkin_id: checkinId, blob: file, filename: file.name, mime: file.type,
    });
    await refreshQueue();
  }

  async function refreshQueue() {
    try {
      const items = await dbAll();
      const caseId = state.context && state.context.case ? state.context.case.id : "";
      state.queue = (items || [])
        .filter((item) => !item.case_id || item.case_id === caseId)
        .sort((a, b) => a.order - b.order);
      renderQueue();
    } catch (err) {
      $("#queue-list").innerHTML = `<p class="empty-small">读取本机队列失败：${esc(err.message)}</p>`;
    }
  }

  function renderQueue() {
    const list = $("#queue-list");
    $("#queue-count").textContent = String(state.queue.length);
    if (!state.queue.length) {
      list.innerHTML = `<p class="empty-small">队列为空。</p>`;
      return;
    }
    list.innerHTML = state.queue.map((item) => {
      const isCheckin = item.kind === "checkin";
      const title = isCheckin ? `到场签到 · ${item.fields.actor_name || ""}` : `证据 · ${item.filename || "文件"}`;
      const meta = isCheckin
        ? `${fmtTime(item.created_at)} · 等待同步`
        : `${fmtTime(item.created_at)} · ${fmtSize(item.blob ? item.blob.size : 0)}${item.status === "error" ? ` · ${item.error || "失败"}` : ""}`;
      return `<div class="queue-item ${item.status === "error" ? "error" : ""}">
        <div class="q-main"><b>${esc(title)}</b><small>${esc(meta)}</small></div>
        <button class="q-del" type="button" data-queue="${esc(item.id)}" aria-label="移除">×</button>
      </div>`;
    }).join("");
    $$("[data-queue]", list).forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!window.confirm("从本机队列移除这一项？未上传的内容将丢失。")) return;
        await dbDelete(btn.dataset.queue);
        await refreshQueue();
      });
    });
  }

  async function syncQueue() {
    if (state.syncing) return;
    if (!navigator.onLine) return toast("当前没有网络，稍后再试", true);
    await refreshQueue();
    if (!state.queue.length) return toast("队列为空");
    state.syncing = true;
    $("#sync-btn").disabled = true;
    $("#sync-btn").textContent = "同步中…";
    let done = 0;
    let failed = 0;
    try {
      for (const item of state.queue) {
        if (item.kind === "checkin") {
          try {
            const r = await sendCheckin(item.fields);
            rememberCheckin(r.checkin_id);
            await dbDelete(item.id);
            done += 1;
          } catch (err) {
            if (err.status === 401 || err.status === 403 || !err.status || err.status >= 500) {
              toast(`同步暂停：${err.message}`, true);
              break;
            }
            item.status = "error";
            item.error = err.message;
            await dbPut(item);
            failed += 1;
          }
        } else {
          const cid = item.checkin_id || recallCheckin();
          if (!cid) {
            toast("同步暂停：缺少签到记录，请先完成签到", true);
            break;
          }
          try {
            await sendEvidence(cid, item.blob, item.fields);
            await dbDelete(item.id);
            done += 1;
          } catch (err) {
            if (err.status === 401 || err.status === 403 || !err.status || err.status >= 500) {
              toast(`同步暂停：${err.message}`, true);
              break;
            }
            item.status = "error";
            item.error = err.message;
            await dbPut(item);
            failed += 1;
          }
        }
        await refreshQueue();
      }
      toast(`同步完成：成功 ${done} 项${failed ? `，失败 ${failed} 项（请查看队列）` : ""}`, failed > 0);
    } finally {
      state.syncing = false;
      $("#sync-btn").disabled = false;
      $("#sync-btn").textContent = "立即同步";
      await refreshQueue();
    }
  }

  /* ---------------- 追加证据 ---------------- */
  async function addMoreEvidence() {
    const input = $("#more-photo");
    const files = Array.from(input.files || []);
    if (!files.length) return toast("请先选择文件", true);
    const cid = recallCheckin();
    if (!cid) return toast("没有可用的签到记录，请先签到", true);
    const fields = buildFields();
    const btn = $("#more-btn");
    btn.disabled = true;
    let ok = 0;
    let queued = 0;
    for (const file of files) {
      try {
        if (!navigator.onLine) {
          await enqueueEvidence(cid, file, fields);
          queued += 1;
          continue;
        }
        await sendEvidence(cid, file, fields);
        ok += 1;
      } catch (err) {
        if (!err.status || err.status >= 500) {
          await enqueueEvidence(cid, file, fields);
          queued += 1;
        } else {
          toast(`${file.name}：${err.message}`, true);
        }
      }
    }
    input.value = "";
    btn.disabled = false;
    toast(`追加完成：上传 ${ok} 个${queued ? `，离线暂存 ${queued} 个` : ""}`);
    await loadContext();
    renderContext();
    refreshQueue();
  }

  /* ---------------- 网络与服务工作者 ---------------- */
  function updateNet() {
    const online = navigator.onLine;
    $("#net-pill").classList.toggle("offline", !online);
    $("#net-text").textContent = online ? "在线" : "离线";
  }

  function registerSw() {
    if (!("serviceWorker" in navigator)) return;
    if (location.protocol !== "https:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* 离线缓存不可用不影响在线核查 */
    });
  }

  /* ---------------- 事件绑定 ---------------- */
  function bind() {
    $("#token-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const token = $("#token-input").value.trim();
      if (!token) return;
      state.token = token;
      localStorage.setItem(TOKEN_KEY, token);
      $("#gate-error").textContent = "";
      try {
        await loadContext();
        history.replaceState(null, "", location.pathname + location.search);
        showCase();
      } catch (err) {
        showGate(err.message);
      }
    });

    $("#refresh-btn").addEventListener("click", async () => {
      if (!state.token) return;
      try {
        await loadContext();
        renderContext();
        toast("案件信息已刷新");
      } catch (err) {
        toast(err.message, true);
      }
    });

    $("#checkin-form").addEventListener("submit", submitCheckin);
    $("#gps-btn") && initGps();
    $("#photo-input").addEventListener("change", (ev) => {
      addFiles(ev.target.files);
      ev.target.value = "";
    });
    $("#more-btn").addEventListener("click", addMoreEvidence);
    $("#sync-btn").addEventListener("click", syncQueue);
    $("#clear-queue").addEventListener("click", async () => {
      if (!state.queue.length) return toast("队列为空");
      if (!window.confirm(`确认清空 ${state.queue.length} 项未同步内容？此操作不可恢复。`)) return;
      await dbClear();
      await refreshQueue();
      toast("本机队列已清空");
    });

    $("#copy-coords").addEventListener("click", async () => {
      const c = state.context && state.context.case;
      if (!c || c.lon == null) return;
      const text = `${Number(c.lon).toFixed(6)}, ${Number(c.lat).toFixed(6)}`;
      try {
        await navigator.clipboard.writeText(text);
        toast("坐标已复制");
      } catch (_) {
        toast(`坐标：${text}`, false);
      }
    });

    window.addEventListener("online", () => {
      updateNet();
      if (state.queue.length) {
        toast("网络已恢复，正在同步离线队列…");
        syncQueue();
      }
    });
    window.addEventListener("offline", updateNet);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.token && navigator.onLine) {
        loadContext().then(renderContext).catch(() => {});
      }
    });
  }

  /* ---------------- 启动 ---------------- */
  async function boot() {
    bind();
    updateNet();
    registerSw();
    finalize();
  }

  async function finalize() {
    const urlToken = tokenFromHash();
    const saved = localStorage.getItem(TOKEN_KEY) || "";
    state.token = urlToken || saved;
    if (urlToken) {
      localStorage.setItem(TOKEN_KEY, urlToken);
      history.replaceState(null, "", location.pathname + location.search);
    }
    if (!state.token) {
      showGate("");
      return;
    }
    try {
      await loadContext();
      if (!state.context) return showGate("");
      showCase();
    } catch (err) {
      showGate(err.message);
    }
  }

  boot();
})();

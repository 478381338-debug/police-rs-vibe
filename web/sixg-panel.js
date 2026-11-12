/* 6G 空天地一体 · 演示线索转正式案件。独立于空中办案主脚本，复用同一页面。 */
(function () {
  "use strict";

  var API = "/api/sixg";
  var state = {
    overview: null,
    scenario: "outage",
    data: null,
    converted: null,
    loading: false,
    converting: false
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function num(v, d) {
    return v === null || v === undefined || isNaN(Number(v)) ? "--" : Number(v).toFixed(d === undefined ? 1 : d);
  }

  var toastTimer = null;
  function notify(message, bad) {
    var el = $("toast");
    if (!el) return;
    el.textContent = message;
    el.className = "toast on" + (bad ? " bad" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.className = "toast"; }, 3600);
  }

  function openModal() {
    var modal = $("sixg-modal");
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    if (!state.overview) loadOverview();
    if (!state.data && !state.loading) runDemo(state.scenario);
  }

  function closeModal() {
    var modal = $("sixg-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  async function loadOverview() {
    try {
      var res = await fetch(API + "/overview", { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      state.overview = await res.json();
      renderScenarios();
    } catch (err) {
      $("sixg-scenarios").innerHTML = '<div class="empty">场景加载失败：' + esc(err.message) + "</div>";
    }
  }

  function renderScenarios() {
    var box = $("sixg-scenarios");
    var list = (state.overview && state.overview.scenarios) || [];
    if (!list.length) {
      box.innerHTML = '<div class="empty">暂无场景</div>';
      return;
    }
    box.innerHTML = list.map(function (s) {
      return '<button class="sixg-scenario' + (s.id === state.scenario ? " on" : "") +
        '" data-scenario="' + esc(s.id) + '" type="button"><b>' + esc(s.label) +
        "</b>" + esc(s.desc || "") + "</button>";
    }).join("");
    Array.prototype.forEach.call(box.querySelectorAll("[data-scenario]"), function (btn) {
      btn.addEventListener("click", function () {
        if (state.loading) return;
        state.scenario = btn.getAttribute("data-scenario");
        Array.prototype.forEach.call(box.querySelectorAll(".sixg-scenario"), function (b) {
          b.classList.toggle("on", b === btn);
        });
        runDemo(state.scenario);
      });
    });
  }

  function renderNetwork(data) {
    var plan = data.plan || {};
    var caseData = data.case || {};
    var kpis = [
      ["端到端", num(plan.end_to_end_ms, 1), "ms"],
      ["影像回传", num(plan.uplink_ms, 1), "ms"],
      ["边缘推理", num(plan.compute_ms, 1), "ms"],
      ["节省带宽", num(plan.bandwidth_saved_pct, 0), "%"],
      ["高清瓦片", (plan.tiles_sent == null ? "--" : plan.tiles_sent) + "/" + (plan.tiles_total == null ? "--" : plan.tiles_total), ""],
      ["置信线索", num(caseData.confidence, 2), ""]
    ];
    $("sixg-network").innerHTML = kpis.map(function (k) {
      return '<div class="sixg-kpi"><small>' + esc(k[0]) + "</small><b>" + esc(k[1]) +
        (k[2] ? "<em>" + esc(k[2]) + "</em>" : "") + "</b></div>";
    }).join("");
  }

  function renderLead(data) {
    var c = data.case || {};
    var plan = data.plan || {};
    var lead = data.lead || {};
    var timeline = (data.timeline || []).filter(function (x) {
      return ["感知", "AI", "案件"].indexOf(x.phase) >= 0;
    }).slice(-3);
    var legal = (c.legal || [])[0];
    var converted = state.converted;
    var actions = "";
    if (converted && converted.workspace_url) {
      actions = '<a class="sixg-link" href="' + esc(converted.workspace_url) + '">打开案件工作台 →</a>';
    }
    $("sixg-lead").innerHTML =
      '<div class="lead-no">' + esc(c.case_no || "SIXG-…") + "</div>" +
      "<h3>" + esc(c.title || "6G 演示线索") + "</h3>" +
      '<div class="lead-row">' +
        '<span>类型 <b class="am">' + esc(c.hazard || lead.class || "待核查") + "</b></span>" +
        "<span>面积 <b>" + num(c.area_ha) + " ha</b></span>" +
        "<span>置信 <b>" + num(c.confidence, 2) + "</b></span>" +
        "<span>状态 <b>" + esc(c.status || "待核查") + "</b></span>" +
      "</div>" +
      '<div class="lead-row" style="margin-top:5px">' +
        "<span>影像 <b>" + esc((data.detection && data.detection.before && data.detection.before.date) || "2023-10-08") +
        " / " + esc((data.detection && data.detection.after && data.detection.after.date) || "2025-09-27") + "</b></span>" +
        "<span>6G 切片 <b>" + esc(plan.slice || "eMBB") + "</b></span>" +
      "</div>" +
      '<div class="sixg-result">' +
        timeline.map(function (x) {
          return "<div><b>" + esc(x.phase) + "</b> · " + esc(x.title) + "</div>";
        }).join("") +
        (legal ? '<div style="margin-top:4px">法条参考：<b>' + esc(legal.law) + " " + esc(legal.article) + "</b></div>" : "") +
      "</div>" +
      actions;
  }

  async function runDemo(scenario) {
    if (state.loading) return;
    state.loading = true;
    $("sixg-refresh").disabled = true;
    $("sixg-convert").disabled = true;
    $("sixg-result").innerHTML = "推演中：6G 切片调度 → 影像回传 → 边缘 AI → 线索生成…";
    try {
      var res = await fetch(API + "/demo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ scenario: scenario })
      });
      var data = await res.json();
      if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
      state.data = data;
      state.converted = null;
      renderNetwork(data);
      renderLead(data);
      $("sixg-convert").disabled = false;
      $("sixg-convert").innerHTML = "一键转正式线索 <span>→</span>";
      $("sixg-result").innerHTML = "演示线索已生成；转办后写入真实案件表，并生成期限、任务与证据链。";
      checkConverted(data.case.case_no);
    } catch (err) {
      state.data = null;
      $("sixg-network").innerHTML = '<div class="empty">推演失败</div>';
      $("sixg-lead").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      $("sixg-result").innerHTML = '<span class="bad">' + esc(err.message) + "</span>";
      notify("6G 推演失败：" + err.message, true);
    } finally {
      state.loading = false;
      $("sixg-refresh").disabled = false;
    }
  }

  async function checkConverted(caseNo) {
    if (!caseNo) return;
    try {
      var res = await fetch(API + "/leads/" + encodeURIComponent(caseNo), { cache: "no-store" });
      if (!res.ok) return;
      var data = await res.json();
      if (data.converted && data.case) {
        state.converted = { workspace_url: "/workspace?case=" + encodeURIComponent(data.case.id) };
        renderLead(state.data);
        $("sixg-convert").disabled = true;
        $("sixg-convert").textContent = "已转正式案件";
        $("sixg-result").innerHTML = "该线索已在案件库中：<b>" + esc(data.case.case_no) + "</b> · " + esc(data.case.status || "") + "。";
      }
    } catch (_) { /* endpoint may be unavailable before backend reload */ }
  }

  async function convertLead() {
    if (!state.data || state.converting) return;
    var caseNo = state.data.case && state.data.case.case_no;
    if (!caseNo) return;
    state.converting = true;
    var btn = $("sixg-convert");
    btn.disabled = true;
    btn.textContent = "转办中…";
    try {
      var res = await fetch(API + "/leads/" + encodeURIComponent(caseNo) + "/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ due_hours: 72 })
      });
      var data = await res.json();
      if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
      state.converted = data;
      var c = data.case || {};
      $("sixg-result").innerHTML =
        '<span class="ok">' + esc(data.notice || "已转正式案件") + "</span>" +
        "<div>案件 <b>" + esc(c.case_no || "--") + "</b> · 状态 <b>" + esc(c.status || "--") + "</b></div>" +
        "<div>期限 <b>" + esc((c.due_at || "").slice(0, 10) || "--") + "</b> · 证据 <b>" +
        esc(c.evidence_count == null ? data.evidence_added : c.evidence_count) + "</b> 份</div>" +
        (data.task ? "<div>任务 <b>" + esc(data.task.title || "--") + "</b></div>" : "") +
        (data.workspace_url ? '<div style="margin-top:6px"><a class="sixg-link" href="' +
          esc(data.workspace_url) + '">打开案件工作台 →</a></div>' : "");
      btn.textContent = "已转正式案件";
      btn.disabled = true;
      notify("已转正式案件：" + (c.case_no || caseNo));
    } catch (err) {
      btn.textContent = "一键转正式线索 →";
      btn.disabled = false;
      $("sixg-result").innerHTML = '<span class="bad">转办失败：' + esc(err.message) + "</span>";
      notify("转办失败：" + err.message, true);
    } finally {
      state.converting = false;
    }
  }

  function bind() {
    // 顶部“6G 链路”是直链 /sixg；弹窗仅由 ?panel=sixg 或旧页面“线索转办”打开。
    Array.prototype.forEach.call(document.querySelectorAll("[data-sixg-close]"), function (el) {
      el.addEventListener("click", closeModal);
    });
    $("sixg-refresh").addEventListener("click", function () { runDemo(state.scenario); });
    $("sixg-convert").addEventListener("click", convertLead);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !$("sixg-modal").hidden) closeModal();
    });
    if (new URLSearchParams(location.search).get("panel") === "sixg") {
      setTimeout(openModal, 120);
    }
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
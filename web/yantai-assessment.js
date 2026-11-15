/* Isolated 2025 assessment overlay. It never mutates the existing workspace map. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let data = null;
  let map = null;
  let candidateLayer = null;
  let aoiLayer = null;

  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function n(v, digits = 2) {
    return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(digits);
  }
  function pct(v) {
    return v == null ? "—" : (Number(v) * 100).toFixed(1) + "%";
  }

  function initMap() {
    if (map) return;
    map = L.map("ywa-map", { zoomControl: true, attributionControl: true }).setView([37.25, 120.22], 10);
    L.tileLayer("/api/tile/osm/{z}/{x}/{y}", {
      maxZoom: 18, maxNativeZoom: 17,
      errorTileUrl: "/static/assets/tile-fallback.png",
      attribution: "本地代理瓦片 · 非正式边界",
    }).addTo(map);
    candidateLayer = L.layerGroup().addTo(map);
    aoiLayer = L.layerGroup().addTo(map);
  }

  function renderMap() {
    initMap();
    candidateLayer.clearLayers();
    aoiLayer.clearLayers();
    const feats = (data.candidates && data.candidates.features) || [];
    const allBounds = [];
    (data.aois || []).forEach((a) => {
      if (!a.bbox || a.bbox.length !== 4) return;
      const [w, s, e, nn] = a.bbox;
      const rect = L.rectangle([[s, w], [nn, e]], {
        color: "#157f74", weight: 1, dashArray: "5 4", fill: false,
      }).bindPopup(`<b>${esc(a.name || a.label)}</b><br><span style="color:#667085">数据内标签：${esc(a.label || "")}（未做行政区划核实）</span><br>` +
        `范围 ${a.bbox.map((v) => n(v, 3)).join(", ")}<br>` +
        `候选 ${a.candidates || 0} 个 / ${n(a.candidate_area_ha)} ha<br>h=${n(a.h, 3)} · 证据 C${(a.evidence_counts || {}).C || 0}/D${(a.evidence_counts || {}).D || 0} · 可派单 ${a.dispatchable || 0} 个（旧口径高优先级 ${a.high_priority || 0} 个，仅对照）`);
      rect.addTo(aoiLayer);
      allBounds.push(rect.getBounds());
    });
    feats.forEach((f) => {
      if (!f.geometry) return;
      const p = f.properties || {};
      const pri = p.evidence_priority || "";
      const high = pri === "P1" || pri === "P2";
      const mid = !high && (pri === "P3" || p.evidence_tier === "C");
      const layer = L.geoJSON(f, {
        style: {
          color: high ? "#b54708" : (mid ? "#b54708" : "#157f74"),
          weight: high ? 2 : 1,
          fillColor: high ? "#f04438" : (mid ? "#f79009" : "#2e90fa"),
          fillOpacity: high ? 0.42 : (mid ? 0.3 : 0.14),
        },
      });
      layer.bindPopup(
        `<b>${esc(p.aoi || "")} · ${n(p.area_ha)} ha</b><br>` +
        `首次报警：${esc(p.first_alarm || "—")}<br>` +
        `末次报警：${esc(p.last_alarm || "—")}<br>` +
        `证据：${esc(p.evidence_tier || "—")}/${esc(p.evidence_priority || "—")} · 分 ${n(p.evidence_score, 1)}（不是概率）<br>` +
        `ΔBSI：${n(p.delta_bsi, 3)} · max CUSUM：${n(p.max_cusum, 2)}<br>` +
        `对象有效比例：${pct(p.valid_frac)} · q≥0.8 日期：${p.n_clear_dates == null ? "—" : p.n_clear_dates}<br>` +
        `最近已知点：${esc(p.nearest_known || "—")}${p.distance_known_m == null ? "" : " / " + n(p.distance_known_m, 0) + " m"}<br>` +
        `<span style="color:#667085">线索，不是违法认定</span>`
      );
      layer.addTo(candidateLayer);
      allBounds.push(layer.getBounds());
    });
    if (allBounds.length) {
      const b = allBounds.reduce((acc, x) => acc.extend(x), allBounds[0]);
      map.fitBounds(b.pad(0.06), { maxZoom: 13 });
    }
  }

  function renderSummary() {
    const lv = data.level || "F1";
    const role = data.catalog_by_role || {};
    $("ywa-summary").innerHTML = `
      <div class="ywa-cards">
        <div class="ywa-card"><b>${data.candidate_count == null ? "—" : data.candidate_count}</b><span>候选图斑</span></div>
        <div class="ywa-card warn"><b>${data.dispatchable_count == null ? "—" : data.dispatchable_count}</b><span>可派单 P1+P2（证据口径）</span></div>
        <div class="ywa-card"><b>A${(data.evidence_counts || {}).A || 0}·B${(data.evidence_counts || {}).B || 0}·C${(data.evidence_counts || {}).C || 0}·D${(data.evidence_counts || {}).D || 0}</b><span>证据等级分布</span></div>
        <div class="ywa-card"><b>${n(data.candidate_area_ha)}</b><span>候选面积 ha（非违法面积）</span></div>
        <div class="ywa-card"><b>${esc(lv)}</b><span>项目阶段层级 · 不是分数</span></div>
      </div>
      <div class="ywa-banner"><strong>${esc(data.title || "2025 试点")}</strong><br>
        ${esc(data.scope_label || data.scope || "")}<br>
        ${esc(data.disclaimer || "")}<br>
        <span style="color:#667085">目录 ${data.catalog_items || "—"} 景（基线 ${role.baseline || "—"} / 标定 ${role.calibration || "—"} / 目标 ${role.target || "—"}）；两个 AOI 共用同一组目标影像，不是独立 90 景。</span>
        ${data.evidence_note ? '<div style="color:#157f74;margin-top:4px">证据口径：' + esc(data.evidence_note) + (data.evidence_as_of ? "（基准日 " + esc(data.evidence_as_of) + "）" : "") + "</div>" : ""}
        ${data.high_priority_note ? '<div style="color:#667085;margin-top:2px">' + esc(data.high_priority_note) + "</div>" : ""}
        ${data.level_note ? '<div style="color:#b54708;margin-top:4px">' + esc(data.level_note) + "</div>" : ""}
        ${data.data_status ? '<div style="color:#667085;margin-top:2px">数据状态：' + esc(data.data_status) + "</div>" : ""}
      </div>`;
  }

  function renderRuns() {
    const runs = data.runs || [];
    const statusClass = { ok: "ok", complete: "ok", missing: "bad", partial: "warn" };
    const rows = runs.map((r) => `<tr>
      <td><b>${esc(r.label)}</b></td>
      <td class="${statusClass[r.status] || "warn"}">${esc(r.status_label || r.status || "—")}</td>
      <td>${esc(r.period || "—")}</td>
      <td>${r.scenes == null ? "—" : r.scenes}${r.key === "original" ? ' <span class="ywa-muted">（两样区各 45 景，共用）</span>' : ""}</td>
      <td>${r.candidates == null ? "—" : r.candidates} / ${n(r.area_ha)} ha</td>
      <td>${r.evidence_counts ? "A" + (r.evidence_counts.A || 0) + "·B" + (r.evidence_counts.B || 0) + "·C" + (r.evidence_counts.C || 0) + "·D" + (r.evidence_counts.D || 0) : "—"} / 可派单 ${r.dispatchable == null ? "—" : r.dispatchable}</td>
      <td>${r.high_priority == null ? "—" : r.high_priority} / ${n(r.high_priority_area_ha)} ha</td>
      <td class="ywa-note-cell">${esc(r.note || "")}</td>
    </tr>`).join("");
    const full = runs.find((r) => r.key === "v2_full");
    const fullNote = full && full.status === "complete"
      ? `v2 全年已完成：${full.candidates == null ? "—" : full.candidates} 个候选 / ${n(full.area_ha)} ha。`
      : "v2 全年未完成。";
    $("ywa-runs").innerHTML = `<div class="ywa-table-wrap"><table class="ywa-table ywa-runs-table">
      <thead><tr><th>运行</th><th>状态</th><th>时段</th><th>景</th><th>候选 / 面积</th><th>证据等级 / 可派单</th><th>旧高优先级（对照）</th><th>说明</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="8">未找到运行记录</td></tr>'}</tbody></table></div>
      <p class="ywa-note">三次运行不能合并统计：原版方法有缺陷、v2 小试只到 2 月、v2 全年是本次主结果。${fullNote} 原版 68、小试 3、全年 ${full && full.candidates != null ? full.candidates : "—"} 是三个不同口径。</p>`;
  }

  function renderMetrics() {
    const m = data.metrics || {};
    const req = (m.required || []).map((x) => "<li>" + esc(x) + "</li>").join("");
    $("ywa-metrics").innerHTML = `
      <div class="ywa-banner"><strong>${esc(m.status_label || "事件级指标未评定")}</strong>
        <div style="margin-top:4px">${esc(m.reason || "")}</div>
      </div>
      <ul class="ywa-list">${req || "<li>缺少独立标签与评价口径</li>"}</ul>
      ${m.sample_size_example ? '<p class="ywa-note">' + esc(m.sample_size_example) + "</p>" : ""}`;
  }

  function renderBoundaries() {
    const items = (data.boundaries || []).map((x) => "<li>" + esc(x) + "</li>").join("");
    $("ywa-boundaries").innerHTML = items ? '<ul class="ywa-list">' + items + "</ul>" : '<p class="ywa-note">未配置边界说明。</p>';
  }

  function renderAois() {
    const rows = (data.aois || []).map((a) => {
      const c = a.coverage || {};
      const hs = (a.h_sensitivity || []).map((x) => `h=${n(x.h, 0)}:${x.pixel_count}`).join(" / ");
      return `<tr>
        <td><b>${esc(a.name || "")}</b><br><span class="ywa-muted">${esc(a.label || "")}</span><br><span class="ywa-muted">${(a.bbox || []).map((v) => n(v, 2)).join(", ")}</span></td>
        <td>${a.target_scenes == null ? "—" : a.target_scenes}</td>
        <td>${a.candidates == null ? "—" : a.candidates}</td>
        <td>${n(a.candidate_area_ha)}</td>
        <td>${a.evidence_counts ? "A" + (a.evidence_counts.A || 0) + "·B" + (a.evidence_counts.B || 0) + "·C" + (a.evidence_counts.C || 0) + "·D" + (a.evidence_counts.D || 0) : "—"}</td>
        <td>${a.dispatchable == null ? "—" : a.dispatchable}</td>
        <td>${a.high_priority == null ? "—" : a.high_priority}</td>
        <td>${pct(c.mean_valid_fraction)} / ${pct(c.q80_fraction)}</td>
        <td>${n(a.h, 3)}${a.h_at_upper_bound ? " (触上限)" : ""}</td>
        <td>${esc(hs || "—")}</td>
      </tr>`;
    }).join("");
    $("ywa-aois").innerHTML = `<div class="ywa-table-wrap"><table class="ywa-table">
      <thead><tr><th>试点</th><th>景</th><th>候选</th><th>面积 ha</th><th>证据等级</th><th>可派单</th><th>旧高优先级（对照）</th><th>有效比例 / q≥0.8</th><th>h</th><th>h 敏感性（像元数）</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="10">未找到结果</td></tr>'}</tbody></table></div>`;
  }

  function renderFixes() {
    const items = (data.fixes || []).map((f) => `
      <div class="ywa-fix"><b>${f.id}. ${esc(f.title)}</b>
        <div class="before">改前：${esc(f.before)}</div>
        <div class="after">改后：${esc(f.after)}</div>
        <div style="color:#667085">影响：${esc(f.effect)}</div>
      </div>`).join("");
    $("ywa-fixes").innerHTML = items || "<p class='ywa-note'>未找到修订清单。</p>";
  }

  function renderTable() {
    const feats = ((data.candidates && data.candidates.features) || []).slice();
    feats.sort((a, b) => {
      const pa = a.properties || {}, pb = b.properties || {};
      return (Number(!!pb.priority) - Number(!!pa.priority)) ||
        (Number(pb.area_ha || 0) - Number(pa.area_ha || 0));
    });
    const rows = feats.map((f) => {
      const p = f.properties || {};
      return `<tr class="${p.priority ? "high" : ""}">
        <td><span class="ywa-pill ${p.priority ? "high" : "normal"}">${p.priority ? "高" : "一般"}</span></td>
        <td>${esc(p.aoi || "")}</td>
        <td>${n(p.area_ha)}</td>
        <td>${esc(p.first_alarm || "—")}</td>
        <td>${esc(p.last_alarm || "—")}</td>
        <td>${n(p.delta_bsi, 3)}</td>
        <td>${n(p.max_cusum, 2)}</td>
        <td>${pct(p.valid_frac)}</td>
        <td>${esc(p.nearest_known || "—")}${p.distance_known_m == null ? "" : " / " + n(p.distance_known_m, 0) + " m"}</td>
      </tr>`;
    }).join("");
    $("ywa-table").innerHTML = `<div class="ywa-table-wrap"><table class="ywa-table">
      <thead><tr><th>级别</th><th>试点</th><th>面积 ha</th><th>首次报警</th><th>末次报警</th><th>ΔBSI</th><th>max CUSUM</th><th>对象有效比例</th><th>最近已知点</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="9">未找到候选</td></tr>'}</tbody></table></div>`;
  }

  function renderE0() {
    const rows = (data.e0 || []).map((e) => `<tr>
      <td>${esc(e.aoi || "")}</td><td>${esc(e.date_2024 || "—")}</td><td>${esc(e.date_2025 || "—")}</td>
      <td>${e.patches == null ? "—" : e.patches}</td><td>${n(e.area_ha)}</td><td>${e.overlap_with_e1_pixels == null ? "—" : e.overlap_with_e1_pixels}</td>
    </tr>`).join("");
    $("ywa-e0").innerHTML = `<div class="ywa-table-wrap"><table class="ywa-table">
      <thead><tr><th>试点</th><th>2024</th><th>2025</th><th>E0 图斑</th><th>面积 ha</th><th>与 E1 像元重叠</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6">未找到 E0</td></tr>'}</tbody></table></div>` +
      (data.e0_note ? '<p class="ywa-note" style="color:#b54708">' + esc(data.e0_note) + "</p>" : "");
  }

  function render() {
    renderSummary();
    renderRuns();
    renderMetrics();
    renderBoundaries();
    renderAois();
    renderFixes();
    renderTable();
    renderE0();
    renderMap();
    const reportLink = data.report_url
      ? `<a href="${esc(data.report_url)}" target="_blank" rel="noopener">${esc(data.report_label || "打开 v2 小试报告")}</a>`
      : "<span>报告未生成</span>";
    const cardLink = data.model_card_url
      ? `<a href="${esc(data.model_card_url)}" target="_blank" rel="noopener">打开 v2 模型卡</a>`
      : "<span>" + esc(data.model_card_note || "v2 模型卡未生成") + "</span>";
    $("ywa-foot").innerHTML = `
      ${reportLink}
      ${cardLink}
      <span>生成：${esc(data.generated_at || "—")}</span>
      <span>${esc(data.report_note || "")}</span>
      <span>边界：不把候选称“非法”；无 2025 独立高分，F2 未达成，不计算 precision/recall。</span>`;
  }

  async function open() {
    $("ywa-overlay").hidden = false;
    document.documentElement.style.overflow = "hidden";
    if (!data) {
      $("ywa-summary").innerHTML = "<p class='ywa-note'>正在读取 v2 评估结果…</p>";
      try {
        const r = await fetch("/api/yantai/assessment", { credentials: "same-origin" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        data = await r.json();
        if (data.status !== "ok") throw new Error(data.note || data.status || "结果未生成");
        render();
      } catch (err) {
        $("ywa-summary").innerHTML = `<div class="ywa-error">读取失败：${esc(err.message)}。v2 结果生成前此面板不显示零值。</div>`;
        $("ywa-fixes").innerHTML = "";
        $("ywa-table").innerHTML = "";
        $("ywa-e0").innerHTML = "";
        return;
      }
    }
    setTimeout(() => { if (map) map.invalidateSize(); }, 80);
  }

  function close() {
    $("ywa-overlay").hidden = true;
    document.documentElement.style.overflow = "";
  }

  function bind() {
    $("yw-assessment-open").addEventListener("click", open);
    $("ywa-close").addEventListener("click", close);
    $("ywa-overlay").addEventListener("click", (e) => { if (e.target === $("ywa-overlay")) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("ywa-overlay").hidden) close(); });
  }

  bind();
})();
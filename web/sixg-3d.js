/* 天枢 6G 全球数据穹顶：基于 globe.gl 2.46（MIT）的轻量三维地球。
 * 只负责态势呈现，不参与业务计算；6G 指标仍由后端仿真接口提供。
 * 断网/切换/拥塞状态通过弧线颜色、节点光环与 HUD 文案表达。 */
(function () {
  "use strict";

  var host = document.getElementById("globe-viz");
  if (!host) return;

  function fallback() {
    host.style.display = "none";
    var svg = document.getElementById("topology");
    if (svg) svg.hidden = false;
    var callouts = document.querySelector(".t3-callouts");
    if (callouts) callouts.style.display = "none";
  }

  if (!window.Globe) {
    fallback();
    return;
  }

  var stage = host.parentElement;
  var globe;

  function altitudeForSize() {
    var w = window.innerWidth;
    if (w >= 1800) return 1.2;
    if (w >= 1500) return 1.05;
    return 0.9;
  }
  try {
    globe = window.Globe()(host);
  } catch (err) {
    fallback();
    return;
  }

  var COLORS = {
    active: "#7df3ff",
    online: "#7df3ff",
    standby: "#ffb454",
    busy: "#8f8bff",
    offline: "#ff4fd8",
  };

  var STATUS_TEXT = {
    normal: "地面 6G 主链路",
    outage: "地面 6G 中断 · NTN 接管",
    handover: "低轨卫星波束切换 · URLLC 保底",
    congestion: "eMBB 拥塞 · 切片重调度",
  };

  var FLOW_TEXT = {
    normal: "业务流向：影像 → 6G 基站 → 边缘 AI",
    outage: "业务流向：影像 → NTN → 边缘 AI",
    handover: "业务流向：影像 → 卫星切换 → 边缘 AI",
    congestion: "业务流向：影像 → URLLC 切片 → 边缘 AI",
  };

  var points = [
    { key: "satellite", name: "NTN 低轨卫星", lat: 47.0, lng: 97.0, alt: 0.2, radius: 0.4, status: "active" },
    { key: "satellite2", name: "NTN 备份星", lat: 34.0, lng: 121.0, alt: 0.18, radius: 0.3, status: "standby" },
    { key: "base_station", name: "地面 6G", lat: 41.60, lng: 109.50, alt: 0.02, radius: 0.45, status: "online" },
    { key: "edge", name: "边缘 AI", lat: 44.20, lng: 116.40, alt: 0.03, radius: 0.4, status: "online" },
    { key: "uav", name: "巡护无人机", lat: 37.20, lng: 104.20, alt: 0.05, radius: 0.32, status: "online" },
    { key: "command", name: "指挥中心", lat: 36.60, lng: 118.80, alt: 0.02, radius: 0.38, status: "online" },
  ];

  var arcs = [
    { key: "sat", startLat: 47.0, startLng: 97.0, endLat: 41.60, endLng: 109.50, status: "active", alt: 0.16 },
    { key: "ground", startLat: 41.60, startLng: 109.50, endLat: 44.20, endLng: 116.40, status: "active", alt: 0.035 },
    { key: "uav", startLat: 37.20, startLng: 104.20, endLat: 44.20, endLng: 116.40, status: "active", alt: 0.05 },
    { key: "command", startLat: 44.20, startLng: 116.40, endLat: 36.60, endLng: 118.80, status: "active", alt: 0.05 },
  ];

  var rings = [];

  function colorOf(status) {
    return COLORS[status] || COLORS.online;
  }

  function statusOf(key, topology) {
    if (key === "satellite2") return "standby";
    return topology[key] || "online";
  }

  function syncData(topology) {
    points.forEach(function (p) { p.status = statusOf(p.key, topology); });
    arcs.forEach(function (a) {
      if (a.key === "sat") a.status = topology.satellite === "active" ? "active" : "standby";
      else if (a.key === "ground") a.status = topology.base_station === "offline" ? "offline" : "active";
      else if (a.key === "uav") a.status = topology.uav === "offline" ? "offline" : "active";
      else a.status = topology.command === "offline" ? "offline" : "active";
    });
  }

  try {
    globe
      .globeImageUrl("/vendor/earth-tech-grid.png")
      .backgroundColor("rgba(0,0,0,0)")
      .showAtmosphere(true)
      .atmosphereColor("#35e6ff")
      .atmosphereAltitude(0.19)
      .arcsData(arcs)
      .arcColor(function (d) { return colorOf(d.status); })
      .arcAltitude(function (d) { return d.alt; })
      .arcStroke(1.15)
      .arcDashLength(0.68)
      .arcDashGap(0.12)
      .arcDashAnimateTime(1700)
      .arcsTransitionDuration(0)
      .pointsData(points)
      .pointColor(function (d) { return colorOf(d.status); })
      .pointAltitude(function (d) { return d.alt; })
      .pointRadius(function (d) { return d.radius * 2.3; })
      .pointsMerge(false)
      .pointResolution(12)
      .ringsData(rings)
      .ringColor(function (d) { return colorOf(d.status); })
      .ringMaxRadius(function (d) { return d.maxR; })
      .ringPropagationSpeed(function (d) { return d.speed; })
      .ringRepeatPeriod(function (d) { return d.period; })
      .labelsData([]);

    var material = globe.globeMaterial();
    if (material && material.color) {
      material.color.set("#ffffff");
      material.emissive.set("#04101f");
      material.emissiveIntensity = 0.45;
      material.wireframe = false;
      material.transparent = false;
      material.opacity = 1;
      material.shininess = 0.08;
      material.needsUpdate = true;
    }

    // 画布越高越宽，相机相应后退一点，避免整颗球被裁得只剩一条带
    var altitude = altitudeForSize();
    var currentAltitude = altitude;
    globe.pointOfView({ lat: 41.0, lng: 109.0, altitude: altitude }, 0);
    var controls = globe.controls();
    controls.autoRotate = false;
    controls.autoRotateSpeed = 0;
    controls.enableZoom = false;
    controls.enablePan = false;
    controls.rotateSpeed = 0.35;
  } catch (err) {
    fallback();
    return;
  }

  host.style.display = "block";
  host.style.pointerEvents = "none";

  function resize() {
    if (!globe) return;
    var rect = stage.getBoundingClientRect();
    globe.width(Math.max(1, Math.round(rect.width))).height(Math.max(1, Math.round(rect.height)));
    var want = altitudeForSize();
    if (want !== currentAltitude) {
      currentAltitude = want;
      globe.pointOfView({ altitude: want }, 0);
    }
  }

  function update(topology, scenario) {
    if (!globe || !topology) return;
    syncData(topology);
    globe
      .arcColor(function (d) { return colorOf(d.status); })
      .pointColor(function (d) { return colorOf(d.status); })
      .ringColor(function (d) { return colorOf(d.status); });

    var mode = document.getElementById("t3-mode");
    var flow = document.getElementById("t3-flow");
    if (mode) mode.textContent = STATUS_TEXT[scenario] || STATUS_TEXT.normal;
    if (flow) flow.textContent = FLOW_TEXT[scenario] || FLOW_TEXT.normal;
    stage.classList.toggle("is-outage", scenario === "outage");
    document.querySelectorAll(".t3-callout").forEach(function (el) {
      var st = statusOf(el.getAttribute("data-key"), topology);
      el.classList.toggle("is-offline", st === "offline");
      el.classList.toggle("is-standby", st === "standby" || st === "busy");
    });
  }

  var pulseTimer = 0;
  function pulse() {
    if (!globe) return;
    globe.arcDashAnimateTime(430);
    clearTimeout(pulseTimer);
    pulseTimer = setTimeout(function () {
      if (globe) globe.arcDashAnimateTime(1700);
    }, 3000);
  }

  resize();
  window.addEventListener("resize", resize);
  window.Tianshu3D = { update: update, resize: resize, pulse: pulse };
})();
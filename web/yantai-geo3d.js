/* global THREE */
/*
 * 烟台遥感研判工作区 · 地质结构三维
 * ------------------------------------------------------------------
 * 数据来源（全部为真实数据，不再程序化生成地表）：
 *   地形  /assets/terrain/*.json  AWS Terrain Tiles (Mapzen Terrarium z15/z16)
 *   影像  /api/yantai/asset/<region>/{before,after}.png  Sentinel-2 真彩色
 *         无成对影像的样区直连 Esri World Imagery 拼贴（CORS 开放）
 *   图斑  /api/yantai/regions  patches（UTM 归一化环，u 自西、v 自北）
 *   事件  /api/yantai/workspace events（geometry 为 WGS84）
 * 地层剖面是按真实地形起伏拉伸的结构示意，不是实测地层。
 * ------------------------------------------------------------------
 * 对外 API 与旧版保持一致：
 *   YTGeo3D.mount(stage, event) / unmount() / setPreset(name) / setSpin(on) / isReady()
 */
(function () {
  "use strict";

  if (!window.THREE) return;
  var THREE = window.THREE;

  var TERRAIN_FILES = {
    p07: "/assets/terrain/p07.json",
    p07_site: "/assets/terrain/p07_site.json",
    p01: "/assets/terrain/p01.json",
    dagujia: "/assets/terrain/dagujia.json",
    yt001: "/assets/terrain/yt001.json",
    yt002: "/assets/terrain/yt002.json",
    yt003: "/assets/terrain/yt003.json"
  };

  var P07_BBOX = [120.185, 37.125, 120.245, 37.175];

  // 2026 影像上提取的采石场作业面轮廓（P07 样区归一化坐标，u 自西、v 自北）
  var QUARRY_RING = [[0.8668, 0.55], [0.8786, 0.5561], [0.8768, 0.5702], [0.8814, 0.5746], [0.8914, 0.5561], [0.8987, 0.5544], [0.8932, 0.5614], [0.8951, 0.5719], [0.8878, 0.5754], [0.9015, 0.5833], [0.8987, 0.5877], [0.9142, 0.5956], [0.9261, 0.6105], [0.9224, 0.6193], [0.9261, 0.6281], [0.9197, 0.6237], [0.9151, 0.6281], [0.9206, 0.6421], [0.9252, 0.6465], [0.9398, 0.636], [0.9498, 0.6456], [0.9516, 0.6596], [0.9699, 0.6719], [0.9553, 0.686], [0.9617, 0.6956], [0.9726, 0.6921], [0.979, 0.6982], [0.9571, 0.7088], [0.9626, 0.7281], [0.9608, 0.7368], [0.9507, 0.743], [0.9425, 0.7368], [0.9535, 0.7281], [0.9425, 0.7193], [0.9462, 0.707], [0.9361, 0.7026], [0.9252, 0.7079], [0.9197, 0.6974], [0.9106, 0.7044], [0.9069, 0.7009], [0.8212, 0.7044], [0.8038, 0.7158], [0.8084, 0.7237], [0.7956, 0.7184], [0.7901, 0.7061], [0.781, 0.7114], [0.7673, 0.7088], [0.7637, 0.6895], [0.7792, 0.6851], [0.7874, 0.6719], [0.7765, 0.6614], [0.7801, 0.6509], [0.8057, 0.6193], [0.7984, 0.6088], [0.8093, 0.5982], [0.8057, 0.5877], [0.8157, 0.5851], [0.8449, 0.5605], [0.8558, 0.5588]];

  var STRATA = [
    { id: "base", label: "变质基底", color: "#3f4c55", from: 0.0, to: 0.30, at: 0.17 },
    { id: "fault", label: "破碎带", color: "#6a5a66", from: 0.30, to: 0.44, at: 0.37 },
    { id: "granite", label: "花岗岩", color: "#7f838d", from: 0.44, to: 0.70, at: 0.57 },
    { id: "weathered", label: "强风化", color: "#9a7a52", from: 0.70, to: 0.86, at: 0.78 },
    { id: "quaternary", label: "第四系", color: "#5c4030", from: 0.86, to: 1.0, at: 0.93 }
  ];

  var C = {
    accent: 0x7fe6ff,
    amber: 0xffc46b,
    terrainRim: 0x9fe8ff,
    fog: 0x06121c
  };

  // ---------------------------------------------------------------- 缓存
  var terrainCache = {};
  var imageCache = {};
  var mosaicCache = {};
  var regionsPromise = null;
  var riverPromise = null;
  var compassTex = null;
  var flowTex = null;

  // ---------------------------------------------------------------- 状态
  var state = {
    epoch: 0,
    stage: null,
    renderer: null,
    scene: null,
    camera: null,
    canvas: null,
    root: null,
    block: null,
    specs: [],
    activeKey: null,
    event: null,
    regions: null,
    sites: {},
    ready: false,
    loading: false,
    visible: true,
    spin: false,
    labels: [],
    beacons: [],
    terrainMesh: null,
    surfaceU: null,
    patches: [],
    focusIds: {},
    focusPatches: [],
    sceneTextures: [],
    riverCore: null,
    view: { az: 0.55, el: 0.47, dist: 420, target: new THREE.Vector3() },
    goal: { az: 0.55, el: 0.47, dist: 420, target: new THREE.Vector3() },
    fit: 420,
    imgMode: "base",
    mixValue: 1,
    mixTarget: 1,
    baseValue: 0,
    baseTarget: 0,
    mixAnim: null,
    source: "",
    dates: {},
    controls: null,
    preset: null,
    clock: null,
    raf: 0,
    dragging: false,
    lastX: 0,
    lastY: 0,
    hoverPending: false,
    frame: 0,
    dust: null,
    dustBase: 0
  };

  var chrome = { hud: null, sites: null, imagery: null, labels: null, tip: null, legend: null, hint: null, loading: null, card: null, compass: null, compassDial: null };

  // ---------------------------------------------------------------- 工具
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function fmt(v) { var n = Number(v); return isFinite(n) ? n.toFixed(2) : "—"; }
  function month(d) { return String(d || "").slice(0, 7); }
  function lerpAngle(a, b, t) {
    var d = ((b - a + Math.PI) % (Math.PI * 2)) - Math.PI;
    if (d < -Math.PI) d += Math.PI * 2;
    return a + d * t;
  }
  function buildUrl(url) { return url; }

  // ---------------------------------------------------------------- 数据加载
  function loadTerrain(name) {
    var file = TERRAIN_FILES[name] || TERRAIN_FILES.p07_site;
    if (!terrainCache[file]) {
      terrainCache[file] = fetch(file, { cache: "force-cache" })
        .then(function (r) {
          if (!r.ok) throw new Error("地形底模 HTTP " + r.status);
          return r.json();
        })
        .then(function (json) {
          if (!json || !json.grid || !json.grid.length) throw new Error("地形底模缺少网格");
          return json;
        });
      terrainCache[file].catch(function () { delete terrainCache[file]; });
    }
    return terrainCache[file];
  }

  function loadImage(url) {
    if (!url) return Promise.reject(new Error("missing image"));
    if (imageCache[url]) return imageCache[url];
    var p = new Promise(function (resolve, reject) {
      var img = new Image();
      var done = false;
      var timer = setTimeout(function () {
        if (done) return;
        done = true;
        reject(new Error("影像超时"));
      }, 16000);
      img.onload = function () {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve(img);
      };
      img.onerror = function () {
        if (done) return;
        done = true;
        clearTimeout(timer);
        reject(new Error("影像加载失败"));
      };
      img.decoding = "async";
      img.src = url;
    });
    imageCache[url] = p;
    p.catch(function () { delete imageCache[url]; });
    return p;
  }

  function loadRegions() {
    if (!regionsPromise) {
      regionsPromise = fetch("/api/yantai/regions", { cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("regions HTTP " + r.status);
          return r.json();
        })
        .then(function (json) {
          if (!json || !json.items) throw new Error("regions 数据为空");
          return json.items;
        });
      regionsPromise.catch(function () { regionsPromise = null; });
    }
    return regionsPromise;
  }

  function fallbackRegions() {
    return [
      {
        id: "p07_2023b", label: "P07 样区", bbox_wgs84: P07_BBOX,
        dates: { base: "2023-11-02", now: "2026-11-11" },
        images: {
          before: "/api/yantai/asset/p07_2023b/before.png",
          after: "/api/yantai/asset/p07_2023b/after.png"
        },
        patches: [], patch_count: 43, patch_area_ha: 22.36
      },
      {
        id: "p01", label: "P01 样区", bbox_wgs84: [120.07, 37.41, 120.12, 37.45],
        dates: { base: "2025-11-21", now: "2026-11-11" },
        images: {
          before: "/api/yantai/asset/p01/before.png",
          after: "/api/yantai/asset/p01/after.png"
        },
        patches: [], patch_count: 0, patch_area_ha: 0
      }
    ];
  }

  function loadRiverCenterline() {
    if (!riverPromise) {
      riverPromise = fetch("/api/yantai/workspace", { cache: "force-cache" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          var list = (d && d.rivers) || [];
          var pick = null;
          for (var i = 0; i < list.length; i++) {
            if (/大沽夹/.test(list[i].name || "")) { pick = list[i]; break; }
          }
          if (!pick) pick = list[0] || null;
          return pick && pick.coords ? pick.coords : [];
        })
        .catch(function () { riverPromise = null; return []; });
    }
    return riverPromise;
  }

  // ---------------------------------------------------------------- 地区规格
  function getSite(id) {
    for (var i = 0; i < state.regions.length; i++) {
      if (state.regions[i].id === id) return state.regions[i];
    }
    return null;
  }

  function siteSpec(item) {
    if (!item) return null;
    var isP01 = item.id === "p01";
    var hasPair = !!(item.images && item.images.before);
    return {
      key: "site:" + item.id,
      id: item.id,
      label: item.label || item.id,
      short: isP01 ? "P01" : "P07",
      terrain: isP01 ? "p01" : "p07_site",
      siteBbox: item.bbox_wgs84 || (isP01 ? [120.07, 37.41, 120.12, 37.45] : P07_BBOX),
      imagery: hasPair ? {
        before: item.images.before,
        after: item.images.after || item.images.before,
        bbox: item.bbox_wgs84,
        dates: item.dates || {}
      } : null,
      basemap: hasPair ? (isP01 ? "/assets/basemap/p01_sat.jpg" : "/assets/basemap/p07_site_sat.jpg") : null,
      patches: item.patches || [],
      mode: isP01 ? "gap" : "site",
      exag: isP01 ? 8.5 : 3.1,
      event: null
    };
  }

  function eventSpec(event) {
    if (!event) return null;
    var site = null;
    var regionId = event.region_id || "";
    if (regionId) site = getSite(regionId);
    if (!site && event.domain !== "river") site = getSite("p07_2023b");
    var terrain = pickTerrain(event);
    var bbox = event.bbox || event.bbox_wgs84 || null;
    var imagery = null;
    if (site && site.images && site.images.before && bbox) {
      imagery = {
        before: site.images.before,
        after: site.images.after || site.images.before,
        bbox: site.bbox_wgs84,
        dates: site.dates || {}
      };
    }
    return {
      key: "event",
      id: event.id,
      label: "本事件",
      short: event.id || "事件",
      terrain: terrain,
      siteBbox: site ? site.bbox_wgs84 : null,
      imagery: imagery,
      basemap: site ? (site.id === "p01" ? "/assets/basemap/p01_sat.jpg" : "/assets/basemap/p07_site_sat.jpg") : null,
      patches: site ? (site.patches || []) : [],
      mode: event.domain === "river" ? "river" : "event",
      exag: event.domain === "river" ? 2.4 : 1.8,
      event: event
    };
  }

  function riverSpec() {
    return {
      key: "dagujia",
      id: "dagujia",
      label: "大沽夹河",
      short: "大沽夹河",
      terrain: "dagujia",
      siteBbox: null,
      imagery: null,
      basemap: "/assets/basemap/dagujia_sat.jpg",
      patches: [],
      mode: "river",
      exag: 5.5,
      event: null
    };
  }

  function pickTerrain(event) {
    var m = String((event && event.id) || "").match(/(\d{3})$/);
    var key = m ? "yt" + m[1] : "";
    if (TERRAIN_FILES[key]) return key;
    if (event && event.domain === "river") {
      var c = event.center_lonlat;
      if (c) {
        var d = Math.hypot((c[0] - 121.15) * 88, (c[1] - 37.12) * 111);
        if (d < 6) return "yt003";
        return "dagujia";
      }
      return "dagujia";
    }
    var cnt = event && event.center_lonlat;
    if (cnt) {
      var d7 = Math.hypot((cnt[0] - 120.215) * 88, (cnt[1] - 37.15) * 111);
      var dd = Math.hypot((cnt[0] - 121.15) * 88, (cnt[1] - 37.12) * 111);
      if (dd < d7) return dd < 6 ? "yt003" : "dagujia";
    }
    return "p07_site";
  }

  // ---------------------------------------------------------------- 纹理
  function maxAniso() {
    return state.renderer ? state.renderer.capabilities.getMaxAnisotropy() : 1;
  }

  function canvasTexture(cv, opts) {
    var t = new THREE.CanvasTexture(cv);
    t.encoding = THREE.sRGBEncoding;
    t.anisotropy = maxAniso();
    t.minFilter = THREE.LinearMipmapLinearFilter;
    t.magFilter = THREE.LinearFilter;
    if (opts && opts.repeatS) {
      t.wrapS = THREE.RepeatWrapping;
      t.wrapT = THREE.ClampToEdgeWrapping;
    }
    t.needsUpdate = true;
    return t;
  }

  function gradeImage(img) {
    var w = img.naturalWidth || img.width;
    var h = img.naturalHeight || img.height;
    var cv = document.createElement("canvas");
    cv.width = w;
    cv.height = h;
    var g = cv.getContext("2d");
    try {
      g.filter = "saturate(1.22) contrast(1.06) brightness(1.18)";
    } catch (e) { /* 老浏览器不支持滤镜 */ }
    g.drawImage(img, 0, 0);
    return cv;
  }

  function fallbackCanvas() {
    var cv = document.createElement("canvas");
    cv.width = 512;
    cv.height = 512;
    var g = cv.getContext("2d");
    g.fillStyle = "#2c3b33";
    g.fillRect(0, 0, 512, 512);
    var seed = 7;
    function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }
    for (var i = 0; i < 2400; i++) {
      var x = rnd() * 512;
      var y = rnd() * 512;
      var r = 2 + rnd() * 18;
      g.fillStyle = "rgba(" + (54 + rnd() * 50 | 0) + "," + (58 + rnd() * 44 | 0) + "," + (42 + rnd() * 30 | 0) + ",0.4)";
      g.beginPath();
      g.arc(x, y, r, 0, Math.PI * 2);
      g.fill();
    }
    return cv;
  }

  var rockMaps = {};

  function rockHash(x, y) {
    var n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
    return n - Math.floor(n);
  }

  function rockNoise(x, y) {
    var xi = Math.floor(x), yi = Math.floor(y);
    var xf = x - xi, yf = y - yi;
    var u = xf * xf * (3 - 2 * xf);
    var v = yf * yf * (3 - 2 * yf);
    var a = rockHash(xi, yi), b = rockHash(xi + 1, yi);
    var c = rockHash(xi, yi + 1), d = rockHash(xi + 1, yi + 1);
    return lerp(lerp(a, b, u), lerp(c, d, u), v);
  }

  function rockFbm(x, y, oct) {
    var v = 0, amp = 0.5, f = 1;
    for (var i = 0; i < oct; i++) {
      v += rockNoise(x * f, y * f) * amp;
      f *= 2.03;
      amp *= 0.5;
    }
    return v;
  }

  // 每种岩性一张 512² 程序化纹理：底色 + 颗粒 + 层理/节理/角砾，另生成法线图
  function getRockMaps(layer) {
    if (rockMaps[layer.id]) return rockMaps[layer.id];
    var S = 512;
    var cv = document.createElement("canvas");
    cv.width = S;
    cv.height = S;
    var g = cv.getContext("2d");
    var img = g.createImageData(S, S);
    var data = img.data;
    var heights = new Float32Array(S * S);
    var hex = parseInt(layer.color.slice(1), 16);
    var br = (hex >> 16) & 255, bg = (hex >> 8) & 255, bb = hex & 255;
    var id = layer.id;
    var seed = 17;
    function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; }

    for (var y = 0; y < S; y++) {
      for (var x = 0; x < S; x++) {
        var n1 = rockFbm(x * 0.012, y * 0.012, 4);
        var n2 = rockFbm(x * 0.055 + 9.7, y * 0.055 + 3.1, 2);
        var band = 0.5 + 0.5 * Math.sin(y * 0.055 + n1 * 8.2);
        var v = 0.62 + 0.34 * n1 + 0.10 * (n2 - 0.5);
        if (id === "quaternary") {
          v = 0.68 + 0.22 * n1 + 0.10 * band + 0.05 * (n2 - 0.5);
        } else if (id === "weathered") {
          v = 0.58 + 0.34 * n1 + 0.12 * (n2 - 0.5) + 0.06 * band;
        } else if (id === "fault") {
          v = 0.44 + 0.30 * n1 + 0.18 * (n2 - 0.5);
        } else if (id === "base") {
          v = 0.52 + 0.32 * n1 + 0.12 * band;
        }
        var i = (y * S + x) * 4;
        data[i] = clamp(br * v + (n2 - 0.5) * 26, 0, 255);
        data[i + 1] = clamp(bg * v + (n2 - 0.5) * 20, 0, 255);
        data[i + 2] = clamp(bb * v + (n2 - 0.5) * 16, 0, 255);
        data[i + 3] = 255;
        heights[y * S + x] = n1 * 0.62 + n2 * 0.28 + band * 0.10;
      }
    }
    g.putImageData(img, 0, 0);

    function lines(count, color, width, len) {
      for (var k = 0; k < count; k++) {
        var x = rnd() * S, y = rnd() * S;
        g.strokeStyle = color;
        g.lineWidth = width * (0.6 + rnd() * 0.8);
        g.beginPath();
        g.moveTo(x, y);
        var segs = 2 + (rnd() * 2 | 0);
        for (var q = 0; q < segs; q++) {
          x += (rnd() - 0.5) * len;
          y += (rnd() - 0.35) * len * 0.38;
          g.lineTo(x, y);
        }
        g.stroke();
      }
    }
    function speckles(count, light, size) {
      for (var k = 0; k < count; k++) {
        var x = rnd() * S, y = rnd() * S;
        var a = 0.10 + rnd() * 0.30;
        g.fillStyle = rnd() > 0.5 ? "rgba(246,242,230," + a + ")" : "rgba(8,10,12," + (a * 0.9) + ")";
        var r = 0.5 + rnd() * size;
        g.beginPath();
        g.arc(x, y, r, 0, Math.PI * 2);
        g.fill();
      }
    }

    if (id === "quaternary") {
      for (var q = 0; q < 46; q++) {
        var qy = rnd() * S;
        g.strokeStyle = "rgba(28,18,12," + (0.06 + rnd() * 0.14) + ")";
        g.lineWidth = 1 + rnd() * 2;
        g.beginPath();
        g.moveTo(0, qy);
        for (var qx = 0; qx <= S; qx += 32) g.lineTo(qx, qy + Math.sin(qx * 0.02 + q) * 2.2);
        g.stroke();
      }
      speckles(700, false, 2.2);
      lines(26, "rgba(20,12,8,.22)", 1.2, 34);
    } else if (id === "weathered") {
      for (var w = 0; w < 90; w++) {
        var wx = rnd() * S, wy = rnd() * S;
        g.fillStyle = "rgba(" + (150 + rnd() * 60 | 0) + "," + (90 + rnd() * 40 | 0) + "," + (40 + rnd() * 30 | 0) + "," + (0.035 + rnd() * 0.07) + ")";
        g.beginPath();
        g.ellipse(wx, wy, 10 + rnd() * 42, 6 + rnd() * 24, rnd() * Math.PI, 0, Math.PI * 2);
        g.fill();
      }
      speckles(520, true, 1.8);
      lines(40, "rgba(34,24,16,.28)", 1.1, 46);
    } else if (id === "granite") {
      speckles(1600, true, 1.5);
      for (var s2 = 0; s2 < 280; s2++) {
        var sx = rnd() * S, sy = rnd() * S;
        g.fillStyle = "rgba(214,198,184," + (0.12 + rnd() * 0.28) + ")";
        g.fillRect(sx, sy, 1 + rnd() * 2.6, 1 + rnd() * 2.2);
      }
      lines(30, "rgba(24,28,34,.34)", 1.4, 56);
    } else if (id === "fault") {
      for (var c = 0; c < 26; c++) {
        var cx = rnd() * S, cy = rnd() * S, cr = 6 + rnd() * 26;
        g.fillStyle = "rgba(" + (90 + rnd() * 65 | 0) + "," + (80 + rnd() * 55 | 0) + "," + (85 + rnd() * 55 | 0) + "," + (0.18 + rnd() * 0.30) + ")";
        g.beginPath();
        for (var e = 0; e < 6; e++) {
          var a2 = (e / 6) * Math.PI * 2;
          var rr = cr * (0.6 + rnd() * 0.6);
          var px = cx + Math.cos(a2) * rr, py = cy + Math.sin(a2) * rr;
          if (e === 0) g.moveTo(px, py); else g.lineTo(px, py);
        }
        g.closePath();
        g.fill();
      }
      lines(80, "rgba(16,12,18,.36)", 1.2, 60);
      lines(18, "rgba(220,240,250,.10)", 1, 90);
    } else {
      for (var b2 = 0; b2 < 26; b2++) {
        var by = rnd() * S;
        g.strokeStyle = "rgba(120,150,172," + (0.06 + rnd() * 0.10) + ")";
        g.lineWidth = 1 + rnd() * 1.6;
        g.beginPath();
        g.moveTo(0, by);
        for (var bx = 0; bx <= S; bx += 22) g.lineTo(bx, by + Math.sin(bx * 0.025 + b2) * 9);
        g.stroke();
      }
      lines(14, "rgba(226,240,248,.22)", 1.3, 100);
      speckles(300, true, 1.4);
    }

    // 法线图
    var ncv = document.createElement("canvas");
    ncv.width = S;
    ncv.height = S;
    var ng = ncv.getContext("2d");
    var nimg = ng.createImageData(S, S);
    var nd = nimg.data;
    var strength = 3.2;
    for (var ny = 0; ny < S; ny++) {
      for (var nx = 0; nx < S; nx++) {
        var hl = heights[ny * S + ((nx - 1 + S) % S)];
        var hr = heights[ny * S + ((nx + 1) % S)];
        var hu = heights[((ny - 1 + S) % S) * S + nx];
        var hd = heights[((ny + 1) % S) * S + nx];
        var dx = (hl - hr) * strength;
        var dy = (hd - hu) * strength;
        var len = Math.sqrt(dx * dx + dy * dy + 1);
        var j = (ny * S + nx) * 4;
        nd[j] = clamp((dx / len * 0.5 + 0.5) * 255, 0, 255);
        nd[j + 1] = clamp((dy / len * 0.5 + 0.5) * 255, 0, 255);
        nd[j + 2] = clamp((1 / len * 0.5 + 0.5) * 255, 0, 255);
        nd[j + 3] = 255;
      }
    }
    ng.putImageData(nimg, 0, 0);

    var tex = canvasTexture(cv, { repeatS: true });
    var ntex = canvasTexture(ncv, { repeatS: true });
    ntex.encoding = THREE.LinearEncoding;
    rockMaps[layer.id] = { map: tex, normalMap: ntex };
    return rockMaps[layer.id];
  }

  function getCompassTexture() {
    if (compassTex) return compassTex;
    var S = 1024;
    var cv = document.createElement("canvas");
    cv.width = S;
    cv.height = S;
    var g = cv.getContext("2d");
    var cx = S / 2;
    var cy = S / 2;
    var grd = g.createRadialGradient(cx, cy, 30, cx, cy, 500);
    grd.addColorStop(0, "rgba(60,150,180,.16)");
    grd.addColorStop(0.75, "rgba(40,110,140,.05)");
    grd.addColorStop(1, "rgba(20,60,80,0)");
    g.fillStyle = grd;
    g.beginPath();
    g.arc(cx, cy, 500, 0, Math.PI * 2);
    g.fill();

    g.strokeStyle = "rgba(130,218,240,.45)";
    g.lineWidth = 2;
    [470, 430, 340].forEach(function (r, idx) {
      g.globalAlpha = idx === 2 ? 0.35 : 0.55;
      g.beginPath();
      g.arc(cx, cy, r, 0, Math.PI * 2);
      g.stroke();
    });
    g.globalAlpha = 1;
    for (var i = 0; i < 72; i++) {
      var a = (i / 72) * Math.PI * 2;
      var major = i % 9 === 0;
      var r1 = major ? 452 : 462;
      var r2 = 476;
      g.strokeStyle = major ? "rgba(180,240,255,.75)" : "rgba(120,200,225,.35)";
      g.lineWidth = major ? 3 : 1.4;
      g.beginPath();
      g.moveTo(cx + Math.sin(a) * r1, cy - Math.cos(a) * r1);
      g.lineTo(cx + Math.sin(a) * r2, cy - Math.cos(a) * r2);
      g.stroke();
    }
    g.strokeStyle = "rgba(140,225,245,.22)";
    g.lineWidth = 2;
    g.setLineDash([12, 14]);
    g.beginPath();
    g.arc(cx, cy, 300, 0, Math.PI * 2);
    g.stroke();
    g.setLineDash([]);
    compassTex = canvasTexture(cv);
    compassTex.minFilter = THREE.LinearFilter;
    return compassTex;
  }

  function getFlowTexture() {
    if (flowTex) return flowTex;
    var cv = document.createElement("canvas");
    cv.width = 256;
    cv.height = 64;
    var g = cv.getContext("2d");
    g.clearRect(0, 0, 256, 64);
    var seed = 31;
    function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }
    for (var i = 0; i < 42; i++) {
      var y = 6 + rnd() * 52;
      var x = rnd() * 256;
      var len = 22 + rnd() * 90;
      var grd = g.createLinearGradient(x, 0, x + len, 0);
      grd.addColorStop(0, "rgba(180,250,255,0)");
      grd.addColorStop(0.45, "rgba(190,252,255," + (0.25 + rnd() * 0.45) + ")");
      grd.addColorStop(1, "rgba(180,250,255,0)");
      g.fillStyle = grd;
      g.fillRect(x, y, len, 1 + rnd() * 2.4);
    }
    flowTex = canvasTexture(cv);
    flowTex.wrapS = THREE.RepeatWrapping;
    flowTex.wrapT = THREE.ClampToEdgeWrapping;
    return flowTex;
  }

  // ---------------------------------------------------------------- 影像
  function bboxInside(inner, outer) {
    var e = 0.0004;
    return inner[0] >= outer[0] - e && inner[1] >= outer[1] - e &&
      inner[2] <= outer[2] + e && inner[3] <= outer[3] + e;
  }

  function uvRectFor(terrainBBox, imgBBox) {
    var w = imgBBox[0];
    var s = imgBBox[1];
    var e = imgBBox[2];
    var n = imgBBox[3];
    var sx = e - w;
    var sy = n - s;
    if (sx <= 0 || sy <= 0) return [0, 0, 1, 1];
    var u0 = clamp((terrainBBox[0] - w) / sx, 0, 1);
    var u1 = clamp((terrainBBox[2] - w) / sx, 0, 1);
    var vTop = clamp((n - terrainBBox[3]) / sy, 0, 1);
    var vBot = clamp((n - terrainBBox[1]) / sy, 0, 1);
    return [u0, 1 - vBot, u1, 1 - vTop];
  }

  function lon2px(lon, lat, z) {
    var n = Math.pow(2, z) * 256;
    var x = (lon + 180) / 360 * n;
    var s = Math.sin(lat * Math.PI / 180);
    var y = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n;
    return [x, y];
  }

  function chooseZoom(bbox) {
    for (var z = 17; z >= 11; z--) {
      var p0 = lon2px(bbox[0], bbox[3], z);
      var p1 = lon2px(bbox[2], bbox[1], z);
      var w = p1[0] - p0[0];
      var h = p1[1] - p0[1];
      var tx0 = Math.floor(p0[0] / 256);
      var tx1 = Math.floor((p1[0] - 0.001) / 256);
      var ty0 = Math.floor(p0[1] / 256);
      var ty1 = Math.floor((p1[1] - 0.001) / 256);
      var n = (tx1 - tx0 + 1) * (ty1 - ty0 + 1);
      if (n <= 72 && w <= 2048 && h <= 2048) return z;
    }
    return 11;
  }

  function loadTile(z, x, y) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      var done = false;
      var timer = setTimeout(function () {
        if (done) return;
        done = true;
        reject(new Error("tile timeout"));
      }, 14000);
      img.onload = function () {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve(img);
      };
      img.onerror = function () {
        if (done) return;
        done = true;
        clearTimeout(timer);
        reject(new Error("tile error"));
      };
      img.src = "/api/tile/esri/" + z + "/" + x + "/" + y;
    });
  }

  function loadMosaic(bbox) {
    var key = bbox.map(function (v) { return v.toFixed(5); }).join(",");
    if (mosaicCache[key]) return mosaicCache[key];
    var z = chooseZoom(bbox);
    var p0 = lon2px(bbox[0], bbox[3], z);
    var p1 = lon2px(bbox[2], bbox[1], z);
    var px0 = p0[0];
    var py0 = p0[1];
    var px1 = p1[0];
    var py1 = p1[1];
    var tx0 = Math.floor(px0 / 256);
    var tx1 = Math.floor((px1 - 0.001) / 256);
    var ty0 = Math.floor(py0 / 256);
    var ty1 = Math.floor((py1 - 0.001) / 256);
    var tasks = [];
    var coords = [];
    var failed = 0;
    for (var ty = ty0; ty <= ty1; ty++) {
      for (var tx = tx0; tx <= tx1; tx++) {
        coords.push([tx, ty]);
        tasks.push(loadTile(z, tx, ty).catch(function () { failed++; return null; }));
      }
    }
    var p = Promise.all(tasks).then(function (imgs) {
      if (failed > tasks.length * 0.6) throw new Error("瓦片大部分不可用");
      var S = 2048;
      var scale = 1;
      var wpx = Math.max(1, Math.round(px1 - px0));
      var hpx = Math.max(1, Math.round(py1 - py0));
      if (wpx > S || hpx > S) scale = Math.min(S / wpx, S / hpx);
      var cv = document.createElement("canvas");
      cv.width = Math.max(1, Math.round(wpx * scale));
      cv.height = Math.max(1, Math.round(hpx * scale));
      var g = cv.getContext("2d");
      g.fillStyle = "#152430";
      g.fillRect(0, 0, cv.width, cv.height);
      var ox = -(px0 - tx0 * 256);
      var oy = -(py0 - ty0 * 256);
      for (var i = 0; i < imgs.length; i++) {
        if (!imgs[i]) continue;
        var tx = coords[i][0];
        var ty = coords[i][1];
        var dx = (tx - tx0) * 256 + ox;
        var dy = (ty - ty0) * 256 + oy;
        g.drawImage(imgs[i], dx * scale, dy * scale, 256 * scale, 256 * scale);
      }
      // 轻调色，避免瓦片拼接边缘发灰
      g.globalCompositeOperation = "source-atop";
      g.fillStyle = "rgba(12,26,34,.12)";
      g.fillRect(0, 0, cv.width, cv.height);
      g.globalCompositeOperation = "source-over";
      return cv;
    });
    mosaicCache[key] = p;
    p.catch(function () { delete mosaicCache[key]; });
    return p;
  }

  function loadImagery(spec, terrain) {
    var bbox = terrain.bbox;
    var pair = spec.imagery;
    var baseP = spec.basemap ? loadImage(spec.basemap).catch(function (err) {
      console.warn("[geo3d] 高清底图不可用：", err && err.message);
      return null;
    }) : Promise.resolve(null);
    if (pair && pair.before) {
      var imgBBox = pair.bbox || bbox;
      return Promise.all([
        loadImage(pair.before).catch(function () { return null; }),
        loadImage(pair.after).catch(function () { return null; }),
        baseP
      ]).then(function (imgs) {
        var a = imgs[0];
        var b = imgs[1];
        var base = imgs[2];
        if (!a && !b && !base) throw new Error("样区影像不可用");
        if (!a) a = b;
        if (!b) b = a;
        if (!a && base) { a = base; b = base; }
        if (!bboxInside(bbox, imgBBox)) throw new Error("事件窗口超出样区影像");
        var texA = canvasTexture(gradeImage(a));
        var texB = a === b ? texA : canvasTexture(gradeImage(b));
        var texC = base ? canvasTexture(base) : texA;
        return {
          texA: texA,
          texB: texB,
          texC: texC,
          rect: uvRectFor(bbox, imgBBox),
          source: base && (imgs[0] || imgs[1]) ? "hybrid" : (base ? "esri" : "sentinel"),
          dates: pair.dates || {}
        };
      }).catch(function (err) {
        console.warn("[geo3d] Sentinel 影像不可用，回退 Esri：", err && err.message);
        return esriImagery(bbox);
      });
    }
    if (spec.basemap) {
      return loadImage(spec.basemap).then(function (img) {
        var tex = canvasTexture(img);
        return { texA: tex, texB: tex, texC: tex, rect: [0, 0, 1, 1], source: "esri", dates: {} };
      }).catch(function (err) {
        console.warn("[geo3d] 本地卫星底图不可用，回退瓦片：", err && err.message);
        return esriImagery(bbox);
      });
    }
    return esriImagery(bbox);
  }

  function esriImagery(bbox) {
    return loadMosaic(bbox).then(function (cv) {
      var tex = canvasTexture(cv);
      return { texA: tex, texB: tex, texC: tex, rect: [0, 0, 1, 1], source: "esri", dates: {} };
    }).catch(function (err) {
      console.warn("[geo3d] Esri 瓦片不可用，回退结构底模：", err && err.message);
      var tex = canvasTexture(fallbackCanvas());
      return { texA: tex, texB: tex, texC: tex, rect: [0, 0, 1, 1], source: "fallback", dates: {} };
    });
  }

  // ---------------------------------------------------------------- 几何
  function makeBlock(terrain, spec) {
    var bbox = terrain.bbox;
    var midLat = (bbox[1] + bbox[3]) / 2;
    var lonM = Math.max(1, (bbox[2] - bbox[0]) * 111320 * Math.cos(midLat * Math.PI / 180));
    var latM = Math.max(1, (bbox[3] - bbox[1]) * 110540);
    var W = 200;
    var D = W * latM / lonM;
    var exag = spec.exag || 3;
    var hs = W / lonM * exag;
    var maxH = Math.max(0.5, (terrain.elev_max - terrain.elev_min) * hs);
    var wallDepth = clamp(maxH * 0.75 + 18, 26, 56);
    var baseY = -wallDepth;
    var gw = terrain.width;
    var gh = terrain.height;
    var grid = terrain.grid;
    var elevMin = terrain.elev_min;

    function heightAt(x, z) {
      var fx = clamp((x + W / 2) / W, 0, 1) * (gw - 1);
      var fz = clamp((z + D / 2) / D, 0, 1) * (gh - 1);
      var ix = Math.floor(fx);
      var iz = Math.floor(fz);
      var ix1 = Math.min(gw - 1, ix + 1);
      var iz1 = Math.min(gh - 1, iz + 1);
      var tx = fx - ix;
      var tz = fz - iz;
      var v00 = grid[iz][ix];
      var v10 = grid[iz][ix1];
      var v01 = grid[iz1][ix];
      var v11 = grid[iz1][ix1];
      var v = lerp(lerp(v00, v10, tx), lerp(v01, v11, tx), tz);
      return (v - elevMin) * hs;
    }

    function lonToX(lon) { return (lon - bbox[0]) / (bbox[2] - bbox[0]) * W - W / 2; }
    function latToZ(lat) { return (bbox[3] - lat) / (bbox[3] - bbox[1]) * D - D / 2; }

    return {
      bbox: bbox,
      W: W,
      D: D,
      hs: hs,
      maxH: maxH,
      baseY: baseY,
      wallDepth: wallDepth,
      elevMin: elevMin,
      elevMax: terrain.elev_max,
      gw: gw,
      gh: gh,
      grid: grid,
      exag: exag,
      spec: spec,
      centerY: (maxH + baseY) / 2,
      heightAt: heightAt,
      lonToX: lonToX,
      latToZ: latToZ,
      lonlat: function (lon, lat) {
        var x = lonToX(lon);
        var z = latToZ(lat);
        return new THREE.Vector3(x, heightAt(x, z), z);
      }
    };
  }

  function surfaceMaterial(texA, texB, texC) {
    var uniforms = {
      uMapB: { value: texB },
      uMapC: { value: texC || texA },
      uBase: { value: texC && state.imgMode === "base" ? 1 : 0 },
      uMix: { value: state.imgMode === "before" ? 0 : 1 },
      uFront: { value: -1 },
      uEdge: { value: 0 }
    };
    var mat = new THREE.MeshStandardMaterial({
      map: texA,
      vertexColors: true,
      roughness: 0.92,
      metalness: 0.02,
      color: 0xffffff
    });
    mat.userData.surfaceUniforms = uniforms;
    mat.onBeforeCompile = function (shader) {
      shader.uniforms.uMapB = uniforms.uMapB;
      shader.uniforms.uMapC = uniforms.uMapC;
      shader.uniforms.uBase = uniforms.uBase;
      shader.uniforms.uMix = uniforms.uMix;
      shader.uniforms.uFront = uniforms.uFront;
      shader.uniforms.uEdge = uniforms.uEdge;
      shader.fragmentShader = shader.fragmentShader
        .replace(
          "#include <map_pars_fragment>",
          "#include <map_pars_fragment>\n" +
          "uniform sampler2D uMapB;\n" +
          "uniform sampler2D uMapC;\n" +
          "uniform float uBase;\n" +
          "uniform float uMix;\n" +
          "uniform float uFront;\n" +
          "uniform float uEdge;\n"
        )
        .replace(
          "#include <map_fragment>",
          "#ifdef USE_MAP\n" +
          "  vec4 texelA = mapTexelToLinear( texture2D( map, vUv ) );\n" +
          "  vec4 texelB = mapTexelToLinear( texture2D( uMapB, vUv ) );\n" +
          "  vec4 texelC = mapTexelToLinear( texture2D( uMapC, vUv ) );\n" +
          "  vec4 texelColor = mix( mix( texelA, texelB, uMix ), texelC, uBase );\n" +
          "  diffuseColor *= texelColor;\n" +
          "  float band = abs( vUv.x - uFront );\n" +
          "  float inside = 1.0 - step( uEdge, band );\n" +
          "  diffuseColor.rgb += vec3( 0.34, 0.68, 0.84 ) * inside * ( 0.08 + 0.30 * ( 1.0 - band / max( uEdge, 0.001 ) ) );\n" +
          "#endif\n"
        );
    };
    mat.customProgramCacheKey = function () { return "yw-geo-surface-v3"; };
    return mat;
  }

  function buildTerrainMesh(block, terrain, texA, texB, rect, texC) {
    var gw = block.gw;
    var gh = block.gh;
    var count = gw * gh;
    var pos = new Float32Array(count * 3);
    var uv = new Float32Array(count * 2);
    var idx = [];
    var i;
    var ix;
    var iy;
    for (iy = 0; iy < gh; iy++) {
      for (ix = 0; ix < gw; ix++) {
        i = iy * gw + ix;
        var x = -block.W / 2 + (ix / (gw - 1)) * block.W;
        var z = -block.D / 2 + (iy / (gh - 1)) * block.D;
        var y = (block.grid[iy][ix] - block.elevMin) * block.hs;
        pos[i * 3] = x;
        pos[i * 3 + 1] = y;
        pos[i * 3 + 2] = z;
        uv[i * 2] = rect[0] + (rect[2] - rect[0]) * (ix / (gw - 1));
        uv[i * 2 + 1] = rect[1] + (rect[3] - rect[1]) * (1 - iy / (gh - 1));
      }
    }
    for (iy = 0; iy < gh - 1; iy++) {
      for (ix = 0; ix < gw - 1; ix++) {
        var a = iy * gw + ix;
        var b = a + 1;
        var c = a + gw;
        var d = c + 1;
        idx.push(a, c, b, b, c, d);
      }
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    geo.setIndex(idx);
    geo.computeVertexNormals();

    // 顶点色：只做坡度明暗 + 微弱高程色偏，颜色仍以真实影像为准
    var normals = geo.attributes.normal;
    var colors = new Float32Array(count * 3);
    for (i = 0; i < count; i++) {
      var ny = normals.getY(i);
      var shade = 0.80 + 0.20 * clamp(ny, 0, 1);
      var t = clamp((block.grid[Math.floor(i / gw)][i % gw] - terrain.elev_min) /
        Math.max(0.001, terrain.elev_max - terrain.elev_min), 0, 1);
      colors[i * 3] = shade * (0.93 + 0.06 * t);
      colors[i * 3 + 1] = shade * (0.95 + 0.03 * t);
      colors[i * 3 + 2] = shade * (1.0 - 0.05 * t);
    }
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    var mat = surfaceMaterial(texA, texB, texC);
    var mesh = new THREE.Mesh(geo, mat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return { mesh: mesh, mat: mat, uniforms: mat.userData.surfaceUniforms, positions: pos, indices: idx, uv: uv };
  }

  function compositeOverlay(im, overlayTex) {
    var ocv = overlayTex && overlayTex.image;
    if (!ocv) return;
    var targets = [im.texA, im.texB, im.texC];
    for (var i = 0; i < targets.length; i++) {
      var t = targets[i];
      if (!t || !t.image) continue;
      if (i > 0 && targets[i] === targets[0]) continue;
      var src = t.image;
      var W = Math.max(1024, src.width || 0);
      var H = Math.max(1024, src.height || 0);
      var cv = document.createElement("canvas");
      cv.width = W;
      cv.height = H;
      var g = cv.getContext("2d");
      g.drawImage(src, 0, 0, W, H);
      g.drawImage(ocv, 0, 0, W, H);
      t.image = cv;
      t.needsUpdate = true;
    }
  }

  function perimeter(W, D, n) {
    var pts = [];
    var i;
    for (i = 0; i <= n; i++) pts.push({ x: -W / 2 + (i / n) * W, z: D / 2, nx: 0, nz: 1 });
    for (i = 1; i <= n; i++) pts.push({ x: W / 2, z: D / 2 - (i / n) * D, nx: 1, nz: 0 });
    for (i = 1; i <= n; i++) pts.push({ x: W / 2 - (i / n) * W, z: -D / 2, nx: 0, nz: -1 });
    for (i = 1; i <= n; i++) pts.push({ x: -W / 2, z: -D / 2 + (i / n) * D, nx: -1, nz: 0 });
    return pts;
  }

  // 五层独立侧壁：层界按噪声/倾角扰动成不整合面，每层单独岩性纹理与法线
  function buildWalls(block) {
    var pts = perimeter(block.W, block.D, 128);
    var total = pts.length;
    var tops = [];
    var dists = [];
    var bounds = [];
    var LAYER_F = [0.0, 0.30, 0.44, 0.70, 0.86, 1.0];
    var i, k;
    for (i = 0; i < total; i++) {
      var p = pts[i];
      var top = block.heightAt(p.x, p.z);
      var prev = i > 0 ? pts[i - 1] : null;
      var dist = i === 0 ? 0 : (dists[i - 1] + Math.hypot(p.x - prev.x, p.z - prev.z));
      tops.push(top);
      dists.push(dist);
      var depth = Math.max(2, top - block.baseY);
      var b = [block.baseY];
      for (k = 1; k <= 4; k++) {
        var f = LAYER_F[k];
        // 层界起伏：沿边界的低频噪声 + 缓倾角；第四系底界起伏更大（不整合）
        var sN = dists[i] / 26;
        var nz = rockFbm(sN * 0.72 + k * 1.9, k * 7.3 + 2.1, 3) - 0.5;
        var amp = k === 4 ? 0.075 : k === 2 ? 0.045 : 0.035;
        var dip = (i / total - 0.5) * 0.045;
        var y = block.baseY + depth * clamp(f + dip + nz * amp, 0.03, 0.97);
        b.push(y);
      }
      b.push(top);
      for (k = 1; k <= 4; k++) {
        b[k] = Math.max(b[k - 1] + 0.35, Math.min(b[k], b[k + 1] - 0.25));
      }
      bounds.push(b);
    }

    var group = new THREE.Group();
    var layers = [];
    for (k = 0; k < 5; k++) {
      var vcount = total * 2;
      var pos = new Float32Array(vcount * 3);
      var uv = new Float32Array(vcount * 2);
      var nor = new Float32Array(vcount * 3);
      var col = new Float32Array(vcount * 3);
      var idx = [];
      for (i = 0; i < total; i++) {
        var pp = pts[i];
        var lo = bounds[i][k];
        var hi = bounds[i][k + 1];
        var d0 = dists[i] / 16;
        var v0 = 0;
        var v1 = 1;
        pos[i * 6] = pp.x;
        pos[i * 6 + 1] = lo;
        pos[i * 6 + 2] = pp.z;
        pos[i * 6 + 3] = pp.x;
        pos[i * 6 + 4] = hi;
        pos[i * 6 + 5] = pp.z;
        uv[i * 4] = d0;
        uv[i * 4 + 1] = v0;
        uv[i * 4 + 2] = d0;
        uv[i * 4 + 3] = v1;
        nor[i * 6] = pp.nx;
        nor[i * 6 + 1] = 0;
        nor[i * 6 + 2] = pp.nz;
        nor[i * 6 + 3] = pp.nx;
        nor[i * 6 + 4] = 0;
        nor[i * 6 + 5] = pp.nz;
        // 墙脚 AO：层内由底到顶提亮，整体下暗上亮
        var h0 = clamp((lo - block.baseY) / Math.max(0.001, tops[i] - block.baseY), 0, 1);
        var h1 = clamp((hi - block.baseY) / Math.max(0.001, tops[i] - block.baseY), 0, 1);
        var a0 = 0.40 + 0.60 * Math.pow(h0, 0.55);
        var a1 = 0.40 + 0.60 * Math.pow(h1, 0.55);
        col[i * 6] = a0;
        col[i * 6 + 1] = a0;
        col[i * 6 + 2] = a0;
        col[i * 6 + 3] = a1;
        col[i * 6 + 4] = a1;
        col[i * 6 + 5] = a1;
      }
      for (i = 0; i < total; i++) {
        var j = (i + 1) % total;
        var bl = i * 2, tl = i * 2 + 1;
        var br = j * 2, tr = j * 2 + 1;
        idx.push(bl, br, tr, bl, tr, tl);
      }
      var geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
      geo.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
      geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
      geo.setIndex(idx);
      var maps = getRockMaps(STRATA[k]);
      var mat = new THREE.MeshStandardMaterial({
        map: maps.map,
        normalMap: maps.normalMap,
        normalScale: new THREE.Vector2(0.55, 0.55),
        vertexColors: true,
        roughness: 0.94,
        metalness: 0.02,
        emissive: 0x08111a,
        emissiveIntensity: 0.32
      });
      var mesh = new THREE.Mesh(geo, mat);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      group.add(mesh);
      layers.push(mesh);
    }

    // 顶部土壤薄带 + 发光边线
    var sPos = [];
    var sIdx = [];
    var rPos = [];
    for (var m = 0; m < total; m++) {
      var q = pts[m];
      var t0 = tops[m] - 0.85;
      var x0 = q.x + q.nx * 0.12;
      var z0 = q.z + q.nz * 0.12;
      sPos.push(x0, t0, z0, x0, tops[m] + 0.02, z0);
      rPos.push(x0 + q.nx * 0.05, tops[m] + 0.06, z0 + q.nz * 0.05);
    }
    for (var s = 0; s < total; s++) {
      var sn = (s + 1) % total;
      sIdx.push(s * 2, sn * 2, sn * 2 + 1, s * 2, sn * 2 + 1, s * 2 + 1);
    }
    var stripGeo = new THREE.BufferGeometry();
    stripGeo.setAttribute("position", new THREE.Float32BufferAttribute(sPos, 3));
    stripGeo.setIndex(sIdx);
    stripGeo.computeVertexNormals();
    var stripMat = new THREE.MeshStandardMaterial({ color: 0x3d2c1f, roughness: 1, metalness: 0 });
    var strip = new THREE.Mesh(stripGeo, stripMat);
    strip.castShadow = true;

    var rimGeo = new THREE.BufferGeometry();
    rimGeo.setAttribute("position", new THREE.Float32BufferAttribute(rPos, 3));
    var rimMat = new THREE.LineBasicMaterial({
      color: C.terrainRim,
      transparent: true,
      opacity: 0.24,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    });
    var rim = new THREE.LineLoop(rimGeo, rimMat);
    return { wall: group, strip: strip, rim: rim, tops: tops, pts: pts, bounds: bounds, layers: layers };
  }

  function buildContours(block, interval, indexEvery) {
    var pos = [];
    var posIndex = [];
    var grid = block.grid;
    var gw = block.gw;
    var gh = block.gh;
    var minE = Math.ceil(block.elevMin / interval) * interval;
    var maxE = block.elevMax;
    function inside(v) { return v > block.elevMin && v < maxE; }
    for (var iy = 0; iy < gh - 1; iy++) {
      for (var ix = 0; ix < gw - 1; ix++) {
        var h00 = grid[iy][ix];
        var h10 = grid[iy][ix + 1];
        var h01 = grid[iy + 1][ix];
        var h11 = grid[iy + 1][ix + 1];
        var lmin = Math.min(h00, h10, h01, h11);
        var lmax = Math.max(h00, h10, h01, h11);
        var start = Math.ceil(lmin / interval) * interval;
        for (var lv = start; lv <= lmax; lv += interval) {
          if (!inside(lv)) continue;
          var hits = [];
          if ((h00 - lv) * (h10 - lv) < 0) hits.push([ix + (lv - h00) / (h10 - h00), iy]);
          if ((h10 - lv) * (h11 - lv) < 0) hits.push([ix + 1, iy + (lv - h10) / (h11 - h10)]);
          if ((h01 - lv) * (h11 - lv) < 0) hits.push([ix + (lv - h01) / (h11 - h01), iy + 1]);
          if ((h00 - lv) * (h01 - lv) < 0) hits.push([ix, iy + (lv - h00) / (h01 - h00)]);
          if (hits.length < 2) continue;
          var arr = (Math.round(lv / interval) % indexEvery === 0) ? posIndex : pos;
          var y = (lv - block.elevMin) * block.hs + 0.12;
          for (var h = 0; h < 2; h++) {
            arr.push(
              -block.W / 2 + (hits[h][0] / (gw - 1)) * block.W,
              y,
              -block.D / 2 + (hits[h][1] / (gh - 1)) * block.D
            );
          }
        }
      }
    }
    var group = new THREE.Group();
    var g1 = new THREE.BufferGeometry();
    g1.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    var m1 = new THREE.LineBasicMaterial({
      color: 0x9fe9ff, transparent: true, opacity: 0.10,
      blending: THREE.AdditiveBlending, depthWrite: false
    });
    group.add(new THREE.LineSegments(g1, m1));
    var g2 = new THREE.BufferGeometry();
    g2.setAttribute("position", new THREE.Float32BufferAttribute(posIndex, 3));
    var m2 = new THREE.LineBasicMaterial({
      color: 0xc8f6ff, transparent: true, opacity: 0.24,
      blending: THREE.AdditiveBlending, depthWrite: false
    });
    group.add(new THREE.LineSegments(g2, m2));
    group.renderOrder = 2;
    return group;
  }

  function buildBase(block) {
    var group = new THREE.Group();
    var planeGeo = new THREE.PlaneGeometry(block.W, block.D);
    var planeMat = new THREE.MeshStandardMaterial({ color: 0x07111a, roughness: 1, metalness: 0, side: THREE.DoubleSide });
    var plane = new THREE.Mesh(planeGeo, planeMat);
    plane.rotation.x = -Math.PI / 2;
    plane.position.y = block.baseY;
    plane.receiveShadow = true;
    group.add(plane);

    // 地块底部的柔和接触阴影
    var scv = document.createElement("canvas");
    scv.width = 256;
    scv.height = 256;
    var sg = scv.getContext("2d");
    var sgr = sg.createRadialGradient(128, 128, 12, 128, 128, 124);
    sgr.addColorStop(0, "rgba(0,0,0,.72)");
    sgr.addColorStop(0.55, "rgba(0,0,0,.36)");
    sgr.addColorStop(1, "rgba(0,0,0,0)");
    sg.fillStyle = sgr;
    sg.fillRect(0, 0, 256, 256);
    var stex = canvasTexture(scv);
    stex.encoding = THREE.LinearEncoding;
    var shadowPlane = new THREE.Mesh(
      new THREE.PlaneGeometry(block.W * 1.55, block.D * 1.55),
      new THREE.MeshBasicMaterial({ map: stex, transparent: true, opacity: 0.85, depthWrite: false, toneMapped: false })
    );
    shadowPlane.rotation.x = -Math.PI / 2;
    shadowPlane.position.y = block.baseY + 0.08;
    group.add(shadowPlane);

    var compassGeo = new THREE.PlaneGeometry(580, 580);
    var compassMat = new THREE.MeshBasicMaterial({
      map: getCompassTexture(),
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      opacity: 0.30,
      toneMapped: false
    });
    var compass = new THREE.Mesh(compassGeo, compassMat);
    compass.rotation.x = -Math.PI / 2;
    compass.position.y = block.baseY - 0.4;
    group.add(compass);
    return group;
  }

  function buildDust(block) {
    var count = 240;
    var pos = new Float32Array(count * 3);
    var seed = 9;
    function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }
    for (var i = 0; i < count; i++) {
      pos[i * 3] = (rnd() - 0.5) * block.W * 1.15;
      pos[i * 3 + 1] = rnd() * (block.maxH + 46) + 2;
      pos[i * 3 + 2] = (rnd() - 0.5) * block.D * 1.15;
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    var mat = new THREE.PointsMaterial({
      color: 0x9fe6ff,
      size: 0.75,
      transparent: true,
      opacity: 0.32,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: true
    });
    var points = new THREE.Points(geo, mat);
    points.renderOrder = 4;
    return points;
  }

  // ---------------------------------------------------------------- 影像叠加层
  function pointInRing(x, y, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1];
      var xj = ring[j][0], yj = ring[j][1];
      if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
    }
    return inside;
  }

  function pointInRings(x, y, rings) {
    if (!rings) return false;
    for (var i = 0; i < rings.length; i++) {
      if (pointInRing(x, y, rings[i])) return true;
    }
    return false;
  }

  function drawRingPath(g, pts) {
    if (!pts.length) return;
    g.beginPath();
    g.moveTo(pts[0][0], pts[0][1]);
    for (var i = 1; i < pts.length; i++) g.lineTo(pts[i][0], pts[i][1]);
    g.closePath();
  }

  function geoJsonRings(geom) {
    if (!geom) return [];
    if (geom.type === "Polygon") return geom.coordinates || [];
    if (geom.type === "MultiPolygon") {
      var out = [];
      (geom.coordinates || []).forEach(function (poly) {
        (poly || []).forEach(function (ring) { out.push(ring); });
      });
      return out;
    }
    return [];
  }

  function buildOverlayTexture(spec, block, terrain) {
    var S = 1024;
    var cv = document.createElement("canvas");
    cv.width = S;
    cv.height = S;
    var g = cv.getContext("2d");
    g.clearRect(0, 0, S, S);
    var tb = terrain.bbox;
    var sb = spec.siteBbox || tb;
    var isEvent = !!spec.event;
    var isP01 = spec.mode === "gap";

    function X(lon) { return (lon - tb[0]) / (tb[2] - tb[0]) * S; }
    function Y(lat) { return (tb[3] - lat) / (tb[3] - tb[1]) * S; }
    function normPt(u, v) {
      return [X(sb[0] + u * (sb[2] - sb[0])), Y(sb[3] - v * (sb[3] - sb[1]))];
    }
    function ringPts(ring) {
      var out = [];
      for (var i = 0; i < ring.length; i++) out.push(normPt(ring[i][0], ring[i][1]));
      return out;
    }
    function drawPatch(p, focus) {
      if (!p.rings) return;
      var fill = focus ? "rgba(88,236,255,0.20)" : "rgba(255,182,52,0.16)";
      var stroke = focus ? "rgba(150,250,255,0.95)" : "rgba(255,202,104,0.95)";
      var glow = focus ? "rgba(90,240,255,0.9)" : "rgba(255,170,40,0.7)";
      g.save();
      g.shadowColor = glow;
      g.shadowBlur = focus ? 12 : 9;
      g.lineWidth = focus ? 2.3 : 1.7;
      g.strokeStyle = stroke;
      g.fillStyle = fill;
      var cx = 0, cy = 0, cn = 0;
      for (var i = 0; i < p.rings.length; i++) {
        var pts = ringPts(p.rings[i]);
        if (!pts.length) continue;
        drawRingPath(g, pts);
        g.fill();
        g.stroke();
        for (var q = 0; q < pts.length; q++) { cx += pts[q][0]; cy += pts[q][1]; cn++; }
      }
      g.restore();
      if (cn) {
        g.save();
        g.shadowColor = glow;
        g.shadowBlur = 7;
        g.fillStyle = focus ? "rgba(190,253,255,0.98)" : "rgba(255,204,104,0.98)";
        g.beginPath();
        g.arc(cx / cn, cy / cn, focus ? 5.6 : 3.6, 0, Math.PI * 2);
        g.fill();
        g.restore();
      }
    }

    // P01：监测空白网格
    if (isP01) {
      g.save();
      g.strokeStyle = "rgba(120,220,240,0.10)";
      g.lineWidth = 1;
      g.setLineDash([7, 9]);
      for (var gx = 1; gx < 8; gx++) {
        g.beginPath();
        g.moveTo((S / 8) * gx, 0);
        g.lineTo((S / 8) * gx, S);
        g.stroke();
        g.beginPath();
        g.moveTo(0, (S / 8) * gx);
        g.lineTo(S, (S / 8) * gx);
        g.stroke();
      }
      g.setLineDash([]);
      g.strokeStyle = "rgba(130,230,250,0.30)";
      g.lineWidth = 1.6;
      var br = 46;
      var pad = 26;
      [[pad, pad, 1, 1], [S - pad, pad, -1, 1], [pad, S - pad, 1, -1], [S - pad, S - pad, -1, -1]].forEach(function (c) {
        g.beginPath();
        g.moveTo(c[0], c[1] + c[3] * br);
        g.lineTo(c[0], c[1]);
        g.lineTo(c[0] + c[2] * br, c[1]);
        g.stroke();
      });
      g.restore();
    }

    // 样区图斑
    if (!isEvent && spec.patches && spec.patches.length) {
      for (var i = 0; i < spec.patches.length; i++) {
        var p = spec.patches[i];
        drawPatch(p, !!state.focusIds[p.id]);
      }
    }

    // 事件视图：焦点光晕 + 虚线搜索环
    if (isEvent && spec.event) {
      var ec = spec.event.center || spec.event.center_lonlat;
      if (ec) {
        var ex = X(ec[0]);
        var ey = Y(ec[1]);
        var grd = g.createRadialGradient(ex, ey, 0, ex, ey, 96);
        grd.addColorStop(0, "rgba(120,244,255,0.20)");
        grd.addColorStop(0.55, "rgba(90,230,255,0.07)");
        grd.addColorStop(1, "rgba(90,230,255,0)");
        g.save();
        g.fillStyle = grd;
        g.beginPath();
        g.arc(ex, ey, 96, 0, Math.PI * 2);
        g.fill();
        g.strokeStyle = "rgba(140,246,255,0.55)";
        g.lineWidth = 1.4;
        g.setLineDash([6, 7]);
        g.beginPath();
        g.arc(ex, ey, 52, 0, Math.PI * 2);
        g.stroke();
        g.setLineDash([]);
        g.restore();
      }
    }

    // 事件视图：只画事件涉及的图斑
    if (isEvent && state.focusPatches.length) {
      for (var f = 0; f < state.focusPatches.length; f++) drawPatch(state.focusPatches[f], true);
    }

    // P07 采石场作业面（真实轮廓）
    if (!isEvent && spec.id === "p07_2023b") {
      g.save();
      g.shadowColor = "rgba(170,245,255,0.85)";
      g.shadowBlur = 14;
      g.strokeStyle = "rgba(236,253,255,0.92)";
      g.lineWidth = 2.2;
      g.fillStyle = "rgba(255,255,255,0.05)";
      var qPts = QUARRY_RING.map(function (uv) { return normPt(uv[0], uv[1]); });
      drawRingPath(g, qPts);
      g.fill();
      g.stroke();
      g.restore();
    }

    // 事件范围（真实 geometry，WGS84）
    if (isEvent && spec.event && spec.event.geometry) {
      var rings = geoJsonRings(spec.event.geometry);
      if (rings.length) {
        g.save();
        g.shadowColor = "rgba(255,190,90,0.9)";
        g.shadowBlur = 14;
        g.fillStyle = "rgba(255,178,52,0.18)";
        g.strokeStyle = "rgba(255,214,150,0.98)";
        g.lineWidth = 2.6;
        for (var r = 0; r < rings.length; r++) {
          var pts2 = rings[r].map(function (pt) { return [X(pt[0]), Y(pt[1])]; });
          if (!pts2.length) continue;
          drawRingPath(g, pts2);
          g.fill();
          g.stroke();
        }
        g.restore();
      }
    }

    var tex = canvasTexture(cv);
    tex.minFilter = THREE.LinearFilter;
    return tex;
  }

  // ---------------------------------------------------------------- 标注
  function makeTag(text, cls, color, anchor, normal) {
    var el = document.createElement("div");
    el.className = "yw-geo-tag " + (cls || "");
    var span = document.createElement("span");
    if (color) {
      var dot = document.createElement("i");
      dot.style.background = color;
      span.appendChild(dot);
    }
    span.appendChild(document.createTextNode(text));
    el.appendChild(span);
    // 先不挂 DOM：等场景真正切换成功后再统一挂载，避免失败场景残留标签
    return { el: el, anchor: anchor.clone(), normal: normal ? normal.clone() : null };
  }

  function eventTagText(e) {
    if (!e) return "事件";
    if (e.domain === "river") return "观测缺口 · 不生成结论";
    var negative = /非采掘|农田|清表|裸化/.test((e.activity_status || "") + " " + (e.title || ""));
    if (negative) return "农田清表 · 非采掘变化";
    return (e.id || "事件") + (e.area_ha != null ? " · " + fmt(e.area_ha) + " ha" : "");
  }

  function buildLabels(block, spec, terrain, built) {
    var labels = [];

    // 地层：只在前壁标三个主层位，避免窄面板下标签互相遮挡（完整五层见下方图例）
    var frontZ = block.D / 2 + 0.55;
    var picks = [[0, -55], [2, 0], [4, 55]];
    for (var p = 0; p < picks.length; p++) {
      var layer = STRATA[picks[p][0]];
      var x = picks[p][1];
      var top = block.heightAt(x, block.D / 2);
      var y = block.baseY + layer.at * (top - block.baseY);
      labels.push(makeTag(layer.label, "is-strata", layer.color,
        new THREE.Vector3(x, y, frontZ), new THREE.Vector3(0, 0, 1)));
    }

    // P07 采石场
    if (spec.key === "site:p07_2023b") {
      var qb = P07_BBOX;
      var qx = qb[0] + 0.905 * (qb[2] - qb[0]);
      var qy = qb[3] - 0.60 * (qb[3] - qb[1]);
      var qw = block.lonlat(qx, qy);
      labels.push(makeTag("采石场作业面 · 2026", "is-quarry", "#eafcff",
        qw.setY(qw.y + 2), null));
    }

    // P01 监测空白
    if (spec.mode === "gap") {
      var cw = block.lonlat((block.bbox[0] + block.bbox[2]) / 2, (block.bbox[1] + block.bbox[3]) / 2);
      labels.push(makeTag("0 图斑 · 监测空白", "is-focus", "#8ff6ff",
        cw.setY(cw.y + 3), null));
    }

    // 事件标注 + 信标
    var focusWorld = null;
    if (spec.event) {
      var e = spec.event;
      var center = e.center_lonlat;
      var eb = e.bbox || e.bbox_wgs84;
      if (!center && eb) {
        center = [(eb[0] + eb[2]) / 2, (eb[1] + eb[3]) / 2];
      }
      if (center) {
        var ground = block.lonlat(center[0], center[1]);
        focusWorld = ground;
        var isNeg = /非采掘|农田|清表|裸化/.test((e.activity_status || "") + " " + (e.title || ""));
        var isGap = e.domain === "river";
        var col = isGap ? "#8ff6ff" : isNeg ? "#8ff6ff" : "#ffcf8a";
        var cls = isGap ? "is-focus" : isNeg ? "is-focus" : "is-event";
        labels.push(makeTag(eventTagText(e), cls, col,
          new THREE.Vector3(ground.x, ground.y + 31, ground.z), null));
        built.beacons.push(makeBeacon(block, ground, isGap ? 0x7fe6ff : isNeg ? 0x7fe6ff : 0xffc46b));
      }
    }

    // 大沽夹河
    if (built.riverMid) {
      labels.push(makeTag("大沽夹河 · OSM 中心线", "is-river", "#7fe6ff",
        new THREE.Vector3(built.riverMid.x, built.riverMid.y + 16, built.riverMid.z), null));
    }
    return labels;
  }

  function makeBeacon(block, ground, color) {
    var group = new THREE.Group();
    var h = 28;
    var rodGeo = new THREE.CylinderGeometry(0.34, 0.34, h, 8, 1, true);
    var rodMat = new THREE.MeshBasicMaterial({
      color: color, transparent: true, opacity: 0.42,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide, toneMapped: false
    });
    var rod = new THREE.Mesh(rodGeo, rodMat);
    rod.position.set(ground.x, ground.y + h / 2, ground.z);
    group.add(rod);

    var dotGeo = new THREE.SphereGeometry(1.45, 14, 10);
    var dotMat = new THREE.MeshBasicMaterial({
      color: color, transparent: true, opacity: 0.95,
      blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false
    });
    var dot = new THREE.Mesh(dotGeo, dotMat);
    dot.position.set(ground.x, ground.y + h, ground.z);
    group.add(dot);

    var ringGeo = new THREE.TorusGeometry(3.4, 0.16, 8, 48);
    var ringMat = new THREE.MeshBasicMaterial({
      color: color, transparent: true, opacity: 0.7,
      blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false
    });
    var ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = Math.PI / 2;
    ring.position.set(ground.x, ground.y + h, ground.z);
    group.add(ring);

    var groundRingGeo = new THREE.TorusGeometry(9.2, 0.14, 8, 72);
    var groundRingMat = new THREE.MeshBasicMaterial({
      color: color, transparent: true, opacity: 0.42,
      blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false
    });
    var groundRing = new THREE.Mesh(groundRingGeo, groundRingMat);
    groundRing.rotation.x = Math.PI / 2;
    groundRing.position.set(ground.x, ground.y + 0.7, ground.z);
    group.add(groundRing);

    return { group: group, dot: dot, ring: ring, groundRing: groundRing, phase: Math.random() * Math.PI * 2 };
  }

  // ---------------------------------------------------------------- 河道
  function clipSegment(lon0, lat0, lon1, lat1, bbox) {
    var dx = lon1 - lon0;
    var dy = lat1 - lat0;
    var t0 = 0;
    var t1 = 1;
    var p = [-dx, dx, -dy, dy];
    var q = [lon0 - bbox[0], bbox[2] - lon0, lat0 - bbox[1], bbox[3] - lat0];
    for (var i = 0; i < 4; i++) {
      if (Math.abs(p[i]) < 1e-12) {
        if (q[i] < 0) return null;
      } else {
        var r = q[i] / p[i];
        if (p[i] < 0) {
          if (r > t1) return null;
          if (r > t0) t0 = r;
        } else {
          if (r < t0) return null;
          if (r < t1) t1 = r;
        }
      }
    }
    return [t0, t1];
  }

  function clipRiver(coords, bbox) {
    var out = [];
    for (var i = 1; i < coords.length; i++) {
      var a = coords[i - 1];
      var b = coords[i];
      var seg = clipSegment(a[1], a[0], b[1], b[0], bbox);
      if (!seg) continue;
      var p0 = [a[1] + (b[1] - a[1]) * seg[0], a[0] + (b[0] - a[0]) * seg[0]];
      var p1 = [a[1] + (b[1] - a[1]) * seg[1], a[0] + (b[0] - a[0]) * seg[1]];
      if (!out.length || Math.hypot(out[out.length - 1][0] - p0[0], out[out.length - 1][1] - p0[1]) > 1e-9) out.push(p0);
      out.push(p1);
    }
    return out;
  }

  function buildRiver(block, coords, spec) {
    if (!coords || coords.length < 2) return null;
    var clipped = clipRiver(coords, block.bbox);
    if (clipped.length < 2) return null;
    var world = clipped.map(function (pt) { return block.lonlat(pt[0], pt[1]); });
    var group = new THREE.Group();
    var corrPos = [];
    var mid = null;
    for (var i = 0; i < world.length; i++) {
      var p = world[i];
      var prev = world[Math.max(0, i - 1)];
      var next = world[Math.min(world.length - 1, i + 1)];
      var tx = next.x - prev.x;
      var tz = next.z - prev.z;
      var tl = Math.hypot(tx, tz) || 1;
      var nx = -tz / tl;
      var nz = tx / tl;
      var wCorr = 20;
      [0, 1].forEach(function (side) {
        var sgn = side === 0 ? -1 : 1;
        var ox = p.x + nx * wCorr * sgn;
        var oz = p.z + nz * wCorr * sgn;
        corrPos.push(ox, block.heightAt(ox, oz) + 0.2, oz);
      });
      if (i === Math.floor(world.length / 2)) mid = p;
    }
    var corrIdx = [];
    for (var j = 0; j < world.length - 1; j++) {
      var a = j * 2;
      var b = a + 1;
      var c = a + 2;
      var d = a + 3;
      corrIdx.push(a, c, b, b, c, d);
    }
    var curvePts = world.map(function (pt) {
      return new THREE.Vector3(pt.x, pt.y + 0.8, pt.z);
    });
    var curve = new THREE.CatmullRomCurve3(curvePts);
    var tubeGeo = new THREE.TubeGeometry(curve, Math.max(48, world.length * 3), 1.5, 8, false);
    var coreMat = new THREE.MeshBasicMaterial({
      map: getFlowTexture(),
      transparent: true,
      opacity: 0.72,
      color: 0x7fe6ff,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false
    });
    var core = new THREE.Mesh(tubeGeo, coreMat);
    core.renderOrder = 4;
    group.add(core);

    var corrGeo = new THREE.BufferGeometry();
    corrGeo.setAttribute("position", new THREE.Float32BufferAttribute(corrPos, 3));
    corrGeo.setIndex(corrIdx);
    var corrMat = new THREE.MeshBasicMaterial({
      color: 0x2f8fa8,
      transparent: true,
      opacity: 0.15,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false
    });
    var corr = new THREE.Mesh(corrGeo, corrMat);
    corr.renderOrder = 2;
    group.add(corr);

    // 缺口虚线环
    var gapCenter = null;
    if (spec.event) {
      var ec = spec.event.center_lonlat || spec.event.center;
      if (ec) gapCenter = block.lonlat(ec[0], ec[1]);
    } else if (spec.mode === "river" && mid) {
      gapCenter = mid;
    }
    if (gapCenter) {
      var ringPts = [];
      var c = gapCenter;
      var R = 20;
      for (var k = 0; k <= 72; k++) {
        var ang = (k / 72) * Math.PI * 2;
        var rx = c.x + Math.cos(ang) * R;
        var rz = c.z + Math.sin(ang) * R;
        ringPts.push(new THREE.Vector3(rx, block.heightAt(rx, rz) + 0.7, rz));
      }
      var ringGeo = new THREE.BufferGeometry().setFromPoints(ringPts);
      var ringMat = new THREE.LineDashedMaterial({
        color: 0xffc46b,
        dashSize: 2.2,
        gapSize: 1.8,
        transparent: true,
        opacity: 0.9,
        depthWrite: false
      });
      var ring = new THREE.Line(ringGeo, ringMat);
      ring.computeLineDistances();
      group.add(ring);
    }
    return { group: group, core: core, mid: mid };
  }

  // ---------------------------------------------------------------- 场景
  function buildScene(spec, epoch, onProgress) {
    return loadTerrain(spec.terrain).then(function (terrain) {
      if (epoch !== state.epoch) throw new Error("aborted");
      if (onProgress) onProgress("正在拼接真实影像…");
      var block = makeBlock(terrain, spec);
      var focusIds = {};
      var focusPatches = [];
      if (spec.event && spec.event.focus_patches && spec.patches) {
        spec.event.focus_patches.forEach(function (id) { focusIds[id] = true; });
        focusPatches = spec.patches.filter(function (p) { return focusIds[p.id]; });
      }
      state.focusIds = focusIds;
      state.focusPatches = focusPatches;
      var riverP = (spec.mode === "river" || spec.key === "dagujia" ||
        (spec.event && spec.event.domain === "river")) ? loadRiverCenterline() : Promise.resolve(null);
      return Promise.all([loadImagery(spec, terrain), riverP]).then(function (res) {
        if (epoch !== state.epoch) throw new Error("aborted");
        var im = res[0];
        var riverCoords = res[1];
        var root = new THREE.Group();
        var built = {
          root: root,
          block: block,
          spec: spec,
          terrain: terrain,
          labels: [],
          beacons: [],
          riverMid: null,
          textures: [im.texA, im.texB, im.texC],
          source: im.source,
          dates: im.dates
        };

        var tm = buildTerrainMesh(block, terrain, im.texA, im.texB, im.rect, im.texC);
        root.add(tm.mesh);

        var overlayTex = buildOverlayTexture(spec, block, terrain);
        built.textures.push(overlayTex);
        compositeOverlay(im, overlayTex);

        var walls = buildWalls(block);
        root.add(walls.wall);
        root.add(walls.strip);
        root.add(walls.rim);

        var interval = 10;
        var indexEvery = 5;
        if (spec.id === "p01") { interval = 2; indexEvery = 5; }
        else if (spec.terrain === "p07_site" || spec.terrain === "dagujia") { interval = 10; indexEvery = 5; }
        else { interval = 10; indexEvery = 5; }
        root.add(buildContours(block, interval, indexEvery));
        root.add(buildBase(block));
        var dust = buildDust(block);
        root.add(dust);
        built.dust = dust;

        if (riverCoords) {
          var rv = buildRiver(block, riverCoords, spec);
          if (rv) {
            root.add(rv.group);
            built.river = rv.group;
            built.riverMid = rv.mid;
          }
        }

        built.labels = buildLabels(block, spec, terrain, built);
        built.beacons.forEach(function (b) { root.add(b.group); });

        return {
          root: root,
          block: block,
          spec: spec,
          terrain: terrain,
          labels: built.labels,
          beacons: built.beacons,
          textures: built.textures,
          surfaceU: tm.uniforms,
          surfaceMat: tm.mat,
          terrainMesh: tm.mesh,
          dust: dust,
          river: built.river || null,
          riverMid: built.riverMid,
          source: built.source,
          dates: built.dates
        };
      });
    });
  }

  // ---------------------------------------------------------------- 相机
  function fitDistance(block, spec, aspect, el, az) {
    var vFov = state.camera.fov * Math.PI / 180;
    var hFov = 2 * Math.atan(Math.tan(vFov / 2) * aspect);
    var k = 1;
    var w = block.W * k;
    var d = block.D * k;
    az = az == null ? 0.55 : az;
    var screenW = (w * Math.abs(Math.cos(az)) + d * Math.abs(Math.sin(az))) / 2;
    var vertical = block.maxH - block.baseY;
    var screenH = (d * Math.sin(el) + vertical * Math.cos(el)) / 2;
    var dh = screenW / Math.max(0.05, Math.tan(hFov / 2));
    var dv = screenH / Math.max(0.05, Math.tan(vFov / 2));
    return Math.max(dh, dv) * 1.22 + 12;
  }

  function focusOf(block, spec) {
    return new THREE.Vector3(0, block.centerY, 0);
  }

  function cameraPosFor(el, az, dist, target) {
    return new THREE.Vector3(
      target.x + dist * Math.cos(el) * Math.sin(az),
      target.y + dist * Math.sin(el),
      target.z + dist * Math.cos(el) * Math.cos(az)
    );
  }

  function currentAz() {
    if (!state.camera || !state.controls) return 0.55;
    var t = state.controls.target;
    return Math.atan2(state.camera.position.x - t.x, state.camera.position.z - t.z);
  }

  function beginPreset(toPos, toTarget, dur) {
    if (!state.controls) return;
    state.preset = {
      from: state.camera.position.clone(),
      to: toPos.clone(),
      fromT: state.controls.target.clone(),
      toT: toTarget.clone(),
      t: 0,
      dur: dur || 1.15
    };
    state.controls.enabled = false;
    state.controls.autoRotate = false;
  }

  function updatePreset(dt) {
    var a = state.preset;
    if (!a) return;
    a.t += dt;
    var k = clamp(a.t / a.dur, 0, 1);
    var e = k * k * (3 - 2 * k);
    state.camera.position.lerpVectors(a.from, a.to, e);
    state.controls.target.lerpVectors(a.fromT, a.toT, e);
    state.camera.lookAt(state.controls.target);
    if (k >= 1) {
      state.preset = null;
      state.controls.enabled = true;
      state.controls.autoRotate = state.spin;
      state.controls.update();
    }
  }

  function setPreset(name) {
    if (!state.block || !state.controls) return;
    var el = name === "top" ? 1.30 : name === "side" ? 0.18 : 0.47;
    var az = name === "top" ? currentAz() : name === "side" ? currentAz() - 0.7 : 0.55;
    var dist = fitDistance(state.block, state.block.spec, cameraAspect(), el, az) * (name === "top" ? 1.02 : 0.98);
    var target = focusOf(state.block, state.block.spec);
    beginPreset(cameraPosFor(el, az, dist, target), target, name === "top" ? 1.25 : 1.1);
  }

  function cameraAspect() {
    return state.camera ? state.camera.aspect : 1.6;
  }

  // ---------------------------------------------------------------- DOM
  function setLoading(text) {
    state.loading = true;
    if (!state.stage) return;
    state.stage.classList.add("is-loading");
    if (chrome.loading) chrome.loading.textContent = text || "正在载入…";
  }

  function clearLoading() {
    state.loading = false;
    if (state.stage) state.stage.classList.remove("is-loading");
  }

  function clearError() {
    if (!state.stage) return;
    var old = state.stage.querySelectorAll(".yw-geo-fallback");
    for (var i = 0; i < old.length; i++) old[i].remove();
    state.stage.classList.remove("is-error");
  }

  function showError(msg) {
    clearLoading();
    if (!state.stage) return;
    state.stage.classList.add("is-error");
    var el = document.createElement("div");
    el.className = "yw-geo-fallback";
    el.textContent = msg;
    state.stage.appendChild(el);
  }

  function ensureChrome(stage) {
    chrome.hud = stage.querySelector("[data-geo-hud]");
    if (!chrome.hud) {
      var hudBox = stage.querySelector(".yw-geo-hud");
      if (hudBox) {
        chrome.hud = document.createElement("span");
        chrome.hud.setAttribute("data-geo-hud", "");
        chrome.hud.textContent = "正在载入…";
        hudBox.appendChild(chrome.hud);
      }
    }
    chrome.sites = stage.querySelector("[data-geo-sites]");
    if (!chrome.sites) {
      chrome.sites = document.createElement("div");
      chrome.sites.className = "yw-geo-sites";
      stage.appendChild(chrome.sites);
    }
    chrome.labels = stage.querySelector("[data-geo-labels]");
    if (!chrome.labels) {
      chrome.labels = document.createElement("div");
      chrome.labels.className = "yw-geo-labels";
      chrome.labels.setAttribute("data-geo-labels", "");
      stage.appendChild(chrome.labels);
    }
    chrome.imagery = stage.querySelector("[data-geo-imagery]");
    if (!chrome.imagery) {
      chrome.imagery = document.createElement("div");
      chrome.imagery.className = "yw-geo-imagery";
      chrome.imagery.setAttribute("data-geo-imagery", "");
      stage.appendChild(chrome.imagery);
    }
    chrome.tip = stage.querySelector("[data-geo-tip]");
    if (!chrome.tip) {
      chrome.tip = document.createElement("div");
      chrome.tip.className = "yw-geo-tip";
      chrome.tip.setAttribute("data-geo-tip", "");
      stage.appendChild(chrome.tip);
    }
    chrome.card = stage.querySelector("[data-geo-card]");
    if (!chrome.card) {
      chrome.card = document.createElement("div");
      chrome.card.className = "yw-geo-card";
      chrome.card.setAttribute("data-geo-card", "");
      stage.appendChild(chrome.card);
    }
    chrome.compass = stage.querySelector("[data-geo-compass]");
    if (!chrome.compass) {
      chrome.compass = document.createElement("div");
      chrome.compass.className = "yw-geo-compass";
      chrome.compass.setAttribute("data-geo-compass", "");
      chrome.compass.innerHTML = '<div class="yw-geo-compass-dial"><span class="yw-geo-compass-n">N</span><span class="yw-geo-compass-e">E</span><span class="yw-geo-compass-s">S</span><span class="yw-geo-compass-w">W</span><i class="yw-geo-compass-needle"></i></div>';
      stage.appendChild(chrome.compass);
    }
    chrome.compassDial = chrome.compass.querySelector(".yw-geo-compass-dial");
    var oldNorth = stage.querySelector(".yw-geo-north");
    if (oldNorth) oldNorth.style.display = "none";
    chrome.loading = stage.querySelector(".yw-geo-loading");
    chrome.hint = stage.querySelector(".yw-geo-hint");
    chrome.legend = document.querySelector("[data-geo-legend]");
    if (!chrome.legend) {
      var block = stage.closest(".yw-geo");
      chrome.legend = block ? block.querySelector(".yw-geo-legend") : null;
    }
  }

  function renderChips() {
    if (!chrome.sites) return;
    chrome.sites.innerHTML = "";
    state.specs.forEach(function (s) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "yw-geo-site" + (s.key === state.activeKey ? " is-on" : "");
      b.textContent = s.label;
      b.title = s.title || s.label;
      b.addEventListener("click", function () { switchRegion(s.key); });
      chrome.sites.appendChild(b);
    });
  }

  function renderImagery(spec) {
    if (!chrome.imagery) return;
    chrome.imagery.innerHTML = "";
    var hasPair = !!(spec.imagery && spec.imagery.before);
    var hasBase = !!spec.basemap;
    if (!hasPair && !hasBase) {
      var span0 = document.createElement("span");
      span0.className = "yw-geo-src";
      span0.textContent = "程序化底纹";
      chrome.imagery.appendChild(span0);
      return;
    }
    if (!hasPair) {
      var span1 = document.createElement("span");
      span1.className = "yw-geo-src";
      span1.textContent = "高清正射 · Esri";
      chrome.imagery.appendChild(span1);
      return;
    }
    var dates = spec.imagery.dates || {};
    var options = [];
    if (hasBase) options.push({ mode: "base", label: "高清", title: "高清卫星正射底图（Esri）" });
    options.push({ mode: "before", label: month(dates.base) || "早期", title: "早期 Sentinel-2 影像" });
    options.push({ mode: "after", label: month(dates.now) || "最新", title: "最新 Sentinel-2 影像" });
    options.forEach(function (o) {
      var b = document.createElement("button");
      b.type = "button";
      b.dataset.geoImg = o.mode;
      b.className = "yw-geo-imgbtn" + (state.imgMode === o.mode ? " is-on" : "");
      b.textContent = o.label;
      b.title = o.title;
      b.addEventListener("click", function () { pickImagery(o.mode); });
      chrome.imagery.appendChild(b);
    });
  }

  function pickImagery(mode) {
    if (state.imgMode === mode) return;
    state.imgMode = mode;
    var target = mode === "before" ? 0 : 1;
    var baseTarget = mode === "base" ? 1 : 0;
    state.mixTarget = target;
    state.baseTarget = baseTarget;
    state.mixAnim = {
      from: state.mixValue, to: target,
      fromBase: state.baseValue, toBase: baseTarget,
      t: 0, dur: 1.05
    };
    if (chrome.imagery) {
      var btns = chrome.imagery.querySelectorAll("[data-geo-img]");
      for (var i = 0; i < btns.length; i++) {
        btns[i].classList.toggle("is-on", btns[i].dataset.geoImg === mode);
      }
    }
  }

  function legendItems(spec) {
    var out = [];
    for (var i = STRATA.length - 1; i >= 0; i--) {
      out.push('<span><i style="background:' + STRATA[i].color + '"></i>' + STRATA[i].label + "</span>");
    }
    if (spec.mode === "site" || spec.id === "p07_2023b") {
      out.push('<span><i style="background:#ffb020"></i>变化图斑（43）</span>');
      out.push('<span><i style="background:#eafcff"></i>采石场作业面</span>');
    } else if (spec.mode === "gap") {
      out.push('<span><i style="background:#5ad9ee"></i>监测空白</span>');
    } else if (spec.mode === "river" || spec.id === "dagujia") {
      out.push('<span><i style="background:#7fe6ff"></i>河道中心线</span>');
      out.push('<span><i style="background:#ffc46b"></i>观测缺口</span>');
    } else if (spec.event) {
      out.push('<span><i style="background:#ffc46b"></i>事件图斑</span>');
      var fl = spec.event.focus_patches || [];
      if (fl.length) out.push('<span><i style="background:#8ff6ff"></i>关联图斑</span>');
    }
    return out.join("");
  }

  function hudText(spec) {
    if (spec.event) {
      if (spec.event.domain === "river") return "大沽夹河 · 观测缺口";
      var area = spec.event.area_ha != null ? fmt(spec.event.area_ha) + " ha" : "局部";
      return spec.event.id + " · " + area;
    }
    if (spec.key === "site:p01") return "P01 样区 · 0 图斑 · 监测空白";
    if (spec.key === "site:p07_2023b") {
      var n = spec.patches.length || 43;
      return "P07 样区 · " + n + " 图斑 · 22.4 ha";
    }
    if (spec.key === "dagujia") return "大沽夹河 · 河谷";
    return spec.label;
  }

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function cardHtml(spec) {
    var b = state.block;
    var bbox = (b && b.bbox) || spec.siteBbox || P07_BBOX;
    var elev = b ? Math.round(b.elevMin) + "–" + Math.round(b.elevMax) + " m" : "—";
    var kind, area = "";
    if (spec.mode === "site" || spec.id === "p07_2023b") {
      kind = "跨三年变化";
      area = " · 43 图斑 · 22.4 ha";
    } else if (spec.event) {
      kind = spec.event.activity_status || spec.event.title || "监测事件";
      if (spec.event.area_ha != null) area = ' · ' + fmt(spec.event.area_ha) + " ha";
    } else if (spec.mode === "gap") {
      kind = "监测空白";
    } else if (spec.mode === "river") {
      kind = "河谷 · 观测缺口";
    } else {
      kind = "样区";
    }
    var src = state.source === "hybrid" ? "Esri z16 + Sentinel-2"
      : state.source === "sentinel" ? "Sentinel-2 双期"
      : state.source === "esri" ? "Esri z16 正射"
      : "程序化底纹";
    var exag = (spec.exag || 1.8).toFixed(1);
    return '<div class="yw-geo-card-h"><b>' + esc(spec.label) + '</b><em>' + esc(kind) + area + '</em></div>' +
      '<div class="yw-geo-card-r"><span>经度</span><b>' + bbox[0].toFixed(3) + '–' + bbox[2].toFixed(3) + '°E</b></div>' +
      '<div class="yw-geo-card-r"><span>纬度</span><b>' + bbox[1].toFixed(3) + '–' + bbox[3].toFixed(3) + '°N</b></div>' +
      '<div class="yw-geo-card-r"><span>高程</span><b>' + elev + '</b></div>' +
      '<div class="yw-geo-card-r"><span>影像</span><b>' + src + '</b></div>' +
      '<div class="yw-geo-card-r"><span>垂向</span><b>×' + exag + ' · DEM z15</b></div>';
  }

  function updateCompass() {
    if (!chrome.compassDial || !state.camera) return;
    var t = state.controls ? state.controls.target : new THREE.Vector3(0, 0, 0);
    var az = Math.atan2(state.camera.position.x - t.x, state.camera.position.z - t.z);
    chrome.compassDial.style.transform = "rotate(" + az.toFixed(3) + "rad)";
  }

  function applyChrome(spec) {
    if (chrome.hud) chrome.hud.textContent = hudText(spec);
    if (chrome.hint) chrome.hint.textContent = "拖拽旋转 · 滚轮缩放 · 垂向 ×" + (spec.exag || 3).toFixed(1);
    if (chrome.card) chrome.card.innerHTML = cardHtml(spec);
    renderChips();
    renderImagery(spec);
    if (chrome.legend) chrome.legend.innerHTML = legendItems(spec);
    updateCompass();
  }

  // ---------------------------------------------------------------- 渲染/标签更新
  function updateLabels() {
    if (!state.stage || !state.camera) return;
    var w = state.stage.clientWidth;
    var h = state.stage.clientHeight;
    for (var i = 0; i < state.labels.length; i++) {
      var item = state.labels[i];
      var el = item.el;
      if (item.normal) {
        var dir = item.anchor.clone().sub(state.camera.position);
        if (dir.dot(item.normal) >= 0) {
          el.classList.remove("is-on");
          continue;
        }
      }
      var p = item.anchor.clone().project(state.camera);
      if (p.z > 1) {
        el.classList.remove("is-on");
        continue;
      }
      var x = (p.x * 0.5 + 0.5) * w;
      var y = (-p.y * 0.5 + 0.5) * h;
      if (x < -30 || x > w + 30 || y < 14 || y > h - 12) {
        el.classList.remove("is-on");
        continue;
      }
      x = clamp(x, 42, w - 42);
      y = clamp(y, 78, h - 34);
      el.style.transform = "translate3d(" + x.toFixed(1) + "px," + y.toFixed(1) + "px,0) translate(-50%,-100%)";
      el.classList.add("is-on");
    }
  }

  function updateBeacons(t) {
    for (var i = 0; i < state.beacons.length; i++) {
      var b = state.beacons[i];
      var pulse = 0.5 + 0.5 * Math.sin(t * 2.3 + b.phase);
      b.dot.scale.setScalar(0.82 + pulse * 0.55);
      b.ring.scale.setScalar(0.9 + pulse * 0.5);
      b.ring.material.opacity = 0.3 + pulse * 0.45;
      if (b.groundRing) {
        b.groundRing.scale.setScalar(0.92 + pulse * 0.16);
        b.groundRing.material.opacity = 0.22 + pulse * 0.34;
      }
    }
  }

  function updateDust(dt) {
    if (!state.dust) return;
    var attr = state.dust.geometry.attributes.position;
    var arr = attr.array;
    var maxY = state.block ? state.block.maxH + 42 : 60;
    for (var i = 0; i < arr.length; i += 3) {
      arr[i + 1] += dt * 1.4;
      if (arr[i + 1] > maxY) arr[i + 1] = 1;
    }
    attr.needsUpdate = true;
  }

  function animate() {
    state.raf = requestAnimationFrame(animate);
    if (!state.renderer || !state.scene || !state.camera) return;
    var dt = Math.min(0.05, state.clock.getDelta());
    if (!state.visible) return;
    var t = performance.now() / 1000;

    if (state.controls) {
      if (state.preset) updatePreset(dt);
      else state.controls.update();
    }

    if (state.mixAnim && state.surfaceU) {
      var a = state.mixAnim;
      a.t += dt;
      var kk = clamp(a.t / a.dur, 0, 1);
      var e = kk * kk * (3 - 2 * kk);
      state.mixValue = a.from + (a.to - a.from) * e;
      state.baseValue = a.fromBase + (a.toBase - a.fromBase) * e;
      state.surfaceU.uMix.value = state.mixValue;
      state.surfaceU.uBase.value = state.baseValue;
      state.surfaceU.uFront.value = -0.08 + e * 1.16;
      state.surfaceU.uEdge.value = 0.045 * Math.sin(Math.PI * kk);
      if (kk >= 1) {
        state.mixAnim = null;
        state.surfaceU.uEdge.value = 0;
        state.surfaceU.uFront.value = -1;
      }
    }

    if (state.river && state.river.children && state.river.children[0] && state.river.children[0].material.map) {
      var map = state.river.children[0].material.map;
      map.offset.x = (t * 0.035) % 1;
    }
    updateBeacons(t);
    updateDust(dt);
    state.renderer.render(state.scene, state.camera);
    updateLabels();
    updateCompass();
    state.frame++;
  }

  // ---------------------------------------------------------------- 交互
  function bindControls(stage) {
    var canvas = state.canvas;
    if (!canvas) return;

    function ndc(e) {
      var rect = stage.getBoundingClientRect();
      return {
        x: ((e.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1,
        y: -((e.clientY - rect.top) / Math.max(1, rect.height)) * 2 + 1,
        px: e.clientX - rect.left,
        py: e.clientY - rect.top
      };
    }

    function hideTip() {
      if (chrome.tip) chrome.tip.classList.remove("is-on");
    }

    canvas.addEventListener("pointermove", function (e) {
      if (!chrome.tip || !state.terrainMesh) return;
      var p = ndc(e);
      state.pointer = { x: p.x, y: p.y, px: p.px, py: p.py };
      if (state.hoverPending) return;
      state.hoverPending = true;
      requestAnimationFrame(function () {
        state.hoverPending = false;
        if (!state.pointer) return;
        hoverAt(state.pointer);
      });
    });

    canvas.addEventListener("pointerleave", function () {
      state.pointer = null;
      hideTip();
    });

    canvas.addEventListener("dblclick", function () { setPreset("oblique"); });
  }

  function hoverAt(p) {
    if (!state.terrainMesh || !chrome.tip) return;
    var ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(p.x, p.y), state.camera);
    var hit = ray.intersectObject(state.terrainMesh, false)[0];
    if (!hit || !hit.uv) {
      chrome.tip.classList.remove("is-on");
      return;
    }
    var u = hit.uv.x;
    var vNorm = 1 - hit.uv.y;
    var block = state.block;
    var spec = block.spec;
    // 事件窗口的地形 UV 是样区影像里的子矩形，先换算回样区归一化坐标再查图斑
    var specBox = spec.siteBbox || block.bbox;
    if (specBox !== block.bbox) {
      var lon = block.bbox[0] + u * (block.bbox[2] - block.bbox[0]);
      var lat = block.bbox[3] - vNorm * (block.bbox[3] - block.bbox[1]);
      u = (lon - specBox[0]) / Math.max(1e-9, specBox[2] - specBox[0]);
      vNorm = (specBox[3] - lat) / Math.max(1e-9, specBox[3] - specBox[1]);
    }
    var label = null;
    if (spec.mode === "event" || spec.mode === "gap") {
      if (spec.event && state.focusPatches.length) {
        for (var i = 0; i < state.focusPatches.length; i++) {
          var fp = state.focusPatches[i];
          if (pointInRings(u, vNorm, fp.rings)) {
            label = fp.id + " · " + fmt(fp.area_ha) + " ha · 候选图斑";
            break;
          }
        }
      }
    } else if (spec.patches && spec.patches.length) {
      for (var j = 0; j < spec.patches.length; j++) {
        var pp = spec.patches[j];
        if (pointInRings(u, vNorm, pp.rings)) {
          label = pp.id + " · " + fmt(pp.area_ha) + " ha · 候选图斑";
          break;
        }
      }
    }
    if (label) {
      chrome.tip.textContent = label;
      chrome.tip.classList.add("is-on");
      var w = state.stage.clientWidth;
      chrome.tip.style.left = clamp(p.px + 12, 6, Math.max(6, w - 150)) + "px";
      chrome.tip.style.top = clamp(p.py - 10, 30, state.stage.clientHeight - 30) + "px";
    } else {
      chrome.tip.classList.remove("is-on");
    }
  }

  function resize() {
    if (!state.renderer || !state.stage || !state.camera) return;
    var w = state.stage.clientWidth;
    var h = state.stage.clientHeight;
    if (!w || !h) return;
    state.renderer.setSize(w, h, false);
    state.camera.aspect = w / h;
    state.camera.updateProjectionMatrix();
    if (state.block) {
      state.fit = fitDistance(state.block, state.block.spec, state.camera.aspect, 0.47, 0.55);
    }
    if (state.controls) state.controls.update();
  }

  // ---------------------------------------------------------------- 规格列表
  function buildSpecList() {
    var list = [];
    if (state.event) list.push({ key: "event", label: "本事件", title: state.event.id || "当前事件" });
    var p07 = siteSpec(getSite("p07_2023b"));
    var p01 = siteSpec(getSite("p01"));
    if (p07) list.push({ key: p07.key, label: "P07", title: p07.label + " · 43 图斑" });
    else list.push({ key: "site:p07_2023b", label: "P07", title: "P07 样区" });
    if (p01) list.push({ key: p01.key, label: "P01", title: p01.label + " · 监测空白" });
    else list.push({ key: "site:p01", label: "P01", title: "P01 样区" });
    if (!state.event || state.event.domain !== "river") {
      list.push({ key: "dagujia", label: "大沽夹河", title: "大沽夹河试点段 · OSM 中心线" });
    }
    state.specs = list;
  }

  function specForKey(key) {
    if (key === "event") return eventSpec(state.event);
    if (key === "dagujia") return riverSpec();
    if (key === "site:p07_2023b") return siteSpec(getSite("p07_2023b"));
    if (key === "site:p01") return siteSpec(getSite("p01"));
    return null;
  }

  // ---------------------------------------------------------------- 切换地区
  function switchRegion(key, initial) {
    if (!state.renderer) return Promise.resolve();
    if (!initial && key === state.activeKey && state.ready) return Promise.resolve();
    var spec = specForKey(key);
    if (!spec) {
      showError("该地区暂无可用的三维底模");
      return Promise.resolve();
    }
    var epoch = ++state.epoch;
    clearError();
    setLoading(initial ? "正在载入样区数据…" : "正在切换地区…");
    return buildScene(spec, epoch, function (text) {
      if (epoch === state.epoch) setLoading(text);
    }).then(function (built) {
      if (epoch !== state.epoch) return;
      swapScene(built, initial);
    }).catch(function (err) {
      if (epoch !== state.epoch) return;
      if (err && err.message === "aborted") return;
      console.warn("[geo3d] 场景构建失败", err);
      showError("三维底模加载失败：" + ((err && err.message) || "未知错误"));
    });
  }

  function swapScene(built, initial) {
    // 释放旧场景
    if (state.root) {
      state.scene.remove(state.root);
      disposeObject(state.root);
    }
    state.sceneTextures.forEach(function (t) { if (t) t.dispose(); });
    state.sceneTextures = [];
    if (chrome.labels) chrome.labels.innerHTML = "";
    built.labels.forEach(function (l) { if (chrome.labels) chrome.labels.appendChild(l.el); });

    state.root = built.root;
    state.block = built.block;
    state.surfaceU = built.surfaceU;
    state.terrainMesh = built.terrainMesh;
    state.labels = built.labels;
    state.beacons = built.beacons;
    state.patches = built.spec.patches || [];
    state.dust = built.dust;
    state.river = built.river;
    state.sceneTextures = [];
    var seen = [];
    built.textures.forEach(function (t) {
      if (t && seen.indexOf(t) < 0) { seen.push(t); state.sceneTextures.push(t); }
    });
    state.scene.add(built.root);
    state.activeKey = built.spec.key;
    state.ready = true;
    state.frame = 0;

    // 相机：先按当前容器尺寸更新投影，再按新地块计算取景距离
    if (state.stage) {
      var vw = state.stage.clientWidth;
      var vh = state.stage.clientHeight;
      if (vw && vh) {
        state.renderer.setSize(vw, vh, false);
        state.camera.aspect = vw / vh;
        state.camera.updateProjectionMatrix();
      }
    }
    var aspect = cameraAspect();
    state.fit = fitDistance(built.block, built.spec, aspect, 0.47, 0.55);
    var target = focusOf(built.block, built.spec);
    if (state.controls) {
      state.controls.target.copy(target);
      state.camera.position.copy(cameraPosFor(0.36, 0.42, state.fit * 1.22, target));
      state.controls.update();
      beginPreset(cameraPosFor(0.47, 0.55, state.fit, target), target, initial ? 1.35 : 1.05);
    } else {
      state.camera.position.copy(cameraPosFor(0.47, 0.55, state.fit, target));
      state.camera.lookAt(target);
    }

    state.source = built.source || "";
    state.dates = built.dates || {};
    var hasBase = built.source === "hybrid";
    if (hasBase) {
      state.imgMode = "base";
      state.mixValue = 1;
      state.baseValue = 0;
      state.mixTarget = 1;
      state.baseTarget = 1;
      state.mixAnim = { from: 1, to: 1, fromBase: 0, toBase: 1, t: 0, dur: initial ? 1.8 : 0.9 };
    } else if (built.source === "sentinel") {
      state.imgMode = initial ? "after" : (state.imgMode === "before" ? "before" : "after");
      state.baseValue = 0;
      state.baseTarget = 0;
      state.mixValue = state.imgMode === "before" ? 0 : 1;
      state.mixTarget = state.mixValue;
      state.mixAnim = initial
        ? { from: 0, to: 1, fromBase: 0, toBase: 0, t: 0, dur: 1.7 }
        : { from: state.mixValue, to: state.mixValue, fromBase: 0, toBase: 0, t: 0, dur: 0.2 };
      if (initial) { state.mixValue = 0; state.imgMode = "after"; }
    } else {
      state.imgMode = "base";
      state.mixValue = 1;
      state.baseValue = 0;
      state.baseTarget = 0;
      state.mixTarget = 1;
      state.mixAnim = null;
    }
    if (state.surfaceU) {
      state.surfaceU.uMix.value = state.mixValue;
      state.surfaceU.uBase.value = state.baseValue;
      state.surfaceU.uFront.value = -1;
      state.surfaceU.uEdge.value = 0;
    }

    clearLoading();
    applyChrome(built.spec);
    if (state.stage) state.stage.classList.add("is-ready");
    if (state.controls) state.controls.update();
    if (!state.raf) animate();
  }

  function disposeObject(root) {
    root.traverse(function (o) {
      if (o.geometry) o.geometry.dispose();
      if (o.material) {
        var mats = Array.isArray(o.material) ? o.material : [o.material];
        mats.forEach(function (m) {
          if (m && m.dispose) m.dispose();
        });
      }
    });
  }

  // ---------------------------------------------------------------- 生命周期
  function buildSky() {
    var uniforms = {
      topColor: { value: new THREE.Color(0x071a2a) },
      horizonColor: { value: new THREE.Color(0x2c5a6d) },
      bottomColor: { value: new THREE.Color(0x04090e) },
      sunColor: { value: new THREE.Color(0xffd9a6) },
      sunDir: { value: new THREE.Vector3(-0.60, 0.38, 0.70).normalize() }
    };
    var mat = new THREE.ShaderMaterial({
      uniforms: uniforms,
      side: THREE.BackSide,
      depthWrite: false,
      fog: false,
      toneMapped: false,
      vertexShader: "varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: [
        "varying vec3 vDir;",
        "uniform vec3 topColor;",
        "uniform vec3 horizonColor;",
        "uniform vec3 bottomColor;",
        "uniform vec3 sunColor;",
        "uniform vec3 sunDir;",
        "void main(){",
        "  vec3 d = normalize(vDir);",
        "  float h = d.y;",
        "  vec3 col = mix(bottomColor, horizonColor, smoothstep(-0.22, 0.16, h));",
        "  col = mix(col, topColor, smoothstep(0.05, 0.66, h));",
        "  float sd = max(dot(d, normalize(sunDir)), 0.0);",
        "  col += sunColor * (pow(sd, 9.0) * 0.55 + pow(sd, 2.0) * 0.10);",
        "  gl_FragColor = vec4(col, 1.0);",
        "}"
      ].join("\n")
    });
    var mesh = new THREE.Mesh(new THREE.SphereGeometry(1100, 32, 20), mat);
    mesh.frustumCulled = false;
    return mesh;
  }

  function initRenderer(stage) {
    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    } catch (err) {
      showError("当前浏览器无法创建 WebGL 上下文，三维面板已降级。");
      return false;
    }
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setClearColor(0x000000, 0);
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.06;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.className = "yw-geo-canvas";
    stage.insertBefore(renderer.domElement, stage.firstChild);
    state.renderer = renderer;
    state.canvas = renderer.domElement;
    state.stage = stage;

    var scene = new THREE.Scene();
    scene.fog = new THREE.Fog(0x0a1a26, 420, 1050);
    scene.add(buildSky());
    state.scene = scene;
    state.camera = new THREE.PerspectiveCamera(32, 1.6, 1, 2400);

    var hemi = new THREE.HemisphereLight(0x9fd6ee, 0x1a2228, 0.55);
    scene.add(hemi);
    var amb = new THREE.AmbientLight(0x2a3a44, 0.32);
    scene.add(amb);
    var sun = new THREE.DirectionalLight(0xffe1bd, 2.0);
    sun.position.set(-180, 140, 190);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = -220;
    sun.shadow.camera.right = 220;
    sun.shadow.camera.top = 220;
    sun.shadow.camera.bottom = -220;
    sun.shadow.camera.near = 10;
    sun.shadow.camera.far = 900;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.02;
    scene.add(sun);
    scene.add(sun.target);
    var rim = new THREE.DirectionalLight(0x4cc9ff, 0.55);
    rim.position.set(160, 90, -190);
    scene.add(rim);

    if (THREE.OrbitControls) {
      var controls = new THREE.OrbitControls(state.camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.07;
      controls.enablePan = false;
      controls.rotateSpeed = 0.7;
      controls.zoomSpeed = 0.8;
      controls.minPolarAngle = 0.16;
      controls.maxPolarAngle = 1.46;
      controls.minDistance = 70;
      controls.maxDistance = 900;
      controls.autoRotateSpeed = 0.65;
      controls.target.set(0, 0, 0);
      controls.addEventListener("start", function () { state.preset = null; });
      controls.update();
      state.controls = controls;
    } else {
      console.warn("[geo3d] OrbitControls 未加载，交互降级。");
    }

    resize();
    return true;
  }

  function bindObservers(stage) {
    state.ro = null;
    state.io = null;
    if (window.ResizeObserver) {
      state.ro = new ResizeObserver(function () { resize(); });
      state.ro.observe(stage);
    }
    if (window.IntersectionObserver) {
      state.io = new IntersectionObserver(function (entries) {
        state.visible = entries[0] ? entries[0].isIntersecting : true;
      }, { threshold: 0.02 });
      state.io.observe(stage);
    }
    window.addEventListener("resize", resize);
  }

  function unbindObservers() {
    if (state.ro) { state.ro.disconnect(); state.ro = null; }
    if (state.io) { state.io.disconnect(); state.io = null; }
    window.removeEventListener("resize", resize);
  }

  function mount(stage, event) {
    if (!stage) return;
    if (state.renderer) unmount();
    state.epoch++;
    var mountEpoch = state.epoch;
    state.stage = stage;
    state.event = event || null;
    state.regions = null;
    state.sites = {};
    state.ready = false;
    state.loading = true;
    state.spin = false;
    state.imgMode = "base";
    state.mixValue = 1;
    state.mixTarget = 1;
    state.baseValue = 0;
    state.baseTarget = 0;
    state.mixAnim = null;
    state.preset = null;
    state.source = "";
    state.dates = {};
    state.labels = [];
    state.beacons = [];
    state.patches = [];
    state.focusIds = {};
    state.focusPatches = [];
    state.view.target = new THREE.Vector3();
    state.goal.target = new THREE.Vector3();
    state.visible = true;
    state.frame = 0;
    state.dust = null;
    state.river = null;
    state.surfaceU = null;
    state.terrainMesh = null;
    state.activeKey = null;
    ensureChrome(stage);
    if (!initRenderer(stage)) return;
    state.clock = new THREE.Clock();
    bindControls(stage);
    bindObservers(stage);
    setLoading(event ? "正在载入事件影像…" : "正在载入样区数据…");

    animate();

    loadRegions()
      .catch(function (err) {
        console.warn("[geo3d] regions 接口不可用，使用内置样区参数：", err && err.message);
        return fallbackRegions();
      })
      .then(function (items) {
        if (mountEpoch !== state.epoch || !state.stage) return;
        state.regions = items || [];
        state.sites = {};
        state.regions.forEach(function (item) { state.sites[item.id] = item; });
        buildSpecList();
        var key = event ? "event" : "site:p07_2023b";
        renderChips();
        switchRegion(key, true);
      });
  }

  function unmount() {
    state.epoch++;
    if (state.raf) {
      cancelAnimationFrame(state.raf);
      state.raf = 0;
    }
    unbindObservers();
    if (state.controls) {
      state.controls.dispose();
      state.controls = null;
    }
    state.preset = null;
    if (state.root && state.scene) {
      state.scene.remove(state.root);
      disposeObject(state.root);
      state.root = null;
    }
    state.sceneTextures.forEach(function (t) { if (t) t.dispose(); });
    state.sceneTextures = [];
    if (state.renderer) {
      state.renderer.dispose();
      if (state.renderer.domElement && state.renderer.domElement.parentNode) {
        state.renderer.domElement.parentNode.removeChild(state.renderer.domElement);
      }
    }
    if (chrome.labels) chrome.labels.innerHTML = "";
    state.renderer = null;
    state.canvas = null;
    state.scene = null;
    state.camera = null;
    state.block = null;
    state.root = null;
    state.labels = [];
    state.beacons = [];
    state.stage = null;
    state.ready = false;
    state.loading = false;
    state.activeKey = null;
    state.view.target = new THREE.Vector3();
    state.goal.target = new THREE.Vector3();
    chrome = { hud: null, sites: null, imagery: null, labels: null, tip: null, legend: null, hint: null, loading: null, card: null, compass: null, compassDial: null };
  }

  function setSpin(on) {
    state.spin = !!on;
    if (state.controls) {
      state.controls.autoRotate = state.spin;
      state.controls.autoRotateSpeed = 0.65;
    }
  }

  window.YTGeo3D = {
    mount: mount,
    unmount: unmount,
    setPreset: setPreset,
    setSpin: setSpin,
    isReady: function () { return !!state.ready && !state.loading; }
  };
})();
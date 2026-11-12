/* 空中办案三维态势：程序化地形 + 同机位航线 + 卫星/无人机/指挥节点 + 电影式推演。
   仅依赖全局 THREE（r128），不引入新依赖。 */
(function () {
  "use strict";

  var T = window.THREE;
  var state = {
    ready: false,
    scene: null,
    camera: null,
    renderer: null,
    clock: null,
    canvas: null,
    center: [109.1, 41.9593],
    patch: null,
    route: null,
    curve: null,
    routePoints: [],
    relHeight: 120,
    texture: null,
    terrainMat: null,
    terrain: null,
    terrainWire: null,
    patchGroup: null,
    patchRing: null,
    patchRing2: null,
    patchBeam: null,
    patchLabel: null,
    bumpTexture: null,
    changeGroup: null,
    changeGlow: null,
    changeRing: null,
    changeRing2: null,
    scanSweep: null,
    changeArea: 0,
    changeMode: "precise",
    siteProps: null,
    changeBase: 0.24,
    changeRingBase: 0.42,
    beacon: null,
    navLights: [],
    routeGroup: null,
    routeTube: null,
    routeGlow: null,
    routeParticles: [],
    uav: null,
    uavLabel: null,
    uavLink: null,
    scanCone: null,
    scanRing: null,
    satellite: null,
    satLabel: null,
    satBeam: null,
    satBeacon: null,
    command: null,
    commandLabel: null,
    commandRing: null,
    evidenceLink: null,
    links: [],
    stars: null,
    dust: null,
    dustData: null,
    cameraMode: "orbit",
    orbit: { theta: 0.58, phi: 1.06, radius: 1900 },
    lookTarget: null,
    dragging: false,
    dragMoved: false,
    lastPointer: { x: 0, y: 0 },
    autoRotate: true,
    run: { active: false, start: 0, duration: 8400, phase: -1 },
    onPhase: null,
    satelliteAngle: 0.7,
    t: 0,
    idleUav: { x: 430, y: 205, z: 380 }
  };

  // ------------------------------------------------------------------ utils
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function lonLatToLocal(lon, lat) {
    var mx = (lon - state.center[0]) * 111320 * Math.cos(state.center[1] * Math.PI / 180);
    var mz = -(lat - state.center[1]) * 110540;
    return new T.Vector3(mx, 0, mz);
  }
  function terrainHeight(x, z) {
    var macro = 34 * Math.sin(x * 0.0011) * Math.cos(z * 0.0014) + 18 * Math.sin(x * 0.00055 + 1.7) * Math.sin(z * 0.0009);
    var ridge = 26 * Math.exp(-Math.pow((z + 260) / 210, 2)) * Math.exp(-Math.pow((x - 120) / 950, 2));
    var ridge2 = 18 * Math.exp(-Math.pow((z - 430) / 180, 2)) * Math.exp(-Math.pow((x + 300) / 800, 2));
    var rough = Math.sin(x * 0.008) * Math.cos(z * 0.009) * 5 + Math.sin(x * 0.021 + 1.7) * 2.4 + Math.cos(z * 0.017 - 0.6) * 2.0;
    var pit = -30 * Math.exp(-(x * x + z * z) / (2 * 230 * 230));
    var pit2 = -14 * Math.exp(-((x - 430) * (x - 430) + (z + 40) * (z + 40)) / (2 * 150 * 150));
    return macro + ridge + ridge2 + rough + pit + pit2;
  }
  function clearGroup(group) {
    if (!group) return;
    while (group.children.length) {
      var child = group.children.pop();
      group.remove(child);
      if (child.geometry) child.geometry.dispose();
      if (child.material) {
        if (child.material.map) child.material.map.dispose();
        child.material.dispose();
      }
    }
  }
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  // ------------------------------------------------------------------ labels
  function makeLabel(text, color, opts) {
    opts = opts || {};
    var canvas = document.createElement("canvas");
    canvas.width = 720;
    canvas.height = 160;
    var texture = new T.CanvasTexture(canvas);
    texture.minFilter = T.LinearFilter;
    texture.magFilter = T.LinearFilter;
    var material = new T.SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false, fog: false });
    var sprite = new T.Sprite(material);
    var obj = { sprite: sprite, canvas: canvas, ctx: canvas.getContext("2d"), texture: texture, color: color, font: opts.font || 34, scale: opts.scale || 0.5 };
    redrawLabel(obj, text, color);
    return obj;
  }
  function redrawLabel(obj, text, color) {
    var canvas = obj.canvas;
    var ctx = obj.ctx;
    var font = "600 " + obj.font + 'px "Microsoft YaHei", "PingFang SC", sans-serif';
    ctx.font = font;
    var tw = ctx.measureText(text).width;
    var w = Math.max(150, Math.min(680, Math.ceil(tw + 58)));
    var h = 120;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      obj.ctx = canvas.getContext("2d");
      ctx = obj.ctx;
    }
    ctx.clearRect(0, 0, w, h);
    ctx.font = font;
    roundRect(ctx, 2, 8, w - 4, h - 16, 14);
    ctx.fillStyle = "rgba(5,15,25,0.86)";
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.fillRect(2, 8, 7, h - 16);
    ctx.fillStyle = "#e6f3ff";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 22, h / 2 + 2);
    obj.texture.needsUpdate = true;
    obj.sprite.scale.set(w * obj.scale, h * obj.scale, 1);
  }
  function placeLabel(obj, position) {
    if (!obj) return;
    obj.sprite.position.copy(position);
  }

  // ------------------------------------------------------------------ textures
  // 6000x4000 大地形；中央 2000x1500 叠加 AOI 真实影像，其余为程序化地表。
  var WORLD_W = 6000, WORLD_H = 4000, AOI_W = 1800, AOI_H = 900;
  var TEX_W = 4096, TEX_H = Math.round(TEX_W * WORLD_H / WORLD_W);
  var TEX_S = TEX_W / 2048;
  var AOI_X = (WORLD_W - AOI_W) / 2 / WORLD_W * TEX_W;
  var AOI_Y = (WORLD_H - AOI_H) / 2 / WORLD_H * TEX_H;
  var AOI_PW = AOI_W / WORLD_W * TEX_W;
  var AOI_PH = AOI_H / WORLD_H * TEX_H;

  function baseCanvas() {
    var canvas = document.createElement("canvas");
    canvas.width = TEX_W;
    canvas.height = TEX_H;
    var ctx = canvas.getContext("2d");
    var g = ctx.createLinearGradient(0, 0, TEX_W, TEX_H);
    g.addColorStop(0, "#38443a");
    g.addColorStop(0.38, "#4a4a38");
    g.addColorStop(0.72, "#3d3428");
    g.addColorStop(1, "#242a24");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, TEX_W, TEX_H);
    var seed = 20261111;
    function rnd() { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; }
    for (var i = 0; i < 26000; i++) {
      var x = rnd() * TEX_W, y = rnd() * TEX_H, r = (0.5 + rnd() * 3.2) * TEX_S;
      ctx.fillStyle = rnd() > 0.5 ? "rgba(20,28,20,0.18)" : "rgba(196,178,138,0.1)";
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
    for (var k = 0; k < 26; k++) {
      var y0 = rnd() * TEX_H;
      ctx.beginPath();
      ctx.moveTo(-40, y0);
      for (var xx = -40; xx <= TEX_W + 40; xx += 48) {
        ctx.lineTo(xx, y0 + Math.sin(xx * 0.004 + k) * 26 + (rnd() - 0.5) * 12);
      }
      ctx.strokeStyle = k % 3 === 0 ? "rgba(18,24,18,0.28)" : "rgba(120,112,86,0.12)";
      ctx.lineWidth = (6 + rnd() * 10) * TEX_S;
      ctx.stroke();
    }
    ctx.strokeStyle = "rgba(210,198,170,0.26)";
    ctx.lineWidth = 3 * TEX_S;
    ctx.beginPath();
    ctx.moveTo(0, TEX_H * 0.62);
    ctx.bezierCurveTo(TEX_W * 0.3, TEX_H * 0.5, TEX_W * 0.62, TEX_H * 0.7, TEX_W, TEX_H * 0.58);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(TEX_W * 0.48, 0);
    ctx.bezierCurveTo(TEX_W * 0.55, TEX_H * 0.3, TEX_W * 0.42, TEX_H * 0.7, TEX_W * 0.5, TEX_H);
    ctx.stroke();
    // 中央采坑占位：真实 AOI 影像加载后会被覆盖
    var cx = AOI_X + AOI_PW * 0.52, cy = AOI_Y + AOI_PH * 0.56;
    for (var ring = 0; ring < 4; ring++) {
      var rad = AOI_PW * (0.18 - ring * 0.035);
      ctx.beginPath();
      for (var a = 0; a <= 52; a++) {
        var ang = Math.PI * 2 * a / 52;
        var rr = rad * (0.88 + 0.18 * Math.sin(ang * 3 + ring));
        var px = cx + Math.cos(ang) * rr, py = cy + Math.sin(ang) * rr * 0.68;
        if (a === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fillStyle = ["#b9b2a3", "#d2cabb", "#a89f8e", "#ddd6c8"][ring];
      ctx.fill();
      ctx.strokeStyle = "rgba(50,44,34,0.28)";
      ctx.lineWidth = 1.6 * TEX_S;
      ctx.stroke();
    }
    return canvas;
  }

  function textureFromCanvas(canvas) {
    var texture = new T.CanvasTexture(canvas);
    texture.encoding = T.sRGBEncoding;
    texture.anisotropy = state.renderer ? state.renderer.capabilities.getMaxAnisotropy() : 4;
    texture.wrapS = T.ClampToEdgeWrapping;
    texture.wrapT = T.ClampToEdgeWrapping;
    return texture;
  }

  function proceduralTexture() {
    return textureFromCanvas(baseCanvas());
  }

  function loadTexture(url) {
    if (!url || !state.terrainMat) return;
    var canvas = baseCanvas();
    var img = new Image();
    img.onload = function () {
      var ctx = canvas.getContext("2d");
      var destAspect = AOI_PW / AOI_PH;
      var srcW = img.width, srcH = img.height;
      var sx = 0, sy = 0, sw = srcW, sh = srcH;
      if (srcW / srcH > destAspect) { sw = srcH * destAspect; sx = (srcW - sw) / 2; }
      else { sh = srcW / destAspect; sy = (srcH - sh) / 2; }
      var tmp = document.createElement("canvas");
      tmp.width = AOI_PW;
      tmp.height = AOI_PH;
      var tctx = tmp.getContext("2d");
      tctx.filter = "brightness(0.94) saturate(0.74) contrast(1.07)";
      tctx.drawImage(img, sx, sy, sw, sh, 0, 0, AOI_PW, AOI_PH);
      tctx.filter = "none";
      tctx.globalCompositeOperation = "multiply";
      tctx.fillStyle = "rgba(16,46,64,0.30)";
      tctx.fillRect(0, 0, AOI_PW, AOI_PH);
      tctx.globalCompositeOperation = "screen";
      var cool = tctx.createLinearGradient(0, 0, AOI_PW, AOI_PH);
      cool.addColorStop(0, "rgba(12,54,76,0.08)");
      cool.addColorStop(0.55, "rgba(4,22,34,0)");
      cool.addColorStop(1, "rgba(80,58,28,0.05)");
      tctx.fillStyle = cool;
      tctx.fillRect(0, 0, AOI_PW, AOI_PH);
      tctx.globalCompositeOperation = "source-over";
      tctx.fillStyle = "rgba(3,10,16,0.10)";
      tctx.fillRect(0, 0, AOI_PW, AOI_PH);
      // 四边渐变羽化：alpha 在画布边界归零，避免矩形硬边
      var maskCanvas = document.createElement("canvas");
      maskCanvas.width = AOI_PW;
      maskCanvas.height = AOI_PH;
      var mctx = maskCanvas.getContext("2d");
      mctx.fillStyle = "#ffffff";
      mctx.fillRect(0, 0, AOI_PW, AOI_PH);
      mctx.globalCompositeOperation = "destination-in";
      var gx = mctx.createLinearGradient(0, 0, AOI_PW, 0);
      gx.addColorStop(0, "rgba(255,255,255,0)");
      gx.addColorStop(0.14, "rgba(255,255,255,1)");
      gx.addColorStop(0.86, "rgba(255,255,255,1)");
      gx.addColorStop(1, "rgba(255,255,255,0)");
      mctx.fillStyle = gx;
      mctx.fillRect(0, 0, AOI_PW, AOI_PH);
      var gy = mctx.createLinearGradient(0, 0, 0, AOI_PH);
      gy.addColorStop(0, "rgba(255,255,255,0)");
      gy.addColorStop(0.24, "rgba(255,255,255,1)");
      gy.addColorStop(0.76, "rgba(255,255,255,1)");
      gy.addColorStop(1, "rgba(255,255,255,0)");
      mctx.fillStyle = gy;
      mctx.fillRect(0, 0, AOI_PW, AOI_PH);
      tctx.globalCompositeOperation = "destination-in";
      tctx.drawImage(maskCanvas, 0, 0);
      tctx.globalCompositeOperation = "source-atop";
      var vignette = tctx.createRadialGradient(AOI_PW / 2, AOI_PH / 2, AOI_PW * 0.18, AOI_PW / 2, AOI_PH / 2, AOI_PW * 0.62);
      vignette.addColorStop(0, "rgba(2,8,13,0)");
      vignette.addColorStop(0.62, "rgba(2,8,13,0.06)");
      vignette.addColorStop(1, "rgba(2,8,13,0.38)");
      tctx.fillStyle = vignette;
      tctx.fillRect(0, 0, AOI_PW, AOI_PH);
      tctx.globalCompositeOperation = "source-over";
      ctx.drawImage(tmp, AOI_X, AOI_Y);
      var texture = textureFromCanvas(canvas);
      if (state.texture) state.texture.dispose();
      state.texture = texture;
      state.terrainMat.map = texture;
      var bumpCanvas = document.createElement("canvas");
      bumpCanvas.width = 1024;
      bumpCanvas.height = 683;
      var bctx = bumpCanvas.getContext("2d");
      bctx.filter = "grayscale(1) contrast(1.35) brightness(0.95)";
      bctx.drawImage(canvas, 0, 0, 1024, 683);
      bctx.filter = "none";
      var bump = new T.CanvasTexture(bumpCanvas);
      bump.encoding = T.LinearEncoding;
      bump.anisotropy = state.renderer ? state.renderer.capabilities.getMaxAnisotropy() : 4;
      if (state.bumpTexture) state.bumpTexture.dispose();
      state.bumpTexture = bump;
      state.terrainMat.bumpMap = bump;
      state.terrainMat.bumpScale = 7.5;
      state.terrainMat.color.setHex(0xffffff);
      state.terrainMat.emissiveIntensity = 0.11;
      state.terrainMat.needsUpdate = true;
    };
    img.onerror = function () { /* 保留程序化底图 */ };
    img.src = url;
  }

  // ------------------------------------------------------------------ terrain
  function buildTerrain(texture) {
    var geo = new T.PlaneGeometry(WORLD_W, WORLD_H, 260, 174);
    var pos = geo.attributes.position;
    for (var i = 0; i < pos.count; i++) {
      var x = pos.getX(i);
      var y = pos.getY(i);
      pos.setZ(i, terrainHeight(x, -y));
    }
    geo.computeVertexNormals();
    var mat = new T.MeshStandardMaterial({
      map: texture || proceduralTexture(),
      color: 0xb9c7c4,
      roughness: 0.94,
      metalness: 0.02,
      emissive: 0x061018,
      emissiveIntensity: 0.14
    });
    var mesh = new T.Mesh(geo, mat);
    mesh.rotation.x = -Math.PI / 2;
    state.terrainMat = mat;
    state.terrain = mesh;
    state.scene.add(mesh);

    var wireMat = new T.MeshBasicMaterial({ color: 0x2b7f9f, wireframe: true, transparent: true, opacity: 0.032, depthWrite: false });
    var wire = new T.Mesh(geo.clone(), wireMat);
    wire.rotation.x = -Math.PI / 2;
    wire.position.y = 0.8;
    state.terrainWire = wire;
    state.scene.add(wire);
  }

  function buildAoiFrame() {
    var x0 = -AOI_W / 2, x1 = AOI_W / 2, z0 = -AOI_H / 2, z1 = AOI_H / 2;
    var arm = 120, pts = [];
    var corners = [[x0, z0, 1, 1], [x1, z0, -1, 1], [x1, z1, -1, -1], [x0, z1, 1, -1]];
    corners.forEach(function (c) {
      var y = terrainHeight(c[0], c[1]) + 6;
      pts.push(new T.Vector3(c[0], y, c[1]), new T.Vector3(c[0] + c[2] * arm, y, c[1]));
      pts.push(new T.Vector3(c[0], y, c[1]), new T.Vector3(c[0], y, c[1] + c[3] * arm));
    });
    var geo = new T.BufferGeometry().setFromPoints(pts);
    var line = new T.LineSegments(geo, new T.LineBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0.58, blending: T.AdditiveBlending, depthWrite: false }));
    state.scene.add(line);
    var cornerMat = new T.MeshBasicMaterial({ color: 0x9ff4ff, transparent: true, opacity: 0.9 });
    corners.forEach(function (c) {
      var dot = new T.Mesh(new T.SphereGeometry(5.5, 8, 8), cornerMat);
      dot.position.set(c[0], terrainHeight(c[0], c[1]) + 8, c[1]);
      state.scene.add(dot);
    });
  }

  function buildAoiGrid() {
    var grid = new T.GridHelper(AOI_W * 0.9, 10, 0x38e1ff, 0x1e5f78);
    grid.scale.z = (AOI_H * 0.9) / (AOI_W * 0.9);
    grid.position.y = terrainHeight(0, 0) + 3.6;
    grid.material.transparent = true;
    grid.material.opacity = 0.16;
    grid.material.depthWrite = false;
    state.scene.add(grid);
  }

  // ------------------------------------------------------------------ site props
  function buildSiteProps() {
    state.siteProps = new T.Group();
    state.scene.add(state.siteProps);
  }

  function updateSiteProps(patch) {
    if (!state.siteProps) return;
    clearGroup(state.siteProps);
    if (!patch || !patch.center_lonlat) return;
    var c = lonLatToLocal(patch.center_lonlat[0], patch.center_lonlat[1]);
    var scale = clamp(Math.sqrt(Number(patch.area_ha) || 10) * 22, 95, 250);
    var pileMat = new T.MeshStandardMaterial({ color: 0x3b342c, roughness: 1, metalness: 0, flatShading: true });
    var shedMat = new T.MeshStandardMaterial({ color: 0x3d4a50, roughness: 0.8, metalness: 0.15, flatShading: true });
    var lampMat = new T.MeshBasicMaterial({ color: 0xffbe55, transparent: true, opacity: 0.9 });
    var piles = [[-1.05, -0.52, 1.0], [1.08, -0.62, 1.15], [-0.55, 1.05, 0.75], [0.9, 0.92, 0.6], [-1.15, 0.45, 0.45]];
    for (var i = 0; i < piles.length; i++) {
      var q = piles[i];
      var x = c.x + q[0] * scale, z = c.z + q[1] * scale;
      var h = 8 + q[2] * 18, r = 10 + q[2] * 10;
      var cone = new T.Mesh(new T.CylinderGeometry(r * 0.38, r, h, 7), pileMat);
      cone.position.set(x, terrainHeight(x, z) + h / 2, z);
      cone.rotation.y = i * 1.7;
      state.siteProps.add(cone);
      var lamp = new T.Mesh(new T.SphereGeometry(1.8, 8, 8), lampMat);
      lamp.position.set(x, terrainHeight(x, z) + h + 2, z);
      state.siteProps.add(lamp);
    }
    var sheds = [[-1.18, -0.72, 34, 13, 20], [1.02, 0.82, 26, 10, 16], [0.18, -1.08, 22, 8, 14]];
    for (var j = 0; j < sheds.length; j++) {
      var d = sheds[j];
      var sx = c.x + d[0] * scale, sz = c.z + d[1] * scale;
      var shed = new T.Mesh(new T.BoxGeometry(d[2], d[3], d[4]), shedMat);
      shed.position.set(sx, terrainHeight(sx, sz) + d[3] / 2, sz);
      shed.rotation.y = j * 0.6;
      state.siteProps.add(shed);
    }
  }

  // ------------------------------------------------------------------ patch
  function buildPatchMarker() {
    var group = new T.Group();
    var y = terrainHeight(0, 0) + 2;
    var ringMat = new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0.62, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false });
    var ring = new T.Mesh(new T.RingGeometry(340, 360, 128), ringMat);
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = y;
    group.add(ring);
    var ring2 = new T.Mesh(new T.RingGeometry(296, 306, 128), new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0.28, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
    ring2.rotation.x = -Math.PI / 2;
    ring2.position.y = y + 0.5;
    group.add(ring2);
    var beam = new T.Mesh(
      new T.CylinderGeometry(4, 12, 560, 24, 1, true),
      new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0.05, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false })
    );
    beam.position.y = y + 280;
    group.add(beam);
    var label = makeLabel("P-01", 0x38e1ff, { font: 24, scale: 0.42 });
    placeLabel(label, new T.Vector3(-210, y + 470, -150));
    group.add(label.sprite);
    state.patchGroup = group;
    state.patchRing = ring;
    state.patchRing2 = ring2;
    state.patchBeam = beam;
    state.patchLabel = label;
    state.scene.add(group);
  }

  function updatePatchMarker(patch, selected) {
    if (!state.patchLabel || !patch) return;
    var color = selected ? 0xffbe55 : 0x38e1ff;
    var text = patch.id + " · " + Number(patch.area_ha || 0).toFixed(1) + " ha";
    redrawLabel(state.patchLabel, text, color);
    state.patchLabel.sprite.position.set(-210, terrainHeight(0, 0) + 470, -150);
    state.patchRing.material.color.setHex(color);
    state.patchRing2.material.color.setHex(color);
    state.patchBeam.material.color.setHex(color);
  }

  // ------------------------------------------------------------------ route
  function buildRoute(route) {
    clearGroup(state.routeGroup);
    state.routeParticles = [];
    state.curve = null;
    state.routePoints = [];
    var preview = !route || !route.waypoints || route.waypoints.length < 2;
    var points = [];
    var minAlt = Infinity, maxAlt = -Infinity, i, li;
    if (preview) {
      // 未创建任务时也展示一条“计划航线”，让三维态势保持动态
      var half = 360, lanes = 6;
      for (li = 0; li < lanes; li++) {
        var z = -half + li * (half * 2 / (lanes - 1));
        var dir = li % 2 === 0 ? 1 : -1;
        for (var k = 0; k < 5; k++) {
          var x = -half * dir + dir * (k / 4) * half * 2;
          points.push(new T.Vector3(x, 0, z));
        }
      }
    } else {
      for (i = 0; i < route.waypoints.length; i++) {
        var w = route.waypoints[i];
        var p = lonLatToLocal(w.lon, w.lat);
        minAlt = Math.min(minAlt, w.terrain_m);
        maxAlt = Math.max(maxAlt, w.terrain_m);
        points.push(p);
      }
    }
    var flightY = preview ? 235 : 230 + Math.max(0, (maxAlt - minAlt) * 0.4);
    for (i = 0; i < points.length; i++) points[i].y = flightY + Math.sin(i * 0.4) * 1.5;
    var curve = new T.CatmullRomCurve3(points, false, "catmullrom", 0.16);
    state.curve = curve;
    state.routePoints = points;

    var tube = new T.Mesh(
      new T.TubeGeometry(curve, Math.max(80, points.length * 2), preview ? 3.2 : 2.4, 8, false),
      new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: preview ? 0.16 : 0.20, blending: T.AdditiveBlending, depthWrite: false })
    );
    var glow = new T.Mesh(
      new T.TubeGeometry(curve, Math.max(60, points.length), 10.0, 8, false),
      new T.MeshBasicMaterial({ color: 0x1e7fa8, transparent: true, opacity: preview ? 0.045 : 0.05, blending: T.AdditiveBlending, depthWrite: false })
    );
    state.routeTube = tube;
    state.routeGlow = glow;
    state.routeGroup.add(tube);
    state.routeGroup.add(glow);

    // 航点微光
    var dotMat = new T.MeshBasicMaterial({ color: 0x9ff4ff, transparent: true, opacity: preview ? 0.55 : 0.20 });
    for (i = 0; i < points.length; i += Math.max(1, Math.floor(points.length / 26))) {
      var dot = new T.Mesh(new T.SphereGeometry(preview ? 4.0 : 3.0, 8, 8), dotMat);
      dot.position.copy(points[i]);
      state.routeGroup.add(dot);
    }
    // 起降点
    var start = new T.Mesh(new T.CylinderGeometry(6, 6, 3, 18), new T.MeshBasicMaterial({ color: 0x38e6a4 }));
    start.position.copy(points[0]).add(new T.Vector3(0, -2, 0));
    var end = new T.Mesh(new T.CylinderGeometry(7, 7, 3, 18), new T.MeshBasicMaterial({ color: 0xffbe55 }));
    end.position.copy(points[points.length - 1]).add(new T.Vector3(0, -2, 0));
    state.routeGroup.add(start);
    state.routeGroup.add(end);

    // 航线粒子
    for (i = 0; i < 10; i++) {
      var particle = new T.Mesh(new T.SphereGeometry(5.0, 8, 8), new T.MeshBasicMaterial({ color: 0x38e6a4, transparent: true, opacity: 0.55 }));
      state.routeGroup.add(particle);
      state.routeParticles.push({ mesh: particle, offset: i / 10, speed: 0.055 + (i % 3) * 0.008 });
    }

    if (state.uav) {
      state.uav.visible = true;
      state.uav.position.copy(points[0]).add(new T.Vector3(0, 10, 0));
    }
    if (state.uavLabel) {
      state.uavLabel.sprite.visible = true;
      redrawLabel(state.uavLabel, preview ? "UAV · 计划航线" : ("UAV · " + Math.round(state.relHeight) + " m"), 0x38e6a4);
    }
  }

  function makeGlowTexture() {
    var canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 64;
    var ctx = canvas.getContext("2d");
    var g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.28, "rgba(255,255,255,0.55)");
    g.addColorStop(0.72, "rgba(255,255,255,0.12)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 64, 64);
    return new T.CanvasTexture(canvas);
  }

  // ------------------------------------------------------------------ uav
  function buildUav() {
    var group = new T.Group();
    var dark = new T.MeshStandardMaterial({ color: 0x1c2a35, roughness: 0.55, metalness: 0.55 });
    var light = new T.MeshStandardMaterial({ color: 0x9fb3c2, roughness: 0.4, metalness: 0.6 });
    var body = new T.Mesh(new T.BoxGeometry(16, 5, 24), light);
    group.add(body);
    var shell = new T.Mesh(new T.BoxGeometry(10, 3, 13), light);
    shell.position.y = 4;
    group.add(shell);
    var gimbal = new T.Mesh(new T.SphereGeometry(3.4, 16, 12), dark);
    gimbal.position.set(0, -2.8, 9);
    group.add(gimbal);
    var lens = new T.Mesh(new T.CylinderGeometry(1.6, 1.6, 3, 16), new T.MeshStandardMaterial({ color: 0x0a1822, metalness: 0.8, roughness: 0.2 }));
    lens.rotation.x = Math.PI / 2;
    lens.position.set(0, -2.8, 11.6);
    group.add(lens);
    var rotors = [];
    var arms = [[-1, -1], [1, -1], [-1, 1], [1, 1]];
    for (var i = 0; i < arms.length; i++) {
      var ax = arms[i][0] * 13, az = arms[i][1] * 13;
      var arm = new T.Mesh(new T.BoxGeometry(2, 1.6, 19), dark);
      arm.position.set(ax * 0.55, 1.2, az * 0.55);
      arm.rotation.y = Math.atan2(ax, az);
      group.add(arm);
      var rotorGroup = new T.Group();
      rotorGroup.position.set(ax, 3.4, az);
      var rotor = new T.Mesh(new T.CylinderGeometry(13, 13, 0.6, 24, 1, true), new T.MeshBasicMaterial({ color: 0x9fd8ea, transparent: true, opacity: 0.35, side: T.DoubleSide, depthWrite: false }));
      rotorGroup.add(rotor);
      var blade1 = new T.Mesh(new T.BoxGeometry(25, 0.8, 1.8), new T.MeshBasicMaterial({ color: 0x283b48 }));
      blade1.position.y = 0.4;
      rotorGroup.add(blade1);
      var blade2 = blade1.clone();
      blade2.rotation.y = Math.PI / 2;
      rotorGroup.add(blade2);
      group.add(rotorGroup);
      rotors.push(rotorGroup);
    }
    var red = new T.Mesh(new T.SphereGeometry(1.4, 8, 8), new T.MeshBasicMaterial({ color: 0xff5d5d, transparent: true, opacity: 1 }));
    red.position.set(-8, 1.5, -12);
    var green = new T.Mesh(new T.SphereGeometry(1.4, 8, 8), new T.MeshBasicMaterial({ color: 0x38e6a4, transparent: true, opacity: 1 }));
    green.position.set(8, 1.5, -12);
    group.add(red);
    group.add(green);
    var cone = new T.Mesh(
      new T.ConeGeometry(100, 220, 32, 1, true),
      new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0.13, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false })
    );
    cone.position.y = -116;
    cone.visible = false;
    group.add(cone);
    var beacon = new T.Mesh(new T.SphereGeometry(5, 12, 12), new T.MeshBasicMaterial({ color: 0x38e6a4, transparent: true, opacity: 0.55 }));
    beacon.position.y = 12;
    group.add(beacon);
    var glow = new T.PointLight(0x38e6a4, 1.2, 240, 2);
    glow.position.set(0, 18, 0);
    group.add(glow);
    state.beacon = beacon;
    state.navLights = [red, green];
    var halo = new T.Mesh(
      new T.RingGeometry(22, 30, 40),
      new T.MeshBasicMaterial({ color: 0x38e6a4, transparent: true, opacity: 0.5, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false })
    );
    halo.rotation.x = -Math.PI / 2;
    halo.position.y = -9;
    group.add(halo);
    var label = makeLabel("UAV · 120 m", 0x38e6a4, { font: 24, scale: 0.24 });
    label.sprite.position.set(120, 200, 0);
    group.add(label.sprite);
    group.scale.setScalar(1.9);
    group.position.set(state.idleUav.x, state.idleUav.y, state.idleUav.z);
    group.visible = true;
    state.uav = group;
    state.rotors = rotors;
    state.scanCone = cone;
    state.uavLabel = label;
    state.scene.add(group);
  }

  function buildScanRing() {
    var ring = new T.Mesh(
      new T.RingGeometry(104, 112, 72),
      new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false })
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = terrainHeight(0, 0) + 2;
    state.scanRing = ring;
    state.scene.add(ring);
  }

  // ------------------------------------------------------------------ satellite
  function buildSatellite() {
    var group = new T.Group();
    var bodyMat = new T.MeshStandardMaterial({ color: 0x93a6b4, roughness: 0.35, metalness: 0.8 });
    var body = new T.Mesh(new T.BoxGeometry(38, 26, 38), bodyMat);
    group.add(body);
    var panelCanvas = document.createElement("canvas");
    panelCanvas.width = 256;
    panelCanvas.height = 128;
    var pctx = panelCanvas.getContext("2d");
    var pg = pctx.createLinearGradient(0, 0, 256, 128);
    pg.addColorStop(0, "#0c2c58");
    pg.addColorStop(0.5, "#174f92");
    pg.addColorStop(1, "#0a2344");
    pctx.fillStyle = pg;
    pctx.fillRect(0, 0, 256, 128);
    pctx.strokeStyle = "rgba(116,190,255,0.42)";
    pctx.lineWidth = 1;
    for (var pxi = 0; pxi <= 256; pxi += 16) { pctx.beginPath(); pctx.moveTo(pxi, 0); pctx.lineTo(pxi, 128); pctx.stroke(); }
    for (var pyi = 0; pyi <= 128; pyi += 16) { pctx.beginPath(); pctx.moveTo(0, pyi); pctx.lineTo(256, pyi); pctx.stroke(); }
    var panelTex = new T.CanvasTexture(panelCanvas);
    panelTex.encoding = T.sRGBEncoding;
    panelTex.wrapS = panelTex.wrapT = T.RepeatWrapping;
    panelTex.repeat.set(2, 1);
    var panelMat = new T.MeshStandardMaterial({ map: panelTex, color: 0xffffff, emissive: 0x0a2850, roughness: 0.42, metalness: 0.6 });
    var panel1 = new T.Mesh(new T.BoxGeometry(120, 2, 42), panelMat);
    panel1.position.x = -78;
    var panel2 = panel1.clone();
    panel2.position.x = 78;
    group.add(panel1);
    group.add(panel2);
    var dish = new T.Mesh(new T.SphereGeometry(13, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2), new T.MeshStandardMaterial({ color: 0xd8e2e8, metalness: 0.6, roughness: 0.3, side: T.DoubleSide }));
    dish.rotation.x = Math.PI;
    dish.position.y = -17;
    group.add(dish);
    var antenna = new T.Mesh(new T.CylinderGeometry(1.2, 1.2, 22, 8), bodyMat);
    antenna.position.set(0, 24, 0);
    group.add(antenna);
    var satBeacon = new T.Mesh(
      new T.SphereGeometry(2.4, 10, 10),
      new T.MeshBasicMaterial({ color: 0xff6b6b, transparent: true, opacity: 0.95 })
    );
    satBeacon.position.set(0, 38, 0);
    group.add(satBeacon);
    state.satBeacon = satBeacon;
    var label = makeLabel("SAT-6G · NTN", 0x9b8cff, { font: 22, scale: 0.30 });
    label.sprite.position.set(220, 20, 0);
    group.add(label.sprite);
    group.scale.setScalar(1.3);
    group.position.set(-760, 650, -620);
    state.satellite = group;
    state.satLabel = label;
    state.scene.add(group);

    var orbit = new T.Mesh(
      new T.TorusGeometry(760, 2.0, 8, 160),
      new T.MeshBasicMaterial({ color: 0x4f6fae, transparent: true, opacity: 0.18 })
    );
    orbit.rotation.x = Math.PI / 2.1;
    orbit.rotation.z = 0.28;
    orbit.position.y = 700;
    state.scene.add(orbit);

    var beam = new T.Mesh(
      new T.CylinderGeometry(4, 90, 1, 28, 1, true),
      new T.MeshBasicMaterial({ color: 0x6fb6ff, transparent: true, opacity: 0.055, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false })
    );
    state.satBeam = beam;
    state.scene.add(beam);
  }

  function updateSatBeam() {
    if (!state.satBeam || !state.satellite) return;
    var start = state.satellite.position.clone();
    var end = new T.Vector3(0, terrainHeight(0, 0) + 4, 0);
    var dir = start.clone().sub(end);
    var len = dir.length();
    state.satBeam.scale.y = len;
    state.satBeam.position.copy(end).addScaledVector(dir, 0.5);
    state.satBeam.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), dir.normalize());
  }

  // ------------------------------------------------------------------ command
  function buildCommand() {
    var group = new T.Group();
    var base = new T.Mesh(new T.CylinderGeometry(48, 62, 18, 6), new T.MeshStandardMaterial({ color: 0x162835, roughness: 0.6, metalness: 0.5 }));
    group.add(base);
    var ring = new T.Mesh(new T.TorusGeometry(54, 1.8, 8, 6), new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0.7 }));
    ring.rotation.x = Math.PI / 2;
    ring.position.y = 10;
    group.add(ring);
    for (var i = 0; i < 3; i++) {
      var pillar = new T.Mesh(new T.CylinderGeometry(1.2, 1.2, 26, 8), new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0.35 }));
      pillar.position.set(Math.cos(i * Math.PI * 2 / 3) * 34, 30, Math.sin(i * Math.PI * 2 / 3) * 34);
      group.add(pillar);
    }
    var holo = new T.Mesh(new T.RingGeometry(34, 40, 6), new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0.26, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
    holo.rotation.x = -Math.PI / 2;
    holo.position.y = 52;
    group.add(holo);
    var label = makeLabel("指挥中心 / EDGE", 0x38e1ff, { font: 22, scale: 0.30 });
    label.sprite.position.set(0, 150, 0);
    group.add(label.sprite);
    group.scale.setScalar(1.05);
    group.position.set(620, 8, 520);
    state.command = group;
    state.commandLabel = label;
    state.commandRing = holo;
    state.scene.add(group);
  }

  // ------------------------------------------------------------------ links
  function makeLink(start, end, color, radius, raised) {
    var mid = start.clone().add(end).multiplyScalar(0.5);
    mid.y += raised || 120;
    var curve = new T.QuadraticBezierCurve3(start, mid, end);
    var tube = new T.Mesh(
      new T.TubeGeometry(curve, 48, radius || 1.6, 5, false),
      new T.MeshBasicMaterial({ color: color, transparent: true, opacity: 0.18, blending: T.AdditiveBlending, depthWrite: false })
    );
    state.scene.add(tube);
    var particles = [];
    for (var i = 0; i < 4; i++) {
      var dot = new T.Mesh(new T.SphereGeometry(3.4, 8, 8), new T.MeshBasicMaterial({ color: color, transparent: true, opacity: 0.9 }));
      state.scene.add(dot);
      particles.push({ mesh: dot, offset: i / 4 });
    }
    var item = { curve: curve, tube: tube, particles: particles, color: color };
    state.links.push(item);
    return item;
  }

  function buildLinks() {
    var commandTop = new T.Vector3(700, 72, 560);
    var patchTop = new T.Vector3(0, 250, 0);
    makeLink(commandTop, patchTop, 0x38e1ff, 1.4, 180);
    state.evidenceLink = makeLink(new T.Vector3(0, 220, 0), commandTop, 0xffbe55, 2.4, 300);
    state.evidenceLink.tube.material.opacity = 0;
    state.evidenceLink.particles.forEach(function (p) { p.mesh.visible = false; });

    var geo = new T.BufferGeometry().setFromPoints([new T.Vector3(), new T.Vector3()]);
    var line = new T.Line(geo, new T.LineBasicMaterial({ color: 0x38e6a4, transparent: true, opacity: 0.32 }));
    state.uavLink = line;
    state.scene.add(line);
  }

  // ------------------------------------------------------------------ sky / change overlay
  function buildSky() {
    var canvas = document.createElement("canvas");
    canvas.width = 1024;
    canvas.height = 512;
    var ctx = canvas.getContext("2d");
    var g = ctx.createLinearGradient(0, 0, 0, 512);
    g.addColorStop(0, "#01040a");
    g.addColorStop(0.46, "#04101a");
    g.addColorStop(0.66, "#092130");
    g.addColorStop(0.78, "#0a1a24");
    g.addColorStop(1, "#02070b");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 1024, 512);
    var band = ctx.createLinearGradient(0, 300, 0, 420);
    band.addColorStop(0, "rgba(56,180,230,0)");
    band.addColorStop(0.5, "rgba(56,180,230,0.10)");
    band.addColorStop(1, "rgba(56,180,230,0)");
    ctx.fillStyle = band;
    ctx.fillRect(0, 300, 1024, 120);
    var tex = new T.CanvasTexture(canvas);
    tex.encoding = T.sRGBEncoding;
    var sky = new T.Mesh(
      new T.SphereGeometry(9000, 36, 20),
      new T.MeshBasicMaterial({ map: tex, side: T.BackSide, fog: false, depthWrite: false })
    );
    state.scene.add(sky);
  }

  function buildMountains() {
    var group = new T.Group();
    var mat = new T.MeshStandardMaterial({ color: 0x040c13, roughness: 1, metalness: 0, flatShading: true });
    for (var i = 0; i < 18; i++) {
      var a = i / 18 * Math.PI * 2 + Math.random() * 0.22;
      var radius = 3300 + Math.random() * 950;
      var h = 90 + Math.random() * 190;
      var w = 260 + Math.random() * 460;
      var peak = new T.Mesh(new T.ConeGeometry(w, h, 5 + (i % 3)), mat);
      peak.position.set(Math.cos(a) * radius, h / 2 - 110, Math.sin(a) * radius);
      peak.rotation.y = Math.random() * Math.PI;
      group.add(peak);
    }
    state.scene.add(group);
  }

  function makeChangeTexture() {
    var canvas = document.createElement("canvas");
    canvas.width = 256;
    canvas.height = 256;
    var ctx = canvas.getContext("2d");
    var g = ctx.createRadialGradient(128, 128, 4, 128, 128, 126);
    g.addColorStop(0, "rgba(255,214,130,0.95)");
    g.addColorStop(0.28, "rgba(255,178,74,0.72)");
    g.addColorStop(0.58, "rgba(255,126,42,0.30)");
    g.addColorStop(0.82, "rgba(255,92,30,0.08)");
    g.addColorStop(1, "rgba(255,80,20,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 256, 256);
    for (var i = 0; i < 22; i++) {
      var x = 42 + Math.random() * 172;
      var y = 42 + Math.random() * 172;
      var r = 8 + Math.random() * 28;
      var spot = ctx.createRadialGradient(x, y, 0, x, y, r);
      spot.addColorStop(0, "rgba(255,235,170,0.24)");
      spot.addColorStop(1, "rgba(255,180,80,0)");
      ctx.fillStyle = spot;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
    var tex = new T.CanvasTexture(canvas);
    tex.encoding = T.sRGBEncoding;
    return tex;
  }

  function buildChangeGlow() {
    var group = new T.Group();
    var plane = new T.Mesh(
      new T.PlaneGeometry(1, 1),
      new T.MeshBasicMaterial({
        map: makeChangeTexture(), transparent: true, opacity: 0,
        blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide
      })
    );
    plane.rotation.x = -Math.PI / 2;
    plane.position.y = 2.4;
    group.add(plane);
    var ring = new T.Mesh(
      new T.RingGeometry(0.47, 0.5, 96),
      new T.MeshBasicMaterial({ color: 0xffbe55, transparent: true, opacity: 0, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false })
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 3.2;
    group.add(ring);
    var ring2 = new T.Mesh(
      new T.RingGeometry(0.38, 0.385, 96),
      new T.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false })
    );
    ring2.rotation.x = -Math.PI / 2;
    ring2.position.y = 3.4;
    group.add(ring2);
    group.visible = false;
    state.changeGroup = group;
    state.changeGlow = plane;
    state.changeRing = ring;
    state.changeRing2 = ring2;
    state.scene.add(group);
  }

  function updateChangeGlow(area, mode, preliminary) {
    if (!state.changeGroup || !state.patch || !state.patch.center_lonlat) return;
    var value = Number(area);
    if (!isFinite(value) || value <= 0) {
      state.changeArea = 0;
      state.changeGroup.visible = false;
      return;
    }
    var p = lonLatToLocal(state.patch.center_lonlat[0], state.patch.center_lonlat[1]);
    var size = clamp(Math.sqrt(value) * (mode === "scene" ? 58 : 62), 120, 560);
    state.changeArea = value;
    state.changeMode = mode || "precise";
    state.changeGroup.visible = true;
    state.changeGroup.position.set(p.x, terrainHeight(p.x, p.z) + 4, p.z);
    state.changeGlow.scale.set(size, size, 1);
    state.changeRing.scale.set(size, size, 1);
    state.changeRing2.scale.set(size, size, 1);
    state.changeBase = preliminary ? 0.16 : 0.30;
    state.changeRingBase = preliminary ? 0.30 : 0.52;
    state.changeGlow.material.opacity = state.changeBase;
    state.changeRing.material.opacity = state.changeRingBase;
    state.changeRing2.material.opacity = preliminary ? 0.10 : 0.24;
  }

  function buildScanSweep() {
    var canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 64;
    var ctx = canvas.getContext("2d");
    var g = ctx.createLinearGradient(0, 0, 512, 0);
    g.addColorStop(0, "rgba(56,225,255,0)");
    g.addColorStop(0.40, "rgba(56,225,255,0.04)");
    g.addColorStop(0.48, "rgba(120,245,255,0.34)");
    g.addColorStop(0.50, "rgba(230,255,255,0.72)");
    g.addColorStop(0.52, "rgba(120,245,255,0.34)");
    g.addColorStop(0.60, "rgba(56,225,255,0.04)");
    g.addColorStop(1, "rgba(56,225,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 512, 64);
    var tex = new T.CanvasTexture(canvas);
    var plane = new T.Mesh(
      new T.PlaneGeometry(AOI_W * 1.25, AOI_H * 1.25),
      new T.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.05, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide })
    );
    plane.rotation.x = -Math.PI / 2;
    plane.position.set(0, terrainHeight(0, 0) + 34, 0);
    state.scanSweep = plane;
    state.scene.add(plane);
  }

  // ------------------------------------------------------------------ stars/dust
  function buildStars() {
    var count = 1600;
    var positions = new Float32Array(count * 3);
    var colors = new Float32Array(count * 3);
    for (var i = 0; i < count; i++) {
      var r = 3600 + Math.random() * 4200;
      var theta = Math.random() * Math.PI * 2;
      var phi = Math.acos(2 * Math.random() - 1);
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = Math.abs(r * Math.cos(phi)) * 0.7 + 120;
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
      var tint = Math.random();
      colors[i * 3] = tint > 0.7 ? 0.55 : 0.8;
      colors[i * 3 + 1] = tint > 0.7 ? 0.9 : 0.9;
      colors[i * 3 + 2] = 1.0;
    }
    var geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.BufferAttribute(positions, 3));
    geo.setAttribute("color", new T.BufferAttribute(colors, 3));
    var mat = new T.PointsMaterial({ size: 3.2, vertexColors: true, transparent: true, opacity: 0.85, depthWrite: false, blending: T.AdditiveBlending });
    state.stars = new T.Points(geo, mat);
    state.scene.add(state.stars);
  }

  function buildDust() {
    var count = 380;
    var positions = new Float32Array(count * 3);
    var data = [];
    for (var i = 0; i < count; i++) {
      var x = (Math.random() - 0.5) * 1400;
      var z = (Math.random() - 0.5) * 1000;
      var y = terrainHeight(x, z) + Math.random() * 100;
      positions[i * 3] = x;
      positions[i * 3 + 1] = y;
      positions[i * 3 + 2] = z;
      data.push({ speed: 2 + Math.random() * 7, drift: (Math.random() - 0.5) * 5 });
    }
    var geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.BufferAttribute(positions, 3));
    var mat = new T.PointsMaterial({ color: 0xd8c2a4, size: 3.2, transparent: true, opacity: 0.32, depthWrite: false });
    state.dust = new T.Points(geo, mat);
    state.dustData = data;
    state.scene.add(state.dust);
  }

  function buildHorizonGlow() {
    var canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 256;
    var ctx = canvas.getContext("2d");
    var g = ctx.createRadialGradient(256, 160, 10, 256, 160, 250);
    g.addColorStop(0, "rgba(56,180,230,0.42)");
    g.addColorStop(0.45, "rgba(24,80,120,0.16)");
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 512, 256);
    var tex = new T.CanvasTexture(canvas);
    var mat = new T.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, blending: T.AdditiveBlending, opacity: 0.65 });
    var sprite = new T.Sprite(mat);
    sprite.position.set(0, 90, -1900);
    sprite.scale.set(3000, 1500, 1);
    state.scene.add(sprite);
  }

  // ------------------------------------------------------------------ init
  function init(canvas) {
    if (!T) throw new Error("Three.js 未加载");
    state.canvas = canvas;
    state.scene = new T.Scene();
    state.scene.background = new T.Color(0x050f19);
    state.scene.fog = new T.FogExp2(0x050f19, 0.00017);
    state.camera = new T.PerspectiveCamera(45, 1, 1, 16000);
    state.camera.position.set(1200, 1450, 1750);
    state.renderer = new T.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
    state.renderer.setPixelRatio(Math.min(Math.max(window.devicePixelRatio || 1, 1.5), 2));
    state.renderer.setSize(1, 1, false);
    state.renderer.outputEncoding = T.sRGBEncoding;
    state.renderer.toneMapping = T.ACESFilmicToneMapping;
    state.renderer.toneMappingExposure = 1.28;
    state.clock = new T.Clock();

    state.scene.add(new T.HemisphereLight(0xa8d8ff, 0x0b1a24, 0.68));
    var ambient = new T.AmbientLight(0x4f6f88, 0.66);
    state.scene.add(ambient);
    var sun = new T.DirectionalLight(0xfff2d6, 1.35);
    sun.position.set(1200, 1800, 900);
    state.scene.add(sun);
    var rim = new T.DirectionalLight(0x3f8cff, 0.45);
    rim.position.set(-1000, 600, -800);
    state.scene.add(rim);
    var pitLight = new T.PointLight(0x38e1ff, 0.45, 1600);
    pitLight.position.set(0, 220, 0);
    state.scene.add(pitLight);

    buildSky();
    buildMountains();
    buildHorizonGlow();
    buildStars();
    buildTerrain(null);
    buildAoiFrame();
    buildAoiGrid();
    buildDust();
    buildSiteProps();
    buildPatchMarker();
    buildChangeGlow();
    buildScanSweep();
    buildScanRing();
    buildUav();
    buildSatellite();
    buildCommand();
    buildLinks();
    state.routeGroup = new T.Group();
    state.scene.add(state.routeGroup);
    state.lookTarget = new T.Vector3(0, 40, 0);

    bindEvents();
    resize();
    state.ready = true;
    animate();
  }

  function resize() {
    if (!state.renderer || !state.canvas) return;
    var parent = state.canvas.parentElement;
    var w = Math.max(1, parent.clientWidth);
    var h = Math.max(1, parent.clientHeight);
    state.renderer.setPixelRatio(Math.min(Math.max(window.devicePixelRatio || 1, 1.5), 2));
    state.renderer.setSize(w, h, false);
    state.camera.aspect = w / h;
    state.camera.updateProjectionMatrix();
  }

  function bindEvents() {
    var canvas = state.canvas;
    canvas.style.touchAction = "none";
    canvas.addEventListener("pointerdown", function (event) {
      state.dragging = true;
      state.dragMoved = false;
      state.autoRotate = false;
      state.lastPointer.x = event.clientX;
      state.lastPointer.y = event.clientY;
      canvas.setPointerCapture(event.pointerId);
      canvas.style.cursor = "grabbing";
    });
    canvas.addEventListener("pointermove", function (event) {
      if (!state.dragging) return;
      var dx = event.clientX - state.lastPointer.x;
      var dy = event.clientY - state.lastPointer.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) state.dragMoved = true;
      state.lastPointer.x = event.clientX;
      state.lastPointer.y = event.clientY;
      if (state.cameraMode === "follow") setCameraMode("orbit");
      state.orbit.theta -= dx * 0.005;
      state.orbit.phi = clamp(state.orbit.phi - dy * 0.004, 0.18, 1.32);
    });
    function endDrag(event) {
      state.dragging = false;
      canvas.style.cursor = "grab";
      if (canvas.hasPointerCapture && canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    }
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      state.autoRotate = false;
      state.orbit.radius = clamp(state.orbit.radius * (1 + event.deltaY * 0.0011), 620, 3600);
    }, { passive: false });
  }

  // ------------------------------------------------------------------ camera
  function setCameraMode(mode) {
    state.cameraMode = mode;
    if (mode === "top") {
      state.orbit.theta = 0.0;
      state.orbit.phi = 0.14;
      state.orbit.radius = 2120;
      state.autoRotate = false;
    } else if (mode === "scene") {
      state.orbit.theta = 0.92;
      state.orbit.phi = 1.22;
      state.orbit.radius = 1820;
      state.autoRotate = false;
    } else if (mode === "orbit") {
      state.orbit.theta = 0.58;
      state.orbit.phi = 1.02;
      state.orbit.radius = 2150;
      state.autoRotate = true;
    }
  }

  function updateCamera(dt) {
    if (!state.camera) return;
    var desired = new T.Vector3();
    var target = new T.Vector3(0, 40, 0);
    if (state.cameraMode === "follow" && state.uav && state.uav.visible) {
      var p = state.uav.position;
      var tangent = state.curve ? state.curve.getTangentAt(clamp(state.uavT || 0, 0, 1)) : new T.Vector3(0, 0, 1);
      desired.copy(p).addScaledVector(tangent, -190).add(new T.Vector3(0, 80, 0));
      target.copy(p);
    } else {
      if (state.autoRotate) state.orbit.theta += dt * 0.045;
      var sinPhi = Math.sin(state.orbit.phi);
      desired.set(
        state.orbit.radius * sinPhi * Math.sin(state.orbit.theta),
        state.orbit.radius * Math.cos(state.orbit.phi) + 40,
        state.orbit.radius * sinPhi * Math.cos(state.orbit.theta)
      );
    }
    var k = state.cameraMode === "follow" ? 0.12 : 0.07;
    state.camera.position.lerp(desired, k);
    state.lookTarget.lerp(target, k);
    state.camera.lookAt(state.lookTarget);
  }

  // ------------------------------------------------------------------ run
  function playRun(duration) {
    if (!state.curve) return false;
    state.run.active = true;
    state.run.start = performance.now();
    state.run.duration = duration || 8400;
    state.run.phase = -1;
    state.uavT = 0;
    state.autoRotate = false;
    if (state.uav) {
      state.uav.visible = true;
      state.uav.position.copy(state.routePoints[0]).add(new T.Vector3(0, 8, 0));
    }
    if (state.scanCone) state.scanCone.visible = false;
    if (state.scanRing) state.scanRing.material.opacity = 0;
    if (state.evidenceLink) {
      state.evidenceLink.tube.material.opacity = 0;
      state.evidenceLink.particles.forEach(function (p) { p.mesh.visible = false; });
    }
    return true;
  }

  function stopRun() {
    state.run.active = false;
    if (state.scanCone) state.scanCone.visible = false;
    if (state.scanRing) state.scanRing.material.opacity = 0;
  }

  function runPhase(progress) {
    if (progress < 0.16) return 1;
    if (progress < 0.28) return 1;
    if (progress < 0.66) return 2;
    if (progress < 0.80) return 3;
    if (progress < 0.92) return 4;
    return 5;
  }

  function updateRun(now) {
    if (!state.run.active || !state.curve) return;
    var elapsed = now - state.run.start;
    var progress = clamp(elapsed / state.run.duration, 0, 1);
    if (state.onPhase) {
      var phase = runPhase(progress);
      if (phase !== state.run.phase) {
        state.run.phase = phase;
        state.onPhase(phase, progress);
      }
    }
    var uavT = 0;
    if (progress > 0.28) uavT = clamp((progress - 0.28) / 0.38, 0, 1);
    state.uavT = uavT;
    if (state.uav) {
      var point = state.curve.getPointAt(uavT);
      var tangent = state.curve.getTangentAt(uavT);
      state.uav.position.copy(point).add(new T.Vector3(0, 8, 0));
      var look = point.clone().add(tangent);
      state.uav.lookAt(look);
      if (state.onPhase && state.run.phase >= 1 && state.run.phase <= 3) {
        state.uav.visible = true;
      }
    }
    if (state.scanCone) state.scanCone.visible = progress >= 0.66 && progress < 0.84;
    if (state.scanRing) {
      var scan = progress >= 0.66 && progress < 0.84;
      state.scanRing.material.opacity = scan ? 0.35 + 0.35 * Math.abs(Math.sin(elapsed * 0.008)) : 0;
      var s = scan ? 0.75 + 0.35 * ((elapsed % 1200) / 1200) : 1;
      state.scanRing.scale.set(s, s, s);
    }
    if (state.satBeam) {
      state.satBeam.material.opacity = (progress > 0 && progress < 0.2) ? 0.10 + 0.06 * Math.abs(Math.sin(elapsed * 0.01)) : 0.035;
    }
    if (state.evidenceLink) {
      var show = progress >= 0.80;
      state.evidenceLink.tube.material.opacity = show ? 0.25 : 0;
      state.evidenceLink.particles.forEach(function (p) { p.mesh.visible = show; });
    }
    if (progress >= 1) {
      state.run.active = false;
      if (state.onPhase) state.onPhase(6, 1);
      if (state.scanCone) state.scanCone.visible = false;
      if (state.scanRing) state.scanRing.material.opacity = 0;
    }
  }

  // ------------------------------------------------------------------ data
  function setData(data) {
    if (!state.ready) return;
    data = data || {};
    if (data.center) state.center = data.center;
    if (data.patch) {
      state.patch = data.patch;
      updatePatchMarker(data.patch, true);
      updateSiteProps(data.patch);
      updateChangeGlow(data.changedArea || data.patch.area_ha, data.mode || "precise", !data.changedArea);
    } else {
      state.patch = null;
      if (state.changeGroup) state.changeGroup.visible = false;
    }
    if (data.route) {
      state.route = data.route;
      state.relHeight = data.route.rel_height_m || 120;
      buildRoute(data.route);
    } else {
      state.route = null;
      buildRoute(null);
    }
    if (data.textureUrl) loadTexture(data.textureUrl);
    if (state.uavLink) state.uavLink.visible = !!(state.uav && state.uav.visible);
    state.autoRotate = true;
    resize();
  }

  // ------------------------------------------------------------------ animate
  function animate() {
    requestAnimationFrame(animate);
    if (!state.ready) return;
    var dt = Math.min(0.05, state.clock.getDelta());
    state.t += dt;

    // 卫星轨道
    if (state.satellite) {
      state.satelliteAngle += dt * 0.045;
      var a = state.satelliteAngle;
      state.satellite.position.set(Math.cos(a) * 760, 650 + Math.sin(a * 2) * 22, Math.sin(a) * 640 - 320);
      state.satellite.rotation.y = -a;
      if (state.satLabel) state.satLabel.sprite.position.set(-260, -30, 0);
      if (state.satBeacon) {
        state.satBeacon.material.opacity = 0.25 + 0.75 * (Math.sin(state.t * 3.4) > 0.4 ? 1 : 0);
      }
      updateSatBeam();
    }

    // 地形网格呼吸
    if (state.terrainWire) state.terrainWire.material.opacity = 0.09 + 0.035 * Math.sin(state.t * 0.9);

    // 图斑脉冲
    if (state.patchRing) {
      var p = 1 + 0.035 * Math.sin(state.t * 1.8);
      state.patchRing.scale.set(p, p, p);
      state.patchRing.material.opacity = 0.52 + 0.18 * Math.sin(state.t * 1.8);
      state.patchBeam.material.opacity = 0.11 + 0.05 * Math.sin(state.t * 1.4);
    }

    // 变化区辉光 / 扫描带
    if (state.changeGroup && state.changeGroup.visible) {
      var pulse = 0.78 + 0.22 * Math.sin(state.t * 2.2);
      state.changeGlow.material.opacity = state.changeBase * pulse;
      state.changeRing.material.opacity = state.changeRingBase * pulse;
      state.changeRing2.material.opacity = (state.changeBase * 0.9) * (1.4 - pulse);
      state.changeRing.rotation.z += dt * 0.18;
      state.changeRing2.rotation.z -= dt * 0.26;
    }
    if (state.scanSweep) {
      var span = AOI_W * 1.25;
      var sx = -span / 2 + ((state.t * 0.055) % 1) * span;
      state.scanSweep.position.x = sx;
      var want = state.run.active ? 0.16 : 0.045;
      state.scanSweep.material.opacity += (want - state.scanSweep.material.opacity) * 0.06;
    }
    if (state.beacon) {
      var bs = 1 + 0.35 * Math.sin(state.t * 4.2);
      state.beacon.scale.set(bs, bs, bs);
      state.beacon.material.opacity = 0.65 + 0.35 * Math.sin(state.t * 4.2);
    }
    if (state.navLights.length) {
      state.navLights[0].material.opacity = 0.35 + 0.65 * (Math.sin(state.t * 3.1) > 0 ? 1 : 0);
      state.navLights[1].material.opacity = 0.35 + 0.65 * (Math.sin(state.t * 2.7 + 1.4) > 0 ? 1 : 0);
    }

    // 航线粒子
    for (var i = 0; i < state.routeParticles.length; i++) {
      var item = state.routeParticles[i];
      var t = (state.t * item.speed + item.offset) % 1;
      if (state.curve) item.mesh.position.copy(state.curve.getPointAt(t));
    }

    // 旋翼
    if (state.rotors) {
      var spin = state.run.active ? 0.55 : 0.12;
      for (i = 0; i < state.rotors.length; i++) state.rotors[i].rotation.y += spin;
    }

    // 无人机待机浮动
    if (state.uav && (!state.route || (!state.run.active && state.uavT === 0))) {
      state.uav.position.y += Math.sin(state.t * 1.6) * 0.08;
    }

    // 指挥节点
    if (state.commandRing) state.commandRing.rotation.z += dt * 0.35;

    // 链路粒子
    for (i = 0; i < state.links.length; i++) {
      var link = state.links[i];
      for (var j = 0; j < link.particles.length; j++) {
        var part = link.particles[j];
        var tt = (state.t * 0.16 + part.offset) % 1;
        part.mesh.position.copy(link.curve.getPointAt(tt));
      }
    }

    // UAV 控制链路
    if (state.uavLink && state.uav && state.uav.visible && state.command) {
      var positions = state.uavLink.geometry.attributes.position;
      positions.setXYZ(0, state.uav.position.x, state.uav.position.y, state.uav.position.z);
      positions.setXYZ(1, 620, 60, 520);
      positions.needsUpdate = true;
      state.uavLink.visible = true;
    } else if (state.uavLink) {
      state.uavLink.visible = false;
    }

    // 星点
    if (state.stars) state.stars.rotation.y += dt * 0.003;

    // 尘粒
    if (state.dust && state.dustData) {
      var arr = state.dust.geometry.attributes.position.array;
      for (i = 0; i < state.dustData.length; i++) {
        arr[i * 3 + 1] += state.dustData[i].speed * dt;
        arr[i * 3] += state.dustData[i].drift * dt;
        if (arr[i * 3 + 1] > 140) {
          arr[i * 3 + 1] = -20;
          arr[i * 3] = (Math.random() - 0.5) * 1400;
        }
      }
      state.dust.geometry.attributes.position.needsUpdate = true;
    }

    updateRun(performance.now());
    updateCamera(dt);
    state.renderer.render(state.scene, state.camera);
  }

  // ------------------------------------------------------------------ api
  window.AirCase3D = {
    init: init,
    resize: resize,
    setData: setData,
    setPatch: function (patch) { updatePatchMarker(patch, true); updateSiteProps(patch); state.patch = patch; },
    setResult: function (result) {
      result = result || {};
      updateChangeGlow(result.changedArea, result.mode || "precise", false);
      if (state.curve && state.uav) {
        state.uav.position.copy(state.curve.getPointAt(0.55)).add(new T.Vector3(0, 10, 0));
      }
    },
    setCamera: setCameraMode,
    playRun: playRun,
    stopRun: stopRun,
    setOnPhase: function (fn) { state.onPhase = fn; },
    isReady: function () { return state.ready; }
  };
})();
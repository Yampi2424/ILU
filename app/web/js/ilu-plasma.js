/**
 * I.L.U. — Motor de Plasma
 *
 * Presencia viva de energía/orgánica/renderizada en Canvas 2D.
 * Reemplaza el orbe CSS por una nube de plasma con partículas,
 * filamentos, halo difuso, respiración orgánica y silueta femenina
 * que se forma temporalmente a partir de la propia energía.
 *
 * Tecnología: Canvas 2D + Simplex Noise procedural.
 * Sin dependencias externas, sin assets, 100% local.
 */

window.ILUPlasma = (function () {
  'use strict';

  // =====================================================================
  //  SIMPLEX NOISE 2D — versión compacta para formas orgánicas
  // =====================================================================

  var GRAD2 = [
    [1,1],[-1,1],[1,-1],[-1,-1],
    [1,0],[-1,0],[0,1],[0,-1]
  ];

  function SimplexNoise(seed) {
    this.perm = new Uint8Array(512);
    var p = new Uint8Array(256);
    var i;
    for (i = 0; i < 256; i++) p[i] = i;
    seed = seed || Math.random() * 65536;
    for (i = 255; i > 0; i--) {
      seed = (seed * 16807 + 0) % 2147483647;
      var j = seed % (i + 1);
      var tmp = p[i]; p[i] = p[j]; p[j] = tmp;
    }
    for (i = 0; i < 512; i++) this.perm[i] = p[i & 255];
  }

  SimplexNoise.prototype.noise2D = function (xin, yin) {
    var F2 = 0.5 * (Math.sqrt(3) - 1);
    var G2 = (3 - Math.sqrt(3)) / 6;
    var s = (xin + yin) * F2;
    var i = Math.floor(xin + s);
    var j = Math.floor(yin + s);
    var t = (i + j) * G2;
    var X0 = i - t, Y0 = j - t;
    var x0 = xin - X0, y0 = yin - Y0;
    var i1, j1;
    if (x0 > y0) { i1 = 1; j1 = 0; } else { i1 = 0; j1 = 1; }
    var x1 = x0 - i1 + G2, y1 = y0 - j1 + G2;
    var x2 = x0 - 1 + 2 * G2, y2 = y0 - 1 + 2 * G2;
    var ii = i & 255, jj = j & 255;
    var n0 = 0, n1 = 0, n2 = 0;
    var t0 = 0.5 - x0 * x0 - y0 * y0;
    if (t0 > 0) {
      t0 *= t0;
      var gi0 = this.perm[ii + this.perm[jj]] % 8;
      n0 = t0 * t0 * (GRAD2[gi0][0] * x0 + GRAD2[gi0][1] * y0);
    }
    var t1 = 0.5 - x1 * x1 - y1 * y1;
    if (t1 > 0) {
      t1 *= t1;
      var gi1 = this.perm[ii + i1 + this.perm[jj + j1]] % 8;
      n1 = t1 * t1 * (GRAD2[gi1][0] * x1 + GRAD2[gi1][1] * y1);
    }
    var t2 = 0.5 - x2 * x2 - y2 * y2;
    if (t2 > 0) {
      t2 *= t2;
      var gi2 = this.perm[ii + 1 + this.perm[jj + 1]] % 8;
      n2 = t2 * t2 * (GRAD2[gi2][0] * x2 + GRAD2[gi2][1] * y2);
    }
    return 70 * (n0 + n1 + n2);
  };

  // =====================================================================
  //  UTILIDADES
  // =====================================================================

  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  function parseColor(hex) {
    hex = hex.replace('#', '');
    return {
      r: parseInt(hex.substring(0, 2), 16),
      g: parseInt(hex.substring(2, 4), 16),
      b: parseInt(hex.substring(4, 6), 16)
    };
  }

  function lerpColor(c1, c2, t) {
    return {
      r: Math.round(lerp(c1.r, c2.r, t)),
      g: Math.round(lerp(c1.g, c2.g, t)),
      b: Math.round(lerp(c1.b, c2.b, t))
    };
  }

  function rgbStr(c, a) {
    return 'rgba(' + c.r + ',' + c.g + ',' + c.b + ',' + a + ')';
  }

  // =====================================================================
  //  OBJECT POOLS
  // =====================================================================

  function Particle() {
    this.x = 0; this.y = 0;
    this.vx = 0; this.vy = 0;
    this.life = 0; this.maxLife = 0;
    this.size = 0; this.baseSize = 0;
    this.alpha = 0; this.hueShift = 0;
  }

  Particle.prototype.init = function (x, y, cfg) {
    this.x = x; this.y = y;
    this.vx = (Math.random() - 0.5) * cfg.speed;
    this.vy = (Math.random() - 0.5) * cfg.speed;
    this.life = Math.random() * cfg.life;
    this.maxLife = cfg.life;
    this.baseSize = cfg.minSize + Math.random() * (cfg.maxSize - cfg.minSize);
    this.size = this.baseSize;
    this.alpha = 0;
    this.hueShift = (Math.random() - 0.5) * 30;
  };

  Particle.prototype.update = function (dt, noiseX, noiseY) {
    this.life += dt;
    if (this.life >= this.maxLife) return false;
    var nx = this.noiseVal(noiseX, noiseY, 0.5);
    var ny = this.noiseVal(noiseX + 31, noiseY + 31, 0.5);
    this.vx += nx * 12 * dt;
    this.vy += ny * 12 * dt;
    this.x += this.vx * dt;
    this.y += this.vy * dt;
    this.vx *= 0.985;
    this.vy *= 0.985;
    var p = this.life / this.maxLife;
    this.alpha = p < 0.15 ? p / 0.15 : p > 0.75 ? (1 - p) / 0.25 : 1;
    this.size = this.baseSize * (0.4 + 0.6 * (1 - p));
    return true;
  };

  Particle.prototype.noiseVal = function (x, y, s) {
    return Math.sin(x * s) * Math.cos(y * s) * 0.5;
  };

  Particle.prototype.draw = function (ctx, color) {
    if (this.alpha < 0.01) return;
    var a = this.alpha * 0.85;
    ctx.globalAlpha = a;
    ctx.fillStyle = rgbStr(color, 1);
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.size, 0, 6.2832);
    ctx.fill();
    if (this.size > 1.2) {
      ctx.globalAlpha = a * 0.25;
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * 3, 0, 6.2832);
      ctx.fill();
    }
  };

  function Filament() {
    this.x1 = 0; this.y1 = 0;
    this.x2 = 0; this.y2 = 0;
    this.cx = 0; this.cy = 0;
    this.alpha = 0; this.width = 0;
    this.life = 0; this.maxLife = 0;
    this.drift = 0; this.phase = 0;
  }

  Filament.prototype.init = function (cx, cy, r, cfg) {
    this.x1 = cx; this.y1 = cy;
    var angle = Math.random() * 6.2832;
    var dist = r * (0.5 + Math.random() * 0.5);
    this.x2 = cx + Math.cos(angle) * dist;
    this.y2 = cy + Math.sin(angle) * dist;
    var cAngle = angle + (Math.random() - 0.5) * 1.2;
    var cDist = dist * (0.25 + Math.random() * 0.35);
    this.cx = cx + Math.cos(cAngle) * cDist;
    this.cy = cy + Math.sin(cAngle) * cDist;
    this.width = cfg.minWidth + Math.random() * (cfg.maxWidth - cfg.minWidth);
    this.life = Math.random() * cfg.life;
    this.maxLife = cfg.life;
    this.drift = (Math.random() - 0.5) * 20;
    this.phase = Math.random() * 6.2832;
    this.alpha = 0;
  };

  Filament.prototype.update = function (dt, time) {
    this.life += dt;
    if (this.life >= this.maxLife) return false;
    this.cx += Math.sin(time * 0.5 + this.phase) * this.drift * dt;
    this.cy += Math.cos(time * 0.3 + this.phase) * this.drift * dt;
    var p = this.life / this.maxLife;
    this.alpha = p < 0.12 ? p / 0.12 : p > 0.7 ? (1 - p) / 0.3 : 1;
    return true;
  };

  Filament.prototype.draw = function (ctx, color) {
    if (this.alpha < 0.01) return;
    var a = this.alpha * 0.5;
    ctx.globalAlpha = a * 0.2;
    ctx.strokeStyle = rgbStr(color, 1);
    ctx.lineWidth = this.width * 4;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(this.x1, this.y1);
    ctx.quadraticCurveTo(this.cx, this.cy, this.x2, this.y2);
    ctx.stroke();
    ctx.globalAlpha = a;
    ctx.lineWidth = this.width;
    ctx.beginPath();
    ctx.moveTo(this.x1, this.y1);
    ctx.quadraticCurveTo(this.cx, this.cy, this.x2, this.y2);
    ctx.stroke();
  };

  function Hair() {
    this.a = 0;      // ángulo de anclaje en la cabeza
    this.len = 0;    // factor de longitud
    this.curl = 0;   // fase de rizo
    this.sway = 0;   // amplitud de vaivén
    this.alpha = 0;
    this.life = 0; this.maxLife = 0;
  }

  Hair.prototype.init = function () {
    this.a = (Math.random() - 0.5) * Math.PI * 1.1;
    this.len = 0.5 + Math.random() * 0.75;
    this.curl = Math.random() * 6.2832;
    this.sway = 0.3 + Math.random() * 0.5;
    this.life = Math.random() * 4;
    this.maxLife = 4;
    this.alpha = 0;
  };

  Hair.prototype.update = function (dt) {
    this.life += dt;
    if (this.life >= this.maxLife) return false;
    var p = this.life / this.maxLife;
    this.alpha = p < 0.15 ? p / 0.15 : p > 0.7 ? (1 - p) / 0.3 : 1;
    return true;
  };

  /**
   * Dibuja un mechón de cabello de energía que nace en la cabeza y
   * fluye hacia afuera/abajo con movimiento independiente.
   */
  Hair.prototype.draw = function (ctx, hx, hy, hr, radius, color, s, time) {
    if (this.alpha < 0.01) return;

    var a = this.a;
    var ax = hx + Math.cos(a) * hr;
    var ay = hy + Math.sin(a) * hr * 0.8;

    // Dirección hacia afuera y arriba (cabello flotante)
    var dirX = Math.cos(a);
    var dirY = Math.sin(a) - 1.15;
    var dl = Math.sqrt(dirX * dirX + dirY * dirY) || 1;
    dirX /= dl; dirY /= dl;

    var L = radius * this.len;
    var swayX = Math.sin(time * 0.7 + this.curl) * this.sway * L * 0.18;
    var swayY = Math.cos(time * 0.55 + this.curl * 1.3) * this.sway * L * 0.08;

    var endX = ax + dirX * L + swayX;
    var endY = ay + dirY * L + swayY;
    var cpx = ax + dirX * L * 0.5 + swayX * 0.5 + Math.cos(time * 0.5 + this.curl) * L * 0.1;
    var cpy = ay + dirY * L * 0.5 + Math.sin(time * 0.4 + this.curl) * L * 0.1;

    var alpha = this.alpha * s * 0.75;

    // Halo ancho del mechón
    ctx.globalAlpha = alpha * 0.25;
    ctx.strokeStyle = rgbStr(color, 1);
    ctx.lineWidth = radius * 0.035;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.quadraticCurveTo(cpx, cpy, endX, endY);
    ctx.stroke();

    // Núcleo fino del mechón
    ctx.globalAlpha = alpha;
    ctx.lineWidth = radius * 0.012;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.quadraticCurveTo(cpx, cpy, endX, endY);
    ctx.stroke();
  };

  // =====================================================================
  //  SILHOUETTE — coord. normalizadas de la figura femenina
  // =====================================================================

  var SILHOUETTE_POINTS = [
    // Cabeza (parte superior)
    { x:  0.00, y: -0.96 },
    { x:  0.06, y: -0.95 },
    { x:  0.12, y: -0.91 },
    { x:  0.15, y: -0.85 },
    // Mejilla / rostro
    { x:  0.16, y: -0.78 },
    { x:  0.13, y: -0.72 },
    // Mandíbula
    { x:  0.09, y: -0.68 },
    // Cuello
    { x:  0.06, y: -0.64 },
    { x:  0.05, y: -0.58 },
    // Hombro (curva elegante)
    { x:  0.12, y: -0.55 },
    { x:  0.22, y: -0.54 },
    { x:  0.26, y: -0.52 },
    // Brazo derecho (colgando)
    { x:  0.27, y: -0.45 },
    { x:  0.26, y: -0.36 },
    { x:  0.24, y: -0.27 },
    // Cintura (marcada)
    { x:  0.20, y: -0.20 },
    { x:  0.16, y: -0.12 },
    { x:  0.15, y: -0.03 },
    // Cadera
    { x:  0.18, y:  0.05 },
    { x:  0.22, y:  0.13 },
    { x:  0.21, y:  0.22 },
    // Muslo
    { x:  0.18, y:  0.34 },
    { x:  0.14, y:  0.46 },
    // Rodilla
    { x:  0.10, y:  0.56 },
    // Pantorrilla
    { x:  0.07, y:  0.66 },
    { x:  0.05, y:  0.76 },
    // Tobillo / pie
    { x:  0.04, y:  0.84 },
    { x:  0.04, y:  0.88 },
    // --- Lado izquierdo (espejo) ---
    { x: -0.04, y:  0.88 },
    { x: -0.04, y:  0.84 },
    { x: -0.05, y:  0.76 },
    { x: -0.07, y:  0.66 },
    { x: -0.10, y:  0.56 },
    { x: -0.14, y:  0.46 },
    { x: -0.18, y:  0.34 },
    { x: -0.21, y:  0.22 },
    { x: -0.22, y:  0.13 },
    { x: -0.18, y:  0.05 },
    { x: -0.15, y: -0.03 },
    { x: -0.16, y: -0.12 },
    { x: -0.20, y: -0.20 },
    // Brazo izquierdo
    { x: -0.24, y: -0.27 },
    { x: -0.26, y: -0.36 },
    { x: -0.27, y: -0.45 },
    // Hombro izquierdo
    { x: -0.26, y: -0.52 },
    { x: -0.22, y: -0.54 },
    { x: -0.12, y: -0.55 },
    // Cuello izq
    { x: -0.05, y: -0.58 },
    { x: -0.06, y: -0.64 },
    // Mandíbula izq
    { x: -0.09, y: -0.68 },
    { x: -0.13, y: -0.72 },
    // Mejilla izq
    { x: -0.16, y: -0.78 },
    { x: -0.15, y: -0.85 },
    { x: -0.12, y: -0.91 },
    { x: -0.06, y: -0.95 }
  ];

  // Pre-computar ángulos y radios de la silueta
  var SIL_POLAR = [];
  var SIL_CX = 0, SIL_SCALE = 1.0;
  for (var si = 0; si < SILHOUETTE_POINTS.length; si++) {
    var sp = SILHOUETTE_POINTS[si];
    var sx = (sp.x - SIL_CX) * SIL_SCALE;
    var sy = sp.y * SIL_SCALE;
    SIL_POLAR.push({
      angle: Math.atan2(sy, sx),
      radius: Math.sqrt(sx * sx + sy * sy)
    });
  }

  /**
   * Radio de la silueta en un ángulo dado (interpolación lineal).
   */
  function silhouetteRadius(angle) {
    // Normalizar ángulo a [-PI, PI]
    while (angle > Math.PI) angle -= 6.2832;
    while (angle < -Math.PI) angle += 6.2832;

    var best = 0;
    var minDist = 999;
    for (var i = 0; i < SIL_POLAR.length; i++) {
      var d = Math.abs(SIL_POLAR[i].angle - angle);
      if (d > Math.PI) d = 6.2832 - d;
      if (d < minDist) { minDist = d; best = i; }
    }

    // Interpolar entre los dos puntos más cercanos
    var next = (best + 1) % SIL_POLAR.length;
    var a1 = SIL_POLAR[best].angle;
    var a2 = SIL_POLAR[next].angle;
    var diff = a2 - a1;
    if (diff > Math.PI) diff -= 6.2832;
    if (diff < -Math.PI) diff += 6.2832;
    var t = (diff !== 0) ? clamp((angle - a1) / diff, 0, 1) : 0;

    return lerp(SIL_POLAR[best].radius, SIL_POLAR[next].radius, t);
  }

  // =====================================================================
  //  PLASMA BODY — renderizado del blob orgánico
  // =====================================================================

  /**
   * Cuerpo de plasma único. El contorno del propio plasma se deforma
   * hacia la silueta (silAmount ∈ [0,1]) — la figura ES el plasma, no
   * una forma superpuesta. Se conserva noise residual para que los
   * bordes nunca queden perfectamente definidos.
   */
  function renderBody(ctx, cx, cy, radius, time, noise, density, color, glowColor, silAmount) {
    var points = Math.max(36, Math.floor(density * 45));
    var s = clamp(silAmount, 0, 1);

    // --- Capa 1: glow exterior (halo) ---
    ctx.globalCompositeOperation = 'screen';
    var grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * (1.5 + s * 0.25));
    grd.addColorStop(0, rgbStr(glowColor, 0.08 + s * 0.04));
    grd.addColorStop(0.5, rgbStr(glowColor, 0.03));
    grd.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grd;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * (1.5 + s * 0.25), 0, 6.2832);
    ctx.fill();

    // --- Capa 2: contorno principal (plasma → silueta) ---
    ctx.beginPath();
    for (var i = 0; i <= points; i++) {
      var angle = (i / points) * 6.2832;
      var n1 = noise.noise2D(
        Math.cos(angle) * 1.1 + time * 0.12,
        Math.sin(angle) * 1.1 + time * 0.09
      );
      var n2 = noise.noise2D(
        Math.cos(angle) * 2.2 + time * 0.08 + 5,
        Math.sin(angle) * 2.2 + time * 0.06 + 5
      ) * 0.35;
      // Plasma puro: forma irregular
      var plasmaR = radius * (0.78 + (n1 + n2) * 0.3);
      // Silueta: radio con noise residual (bordes etéreos)
      var silN = noise.noise2D(
        Math.cos(angle) * 2 + time * 0.08,
        Math.sin(angle) * 2 + time * 0.05
      ) * 0.07;
      var silR = (silhouetteRadius(angle) + silN) * radius;
      // Interpolación: la energía se organiza hacia la figura
      var r = lerp(plasmaR, silR, s);
      var px = cx + Math.cos(angle) * r;
      var py = cy + Math.sin(angle) * r;
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.closePath();

    var bodyGrd = ctx.createRadialGradient(cx, cy - radius * s * 0.15, 0, cx, cy, radius);
    bodyGrd.addColorStop(0, rgbStr(color, 0.6 + s * 0.05));
    bodyGrd.addColorStop(0.35, rgbStr(color, 0.4));
    bodyGrd.addColorStop(0.7, rgbStr(glowColor, 0.16));
    bodyGrd.addColorStop(1, rgbStr(glowColor, 0.03));
    ctx.fillStyle = bodyGrd;
    ctx.fill();

    // --- Capa 3: núcleo interior brillante (esqueleto de energía) ---
    ctx.beginPath();
    for (var j = 0; j <= points; j++) {
      var angle2 = (j / points) * 6.2832;
      var n3 = noise.noise2D(
        Math.cos(angle2) * 1.5 + time * 0.15 + 10,
        Math.sin(angle2) * 1.5 + time * 0.11 + 10
      );
      var plasmaR2 = radius * (0.48 + n3 * 0.14);
      var silR2 = silhouetteRadius(angle2) * radius * 0.55;
      var r2 = lerp(plasmaR2, silR2, s);
      var px2 = cx + Math.cos(angle2) * r2;
      var py2 = cy + Math.sin(angle2) * r2 - radius * s * 0.1;
      if (j === 0) ctx.moveTo(px2, py2);
      else ctx.lineTo(px2, py2);
    }
    ctx.closePath();

    var innerGrd = ctx.createRadialGradient(cx, cy - radius * s * 0.2, 0, cx, cy, radius * 0.55);
    innerGrd.addColorStop(0, rgbStr(color, 0.5));
    innerGrd.addColorStop(0.5, rgbStr(glowColor, 0.18));
    innerGrd.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = innerGrd;
    ctx.fill();

    ctx.globalCompositeOperation = 'source-over';
  }

  /**
   * Concentraciones de energía internas (cabeza, hombros, cintura,
   * caderas) que hacen legible la figura sin ser un cuerpo sólido.
   * Aparecen dentro del plasma a medida que se organiza.
   */
  function renderAnatomy(ctx, cx, cy, radius, color, glowColor, s) {
    if (s < 0.3) return;
    var a = clamp((s - 0.3) / 0.7, 0, 1);

    ctx.globalCompositeOperation = 'screen';

    // Cabeza
    _anatomyOrb(ctx, cx, cy - radius * 0.86, radius * 0.17, color, glowColor, a * 0.9);
    // Hombros (banda ancha)
    _anatomyBand(ctx, cx, cy - radius * 0.52, radius * 0.3, radius * 0.12, color, a * 0.55);
    // Torso / pecho
    _anatomyBand(ctx, cx, cy - radius * 0.3, radius * 0.16, radius * 0.1, color, a * 0.5);
    // Cintura (estrecha)
    _anatomyBand(ctx, cx, cy - radius * 0.05, radius * 0.11, radius * 0.08, color, a * 0.6);
    // Caderas
    _anatomyBand(ctx, cx, cy + radius * 0.12, radius * 0.2, radius * 0.1, color, a * 0.5);

    ctx.globalCompositeOperation = 'source-over';
  }

  function _anatomyOrb(ctx, cx, cy, r, color, glowColor, a) {
    var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 2.2);
    g.addColorStop(0, rgbStr(color, 0.5 * a));
    g.addColorStop(0.5, rgbStr(glowColor, 0.18 * a));
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 2.2, 0, 6.2832);
    ctx.fill();
  }

  function _anatomyBand(ctx, cx, cy, rx, ry, color, a) {
    var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rx * 1.6);
    g.addColorStop(0, rgbStr(color, 0.4 * a));
    g.addColorStop(0.6, rgbStr(color, 0.15 * a));
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(1, ry / rx);
    ctx.beginPath();
    ctx.arc(0, 0, rx * 1.6, 0, 6.2832);
    ctx.fill();
    ctx.restore();
  }

  // =====================================================================
  //  ILUPlasma — módulo principal
  // =====================================================================

  var _canvas, _ctx, _running = false;
  var _state = 'idle', _targetState = 'idle';
  var _time = 0, _lastFrame = 0;
  var _width = 0, _height = 0, _dpr = 1;
  var _plasmaRadius = 0;
  var _noise = new SimplexNoise(Date.now());

  // Parámetros interpolados (smooth)
  var _speed = 0.4;
  var _amplitude = 0.28;
  var _density = 0.7;
  var _tintR = 80, _tintG = 70, _tintB = 230;

  // Energía viva de voz [0..1]: hace latir el plasma con la voz
  // (del usuario mientras escucha, de I.L.U. mientras responde).
  var _energy = 0, _tEnergy = 0;

  // Objetivos de interpolación
  var _tSpeed = 0.4, _tAmplitude = 0.28, _tDensity = 0.7;
  var _tR = 80, _tG = 70, _tB = 230;

  // Silhouette
  var _silhouette = 0, _tSilhouette = 0;
  var _silTimer = 0, _silInterval = 45;
  var _silHold = 0;

  // Pool
  var _particles = [], _filaments = [], _hairs = [];
  var HAIR_COUNT = 16;

  // Performance
  var _frames = 0, _fpsTime = 0, _fps = 60;

  // =====================================================================
  //  CONFIGURACIÓN POR ESTADO
  // =====================================================================

  var STATE_CONFIG = {
    idle: {
      speed: 0.55, amplitude: 0.3, density: 0.75,
      r: 95, g: 85, b: 235,
      particles: 80, pSpeed: 9, pLife: 4.2, pMin: 0.5, pMax: 2.4,
      filaments: 9, fLife: 5.5, fWidth: 0.7
    },
    listening: {
      speed: 0.6, amplitude: 0.32, density: 0.8,
      r: 100, g: 80, b: 240,
      particles: 85, pSpeed: 10, pLife: 3.8, pMin: 0.5, pMax: 2.5,
      filaments: 11, fLife: 5, fWidth: 0.7
    },
    thinking: {
      speed: 1.3, amplitude: 0.45, density: 1.0,
      r: 100, g: 120, b: 255,
      particles: 120, pSpeed: 18, pLife: 2.5, pMin: 0.4, pMax: 2.8,
      filaments: 15, fLife: 3.5, fWidth: 0.9
    },
    working: {
      speed: 1.0, amplitude: 0.38, density: 0.9,
      r: 120, g: 80, b: 250,
      particles: 100, pSpeed: 14, pLife: 3.0, pMin: 0.5, pMax: 2.6,
      filaments: 13, fLife: 4, fWidth: 0.8
    },
    responding: {
      speed: 0.8, amplitude: 0.35, density: 0.85,
      r: 130, g: 100, b: 255,
      particles: 95, pSpeed: 12, pLife: 3.2, pMin: 0.6, pMax: 2.8,
      filaments: 10, fLife: 4.5, fWidth: 0.7
    },
    learning: {
      speed: 0.5, amplitude: 0.3, density: 0.75,
      r: 60, g: 180, b: 120,
      particles: 80, pSpeed: 8, pLife: 4.0, pMin: 0.5, pMax: 2.4,
      filaments: 9, fLife: 5.5, fWidth: 0.7
    },
    authorization: {
      speed: 0.2, amplitude: 0.15, density: 0.5,
      r: 210, g: 155, b: 30,
      particles: 40, pSpeed: 4, pLife: 5.5, pMin: 0.6, pMax: 2.0,
      filaments: 5, fLife: 7, fWidth: 0.5
    },
    error: {
      speed: 0.9, amplitude: 0.4, density: 0.7,
      r: 220, g: 60, b: 60,
      particles: 65, pSpeed: 16, pLife: 2.0, pMin: 0.4, pMax: 2.6,
      filaments: 7, fLife: 3, fWidth: 0.8
    },
    emergency: {
      speed: 1.6, amplitude: 0.55, density: 1.0,
      r: 240, g: 30, b: 30,
      particles: 110, pSpeed: 22, pLife: 1.5, pMin: 0.5, pMax: 3.0,
      filaments: 14, fLife: 2.5, fWidth: 1.0
    }
  };

  // =====================================================================
  //  INICIALIZACIÓN
  // =====================================================================

  function init() {
    _canvas = document.getElementById('iluPlasma');
    if (!_canvas) return false;
    _ctx = _canvas.getContext('2d');
    _resize();
    window.addEventListener('resize', _resize);
    // Atajo de evaluación (dev): tecla S → fuerza la silueta
    window.addEventListener('keydown', function (e) {
      if (e.key === 's' || e.key === 'S') forceSilhouette();
    });
    return true;
  }

  function _resize() {
    if (!_canvas) return;
    var rect = _canvas.parentElement.getBoundingClientRect();
    _dpr = Math.min(window.devicePixelRatio || 1, 2);
    _width = rect.width;
    _height = rect.height;
    _canvas.width = _width * _dpr;
    _canvas.height = _height * _dpr;
    _canvas.style.width = _width + 'px';
    _canvas.style.height = _height + 'px';
    _ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);
    _plasmaRadius = Math.min(_width, _height) * 0.42;
  }

  // =====================================================================
  //  ESTADOS
  // =====================================================================

  function setState(newState) {
    if (!STATE_CONFIG[newState]) return;
    _state = newState;
    _targetState = newState;

    var c = STATE_CONFIG[newState];
    _tSpeed = c.speed;
    _tAmplitude = c.amplitude;
    _tDensity = c.density;
    _tR = c.r; _tG = c.g; _tB = c.b;

    // Ajustar pool
    _adjustPool(c.particles, c);

    // Silhouette: intervalos más cortos para poder evaluarla (dev)
    if (newState === 'thinking' || newState === 'working' || newState === 'responding') {
      _silInterval = 8 + Math.random() * 12;
    } else if (newState === 'idle') {
      _silInterval = 12 + Math.random() * 15;
    } else {
      _silInterval = 999; // No aparecer en otros estados
    }
  }

  function _adjustPool(targetCount, cfg) {
    // Partículas
    while (_particles.length < targetCount) {
      var p = new Particle();
      var angle = Math.random() * 6.2832;
      var dist = Math.random() * _plasmaRadius * 0.8;
      p.init(
        _width / 2 + Math.cos(angle) * dist,
        _height / 2 + Math.sin(angle) * dist,
        { speed: cfg.pSpeed, life: cfg.pLife, minSize: cfg.pMin, maxSize: cfg.pMax }
      );
      p.life = Math.random() * p.maxLife;
      _particles.push(p);
    }
    // Filamentos
    while (_filaments.length < cfg.filaments) {
      var f = new Filament();
      f.init(_width / 2, _height / 2, _plasmaRadius, {
        minWidth: 0.4, maxWidth: cfg.fWidth, life: cfg.fLife
      });
      f.life = Math.random() * f.maxLife;
      _filaments.push(f);
    }
  }

  // =====================================================================
  //  LOOP DE ANIMACIÓN
  // =====================================================================

  function start() {
    if (_running) return;
    _running = true;
    _lastFrame = performance.now();
    requestAnimationFrame(_loop);
  }

  function stop() {
    _running = false;
  }

  function _loop(now) {
    if (!_running) return;

    var dt = Math.min((now - _lastFrame) / 1000, 0.05);
    _lastFrame = now;
    _time += dt;

    // Performance
    _frames++;
    if (now - _fpsTime > 1000) {
      _fps = _frames;
      _frames = 0;
      _fpsTime = now;
    }

    _update(dt);
    _render();

    requestAnimationFrame(_loop);
  }

  function _update(dt) {
    // Interpolación suave de parámetros
    var lerpF = 1 - Math.exp(-2.5 * dt);
    _speed = lerp(_speed, _tSpeed, lerpF);
    _amplitude = lerp(_amplitude, _tAmplitude, lerpF);
    _density = lerp(_density, _tDensity, lerpF);
    _energy = lerp(_energy, _tEnergy, 1 - Math.exp(-8 * dt));
    _tintR = lerp(_tintR, _tR, lerpF);
    _tintG = lerp(_tintG, _tG, lerpF);
    _tintB = lerp(_tintB, _tB, lerpF);

    // Silhouette: sube, se mantiene (hold), se disuelve
    _silTimer += dt;
    if (_silHold > 0) {
      _silHold -= dt;
      if (_silHold <= 0) _tSilhouette = 0;
    } else if (_silhouette < 0.02 && _silTimer > _silInterval) {
      _silTimer = 0;
      _tSilhouette = 1;
      _silHold = 1.6;
    }
    var silLerp = _tSilhouette > _silhouette ? 0.9 : 0.4;
    _silhouette = lerp(_silhouette, _tSilhouette, dt * silLerp);

    // Actualizar partículas
    var cx = _width / 2;
    var cy = _height / 2;
    var cfg = STATE_CONFIG[_targetState] || STATE_CONFIG.idle;

    for (var i = _particles.length - 1; i >= 0; i--) {
      var px = _particles[i].x;
      var py = _particles[i].y;
      var noiseX = (px / _plasmaRadius) * 1.5;
      var noiseY = (py / _plasmaRadius) * 1.5;

      if (!_particles[i].update(dt * _speed, noiseX, noiseY)) {
        var angle = Math.random() * 6.2832;
        var dist = Math.random() * _plasmaRadius * 0.7;
        _particles[i].init(
          cx + Math.cos(angle) * dist,
          cy + Math.sin(angle) * dist,
          { speed: cfg.pSpeed, life: cfg.pLife, minSize: cfg.pMin, maxSize: cfg.pMax }
        );
      }
    }

    // Actualizar filamentos
    for (var j = _filaments.length - 1; j >= 0; j--) {
      if (!_filaments[j].update(dt * _speed, _time)) {
        _filaments[j].init(cx, cy, _plasmaRadius, {
          minWidth: 0.4, maxWidth: cfg.fWidth, life: cfg.fLife
        });
      }
    }

    // Cabello de energía (vive siempre; se dibuja con la silueta)
    if (_hairs.length === 0) {
      for (var hk = 0; hk < HAIR_COUNT; hk++) {
        var nh = new Hair();
        nh.init();
        _hairs.push(nh);
      }
    }
    for (var hd = 0; hd < _hairs.length; hd++) {
      if (!_hairs[hd].update(dt)) _hairs[hd].init();
    }

    // Partículas que se desprenden del cabello durante la silueta
    if (_silhouette > 0.4 && Math.random() < dt * 6) {
      var hxp = cx;
      var hyp = cy - _plasmaRadius * 0.86;
      var hrp = _plasmaRadius * 0.17;
      var ha = (Math.random() - 0.5) * Math.PI * 1.2;
      var shed = new Particle();
      shed.init(
        hxp + Math.cos(ha) * hrp * 1.4,
        hyp + Math.sin(ha) * hrp * 1.2,
        { speed: cfg.pSpeed * 1.5, life: 1.5, minSize: cfg.pMin, maxSize: cfg.pMax * 0.7 }
      );
      shed.vx = Math.cos(ha) * 22 + (Math.random() - 0.5) * 8;
      shed.vy = Math.sin(ha) * 12 + (Math.random() - 0.5) * 10 - 8;
      _particles.push(shed);
    }

    // Poda de exceso
    while (_particles.length > cfg.particles + 10) _particles.pop();
    while (_filaments.length > cfg.filaments + 2) _filaments.pop();
  }

  // =====================================================================
  //  RENDERIZADO
  // =====================================================================

  function _render() {
    _ctx.clearRect(0, 0, _width, _height);
    if (_plasmaRadius < 1) return;

    var cx = _width / 2;
    var cy = _height / 2;
    var color = { r: Math.round(_tintR), g: Math.round(_tintG), b: Math.round(_tintB) };
    var glowColor = { r: Math.min(255, color.r + 55), g: Math.min(255, color.g + 40), b: Math.min(255, color.b + 20) };

    // Silhouette blending
    var effectiveAmplitude = _amplitude * (1 - _silhouette * 0.55) * energyBoost;
    var effectiveDensity = _density * (1 + _silhouette * 0.15) + _energy * 0.25;

    // --- HALO DIFUSO ---
    // La energía viva de la voz hace latir el halo y la amplitud:
    // el plasma respira con quien habla.
    var energyBoost = 1 + _energy * 0.9;
    var breathe = Math.sin(_time * 0.5 * _speed) * (0.12 + _energy * 0.3);
    var haloR = _plasmaRadius * (1.25 + breathe + _energy * 0.12);
    var haloGrd = _ctx.createRadialGradient(cx, cy, _plasmaRadius * 0.3, cx, cy, haloR);
    haloGrd.addColorStop(0, rgbStr(color, 0.06 + _energy * 0.05));
    haloGrd.addColorStop(0.5, rgbStr(glowColor, 0.025 + _energy * 0.04));
    haloGrd.addColorStop(1, 'rgba(0,0,0,0)');
    _ctx.fillStyle = haloGrd;
    _ctx.beginPath();
    _ctx.arc(cx, cy, haloR, 0, 6.2832);
    _ctx.fill();

    // --- ANILLO DE RESONANCIA (voz real) ---
    // Onda de energía que emana de la presencia con la voz viva
    // (del usuario al escuchar, de I.L.U. al responder). Es la
    // "voz visible" del plasma: reacciona al audio real, no a un
    // ecualizador. Silencioso en reposo (_energy ≈ 0).
    if (_energy > 0.03) {
      var ringR = _plasmaRadius * (1 + _energy * 0.55);
      var ringA = Math.min(1, _energy * 1.5);
      _ctx.globalCompositeOperation = 'screen';
      _ctx.globalAlpha = ringA * 0.55;
      _ctx.strokeStyle = rgbStr(glowColor, 1);
      _ctx.lineWidth = 1.5 + _energy * 4;
      _ctx.lineCap = 'round';
      _ctx.beginPath();
      _ctx.arc(cx, cy, ringR, 0, 6.2832);
      _ctx.stroke();
      _ctx.globalAlpha = ringA * 0.2;
      _ctx.lineWidth = 8 + _energy * 6;
      _ctx.beginPath();
      _ctx.arc(cx, cy, ringR * 1.07, 0, 6.2832);
      _ctx.stroke();
      _ctx.globalAlpha = 1;
      _ctx.globalCompositeOperation = 'source-over';
    }

    // --- CUERPO DE PLASMA (el contorno se organiza hacia la silueta) ---
    renderBody(_ctx, cx, cy, _plasmaRadius, _time * _speed, _noise, effectiveDensity, color, glowColor, _silhouette);

    // --- CABELLO DE ENERGÍA (silueta) ---
    if (_silhouette > 0.25) {
      var hx = cx;
      var hy = cy - _plasmaRadius * 0.86;
      var hr = _plasmaRadius * 0.17;
      _ctx.globalCompositeOperation = 'screen';
      for (var hi = 0; hi < _hairs.length; hi++) {
        _hairs[hi].draw(_ctx, hx, hy, hr, _plasmaRadius, color, _silhouette, _time);
      }
      _ctx.globalCompositeOperation = 'source-over';
    }

    // --- ANATOMÍA (concentraciones internas de energía) ---
    renderAnatomy(_ctx, cx, cy, _plasmaRadius, color, glowColor, _silhouette);

    // --- FILAMENTOS ---
    var fAlphaMul = 1;
    if (_state === 'idle') fAlphaMul = 0.5;
    else if (_state === 'authorization') fAlphaMul = 0.35;
    else if (_state === 'error' || _state === 'emergency') fAlphaMul = 0.8;

    for (var fi = 0; fi < _filaments.length; fi++) {
      if (_silhouette > 0.3) {
        // Re-centrar filamentos hacia la silueta
        var f = _filaments[fi];
        f.x1 = cx;
        f.y1 = cy;
        var baseAngle = Math.atan2(f.y2 - cy, f.x2 - cx);
        var silR = silhouetteRadius(baseAngle) * _plasmaRadius;
        var drift = Math.sin(_time + fi * 1.7) * _plasmaRadius * 0.04;
        f.x2 = cx + Math.cos(baseAngle) * (silR + drift);
        f.y2 = cy + Math.sin(baseAngle) * (silR + drift);
      }
      _ctx.globalAlpha = fAlphaMul;
      _filaments[fi].draw(_ctx, glowColor);
    }
    _ctx.globalAlpha = 1;

    // --- PARTÍCULAS ---
    var pAlphaMul = 1;
    if (_state === 'idle') pAlphaMul = 0.7;
    else if (_state === 'authorization') pAlphaMul = 0.5;
    else if (_state === 'error' || _state === 'emergency') pAlphaMul = 0.9;

    for (var pi = 0; pi < _particles.length; pi++) {
      _ctx.globalAlpha = pAlphaMul;
      _particles[pi].draw(_ctx, glowColor);
    }
    _ctx.globalAlpha = 1;

    // --- BRILLO CENTRAL (siempre) ---
    // El núcleo se enciende con la voz viva: al hablar se vuelve más
    // luminoso, como una entidad que "cobra voz".
    var corePulse = 0.3 + Math.sin(_time * 0.8 * _speed) * 0.1 + _energy * 0.45;
    var coreGrd = _ctx.createRadialGradient(cx, cy, 0, cx, cy, _plasmaRadius * 0.25);
    coreGrd.addColorStop(0, rgbStr({ r: 255, g: 255, b: 255 }, corePulse * 0.4));
    coreGrd.addColorStop(0.4, rgbStr(glowColor, corePulse * 0.25));
    coreGrd.addColorStop(1, 'rgba(0,0,0,0)');
    _ctx.fillStyle = coreGrd;
    _ctx.beginPath();
    _ctx.arc(cx, cy, _plasmaRadius * 0.25, 0, 6.2832);
    _ctx.fill();
  }

  // =====================================================================
  //  API PÚBLICA
  // =====================================================================

  function getState() { return _state; }

  function getFPS() { return _fps; }

  function forceSilhouette() {
    _tSilhouette = 1;
    _silHold = 1.8;
    _silTimer = 0;
  }

  /**
   * Energía viva de voz [0..1]: hace latir el plasma con quien habla.
   * El llamador la alimenta cada frame (0 cuando hay silencio) y el
   * motor la suaviza/decae por sí solo.
   */
  function setEnergy(level) {
    _tEnergy = clamp(level || 0, 0, 1);
  }

  return {
    init: init,
    start: start,
    stop: stop,
    setState: setState,
    getState: getState,
    getFPS: getFPS,
    forceSilhouette: forceSilhouette,
    setEnergy: setEnergy,
    resize: _resize
  };
})();

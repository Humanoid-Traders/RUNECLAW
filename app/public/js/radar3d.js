/*
 * RC3DRadar — a self-contained pseudo-3D "sweep radar" on a tilted ground plane.
 *
 * A web3-native way to FEEL the market: real data points (RWA/meme radar tokens,
 * live signals) plotted as glowing blips on a perspective radar, lit up in
 * sequence by a rotating sweep beam. Pure Canvas 2D — no WebGL, no deps, no
 * module loading — DPR-aware, and reduced-motion safe (draws one static frame
 * and never animates). Exposed as window.RC3DRadar.
 *
 * Usage:
 *   const r = RC3DRadar.mount(canvasEl, { title:'Sector radar' });
 *   r.update([{ label:'BTC', angle:0.1, radius:0.6, intensity:0.8, up:true }, …]);
 *   r.destroy();   // stops the loop, releases the canvas
 */
(function (global) {
  'use strict';

  var reduced = function () {
    return !!(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
  };
  var TAU = Math.PI * 2;
  var TILT = 0.46;           // vertical squash → the "3D ground plane" feel
  var UP = '#31c48d', DOWN = '#f05252', GRID = 'rgba(120,150,190,0.16)', BEAM = '#4db6ff';

  function mount(canvas, opts) {
    if (!canvas || !canvas.getContext) return { update: function () {}, destroy: function () {} };
    opts = opts || {};
    var ctx = canvas.getContext('2d');
    var points = [];
    var raf = null, running = false, angle = 0, t0 = 0, dead = false;

    function size() {
      var dpr = Math.min(2, global.devicePixelRatio || 1);
      var w = canvas.clientWidth || 320, h = canvas.clientHeight || 200;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { w: w, h: h };
    }

    // Project a polar point (a=0..1 around, r=0..1 out) onto the tilted plane.
    function project(cx, cy, rx, ry, a, r) {
      var th = a * TAU - Math.PI / 2;
      return { x: cx + Math.cos(th) * rx * r, y: cy + Math.sin(th) * ry * r };
    }

    function draw(sweep) {
      var d = size();
      var cx = d.w / 2, cy = d.h * 0.56, rx = Math.min(d.w * 0.46, 260), ry = rx * TILT;
      ctx.clearRect(0, 0, d.w, d.h);

      // Rings + radial grid.
      ctx.lineWidth = 1;
      for (var k = 1; k <= 4; k++) {
        ctx.beginPath(); ctx.ellipse(cx, cy, rx * k / 4, ry * k / 4, 0, 0, TAU);
        ctx.strokeStyle = GRID; ctx.stroke();
      }
      for (var s = 0; s < 12; s++) {
        var e = project(cx, cy, rx, ry, s / 12, 1);
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(e.x, e.y);
        ctx.strokeStyle = GRID; ctx.stroke();
      }

      // Rotating sweep beam with a fading trail.
      if (sweep != null) {
        var span = 0.42;
        for (var q = 0; q < 14; q++) {
          var a2 = sweep - (q / 14) * span;
          var p = project(cx, cy, rx, ry, ((a2 % 1) + 1) % 1, 1);
          ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(p.x, p.y);
          ctx.strokeStyle = 'rgba(77,182,255,' + (0.22 * (1 - q / 14)).toFixed(3) + ')';
          ctx.lineWidth = 2; ctx.stroke();
        }
        var lead = project(cx, cy, rx, ry, ((sweep % 1) + 1) % 1, 1);
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(lead.x, lead.y);
        ctx.strokeStyle = BEAM; ctx.lineWidth = 1.5; ctx.stroke();
      }

      // Blips — brighten as the beam passes (a pulse that decays with angular gap).
      for (var i = 0; i < points.length; i++) {
        var pt = points[i];
        var a = ((pt.angle % 1) + 1) % 1;
        var pos = project(cx, cy, rx, ry, a, Math.max(0.06, Math.min(1, pt.radius)));
        var lit = 0.35 + 0.65 * (pt.intensity || 0.5);
        if (sweep != null) {
          var gap = Math.abs(((a - (((sweep % 1) + 1) % 1)) + 1.5) % 1 - 0.5) * 2; // 0=on beam
          lit = Math.max(lit * 0.55, 1 - gap);
        }
        var col = pt.up === false ? DOWN : UP;
        var rad = 2.5 + 5 * (pt.intensity || 0.5);
        var g = ctx.createRadialGradient(pos.x, pos.y, 0, pos.x, pos.y, rad * 3.2);
        g.addColorStop(0, col); g.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.globalAlpha = lit; ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(pos.x, pos.y, rad * 3.2, 0, TAU); ctx.fill();
        ctx.globalAlpha = Math.min(1, lit + 0.2); ctx.fillStyle = col;
        ctx.beginPath(); ctx.arc(pos.x, pos.y, rad, 0, TAU); ctx.fill();
        ctx.globalAlpha = 1;
        if (pt.label && (pt.intensity || 0) > 0.55) {
          ctx.fillStyle = 'rgba(210,225,245,0.85)'; ctx.font = '10px system-ui,sans-serif';
          ctx.fillText(String(pt.label), pos.x + rad + 3, pos.y + 3);
        }
      }
    }

    function frame(ts) {
      if (dead) return;
      if (!t0) t0 = ts;
      angle = (((ts - t0) / 6000) % 1);       // ~6s per revolution
      draw(angle);
      raf = global.requestAnimationFrame(frame);
    }

    function start() {
      if (running || dead) return;
      if (reduced() || !global.requestAnimationFrame) { draw(null); return; }
      running = true; t0 = 0; raf = global.requestAnimationFrame(frame);
    }

    start();
    return {
      update: function (pts) { points = Array.isArray(pts) ? pts : []; if (!running) draw(reduced() ? null : angle); },
      destroy: function () { dead = true; running = false; if (raf) global.cancelAnimationFrame(raf); }
    };
  }

  global.RC3DRadar = { mount: mount };
})(typeof window !== 'undefined' ? window : this);

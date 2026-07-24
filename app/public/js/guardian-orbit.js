/*
 * RCGuardianOrbit — the Guardian principle as a living scene: six module
 * nodes orbit a glowing core (your capital) on a tilted 3D plane, each
 * connected by a faint tether — one safety envelope, drawn literally.
 *
 * Motion carries meaning: every module is LIVE, so every node glows; hover
 * lifts a node and names it; click navigates to the tool. Depth comes from
 * the tilted orbit (far nodes smaller/dimmer, near nodes larger/brighter),
 * so the scene reads as a 3D system, not a flat ring.
 *
 * Pure Canvas 2D — no WebGL, no deps — DPR-aware, pointer-interactive, and
 * reduced-motion safe (draws one calm static frame and never animates).
 * Exposed as window.RCGuardianOrbit: mount(canvas, modules) → { destroy }.
 * modules: [{ label, emoji, href }]
 */
(function (global) {
  'use strict';

  var reduced = function () {
    return !!(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
  };
  var TAU = Math.PI * 2;
  var TILT = 0.42;                       // vertical squash → tilted plane
  var CORE = '#3fb6ff', TETHER = 'rgba(120,170,220,0.20)', RING = 'rgba(120,150,190,0.16)';

  function mount(canvas, modules) {
    if (!canvas || !canvas.getContext) return { destroy: function () {} };
    var ctx = canvas.getContext('2d');
    var raf = null, dead = false, t0 = 0, hover = -1;
    var nodes = (modules || []).map(function (m, i) {
      return { label: m.label, emoji: m.emoji, href: m.href,
        phase: (i / (modules.length || 1)) * TAU, sx: 0, sy: 0, r: 0 };
    });

    function size() {
      var dpr = Math.min(2, global.devicePixelRatio || 1);
      var w = canvas.clientWidth || 320, h = canvas.clientHeight || 220;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { w: w, h: h };
    }

    function draw(t) {
      var d = size(), w = d.w, h = d.h;
      var cx = w / 2, cy = h / 2 + 8;
      var R = Math.min(w * 0.36, h * 0.62);
      var spin = reduced() ? 0.9 : (t - t0) / 9000;     // one slow orbit ~9s
      ctx.clearRect(0, 0, w, h);

      // Orbit rings (two faint ellipses for depth).
      ctx.strokeStyle = RING; ctx.lineWidth = 1;
      [1, 0.62].forEach(function (k) {
        ctx.beginPath();
        ctx.ellipse(cx, cy, R * k, R * k * TILT, 0, 0, TAU);
        ctx.stroke();
      });

      // Depth-sort the nodes: far (top of ellipse) first, near last.
      var placed = nodes.map(function (n, i) {
        var a = n.phase + spin * TAU;
        var x = cx + Math.cos(a) * R;
        var y = cy + Math.sin(a) * R * TILT;
        var depth = (Math.sin(a) + 1) / 2;              // 0 far … 1 near
        return { n: n, i: i, x: x, y: y, depth: depth };
      }).sort(function (p, q) { return p.depth - q.depth; });

      // Tethers first (behind everything): node → core.
      placed.forEach(function (p) {
        ctx.strokeStyle = TETHER; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(cx, cy); ctx.stroke();
      });

      // The core — your capital, breathing gently.
      var pulse = reduced() ? 1 : 1 + 0.06 * Math.sin((t - t0) / 700);
      var g = ctx.createRadialGradient(cx, cy, 2, cx, cy, 26 * pulse);
      g.addColorStop(0, 'rgba(63,182,255,0.9)');
      g.addColorStop(1, 'rgba(63,182,255,0)');
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(cx, cy, 26 * pulse, 0, TAU); ctx.fill();
      ctx.fillStyle = CORE;
      ctx.beginPath(); ctx.arc(cx, cy, 5, 0, TAU); ctx.fill();

      // Modules — far nodes small/dim, near nodes big/bright; hover lifts.
      placed.forEach(function (p) {
        var isHover = p.i === hover;
        var s = 0.72 + 0.55 * p.depth + (isHover ? 0.25 : 0);
        var alpha = 0.45 + 0.55 * p.depth;
        p.n.sx = p.x; p.n.sy = p.y; p.n.r = 16 * s;
        ctx.globalAlpha = alpha;
        ctx.font = Math.round(18 * s) + 'px system-ui, sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(p.n.emoji, p.x, p.y - (isHover ? 4 : 0));
        if (isHover || reduced()) {
          ctx.globalAlpha = 1;
          ctx.font = '600 11px system-ui, sans-serif';
          ctx.fillStyle = '#cfe3f7';
          ctx.fillText(p.n.label, p.x, p.y + 20 * s);
        }
        ctx.globalAlpha = 1;
      });
    }

    function loop(t) {
      if (dead) return;
      if (!t0) t0 = t;
      draw(t);
      if (!reduced()) raf = global.requestAnimationFrame(loop);
    }

    function pick(e) {
      var rect = canvas.getBoundingClientRect();
      var x = e.clientX - rect.left, y = e.clientY - rect.top;
      var best = -1, bestD = 1e9;
      nodes.forEach(function (n, i) {
        var dx = x - n.sx, dy = y - n.sy, dd = dx * dx + dy * dy;
        if (dd < (n.r + 8) * (n.r + 8) && dd < bestD) { best = i; bestD = dd; }
      });
      return best;
    }
    function onMove(e) {
      var h2 = pick(e);
      if (h2 !== hover) {
        hover = h2;
        canvas.style.cursor = hover >= 0 ? 'pointer' : 'default';
        if (reduced()) draw(performance.now());        // static mode repaints on hover
      }
    }
    function onClick(e) {
      var i = pick(e);
      if (i >= 0 && nodes[i].href) global.location.href = nodes[i].href;
    }
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('click', onClick);

    raf = global.requestAnimationFrame(loop);
    return {
      destroy: function () {
        dead = true;
        if (raf) global.cancelAnimationFrame(raf);
        canvas.removeEventListener('pointermove', onMove);
        canvas.removeEventListener('click', onClick);
      },
    };
  }

  global.RCGuardianOrbit = { mount: mount };
})(typeof window !== 'undefined' ? window : this);

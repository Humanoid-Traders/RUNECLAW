/*
 * RCCharts — two self-contained real-data Canvas 2D charts for the dashboard.
 *
 *   RCCharts.donut(canvas, opts)      — an allocation ring (funds by source).
 *   RCCharts.underwater(canvas, opts) — an equity drawdown ("underwater") area.
 *
 * Both are pure Canvas 2D — no WebGL, no deps, no module loading — DPR-aware,
 * theme-agnostic (they read explicit colours), and reduced-motion safe: they
 * draw ONE static final frame and never animate when the user asked for reduced
 * motion. Each returns { update(data), destroy() } and, like RC3DRadar, any rAF
 * entrance loop is cancelled by destroy() so nothing leaks across a view change.
 * Visualization only — nothing here reads, moves, or trades a balance.
 *
 * Exposed as window.RCCharts.
 */
(function (global) {
  'use strict';

  var reduced = function () {
    return !!(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
  };
  var TAU = Math.PI * 2;
  // A calm, distinguishable palette used when a segment carries no colour.
  var PALETTE = ['#4db6ff', '#31c48d', '#f6a609', '#a78bfa', '#f05252',
                 '#38bdf8', '#f472b6', '#84cc16', '#fb923c', '#22d3ee'];

  function ctxOf(canvas) { return canvas && canvas.getContext ? canvas.getContext('2d') : null; }

  function sizer(canvas, ctx) {
    return function () {
      var dpr = Math.min(2, global.devicePixelRatio || 1);
      var w = canvas.clientWidth || 320, h = canvas.clientHeight || 200;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { w: w, h: h };
    };
  }

  // ── Allocation donut ──────────────────────────────────────────────────────
  // opts: { segments:[{label,value,color?}], centerLabel?, centerSub? }
  function donut(canvas, opts) {
    var ctx = ctxOf(canvas);
    if (!ctx) return { update: function () {}, destroy: function () {} };
    opts = opts || {};
    var size = sizer(canvas, ctx);
    var segs = [], center = opts.centerLabel || '', sub = opts.centerSub || '';
    var raf = null, dead = false;

    function draw(prog) {
      var d = size();
      ctx.clearRect(0, 0, d.w, d.h);
      var total = 0;
      for (var i = 0; i < segs.length; i++) total += Math.max(0, segs[i].value || 0);
      var cx = d.w / 2, cy = d.h / 2;
      var R = Math.min(d.w, d.h) / 2 - 6, r = R * 0.62;
      // Track ring so an all-zero / empty state still reads as a chart.
      ctx.beginPath(); ctx.arc(cx, cy, (R + r) / 2, 0, TAU);
      ctx.lineWidth = (R - r); ctx.strokeStyle = 'rgba(120,150,190,0.14)'; ctx.stroke();
      if (total > 0) {
        var a0 = -Math.PI / 2;
        for (var s = 0; s < segs.length; s++) {
          var frac = Math.max(0, segs[s].value || 0) / total;
          var a1 = a0 + frac * TAU * prog;
          ctx.beginPath(); ctx.arc(cx, cy, (R + r) / 2, a0, a1);
          ctx.lineWidth = (R - r);
          ctx.strokeStyle = segs[s].color || PALETTE[s % PALETTE.length];
          ctx.lineCap = 'butt'; ctx.stroke();
          a0 += frac * TAU;
        }
      }
      // Center label (total) + caption.
      if (center) {
        ctx.fillStyle = 'rgba(226,236,250,0.96)';
        ctx.font = '600 ' + Math.round(R * 0.34) + 'px system-ui,sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(String(center), cx, cy - (sub ? R * 0.12 : 0));
        if (sub) {
          ctx.fillStyle = 'rgba(150,170,200,0.85)';
          ctx.font = '500 ' + Math.round(R * 0.15) + 'px system-ui,sans-serif';
          ctx.fillText(String(sub), cx, cy + R * 0.2);
        }
        ctx.textAlign = 'start'; ctx.textBaseline = 'alphabetic';
      }
    }

    function animateIn() {
      if (reduced() || !global.requestAnimationFrame) { draw(1); return; }
      var t0 = 0;
      var step = function (ts) {
        if (dead) return;
        if (!t0) t0 = ts;
        var p = Math.min(1, (ts - t0) / 650);
        draw(1 - Math.pow(1 - p, 3));         // easeOutCubic
        if (p < 1) raf = global.requestAnimationFrame(step);
      };
      raf = global.requestAnimationFrame(step);
    }

    return {
      update: function (data) {
        data = data || {};
        segs = Array.isArray(data.segments) ? data.segments : segs;
        if (data.centerLabel != null) center = data.centerLabel;
        if (data.centerSub != null) sub = data.centerSub;
        if (dead) return;
        if (raf) { global.cancelAnimationFrame(raf); raf = null; }
        animateIn();
      },
      destroy: function () { dead = true; if (raf) global.cancelAnimationFrame(raf); raf = null; }
    };
  }

  // ── Equity drawdown ("underwater") area ───────────────────────────────────
  // opts: { points:[number] | [{v}] }. Draws % below running peak; 0 at top.
  function underwater(canvas, opts) {
    var ctx = ctxOf(canvas);
    if (!ctx) return { update: function () {}, destroy: function () {} };
    opts = opts || {};
    var size = sizer(canvas, ctx);
    var dd = [];                 // drawdown pct series (<= 0)
    var raf = null, dead = false;

    function computeDD(points) {
      var vals = (points || []).map(function (p) {
        return typeof p === 'number' ? p : (p && (p.v != null ? +p.v : +p.equity));
      }).filter(function (v) { return isFinite(v); });
      var peak = -Infinity, out = [];
      for (var i = 0; i < vals.length; i++) {
        if (vals[i] > peak) peak = vals[i];
        out.push(peak > 0 ? (vals[i] - peak) / peak * 100 : 0);
      }
      return out;
    }

    function draw(prog) {
      var d = size();
      ctx.clearRect(0, 0, d.w, d.h);
      var PAD = { l: 6, r: 46, t: 10, b: 8 };
      var innerW = d.w - PAD.l - PAD.r, innerH = d.h - PAD.t - PAD.b;
      var minDD = 0;
      for (var i = 0; i < dd.length; i++) minDD = Math.min(minDD, dd[i]);
      var span = Math.min(-0.5, minDD);           // at least a small scale so a flat 0 still frames
      var n = dd.length;
      var x = function (i) { return PAD.l + (n <= 1 ? 0 : i * (innerW / (n - 1))); };
      var y = function (v) { return PAD.t + (v / span) * innerH; };   // v<=0 → grows downward from 0

      // Gridlines at 0 and the low, with % labels on the right.
      ctx.lineWidth = 1;
      var marks = [0, span];
      for (var g = 0; g < marks.length; g++) {
        var yy = y(marks[g]);
        ctx.beginPath(); ctx.moveTo(PAD.l, yy); ctx.lineTo(d.w - PAD.r, yy);
        ctx.strokeStyle = 'rgba(120,150,190,0.16)'; ctx.stroke();
        ctx.fillStyle = 'rgba(150,170,200,0.8)'; ctx.font = '11px system-ui,sans-serif';
        ctx.fillText((marks[g] > -0.05 ? '0' : marks[g].toFixed(0)) + '%', d.w - PAD.r + 6, yy + 4);
      }
      if (n < 2) return;

      var lastI = Math.max(1, Math.round((n - 1) * prog));
      // Underwater fill from the 0 line down to the drawdown curve.
      ctx.beginPath(); ctx.moveTo(x(0), y(0));
      for (var j = 0; j <= lastI; j++) ctx.lineTo(x(j), y(dd[j]));
      ctx.lineTo(x(lastI), y(0)); ctx.closePath();
      ctx.fillStyle = 'rgba(240,82,82,0.16)'; ctx.fill();
      // Drawdown line.
      ctx.beginPath();
      for (var k = 0; k <= lastI; k++) { var m = x(k), yv = y(dd[k]); if (k) ctx.lineTo(m, yv); else ctx.moveTo(m, yv); }
      ctx.strokeStyle = '#f05252'; ctx.lineWidth = 1.6; ctx.stroke();
      // Mark the deepest point.
      var wi = 0; for (var w = 0; w <= lastI; w++) if (dd[w] < dd[wi]) wi = w;
      if (dd[wi] < -0.05) {
        ctx.beginPath(); ctx.arc(x(wi), y(dd[wi]), 3, 0, TAU); ctx.fillStyle = '#f05252'; ctx.fill();
      }
    }

    function animateIn() {
      if (reduced() || !global.requestAnimationFrame) { draw(1); return; }
      var t0 = 0;
      var step = function (ts) {
        if (dead) return;
        if (!t0) t0 = ts;
        var p = Math.min(1, (ts - t0) / 700);
        draw(p);
        if (p < 1) raf = global.requestAnimationFrame(step);
      };
      raf = global.requestAnimationFrame(step);
    }

    return {
      update: function (data) {
        data = data || {};
        if (Array.isArray(data.points)) dd = computeDD(data.points);
        if (dead) return;
        if (raf) { global.cancelAnimationFrame(raf); raf = null; }
        animateIn();
      },
      destroy: function () { dead = true; if (raf) global.cancelAnimationFrame(raf); raf = null; }
    };
  }

  global.RCCharts = { donut: donut, underwater: underwater };
})(typeof window !== 'undefined' ? window : this);

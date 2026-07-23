/*
 * RC3DRadar — a self-contained orbiting 3D "sweep radar" on a tilted ground plane.
 *
 * A web3-native way to FEEL the market: real data points (RWA/meme radar tokens,
 * live signals) plotted as glowing blips on a slowly-orbiting perspective radar,
 * lit in sequence by a rotating sweep beam. Each blip is LIFTED off the plane by
 * its elevation (|momentum|) with a vertical stem + ground shadow, so the scene
 * reads as depth, not a flat disc. Strong blips fire an expanding "contact ping"
 * when the beam crosses them; a faint starfield drifts behind for parallax; and
 * the whole plane yaws like a camera orbit. Pointer hover inspects a blip.
 *
 * Pure Canvas 2D — no WebGL, no deps, no module loading — DPR-aware, and
 * reduced-motion safe (draws one calm static frame and never animates).
 * Exposed as window.RC3DRadar. Back-compatible API:
 *   const r = RC3DRadar.mount(canvasEl, {});
 *   r.update([{ label:'BTC', angle:0.1, radius:0.6, intensity:0.8, up:true,
 *               elev:0.7 }, …]);   // elev optional (falls back to intensity)
 *   r.destroy();
 */
(function (global) {
  'use strict';

  var reduced = function () {
    return !!(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
  };
  var TAU = Math.PI * 2;
  var TILT = 0.46;           // vertical squash → the "3D ground plane" feel
  var UP = '#31c48d', DOWN = '#f05252', GRID = 'rgba(120,150,190,0.16)', BEAM = '#4db6ff';

  // Tiny seeded PRNG so the starfield is stable across frames (no reshuffle).
  function rng(seed) {
    var s = seed >>> 0;
    return function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  }

  function mount(canvas, opts) {
    if (!canvas || !canvas.getContext) return { update: function () {}, destroy: function () {} };
    opts = opts || {};
    var ctx = canvas.getContext('2d');
    var points = [];
    var raf = null, running = false, t0 = 0, dead = false;
    var pings = [];            // active contact rings {x,y,born,color}
    var lastLit = [];          // per-point beam-gap memory (rising-edge ping trigger)
    var hover = -1;            // hovered point index (-1 none)
    var stars = null;          // lazily built starfield [{a,r,ph,mag}]

    function size() {
      var dpr = Math.min(2, global.devicePixelRatio || 1);
      var w = canvas.clientWidth || 320, h = canvas.clientHeight || 200;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { w: w, h: h };
    }

    function buildStars() {
      var r = rng(1337), out = [];
      for (var i = 0; i < 70; i++) {
        out.push({ a: r(), r: 0.2 + 1.15 * r(), ph: r() * TAU, mag: 0.25 + 0.6 * r() });
      }
      stars = out;
    }

    // Project a polar point (a=0..1 around, rr=0..1 out) onto the tilted plane,
    // with the scene yaw applied so the whole disc orbits like a camera move.
    function project(cx, cy, rx, ry, a, rr, yaw) {
      var th = (a + yaw) * TAU - Math.PI / 2;
      return { x: cx + Math.cos(th) * rx * rr, y: cy + Math.sin(th) * ry * rr,
               depth: Math.sin(th) };   // depth: -1 far … +1 near (front of disc)
    }

    function drawStarfield(d, tsec) {
      if (!stars) buildStars();
      for (var i = 0; i < stars.length; i++) {
        var st = stars[i];
        var x = d.w * st.a, y = (d.h * 0.5) * (st.r % 1) + 2;
        var tw = 0.5 + 0.5 * Math.sin(tsec * 1.3 + st.ph);
        ctx.globalAlpha = st.mag * (0.35 + 0.65 * tw) * 0.5;
        ctx.fillStyle = '#9fb8e0';
        ctx.fillRect(x, y, 1.2, 1.2);
      }
      ctx.globalAlpha = 1;
    }

    function draw(sweep, tsec, animate) {
      var d = size();
      // Center sits a little above mid-height and the lift is bounded to the
      // headroom above it, so tall blips never clip the top and the full ground
      // ellipse stays visible below.
      var cx = d.w / 2, cy = d.h * 0.62, rx = Math.min(d.w * 0.46, 300), ry = rx * TILT;
      var lift = Math.min(ry * 1.35, cy - 14);    // max vertical lift for elev=1
      var yaw = animate ? (tsec * 0.035) % 1 : 0; // slow camera orbit of the plane
      ctx.clearRect(0, 0, d.w, d.h);

      if (animate) drawStarfield(d, tsec);

      // Rings (concentric) + radial grid, both yawing with the scene.
      ctx.lineWidth = 1;
      for (var k = 1; k <= 4; k++) {
        ctx.beginPath(); ctx.ellipse(cx, cy, rx * k / 4, ry * k / 4, 0, 0, TAU);
        ctx.strokeStyle = GRID; ctx.stroke();
      }
      for (var s = 0; s < 12; s++) {
        var e = project(cx, cy, rx, ry, s / 12, 1, yaw);
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(e.x, e.y);
        ctx.strokeStyle = GRID; ctx.stroke();
      }
      // A soft horizon glow at the disc's far edge sells the tilt.
      var horizon = ctx.createLinearGradient(0, cy - ry, 0, cy + ry);
      horizon.addColorStop(0, 'rgba(77,182,255,0.05)');
      horizon.addColorStop(0.5, 'rgba(77,182,255,0)');
      ctx.fillStyle = horizon;
      ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, 0, TAU); ctx.fill();

      // Rotating sweep beam with a fading trail (relative to the yawing plane).
      if (sweep != null) {
        var span = 0.42;
        for (var q = 0; q < 16; q++) {
          var a2 = sweep - (q / 16) * span;
          var p = project(cx, cy, rx, ry, ((a2 % 1) + 1) % 1, 1, yaw);
          ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(p.x, p.y);
          ctx.strokeStyle = 'rgba(77,182,255,' + (0.20 * (1 - q / 16)).toFixed(3) + ')';
          ctx.lineWidth = 2; ctx.stroke();
        }
        var lead = project(cx, cy, rx, ry, ((sweep % 1) + 1) % 1, 1, yaw);
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(lead.x, lead.y);
        ctx.strokeStyle = BEAM; ctx.lineWidth = 1.5; ctx.stroke();
      }

      // Resolve every blip's screen position + lit level first, so we can draw
      // ground shadows/stems behind, then depth-sort the glowing heads.
      var sw = sweep != null ? (((sweep % 1) + 1) % 1) : null;
      var resolved = [];
      for (var i = 0; i < points.length; i++) {
        var pt = points[i];
        var a = ((pt.angle % 1) + 1) % 1;
        var rr = Math.max(0.06, Math.min(1, pt.radius));
        var ground = project(cx, cy, rx, ry, a, rr, yaw);
        var elev = pt.elev == null ? (pt.intensity || 0.5) : Math.max(0, Math.min(1, pt.elev));
        var headY = ground.y - elev * lift * (0.35 + 0.65 * rr);
        var lit = 0.32 + 0.68 * (pt.intensity || 0.5);
        var gap = 1;
        if (sw != null) {
          gap = Math.abs(((a - sw) + 1.5) % 1 - 0.5) * 2;   // 0 = on the beam
          lit = Math.max(lit * 0.5, 1 - gap);
        }
        // Continuous twinkle so the field feels alive between sweeps.
        if (animate) lit *= 0.82 + 0.18 * Math.sin(tsec * 2.2 + i * 1.7);
        // Rising-edge contact ping when the beam first crosses a strong blip.
        if (animate && sw != null) {
          var was = lastLit[i] == null ? 1 : lastLit[i];
          if (gap < 0.05 && was >= 0.05 && (pt.intensity || 0) > 0.5) {
            pings.push({ x: ground.x, y: headY, born: tsec, color: pt.up === false ? DOWN : UP });
            if (pings.length > 24) pings.shift();
            // Surface the contact so the host can keep a live "contact log"
            // (last movers the beam swept). Best-effort; never blocks the frame.
            if (typeof opts.onContact === 'function') {
              try { opts.onContact({ label: pt.label, up: pt.up !== false, intensity: pt.intensity || 0 }); }
              catch (e) { /* decorative */ }
            }
          }
          lastLit[i] = gap;
        }
        resolved.push({ pt: pt, gx: ground.x, gy: ground.y, hx: ground.x, hy: headY,
                        depth: ground.depth, lit: lit, rr: rr, elev: elev, idx: i });
      }
      // Painter's order: far (depth −1) first, near (depth +1) last.
      resolved.sort(function (a2, b2) { return a2.depth - b2.depth; });

      // Ground shadows + vertical stems (behind the heads).
      for (var j = 0; j < resolved.length; j++) {
        var rz = resolved[j];
        ctx.globalAlpha = 0.18 + 0.12 * rz.rr;
        ctx.fillStyle = 'rgba(10,16,28,0.9)';
        ctx.beginPath(); ctx.ellipse(rz.gx, rz.gy, 4 + 3 * rz.rr, (4 + 3 * rz.rr) * TILT, 0, 0, TAU); ctx.fill();
        if (rz.elev > 0.04) {
          ctx.globalAlpha = 0.25 + 0.35 * rz.lit;
          ctx.strokeStyle = rz.pt.up === false ? DOWN : UP;
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(rz.gx, rz.gy); ctx.lineTo(rz.hx, rz.hy); ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;

      // Contact pings (expanding fading rings), drawn under the heads.
      if (animate) {
        for (var pI = pings.length - 1; pI >= 0; pI--) {
          var pg = pings[pI];
          var age = tsec - pg.born;
          if (age > 1.1) { pings.splice(pI, 1); continue; }
          var prog = age / 1.1;
          ctx.globalAlpha = (1 - prog) * 0.6;
          ctx.strokeStyle = pg.color; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.ellipse(pg.x, pg.y, 4 + prog * 26, (4 + prog * 26) * TILT, 0, 0, TAU); ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }

      // Glowing heads (depth-sorted) + labels for strong/hovered blips.
      for (var m = 0; m < resolved.length; m++) {
        var r2 = resolved[m];
        var col = r2.pt.up === false ? DOWN : UP;
        var rad = 2.5 + 5 * (r2.pt.intensity || 0.5) + (r2.idx === hover ? 2 : 0);
        var g = ctx.createRadialGradient(r2.hx, r2.hy, 0, r2.hx, r2.hy, rad * 3.4);
        g.addColorStop(0, col); g.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.globalAlpha = r2.lit; ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(r2.hx, r2.hy, rad * 3.4, 0, TAU); ctx.fill();
        ctx.globalAlpha = Math.min(1, r2.lit + 0.2); ctx.fillStyle = col;
        ctx.beginPath(); ctx.arc(r2.hx, r2.hy, rad, 0, TAU); ctx.fill();
        if (r2.idx === hover) {   // crisp hover ring
          ctx.globalAlpha = 1; ctx.strokeStyle = '#eaf2ff'; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(r2.hx, r2.hy, rad + 3, 0, TAU); ctx.stroke();
        }
        ctx.globalAlpha = 1;
        if (r2.pt.label && ((r2.pt.intensity || 0) > 0.55 || r2.idx === hover)) {
          ctx.fillStyle = r2.idx === hover ? '#eaf2ff' : 'rgba(210,225,245,0.85)';
          ctx.font = (r2.idx === hover ? '600 11px' : '10px') + ' system-ui,sans-serif';
          ctx.fillText(String(r2.pt.label), r2.hx + rad + 3, r2.hy + 3);
        }
      }
      // Remember head positions for hit-testing on hover.
      _heads = resolved;
    }

    var _heads = [];
    function onMove(ev) {
      var rect = canvas.getBoundingClientRect();
      var mx = (ev.touches ? ev.touches[0].clientX : ev.clientX) - rect.left;
      var my = (ev.touches ? ev.touches[0].clientY : ev.clientY) - rect.top;
      var best = -1, bestD = 18 * 18;
      for (var i = 0; i < _heads.length; i++) {
        var dx = _heads[i].hx - mx, dy = _heads[i].hy - my, dd = dx * dx + dy * dy;
        if (dd < bestD) { bestD = dd; best = _heads[i].idx; }
      }
      if (best !== hover) { hover = best; if (!running) draw(null, 0, false); }
      canvas.style.cursor = best >= 0 ? 'pointer' : '';
    }
    function onLeave() { if (hover !== -1) { hover = -1; if (!running) draw(null, 0, false); } }
    canvas.addEventListener('mousemove', onMove);
    canvas.addEventListener('mouseleave', onLeave);
    canvas.addEventListener('touchstart', onMove, { passive: true });

    function frame(ts) {
      if (dead) return;
      if (!t0) t0 = ts;
      var tsec = (ts - t0) / 1000;
      var sweep = ((tsec / 6) % 1);        // ~6s per sweep revolution
      draw(sweep, tsec, true);
      raf = global.requestAnimationFrame(frame);
    }

    function start() {
      if (running || dead) return;
      if (reduced() || !global.requestAnimationFrame) { draw(null, 0, false); return; }
      running = true; t0 = 0; raf = global.requestAnimationFrame(frame);
    }

    start();
    return {
      update: function (pts) { points = Array.isArray(pts) ? pts : []; lastLit = []; if (!running) draw(null, 0, false); },
      destroy: function () {
        dead = true; running = false; if (raf) global.cancelAnimationFrame(raf);
        canvas.removeEventListener('mousemove', onMove);
        canvas.removeEventListener('mouseleave', onLeave);
        canvas.removeEventListener('touchstart', onMove);
      }
    };
  }

  global.RC3DRadar = { mount: mount };
})(typeof window !== 'undefined' ? window : this);

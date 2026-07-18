/**
 * RUNECLAW ambient constellation — a lightweight drifting node network behind
 * the landing hero. Self-contained (no deps), DPR-aware, capped node count,
 * pauses when the tab is hidden or the hero scrolls out of view, and honours
 * prefers-reduced-motion (draws a single static frame, no animation loop).
 */
(function () {
  'use strict';
  var c = document.getElementById('rc-constellation');
  if (!c || !c.getContext) return;
  var ctx = c.getContext('2d');
  var host = c.parentElement || document.body;
  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var nodes = [], W = 1, H = 1, raf = 0, onscreen = true;
  var LINK = 132, LINK2 = LINK * LINK;

  function size() {
    var r = host.getBoundingClientRect();
    W = Math.max(1, r.width); H = Math.max(1, r.height);
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
    c.style.width = W + 'px'; c.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function seed() {
    var n = Math.min(70, Math.max(26, Math.round(W * H / 20000)));
    nodes = [];
    for (var i = 0; i < n; i++) {
      nodes.push({
        x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.28, vy: (Math.random() - 0.5) * 0.28,
        r: Math.random() * 1.6 + 0.7,
      });
    }
  }
  function draw(step) {
    ctx.clearRect(0, 0, W, H);
    var i, j;
    for (i = 0; i < nodes.length; i++) {
      var a = nodes[i];
      if (step) {
        a.x += a.vx; a.y += a.vy;
        if (a.x < 0 || a.x > W) a.vx *= -1;
        if (a.y < 0 || a.y > H) a.vy *= -1;
      }
      for (j = i + 1; j < nodes.length; j++) {
        var b = nodes[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < LINK2) {
          var al = (1 - Math.sqrt(d2) / LINK) * 0.22;
          ctx.strokeStyle = 'rgba(63,182,255,' + al.toFixed(3) + ')';
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    }
    for (i = 0; i < nodes.length; i++) {
      var p = nodes[i];
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.2832);
      ctx.fillStyle = 'rgba(143,217,255,0.72)'; ctx.fill();
    }
  }
  function frame() { raf = 0; draw(true); if (onscreen && !document.hidden) raf = requestAnimationFrame(frame); }
  function start() { if (!raf && onscreen && !document.hidden) raf = requestAnimationFrame(frame); }
  function stop() { if (raf) { cancelAnimationFrame(raf); raf = 0; } }

  size(); seed();
  if (reduce) { draw(false); return; } // one static frame, no loop
  start();

  var rt;
  window.addEventListener('resize', function () {
    clearTimeout(rt); rt = setTimeout(function () { size(); seed(); }, 200);
  });
  document.addEventListener('visibilitychange', function () { document.hidden ? stop() : start(); });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (es) {
      onscreen = es[0].isIntersecting; onscreen ? start() : stop();
    }, { threshold: 0 }).observe(host);
  }
})();

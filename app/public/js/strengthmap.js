/**
 * RUNECLAW — 3D Strength Map.
 * Every Bitget USDT-perp plotted in 3D by selectable factor axes, coloured by
 * long-vs-short strength (sized by open interest). Click a coin for its factor
 * breakdown and where to trade it (CEX + DEX). Public market data only — no
 * account or P&L (§4). Data-viz, not investment advice.
 *
 * Progressive enhancement: if WebGL/three fails, a 2D table renders the same
 * data. three.js is vendored under /vendor/three (the page declares the map).
 * Motion (glow, morphing, auto-orbit, count-ups) is gated on prefers-reduced-motion.
 */
const $ = (id) => document.getElementById(id);
const FACTORS = window.__SM_FACTORS;
const AXIS_OPTS = FACTORS.concat([{ key: 'volume', label: 'Volume' }, { key: 'oi', label: 'Open interest' }]);
const state = { coins: [], bias: 'long', ax: { x: 'momentum', y: 'funding', z: 'volume' }, sel: null, vmm: null, omm: null };
const REDUCED = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
const now = () => (window.performance && performance.now ? performance.now() : Date.now());

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtUsd = (v) => {
  v = Number(v) || 0; const a = Math.abs(v);
  if (a >= 1e9) return '$' + (v / 1e9).toFixed(2) + 'B';
  if (a >= 1e6) return '$' + (v / 1e6).toFixed(2) + 'M';
  if (a >= 1e3) return '$' + (v / 1e3).toFixed(1) + 'K';
  return '$' + v.toFixed(2);
};
const fmtPrice = (v) => { v = Number(v) || 0; return '$' + (v >= 1 ? v.toFixed(4) : v.toPrecision(4)); };
const pct = (v) => (v >= 0 ? '+' : '') + (Number(v) || 0).toFixed(2) + '%';
const smooth = (t) => { t = Math.max(0, Math.min(1, t)); return t * t * (3 - 2 * t); };

// ── axis / colour maths (shared by 3D + fallback) ────────────────────
function norm(v, mm) { if (!mm || mm.max <= mm.min) return 0; return ((v - mm.min) / (mm.max - mm.min)) * 2 - 1; }
function axisValue(c, key) {
  if (key === 'volume') return norm(Math.log10((c.volume_usd || 0) + 1), state.vmm);
  if (key === 'oi') return norm(Math.log10((c.oi_usd || 0) + 1), state.omm);
  const f = c.factors && c.factors[key];
  return Math.max(-1, Math.min(1, Number(f) || 0));
}
// Green (long-dominant) ↔ red (short-dominant); brightness by the chosen bias.
function coinColor(c) {
  const hue = c.dir >= 0 ? 135 : 0;               // green vs red
  const strong = (state.bias === 'long' ? c.long_score : c.short_score) / 100;
  const sat = 0.55 + 0.35 * Math.min(1, Math.abs(c.dir) * 1.6);
  const light = 0.34 + 0.30 * strong;
  return { hue, sat, light, strong };
}

// ── small animation helpers (panel) ──────────────────────────────────
function countUp(el, to, dec) {
  if (!el) return;
  if (REDUCED) { el.textContent = to.toFixed(dec); return; }
  const t0 = now(), dur = 620;
  (function step() {
    const p = Math.min(1, (now() - t0) / dur), e = 1 - Math.pow(1 - p, 3);
    el.textContent = (to * e).toFixed(dec);
    if (p < 1) requestAnimationFrame(step);
  })();
}

// ── detail panel ─────────────────────────────────────────────────────
function facBar(label, v) {
  const w = Math.min(50, Math.abs(v) * 50);
  const col = v >= 0 ? 'var(--up)' : 'var(--down)';
  const left = v >= 0 ? 50 : 50 - w;
  // Render collapsed; the frame after insert we animate to the real width.
  return `<div class="sm-frow"><span>${esc(label)}</span>`
    + `<span class="bar"><span class="mid"></span>`
    + `<i data-w="${w}" data-left="${left}" style="left:50%;width:0;background:${col}"></i></span>`
    + `<span class="val" style="color:${col}">${v >= 0 ? '+' : ''}${v.toFixed(3)}</span></div>`;
}
async function openPanel(c) {
  state.sel = c.symbol;
  const facs = FACTORS.map((f) => facBar(f.label, Number((c.factors && c.factors[f.key]) || 0))).join('');
  const body = $('smPanelBody');
  body.innerHTML = `
    <h2>${esc(c.base)}<span class="muted" style="font-size:var(--fs-sm)">USDT</span></h2>
    <div class="px">${fmtPrice(c.price)} <span class="${c.change_pct >= 0 ? 'up' : 'down'}">${pct(c.change_pct)}</span></div>
    <div class="sm-ls">
      <div class="c long${state.bias === 'long' ? ' on' : ''}"><div class="k">Long</div><div class="v">0.0</div></div>
      <div class="c short${state.bias === 'short' ? ' on' : ''}"><div class="k">Short</div><div class="v">0.0</div></div>
    </div>
    <div class="sm-stats">
      <span class="k">24h volume</span><span class="v">${fmtUsd(c.volume_usd)}</span>
      <span class="k">Open interest</span><span class="v">${fmtUsd(c.oi_usd)}</span>
      <span class="k">Funding</span><span class="v ${c.funding >= 0 ? 'up' : 'down'}">${(c.funding * 100).toFixed(4)}%</span>
      <span class="k">ΔOI</span><span class="v ${c.doi_pct >= 0 ? 'up' : 'down'}">${pct(c.doi_pct)}</span>
    </div>
    <div class="sm-fac"><div class="h">Factor breakdown</div>${facs}</div>
    <div class="sm-venues"><div class="h">Open the trade — pick a venue</div>
      <div class="sm-vgrid">
        <a class="sm-v sm-v--rc" href="/dashboard?trade=${encodeURIComponent(c.base)}&dir=${c.dir >= 0 ? 'LONG' : 'SHORT'}#trade">
          <span class="nm">Trade in RUNECLAW</span>
          <span class="tag">${c.dir >= 0 ? '▲ Long' : '▼ Short'} · paper/live</span>
          <span class="rc">◆ risk-gated</span><span class="go">Open ticket →</span></a>
      </div>
      <div class="h" style="margin-top:var(--s2)">…or on an exchange</div>
      <div class="sm-vgrid" id="smVenues"><span class="muted small">Finding venues…</span></div></div>
    <p class="sm-disc">Public Bitget market data · scores are data-viz, not investment advice. Venue links are where the coin is tradeable — RUNECLAW never auto-routes an order.</p>`;
  $('smPanel').classList.add('open');
  // Count the scores up and grow the factor bars from zero.
  countUp(body.querySelector('.c.long .v'), c.long_score, 1);
  countUp(body.querySelector('.c.short .v'), c.short_score, 1);
  const grow = () => body.querySelectorAll('.sm-frow i').forEach((el) => {
    el.style.width = el.dataset.w + '%'; el.style.left = el.dataset.left + '%';
  });
  if (REDUCED) grow(); else requestAnimationFrame(() => requestAnimationFrame(grow));
  try {
    const r = await fetch('/api/market/venues/' + encodeURIComponent(c.base), { headers: { Accept: 'application/json' } });
    const d = r.ok ? await r.json() : null;
    const vs = (d && d.venues) || [];
    $('smVenues').innerHTML = vs.map((v) =>
      `<a class="sm-v" href="${esc(v.url)}" target="_blank" rel="noopener">
        <span class="nm">${esc(v.name)}</span>
        <span class="tag ${v.type === 'DEX' ? 'dex' : ''}">${esc(v.type)} · ${esc(v.kind)}</span>
        ${v.runeclaw ? '<span class="rc">◆ RUNECLAW</span>' : ''}
        <span class="go">Trade ↗</span></a>`).join('')
      || '<span class="muted small">No venues found.</span>';
  } catch (e) { $('smVenues').innerHTML = '<span class="muted small">Venue lookup unavailable.</span>'; }
}
function closePanel() { state.sel = null; $('smPanel').classList.remove('open'); }

// ── 2D fallback (no WebGL) ───────────────────────────────────────────
function renderFallback() {
  $('smFallback').style.display = 'block';
  $('smEmpty').style.display = 'none';
  const rows = state.coins.slice().sort((a, b) => b.long_score - a.long_score).slice(0, 80).map((c) =>
    `<tr style="cursor:pointer" data-sym="${esc(c.symbol)}"><td><b>${esc(c.base)}</b></td>
      <td class="${c.change_pct >= 0 ? 'up' : 'down'}">${pct(c.change_pct)}</td>
      <td class="up">${c.long_score.toFixed(1)}</td><td class="down">${c.short_score.toFixed(1)}</td>
      <td class="${c.funding >= 0 ? 'up' : 'down'}">${(c.funding * 100).toFixed(3)}%</td></tr>`).join('');
  $('smFbBody').innerHTML = rows;
  $('smFbBody').addEventListener('click', (e) => {
    const tr = e.target.closest('[data-sym]'); if (!tr) return;
    const c = state.coins.find((x) => x.symbol === tr.dataset.sym); if (c) openPanel(c);
  });
}

// ── controls wiring ──────────────────────────────────────────────────
function fillAxisSelects() {
  ['x', 'y', 'z'].forEach((a) => {
    const sel = $('ax' + a.toUpperCase());
    sel.innerHTML = AXIS_OPTS.map((o) => `<option value="${o.key}"${o.key === state.ax[a] ? ' selected' : ''}>${o.label}</option>`).join('');
    sel.addEventListener('change', () => { state.ax[a] = sel.value; if (window.__smRelayout) window.__smRelayout(); });
  });
}
function wireBias(onChange) {
  const set = (b) => {
    state.bias = b;
    $('biasLong').setAttribute('aria-pressed', String(b === 'long'));
    $('biasShort').setAttribute('aria-pressed', String(b === 'short'));
    onChange();
  };
  $('biasLong').addEventListener('click', () => set('long'));
  $('biasShort').addEventListener('click', () => set('short'));
}

async function loadData() {
  const r = await fetch('/api/market/strengthmap?limit=240', { headers: { Accept: 'application/json' } });
  if (!r.ok) throw new Error('data');
  const d = await r.json();
  state.coins = (d && d.coins) || [];
  const vols = state.coins.map((c) => Math.log10((c.volume_usd || 0) + 1));
  const ois = state.coins.map((c) => Math.log10((c.oi_usd || 0) + 1));
  state.vmm = { min: Math.min.apply(null, vols), max: Math.max.apply(null, vols) };
  state.omm = { min: Math.min.apply(null, ois), max: Math.max.apply(null, ois) };
  $('smCount').textContent = state.coins.length + ' coins · updated ' + new Date().toLocaleTimeString();
}

// ── boot ─────────────────────────────────────────────────────────────
(async function boot() {
  $('smClose').addEventListener('click', closePanel);
  fillAxisSelects();
  try { await loadData(); } catch (e) {
    $('smEmpty').textContent = 'The market feed is unavailable right now. Try the app.'; return;
  }
  if (!state.coins.length) { $('smEmpty').textContent = 'No market data right now.'; return; }

  let THREE, OrbitControls;
  try {
    THREE = await import('three');
    ({ OrbitControls } = await import('three/addons/controls/OrbitControls.js'));
  } catch (e) { wireBias(renderFallback); renderFallback(); return; }

  const canvas = $('smCanvas');
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true }); }
  catch (e) { wireBias(renderFallback); renderFallback(); return; }
  $('smEmpty').style.display = 'none';
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x06070b, 0.012);
  const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 200);
  camera.position.set(9, 7, 13);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.autoRotate = !REDUCED; controls.autoRotateSpeed = 0.55;
  controls.minDistance = 6; controls.maxDistance = 46;

  scene.add(new THREE.AmbientLight(0x9fb0d0, 0.85));
  const key = new THREE.PointLight(0xffffff, 70, 90); key.position.set(8, 12, 8); scene.add(key);
  const rim = new THREE.PointLight(0x4a6cff, 26, 90); rim.position.set(-11, -6, -9); scene.add(rim);

  const SPREAD = 6;
  const geo = new THREE.SphereGeometry(1, 18, 14);
  const group = new THREE.Group(); scene.add(group);

  // Reference cube + a faint floor grid, so the axes read as a 3D volume.
  const box = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(SPREAD * 2, SPREAD * 2, SPREAD * 2)),
    new THREE.LineBasicMaterial({ color: 0x2a3550, transparent: true, opacity: 0.42 }));
  scene.add(box);
  const grid = new THREE.GridHelper(SPREAD * 2, 12, 0x2a3856, 0x161d30);
  grid.position.y = -SPREAD; grid.material.transparent = true; grid.material.opacity = 0.32; scene.add(grid);

  // Starfield backdrop for depth.
  (function makeStars() {
    const N = 700, pos = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const r = 34 + Math.random() * 62, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
      pos[i * 3 + 1] = r * Math.sin(ph) * Math.sin(th);
      pos[i * 3 + 2] = r * Math.cos(ph);
    }
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const stars = new THREE.Points(g, new THREE.PointsMaterial({
      color: 0x8aa0c8, size: 0.14, transparent: true, opacity: 0.5, sizeAttenuation: true, depthWrite: false }));
    scene.add(stars);
    scene.userData.stars = stars;
  })();

  // Soft radial sprite for the additive glow halos.
  const haloTex = (function () {
    const cv = document.createElement('canvas'); cv.width = cv.height = 64;
    const g = cv.getContext('2d');
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.25, 'rgba(255,255,255,0.5)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(cv);
  })();

  // Persistent node per symbol so relayout/bias/refresh MORPH rather than snap.
  const nodes = new Map();
  let pickables = [];
  let createSeq = 0;
  const tgtColor = new THREE.Color(), tgtEmis = new THREE.Color();

  function targetFor(c) {
    return {
      x: axisValue(c, state.ax.x) * SPREAD,
      y: axisValue(c, state.ax.y) * SPREAD,
      z: axisValue(c, state.ax.z) * SPREAD,
      s: 0.12 + 0.42 * ((norm(Math.log10((c.oi_usd || 0) + 1), state.omm) + 1) / 2),
    };
  }
  function colorFor(c) {
    const col = coinColor(c);
    return {
      c: tgtColor.clone().setHSL(col.hue / 360, col.sat, col.light),
      e: tgtEmis.clone().setHSL(col.hue / 360, col.sat, 0.5),
      ei: 0.15 + 0.85 * col.strong, strong: col.strong,
    };
  }
  function layout() {
    const seen = new Set();
    state.coins.forEach((c) => {
      seen.add(c.symbol);
      const tgt = targetFor(c), cl = colorFor(c);
      let n = nodes.get(c.symbol);
      if (!n) {
        const mat = new THREE.MeshStandardMaterial({
          color: cl.c.clone(), emissive: cl.e.clone(), emissiveIntensity: cl.ei, roughness: 0.35, metalness: 0.12 });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(tgt.x, tgt.y, tgt.z); mesh.scale.setScalar(0.001);
        const halo = new THREE.Sprite(new THREE.SpriteMaterial({
          map: haloTex, color: cl.c.clone(), transparent: true, opacity: 0,
          blending: THREE.AdditiveBlending, depthWrite: false }));
        halo.position.copy(mesh.position);
        group.add(mesh); group.add(halo);
        n = { mesh, halo, mat, cur: { x: tgt.x, y: tgt.y, z: tgt.z, s: 0 }, tgt, cl, coin: c,
          born: now(), delay: REDUCED ? 0 : (createSeq++ % Math.max(1, state.coins.length)) * 4, hover: 0, hoverT: 0 };
        mesh.userData.node = n;
        nodes.set(c.symbol, n);
      } else {
        n.tgt = tgt; n.cl = cl; n.coin = c;
      }
    });
    for (const [sym, n] of nodes) if (!seen.has(sym)) { group.remove(n.mesh); group.remove(n.halo); nodes.delete(sym); }
    pickables = Array.from(nodes.values()).map((n) => n.mesh);
  }
  window.__smRelayout = layout;
  layout();
  wireBias(layout);

  // ── picking + hover ────────────────────────────────────────────────
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let downXY = null, hoverNDC = null, lastInteract = 0;
  const tip = $('smTip');

  function setNDC(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    ndc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    ndc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    return rect;
  }
  canvas.addEventListener('pointerdown', (e) => { downXY = [e.clientX, e.clientY]; lastInteract = now(); });
  canvas.addEventListener('pointerup', (e) => {
    if (!downXY) return;
    const moved = Math.hypot(e.clientX - downXY[0], e.clientY - downXY[1]); downXY = null;
    if (moved > 6) return; // a drag, not a tap
    setNDC(e.clientX, e.clientY);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(pickables, false)[0];
    if (hit && hit.object.userData.node) openPanel(hit.object.userData.node.coin);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (downXY) { if (tip) tip.classList.remove('on'); return; } // dragging: no hover
    hoverNDC = [e.clientX, e.clientY];
  });
  canvas.addEventListener('pointerleave', () => { hoverNDC = null; if (tip) tip.classList.remove('on'); });
  // Only the USER-initiated 'start' resets the idle timer — not the 'change'
  // events auto-rotate itself fires (that would stop it re-arming after 3.2s).
  controls.addEventListener('start', () => { lastInteract = now(); });

  function processHover() {
    if (!hoverNDC) { hovered = null; return; }
    setNDC(hoverNDC[0], hoverNDC[1]);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(pickables, false)[0];
    hovered = (hit && hit.object.userData.node) || null;
    if (tip) {
      if (hovered) {
        const c = hovered.coin;
        tip.innerHTML = `<b>${esc(c.base)}</b> <span class="${c.change_pct >= 0 ? 'up' : 'down'}">${pct(c.change_pct)}</span>`
          + `<span class="sm-tip-ls"><span class="up">L ${c.long_score.toFixed(0)}</span> · <span class="down">S ${c.short_score.toFixed(0)}</span></span>`;
        tip.style.left = hoverNDC[0] + 'px'; tip.style.top = hoverNDC[1] + 'px';
        tip.classList.add('on');
        canvas.style.cursor = 'pointer';
      } else { tip.classList.remove('on'); canvas.style.cursor = 'grab'; }
    }
  }
  let hovered = null;

  // ── axis labels (DOM, projected each frame) ────────────────────────
  const axLabels = ['x', 'y', 'z'].map((a) => {
    const el = document.createElement('div');
    el.className = 'sm-axlabel sm-ax-' + a;
    document.body.appendChild(el);
    return { a, el, v: new THREE.Vector3(), txt: '' };
  });
  const AX_POS = { x: [SPREAD * 1.15, 0, 0], y: [0, SPREAD * 1.15, 0], z: [0, 0, SPREAD * 1.15] };
  function labelFor(key) { const o = AXIS_OPTS.find((x) => x.key === key); return o ? o.label : key; }
  function updateAxisLabels() {
    const rect = canvas.getBoundingClientRect();
    axLabels.forEach((L) => {
      const p = AX_POS[L.a]; L.v.set(p[0], p[1], p[2]).project(camera);
      const x = rect.left + (L.v.x * 0.5 + 0.5) * rect.width;
      const y = rect.top + (-L.v.y * 0.5 + 0.5) * rect.height;
      const inFront = L.v.z < 1;
      L.el.style.transform = `translate(-50%,-50%) translate(${x}px,${y}px)`;
      L.el.style.opacity = inFront ? '1' : '0';
      const t = L.a.toUpperCase() + ' · ' + labelFor(state.ax[L.a]);
      if (t !== L.txt) { L.txt = t; L.el.textContent = t; }
    });
  }

  // ── resize + frame loop ────────────────────────────────────────────
  function resize() {
    const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize); resize();

  let last = now();
  (function frame() {
    const t = now(), dt = Math.min(60, t - last); last = t;
    const k = REDUCED ? 1 : (1 - Math.pow(0.0026, dt / 1000)); // frame-rate-independent smoothing

    if (hoverNDC || hovered) processHover();

    nodes.forEach((n) => {
      const gate = REDUCED ? 1 : smooth((t - n.born - n.delay) / 430);
      n.cur.x += (n.tgt.x - n.cur.x) * k;
      n.cur.y += (n.tgt.y - n.cur.y) * k;
      n.cur.z += (n.tgt.z - n.cur.z) * k;
      n.cur.s += (n.tgt.s * gate - n.cur.s) * k;
      // hover / selection emphasis
      n.hoverT += (((hovered === n) ? 1 : 0) - n.hoverT) * (REDUCED ? 1 : 0.25);
      const selPulse = (!REDUCED && state.sel === n.coin.symbol) ? (1 + 0.16 * Math.sin(t * 0.006)) : 1;
      const s = Math.max(0.001, n.cur.s * (1 + 0.55 * n.hoverT) * selPulse);
      n.mesh.position.set(n.cur.x, n.cur.y, n.cur.z);
      n.mesh.scale.setScalar(s);
      n.halo.position.copy(n.mesh.position);
      n.halo.scale.setScalar(s * 3.4);
      n.mat.color.lerp(n.cl.c, k); n.mat.emissive.lerp(n.cl.e, k);
      n.mat.emissiveIntensity += (n.cl.ei - n.mat.emissiveIntensity) * k;
      n.halo.material.color.lerp(n.cl.c, k);
      const sel = state.sel === n.coin.symbol;
      const haloOp = (0.10 + 0.5 * n.cl.strong + 0.4 * n.hoverT + (sel ? 0.35 : 0)) * gate;
      n.halo.material.opacity += (haloOp - n.halo.material.opacity) * k;
    });

    if (scene.userData.stars && !REDUCED) scene.userData.stars.rotation.y += 0.00006 * dt;
    controls.autoRotate = !REDUCED && !state.sel && (t - lastInteract > 3200);
    controls.update();
    updateAxisLabels();
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  })();

  // Live refresh: pull fresh scores every 20s and morph in place.
  setInterval(async () => {
    try { await loadData(); layout(); } catch (e) { /* keep the last frame */ }
  }, 20000);
})();

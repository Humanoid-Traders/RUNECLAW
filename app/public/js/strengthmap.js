/**
 * RUNECLAW — 3D Strength Map.
 * Every Bitget USDT-perp plotted in 3D by selectable factor axes, coloured by
 * long-vs-short strength (sized by open interest). Click a coin for its factor
 * breakdown and where to trade it (CEX + DEX). Public market data only — no
 * account or P&L (§4). Data-viz, not investment advice.
 *
 * Progressive enhancement: if WebGL/three fails, a 2D table renders the same
 * data. three.js is vendored under /vendor/three (the page declares the map).
 */
const $ = (id) => document.getElementById(id);
const FACTORS = window.__SM_FACTORS;
const AXIS_OPTS = FACTORS.concat([{ key: 'volume', label: 'Volume' }, { key: 'oi', label: 'Open interest' }]);
const state = { coins: [], bias: 'long', ax: { x: 'momentum', y: 'funding', z: 'volume' }, sel: null, vmm: null, omm: null };

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

// ── detail panel ─────────────────────────────────────────────────────
function facBar(label, v) {
  const w = Math.min(50, Math.abs(v) * 50);
  const col = v >= 0 ? 'var(--up)' : 'var(--down)';
  const style = v >= 0 ? `left:50%;width:${w}%` : `left:${50 - w}%;width:${w}%`;
  return `<div class="sm-frow"><span>${esc(label)}</span>`
    + `<span class="bar"><span class="mid"></span><i style="${style};background:${col}"></i></span>`
    + `<span class="val" style="color:${col}">${v >= 0 ? '+' : ''}${v.toFixed(3)}</span></div>`;
}
async function openPanel(c) {
  state.sel = c.symbol;
  const facs = FACTORS.map((f) => facBar(f.label, Number((c.factors && c.factors[f.key]) || 0))).join('');
  $('smPanelBody').innerHTML = `
    <h2>${esc(c.base)}<span class="muted" style="font-size:var(--fs-sm)">USDT</span></h2>
    <div class="px">${fmtPrice(c.price)} <span class="${c.change_pct >= 0 ? 'up' : 'down'}">${pct(c.change_pct)}</span></div>
    <div class="sm-ls">
      <div class="c long"><div class="k">Long</div><div class="v">${c.long_score.toFixed(1)}</div></div>
      <div class="c short"><div class="k">Short</div><div class="v">${c.short_score.toFixed(1)}</div></div>
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
        <a class="sm-v" href="/dashboard?trade=${encodeURIComponent(c.base)}&dir=${c.dir >= 0 ? 'LONG' : 'SHORT'}#trade" style="border-color:var(--gold-bright)">
          <span class="nm">Trade in RUNECLAW</span>
          <span class="tag">${c.dir >= 0 ? '▲ Long' : '▼ Short'} · paper/live</span>
          <span class="rc">◆ risk-gated</span><span class="go">Open ticket →</span></a>
      </div>
      <div class="h" style="margin-top:var(--s2)">…or on an exchange</div>
      <div class="sm-vgrid" id="smVenues"><span class="muted small">Finding venues…</span></div></div>
    <p class="sm-disc">Public Bitget market data · scores are data-viz, not investment advice. Venue links are where the coin is tradeable — RUNECLAW never auto-routes an order.</p>`;
  $('smPanel').classList.add('open');
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
  const reduced = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  try {
    THREE = await import('three');
    ({ OrbitControls } = await import('three/addons/controls/OrbitControls.js'));
  } catch (e) { wireBias(renderFallback); renderFallback(); return; }

  let scene, camera, renderer, controls, raycaster, group;
  const canvas = $('smCanvas');
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch (e) { wireBias(renderFallback); renderFallback(); return; }
  $('smEmpty').style.display = 'none';
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(52, 1, 0.1, 100);
  camera.position.set(9, 7, 12);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.autoRotate = !reduced; controls.autoRotate = false;
  scene.add(new THREE.AmbientLight(0x9fb0d0, 0.9));
  const key = new THREE.PointLight(0xffffff, 60, 60); key.position.set(8, 10, 8); scene.add(key);

  const SPREAD = 6;
  const geo = new THREE.SphereGeometry(1, 16, 12);
  group = new THREE.Group(); scene.add(group);

  // A faint reference cube so the axes read as a 3D volume.
  const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(SPREAD * 2, SPREAD * 2, SPREAD * 2)),
    new THREE.LineBasicMaterial({ color: 0x2a3550, transparent: true, opacity: 0.5 }));
  scene.add(box);

  function layout() {
    group.clear();
    state.coins.forEach((c) => {
      const col = coinColor(c);
      const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
        color: new THREE.Color().setHSL(col.hue / 360, col.sat, col.light),
        emissive: new THREE.Color().setHSL(col.hue / 360, col.sat, 0.5),
        emissiveIntensity: 0.15 + 0.85 * col.strong, roughness: 0.4, metalness: 0.1,
      }));
      const sz = 0.12 + 0.42 * ((norm(Math.log10((c.oi_usd || 0) + 1), state.omm) + 1) / 2);
      m.scale.setScalar(sz);
      m.position.set(axisValue(c, state.ax.x) * SPREAD, axisValue(c, state.ax.y) * SPREAD, axisValue(c, state.ax.z) * SPREAD);
      m.userData.coin = c;
      group.add(m);
    });
  }
  window.__smRelayout = layout;
  layout();
  wireBias(layout);

  raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let downXY = null;
  canvas.addEventListener('pointerdown', (e) => { downXY = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', (e) => {
    if (!downXY) return;
    const moved = Math.hypot(e.clientX - downXY[0], e.clientY - downXY[1]); downXY = null;
    if (moved > 6) return; // a drag, not a tap
    const rect = canvas.getBoundingClientRect();
    ndc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    ndc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(group.children, false)[0];
    if (hit && hit.object.userData.coin) openPanel(hit.object.userData.coin);
  });

  function resize() {
    const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize); resize();

  (function frame() {
    controls.update();
    if (!reduced) group.rotation.y += 0.0012;
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  })();

  // Live refresh: pull fresh scores every 20s and re-lay-out in place.
  setInterval(async () => {
    try { await loadData(); layout(); } catch (e) { /* keep the last frame */ }
  }, 20000);
})();

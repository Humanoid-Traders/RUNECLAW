/**
 * Trade replay theater (landing page).
 *
 * Animates ONE real recorded trade — picked server-side from the same closed
 * history behind the public track record. The price path is REAL candles
 * fetched for the trade's window; when candles are unavailable the theater
 * falls back to a marker-and-counter story and says the path is unavailable,
 * because a price curve is never invented. No recorded trades → the whole
 * section stays hidden.
 */
(function () {
  const section = document.getElementById('theaterSection');
  if (!section) return;

  const stage = document.getElementById('theaterStage');
  const titleEl = document.getElementById('theaterTitle');
  const whenEl = document.getElementById('theaterWhen');
  const entryEl = document.getElementById('theaterEntry');
  const exitEl = document.getElementById('theaterExit');
  const pnlEl = document.getElementById('theaterPnl');
  const replayBtn = document.getElementById('theaterReplay');

  const REDUCED = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function fmtPrice(v) {
    const n = Number(v);
    if (!isFinite(n)) return '—';
    const dp = n >= 1000 ? 1 : n >= 1 ? 3 : 6;
    return '$' + n.toLocaleString('en-US', { maximumFractionDigits: dp });
  }
  function fmtPnl(v) {
    const n = Number(v) || 0;
    return (n < 0 ? '-$' : '+$') + Math.abs(n).toFixed(2);
  }

  async function getJSON(url) {
    const r = await fetch(url, { signal: AbortSignal.timeout(12000) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  // Pick a granularity that yields a readable number of candles (~40-120).
  function pickGranularity(ms) {
    const H = 3_600_000;
    if (ms <= 4 * H) return { g: '5min', ms: 5 * 60_000 };
    if (ms <= 24 * H) return { g: '30min', ms: 30 * 60_000 };
    if (ms <= 5 * 24 * H) return { g: '1h', ms: H };
    return { g: '4h', ms: 4 * H };
  }

  async function loadCandles(trade) {
    const sym = String(trade.symbol || '').replace(/[^A-Z0-9]/gi, '').toUpperCase();
    if (!sym) return null;
    const open = new Date(trade.opened_at).getTime();
    const close = new Date(trade.closed_at).getTime();
    if (!isFinite(open) || !isFinite(close) || close <= open) return null;
    const pad = Math.max((close - open) * 0.25, 30 * 60_000);
    const { g } = pickGranularity(close - open);
    const url = `/api/market/candles/${encodeURIComponent(sym)}?granularity=${g}`
      + `&limit=200&startTime=${Math.floor(open - pad)}&endTime=${Math.ceil(close + pad)}`;
    try {
      const d = await getJSON(url);
      // Bitget candle row: [ts, open, high, low, close, ...]
      const rows = (d && d.data) || [];
      const pts = rows.map(r => ({ t: parseInt(r[0]), c: parseFloat(r[4]) }))
        .filter(p => isFinite(p.t) && isFinite(p.c))
        .sort((a, b) => a.t - b.t);
      return pts.length >= 8 ? pts : null;
    } catch (e) {
      return null;
    }
  }

  let animToken = 0;

  function renderStage(trade, pts) {
    const W = 800, H = 260, PAD = { l: 10, r: 90, t: 16, b: 18 };
    const open = new Date(trade.opened_at).getTime();
    const close = new Date(trade.closed_at).getTime();
    const hasPath = Array.isArray(pts) && pts.length >= 8;

    if (!hasPath) {
      stage.innerHTML = `<div class="card" style="text-align:center;padding:var(--s5)">
          <div style="font-size:28px" id="thNoPathPnl" class="num">$0.00</div>
          <p class="muted small" style="margin-top:var(--s2)">${trade.direction} ${trade.symbol}
            — held ${Math.max(1, Math.round((close - open) / 3_600_000))}h.
            Price path unavailable for this window (never invented).</p>
        </div>`;
      return { counterEl: document.getElementById('thNoPathPnl'), pathLen: 0 };
    }

    const vals = pts.map(p => p.c).concat([trade.entry_price, trade.exit_price]);
    const min = Math.min(...vals), max = Math.max(...vals);
    const span = (max - min) || 1;
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
    const x = t => PAD.l + (t - t0) / (t1 - t0 || 1) * (W - PAD.l - PAD.r);
    const y = v => PAD.t + (max - v) / span * (H - PAD.t - PAD.b);
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.c).toFixed(1)}`).join('');
    const win = (Number(trade.pnl) || 0) >= 0;
    const col = win ? 'var(--up)' : 'var(--down)';
    const dirUp = trade.direction === 'LONG';

    stage.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
        aria-label="Replay of a recorded ${trade.direction} ${trade.symbol} trade" style="display:block">
      <line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(trade.entry_price)}" y2="${y(trade.entry_price)}"
        stroke="var(--text-3)" stroke-dasharray="4 4" opacity="0.6"/>
      <text x="${W - PAD.r + 6}" y="${y(trade.entry_price) + 4}" fill="var(--text-3)" font-size="11">entry</text>
      <path id="thPath" d="${line}" fill="none" stroke="${col}" stroke-width="2"/>
      <g id="thEntryMark" opacity="0">
        <circle cx="${x(open)}" cy="${y(trade.entry_price)}" r="5" fill="none" stroke="${col}" stroke-width="2"/>
        <text x="${x(open)}" y="${y(trade.entry_price) + (dirUp ? 22 : -12)}" fill="${col}"
          font-size="12" text-anchor="middle">${dirUp ? '▲' : '▼'} ${trade.direction}</text>
      </g>
      <g id="thExitMark" opacity="0">
        <circle cx="${x(close)}" cy="${y(trade.exit_price)}" r="5" fill="${col}"/>
        <text x="${Math.min(x(close), W - PAD.r - 4)}" y="${y(trade.exit_price) - 10}" fill="${col}"
          font-size="12" text-anchor="middle">exit</text>
      </g>
    </svg>`;
    const path = document.getElementById('thPath');
    const pathLen = path.getTotalLength();
    return {
      path, pathLen,
      entryMark: document.getElementById('thEntryMark'),
      exitMark: document.getElementById('thExitMark'),
      entryFrac: (x(open) - PAD.l) / (W - PAD.l - PAD.r),
    };
  }

  function animate(trade, parts) {
    const token = ++animToken;
    const pnl = Number(trade.pnl) || 0;
    const DUR = 4200;
    pnlEl.textContent = '+$0.00';
    pnlEl.className = 'num';

    if (REDUCED || !parts.pathLen) {
      // No motion: final state immediately.
      if (parts.path) {
        parts.path.style.strokeDasharray = 'none';
        parts.entryMark.style.opacity = 1;
        parts.exitMark.style.opacity = 1;
      }
      const el = parts.counterEl || pnlEl;
      el.textContent = fmtPnl(pnl);
      el.classList.add(pnl >= 0 ? 'up' : 'down');
      if (el !== pnlEl) { pnlEl.textContent = fmtPnl(pnl); pnlEl.classList.add(pnl >= 0 ? 'up' : 'down'); }
      return;
    }

    parts.path.style.strokeDasharray = `${parts.pathLen}`;
    parts.path.style.strokeDashoffset = `${parts.pathLen}`;
    parts.entryMark.style.opacity = 0;
    parts.exitMark.style.opacity = 0;
    const start = performance.now();

    function frame(now) {
      if (token !== animToken) return;              // superseded by a replay
      const p = Math.min((now - start) / DUR, 1);
      const ease = 1 - Math.pow(1 - p, 2);
      parts.path.style.strokeDashoffset = String(parts.pathLen * (1 - ease));
      if (ease >= (parts.entryFrac || 0)) parts.entryMark.style.opacity = 1;
      // The PnL counts only over the in-trade portion of the reveal.
      const tradeP = Math.max(0, Math.min(1,
        (ease - (parts.entryFrac || 0)) / (1 - (parts.entryFrac || 0) || 1)));
      pnlEl.textContent = fmtPnl(pnl * tradeP);
      if (p >= 1) {
        parts.exitMark.style.opacity = 1;
        pnlEl.textContent = fmtPnl(pnl);
        pnlEl.classList.add(pnl >= 0 ? 'up' : 'down');
        return;
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  async function init() {
    let trade = null;
    try {
      const d = await getJSON('/api/public/replay-trade');
      trade = d && d.trade;
    } catch (e) { /* section stays hidden */ }
    if (!trade) return;                              // nothing real to show

    const base = String(trade.symbol || '').split('/')[0];
    titleEl.textContent = `${trade.direction} ${base}`;
    const closed = new Date(trade.closed_at);
    whenEl.textContent = isNaN(closed.getTime()) ? '' : `closed ${closed.toISOString().slice(0, 10)}`;
    entryEl.textContent = fmtPrice(trade.entry_price);
    exitEl.textContent = fmtPrice(trade.exit_price);

    const pts = await loadCandles(trade);
    const parts = renderStage(trade, pts);
    section.classList.remove('hidden');
    section.hidden = false;
    animate(trade, parts);
    replayBtn.onclick = () => animate(trade, renderStage(trade, pts));
  }

  init();
})();

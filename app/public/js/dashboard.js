/**
 * RUNECLAW dashboard — hash router + 7 views.
 *
 * Views: home | markets | signals | trade | portfolio | engine | account.
 * Every panel renders through RC.renderPanel (skeleton -> data|empty|error),
 * engine-analytics panels auto-hide when the insight bridge is down, and
 * nothing on this page invents a number: no data means an empty state.
 */
(function () {
  'use strict';
  const RC = window.RC;
  const { LOGGED_IN, fetchJSON, esc, fmt, fmtMoney, fmtPrice, fmtK, signed,
          pnlClass, fmtAgo, dirChip, sanitizeBotHtml, toast, renderPanel,
          stateBlock, connectStream } = RC;

  const VIEWS = [
    { id: 'home',      label: 'Home',      icon: 'icon-home' },
    { id: 'chat',      label: 'AI Chat',   icon: 'icon-chat' },
    { id: 'markets',   label: 'Markets',   icon: 'icon-globe' },
    { id: 'signals',   label: 'Signals',   icon: 'icon-radar' },
    { id: 'feed',      label: 'Live Feed', icon: 'icon-sparkle' },
    { id: 'trade',     label: 'Trade',     icon: 'icon-target' },
    { id: 'portfolio', label: 'Portfolio', icon: 'icon-chart' },
    { id: 'leaderboard', label: 'Leaders', icon: 'icon-target' },
    { id: 'lab',       label: 'Lab',       icon: 'icon-sparkle' },
    { id: 'engine',    label: 'Engine',    icon: 'icon-cog' },
    { id: 'account',   label: 'Account',   icon: 'icon-user' },
  ];

  const container = document.getElementById('viewContainer');
  let currentView = '';
  let viewTimers = [];

  // ── Shared data caches ────────────────────────────────────────────────
  const cache = { scan: null, scanAt: 0, tickers: {}, portfolio: null, insightOk: null };

  async function getScan(maxAgeMs = 45000) {
    if (cache.scan && Date.now() - cache.scanAt < maxAgeMs) return cache.scan;
    const r = await fetchJSON('/api/bot/sync/scan', { auth: false });
    if (r.ok && r.data?.scan) { cache.scan = r.data.scan; cache.scanAt = Date.now(); }
    return cache.scan;
  }
  async function getTickers() {
    const r = await fetchJSON('/api/market/tickers', { auth: false });
    if (r.ok && r.data?.data) for (const t of r.data.data) cache.tickers[t.symbol] = t;
    return cache.tickers;
  }
  async function getPortfolio(force = false) {
    if (!LOGGED_IN) return null;
    if (cache.portfolio && !force) return cache.portfolio;
    const r = await fetchJSON('/api/portfolio', { timeoutMs: 16000 });
    if (r.ok) cache.portfolio = r.data;
    return cache.portfolio;
  }
  // One probe per page-load decides whether bridge-fed analytics render at all.
  async function insightAvailable() {
    if (cache.insightOk !== null) return cache.insightOk;
    const r = await fetchJSON('/api/insight?symbol=BTC%2FUSDT&timeframe=1h&limit=50', { auth: false, timeoutMs: 7000 }).catch(() => null);
    cache.insightOk = !!(r && r.ok);
    return cache.insightOk;
  }

  // ── Top chrome: connection + mode chips ───────────────────────────────
  function updateConnChip() {
    const el = document.getElementById('connChip');
    if (!el) return;
    const syncTime = cache.scan?.received_at || cache.scan?.timestamp;
    if (!syncTime) { el.textContent = '● ENGINE OFFLINE'; el.className = 'chip chip--offline'; return; }
    const ageSec = (Date.now() - new Date(syncTime).getTime()) / 1000;
    if (ageSec < 900) { el.textContent = '● ENGINE LIVE'; el.className = 'chip chip--up'; }
    else if (ageSec < 1800) { el.textContent = '● ENGINE STALE'; el.className = 'chip chip--warn'; }
    else { el.textContent = '● ENGINE OFFLINE'; el.className = 'chip chip--offline'; }
  }
  function updateModeChip(pf) {
    const el = document.getElementById('modeChip');
    if (!el || !pf) return;
    el.classList.remove('hidden');
    if (pf.stale && pf.source !== 'sync') {
      // Bot unreachable and no live feed: mode is unknown — don't assert PAPER.
      el.textContent = 'MODE ?';
      el.className = 'chip chip--offline';
      return;
    }
    const live = pf.mode === 'LIVE' || pf.mode === 'MIXED';
    // LIVE mode but the balance can't be read: don't flash a confident "LIVE"
    // over an unavailable account — say so.
    if (live && pf.live_unavailable) {
      el.textContent = 'LIVE — BALANCE UNAVAILABLE';
      el.className = 'chip chip--warn';
      return;
    }
    el.textContent = live ? 'LIVE' : 'PAPER';
    el.className = 'chip ' + (live ? 'chip--live' : 'chip--paper');
  }

  // ── Router ─────────────────────────────────────────────────────────────
  function navHtml(active) {
    return VIEWS.map(v => `
      <a href="#${v.id}" ${v.id === active ? 'aria-current="page"' : ''}>
        <svg class="icon" aria-hidden="true"><use href="#${v.icon}"></use></svg>${v.label}
      </a>`).join('');
  }
  function renderNav(active) {
    document.getElementById('railNav').innerHTML = navHtml(active);
    document.getElementById('tabbarNav').innerHTML = navHtml(active);
  }
  function every(ms, fn) { viewTimers.push(setInterval(fn, ms)); }
  function showView(id) {
    if (!VIEWS.some(v => v.id === id)) id = 'home';
    currentView = id;
    viewTimers.forEach(clearInterval);
    viewTimers = [];
    renderNav(id);
    window.scrollTo({ top: 0 });
    // Pull the docked chat back out before the container is wiped; the chat
    // view re-docks it. Other views keep the floating FAB.
    if (window.RCChat) window.RCChat.unmountInline();
    RENDER[id]();
  }

  // Panels are declared as [id, title, icon, extraClass] and mounted together.
  function mount(panels) {
    container.innerHTML = panels.map(p => `
      <section class="panel ${p.cls || ''}" ${p.hidden ? 'hidden' : ''} id="p-${p.id}">
        ${p.title ? `<h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#${p.icon || 'icon-chart'}"></use></svg>${p.title}<span class="right" id="pt-${p.id}"></span></h2>` : ''}
        <div id="c-${p.id}"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>
      </section>`).join('');
  }
  const C = id => document.getElementById('c-' + id);

  function loginGate(text) {
    return stateBlock({ icon: 'icon-user', text, cta: { label: 'Log in or create an account', href: '/' } });
  }
  function viewHead(title, sub) {
    return `<div class="view-head"><h1>${esc(title)}</h1>${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</div>`;
  }

  /* ── Agent mind-stream (shared: home panel + Live Feed view + SSE) ──
     Events come from the bot's agent_feed emitter via /api/feed/recent and
     arrive live as SSE 'activity' events. Public, pre-sanitized data. */
  const FEED_META = {
    scan: '📡', thesis: '🧠', trade_open: '🟢', trade_close: '🏁',
    sl_move: '🛡️', alert: '⚠️', stance: '🎚️', info: 'ℹ️',
  };
  let feedFilter = 'all';

  function feedItemHtml(ev) {
    const icon = FEED_META[ev.event_type] || FEED_META.info;
    const sym = ev.symbol
      ? `<span class="chip" style="font-size:11px;padding:1px 7px">${esc(String(ev.symbol).replace(':USDT', '').replace('/USDT', ''))}</span>` : '';
    const body = ev.body
      ? `<div class="small muted" style="margin-top:2px;max-width:72ch">${esc(ev.body)}</div>` : '';
    return `<div class="feed-item" data-type="${esc(ev.event_type || 'info')}" style="display:flex;gap:10px;padding:9px 0;border-bottom:1px solid rgba(128,128,128,.15)">
      <div style="flex:0 0 auto;line-height:1.5">${icon}</div>
      <div style="flex:1;min-width:0">
        <div class="row" style="gap:8px;align-items:baseline;flex-wrap:wrap">
          <b>${esc(ev.title || '')}</b>${sym}
          <span class="muted small num" style="margin-left:auto">${fmtAgo(ev.created_at)}</span>
        </div>
        ${body}
      </div>
    </div>`;
  }
  async function getFeed(limit) {
    const r = await fetchJSON('/api/feed/recent?limit=' + limit, { auth: false });
    return (r.ok && r.data?.events) || [];
  }
  function feedListHtml(events) {
    if (!events.length) return null; // renderPanel shows the empty state
    return `<div class="feed-list">${events.map(feedItemHtml).join('')}</div>`;
  }
  function applyFeedFilter(list) {
    Array.from(list.children).forEach(el => {
      el.style.display = (feedFilter === 'all' || el.dataset.type === feedFilter) ? '' : 'none';
    });
  }
  // SSE 'activity': prepend into whichever mind-stream hosts are on screen.
  function onActivity(e) {
    let ev; try { ev = JSON.parse(e.data); } catch (err) { return; }
    if (!ev || !ev.title) return;
    [['c-mind', 10], ['feedLive', 120]].forEach(([id, max]) => {
      const host = document.getElementById(id);
      if (!host) return;
      let list = host.querySelector('.feed-list');
      if (!list) { // replaces the empty state on first live event
        host.innerHTML = '<div class="feed-list"></div>';
        list = host.firstElementChild;
      }
      list.insertAdjacentHTML('afterbegin', feedItemHtml(ev));
      while (list.children.length > max) list.lastElementChild.remove();
      if (id === 'feedLive' && feedFilter !== 'all') applyFeedFilter(list);
    });
  }

  // ── Per-user agent profile (server-side; anon falls back to local) ──
  // { risk_pref, watchlist[], prefs{} } — logged-in users get cross-device
  // persistence via /api/profile; anonymous visitors keep a local watchlist.
  let profileCache = null;
  async function getUserProfile(force = false) {
    if (!LOGGED_IN) {
      let wl = [];
      try { wl = JSON.parse(localStorage.getItem('rc_watchlist') || '[]'); } catch (e) { /* fresh */ }
      return { risk_pref: null, watchlist: Array.isArray(wl) ? wl : [], prefs: {} };
    }
    if (profileCache && !force) return profileCache;
    const r = await fetchJSON('/api/profile').catch(() => null);
    profileCache = (r?.ok && r.data) ? r.data : { risk_pref: null, watchlist: [], prefs: {} };
    return profileCache;
  }
  async function saveUserProfile(patch) {
    if (!LOGGED_IN) {
      if (patch.watchlist) localStorage.setItem('rc_watchlist', JSON.stringify(patch.watchlist));
      return true;
    }
    const r = await fetchJSON('/api/profile', { method: 'PUT', body: patch }).catch(() => null);
    if (r?.ok && r.data) { profileCache = r.data; return true; }
    return false;
  }

  // ── Bot intelligence reports (funding / arb / parity / yield) ──
  // Pushed hourly by the bot; panels render real data or an empty state.
  async function getReports() {
    const r = await fetchJSON('/api/reports', { auth: false });
    return (r.ok && r.data?.reports) || null;
  }
  function reportAge(rep) {
    const t = rep?.generated_at || rep?.received_at;
    return t ? `updated ${fmtAgo(t)}` : '';
  }

  /* ═══════════════ LIVE FEED ═══════════════ */
  async function renderFeed() {
    container.innerHTML = viewHead('Live Feed',
      "The agent's mind-stream — every scan, thesis, trade and alert, as it happens")
      + `<section class="panel">
          <div class="row" id="feedChips" style="gap:var(--s2);flex-wrap:wrap;margin-bottom:var(--s3)"></div>
          <div id="feedLive"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>
        </section>`;
    const TYPES = [['all', 'All'], ['scan', '📡 Scans'], ['thesis', '🧠 Theses'],
      ['trade_open', '🟢 Opens'], ['trade_close', '🏁 Closes'],
      ['sl_move', '🛡️ Stops'], ['alert', '⚠️ Alerts'], ['stance', '🎚️ Stance']];
    const chips = document.getElementById('feedChips');
    chips.innerHTML = TYPES.map(([t, l]) =>
      `<button class="btn btn--sm ${t === feedFilter ? 'btn--primary' : ''}" data-t="${t}" type="button">${l}</button>`).join('');
    chips.onclick = (e) => {
      const b = e.target.closest('button[data-t]'); if (!b) return;
      feedFilter = b.dataset.t;
      chips.querySelectorAll('button').forEach(x =>
        x.classList.toggle('btn--primary', x.dataset.t === feedFilter));
      const list = document.querySelector('#feedLive .feed-list');
      if (list) applyFeedFilter(list);
    };
    await renderPanel(document.getElementById('feedLive'),
      async () => feedListHtml(await getFeed(100)),
      { empty: { icon: 'icon-radar', text: 'No agent events yet — they appear here the moment the engine pushes its next scan.' } });
    const list = document.querySelector('#feedLive .feed-list');
    if (list && feedFilter !== 'all') applyFeedFilter(list);
  }

  /* ═══════════════ HOME ═══════════════ */
  async function renderHome() {
    container.innerHTML = viewHead('Home', 'Your account at a glance');
    // First visit after signup: the agent introduces itself once, with three
    // guided first actions. Dismiss persists in localStorage.
    const firstRun = LOGGED_IN && !localStorage.getItem('rc_welcomed');
    if (firstRun) {
      container.insertAdjacentHTML('beforeend', `
        <section class="panel panel--primary" id="p-welcome">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>Meet your agent</h2>
          <p style="max-width:62ch;color:var(--text-2)">Welcome to RUNECLAW. From here on, an autonomous trading agent works this dashboard with you —
          it scans the market around the clock, explains every read, and only ever trades through a strict risk gate. Three good first moves:</p>
          <div class="row mt-3" style="gap:var(--s2);flex-wrap:wrap">
            <a class="btn btn--primary btn--sm" href="#chat">💬 1 · Say hello to your agent</a>
            <a class="btn btn--sm" href="#signals">📡 2 · Watch it read the market</a>
            <a class="btn btn--sm" href="#trade">🎯 3 · Place a risk-gated paper trade</a>
          </div>
          <button class="btn btn--ghost btn--sm mt-3" id="welcomeDismiss" type="button">Got it — don't show again</button>
        </section>`);
      // The element exists synchronously after insertAdjacentHTML — attach
      // immediately so a fast click can't land before the handler does.
      const dismissBtn = document.getElementById('welcomeDismiss');
      if (dismissBtn) dismissBtn.onclick = () => {
        localStorage.setItem('rc_welcomed', '1');
        const p = document.getElementById('p-welcome');
        if (p) p.remove();
      };
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel panel--primary" id="p-hero"><div id="c-hero"><div class="skel"></div><div class="skel"></div></div></section>
        ${LOGGED_IN ? `<section class="panel" id="p-agent"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>Your agent
          <span class="right muted small">what it's doing for you</span></h2><div id="c-agent"><div class="skel"></div></div></section>` : ''}
        <section class="panel" id="p-mind"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg>Agent mind-stream
          <span class="right"><a class="small" href="#feed">full feed →</a></span></h2><div id="c-mind"><div class="skel"></div><div class="skel"></div></div></section>
        <div class="grid grid-main">
          <section class="panel" id="p-hpos"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>Open positions</h2><div id="c-hpos"><div class="skel"></div><div class="skel"></div></div></section>
          <section class="panel" id="p-next"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-rocket"></use></svg>Getting started</h2><div id="c-next"><div class="skel"></div></div></section>
        </div>
        <section class="panel" id="p-hsig"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg>Latest engine signals</h2><div id="c-hsig"><div class="skel"></div><div class="skel"></div></div></section>
      </div>`);

    renderPanel(C('hero'), async () => {
      if (!LOGGED_IN) {
        return `<div class="stat"><div class="k">Welcome to RUNECLAW</div>
          <div style="font-size:var(--fs-md);margin:6px 0 14px;max-width:52ch">Watch the autonomous engine, chat with the analyst, and paper-trade with a real risk gate — free.</div>
          <a class="btn btn--primary" href="/">Create your account</a></div>`;
      }
      const pf = await getPortfolio(true);
      updateModeChip(pf);
      if (pf && pf.live_unavailable) {
        // LIVE account but the exchange balance can't be read right now — say so
        // honestly instead of inviting a paper trade or faking a number.
        return stateBlock({ icon: 'icon-coin', text: 'Live account connected, but the exchange balance is unavailable right now — the engine will refresh it on the next sync.' });
      }
      if (!pf || pf.equity == null) {
        return stateBlock({ icon: 'icon-coin', text: 'No portfolio yet — place your first paper trade and your equity shows up here.', cta: { label: 'Place a paper trade', href: '#trade' } });
      }
      const daily = pf.daily_pnl, total = pf.total_pnl;
      // 'sync' source IS the live feed (operator account) — only label offline
      // when the data is genuinely stale.
      const offline = pf.stale ? '<span class="chip chip--offline">bot offline — last known</span>'
        : pf.source === 'sync' && pf.mode === 'LIVE' ? '<span class="chip chip--live">LIVE ACCOUNT</span>' : '';
      const dailyPart = daily != null ? `<span class="${pnlClass(daily)}">${signed(daily)} today</span> · ` : '';
      return `<div class="row" style="justify-content:space-between;align-items:flex-start">
        <div class="stat">
          <div class="k">My equity ${offline}</div>
          <div class="v big">${fmtMoney(pf.equity)}</div>
          <div class="d num">${dailyPart}<span class="${pnlClass(total)}">${total != null ? signed(total) + ' all-time' : ''}</span></div>
        </div>
        <div class="stat-row" style="flex:1;max-width:420px">
          <div class="stat"><div class="k">Win rate</div><div class="v">${pf.win_rate != null ? fmt(pf.win_rate, 1) + '%' : '—'}</div></div>
          <div class="stat"><div class="k">Trades</div><div class="v">${pf.total_trades ?? 0}</div></div>
          <div class="stat"><div class="k">Open</div><div class="v">${(pf.open_positions || []).length}</div></div>
        </div>
      </div>`;
    }, { empty: { text: 'No portfolio data yet.' } });

    if (LOGGED_IN) {
      renderPanel(C('agent'), async () => {
        // Everything here is real synced data; anything unavailable is
        // omitted, never invented.
        const [pf, hist, scanR, meR, prof] = await Promise.all([
          getPortfolio(),
          fetchJSON('/api/trades/history?limit=50', { timeoutMs: 12000 }).catch(() => null),
          fetchJSON('/api/bot/sync/scan', { auth: false, timeoutMs: 10000 }).catch(() => null),
          fetchJSON('/api/auth/me', { timeoutMs: 10000 }).catch(() => null),
          getUserProfile().catch(() => ({ risk_pref: null, watchlist: [], prefs: {} })),
        ]);
        const isAdmin = meR?.data?.plan === 'admin';
        const scan = scanR?.data?.scan || null;
        const cb = scan?.circuit_breaker || {};
        const stance = String(cb.strategy_mode || '').toLowerCase();
        const STANCE = {
          defensive: ['🛡', 'Defensive', 'capital protection first'],
          balanced: ['⚔️', 'Balanced', 'the default posture'],
          aggressive: ['🔥', 'Aggressive', 'larger sizing bias, every gate still on'],
          manual: ['🧘', 'Manual', 'proposes only — you confirm each trade'],
        }[stance];
        const lines = [];
        if (STANCE) {
          lines.push(`<div class="kv-row"><span>Stance</span><b>${STANCE[0]} ${STANCE[1]} <span class="muted small">— ${STANCE[2]}</span></b></div>`);
        }
        if (scan) {
          const nSyms = Object.keys(scan.symbols || {}).length;
          const at = scan.received_at ? new Date(scan.received_at) : null;
          const fresh = at && (Date.now() - at.getTime()) < 3 * 3600 * 1000;
          if (nSyms && at) {
            lines.push(`<div class="kv-row"><span>Last scan</span><b class="num">${nSyms} pairs · ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}${fresh ? '' : ' <span class="muted small">(stale)</span>'}</b></div>`);
          }
        }
        const rows = hist?.data?.trades || hist?.data?.rows || [];
        const todayStr = new Date().toDateString();
        const today = rows.filter(t => t.closed_at && new Date(t.closed_at).toDateString() === todayStr);
        if (today.length) {
          const net = today.reduce((a, t) => a + (parseFloat(t.pnl) || 0), 0);
          const wins = today.filter(t => parseFloat(t.pnl) > 0).length;
          lines.push(`<div class="kv-row"><span>Today for you</span><b class="num ${pnlClass(net)}">${today.length} closed (${wins} wins) · ${net < 0 ? '-' : '+'}$${Math.abs(net).toFixed(2)}</b></div>`);
        } else {
          lines.push(`<div class="kv-row"><span>Today for you</span><b class="muted">no closed trades yet — only setups that clear the risk gate get taken</b></div>`);
        }
        const nOpen = (pf?.open_positions || []).length;
        lines.push(`<div class="kv-row"><span>Carrying</span><b class="num">${nOpen} open position${nOpen === 1 ? '' : 's'}</b></div>`);
        // YOUR risk preference — personal (saved to your profile, shapes how
        // the agent talks to you in chat); it never changes the engine.
        const rp = prof?.risk_pref || null;
        lines.push(`<div class="kv-row"><span>Your risk preference</span><b>
          ${[['conservative', '🛡'], ['balanced', '⚖️'], ['aggressive', '🔥']].map(([m, ic]) =>
            `<button class="btn btn--sm ${rp === m ? 'btn--primary' : ''}" data-riskpref="${m}" type="button" aria-pressed="${rp === m}" style="margin-left:4px">${ic} ${m[0].toUpperCase() + m.slice(1)}</button>`).join('')}
        </b></div>`);
        if ((prof?.watchlist || []).length) {
          lines.push(`<div class="kv-row"><span>Watching</span><b class="num small">${prof.watchlist.slice(0, 8).map(s => esc(s.replace('USDT', ''))).join(' · ')}</b></div>`);
        }
        lines.push(`<div class="row mt-3" style="gap:var(--s2);flex-wrap:wrap">
          <a class="btn btn--sm" href="#chat">💬 Ask your agent</a>
          <a class="btn btn--sm" href="#signals">📡 Signals</a>
          <a class="btn btn--sm" href="#portfolio">📊 Portfolio</a>
        </div>`);
        // Operator stance control — same presets as Telegram /agent. The web
        // only QUEUES the change; the bot re-verifies the requester's tier
        // against its own UserStore and applies within ~30s.
        if (isAdmin) {
          lines.push(`<div class="row mt-3" style="gap:var(--s2);flex-wrap:wrap">
            ${[['defensive', '🛡 Defensive'], ['balanced', '⚔️ Balanced'],
               ['aggressive', '🔥 Aggressive'], ['manual', '🧘 Manual']]
              .map(([m, l]) => `<button class="btn btn--sm" data-stance="${m}" type="button">${l}</button>`).join('')}
          </div>
          <p class="muted small" style="margin-top:6px">Operator: set the agent's global stance — the bot verifies and applies it within ~30s.</p>`);
        }
        return lines.join('');
      }, { empty: { text: 'Agent status unavailable right now.' } });
      // Delegated so it survives panel re-renders. data-stance buttons exist
      // only for admins (global bot posture); data-riskpref is every user's
      // own saved preference.
      C('agent').addEventListener('click', async (e) => {
        const rb = e.target.closest('button[data-riskpref]');
        if (rb) {
          rb.disabled = true;
          const ok = await saveUserProfile({ risk_pref: rb.dataset.riskpref });
          rb.disabled = false;
          if (ok) { toast(`Saved — your agent now knows you prefer ${rb.dataset.riskpref}.`); showView('home'); }
          else toast('Could not save your preference — try again.');
          return;
        }
        const b = e.target.closest('button[data-stance]');
        if (!b) return;
        b.disabled = true;
        const r = await fetchJSON('/api/controls/stance', {
          method: 'POST', body: { mode: b.dataset.stance } }).catch(() => null);
        b.disabled = false;
        if (r?.ok) toast(`Stance change queued: ${b.dataset.stance} — the bot applies it within ~30s.`);
        else toast(r?.data?.detail || r?.data?.error || 'Stance change failed.');
      });
    }

    renderPanel(C('mind'), async () => feedListHtml(await getFeed(10)),
      { empty: { icon: 'icon-radar', text: 'The agent narrates its work here — scans, theses, trades and stop moves, live as they happen.' } });

    renderPanel(C('hpos'), async () => {
      if (!LOGGED_IN) return loginGate('Log in to see your open positions.');
      const pf = await getPortfolio();
      const open = pf?.open_positions || [];
      if (!open.length) return null;
      return posTable(open.slice(0, 6));
    }, { empty: { icon: 'icon-target', text: 'No open positions. The Trade view has a full order ticket.', cta: { label: 'Open the trade ticket', href: '#trade' } } });

    renderPanel(C('next'), async () => {
      if (!LOGGED_IN) return `<p class="small muted mb-3">One account unlocks paper trading, chat, and portfolio tracking.</p><a class="btn btn--primary" href="/">Create free account</a>`;
      const [me, creds, ctl, pf] = await Promise.all([
        fetchJSON('/api/auth/me'), fetchJSON('/api/credentials/status'),
        fetchJSON('/api/controls/status'), getPortfolio(),
      ]);
      // Every state comes from real endpoints — no invented progress.
      const verified = !!me.data?.email_verified;
      const linked = !!me.data?.telegram_linked;
      const connected = !!creds.data?.connected;
      const credsPending = creds.data?.pending === 'connect';
      const traded = (pf?.total_trades || 0) > 0 || (pf?.open_positions || []).length > 0;
      const liveReady = !!(ctl.data?.live_enabled && ctl.data?.allowlisted);
      const paused = !!ctl.data?.paused;

      // Ordered onboarding ladder. `locked` steps can't be started until an
      // earlier prerequisite is met (Go live needs connected keys).
      const steps = [
        { done: verified, label: 'Verify your email',
          hint: 'Confirm your address to secure the account and enable recovery.',
          cta: { label: 'Resend verification', href: '#account' } },
        { done: traded, label: 'Place a paper trade',
          hint: 'Real 23-check risk gate, zero risk — watch the engine execute.',
          cta: { label: 'Open the trade ticket', href: '#trade' } },
        { done: connected, pending: credsPending, label: 'Connect an exchange',
          hint: 'Link Bitget, Bybit, BingX or Hyperliquid keys to prepare live trading.',
          cta: { label: credsPending ? 'Finish connecting' : 'Connect exchange', href: '#account' } },
        { done: linked, label: 'Link Telegram',
          hint: 'Get trade alerts and chat with the agent from Telegram too.',
          cta: { label: 'Link Telegram', href: '#account' } },
        { done: liveReady, locked: !connected, label: 'Go live',
          hint: liveReady ? 'Live trading is enabled for your account.'
            : 'Needs connected keys, your live toggle, and operator approval.',
          cta: { label: 'Review live controls', href: '#account' } },
      ];
      const doneN = steps.filter((s) => s.done).length;
      const pct = Math.round(doneN / steps.length * 100);
      const banner = paused
        ? `<div class="onboard-banner mb-3">Trading is paused — everything routes to paper. <a href="#account">Resume in controls</a>.</div>`
        : '';

      // Fully set up: collapse to a compact confirmation so veterans aren't nagged.
      if (doneN === steps.length) {
        return `${banner}<div class="chk-done"><span class="chip chip--up">✓ All set</span>`
          + `<p class="small" style="color:var(--text-2)">You're fully set up. Watch the engine or manage your risk caps any time.</p>`
          + `<a class="btn btn--sm" href="#engine">Open Engine telemetry</a></div>`;
      }

      const rows = steps.map((s) => {
        const status = s.done ? '<span class="chip chip--up">Done</span>'
          : s.pending ? '<span class="chip chip--warn">Pending</span>'
          : s.locked ? '<span class="chip chip--offline">Locked</span>'
          : '<span class="chip chip--gold">To do</span>';
        const cta = (!s.done && !s.locked)
          ? `<a class="btn btn--sm" href="${s.cta.href}">${esc(s.cta.label)}</a>` : '';
        return `<li class="chk-item${s.done ? ' is-done' : ''}">`
          + `<div class="chk-head"><span class="chk-label">${esc(s.label)}</span>${status}</div>`
          + `<div class="chk-hint">${esc(s.hint)}</div>${cta}</li>`;
      }).join('');

      return `${banner}`
        + `<div class="chk-progress"><div class="chk-progressbar"><span style="width:${pct}%"></span></div>`
        + `<span class="chk-progress-label">${doneN} of ${steps.length} · ${pct}%</span></div>`
        + `<ol class="checklist">${rows}</ol>`;
    }, { empty: { text: 'All set.' } });

    renderPanel(C('hsig'), async () => {
      const r = await fetchJSON('/api/signals?limit=3', { auth: false });
      const sigs = r.data?.signals || [];
      if (!sigs.length) return null;
      return sigs.map(s => `
        <div class="kv-row">
          <span class="row" style="gap:8px">${dirChip(s.direction)}<b style="font-family:var(--font-ui)">${esc(s.symbol)}</b><span class="muted small">${esc(s.pattern || '')}</span></span>
          <span class="num muted">${fmtPrice(s.entry_price)} · ${fmtAgo(s.created_at)}</span>
        </div>`).join('') + `<a class="btn btn--ghost btn--sm mt-2" href="#signals">All signals →</a>`;
    }, { empty: { icon: 'icon-radar', text: 'No signals yet — they appear as the engine scans.', }, timeoutMs: 10000 });

    every(60000, () => { getScan().then(updateConnChip); });
  }

  function posTable(rows) {
    return `<div class="tbl-wrap"><table class="tbl tbl--collapse">
      <thead><tr><th>Pair</th><th>Side</th><th class="r">Entry</th><th class="r">Stop / Target</th><th class="r">Size</th></tr></thead>
      <tbody>${rows.map(p => `
        <tr>
          <td data-label="Pair"><b>${esc(String(p.symbol).split('/')[0])}</b></td>
          <td data-label="Side">${dirChip(p.direction)}</td>
          <td data-label="Entry" class="r num">${fmtPrice(p.entry_price)}</td>
          <td data-label="Stop / Target" class="r num muted">${fmtPrice(p.stop_loss)} / ${fmtPrice(p.take_profit)}</td>
          <td data-label="Size" class="r num">${fmtMoney(p.size_usd, 0)}</td>
        </tr>`).join('')}</tbody></table></div>`;
  }

  /* ═══════════════ MARKETS ═══════════════ */
  async function renderMarkets() {
    container.innerHTML = viewHead('Markets', 'Live exchange data');
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel panel--primary" id="p-chart">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>Price chart
            <span class="right">
              <label class="visually-hidden" for="chartSym">Symbol</label>
              <select class="input" id="chartSym" style="width:auto;padding:5px 9px;font-size:var(--fs-sm)"></select>
              <label class="visually-hidden" for="chartGran">Timeframe</label>
              <select class="input" id="chartGran" style="width:auto;padding:5px 9px;font-size:var(--fs-sm)">
                <option value="15min">15m</option><option value="1h" selected>1H</option><option value="4h">4H</option><option value="1d">1D</option>
              </select>
            </span></h2>
          <div id="c-chart"><div class="skel"></div><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-insight"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>AI decision picture
          <span class="right muted small">the same read the engine trades off</span></h2>
          <div id="c-insight"><div class="skel"></div><div class="skel"></div></div>
        </section>
        <div class="grid grid-2">
          <section class="panel" id="p-depth"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>Order book</h2><div id="c-depth"><div class="skel"></div></div></section>
          <section class="panel" id="p-funding"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-bolt"></use></svg>Funding rate</h2><div id="c-funding"><div class="skel"></div></div></section>
        </div>
        <div class="grid grid-2">
          <section class="panel" id="p-xfunding"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Cross-venue funding</h2><div id="c-xfunding"><div class="skel"></div></div></section>
          <section class="panel" id="p-arb"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>Funding-arb paper tracker</h2><div id="c-arb"><div class="skel"></div></div></section>
        </div>
        <section class="panel" id="p-universe"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Universe
          <span class="right"><label class="visually-hidden" for="uniSearch">Filter symbols</label><input class="input" id="uniSearch" placeholder="Filter…" style="width:130px;padding:5px 9px;font-size:var(--fs-sm)"></span></h2>
          <div id="c-universe"><div class="skel"></div><div class="skel"></div></div>
        </section>
      </div>`);

    const symSel = document.getElementById('chartSym');
    const DEFAULTS = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];
    symSel.innerHTML = DEFAULTS.map(s => `<option value="${s}">${s.replace('USDT','')}/USDT</option>`).join('');

    // Cross-venue intelligence (one shared fetch; hourly bot-pushed data).
    const reportsP = getReports();
    renderPanel(C('xfunding'), async () => {
      const rep = await reportsP;
      const rows = rep?.funding?.rows || [];
      if (!rows.length) return null;
      const venues = [...new Set(rows.flatMap(r => Object.keys(r.rates || {})))];
      return `<p class="muted small">Annualized funding APR by venue — carry pays long where it's low, short where it's high. ${esc(reportAge(rep))}</p>
        <div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Coin</th>${venues.map(v => `<th class="r">${esc(v)}</th>`).join('')}<th class="r">Spread</th></tr></thead>
        <tbody>${rows.slice(0, 10).map(r => `<tr><td><b>${esc(r.base)}</b></td>
          ${venues.map(v => `<td class="num r">${r.rates && r.rates[v] != null ? Number(r.rates[v]).toFixed(1) + '%' : '—'}</td>`).join('')}
          <td class="num r" title="long ${esc(r.long_venue || '')} / short ${esc(r.short_venue || '')}"><b>${Number(r.spread_apr || 0).toFixed(1)}%</b></td></tr>`).join('')}</tbody></table></div>`;
    }, { empty: { icon: 'icon-globe', text: 'Cross-venue funding arrives when the bot pushes its hourly report.' } });

    renderPanel(C('arb'), async () => {
      const rep = await reportsP;
      const arb = rep?.arb;
      if (!arb || !(arb.carries || []).length) return null;
      const total = arb.carries.reduce((a, c) => a + (Number(c.earned_usd) || 0), 0);
      return `<p class="muted small">What $${Number(arb.notional_usd || 1000).toLocaleString()} would have earned holding each spread — an evidence tracker; nothing is traded. ${esc(reportAge(rep))}</p>
        <div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Coin</th><th class="r">Paper carry</th><th class="r">Held / seen</th><th class="r">Last spread</th></tr></thead>
        <tbody>${arb.carries.slice(0, 8).map(c => `<tr><td><b>${esc(c.base)}</b></td>
          <td class="num r ${pnlClass(c.earned_usd)}">$${Number(c.earned_usd || 0).toFixed(2)}</td>
          <td class="num r">${Number(c.held_hours || 0).toFixed(0)}h / ${Number(c.observed_hours || 0).toFixed(0)}h</td>
          <td class="num r">${Number(c.last_spread_apr || 0).toFixed(1)}%</td></tr>`).join('')}</tbody></table></div>
        <p class="small muted mt-2">Total paper carry <b class="num ${pnlClass(total)}">$${total.toFixed(2)}</b> over ${arb.snapshots || 0} snapshots. A real 2-venue round trip costs ~0.24% of notional in fees.</p>`;
    }, { empty: { icon: 'icon-coin', text: 'The paper arb tracker fills in as the bot records hourly funding snapshots.' } });

    const drawAll = () => { drawChart(); drawDepth(); drawFunding(); drawInsight(); };
    symSel.addEventListener('change', () => {
      drawAll();
      saveUserProfile({ prefs: { chart_symbol: symSel.value } });
    });
    document.getElementById('chartGran').addEventListener('change', () => {
      drawChart(); drawInsight();
      saveUserProfile({ prefs: { chart_tf: document.getElementById('chartGran').value } });
    });

    let tvChart = null, tvSeries = null, tvTimer = null;
    async function drawChart() {
      if (tvTimer) { clearInterval(tvTimer); tvTimer = null; }
      const sym = symSel.value, gran = document.getElementById('chartGran').value;
      let rows = null;
      await renderPanel(C('chart'), async () => {
        const r = await fetchJSON(`/api/market/candles/${sym}?granularity=${gran}&limit=200`, { auth: false, timeoutMs: 12000 });
        rows = r.data?.data;
        if (!rows || !rows.length) return null;
        // TradingView Lightweight Charts (vendored, self-hosted). Fall back to
        // the SVG renderer when the library failed to load.
        if (!window.LightweightCharts) return candleSvg(rows);
        return `<div id="tvChart" style="height:340px"></div>`;
      }, { empty: { icon: 'icon-chart', text: 'No candle data for this pair right now.' }, errorText: 'Market data unavailable — retry in a moment.' });

      const host = document.getElementById('tvChart');
      if (!host || !window.LightweightCharts || !rows || !rows.length) return;
      if (tvChart) { try { tvChart.remove(); } catch (e) { /* host already gone */ } tvChart = null; tvSeries = null; }
      const data = rows.map(c => ({
        time: Math.floor(+c[0] / 1000),
        open: +c[1], high: +c[2], low: +c[3], close: +c[4],
      })).sort((a, b) => a.time - b.time)
        // Bitget can echo a candle twice at the live edge — TV requires
        // strictly ascending times.
        .filter((c, i, arr) => i === 0 || c.time > arr[i - 1].time);
      if (!data.length) return;
      const css = getComputedStyle(document.documentElement);
      tvChart = LightweightCharts.createChart(host, {
        layout: {
          background: { type: 'solid', color: 'transparent' },
          textColor: css.getPropertyValue('--text-3').trim() || '#8f99ab',
          fontFamily: css.getPropertyValue('--font-data').trim() || 'monospace',
        },
        grid: {
          vertLines: { color: 'rgba(49,57,80,.35)' },
          horzLines: { color: 'rgba(49,57,80,.35)' },
        },
        rightPriceScale: { borderColor: 'rgba(49,57,80,.6)' },
        timeScale: { borderColor: 'rgba(49,57,80,.6)', timeVisible: true, secondsVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        autoSize: true,
      });
      tvSeries = tvChart.addCandlestickSeries({
        upColor: '#2fbf71', downColor: '#e5484d',
        wickUpColor: '#2fbf71', wickDownColor: '#e5484d',
        borderVisible: false,
      });
      tvSeries.setData(data);

      // Indicator overlays computed from the same candles: EMA20/50 + VWAP.
      const emaLine = (period, color) => {
        const k = 2 / (period + 1);
        let ema = null;
        const pts = data.map(c => {
          ema = ema === null ? c.close : c.close * k + ema * (1 - k);
          return { time: c.time, value: ema };
        }).slice(period);
        const s = tvChart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        s.setData(pts);
      };
      emaLine(20, 'rgba(63,182,255,.75)');
      emaLine(50, 'rgba(154,167,255,.65)');
      // VWAP over the loaded window (Bitget candles carry base volume at [5]).
      const volByTime = new Map(rows.map(c => [Math.floor(+c[0] / 1000), +c[5] || 0]));
      let cumPV = 0, cumV = 0;
      const vwapPts = data.map(c => {
        const v = volByTime.get(c.time) || 0;
        cumPV += ((c.high + c.low + c.close) / 3) * v; cumV += v;
        return cumV > 0 ? { time: c.time, value: cumPV / cumV } : null;
      }).filter(Boolean);
      if (vwapPts.length) {
        tvChart.addLineSeries({ color: 'rgba(185,197,214,.55)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
          .setData(vwapPts);
      }

      // Entry/SL/TP price lines when the user has an open position on this pair.
      try {
        const pf = LOGGED_IN ? await getPortfolio() : null;
        const norm = s => String(s || '').replace(/[/:]/g, '').replace(/USDT.*$/, 'USDT');
        const pos = (pf?.open_positions || []).find(p => norm(p.symbol) === norm(sym));
        if (pos && tvSeries) {
          const line = (price, color, title) => price > 0 && tvSeries.createPriceLine({
            price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title,
          });
          line(+pos.entry_price, 'rgba(63,182,255,.9)', `entry ${String(pos.direction || '').toUpperCase()}`);
          line(+pos.stop_loss, '#e5484d', 'SL');
          line(+pos.take_profit, '#2fbf71', 'TP');
        }
      } catch (e) { /* overlays are best-effort */ }

      tvChart.timeScale().fitContent();

      // Live updates: refresh the last candle while the chart stays mounted.
      if (tvTimer) clearInterval(tvTimer);
      tvTimer = setInterval(async () => {
        if (!document.getElementById('tvChart') || document.visibilityState !== 'visible') return;
        try {
          const r2 = await fetchJSON(`/api/market/candles/${sym}?granularity=${gran}&limit=2`, { auth: false, timeoutMs: 8000 });
          const last = (r2.data?.data || []).map(c => ({
            time: Math.floor(+c[0] / 1000), open: +c[1], high: +c[2], low: +c[3], close: +c[4],
          })).sort((a, b) => a.time - b.time).pop();
          const lastLoaded = data[data.length - 1];
          if (last && tvSeries && last.time >= lastLoaded.time) tvSeries.update(last);
        } catch (e) { /* transient — next tick retries */ }
      }, 15000);
    }
    async function drawDepth() {
      renderPanel(C('depth'), async () => {
        const sym = symSel.value;
        const r = await fetchJSON(`/api/market/depth/${sym}`, { auth: false, timeoutMs: 10000 });
        const d = r.data?.data;
        if (!d || !d.asks) return null;
        const rows = (side, arr) => arr.slice(0, 6).map(([p, q]) => `
          <div class="kv-row"><span class="num ${side === 'bid' ? 'pos' : 'neg'}">${fmtPrice(parseFloat(p))}</span><b class="muted">${fmtK(parseFloat(q))}</b></div>`).join('');
        return `<div class="grid grid-2" style="gap:var(--s3)">
          <div><div class="stat"><div class="k">Bids</div></div>${rows('bid', d.bids || [])}</div>
          <div><div class="stat"><div class="k">Asks</div></div>${rows('ask', d.asks || [])}</div>
        </div>`;
      }, { empty: { text: 'Order book unavailable.' } });
    }
    async function drawFunding() {
      renderPanel(C('funding'), async () => {
        const sym = symSel.value;
        const r = await fetchJSON(`/api/market/funding/${sym}`, { auth: false, timeoutMs: 10000 });
        const raw = r.data?.data;
        const item = Array.isArray(raw) ? raw[0] : raw;
        const rate = parseFloat(item?.fundingRate);
        if (!isFinite(rate)) return null;
        const pct = rate * 100;
        return `<div class="stat"><div class="k">${esc(symSel.value)} current funding</div>
          <div class="v big num ${pnlClass(pct)}" style="font-size:var(--fs-xl)">${signed(pct, 4)}%</div>
          <div class="d muted">${pct >= 0 ? 'Longs pay shorts' : 'Shorts pay longs'} · settles every 8h (00/08/16 UTC)</div></div>`;
      }, { empty: { text: 'Funding data unavailable.' } });
    }

    // AI decision picture — the SAME read the engine trades off: directional
    // confluence, the voters behind it, key levels, fair-value gaps and flow.
    async function drawInsight() {
      renderPanel(C('insight'), async () => {
        const base = symSel.value.replace('USDT', '');
        // Chart uses Bitget granularity (15min/1h/4h/1d); the insight bridge feeds
        // ccxt fetch_ohlcv, which wants 15m/1h/4h/1d — map the one that differs.
        const gran = document.getElementById('chartGran').value;
        const tf = ({ '15min': '15m' })[gran] || gran;
        const r = await fetchJSON(
          `/api/insight?symbol=${encodeURIComponent(base + '/USDT')}&timeframe=${tf}&limit=200`,
          { auth: false, timeoutMs: 12000 });
        const d = r.data;
        if (!d || d.error || typeof d.confluence !== 'number') return null;

        // Bridge confluence is a 0..1 conviction score: 0.5 = neutral, >0.5
        // bullish, <0.5 bearish (analyzer._score_confluence). Map to a signed
        // -100..+100 lean for display; 0.5 sits dead-centre on the bar.
        const conf = d.confluence;
        const dir = (conf - 0.5) * 2;              // -1 (bearish) .. +1 (bullish)
        const lean = dir > 0.1 ? 'Bullish' : dir < -0.1 ? 'Bearish' : 'Neutral';
        const leanCls = dir > 0.1 ? 'up' : dir < -0.1 ? 'down' : '';
        const pos = Math.max(0, Math.min(100, conf * 100));  // 0..100 on the bar

        // Confluence meter (bearish ← 0 → bullish).
        const meter = `
          <div class="stat"><div class="k">Directional confluence</div>
            <div class="v big ${leanCls}" style="font-size:var(--fs-xl)">${lean} <span class="num" style="font-size:var(--fs-md)">${signed(dir * 100, 0)}</span></div>
            <div style="position:relative;height:8px;border-radius:5px;margin-top:8px;background:linear-gradient(90deg,var(--down-dim),var(--surface-3) 45% 55%,var(--up-dim))">
              <div style="position:absolute;top:-3px;left:calc(${pos}% - 2px);width:4px;height:14px;border-radius:2px;background:var(--text)"></div>
            </div>
            <div class="d muted small mt-2">Regime <b>${esc(String(d.regime || '—').replace(/_/g, ' '))}</b> · price ${fmtPrice(d.price)} · ATR ${fmtPrice(d.atr)}</div>
          </div>`;

        // Voters — WHY it leans this way (top contributors by |vote·weight|).
        const votes = (d.votes || [])
          .map(v => ({ ...v, c: (v.vote || 0) * (v.weight || 0) }))
          .filter(v => Math.abs(v.c) > 1e-6)
          .sort((a, b) => Math.abs(b.c) - Math.abs(a.c))
          .slice(0, 8);
        const maxC = votes.length ? Math.max(...votes.map(v => Math.abs(v.c))) : 1;
        const voteRows = votes.length ? votes.map(v => {
          const w = Math.max(4, Math.round(Math.abs(v.c) / maxC * 100));
          const bull = v.c >= 0;
          return `<div class="kv-row" style="align-items:center">
            <span class="small" style="font-family:var(--font-data);flex:0 0 42%">${esc(String(v.name).replace(/_/g, ' ').slice(0, 26))}</span>
            <span style="flex:1;height:7px;border-radius:4px;background:var(--surface-3);position:relative;overflow:hidden">
              <span style="position:absolute;${bull ? 'left' : 'right'}:50%;width:${w / 2}%;height:100%;background:var(${bull ? '--up' : '--down'})"></span>
              <span style="position:absolute;left:50%;top:-2px;width:1px;height:11px;background:var(--line-2)"></span>
            </span>
            <span class="num small ${bull ? 'up' : 'down'}" style="flex:0 0 48px;text-align:right">${signed(v.c * 100, 0)}</span>
          </div>`;
        }).join('') : '<div class="muted small">No active voters this bar.</div>';

        // Key levels (nearest to price first).
        const px = d.price || 0;
        const levels = (d.levels || [])
          .slice().sort((a, b) => Math.abs(a.price - px) - Math.abs(b.price - px)).slice(0, 6);
        const levelRows = levels.length ? levels.map(lv => {
          const above = lv.price >= px;
          return `<tr>
            <td data-label="Level"><span class="chip ${above ? 'chip--down' : 'chip--up'}">${above ? 'RES' : 'SUP'}</span> <span class="muted small">${esc(String(lv.kind || '').replace(/_/g, ' '))}</span></td>
            <td data-label="Price" class="r num">${fmtPrice(lv.price)}</td>
            <td data-label="Score" class="r num muted">${(lv.score ?? 0).toFixed(1)} · ${lv.touches || 0}×</td></tr>`;
        }).join('') : '';
        const levelsBlock = levelRows ? `<div class="tbl-wrap"><table class="tbl tbl--collapse">
          <thead><tr><th>Level</th><th class="r">Price</th><th class="r">Score · touches</th></tr></thead>
          <tbody>${levelRows}</tbody></table></div>` : '';

        // Fair-value gaps (unfilled) + flow footer.
        const openGaps = (d.fvgs || []).filter(g => !g.filled).slice(0, 4);
        const gapsLine = openGaps.length
          ? `<div class="muted small mt-2">Open FVGs: ${openGaps.map(g => `${g.kind === 'bull' || g.kind === 'bullish' ? '▲' : '▼'} ${fmtPrice(g.bottom)}–${fmtPrice(g.top)}`).join(' · ')}</div>`
          : '';
        const cvdVal = (d.cvd && (typeof d.cvd === 'object' ? d.cvd.cum_delta_usd : d.cvd));
        const flowBits = [];
        if (isFinite(parseFloat(cvdVal))) flowBits.push(`CVD <b class="${pnlClass(parseFloat(cvdVal))}">${signed(parseFloat(cvdVal), 0)}</b>`);
        if (typeof d.premium_discount === 'number') flowBits.push(`Prem/disc <b>${signed(d.premium_discount * 100, 0)}%</b>`);
        const rs = d.risk_state || {};
        if (rs.latched) flowBits.push('<span class="chip chip--warn">entries gated</span>');
        const flowLine = flowBits.length ? `<div class="muted small mt-2">${flowBits.join(' · ')}</div>` : '';

        return `<div class="stack" style="gap:var(--s3)">
          ${meter}
          <div><div class="k muted small mb-2" style="text-transform:uppercase;letter-spacing:.08em">Why — confluence voters</div>${voteRows}</div>
          ${levelsBlock}
          ${gapsLine}${flowLine}
          <p class="muted small">The engine's own read — not personal advice. Confirmations still run the full risk gate.</p>
        </div>`;
      }, {
        empty: { icon: 'icon-sparkle', text: 'Decision picture unavailable — the analysis bridge may be offline for this pair.' },
        errorText: 'Analysis bridge unreachable — retry in a moment.',
      });
    }

    async function drawUniverse() {
      renderPanel(C('universe'), async () => {
        const [tickers, scan, prof] = await Promise.all([getTickers(), getScan(), getUserProfile()]);
        updateConnChip();
        const filter = (document.getElementById('uniSearch')?.value || '').toUpperCase();
        const scanSyms = scan?.symbols || {};
        const pinned = new Set(prof.watchlist || []);
        let rows = Object.values(tickers);
        if (!rows.length) return null;
        if (filter) rows = rows.filter(t => t.symbol.includes(filter));
        // Watchlist pins float to the top (kept across devices via the
        // profile API); the rest sort by 24h volume as before.
        rows.sort((a, b) => {
          const pa = pinned.has(a.symbol) ? 1 : 0, pb = pinned.has(b.symbol) ? 1 : 0;
          if (pa !== pb) return pb - pa;
          return parseFloat(b.quoteVolume || 0) - parseFloat(a.quoteVolume || 0);
        });
        return `<div class="tbl-wrap"><table class="tbl tbl--collapse">
          <thead><tr><th aria-label="Pinned"></th><th>Pair</th><th class="r">Price</th><th class="r">24h</th><th class="r">Volume</th><th>Engine</th></tr></thead>
          <tbody>${rows.slice(0, 30).map(t => {
            const chg = parseFloat(t.change24h) * 100;
            const tag = scanSyms[t.symbol];
            const isPinned = pinned.has(t.symbol);
            return `<tr>
              <td data-label="Pin"><button class="btn btn--ghost btn--sm" data-pin="${esc(t.symbol)}" type="button" title="${isPinned ? 'Unpin from watchlist' : 'Pin to watchlist'}" aria-pressed="${isPinned}" style="padding:2px 7px">${isPinned ? '★' : '☆'}</button></td>
              <td data-label="Pair"><b>${esc(t.symbol.replace('USDT', ''))}</b><span class="muted">/USDT</span></td>
              <td data-label="Price" class="r num">${fmtPrice(parseFloat(t.lastPr))}</td>
              <td data-label="24h" class="r num ${pnlClass(chg)}">${signed(chg, 2)}%</td>
              <td data-label="Volume" class="r num muted">${fmtK(parseFloat(t.quoteVolume))}</td>
              <td data-label="Engine">${tag ? `<span class="chip ${tag.status === 'setup' ? 'chip--gold' : tag.status === 'alert' ? 'chip--warn' : ''}">${esc(tag.status_label || '')}</span>` : '<span class="muted small">—</span>'}</td>
            </tr>`;
          }).join('')}</tbody></table></div>`;
      }, { empty: { text: 'No market data — the exchange proxy may be unreachable.' }, timeoutMs: 12000 });
    }
    document.getElementById('uniSearch').addEventListener('input', drawUniverse);
    // Pin toggles — delegated so it survives the 15s re-render loop.
    C('universe').addEventListener('click', async (e) => {
      const b = e.target.closest('button[data-pin]');
      if (!b) return;
      const sym = b.dataset.pin;
      const prof = await getUserProfile();
      const wl = new Set(prof.watchlist || []);
      if (wl.has(sym)) wl.delete(sym);
      else {
        if (wl.size >= 20) { toast('Watchlist is capped at 20 symbols.'); return; }
        wl.add(sym);
      }
      const ok = await saveUserProfile({ watchlist: [...wl] });
      if (!ok) { toast('Could not save your watchlist — try again.'); return; }
      drawUniverse();
    });

    // Restore saved chart prefs BEFORE the first draw (cross-device memory).
    try {
      const prof = await getUserProfile();
      const p = prof?.prefs || {};
      if (p.chart_symbol && [...symSel.options].some(o => o.value === p.chart_symbol)) {
        symSel.value = p.chart_symbol;
      }
      const granSel = document.getElementById('chartGran');
      if (p.chart_tf && [...granSel.options].some(o => o.value === p.chart_tf)) {
        granSel.value = p.chart_tf;
      }
    } catch (e) { /* defaults are fine */ }
    drawAll(); drawUniverse();
    every(20000, drawChart);
    every(15000, drawUniverse);
    every(30000, () => { drawDepth(); drawFunding(); });
  }

  // Candlestick SVG: grid + wicks/bodies + last-price line, tabular labels.
  function candleSvg(rows) {
    // Bitget v2: [ts, open, high, low, close, ...]; API returns newest-last or
    // newest-first depending on endpoint — normalize to chronological.
    const cs = rows.map(r => ({ t: +r[0], o: +r[1], h: +r[2], l: +r[3], c: +r[4] }))
      .sort((a, b) => a.t - b.t);
    const W = 800, H = 300, PAD = { l: 8, r: 62, t: 12, b: 8 };
    const min = Math.min(...cs.map(c => c.l)), max = Math.max(...cs.map(c => c.h));
    const span = (max - min) || 1;
    const x = i => PAD.l + i * ((W - PAD.l - PAD.r) / cs.length);
    const y = v => PAD.t + (max - v) / span * (H - PAD.t - PAD.b);
    const cw = Math.max(2, (W - PAD.l - PAD.r) / cs.length - 2);
    let out = '';
    for (let g = 0; g <= 4; g++) {
      const v = min + span * g / 4;
      out += `<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" stroke-width="1"/>
        <text x="${W - PAD.r + 6}" y="${y(v) + 4}" fill="var(--text-3)" font-size="11" font-family="var(--font-data)">${fmtPrice(v).replace('$', '')}</text>`;
    }
    cs.forEach((c, i) => {
      const up = c.c >= c.o;
      const col = up ? 'var(--up)' : 'var(--down)';
      const bx = x(i);
      out += `<line x1="${bx + cw / 2}" x2="${bx + cw / 2}" y1="${y(c.h)}" y2="${y(c.l)}" stroke="${col}" stroke-width="1"/>
        <rect x="${bx}" y="${y(Math.max(c.o, c.c))}" width="${cw}" height="${Math.max(1, Math.abs(y(c.o) - y(c.c)))}" fill="${col}"/>`;
    });
    const last = cs[cs.length - 1];
    out += `<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(last.c)}" y2="${y(last.c)}" stroke="var(--gold)" stroke-width="1" stroke-dasharray="4 4"/>
      <text x="${W - PAD.r + 6}" y="${y(last.c) + 4}" fill="var(--gold-bright)" font-size="11" font-weight="700" font-family="var(--font-data)">${fmtPrice(last.c).replace('$', '')}</text>`;
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Price chart" style="display:block">${out}</svg>`;
  }

  /* ═══════════════ SIGNALS ═══════════════ */
  async function renderSignals() {
    container.innerHTML = viewHead('Signals', 'Every setup the engine generates — taken or not');
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-sstats"><div id="c-sstats"><div class="skel"></div></div></section>
        <section class="panel panel--primary" id="p-stream"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg>Signal stream</h2><div id="c-stream"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel" id="p-sinsights"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>What works
          <span class="right muted small">win-rate by pattern & symbol (resolved signals)</span></h2><div id="c-sinsights"><div class="skel"></div></div></section>
      </div>`);

    renderPanel(C('sstats'), async () => {
      const r = await fetchJSON('/api/signals/stats', { auth: false });
      const s = r.data;
      if (!s || !s.resolved) return null;
      return `<div class="stat-row">
        <div class="stat"><div class="k">Resolved</div><div class="v">${s.resolved}</div></div>
        <div class="stat"><div class="k">Win rate</div><div class="v">${fmt(s.win_rate, 1)}%</div></div>
        <div class="stat"><div class="k">Wins / Losses</div><div class="v">${s.wins} / ${s.losses}</div></div>
        <div class="stat"><div class="k">Net PnL</div><div class="v num ${pnlClass(s.net_pnl)}">${signed(s.net_pnl)}</div></div>
      </div>`;
    }, { empty: { icon: 'icon-radar', text: 'No resolved signals yet — outcomes appear once signals hit target or stop.' } });

    async function drawStream() {
      renderPanel(C('stream'), async () => {
        const r = await fetchJSON('/api/signals?limit=40', { auth: false });
        const sigs = r.data?.signals || [];
        if (!sigs.length) return null;
        return `<div class="tbl-wrap"><table class="tbl tbl--collapse">
          <thead><tr><th>Signal</th><th class="r">Conf.</th><th class="r">Entry</th><th class="r">Stop / Target</th><th class="r">R:R</th><th>Status</th><th class="r">Age</th></tr></thead>
          <tbody>${sigs.map(s => {
            const status = s.pnl != null
              ? `<span class="chip ${Number(s.pnl) > 0 ? 'chip--up' : 'chip--down'}">${Number(s.pnl) > 0 ? '✓ WIN' : '✗ LOSS'}</span>`
              : `<span class="chip">${esc(s.status || 'NEW')}</span>`;
            return `<tr>
              <td data-label="Signal">${dirChip(s.direction)} <b>${esc(s.symbol)}</b><div class="muted small">${esc(s.pattern || '')}</div></td>
              <td data-label="Conf." class="r num">${Math.round((s.confidence || 0) * 100)}%</td>
              <td data-label="Entry" class="r num">${fmtPrice(s.entry_price)}</td>
              <td data-label="Stop / Target" class="r num muted">${fmtPrice(s.stop_loss)} / ${fmtPrice(s.take_profit)}</td>
              <td data-label="R:R" class="r num">${fmt(s.rr, 1)}</td>
              <td data-label="Status">${status}</td>
              <td data-label="Age" class="r muted small">${fmtAgo(s.created_at)}</td>
            </tr>`;
          }).join('')}</tbody></table></div>`;
      }, { empty: { icon: 'icon-radar', text: 'No signals yet. They stream in as the engine scans the market.' } });
    }

    renderPanel(C('sinsights'), async () => {
      const r = await fetchJSON('/api/signals/analytics', { auth: false });
      const a = r.data;
      if (!a || !(a.by_pattern?.length || a.by_symbol?.length)) return null;
      const bars = (rows, key) => rows.slice(0, 6).map(g => {
        const wr = g.n ? Math.round(g.wins / g.n * 100) : 0;
        return `<div class="kv-row"><span>${esc(g[key] || '(none)')} <span class="muted small">×${g.n}</span></span>
          <b class="${wr >= 50 ? 'pos' : 'neg'}">${wr}%</b></div>`;
      }).join('');
      return `<div class="grid grid-2">
        <div><div class="stat mb-2"><div class="k">By pattern</div></div>${bars(a.by_pattern || [], 'pattern') || '<p class="muted small">No data.</p>'}</div>
        <div><div class="stat mb-2"><div class="k">By symbol</div></div>${bars(a.by_symbol || [], 'symbol') || '<p class="muted small">No data.</p>'}</div>
      </div>`;
    }, { empty: { text: 'Insights build up as signals resolve.' } });

    drawStream();
    every(30000, drawStream);
  }

  /* ═══════════════ TRADE ═══════════════ */
  async function renderTrade() {
    container.innerHTML = viewHead('Trade', 'Manual trading through the engine\'s risk gate');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend', `<section class="panel">${loginGate('Log in to place paper trades — the same risk engine, zero risk.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <div id="tradeModeNote"></div>
        <div class="grid grid-main">
          <section class="panel panel--primary" id="p-ticket">
            <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-target"></use></svg>Order ticket</h2>
            <form id="ticketForm" class="stack" novalidate>
              <div class="form-row">
                <div class="field"><label for="tDir">Direction</label>
                  <select class="input" id="tDir"><option value="LONG">▲ Long</option><option value="SHORT">▼ Short</option></select></div>
                <div class="field"><label for="tSym">Symbol</label>
                  <input class="input" id="tSym" maxlength="15" placeholder="SOL" style="text-transform:uppercase" autocomplete="off"></div>
                <div class="field"><label for="tMargin">Margin $ <span class="muted">(optional)</span></label>
                  <input class="input input--num" id="tMargin" type="number" step="any" min="0" placeholder="Auto"></div>
              </div>
              <div class="form-row">
                <div class="field"><label for="tEntry">Entry (limit)</label><input class="input input--num" id="tEntry" type="number" step="any" min="0" placeholder="0.00"></div>
                <div class="field"><label for="tSl">Stop loss</label><input class="input input--num" id="tSl" type="number" step="any" min="0" placeholder="0.00"></div>
                <div class="field"><label for="tTp">Take profit</label><input class="input input--num" id="tTp" type="number" step="any" min="0" placeholder="0.00"></div>
              </div>
              <p id="tPreview" class="small muted" aria-live="polite">Fill in entry, stop, and target to preview risk/reward.</p>
              <div class="row">
                <button class="btn btn--primary" type="submit">Review trade</button>
                <span id="tMsg" class="small muted" aria-live="polite"></span>
              </div>
              <p class="muted small">Every trade re-runs the full risk gate at confirmation. Limit order, same path as the Telegram bot.</p>
            </form>
          </section>
          <section class="panel" id="p-sizer">
            <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>Position sizer</h2>
            <div class="stack">
              <div class="field"><label for="szRisk">Risk amount ($)</label><input class="input input--num" id="szRisk" type="number" step="any" min="0" placeholder="25"></div>
              <div class="field"><label for="szLev">Leverage</label><input class="input input--num" id="szLev" type="number" step="1" min="1" value="10"></div>
              <p id="szOut" class="small muted">Uses the ticket's entry and stop to size the position for your risk.</p>
            </div>
          </section>
        </div>
        <section class="panel" id="p-tpos"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>Open positions</h2><div id="c-tpos"><div class="skel"></div></div></section>
      </div>`);

    // Mode note: quiet chip, not a blocker.
    getPortfolio().then(pf => {
      updateModeChip(pf);
      const el = document.getElementById('tradeModeNote');
      if (!el) return;
      if (pf && pf.linked === false) {
        el.innerHTML = `<div class="section-note"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>
          Paper mode — trades execute on your paper portfolio. Live trading requires a linked Telegram account and operator approval.</div>`;
      }
    });

    const $ = id => document.getElementById(id);
    function preview() {
      const dir = $('tDir').value, e = parseFloat($('tEntry').value), sl = parseFloat($('tSl').value), tp = parseFloat($('tTp').value);
      const out = $('tPreview');
      if (!e || !sl || !tp) { out.textContent = 'Fill in entry, stop, and target to preview risk/reward.'; return; }
      const sideOk = dir === 'LONG' ? (sl < e && tp > e) : (sl > e && tp < e);
      if (!sideOk) {
        out.innerHTML = `<span class="neg">${dir === 'LONG' ? 'A long needs the stop below entry and target above.' : 'A short needs the stop above entry and target below.'}</span>`;
        return;
      }
      const rr = Math.abs(tp - e) / Math.abs(e - sl);
      out.innerHTML = `R:R <b class="num" style="color:var(--gold-bright)">${rr.toFixed(2)}</b>
        · stop ${fmt(Math.abs(e - sl) / e * 100, 1)}% away · target ${fmt(Math.abs(tp - e) / e * 100, 1)}% away`;
      sizer();
    }
    function sizer() {
      const e = parseFloat($('tEntry').value), sl = parseFloat($('tSl').value);
      const risk = parseFloat($('szRisk').value), lev = Math.max(1, parseFloat($('szLev').value) || 1);
      const out = $('szOut');
      if (!e || !sl || !risk || e === sl) { out.textContent = "Uses the ticket's entry and stop to size the position for your risk."; return; }
      const qty = risk / Math.abs(e - sl);
      const notional = qty * e;
      out.innerHTML = `Size <b class="num" style="color:var(--gold-bright)">${qty.toFixed(4)}</b> units
        · notional <b class="num">${fmtMoney(notional)}</b> · margin <b class="num">${fmtMoney(notional / lev)}</b> @ ${lev}x`;
    }
    ['tDir', 'tEntry', 'tSl', 'tTp'].forEach(id => $(id).addEventListener('input', preview));
    ['szRisk', 'szLev'].forEach(id => $(id).addEventListener('input', sizer));

    document.getElementById('ticketForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = $('tMsg');
      const body = {
        direction: $('tDir').value,
        symbol: $('tSym').value.trim().toUpperCase(),
        entry: parseFloat($('tEntry').value),
        sl: parseFloat($('tSl').value),
        tp: parseFloat($('tTp').value),
      };
      const marginRaw = $('tMargin').value.trim();
      if (marginRaw !== '') body.margin = Number(marginRaw);
      if (!body.symbol || !body.entry || !body.sl || !body.tp) { msg.innerHTML = '<span class="neg">Symbol, entry, stop, and target are required.</span>'; return; }
      msg.textContent = 'Checking…';
      const r = await fetchJSON('/api/trade/propose', { method: 'POST', body, timeoutMs: 15000 }).catch(() => ({ ok: false, data: null }));
      if (!r.ok) { msg.innerHTML = `<span class="neg">${esc(r.data?.detail || r.data?.error || 'Proposal rejected.')}</span>`; return; }
      msg.textContent = '';
      openTradeModal(r.data.pending_trade, () => drawPositions());
    });

    async function drawPositions() {
      renderPanel(C('tpos'), async () => {
        const pf = await getPortfolio(true);
        const open = pf?.open_positions || [];
        if (!open.length) return null;
        return posTable(open);
      }, { empty: { icon: 'icon-target', text: 'No open positions — your confirmed trades appear here.' } });
    }
    drawPositions();
    document.addEventListener('rc:portfolio-changed', drawPositions);
  }

  // ── Trade confirm modal (shared with chat) ─────────────────────────────
  function openTradeModal(pt, onDone) {
    const modal = document.getElementById('tradeModal');
    const body = document.getElementById('tradeModalBody');
    const msg = document.getElementById('tradeModalMsg');
    msg.textContent = '';
    const live = pt.mode === 'LIVE';
    body.innerHTML = `
      <span class="mode-badge ${live ? 'mode-badge--live' : 'mode-badge--paper'}">${live ? 'LIVE — REAL MONEY' : 'PAPER'}</span>
      <div class="kv-row"><span>Pair</span><b>${esc(pt.symbol)}/USDT ${esc(pt.direction)}</b></div>
      <div class="kv-row"><span>Entry (limit)</span><b>$${fmt(pt.entry, 4)}</b></div>
      <div class="kv-row"><span>Stop loss</span><b>$${fmt(pt.sl, 4)} (−${fmt(pt.sl_pct, 1)}%)</b></div>
      <div class="kv-row"><span>Take profit</span><b>$${fmt(pt.tp, 4)} (+${fmt(pt.tp_pct, 1)}%)</b></div>
      <div class="kv-row"><span>Risk : reward</span><b>${fmt(pt.rr)}</b></div>
      <div class="kv-row"><span>Margin</span><b>${pt.margin_usd ? fmtMoney(pt.margin_usd, 0) : 'auto (risk-sized)'}</b></div>
      ${live ? '' : '<p class="muted small mt-2">Executes on your paper portfolio. The risk engine re-checks everything now.</p>'}`;
    modal.classList.remove('hidden');
    modal.hidden = false;
    const a11y = window.RC.modalA11y(modal);
    a11y.open(document.getElementById('tradeModalConfirm'));
    const onEsc = (e) => { if (e.key === 'Escape') close(); };
    const close = () => {
      modal.classList.add('hidden'); modal.hidden = true;
      document.removeEventListener('keydown', onEsc, true);
      a11y.close();  // release inert/trap + return focus to the trigger
    };
    document.addEventListener('keydown', onEsc, true);
    document.getElementById('tradeModalConfirm').onclick = async () => {
      msg.textContent = 'Executing…';
      const r = await fetchJSON('/api/trade/confirm', { method: 'POST', body: { trade_id: pt.trade_id }, timeoutMs: 35000 }).catch(() => ({ ok: false, data: null }));
      if (!r.ok) {
        const reason = r.data?.error === 'live_not_enabled'
          ? 'Live trading is not enabled for your account (your toggle + operator approval needed).'
          : (r.data?.detail || r.data?.error || 'Confirm failed.');
        msg.innerHTML = `<span class="neg">${esc(reason)}</span>`;
        return;
      }
      close();
      toast('Trade confirmed.', 'up');
      cache.portfolio = null;
      document.dispatchEvent(new CustomEvent('rc:portfolio-changed'));
      if (onDone) onDone(r.data.result_html);
    };
    document.getElementById('tradeModalCancel').onclick = async () => {
      await fetchJSON('/api/trade/cancel', { method: 'POST', body: { trade_id: pt.trade_id } }).catch(() => {});
      close();
      toast('Order cancelled — nothing was placed.');
    };
  }

  /* ═══════════════ PORTFOLIO ═══════════════ */
  async function renderPortfolio() {
    container.innerHTML = viewHead('Portfolio', 'Your equity, history, and journal');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend', `<section class="panel">${loginGate('Log in to track your trades, equity curve, and journal.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel panel--primary" id="p-pstats"><div id="c-pstats"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel" id="p-curve"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>Equity curve</h2><div id="c-curve"><div class="skel"></div></div></section>
        <div class="grid grid-2">
          <section class="panel" id="p-breakdown"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>By symbol</h2><div id="c-breakdown"><div class="skel"></div></div></section>
          <section class="panel" id="p-cal"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>Daily PnL — last 4 weeks</h2><div id="c-cal"><div class="skel"></div></div></section>
        </div>
        <section class="panel" id="p-edge"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-bolt"></use></svg>Edge metrics — the numbers pro desks track</h2><div id="c-edge"><div class="skel"></div></div></section>
        <section class="panel" id="p-hist"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>Trade history & journal</h2><div id="c-hist"><div class="skel"></div><div class="skel"></div></div></section>
      </div>`);

    // Fetch /api/portfolio first: triggers the DB write-through so the
    // DB-backed panels below reflect the freshest paper state. Keep pf around —
    // it carries the authoritative (truthful) equity + live_unavailable state,
    // which /api/trades/stats does not know about.
    const pf = await getPortfolio(true);
    updateModeChip(pf);

    renderPanel(C('pstats'), async () => {
      const r = await fetchJSON('/api/trades/stats');
      const s = r.data;
      if (!s || (s.equity == null && !s.total_trades)) return null;
      // Equity comes from pf (honest: null/unavailable in LIVE mode when the
      // balance can't be read), not the raw snapshot in /stats which could be
      // stale. The ratios (PnL, win, PF, Sharpe) still come from /stats.
      const equityCell = (pf && pf.live_unavailable)
        ? '<span class="muted" style="font-size:var(--fs-md)">unavailable</span>'
        : ((pf && pf.equity != null) ? fmtMoney(pf.equity)
          : (s.equity != null ? fmtMoney(s.equity) : '—'));
      return `<div class="stat-row">
        <div class="stat"><div class="k">Equity</div><div class="v big" style="font-size:var(--fs-xl)">${equityCell}</div></div>
        <div class="stat"><div class="k">Net PnL</div><div class="v num ${pnlClass(s.net_pnl)}">${signed(s.net_pnl)}</div></div>
        <div class="stat"><div class="k">Win rate</div><div class="v">${fmt(s.win_rate, 1)}%</div></div>
        <div class="stat"><div class="k">Profit factor</div><div class="v">${fmt(s.profit_factor)}</div></div>
        <div class="stat"><div class="k">Sharpe</div><div class="v">${fmt(s.sharpe)}</div></div>
        <div class="stat"><div class="k">Trades</div><div class="v">${s.total_trades} <span class="muted small">(${s.wins}W/${s.losses}L)</span></div></div>
      </div>`;
    }, { empty: { icon: 'icon-coin', text: 'No trading data yet — your stats build from the first closed trade.', cta: { label: 'Place a paper trade', href: '#trade' } } });

    renderPanel(C('curve'), async () => {
      const r = await fetchJSON('/api/trades/equity-curve');
      const snaps = r.data?.snapshots || [];
      if (snaps.length < 2) return null;
      return equitySvg(snaps);
    }, { empty: { icon: 'icon-chart', text: 'The equity curve draws once you have a few snapshots — trade and check back.' } });

    renderPanel(C('breakdown'), async () => {
      const r = await fetchJSON('/api/trades/breakdown');
      const rows = r.data?.by_symbol || [];
      if (!rows.length) return null;
      return rows.slice(0, 8).map(g => `
        <div class="kv-row"><span><b>${esc(String(g.symbol).split('/')[0])}</b> <span class="muted small">×${g.n}</span></span>
        <b class="num ${pnlClass(g.net_pnl)}">${signed(g.net_pnl)}</b></div>`).join('');
    }, { empty: { text: 'Per-symbol results appear after your first closed trades.' } });

    renderPanel(C('cal'), async () => {
      const r = await fetchJSON('/api/trades/history?limit=200');
      const trades = (r.data?.trades || []).filter(t => t.closed_at);
      if (!trades.length) return null;
      const byDay = {};
      trades.forEach(t => {
        const d = new Date(t.closed_at).toISOString().slice(0, 10);
        byDay[d] = (byDay[d] || 0) + (parseFloat(t.pnl) || 0);
      });
      let cells = '';
      for (let i = 27; i >= 0; i--) {
        const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
        const v = byDay[d];
        const bg = v == null ? 'var(--surface-2)' : v >= 0 ? 'var(--up-dim)' : 'var(--down-dim)';
        const bd = v == null ? 'var(--line)' : v >= 0 ? 'var(--up)' : 'var(--down)';
        cells += `<div title="${d}${v != null ? ` ${signed(v)}` : ''}" style="aspect-ratio:1;border-radius:4px;background:${bg};border:1px solid ${bd};display:flex;align-items:center;justify-content:center;font-size:10px;font-family:var(--font-data)">${v != null ? (v >= 0 ? '+' : '−') : ''}</div>`;
      }
      return `<div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px">${cells}</div>
        <p class="muted small mt-2">One cell per day, newest bottom-right. + profit · − loss.</p>`;
    }, { empty: { text: 'Your daily PnL calendar fills as trades close.' } });

    // Edge metrics — expectancy, payoff ratio, streaks, hold time: the numbers
    // professional traders manage by. All computed client-side from the same
    // closed-trade history the calendar uses; nothing is invented.
    renderPanel(C('edge'), async () => {
      const r = await fetchJSON('/api/trades/history?limit=200');
      const trades = (r.data?.trades || []).filter(t => t.closed_at);
      if (trades.length < 2) return null;
      const pnls = trades.map(t => parseFloat(t.pnl) || 0);
      const wins = pnls.filter(p => p > 0), losses = pnls.filter(p => p < 0);
      const expectancy = pnls.reduce((a, b) => a + b, 0) / pnls.length;
      const avgWin = wins.length ? wins.reduce((a, b) => a + b, 0) / wins.length : 0;
      const avgLoss = losses.length ? Math.abs(losses.reduce((a, b) => a + b, 0) / losses.length) : 0;
      const payoff = avgLoss > 0 ? avgWin / avgLoss : null;
      // Streaks over trades ordered oldest -> newest (history arrives newest-first).
      let winStreak = 0, lossStreak = 0, curW = 0, curL = 0;
      [...pnls].reverse().forEach(p => {
        if (p > 0) { curW++; curL = 0; } else if (p < 0) { curL++; curW = 0; }
        winStreak = Math.max(winStreak, curW); lossStreak = Math.max(lossStreak, curL);
      });
      const holds = trades
        .map(t => (new Date(t.closed_at) - new Date(t.opened_at)) / 3600000)
        .filter(h => isFinite(h) && h >= 0);
      const avgHold = holds.length ? holds.reduce((a, b) => a + b, 0) / holds.length : null;
      const holdTxt = avgHold == null ? '—' : avgHold >= 48 ? `${fmt(avgHold / 24, 1)}d` : `${fmt(avgHold, 1)}h`;
      const best = Math.max(...pnls), worst = Math.min(...pnls);
      return `<div class="stat-row">
        <div class="stat"><div class="k">Expectancy / trade</div><div class="v num ${pnlClass(expectancy)}">${signed(expectancy)}</div></div>
        <div class="stat"><div class="k">Payoff (avg win / loss)</div><div class="v">${payoff == null ? '—' : fmt(payoff)}</div></div>
        <div class="stat"><div class="k">Best streak</div><div class="v">${winStreak}W</div></div>
        <div class="stat"><div class="k">Worst streak</div><div class="v">${lossStreak}L</div></div>
        <div class="stat"><div class="k">Avg hold</div><div class="v">${holdTxt}</div></div>
        <div class="stat"><div class="k">Best / worst</div><div class="v num"><span class="${pnlClass(best)}">${signed(best)}</span> / <span class="${pnlClass(worst)}">${signed(worst)}</span></div></div>
      </div>
      <p class="muted small mt-2">Positive expectancy with payoff ≥ 1 is a durable edge. Ask the AI analyst to review any of it.</p>`;
    }, { empty: { icon: 'icon-bolt', text: 'Edge metrics unlock after a couple of closed trades.' } });

    renderPanel(C('hist'), async () => {
      const r = await fetchJSON('/api/trades/history?limit=25');
      const trades = r.data?.trades || [];
      if (!trades.length) return null;
      return `<div class="tbl-wrap"><table class="tbl tbl--collapse">
        <thead><tr><th>Trade</th><th class="r">Entry → Exit</th><th class="r">PnL</th><th class="r">Closed</th><th>Note</th></tr></thead>
        <tbody>${trades.map(t => `
          <tr>
            <td data-label="Trade">${dirChip(t.direction)} <b>${esc(String(t.symbol).split('/')[0])}</b></td>
            <td data-label="Entry → Exit" class="r num muted">${fmtPrice(t.entry_price)} → ${fmtPrice(t.exit_price)}</td>
            <td data-label="PnL" class="r num ${pnlClass(t.pnl)}">${signed(parseFloat(t.pnl))}</td>
            <td data-label="Closed" class="r muted small">${fmtAgo(t.closed_at)}</td>
            <td data-label="Note"><div class="row" style="gap:6px;align-items:center">
              <input class="input" style="padding:4px 8px;font-size:var(--fs-xs);min-width:110px" placeholder="Add note…" value="${esc(t.notes || '')}" data-trade-id="${t.id}" aria-label="Journal note for ${esc(t.symbol)}">
              <button class="btn btn--sm share-trade" type="button" title="Share this trade" aria-label="Share ${esc(String(t.symbol).split('/')[0])} trade" data-sym="${esc(String(t.symbol).split('/')[0])}" data-dir="${esc(t.direction)}" data-entry="${esc(String(t.entry_price))}" data-exit="${esc(String(t.exit_price))}">Share</button>
              <button class="btn btn--sm ask-ai" type="button" title="Ask the AI analyst to post-mortem this trade" aria-label="Post-mortem ${esc(String(t.symbol).split('/')[0])} trade with the AI analyst" data-sym="${esc(String(t.symbol).split('/')[0])}" data-dir="${esc(t.direction)}" data-entry="${esc(String(t.entry_price))}" data-exit="${esc(String(t.exit_price))}" data-pnl="${esc(String(t.pnl))}">Ask AI</button>
            </div></td>
          </tr>`).join('')}</tbody></table></div>`;
    }, { empty: { icon: 'icon-coin', text: 'No closed trades yet — your history and journal live here.', cta: { label: 'Place a paper trade', href: '#trade' } } });

    // Journal notes: save on change (PATCH, debounced by blur).
    container.addEventListener('change', async (e) => {
      const inp = e.target.closest('input[data-trade-id]');
      if (!inp) return;
      const r = await fetchJSON(`/api/trades/${inp.dataset.tradeId}/notes`, { method: 'PATCH', body: { notes: inp.value.slice(0, 500) } }).catch(() => ({ ok: false }));
      toast(r.ok ? 'Note saved.' : 'Could not save the note — try again.', r.ok ? 'up' : 'down');
    });

    // Share a closed trade — symbol · direction · PnL% only (never a dollar
    // amount, so account size never leaks), carrying the user's invite link so
    // a shared win also recruits. Native share sheet when available, else a
    // Telegram share intent.
    // Post-mortem coaching: hand the trade to the AI analyst (drawer opens in
    // place) with a prompt a trading coach would actually answer.
    container.addEventListener('click', (e) => {
      const btn = e.target.closest('.ask-ai');
      if (!btn || !window.RCChat) return;
      const { sym, dir, entry, exit, pnl } = btn.dataset;
      const won = (parseFloat(pnl) || 0) >= 0;
      window.RCChat.ask(
        `Post-mortem my ${dir} ${sym} trade: entry ${entry}, exit ${exit}, ` +
        `PnL ${pnl}. It ${won ? 'won' : 'lost'} — what did I do right or wrong, ` +
        `and what should I look for before taking this setup again?`);
    });

    container.addEventListener('click', async (e) => {
      const btn = e.target.closest('.share-trade');
      if (!btn) return;
      const { sym, dir, entry, exit } = btn.dataset;
      const e0 = parseFloat(entry), x0 = parseFloat(exit);
      let pctTxt = '';
      if (isFinite(e0) && isFinite(x0) && e0 > 0) {
        const pct = (String(dir).toUpperCase() === 'LONG' ? (x0 - e0) : (e0 - x0)) / e0 * 100;
        pctTxt = ` ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
      }
      let url = location.origin;
      try {
        const rr = await fetchJSON('/api/auth/referrals');
        if (rr.ok && rr.data?.code) url = `${location.origin}/?ref=${encodeURIComponent(rr.data.code)}`;
      } catch (_) { /* fall back to the bare origin */ }
      const text = `${dir} ${sym}${pctTxt} — traded with RUNECLAW, the autonomous AI trading agent.`;
      if (navigator.share) {
        try { await navigator.share({ text, url }); return; } catch (_) { /* cancelled / unsupported → fall through */ }
      }
      window.open(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`,
        '_blank', 'noopener');
    });
  }

  function equitySvg(snaps) {
    const pts = snaps.map(s => ({ t: new Date(s.snapshot_at).getTime(), v: parseFloat(s.equity) }))
      .filter(p => isFinite(p.v)).sort((a, b) => a.t - b.t);
    if (pts.length < 2) return '';
    const W = 800, H = 220, PAD = { l: 8, r: 64, t: 12, b: 8 };
    const min = Math.min(...pts.map(p => p.v)), max = Math.max(...pts.map(p => p.v));
    const span = (max - min) || 1;
    const x = i => PAD.l + i * ((W - PAD.l - PAD.r) / (pts.length - 1));
    const y = v => PAD.t + (max - v) / span * (H - PAD.t - PAD.b);
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
    const up = pts[pts.length - 1].v >= pts[0].v;
    const col = up ? 'var(--up)' : 'var(--down)';
    let grid = '';
    for (let g = 0; g <= 3; g++) {
      const v = min + span * g / 3;
      grid += `<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/>
        <text x="${W - PAD.r + 6}" y="${y(v) + 4}" fill="var(--text-3)" font-size="11" font-family="var(--font-data)">${fmtK(v)}</text>`;
    }
    const lastX = x(pts.length - 1), lastY = y(pts[pts.length - 1].v);
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Equity curve" style="display:block">
      ${grid}
      <path d="${line} L${lastX},${H - PAD.b} L${PAD.l},${H - PAD.b} Z" fill="${col}" opacity="0.08"/>
      <path d="${line}" fill="none" stroke="${col}" stroke-width="2"/>
      <circle cx="${lastX}" cy="${lastY}" r="3.5" fill="${col}"/>
    </svg>`;
  }

  /* ═══════════════ ENGINE ═══════════════ */
  async function renderEngine() {
    container.innerHTML = viewHead('Engine', 'The autonomous RUNECLAW engine, live');
    container.insertAdjacentHTML('beforeend', `
      <div class="engine-banner"><svg class="icon" aria-hidden="true"><use href="#icon-cog"></use></svg>
        <span><b>Shared engine telemetry.</b> This is the operator's autonomous bot — read-only, the same numbers for every viewer. Your own account lives in Home and Portfolio.</span></div>
      <div class="stack">
        <div class="grid grid-2">
          <section class="panel" id="p-eregime"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Market regime</h2><div id="c-eregime"><div class="skel"></div></div></section>
          <section class="panel" id="p-ecb"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Engine account</h2><div id="c-ecb"><div class="skel"></div></div></section>
        </div>
        <section class="panel" id="p-emods"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-bolt"></use></svg>Engine modules</h2><div id="c-emods"><div class="skel"></div></div></section>
        <section class="panel" id="p-ecards"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-target"></use></svg>Engine's current setups</h2><div id="c-ecards"><div class="skel"></div></div></section>
        <div class="grid grid-2">
          <section class="panel panel--quiet" id="p-eshadow"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Shadow book — what the gates cost</h2><div id="c-eshadow"><div class="skel"></div></div></section>
          <section class="panel panel--quiet" id="p-elist"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-rocket"></use></svg>New listings radar</h2><div id="c-elist"><div class="skel"></div></div></section>
        </div>
        <section class="panel panel--quiet" id="p-eparity"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Live ↔ backtest parity</h2><div id="c-eparity"><div class="skel"></div></div></section>
        <section class="panel panel--quiet" id="p-estrat"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-cog"></use></svg>Strategy configuration</h2><div id="c-estrat"><div class="skel"></div></div></section>
      </div>`);

    // Parity headline: does live execution still match the model? Pushed
    // hourly by the bot from its real closed-trades journal.
    renderPanel(C('eparity'), async () => {
      const rep = await getReports();
      const p = rep?.parity;
      if (!p || !p.trades) return null;
      const feeX = p.fee_vs_model != null ? Number(p.fee_vs_model) : null;
      const tiles = [
        ['Filled trades', String(p.trades), ''],
        ['Win rate', p.win_rate != null ? (p.win_rate * 100).toFixed(0) + '%' : '—', ''],
        ['Net PnL', p.net_pnl != null ? signed(p.net_pnl) : '—', pnlClass(p.net_pnl)],
        ['Profit factor', p.pf != null ? Number(p.pf).toFixed(2) : '—', ''],
        ['Fees vs model', feeX != null ? feeX.toFixed(2) + '×' : '—', feeX != null && feeX > 1.5 ? 'neg' : ''],
      ];
      const notes = [];
      if (p.inferred_fills) notes.push(`${p.inferred_fills} close price(s) inferred from ticker`);
      if (p.excluded_non_fills) notes.push(`${p.excluded_non_fills} never-filled record(s) excluded`);
      return `<p class="muted small">Realized live execution vs the modeled backtest assumptions — drift here is the earliest sign the model no longer describes reality. ${esc(reportAge(rep))}</p>
        <div class="grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:var(--s2);margin-top:var(--s3)">
          ${tiles.map(([k, v, c]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v num ${c}">${esc(String(v))}</div></div>`).join('')}
        </div>
        ${notes.length ? `<p class="small muted mt-2">${esc(notes.join(' · '))}</p>` : ''}`;
    }, { empty: { icon: 'icon-shield', text: 'Parity stats arrive with the bot\'s hourly report once live trades have closed.' } });

    const scan = await getScan();
    updateConnChip();
    const OFFLINE = { icon: 'icon-offline', text: 'Engine telemetry arrives when the bot pushes its next scan. Market data stays live meanwhile.' };

    renderPanel(C('eregime'), async () => {
      const reg = scan?.regime;
      if (!reg) return null;
      const cls = reg.label === 'BULLISH' ? 'chip--up' : reg.label === 'BEARISH' ? 'chip--down' : '';
      return `<div class="row" style="justify-content:space-between">
        <span class="chip ${cls}" style="font-size:var(--fs-sm);padding:6px 14px">${reg.label === 'BULLISH' ? '▲' : reg.label === 'BEARISH' ? '▼' : '◆'} ${esc(reg.label)}</span>
        <div class="stat right"><div class="k">BTC anchor</div><div class="v">${fmtPrice(reg.gate)}</div></div>
      </div>
      ${scan?.key_call ? `<div class="mt-3 small" style="color:var(--text-2)">${sanitizeBotHtml(scan.key_call)}</div>` : ''}`;
    }, { empty: OFFLINE });

    renderPanel(C('ecb'), async () => {
      const cb = scan?.circuit_breaker;
      if (!cb || (cb.equity == null && !cb.total_trades)) return null;
      return `<div class="stat-row">
        <div class="stat"><div class="k">Engine equity</div><div class="v">${cb.equity != null ? fmtMoney(cb.equity) : '—'}</div></div>
        <div class="stat"><div class="k">Net PnL</div><div class="v num ${pnlClass(cb.net_pnl)}">${signed(cb.net_pnl)}</div></div>
        <div class="stat"><div class="k">Win rate</div><div class="v">${fmt(cb.win_rate, 1)}%</div></div>
        <div class="stat"><div class="k">Open</div><div class="v">${cb.open_count ?? 0}</div></div>
      </div>
      <div class="row mt-3">${(cb.rules || []).map(r => `<span class="chip ${r.active ? 'chip--down' : 'chip--up'}">${r.active ? '⚠' : '✓'} ${esc(r.label)}</span>`).join('')}</div>`;
    }, { empty: OFFLINE });

    renderPanel(C('emods'), async () => {
      const f = scan?.features;
      if (!f || !Object.keys(f).length) return null;
      const tiles = [];
      if (f.venue) tiles.push(tile('Trading venue', esc(String(f.venue.name || f.venue.id).toUpperCase()), 'Live on Bitget USDT-M · Hyperliquid adapter available'));
      if (f.funding_clock) {
        const secs = Math.max(0, f.funding_clock.seconds_to_settlement || 0);
        tiles.push(tile(`Funding clock ${f.funding_clock.enabled ? '· gate on' : '· gate off'}`,
          `${Math.floor(secs / 3600)}h ${String(Math.floor(secs % 3600 / 60)).padStart(2, '0')}m`,
          'to settlement — blocks paying-side entries on extreme rates'));
      }
      if (f.equity_throttle) {
        const t = f.equity_throttle;
        tiles.push(tile('Equity throttle', `${esc(t.status || '—')}${t.multiplier != null && t.multiplier < 1 ? ` · ${Math.round(t.multiplier * 100)}% size` : ''}`,
          `rolling PF ${t.pf != null ? fmt(t.pf) : '—'} over ${t.samples ?? 0} closes`));
      }
      if (f.entry_timing) tiles.push(tile('Entry timing', f.entry_timing.enabled ? 'ALL REGIMES' : (f.entry_timing.regimes || []).join(', ').toUpperCase() || 'OFF', 'wave-degree confirmation before entries'));
      if (f.shadow_book?.counts) {
        const c = f.shadow_book.counts;
        tiles.push(tile('Shadow book', `${c.closed || 0} closed · ${(c.open || 0) + (c.pending || 0)} tracked`, 'every gate rejection gets a counterfactual price'));
      }
      return tiles.length ? `<div class="grid grid-3">${tiles.join('')}</div>` : null;
    }, { empty: OFFLINE });

    renderPanel(C('ecards'), async () => {
      const cards = scan?.entry_cards || [];
      if (!cards.length) return null;
      return `<div class="tbl-wrap"><table class="tbl tbl--collapse">
        <thead><tr><th>Setup</th><th class="r">Entry</th><th class="r">Stop / TP1</th><th class="r">R:R</th><th>Trigger</th></tr></thead>
        <tbody>${cards.slice(0, 8).map(c => `
          <tr>
            <td data-label="Setup">${dirChip(c.direction)} <b>${esc(c.symbol)}</b></td>
            <td data-label="Entry" class="r num">${fmtPrice(parseFloat(c.entry))}</td>
            <td data-label="Stop / TP1" class="r num muted">${fmtPrice(parseFloat(c.stop_loss))} / ${fmtPrice(parseFloat(c.tp1))}</td>
            <td data-label="R:R" class="r num">${esc(c.rr)}</td>
            <td data-label="Trigger" class="muted small">${esc(c.trigger || '')}</td>
          </tr>`).join('')}</tbody></table></div>
        <p class="muted small mt-2">The engine's own candidates — not personal advice. Confirmations run through its risk gate.</p>`;
    }, { empty: { icon: 'icon-target', text: 'No qualifying setups in the last scan — the gate is doing its job.' } });

    renderPanel(C('eshadow'), async () => {
      const sb = scan?.features?.shadow_book;
      if (!sb || !(sb.gates || []).length) return null;
      const c = sb.counts || {};
      return `<p class="muted small mb-2">net R &gt; 0 = the gate blocked winners; &lt; 0 = it saved money. ${c.closed || 0} closed counterfactuals.</p>` +
        sb.gates.slice(0, 8).map(g => `
        <div class="kv-row"><span class="small" style="font-family:var(--font-data)">${esc(String(g.gate).slice(0, 30))}</span>
          <b class="num ${g.net_r > 0 ? 'neg' : 'pos'}">${signed(g.net_r, 1)}R <span class="muted">×${g.n}</span></b></div>`).join('');
    }, { empty: { text: 'The shadow book fills as risk gates reject ideas and their counterfactuals resolve.' } });

    renderPanel(C('elist'), async () => {
      const recent = scan?.features?.catalog_watch?.recent;
      if (!recent || !recent.length) return null;
      return recent.slice().reverse().map(ev => `
        <div class="kv-row"><b style="font-family:var(--font-data);color:var(--gold-bright)">${esc(String(ev.symbol).split('/')[0])}</b>
        <span class="muted small">${esc(ev.category || 'Crypto')} · vol $${fmtK(ev.vol_usd)}</span></div>`).join('');
    }, { empty: { icon: 'icon-rocket', text: 'No new exchange listings detected — the engine diffs the catalog every scan.' } });

    renderPanel(C('estrat'), async () => {
      const cfg = scan?.config;
      if (!cfg) return null;
      const onOff = v => v ? '<span class="chip chip--up">✓ ON</span>' : '<span class="chip">OFF</span>';
      return Object.entries(cfg).slice(0, 14).map(([k, v]) => `
        <div class="kv-row"><span class="small">${esc(k.replace(/_/g, ' '))}</span>
        <b>${typeof v === 'boolean' ? onOff(v) : esc(String(v))}</b></div>`).join('');
    }, { empty: { text: 'Strategy config arrives with the engine sync.' } });

    function tile(k, v, s) {
      return `<div class="panel" style="background:var(--surface-2)"><div class="stat">
        <div class="k">${k}</div><div class="v" style="color:var(--gold-bright)">${v}</div><div class="d muted small">${s}</div></div></div>`;
    }
  }

  /* ═══════════════ ACCOUNT ═══════════════ */
  async function renderAccount() {
    container.innerHTML = viewHead('Account', 'Profile, connections, and live-trading controls');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend', `<section class="panel">${loginGate('Log in to manage your account and connections.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-aprof"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-user"></use></svg>Profile</h2><div id="c-aprof"><div class="skel"></div></div></section>
        <section class="panel" id="p-aplan"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>Membership</h2><div id="c-aplan"><div class="skel"></div></div></section>
        <section class="panel" id="p-atg"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-link"></use></svg>Telegram link <span class="right muted small">optional — unlocks live trading</span></h2><div id="c-atg"><div class="skel"></div></div></section>
        <section class="panel" id="p-apush"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-bolt"></use></svg>Push notifications <span class="right muted small">trades & alerts, straight to this device</span></h2><div id="c-apush"><div class="skel"></div></div></section>
        <section class="panel" id="p-ainvite"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-user"></use></svg>Invite friends</h2><div id="c-ainvite"><div class="skel"></div></div></section>
        <section class="panel" id="p-akeys"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-wallet"></use></svg>Exchange keys</h2><div id="c-akeys"><div class="skel"></div></div></section>
        <section class="panel" id="p-actl"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Live controls</h2><div id="c-actl"><div class="skel"></div></div></section>
      </div>`);

    const me = await fetchJSON('/api/auth/me').catch(() => null);
    const linked = !!me?.data?.telegram_linked;

    // Yield radar — OPERATOR report (real account idle balances), so the
    // panel only exists for admin-plan users; the API re-checks server-side.
    if (me?.data?.plan === 'admin') {
      document.getElementById('p-aplan').insertAdjacentHTML('afterend', `
        <section class="panel" id="p-ayield"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>Yield radar <span class="right muted small">operator report</span></h2><div id="c-ayield"><div class="skel"></div></div></section>`);
      renderPanel(C('ayield'), async () => {
        const r = await fetchJSON('/api/reports/yield', { timeoutMs: 12000 });
        const y = r.data?.yield;
        if (!y || !(y.rows || []).length) return null;
        return `<p class="muted small">Idle assets vs Bitget flexible Earn — same data as Telegram /yield. Staking stays behind the bot's confirm buttons.</p>
          <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Coin</th><th class="r">Idle</th><th class="r">Stakeable</th><th class="r">Flex APY</th><th class="r">Est/yr</th></tr></thead>
          <tbody>${y.rows.slice(0, 10).map(row => `<tr><td><b>${esc(row.coin)}</b>${row.alt_note ? ` <span class="muted small">${esc(row.alt_note)}</span>` : ''}</td>
            <td class="num r">$${Number(row.idle_usd || 0).toFixed(2)}</td>
            <td class="num r">$${Number(row.stakeable_usd || 0).toFixed(2)}</td>
            <td class="num r">${row.apy_flexible != null ? Number(row.apy_flexible).toFixed(2) + '%' : '—'}</td>
            <td class="num r">$${Number(row.est_year_usd || 0).toFixed(2)}</td></tr>`).join('')}</tbody></table></div>
          <p class="small muted mt-2">Total idle <b class="num">$${Number(y.total_idle_usd || 0).toFixed(2)}</b> · est. <b class="num">$${Number(y.total_est_year_usd || 0).toFixed(2)}/yr</b> at current flexible rates. Use /stake in Telegram to act.</p>`;
      }, { empty: { icon: 'icon-coin', text: 'Yield data arrives with the bot\'s hourly report (needs operator Earn credentials).' } });
    }

    // Web push: opt-in per browser. Requires VAPID keys server-side and
    // Notification permission client-side; every state is shown honestly.
    async function drawPush() {
      renderPanel(C('apush'), async () => {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
          return `<p class="small muted">This browser doesn't support web push.</p>`;
        }
        const k = await fetchJSON('/api/push/key');
        if (!k?.ok || !k.data?.enabled) {
          return `<p class="small muted">Push isn't configured on the server yet (operator: set VAPID keys). Telegram alerts keep working meanwhile.</p>`;
        }
        const reg = await navigator.serviceWorker.ready.catch(() => null);
        const sub = reg ? await reg.pushManager.getSubscription().catch(() => null) : null;
        if (sub) {
          return `<p class="small" style="color:var(--text-2)">✅ This device gets a notification when the agent opens or closes a trade, or raises a warning.</p>
            <button class="btn btn--sm" id="pushOff" type="button">Turn off on this device</button>`;
        }
        return `<p class="small" style="color:var(--text-2)">Get a notification the moment the agent opens or closes a trade, or raises a warning — even with the tab closed.</p>
          <button class="btn btn--primary btn--sm" id="pushOn" type="button">Enable on this device</button>
          ${Notification.permission === 'denied' ? '<p class="small muted mt-2">Notifications are blocked in your browser settings for this site — unblock them first.</p>' : ''}`;
      }, { empty: { text: 'Push status unavailable.' } });
    }
    function urlB64ToU8(s) {
      const pad = '='.repeat((4 - s.length % 4) % 4);
      const raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
      return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
    }
    C('apush').addEventListener('click', async (e) => {
      const on = e.target.closest('#pushOn'), off = e.target.closest('#pushOff');
      if (!on && !off) return;
      try {
        const reg = await navigator.serviceWorker.ready;
        if (on) {
          const perm = await Notification.requestPermission();
          if (perm !== 'granted') { toast('Notifications were not allowed.'); return; }
          const k = await fetchJSON('/api/push/key');
          const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlB64ToU8(k.data.public_key),
          });
          const r = await fetchJSON('/api/push/subscribe', { method: 'POST', body: { subscription: sub.toJSON() } });
          toast(r?.ok ? 'Push enabled — the agent can reach you here now.' : 'Could not save the subscription.');
        } else {
          const sub = await reg.pushManager.getSubscription();
          if (sub) {
            await fetchJSON('/api/push/unsubscribe', { method: 'POST', body: { endpoint: sub.endpoint } });
            await sub.unsubscribe();
          }
          toast('Push disabled on this device.');
        }
      } catch (err) {
        toast('Push setup failed: ' + (err?.message || 'unknown error'));
      }
      drawPush();
    });
    drawPush();

    renderPanel(C('aprof'), async () => {
      if (!me?.ok) return null;
      return `<div class="kv-row"><span>Email</span><b style="font-family:var(--font-ui)">${esc(me.data.email || '—')}</b></div>
        <div class="kv-row"><span>Mode</span><b><span class="chip chip--paper">PAPER</span>${linked ? ' <span class="chip chip--gold">LIVE-CAPABLE</span>' : ''}</b></div>
        <button class="btn btn--ghost btn--sm mt-3" id="logoutBtn">Log out</button>`;
    }, { empty: { text: 'Could not load your profile.' } });

    renderPanel(C('aplan'), async () => {
      if (!me?.ok) return null;
      // 'free' is the pre-tier-sync default; the bot's tier authority calls
      // the same thing 'basic'. One label for both.
      const raw = String(me.data.plan || 'basic').toLowerCase();
      const plan = raw === 'free' ? 'basic' : raw;
      const PLANS = [
        { id: 'basic', name: 'Basic', pts: ['Paper trading with the real risk gate', 'Live charts, signals & AI chat', 'Strategy Lab backtests'] },
        { id: 'pro', name: 'Pro', pts: ['Premium AI models answer your scans', 'Live trading eligibility (linked + approved)', 'Priority support'] },
        { id: 'elite', name: 'Elite', pts: ['Everything in Pro', 'Higher live caps', 'Early access to new agent features'] },
      ];
      const cards = PLANS.map(p => `
        <div class="tile" style="flex:1;min-width:180px;border:1px solid ${p.id === plan ? 'var(--gold)' : 'var(--line)'};border-radius:var(--radius);padding:var(--s3) var(--s4)">
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <b>${esc(p.name)}</b>${p.id === plan ? '<span class="chip chip--gold">your plan</span>' : ''}
          </div>
          <ul class="small" style="margin:8px 0 0 16px;color:var(--text-2);display:flex;flex-direction:column;gap:4px">
            ${p.pts.map(t2 => `<li>${esc(t2)}</li>`).join('')}
          </ul>
        </div>`).join('');
      return `<div class="row" style="gap:var(--s3);flex-wrap:wrap;align-items:stretch">${cards}</div>
        <p class="muted small mt-3">Tiers are granted by the operator through the Telegram bot. Interested in Pro or Elite? Ask in <a href="https://t.me/HTRUNECLAW_bot" target="_blank" rel="noopener">@HTRUNECLAW_bot</a> — online checkout is coming later.</p>`;
    }, { empty: { text: 'Membership info unavailable.' } });
    setTimeout(() => {
      const b = document.getElementById('logoutBtn');
      if (b) b.onclick = RC.logout;
    }, 300);

    renderPanel(C('atg'), async () => {
      if (linked) {
        return `<div class="section-note" style="border-style:solid;border-color:var(--up);color:var(--up)">
          <svg class="icon" aria-hidden="true"><use href="#icon-check"></use></svg>
          Telegram linked — exchange-key management and your live controls are unlocked (going live still needs operator approval).</div>`;
      }
      return `<p class="small" style="color:var(--text-2)">Paper trading and chat already work without Telegram. Linking unlocks <b>exchange-key management</b> and your <b>live-trading controls</b> (going live also needs operator approval), and sends the bot's alerts to your Telegram.</p>
        <ol class="steps-list mt-2">
          <li>Open <a href="https://t.me/HTRUNECLAW_bot" target="_blank" rel="noopener">@HTRUNECLAW_bot</a> on Telegram</li>
          <li>Generate your personal link token below</li>
          <li>Send the bot <code>/link &lt;token&gt;</code></li>
        </ol>
        <div class="row mt-3" style="gap:8px;flex-wrap:wrap">
          <button class="btn btn--primary btn--sm" id="tgGenTok" type="button">Generate link token</button>
          <a class="btn btn--ghost btn--sm" href="https://t.me/HTRUNECLAW_bot" target="_blank" rel="noopener">Open @HTRUNECLAW_bot ↗</a>
        </div>
        <div id="tgTokArea" class="mt-3" aria-live="polite"></div>`;
    }, { empty: { text: '' } });
    // The link flow lives HERE now (it used to bounce the user back to the
    // landing page): generate a 10-min token, show it with copy-to-clipboard and
    // the exact /link command to paste into the bot.
    container.addEventListener('click', async (e) => {
      if (e.target.id === 'tgGenTok') {
        const area = document.getElementById('tgTokArea');
        e.target.disabled = true;
        const r = await fetchJSON('/api/auth/link-token', { method: 'POST' }).catch(() => ({ ok: false }));
        e.target.disabled = false;
        if (!r.ok || !r.data?.token) {
          area.innerHTML = `<span class="small" style="color:var(--down)">${esc(r.data?.error || 'Could not generate a token — try again.')}</span>`;
          return;
        }
        const tok = String(r.data.token);
        area.innerHTML = `<p class="muted small mb-1">Your link token (valid 10 min) — tap to copy:</p>
          <div class="token-display" id="tgTok" role="button" tabindex="0" title="Copy">${esc(tok)}</div>
          <p class="muted small mt-2">Send the bot: <code>/link ${esc(tok)}</code></p>`;
      }
      const tokEl = e.target.id === 'tgTok' ? e.target : e.target.closest?.('#tgTok');
      if (tokEl) {
        try {
          await navigator.clipboard.writeText(tokEl.textContent.trim());
          tokEl.classList.add('copied');
          toast('Link token copied.');
          setTimeout(() => tokEl.classList.remove('copied'), 1500);
        } catch { /* clipboard blocked — the token is still visible to copy manually */ }
      }
      // Copy the invite link (same idiom as the Telegram token).
      const refEl = e.target.id === 'refLink' ? e.target : e.target.closest?.('#refLink');
      if (refEl) {
        try {
          await navigator.clipboard.writeText(refEl.textContent.trim());
          refEl.classList.add('copied');
          toast('Invite link copied.');
          setTimeout(() => refEl.classList.remove('copied'), 1500);
        } catch { /* clipboard blocked — the link is still visible to copy manually */ }
      }
    });

    // Invite friends — the user's own share link + a live count of who joined.
    renderPanel(C('ainvite'), async () => {
      const r = await fetchJSON('/api/auth/referrals');
      if (!r.ok || !r.data?.code) return null;
      const link = `${location.origin}/?ref=${encodeURIComponent(r.data.code)}`;
      const count = r.data.count || 0;
      const share = encodeURIComponent(link);
      const text = encodeURIComponent('Trade alongside an autonomous AI on RUNECLAW:');
      // Reward tier + progress to the next milestone (server-computed).
      const tier = r.data.tier || { name: 'Starter', perk: '' };
      const next = r.data.next;
      const pct = next ? Math.min(100, Math.round((count / next.at) * 100)) : 100;
      const tierBlock = `
        <div class="ref-tier mt-3">
          <div class="row" style="justify-content:space-between;align-items:baseline">
            <span class="chip chip--gold">${esc(tier.name)}</span>
            <span class="muted small">${count} joined</span>
          </div>
          <p class="small mt-1" style="color:var(--text-2)">${esc(tier.perk)}</p>
          ${next ? `<div class="ref-bar mt-2"><span style="width:${pct}%"></span></div>
            <p class="muted small mt-1">${next.remaining} more to reach <b style="color:var(--gold)">${esc(next.name)}</b></p>`
          : `<p class="muted small mt-2">Top tier reached — thank you. 🏆</p>`}
        </div>`;
      return `<p class="small mb-2" style="color:var(--text-2)">Share your link — anyone who signs up through it is credited to you.</p>
        <div class="token-display" id="refLink" role="button" tabindex="0" title="Copy invite link">${esc(link)}</div>
        <div class="row mt-3" style="gap:var(--s2);flex-wrap:wrap">
          <a class="btn btn--sm" href="https://t.me/share/url?url=${share}&text=${text}" target="_blank" rel="noopener">Share on Telegram</a>
          <a class="btn btn--sm" href="https://twitter.com/intent/tweet?url=${share}&text=${text}" target="_blank" rel="noopener">Share on X</a>
        </div>
        ${tierBlock}`;
    }, { empty: { text: 'Your invite link will appear here shortly.' } });

    // Venue catalog (from /config) shared by the panel + submit handler. The
    // form is data-driven: each venue declares its own fields, so adding a venue
    // server-side needs no client change.
    let venuesCatalog = [];
    const venueById = (id) => venuesCatalog.find(v => v.id === id) || venuesCatalog[0];
    const fieldsHtml = (venue) => (venue?.fields || []).map(f =>
      `<div class="field"><label for="cf-${esc(f.key)}">${esc(f.label)}</label>
        <input class="input" id="cf-${esc(f.key)}" data-fkey="${esc(f.key)}" type="${f.type === 'password' ? 'password' : 'text'}" autocomplete="off"></div>`
    ).join('');

    renderPanel(C('akeys'), async () => {
      const [r, cfg] = await Promise.all([
        fetchJSON('/api/credentials/status'),
        fetchJSON('/api/auth/config', { auth: false }).catch(() => ({ data: {} })),
      ]);
      venuesCatalog = (cfg.data?.venues) || [];
      if (r.status === 409) {
        return `<div class="section-note"><svg class="icon" aria-hidden="true"><use href="#icon-link"></use></svg>
          ${esc(r.data?.detail || 'Exchange keys require a linked Telegram account.')}</div>`;
      }
      const c = r.data || {};
      const venueLabel = (id) => (venuesCatalog.find(v => v.id === id)?.label) || (id ? id[0].toUpperCase() + id.slice(1) : 'Exchange');
      if (c.connected) {
        return `<div class="row" style="justify-content:space-between">
          <span class="chip chip--up">✓ ${esc(venueLabel(c.venue))} connected</span>
          <button class="btn btn--danger btn--sm" id="credDisc">Disconnect</button></div>
          <p class="muted small mt-2">Keys are AES-256-GCM encrypted at rest and pulled by the bot over an authenticated channel. Withdrawal permissions are never required.</p>`;
      }
      if (!venuesCatalog.length) return null;
      const first = venuesCatalog[0];
      const options = venuesCatalog.map(v => `<option value="${esc(v.id)}">${esc(v.label)}</option>`).join('');
      return `<form id="credForm" class="stack">
        <p class="small" style="color:var(--text-2)">Connect your own exchange keys to prepare live trading. Keys are encrypted at rest; withdrawal permission is never required.</p>
        <div class="field" style="max-width:220px"><label for="credVenue">Venue</label>
          <select class="input" id="credVenue">${options}</select></div>
        <p class="muted small" id="venueHelp">${esc(first.help || '')}</p>
        <div class="form-row" id="credFields">${fieldsHtml(first)}</div>
        <div class="row"><button class="btn btn--primary btn--sm" type="submit">Connect exchange</button>
        <span id="credMsg" class="small muted" aria-live="polite">${c.pending ? `Applying ${esc(venueLabel(c.pending_venue))}…` : ''}</span></div>
      </form>`;
    }, { empty: { text: 'Credential connect is unavailable right now.' } });
    // Swap the fields + help when the venue changes.
    container.addEventListener('change', (e) => {
      if (e.target.id !== 'credVenue') return;
      const v = venueById(e.target.value);
      const fw = document.getElementById('credFields');
      const help = document.getElementById('venueHelp');
      if (fw) fw.innerHTML = fieldsHtml(v);
      if (help) help.textContent = v?.help || '';
    });
    container.addEventListener('submit', async (e) => {
      const f = e.target.closest('#credForm');
      if (!f) return;
      e.preventDefault();
      const msg = document.getElementById('credMsg');
      const venue = document.getElementById('credVenue')?.value || 'bitget';
      const body = { venue };
      for (const inp of f.querySelectorAll('[data-fkey]')) body[inp.dataset.fkey] = inp.value.trim();
      msg.textContent = 'Encrypting & queueing…';
      const r = await fetchJSON('/api/credentials', { method: 'POST', body }).catch(() => ({ ok: false }));
      msg.textContent = r.ok ? 'Queued — the bot applies it within a minute.' : (r.data?.detail || r.data?.error || 'Failed.');
      if (r.ok) setTimeout(() => showView('account'), 1200);
    });
    container.addEventListener('click', async (e) => {
      if (e.target.id !== 'credDisc') return;
      if (!confirm('Disconnect your exchange keys?')) return;
      await fetchJSON('/api/credentials', { method: 'DELETE' }).catch(() => {});
      toast('Disconnect queued.');
      showView('account');
    });

    renderPanel(C('actl'), async () => {
      const r = await fetchJSON('/api/controls/status');
      if (r.status === 409) {
        return `<div class="section-note"><svg class="icon" aria-hidden="true"><use href="#icon-link"></use></svg>
          ${esc(r.data?.detail || 'Live controls require a linked Telegram account.')}</div>`;
      }
      const c = r.data || {};
      const liveEff = c.live_enabled && c.allowlisted;
      return `<div class="row mb-3">
          ${liveEff ? '<span class="chip chip--live">● LIVE ON</span>'
            : c.live_enabled ? '<span class="chip chip--warn">⏳ ON — pending operator approval</span>'
            : '<span class="chip chip--paper">PAPER</span>'}
          ${c.pending ? '<span class="chip">applying…</span>' : ''}
        </div>
        <div class="stack">
          <label class="switch"><input type="checkbox" id="ctlLive" ${c.live_enabled ? 'checked' : ''}><span class="track"></span>Live trading <span class="muted small">(also needs operator approval)</span></label>
          <label class="switch"><input type="checkbox" id="ctlPause" ${c.paused ? 'checked' : ''}><span class="track"></span>Pause — route everything to paper</label>
          <div class="field" style="max-width:220px"><label for="ctlMargin">Max margin per trade ($, 0 = no cap)</label>
            <input class="input input--num" id="ctlMargin" type="number" min="0" step="1" value="${c.max_margin != null ? c.max_margin : ''}"></div>
          <div class="row">
            <button class="btn btn--primary btn--sm" id="ctlSave">Apply</button>
            <button class="btn btn--danger btn--sm" id="ctlStop">Emergency stop</button>
            <span id="ctlMsg" class="small muted" aria-live="polite"></span>
          </div>
          <p class="muted small">Emergency stop disables live, pauses, and closes your open positions.</p>
        </div>`;
    }, { empty: { text: 'Controls unavailable.' } });
    container.addEventListener('click', async (e) => {
      if (e.target.id === 'ctlSave') {
        const msg = document.getElementById('ctlMsg');
        msg.textContent = 'Applying…';
        const body = {
          live_enabled: document.getElementById('ctlLive').checked,
          paused: document.getElementById('ctlPause').checked,
        };
        const m = document.getElementById('ctlMargin').value.trim();
        if (m !== '') body.max_margin = Number(m);
        const r = await fetchJSON('/api/controls', { method: 'POST', body }).catch(() => ({ ok: false }));
        msg.textContent = r.ok ? 'Queued — the bot applies it within a minute.' : (r.data?.error || 'Failed.');
      }
      if (e.target.id === 'ctlStop') {
        if (!confirm('Emergency stop: disable live, pause, and close your open positions. Continue?')) return;
        const r = await fetchJSON('/api/controls/stop', { method: 'POST' }).catch(() => ({ ok: false }));
        toast(r.ok ? 'Emergency stop queued — closing positions.' : 'Emergency stop failed.', r.ok ? 'warn' : 'down');
      }
    });
  }

  /* ═══════════════ LEADERBOARD ═══════════════ */
  async function renderLeaderboard() {
    container.innerHTML = viewHead('Leaderboard', 'Opt-in ranks by return % — anonymous handles, no dollar amounts');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend', `<section class="panel">${loginGate('Log in to see the leaderboard and join with a handle.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-lbjoin"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-user"></use></svg>Your spot</h2><div id="c-lbjoin"><div class="skel"></div></div></section>
        <section class="panel" id="p-lbtable"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-target"></use></svg>Top traders</h2><div id="c-lbtable"><div class="skel"></div><div class="skel"></div></div></section>
      </div>`);

    let data = null;
    const load = async () => { const r = await fetchJSON('/api/leaderboard'); data = r.ok ? r.data : null; return data; };

    renderPanel(C('lbjoin'), async () => {
      await load();
      if (!data) return null;
      if (data.opted_in) {
        return `<p class="small" style="color:var(--text-2)">You're on the board as <span class="chip chip--gold">${esc(data.handle)}</span>. Only this handle and your % return show — never your email or balance.</p>
          <button class="btn btn--ghost btn--sm mt-3" id="lbLeave" type="button">Leave the leaderboard</button>`;
      }
      return `<p class="small" style="color:var(--text-2)">Join with an anonymous handle to appear in the ranks — leave any time. We show your handle and % return only, never your email or any dollar amount.</p>
        <div class="row mt-3" style="gap:var(--s2);flex-wrap:wrap;align-items:center">
          <input class="input" id="lbHandle" maxlength="20" placeholder="Pick a handle (3–20 chars)" style="max-width:220px" aria-label="Leaderboard handle">
          <button class="btn btn--primary btn--sm" id="lbJoin" type="button">Join leaderboard</button>
        </div>
        <p class="small mt-1" id="lbMsg" aria-live="polite" style="color:var(--down)"></p>`;
    }, { empty: { text: 'The leaderboard is unavailable right now.' } });

    renderPanel(C('lbtable'), async () => {
      if (!data) await load();
      const rows = (data && data.rows) || [];
      if (!rows.length) return null;
      return `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>#</th><th>Trader</th><th class="r">Return</th><th class="r">Trades</th><th class="r">Win rate</th></tr></thead>
        <tbody>${rows.map(row => `
          <tr${row.is_me ? ' style="background:var(--gold-dim)"' : ''}>
            <td class="num muted">${row.rank}</td>
            <td><b>${esc(row.handle)}</b>${row.is_me ? ' <span class="chip chip--gold">you</span>' : ''}</td>
            <td class="r num ${pnlClass(row.return_pct)}">${signed(row.return_pct)}%</td>
            <td class="r num muted">${row.trades}</td>
            <td class="r num muted">${fmt(row.win_rate, 1)}%</td>
          </tr>`).join('')}</tbody></table></div>
        <p class="muted small mt-2">Return % is measured on the standard paper stake. Dollar amounts are never shown.</p>`;
    }, { empty: { icon: 'icon-target', text: 'No ranked traders yet — pick a handle above and close a trade to be the first.' } });

    // Join / leave.
    container.addEventListener('click', async (e) => {
      if (e.target.id === 'lbJoin') {
        const h = (document.getElementById('lbHandle').value || '').trim();
        const msg = document.getElementById('lbMsg');
        e.target.disabled = true;
        const r = await fetchJSON('/api/leaderboard/opt-in', { method: 'POST', body: { handle: h } })
          .catch(() => ({ ok: false, data: null }));
        e.target.disabled = false;
        if (!r.ok) { if (msg) msg.textContent = (r.data && r.data.error) || 'Could not join — try another handle.'; return; }
        toast('You\'re on the leaderboard.'); showView('leaderboard');
      }
      if (e.target.id === 'lbLeave') {
        e.target.disabled = true;
        await fetchJSON('/api/leaderboard/opt-out', { method: 'POST' }).catch(() => {});
        toast('Left the leaderboard.'); showView('leaderboard');
      }
    });
  }

  /* ═══════════════ AI CHAT (docked) ═══════════════ */
  async function renderChat() {
    container.innerHTML = viewHead('AI Analyst',
      'The same agent that runs the Telegram bot — portfolio-aware, trade-capable.') +
      '<div id="chatInlineHost"></div>';
    if (window.RCChat) {
      window.RCChat.mountInline(document.getElementById('chatInlineHost'));
      window.RCChat.focus();
    }
  }

  /* ═══════════════ Lab (Strategy Lab — frozen-snapshot backtests) ═══════ */
  async function renderLab() {
    container.innerHTML = viewHead('Strategy Lab', 'Run the engine\'s honest backtester on frozen benchmark data');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend', `<section class="panel">${loginGate('Log in to run backtests in the Strategy Lab.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-labform"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>Configure a run
          <span class="right muted small">frozen data · honest fees &amp; fills · one job at a time</span></h2>
          <div id="c-labform"><div class="skel"></div></div></section>
        <section class="panel" id="p-labres" hidden><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>Result</h2>
          <div id="c-labres"></div></section>
      </div>`);

    let meta = null;
    await renderPanel(C('labform'), async () => {
      const r = await fetchJSON('/api/lab/meta', { timeoutMs: 16000 });
      if (r.status === 503) {
        return `<div class="state-block"><svg class="icon"><use href="#icon-offline"></use></svg>
          <p>The Strategy Lab needs the bot's analysis bridge (run <code>python api_bridge.py</code> on the bot host, port 8000).</p></div>`;
      }
      if (!r.ok || !r.data?.datasets) return null;
      meta = r.data;
      const names = Object.keys(meta.datasets);
      if (!names.length) return `<div class="state-block"><p>No frozen benchmark snapshots found on the bot host.</p></div>`;
      const dsOpts = names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
      return `
        <div class="row" style="gap:var(--s3);flex-wrap:wrap;align-items:flex-end">
          <div class="field"><label for="labDs">Dataset</label><select class="input" id="labDs">${dsOpts}</select></div>
          <div class="field"><label for="labBars">History</label><select class="input" id="labBars">
            <option value="720">~1 month (720 bars)</option>
            <option value="1500" selected>~2 months (1500)</option>
            <option value="3000">~4 months (3000)</option>
            <option value="6000">~8 months (6000)</option></select></div>
          <div class="field"><label for="labConf">Min confidence</label><select class="input" id="labConf">
            <option value="0" selected>Engine default</option>
            <option value="0.5">0.50</option><option value="0.6">0.60</option>
            <option value="0.7">0.70</option><option value="0.8">0.80</option></select></div>
          <div class="field"><label for="labBal">Balance $</label><input class="input" id="labBal" type="number" value="10000" min="100" max="1000000" style="width:110px"></div>
        </div>
        <div class="mt-3"><span class="muted small">Symbols (max 4):</span>
          <div id="labSyms" class="row mt-1" style="gap:6px;flex-wrap:wrap"></div></div>
        <div class="row mt-3" style="gap:var(--s3);align-items:center">
          <button class="btn btn--primary" id="labRun" type="button">Run backtest</button>
          <span class="small muted" id="labMsg" aria-live="polite"></span>
        </div>
        <p class="muted small mt-2">Runs on frozen, content-hashed market data with the honest fee/fill model — the same engine used to validate the live strategy. It never touches the live account.</p>`;
    }, { errorText: 'Strategy Lab unavailable.' });

    // Populate symbol chips when the dataset changes (data-driven from meta).
    const fillSyms = () => {
      const host = document.getElementById('labSyms');
      const ds = meta?.datasets?.[document.getElementById('labDs')?.value];
      if (!host || !ds) return;
      host.innerHTML = ds.symbols.map((s, i) => `
        <label class="chip" style="cursor:pointer;user-select:none">
          <input type="checkbox" value="${esc(s)}" ${i < 3 ? 'checked' : ''} style="margin-right:5px">${esc(s.replace(':USDT', '').replace('/USDT', ''))}</label>`).join('');
    };
    fillSyms();
    container.addEventListener('change', (e) => { if (e.target.id === 'labDs') fillSyms(); });

    let pollTimer = null;
    container.addEventListener('click', async (e) => {
      if (e.target.id !== 'labRun') return;
      const msg = document.getElementById('labMsg');
      const syms = [...document.querySelectorAll('#labSyms input:checked')].map(i => i.value).slice(0, 4);
      if (!syms.length) { msg.textContent = 'Pick at least one symbol.'; return; }
      e.target.disabled = true;
      msg.textContent = 'Submitting…';
      const r = await fetchJSON('/api/lab/run', { method: 'POST', timeoutMs: 16000, body: {
        dataset: document.getElementById('labDs').value,
        symbols: syms,
        last_bars: parseInt(document.getElementById('labBars').value, 10),
        confidence_threshold: parseFloat(document.getElementById('labConf').value),
        balance: parseFloat(document.getElementById('labBal').value) || 10000,
      }});
      if (!r.ok) {
        e.target.disabled = false;
        msg.textContent = r.data?.detail || r.data?.error || 'Could not start the run.';
        return;
      }
      const jobId = r.data.job_id;
      const started = Date.now();
      msg.textContent = 'Running…';
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(async () => {
        if (!document.getElementById('labMsg')) { clearInterval(pollTimer); pollTimer = null; return; }
        const st = await fetchJSON(`/api/lab/status/${jobId}`, { timeoutMs: 16000 });
        if (!st.ok) return;
        const s = st.data;
        if (s.status === 'running') {
          msg.textContent = `Running… ${Math.round((Date.now() - started) / 1000)}s (analyzing every bar, be patient)`;
          return;
        }
        clearInterval(pollTimer); pollTimer = null;
        e.target.disabled = false;
        if (s.status === 'error') { msg.textContent = s.error || 'Run failed.'; return; }
        msg.textContent = 'Done.';
        drawLabResult(s.result, s.params);
      }, 3000);
    });

    function drawLabResult(res, params) {
      const panel = document.getElementById('p-labres');
      const host = C('labres');
      if (!panel || !host || !res) return;
      panel.hidden = false;
      const pct = v => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(+v).toFixed(2)}%`);
      const usd = v => (v == null ? '—' : `${v < 0 ? '-' : ''}$${Math.abs(+v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
      const tiles = [
        ['Return', pct(res.total_return_pct), pnlClass(res.total_return_pct)],
        ['Net PnL', usd(res.net_pnl), pnlClass(res.net_pnl)],
        ['Profit factor', res.profit_factor?.toFixed(2) ?? '—', pnlClass((res.profit_factor || 1) - 1)],
        ['Win rate', res.win_rate != null ? `${(res.win_rate * 100).toFixed(0)}%` : '—', ''],
        ['Max drawdown', res.max_drawdown_pct != null ? `${res.max_drawdown_pct.toFixed(2)}%` : '—', 'neg'],
        ['Sharpe', res.sharpe_ratio?.toFixed(2) ?? '—', ''],
        ['Trades', res.total_trades ?? '—', ''],
      ];
      const curve = res.equity_curve_points || [];
      let curveSvg = '';
      if (curve.length >= 2) {
        const W = 1000, H = 200, P = 6;
        const ys = curve.map(p => p.equity);
        const y0 = Math.min(...ys), y1 = Math.max(...ys);
        const pts = curve.map((p, i) => `${(P + (W - 2 * P) * i / (curve.length - 1)).toFixed(1)},${(H - P - (H - 2 * P) * (y1 === y0 ? 0.5 : (p.equity - y0) / (y1 - y0))).toFixed(1)}`).join(' ');
        const up = ys[ys.length - 1] >= ys[0];
        curveSvg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:170px;display:block;margin-top:var(--s3)" role="img" aria-label="Backtest equity curve">
          <polyline points="${pts}" fill="none" stroke="${up ? '#2fbf71' : '#e5484d'}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
      }
      const perSym = res.per_symbol ? Object.entries(res.per_symbol) : [];
      host.innerHTML = `
        <p class="muted small">${esc(params?.dataset || '')} · ${esc((params?.symbols || []).join(', '))} · ${esc(String(params?.last_bars || ''))} bars · honest fees/fills · frozen data</p>
        <div class="grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:var(--s2);margin-top:var(--s3)">
          ${tiles.map(([k, v, c]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v num ${c}">${esc(String(v))}</div></div>`).join('')}
        </div>
        ${curveSvg}
        ${perSym.length ? `<div class="tbl-wrap mt-3"><table class="tbl">
          <thead><tr><th>Symbol</th><th class="r">Trades</th><th class="r">Net PnL</th><th class="r">Win rate</th></tr></thead>
          <tbody>${perSym.map(([s, row]) => `<tr><td>${esc(s.replace(':USDT', '').replace('/USDT', ''))}</td>
            <td class="num r">${row.trades}</td>
            <td class="num r ${pnlClass(row.net_pnl)}">${usd(row.net_pnl)}</td>
            <td class="num r">${(row.win_rate * 100).toFixed(0)}%</td></tr>`).join('')}</tbody></table></div>` : ''}
        <p class="muted small mt-3">Past simulated performance does not guarantee future results.</p>`;
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  /* ═══════════════ Boot ═══════════════ */
  const RENDER = { home: renderHome, chat: renderChat, markets: renderMarkets, signals: renderSignals,
                   feed: renderFeed, trade: renderTrade, portfolio: renderPortfolio,
                   leaderboard: renderLeaderboard, lab: renderLab, engine: renderEngine,
                   account: renderAccount };

  window.addEventListener('hashchange', () => showView(location.hash.slice(1) || 'home'));

  // SSE: refresh the bits that changed, only re-render if the view shows them.
  connectStream({
    scan: () => { cache.scan = null; getScan().then(updateConnChip); if (currentView === 'engine') showView('engine'); },
    portfolio: () => { cache.portfolio = null; if (currentView === 'home' || currentView === 'portfolio') showView(currentView); },
    trade: () => { cache.portfolio = null; toast('Trade update from the engine.'); if (currentView === 'home' || currentView === 'portfolio' || currentView === 'trade') showView(currentView); },
    signals: () => { if (currentView === 'signals') showView('signals'); },
    activity: onActivity,
  });

  getScan().then(updateConnChip);
  showView(location.hash.slice(1) || 'home');
})();

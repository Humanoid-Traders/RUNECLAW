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

  // Ordered by the new-user JOURNEY, not build order: the core loop
  // (chat → read signals → trade → see portfolio → browse markets) comes first,
  // the analyst/advanced surfaces follow, and Account sits last. Views are
  // hash-routed by id, so order only affects display, never routing.
  const VIEWS = [
    { id: 'home',      label: 'Home',      icon: 'icon-home' },
    { id: 'chat',      label: 'AI Chat',   icon: 'icon-chat' },
    { id: 'signals',   label: 'Signals',   icon: 'icon-radar' },
    { id: 'news',      label: 'News',      icon: 'icon-globe' },
    { id: 'trade',     label: 'Trade',     icon: 'icon-target' },
    { id: 'portfolio', label: 'Portfolio', icon: 'icon-chart' },
    { id: 'tax',       label: 'Tax',       icon: 'icon-check' },
    { id: 'reputation', label: 'Reputation', icon: 'icon-shield' },
    { id: 'counterparty', label: 'Counterparty', icon: 'icon-shield' },
    { id: 'worlds',    label: 'Worlds',    icon: 'icon-sparkle' },
    { id: 'dapps',     label: 'dApps',     icon: 'icon-bolt' },
    { id: 'markets',   label: 'Markets',   icon: 'icon-globe' },
    { id: 'macro',     label: 'Macro',     icon: 'icon-shield' },
    { id: 'guardian',  label: 'Guardian',  icon: 'icon-check' },
    { id: 'deepscan',  label: 'Deep Scan', icon: 'icon-target' },
    { id: 'feed',      label: 'Live Feed', icon: 'icon-sparkle' },
    { id: 'leaderboard', label: 'Leaders', icon: 'icon-target' },
    { id: 'lab',       label: 'Lab',       icon: 'icon-sparkle' },
    { id: 'hub',       label: 'Agent Hub', icon: 'icon-bolt' },
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
        <svg class="icon" aria-hidden="true"><use href="#${v.icon}"></use></svg><span data-i18n="nav.${v.id}">${v.label}</span>
      </a>`).join('');
  }
  function renderNav(active) {
    document.getElementById('railNav').innerHTML = navHtml(active);
    document.getElementById('tabbarNav').innerHTML = navHtml(active);
    // Localize the freshly-built nav to the active language (the i18n engine
    // applied once at load; nav is (re)built later on every view change).
    if (window.RCI18N) { RCI18N.apply(document.getElementById('railNav'));
                         RCI18N.apply(document.getElementById('tabbarNav')); }
    // UX-4: the mobile tabbar scrolls horizontally behind a hidden scrollbar,
    // so the active tab can sit off-screen (half the product looked missing on
    // phones). Center it after paint — block:'nearest' avoids any vertical jump.
    requestAnimationFrame(() => {
      const cur = document.querySelector('#tabbarNav a[aria-current="page"]');
      if (cur) cur.scrollIntoView({ inline: 'center', block: 'nearest' });
    });
  }
  function every(ms, fn) { viewTimers.push(setInterval(fn, ms)); }
  function showView(id, opts = {}) {
    // Support "#view/section" deep links (e.g. #account/akeys): the view part
    // routes normally; the section part scrolls the matching panel into view
    // after render, so a checklist CTA lands on the RIGHT card, not the top of
    // a long page.
    const _slash = String(id).indexOf('/');
    const section = _slash >= 0 ? id.slice(_slash + 1) : (opts.section || '');
    if (_slash >= 0) id = id.slice(0, _slash);
    if (!VIEWS.some(v => v.id === id)) id = 'home';
    currentView = id;
    viewTimers.forEach(clearInterval);
    viewTimers = [];
    renderNav(id);
    // Soft refresh (live SSE nudges): update in place — no scroll-to-top jump
    // and no replayed entrance stagger. Only real navigation gets the full
    // "assembling itself" treatment.
    container.classList.toggle('rc-soft', !!opts.soft);
    if (!opts.soft) window.scrollTo({ top: 0 });
    // Pull the docked chat back out before the container is wiped; the chat
    // view re-docks it. Other views keep the floating FAB.
    if (window.RCChat) window.RCChat.unmountInline();
    // Free any live 3D agent viewer before its DOM host is wiped (reclaims the
    // WebGL context; the chat/hub views re-mount their own).
    if (window.RCAgent3D) window.RCAgent3D.disposeAll();
    RENDER[id]();
    // Localize the freshly-rendered view (headers etc.) to the active language.
    if (window.RCI18N) RCI18N.apply(container);
    // Deep-link section scroll: panels mount their skeleton first and fill
    // async, so poll briefly for the target panel, then scroll + flash it.
    if (section) {
      let _tries = 0;
      const _seek = () => {
        const el = document.getElementById('p-' + section);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
          el.classList.remove('sec-flash'); void el.offsetWidth; el.classList.add('sec-flash');
        } else if (_tries++ < 25) { setTimeout(_seek, 100); }
      };
      setTimeout(_seek, 60);
    }
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

  // UX-4: one-tap paper-trade — a signal's geometry stashed here by a "Trade"
  // button, then applied when the Trade view mounts (survives the hash-nav
  // re-render). Cleared on apply so a later manual visit starts blank.
  let tradePrefill = null;

  function loginGate(text) {
    return stateBlock({ icon: 'icon-user', text, cta: { label: 'Log in or create an account', href: '/' } });
  }
  // VAPID application-server key: base64url → Uint8Array (push subscribe).
  function urlB64ToU8(s) {
    const pad = '='.repeat((4 - s.length % 4) % 4);
    const raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
  }
  // Map each view header's English title to an i18n key base, so viewHead can
  // emit data-i18n without touching the ~12 call sites. Unmapped titles just
  // render in English (the in-markup fallback).
  const VH_KEYS = {
    'Home': 'home', 'AI Chat': 'chat', 'Agent Hub': 'hub', 'Markets': 'markets',
    'Macro': 'macro', 'Guardian': 'guardian', 'Signals': 'signals',
    'Deep Scan': 'deepscan', 'Live Feed': 'feed', 'Trade': 'trade',
    'Portfolio': 'portfolio', 'Leaderboard': 'leaderboard', 'Strategy Lab': 'lab',
    'Engine': 'engine', 'Account': 'account',
  };
  function viewHead(title, sub) {
    const k = VH_KEYS[title];
    const tAttr = k ? ` data-i18n="vh.${k}.title"` : '';
    const sAttr = (k && sub) ? ` data-i18n="vh.${k}.sub"` : '';
    return `<div class="view-head"><h1${tAttr}>${esc(title)}</h1>${sub ? `<span class="sub"${sAttr}>${esc(sub)}</span>` : ''}</div>`;
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
      // A REAL live event should announce itself — rise in instead of popping.
      if (list.firstElementChild) list.firstElementChild.classList.add('rc-rise');
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
      + (LOGGED_IN ? `<section class="panel" id="p-tripwires">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-alert"></use></svg>My tripwires
            <span class="badge" style="margin-left:auto" title="One-shot alerts — each disarms after it trips">one-shot</span></h2>
          <p style="color:var(--text-2);margin-bottom:var(--s3)">Personal price alerts, delivered as push notifications.
            You can also just tell the chat: <i>"alert me when BTC drops below $100k"</i>.</p>
          <form class="row" id="alertForm" style="gap:var(--s2);flex-wrap:wrap;margin-bottom:var(--s3)">
            <input class="input" id="alertSym" placeholder="BTC" style="width:7rem" maxlength="10" aria-label="Symbol" required>
            <select class="input" id="alertOp" aria-label="Direction" style="width:auto">
              <option value=">">price above</option>
              <option value="<">price below</option>
            </select>
            <input class="input" id="alertTh" type="number" step="any" min="0" placeholder="100000" style="width:9rem" aria-label="Level" required>
            <select class="input" id="alertMode" aria-label="Alert mode" style="width:auto">
              <option value="once">one-shot</option>
              <option value="recurring">recurring (hourly max)</option>
            </select>
            <button class="btn btn--primary btn--sm" type="submit">Arm alert</button>
          </form>
          <div id="alertList"><div class="skel"></div></div>
        </section>` : '')
      + `<section class="panel">
          <div class="row" id="feedChips" style="gap:var(--s2);flex-wrap:wrap;margin-bottom:var(--s3)"></div>
          <div id="feedLive"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>
        </section>`;
    if (LOGGED_IN) wireAlertsPanel();
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

  /* ── My tripwires (custom one-shot alerts → web push) ── */
  async function loadAlertList() {
    const el = document.getElementById('alertList');
    if (!el) return;
    const r = await fetchJSON('/api/alerts').catch(() => null);
    if (!r || !r.ok) {
      el.innerHTML = '<p style="color:var(--text-2)">Could not load alerts.</p>';
      return;
    }
    const rows = (r.data && r.data.alerts) || [];
    if (!rows.length) {
      el.innerHTML = '<p style="color:var(--text-2)">No alerts armed. Set a level above, or ask the chat.</p>';
      return;
    }
    el.innerHTML = rows.map((a) => {
      const state = a.active
        ? '<span class="badge badge--success">armed</span>'
        : `<span class="badge">tripped${a.trigger_price != null ? ' @ ' + fmtPrice(a.trigger_price) : ''}</span>`;
      return `<div class="row" style="gap:var(--s2);align-items:center;padding:var(--s1) 0;border-bottom:1px solid var(--border)">
          <b>${esc(a.label)}</b> ${state}
          <button class="btn btn--sm" data-del="${a.id}" type="button" style="margin-left:auto" aria-label="Delete alert">✕</button>
        </div>`;
    }).join('');
    el.onclick = async (e) => {
      const b = e.target.closest('button[data-del]'); if (!b) return;
      const del = await fetchJSON(`/api/alerts/${b.dataset.del}`, { method: 'DELETE' }).catch(() => null);
      toast(del && del.ok ? 'Alert deleted.' : 'Could not delete that alert.');
      loadAlertList();
    };
  }

  function wireAlertsPanel() {
    const form = document.getElementById('alertForm');
    if (!form) return;
    form.onsubmit = async (e) => {
      e.preventDefault();
      const symbol = document.getElementById('alertSym').value.trim();
      const op = document.getElementById('alertOp').value;
      const threshold = parseFloat(document.getElementById('alertTh').value);
      if (!symbol || !isFinite(threshold)) return;
      const mode = document.getElementById('alertMode')?.value === 'recurring' ? 'recurring' : 'once';
      const r = await fetchJSON('/api/alerts', {
        method: 'POST', body: { symbol, metric: 'price', op, threshold, mode },
      }).catch(() => null);
      if (r && r.ok) {
        toast(`Armed: ${r.data.label}`);
        form.reset();
        loadAlertList();
      } else {
        toast((r && r.data && r.data.error) || 'Could not arm that alert.');
      }
    };
    loadAlertList();
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
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg><span data-i18n="home.welcome_title">Meet your agent</span></h2>
          <p style="max-width:62ch;color:var(--text-2)" data-i18n="home.welcome_body">Welcome to RUNECLAW. From here on, an autonomous trading agent works this dashboard with you —
          it scans the market around the clock, explains every read, and only ever trades through a strict risk gate. Three good first moves:</p>
          <div class="row mt-3" style="gap:var(--s2);flex-wrap:wrap">
            <a class="btn btn--primary btn--sm" href="#chat" data-i18n="home.welcome_1">💬 1 · Say hello to your agent</a>
            <a class="btn btn--sm" href="#signals" data-i18n="home.welcome_2">📡 2 · Watch it read the market</a>
            <a class="btn btn--sm" href="#trade" data-i18n="home.welcome_3">🎯 3 · Place a risk-gated paper trade</a>
          </div>
          <button class="btn btn--ghost btn--sm mt-3" id="welcomeDismiss" type="button" data-i18n="home.welcome_dismiss">Got it — don't show again</button>
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
        ${LOGGED_IN ? `<section class="panel" id="p-cmd" style="padding-top:var(--s3);padding-bottom:var(--s3)"><div id="c-cmd"><div class="skel"></div></div></section>` : ''}
        <section class="panel" id="p-next"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-rocket"></use></svg>Getting started</h2><div id="c-next"><div class="skel"></div></div></section>
        ${LOGGED_IN ? `<section class="panel" id="p-agent"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>Your agent
          <span class="right muted small">what it's doing for you</span></h2><div id="c-agent"><div class="skel"></div></div></section>` : ''}
        <section class="panel" id="p-mind"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg>Agent mind-stream
          <span class="right"><a class="small" href="#feed">full feed →</a></span></h2><div id="c-mind"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel" id="p-verify"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Don't trust the dashboard — verify the fills
          <span class="right"><a class="small" href="/proof">Proof of PnL →</a></span></h2>
          <div id="c-verify"><p style="max-width:64ch;color:var(--text-2);margin:0 0 12px">Every figure here is reconstructed from raw exchange fills and published as a sealed, hash-verifiable statement. Re-derive the hash in your own browser — no login, no trust required.</p>
            <div class="row" style="gap:var(--s2);flex-wrap:wrap">
              <a class="btn btn--sm" href="/proof">🔐 Re-verify the fills</a>
              <a class="btn btn--sm btn--ghost" href="/track">📈 Public track record</a>
            </div></div></section>
        <section class="panel" id="p-macmini"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Macro backdrop
          <span class="right"><a class="small" href="#macro">open Macro →</a></span></h2><div id="c-macmini"><div class="skel"></div></div></section>
        ${LOGGED_IN ? `<section class="panel" id="p-letter"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>The Agent Letter
          <select class="input" id="letterWeek" aria-label="Letter week" style="margin-left:auto;width:auto;padding:2px 8px"></select>
          <a class="small" href="/letter" style="margin-left:8px;white-space:nowrap">public archive →</a></h2>
          <div id="c-letter"><div class="skel"></div><div class="skel"></div></div></section>` : ''}
        <section class="panel" id="p-hpos"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>Open positions</h2><div id="c-hpos"><div class="skel"></div><div class="skel"></div></div></section>
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
      // Fresh-tick flash: when a live push re-renders this panel with a
      // different equity, the number glows up/down for a beat (rc-flash-*).
      const eqFlash = (window._rcLastEquity != null && pf.equity != null
                       && pf.equity !== window._rcLastEquity)
        ? (pf.equity > window._rcLastEquity ? ' rc-flash-up' : ' rc-flash-down') : '';
      if (pf.equity != null) window._rcLastEquity = pf.equity;
      return `<div class="row" style="justify-content:space-between;align-items:flex-start">
        <div class="stat">
          <div class="k">My equity ${offline}</div>
          <div class="v big${eqFlash}">${fmtMoney(pf.equity)}</div>
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
      // Mission-control command bar: a one-glance status strip of the things
      // that matter right now — mode, agent stance, open positions, ⚠️ how many
      // are UNPROTECTED (no exchange stop), today's PnL, and whether trading is
      // paused. Each chip deep-links to where you act on it. Reads live sources
      // and auto-refreshes with the home view on portfolio/scan/trade SSE.
      renderPanel(C('cmd'), async () => {
        const [pf, posR, ctlR, scanR] = await Promise.all([
          getPortfolio(),
          fetchJSON('/api/positions', { timeoutMs: 12000 }).catch(() => null),
          fetchJSON('/api/controls/status', { timeoutMs: 10000 }).catch(() => null),
          getScan().catch(() => null),
        ]);
        const pos = posR && posR.ok ? posR.data : null;
        const openN = (pos?.positions || pf?.open_positions || []).length;
        const unp = pos?.unprotected_count || 0;
        const mode = (pf?.mode) || (pos?.live ? 'LIVE' : 'PAPER');
        const paused = !!ctlR?.data?.paused;
        const stance = scanR?.circuit_breaker?.strategy_mode || null;
        const daily = pf?.daily_pnl;
        const chip = (href, k, v, cls) => {
          const inner = `<span class="mc-k">${k}</span>${v}`;
          return href ? `<a class="mc-chip${cls ? ' ' + cls : ''}" href="${href}">${inner}</a>`
            : `<div class="mc-chip${cls ? ' ' + cls : ''}">${inner}</div>`;
        };
        const cells = [];
        cells.push(chip('#portfolio', 'Mode', `<span class="chip ${mode === 'LIVE' ? 'chip--live' : ''}">${mode === 'LIVE' ? 'LIVE' : 'PAPER'}</span>`));
        if (stance) cells.push(chip('#engine', 'Stance', `<b>${esc(String(stance))}</b>`));
        cells.push(chip('#portfolio', 'Open', `<b>${openN}</b>`));
        if (unp > 0) cells.push(chip('#portfolio', '⚠️ Unprotected', `<b>${unp}</b>`, 'mc-chip--alert'));
        if (daily != null) cells.push(chip(null, 'Today', `<b class="num ${pnlClass(daily)}">${signed(daily)}</b>`));
        if (paused) cells.push(chip('#account/actl', '', `<span class="chip chip--warn">Paused</span>`));
        return `<div class="mc-bar">${cells.join('')}</div>`;
      }, { empty: { text: '' } });

      // The Agent Letter — weekly fund-style letter from recorded data.
      const letterHtml = (letter) => {
        const secs = (letter.sections || []).map(s =>
          `<h3 class="small" style="margin:var(--s3) 0 var(--s1);letter-spacing:.06em;text-transform:uppercase;color:var(--text-3)">${esc(s.title)}</h3>
           <p style="max-width:70ch">${s.html}</p>`).join('');
        return `<p class="muted small" style="margin-bottom:var(--s1)">${esc(letter.period.start)} → ${esc(letter.period.end)}</p>
          <p style="font-weight:600">${esc(letter.headline)}</p>${secs}
          <p class="small muted" style="margin-top:var(--s3)"><i>${esc(letter.footer)}</i></p>`;
      };
      async function loadLetter(week) {
        await renderPanel(C('letter'), async () => {
          const r = await fetchJSON(week ? `/api/letter/${encodeURIComponent(week)}` : '/api/letter/latest');
          if (!r.ok || !r.data?.letter) return null;
          return letterHtml(r.data.letter);
        }, { empty: { icon: 'icon-sparkle', text: 'The first letter writes itself after the first full week of recorded activity.' } });
      }
      (async () => {
        const arc = await fetchJSON('/api/letter/archive').catch(() => null);
        const sel = document.getElementById('letterWeek');
        const weeks = arc?.data?.letters || [];
        if (sel) {
          sel.innerHTML = '<option value="">latest</option>'
            + weeks.map(w => `<option value="${esc(w.week_key)}">${esc(w.week_key)}</option>`).join('');
          sel.onchange = () => loadLetter(sel.value);
        }
        loadLetter('');
      })();

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
          <a class="btn btn--sm" href="#hub">🎛 Agent Hub</a>
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

    renderPanel(C('macmini'), async () => {
      const r = await fetchJSON('/api/macro', { auth: false, timeoutMs: 14000 });
      const m = r && r.ok && r.data && r.data.macro;
      if (!m || !m.band) return null;
      const toneCol = m.band.tone === 'up' ? 'var(--up)' : m.band.tone === 'down' ? 'var(--down)' : 'var(--text-2)';
      const pos = m.risk_score == null ? 50 : Math.max(0, Math.min(100, m.risk_score));
      const fg = m.fear_greed;
      const cell = (k, v) => `<div><div class="k muted small">${k}</div><div class="v" style="font-size:var(--fs-lg)">${v}</div></div>`;
      return `<div class="row" style="align-items:center;gap:var(--s5);flex-wrap:wrap">
          ${cell('Market posture', `<span style="color:${toneCol}">${esc(m.band.label)}</span> <span class="num muted" style="font-size:var(--fs-sm)">${m.risk_score}/100</span>`)}
          ${fg ? cell('Fear &amp; Greed', `${fg.value} <span class="muted small">${esc(fg.classification)}</span>`) : ''}
          ${m.regime && m.regime.label ? cell('Engine regime', esc(m.regime.label)) : ''}
          ${m.structure ? cell('Structure', esc(m.structure)) : ''}
        </div>
        <div style="position:relative;height:8px;border-radius:5px;margin-top:12px;background:linear-gradient(90deg,var(--down),#e0a63a 50%,var(--up))"><div style="position:absolute;top:-3px;left:calc(${pos}% - 2px);width:5px;height:14px;border-radius:3px;background:var(--text)"></div></div>`;
    }, { empty: { icon: 'icon-shield', text: 'The macro backdrop appears once market data is available.' } });

    renderPanel(C('hpos'), async () => {
      if (!LOGGED_IN) return loginGate('Log in to see your open positions.');
      // Show positions WITH their stop-loss protection status (🛡️ on exchange /
      // 🤖 bot-managed / ⚠️ unprotected) — the same safety truth as Portfolio,
      // capped to the top few with a link to the full view.
      const r = await fetchJSON('/api/positions', { timeoutMs: 15000 });
      const d = r.ok ? r.data : null;
      if (!d || !(d.positions || []).length) return null;
      return slPositionsHtml(d, { limit: 5 });
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
          cta: { label: 'Resend verification', href: '#account/aprof' } },
        { done: traded, label: 'Place a paper trade',
          hint: 'Real 23-check risk gate, zero risk — watch the engine execute.',
          cta: { label: 'Open the trade ticket', href: '#trade' } },
        { done: connected, pending: credsPending, label: 'Connect an exchange',
          hint: 'Link Bitget, Bybit, BingX or Hyperliquid keys to prepare live trading.',
          cta: { label: credsPending ? 'Finish connecting' : 'Connect exchange', href: '#account/akeys' } },
        { done: linked, label: 'Link Telegram',
          hint: 'Get trade alerts and chat with the agent from Telegram too.',
          cta: { label: 'Link Telegram', href: '#account/atg' } },
        { done: liveReady, locked: !connected, label: 'Go live',
          hint: liveReady ? 'Live trading is enabled for your account.'
            : 'Needs connected keys, your live toggle, and operator approval.',
          cta: { label: 'Review live controls', href: '#account/actl' } },
      ];
      // Completion moment: a step that flipped to Done since the last render
      // (on this device) gets a brief pop, so progress is felt, not silent.
      try {
        const prevDone = new Set(JSON.parse(localStorage.getItem('rc_chk_done') || '[]'));
        steps.forEach((s) => { s._justDone = s.done && !prevDone.has(s.label); });
        localStorage.setItem('rc_chk_done',
          JSON.stringify(steps.filter((s) => s.done).map((s) => s.label)));
      } catch (_e) { /* private-mode / quota — pop is cosmetic, skip */ }
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
        return `<li class="chk-item${s.done ? ' is-done' : ''}${s._justDone ? ' chk-pop' : ''}">`
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

  // Shared: open positions rendered with STOP-LOSS PROTECTION TRUTH (the /api/
  // positions payload) — a protection banner (🛡️ all protected / ⚠️ N without an
  // exchange stop / paper bot-managed) plus per-position rows with a per-stop
  // chip. Used by BOTH the Home command center and the Portfolio view so the two
  // read identically. Returns '' for an empty book. `opts.limit` caps rows and
  // adds a "+N more →" link to the full Portfolio view.
  function slPositionsHtml(d, opts) {
    opts = opts || {};
    const rows = (d && d.positions) || [];
    if (!rows.length) return '';
    const prot = d.protected_count || 0, unp = d.unprotected_count || 0;
    let banner;
    if (d.live && unp > 0) banner = `<div class="lpos-alert lpos-alert--bad">⚠️ <b>${unp} live position${unp === 1 ? '' : 's'} without an exchange stop.</b> The bot keeps re-arming the stop, but until it's placed the exchange itself won't auto-close it. Review below.</div>`;
    else if (d.live) banner = `<div class="lpos-alert lpos-alert--ok">🛡️ All ${prot} live position${prot === 1 ? '' : 's'} have their stop-loss on the exchange.</div>`;
    else banner = `<div class="lpos-alert">Paper — stops are bot-managed in-sim (no exchange order). Go live to place real exchange stops.</div>`;
    const shown = opts.limit ? rows.slice(0, opts.limit) : rows;
    const body = shown.map((p) => {
      const dist = (p.sl_dist_pct != null && p.sl_dist_pct > 0) ? ` <span class="muted small">(${p.sl_dist_pct}% away)</span>` : '';
      let chip;
      if (p.unprotected) chip = `<span class="chip chip--down">⚠️ unprotected</span>`;
      else if (p.sl_order === 'exchange') chip = `<span class="chip chip--up">🛡️ on exchange</span>`;
      else chip = `<span class="chip">🤖 bot-managed</span>`;
      const lev = p.leverage ? ` · <span class="muted small">${p.leverage}×</span>` : '';
      return `<div class="lpos-item">
        <div class="row" style="justify-content:space-between;gap:var(--s2);flex-wrap:wrap">
          <div><b>${esc(p.pair || String(p.symbol || '').split('/')[0])}</b> ${dirChip(p.direction)}${lev}</div>
          ${chip}
        </div>
        <div class="small muted">Entry ${fmtPrice(p.entry_price)} · SL ${fmtPrice(p.stop_loss)}${dist} · TP ${fmtPrice(p.take_profit)} · ${fmtMoney(p.size_usd, 0)}</div>
      </div>`;
    }).join('');
    const more = (opts.limit && rows.length > opts.limit)
      ? `<div class="small muted" style="padding-top:var(--s2)"><a href="#portfolio">+${rows.length - opts.limit} more →</a></div>` : '';
    return banner + body + more;
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
        <section class="panel" id="p-dex"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>DEX ↔ CEX — Hyperliquid vs this venue
          <span class="badge" style="margin-left:auto" title="Public data comparison — nothing here trades">read-only</span></h2>
          <div id="c-dex"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-rwa"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>RWA &amp; on-chain radar
          <span class="badge" style="margin-left:auto" title="Market intelligence from live venue tickers — the radar never trades">read-only</span></h2>
          <div id="c-rwa"><div class="skel"></div><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-airdrops"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>Airdrop &amp; testnet radar
          <span class="badge" style="margin-left:auto" title="Curated campaigns with guided checklists — you perform and sign every step yourself. RUNECLAW never automates participation or farms with multiple wallets.">guided-only</span></h2>
          <div id="c-airdrops"><div class="skel"></div><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-meme"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg>Meme &amp; AI-token radar
          <span class="badge" style="margin-left:auto" title="Live DEX pairs with an explicit per-token risk read — most memecoins go to zero; this never trades or launches anything">safety-first</span></h2>
          <div id="c-meme"><div class="skel"></div><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-flow"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>On-chain flow — DEX taker balance
          <span class="badge" style="margin-left:auto" title="24h buy/sell taker balance across the deepest DEX pools per asset. NOT exchange netflow, NOT whale attribution — keyless public data, honestly labeled">read-only</span></h2>
          <div id="c-flow"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-router"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-target"></use></svg>Venue router — cheapest exchange per pair
          <span class="badge" style="margin-left:auto" title="Funding-cost read from the hourly cross-venue scan. Recommendations only — RUNECLAW never auto-routes orders">manual-first</span></h2>
          <div id="c-router"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-mkpat"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-target"></use></svg>Engine pattern read
          <span class="right muted small">chart &amp; candle patterns · observations, not signals</span></h2>
          <div id="c-mkpat"><div class="skel"></div><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-universe"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Universe
          <span class="right"><label class="visually-hidden" for="uniSearch">Filter symbols</label><input class="input" id="uniSearch" placeholder="Filter…" style="width:130px;padding:5px 9px;font-size:var(--fs-sm)"></span></h2>
          <div id="c-universe"><div class="skel"></div><div class="skel"></div></div>
        </section>
      </div>`);

    // The deep-scan pattern read, surfaced right inside Markets (same cards as
    // the Deep Scan view). Reuses the synced batch — populated after a deep scan.
    renderPanel(C('mkpat'), async () => {
      const scan = await getScan();
      const ds = scan && scan.deepscan;
      if (!ds || !(ds.hits || []).length) return null;
      const top = ds.hits.slice(0, 8);
      return `<p class="muted small mb-2">${ds.count || ds.hits.length} symbols with detected patterns · ${esc(ds.tf || '')}${ds.generated_at ? ' · ' + esc(ds.generated_at) : ''}</p>`
        + top.map(deepScanCard).join('')
        + (ds.hits.length > top.length
          ? `<p class="small mt-1"><a href="#deepscan">See all ${ds.hits.length} in Deep Scan →</a></p>` : '');
    }, { empty: { icon: 'icon-target', text: 'Pattern read appears after the engine\'s next deep scan — the full board lives in the Deep Scan view.' } }).then(mountDeepScanMinis);

    const symSel = document.getElementById('chartSym');
    const DEFAULTS = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT','AVAXUSDT','SUIUSDT'];
    symSel.innerHTML = DEFAULTS.map(s => `<option value="${s}">${s.replace('USDT','')}/USDT</option>`).join('');

    // DEX ↔ CEX comparison — Hyperliquid mids vs this venue's perp prices.
    renderPanel(C('dex'), async () => {
      const r = await fetchJSON('/api/market/dex', { auth: false, timeoutMs: 12000 });
      const d = r.data;
      if (!r.ok || !d || !(d.rows || []).length) return null;
      return `<p class="muted small">Live mid prices on <b>${esc(d.dex)}</b> against ${esc(d.cex)} —
          the on-chain perps market, side by side. Avg |basis| ${d.avg_abs_delta_bps != null ? fmt(d.avg_abs_delta_bps, 1) + ' bps' : '—'}.</p>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Coin</th><th class="r">DEX mid</th><th class="r">CEX price</th><th class="r">Basis</th></tr></thead>
          <tbody>${d.rows.map(x => `<tr>
            <td><b>${esc(x.base)}</b></td>
            <td class="num r">$${fmtPrice(x.dex_mid)}</td>
            <td class="num r">${x.cex_price != null ? '$' + fmtPrice(x.cex_price) : '—'}</td>
            <td class="num r ${x.delta_bps >= 0 ? 'up' : 'down'}">${x.delta_bps != null ? (x.delta_bps >= 0 ? '+' : '') + fmt(x.delta_bps, 1) + ' bps' : '—'}</td></tr>`).join('')}</tbody>
        </table></div>
        <p class="muted small" style="margin-top:var(--s2)">${esc(d.execution_note)}</p>`;
    }, { empty: { icon: 'icon-globe', text: 'The DEX comparison lights up when Hyperliquid public data is reachable.' } });

    // RWA & on-chain radar — live sector read from public venue tickers.
    renderPanel(C('rwa'), async () => {
      const r = await fetchJSON('/api/market/rwa', { auth: false, timeoutMs: 12000 });
      const d = r.data;
      if (!r.ok || !d || !d.sector || !d.sector.listed) return null;
      const s = d.sector;
      const chip = (v) => v == null ? '—'
        : `<b class="num ${v >= 0 ? 'up' : 'down'}">${v >= 0 ? '+' : ''}${fmt(v, 2)}%</b>`;
      const head = `<p class="muted small">Tokenized real-world assets, read through this venue's live perpetual tickers.
          Sector 24h (volume-weighted): ${chip(s.change_24h_pct)}${s.vs_btc_pct != null ? ` · vs BTC ${chip(s.vs_btc_pct)}` : ''}
          · ${s.listed} tokens listed · $${fmtK(s.volume_24h_usd)} volume.
          Market intelligence only — nothing here trades.</p>`;
      const cats = d.categories.filter(c => c.listed).map((c) => `
        <h3 class="small" style="margin:var(--s3) 0 var(--s1);letter-spacing:.06em;text-transform:uppercase;color:var(--text-3)">
          ${esc(c.title)} <span class="muted">· ${c.listed}/${c.tracked} listed · 24h ${c.change_24h_pct != null ? (c.change_24h_pct >= 0 ? '+' : '') + fmt(c.change_24h_pct, 2) + '%' : '—'}</span></h3>
        <p class="muted small" style="margin-bottom:var(--s1)">${esc(c.blurb)}</p>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Token</th><th class="r">Price</th><th class="r">24h</th><th class="r">Volume</th></tr></thead>
          <tbody>${c.tokens.map(t => `<tr>
            <td><b>${esc(t.base)}</b></td>
            <td class="num r">$${fmtPrice(t.price)}</td>
            <td class="num r ${t.change_24h_pct >= 0 ? 'up' : 'down'}">${t.change_24h_pct >= 0 ? '+' : ''}${fmt(t.change_24h_pct, 2)}%</td>
            <td class="num r">$${fmtK(t.volume_24h_usd)}</td></tr>`).join('')}</tbody>
        </table></div>`).join('');
      return head + cats;
    }, { empty: { icon: 'icon-coin', text: 'The RWA radar lights up when live tickers are reachable.' } });

    // Airdrop & testnet radar — curated campaigns, guided checklists, and
    // (when logged in) honest wallet-readiness hints. Never automated.
    renderPanel(C('airdrops'), async () => {
      const r = LOGGED_IN
        ? await fetchJSON('/api/airdrops/me', { timeoutMs: 20000 })
        : await fetchJSON('/api/airdrops', { auth: false, timeoutMs: 12000 });
      const d = r.data;
      if (!r.ok || !d || !(d.campaigns || []).length) return null;
      const statusChip = (s) => ({
        live: '<span class="badge" style="color:var(--up)">live</span>',
        points: '<span class="badge">points</span>',
        expected: '<span class="badge muted">speculative</span>',
      }[s] || `<span class="badge muted">${esc(s)}</span>`);
      const cards = d.campaigns.map((c) => `
        <details style="border:1px solid var(--line);border-radius:8px;padding:var(--s2) var(--s3);margin-bottom:var(--s2)">
          <summary style="cursor:pointer"><b>${esc(c.name)}</b> <span class="muted small">${esc(c.project_type)}</span>
            ${statusChip(c.status)} <span class="muted small">· ${esc(c.costs)} · effort ${esc(c.effort)}</span>
            ${(c.hints || []).some(h => h.kind === 'ready') ? ' <span class="small" style="color:var(--up)">✅ wallet ready</span>' : ''}</summary>
          ${(c.hints || []).map(h => `<p class="small" style="margin-top:var(--s1);color:${h.kind === 'ready' ? 'var(--up)' : 'var(--text-2)'}">${esc(h.text)}</p>`).join('')}
          <p class="small muted" style="margin-top:var(--s1)">${esc(c.notes)}</p>
          <ol class="small" style="margin:var(--s2) 0 0 var(--s4);color:var(--text-2)">
            ${(c.steps || []).map(s => `<li style="margin-bottom:4px">${esc(s)}</li>`).join('')}
          </ol>
          <p class="small" style="margin-top:var(--s2)"><a href="${esc(c.official_url)}" target="_blank" rel="noopener">Official site ↗</a>
            <span class="muted">— verify everything there before acting.</span></p>
        </details>`).join('');
      return `<p class="muted small">Curated ${esc(d.curated_at)} — campaigns churn, always confirm on the official link.
          ${d.wallet_linked ? 'Readiness hints read from your linked wallet (read-only).' : 'Link a wallet (Sign-In with Ethereum) for readiness hints.'}</p>`
        + cards
        + `<p class="small muted" style="margin-top:var(--s2)"><i>${esc(d.anti_sybil)}</i></p>`;
    }, { empty: { icon: 'icon-sparkle', text: 'The airdrop radar is loading its curated catalog.' } });

    // Meme & AI-token radar — live DEX pairs with the safety read up front.
    // Ranked by real volume (never by pump %); risk tier rides every row.
    renderPanel(C('meme'), async () => {
      const r = await fetchJSON('/api/market/meme', { auth: false, timeoutMs: 15000 });
      const d = r.data;
      if (!r.ok || !d || !(d.tokens || []).length) return null;
      const TIER = {
        extreme: '<span class="badge" style="color:var(--down)">extreme</span>',
        high: '<span class="badge" style="color:var(--text-2)">high</span>',
      };
      const s = d.summary;
      const head = `<p class="muted small">${s.tokens} trending on-chain tokens · $${fmtK(s.volume_24h_usd)} 24h volume
          · <b class="${s.extreme_risk ? 'down' : ''}">${s.extreme_risk} at extreme risk</b>.
          Ranked by real volume, never by pump %.</p>`;
      const rows = d.tokens.slice(0, 12).map(t => `<tr>
          <td><b>${esc(t.symbol)}</b> <span class="muted small">${esc(t.chain_label)}</span></td>
          <td class="num r">$${fmtPrice(t.price_usd)}</td>
          <td class="num r ${t.change_24h_pct >= 0 ? 'up' : 'down'}">${t.change_24h_pct != null ? (t.change_24h_pct >= 0 ? '+' : '') + fmt(t.change_24h_pct, 1) + '%' : '—'}</td>
          <td class="num r">$${fmtK(t.volume_24h_usd)}</td>
          <td class="num r">${t.liquidity_usd != null ? '$' + fmtK(t.liquidity_usd) : '—'}</td>
          <td class="r" title="${esc((t.risk.flags || []).join(', ') || 'no extra flags — memecoins are high-risk by default')}">${TIER[t.risk.tier] || `<span class="badge muted">${esc(t.risk.tier)}</span>`}</td>
        </tr>`).join('');
      return head
        + `<div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Token</th><th class="r">Price</th><th class="r">24h</th><th class="r">Volume</th><th class="r">Liquidity</th><th class="r">Risk</th></tr></thead>
            <tbody>${rows}</tbody></table></div>`
        + `<p class="small muted" style="margin-top:var(--s2)">${esc(d.disclaimer)}</p>`;
    }, { empty: { icon: 'icon-radar', text: 'The meme radar lights up when DEXScreener public data is reachable.' } });

    // On-chain flow — 24h DEX taker balance for the majors. The same payload
    // the engine's gated on-chain voter consumes; honestly labeled.
    renderPanel(C('flow'), async () => {
      const r = await fetchJSON('/api/market/onchain-flow', { auth: false, timeoutMs: 15000 });
      const d = r.data;
      if (!r.ok || !d || !(d.bases || []).length) return null;
      const rows = d.bases.map(b => `<tr>
          <td><b>${esc(b.base)}</b> <span class="muted small">${b.pairs} pools</span></td>
          <td class="num r ${b.flow_bias >= 0 ? 'up' : 'down'}">${b.flow_bias >= 0 ? '+' : ''}${b.flow_bias}</td>
          <td class="num r">${fmt(b.buy_share_pct, 1)}%</td>
          <td class="num r">${b.txns_24h.toLocaleString()}${b.sample === 'thin' ? ' <span class="muted small">thin</span>' : ''}</td>
          <td class="num r">$${fmtK(b.volume_24h_usd)}</td>
        </tr>`).join('');
      return `<p class="muted small">${esc(d.note)}</p>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Asset</th><th class="r">Flow bias</th><th class="r">Buy share</th><th class="r">Txns 24h</th><th class="r">DEX volume</th></tr></thead>
          <tbody>${rows}</tbody></table></div>`
        + (d.unavailable.length ? `<p class="small muted" style="margin-top:var(--s2)">No usable on-chain sample right now: ${d.unavailable.map(esc).join(', ')}.</p>` : '');
    }, { empty: { icon: 'icon-globe', text: 'Flow reads appear when DEXScreener public data is reachable.' } });

    // Venue router — where is each pair cheapest to hold right now?
    // Pure funding-cost read; nothing here places or routes an order.
    renderPanel(C('router'), async () => {
      const r = await fetchJSON('/api/market/venue-router', { auth: false, timeoutMs: 12000 });
      const d = r.data;
      if (!r.ok || !d || !(d.rows || []).length) return null;
      const rows = d.rows.map(x => `<tr>
          <td><b>${esc(x.base)}</b></td>
          <td class="r"><b>${esc(x.long_venue)}</b> <span class="num muted small">${x.long_apr >= 0 ? '+' : ''}${x.long_apr}%</span></td>
          <td class="r"><b>${esc(x.short_venue)}</b> <span class="num muted small">${x.short_apr >= 0 ? '+' : ''}${x.short_apr}%</span></td>
          <td class="num r">${x.spread_apr}%</td>
          <td class="num r">${x.dex_basis_bps != null ? (x.dex_basis_bps >= 0 ? '+' : '') + fmt(x.dex_basis_bps, 1) + ' bps' : '—'}</td>
        </tr>`).join('');
      return `<p class="muted small">${esc(d.mechanics)}${d.stale ? ' <b>Scan is stale — verify before acting.</b>' : ''}
          ${d.report_age_minutes != null ? ` Scan age: ${d.report_age_minutes} min.` : ''}</p>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Pair</th><th class="r">Cheapest long</th><th class="r">Best paid short</th><th class="r">Spread APR</th><th class="r">DEX basis</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        <p class="small muted" style="margin-top:var(--s2)">${esc(d.manual_first)}</p>`;
    }, { empty: { icon: 'icon-target', text: 'The venue router lights up after the next hourly cross-venue funding scan.' } });

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

  // ── Deep-scan card mini-charts ──────────────────────────────────────────────
  // A compact, library-free candlestick sparkline drawn from the same
  // /api/market/candles the main chart uses. It shows the recent 4h price with
  // the swing high/low band (the reference levels most detected chart patterns
  // key off — double top/bottom, rectangle, triangle, H&S neckline) and the last
  // close, tinted by the dominant pattern's directional bias. Real price + real
  // computed levels; no fabricated geometry.
  function miniCandleSvg(rows, opts) {
    opts = opts || {};
    const cs = rows.map(r => ({ t: +r[0], o: +r[1], h: +r[2], l: +r[3], c: +r[4] }))
      .filter(c => isFinite(c.h) && isFinite(c.l) && isFinite(c.o) && isFinite(c.c))
      .sort((a, b) => a.t - b.t).slice(-44);
    if (cs.length < 3) return '';
    const W = 240, H = 64, P = 3;
    const min = Math.min(...cs.map(c => c.l)), max = Math.max(...cs.map(c => c.h));
    const span = (max - min) || 1;
    const x = i => P + i * ((W - 2 * P) / cs.length);
    const y = v => P + (max - v) / span * (H - 2 * P);
    const cw = Math.max(1.4, (W - 2 * P) / cs.length - 1.4);
    const f = n => n.toFixed(1);
    let out = '';
    // Swing high/low reference band.
    out += `<line x1="${P}" x2="${W - P}" y1="${f(y(max))}" y2="${f(y(max))}" stroke="var(--down)" stroke-opacity=".33" stroke-width="1" stroke-dasharray="3 3"/>`
      + `<line x1="${P}" x2="${W - P}" y1="${f(y(min))}" y2="${f(y(min))}" stroke="var(--up)" stroke-opacity=".33" stroke-width="1" stroke-dasharray="3 3"/>`;
    cs.forEach((c, i) => {
      const up = c.c >= c.o, col = up ? 'var(--up)' : 'var(--down)', bx = x(i);
      out += `<line x1="${f(bx + cw / 2)}" x2="${f(bx + cw / 2)}" y1="${f(y(c.h))}" y2="${f(y(c.l))}" stroke="${col}" stroke-width="1"/>`
        + `<rect x="${f(bx)}" y="${f(y(Math.max(c.o, c.c)))}" width="${f(cw)}" height="${f(Math.max(1, Math.abs(y(c.o) - y(c.c))))}" fill="${col}"/>`;
    });
    const last = cs[cs.length - 1];
    const lc = opts.bias === 'bull' ? 'var(--up)' : opts.bias === 'bear' ? 'var(--down)' : 'var(--gold-bright)';
    out += `<line x1="${P}" x2="${W - P}" y1="${f(y(last.c))}" y2="${f(y(last.c))}" stroke="${lc}" stroke-width="1" stroke-dasharray="2 2" stroke-opacity=".85"/>`;
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none" role="img" aria-label="Recent 4h price with pattern high/low band" style="display:block">${out}</svg>`;
  }

  const _miniCandles = new Map(); // "SYMUSDT" -> { ts, rows }
  let _miniIO = null;
  async function _fetchMiniCandles(sym) {
    const hit = _miniCandles.get(sym);
    if (hit && Date.now() - hit.ts < 120000) return hit.rows;
    const r = await fetchJSON(`/api/market/candles/${encodeURIComponent(sym)}?granularity=4h&limit=48`,
      { auth: false, timeoutMs: 10000 });
    const rows = (r && r.data && r.data.data) || [];
    _miniCandles.set(sym, { ts: Date.now(), rows });
    return rows;
  }
  // Lazily draw the mini-chart in every un-rendered .ds-mini as it scrolls into
  // view. Idempotent and re-run after each render that emits deep-scan cards;
  // fetches are cached and one-per-symbol, so switching views is cheap.
  function mountDeepScanMinis() {
    const nodes = document.querySelectorAll('.ds-mini[data-mini-sym]:not([data-mini-done])');
    if (!nodes.length) return;
    if (!('IntersectionObserver' in window)) { // no lazy path — draw immediately
      nodes.forEach(el => _drawMini(el));
      return;
    }
    if (_miniIO) _miniIO.disconnect();
    _miniIO = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        _miniIO.unobserve(e.target);
        _drawMini(e.target);
      }
    }, { rootMargin: '140px' });
    document.querySelectorAll('.ds-mini[data-mini-sym]:not([data-mini-done])').forEach(el => _miniIO.observe(el));
  }
  function _drawMini(el) {
    if (!el || el.getAttribute('data-mini-done')) return;
    el.setAttribute('data-mini-done', '1');
    const sym = el.getAttribute('data-mini-sym');
    const bias = el.getAttribute('data-mini-bias') || 'neutral';
    _fetchMiniCandles(sym).then((rows) => {
      if (!el.isConnected) return;
      const svg = rows && rows.length >= 3 ? miniCandleSvg(rows, { bias }) : '';
      if (svg) el.innerHTML = svg; else el.style.display = 'none';
    }).catch(() => { el.style.display = 'none'; });
  }

  /* ═══════════════ SIGNALS ═══════════════ */
  async function renderSignals() {
    container.innerHTML = viewHead('Signals', 'Every setup the engine generates — taken or not');
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-sstats"><div id="c-sstats"><div class="skel"></div></div></section>
        <section class="panel panel--primary" id="p-stream"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg>Signal stream</h2><div id="c-stream"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel" id="p-spat"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-target"></use></svg>Pattern read
          <span class="right muted small">the chart &amp; candle patterns behind live signals</span></h2><div id="c-spat"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel" id="p-sinsights"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>What works
          <span class="right muted small">win-rate by pattern & symbol (resolved signals)</span></h2><div id="c-sinsights"><div class="skel"></div></div></section>
      </div>`);

    // Deep-scan pattern read for the symbols that currently have signals — the
    // detectors behind the setups. Falls back to the whole board when none of
    // the live signals overlap the last deep scan.
    renderPanel(C('spat'), async () => {
      const [scan, sig] = await Promise.all([
        getScan(),
        fetchJSON('/api/signals?limit=40', { auth: false }).catch(() => ({ data: {} })),
      ]);
      const idx = deepScanIndex(scan);
      if (!idx.size) return null;
      const wanted = new Set((sig.data?.signals || []).map(s => dsBase(s.symbol)));
      let hits = [...idx.entries()].filter(([b]) => wanted.has(b)).map(([, h]) => h);
      const scoped = hits.length > 0;
      if (!scoped) hits = [...idx.values()];
      const note = scoped
        ? `Patterns behind ${hits.length} live signal ${hits.length === 1 ? 'symbol' : 'symbols'}.`
        : 'No live signal overlaps the last deep scan yet — showing the full pattern board.';
      return `<p class="muted small mb-2">${note}</p>` + hits.slice(0, 8).map(deepScanCard).join('')
        + `<p class="muted small mt-1">Patterns are observations, not signals · <a href="#deepscan">full Deep Scan →</a></p>`;
    }, { empty: { icon: 'icon-target', text: 'The pattern read fills in after the engine\'s next deep scan.' } }).then(mountDeepScanMinis);

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
          <thead><tr><th>Signal</th><th class="r">Conf.</th><th class="r">Entry</th><th class="r">Stop / Target</th><th class="r">R:R</th><th>Status</th><th class="r">Age</th><th class="r"></th></tr></thead>
          <tbody>${sigs.map(s => {
            const status = s.pnl != null
              ? `<span class="chip ${Number(s.pnl) > 0 ? 'chip--up' : 'chip--down'}">${Number(s.pnl) > 0 ? '✓ WIN' : '✗ LOSS'}</span>`
              : `<span class="chip">${esc(s.status || 'NEW')}</span>`;
            // UX-4: one-tap paper-trade — only for still-actionable signals
            // (unresolved + full geometry). Resolved rows show nothing.
            const canTrade = s.pnl == null && s.entry_price && s.stop_loss && s.take_profit;
            const tradeBtn = canTrade
              ? `<button class="btn btn--sm" data-ptrade='${esc(JSON.stringify({ d: s.direction, sy: s.symbol, e: s.entry_price, sl: s.stop_loss, tp: s.take_profit }))}'>Trade</button>`
              : '';
            return `<tr>
              <td data-label="Signal">${dirChip(s.direction)} <b>${esc(s.symbol)}</b><div class="muted small">${esc(s.pattern || '')}</div></td>
              <td data-label="Conf." class="r num">${Math.round((s.confidence || 0) * 100)}%</td>
              <td data-label="Entry" class="r num">${fmtPrice(s.entry_price)}</td>
              <td data-label="Stop / Target" class="r num muted">${fmtPrice(s.stop_loss)} / ${fmtPrice(s.take_profit)}</td>
              <td data-label="R:R" class="r num">${fmt(s.rr, 1)}</td>
              <td data-label="Status">${status}</td>
              <td data-label="Age" class="r muted small">${fmtAgo(s.created_at)}</td>
              <td data-label="" class="r">${tradeBtn}</td>
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

  /* ═══════════════ DEEP SCAN ═══════════════ */
  // One card per symbol: header (arrow/price/change/RSI), the top chart
  // patterns each with a signal-coloured confidence bar, and candle chips —
  // the same readout as the Telegram /deepscan card. "Observations, not
  // signals": this view never asserts a trade, only what the detectors saw.
  function _dsArrow(chg) {
    if (chg == null) return '<span style="color:var(--text-3)">●</span>';
    if (chg > 0) return '<span style="color:var(--up)">▲</span>';
    if (chg < 0) return '<span style="color:var(--down)">▼</span>';
    return '<span style="color:var(--text-3)">●</span>';
  }
  function _dsSigCol(sig) {
    return sig === 'bullish' ? 'var(--up)' : sig === 'bearish' ? 'var(--down)' : 'var(--text-3)';
  }
  // Base ticker (BTC/USDT | BTCUSDT | BTC → BTC) — used to match deep-scan hits
  // to universe rows and signal symbols so the pattern read follows the symbol.
  function dsBase(sym) {
    return String(sym || '').toUpperCase().replace('/USDT', '').replace(':USDT', '').replace(/USDT$/, '').replace(/[^A-Z0-9]/g, '');
  }
  // The synced deep-scan hits indexed by base ticker (empty until a deep scan
  // has synced). Shared by the Deep Scan, Markets, and Signals views.
  function deepScanIndex(scan) {
    const idx = new Map();
    for (const h of (scan && scan.deepscan && scan.deepscan.hits) || []) idx.set(dsBase(h.symbol), h);
    return idx;
  }
  function deepScanCard(h) {
    const chg = (h.chg == null || h.chg === '') ? null : Number(h.chg);
    const rsi = (h.rsi == null || h.rsi === '') ? null : Number(h.rsi);
    const sym = esc(String(h.symbol || '').replace('/USDT', '').replace(':USDT', ''));
    const priceHtml = h.price != null
      ? `<span class="num" style="color:var(--info)">${fmtPrice(h.price)}</span>` : '';
    const chgHtml = chg == null ? ''
      : `<span class="num" style="color:${chg > 0 ? 'var(--up)' : chg < 0 ? 'var(--down)' : 'var(--text-2)'}">${chg > 0 ? '+' : ''}${fmt(chg, 1)}%</span>`;
    let rsiHtml = '';
    if (rsi != null && isFinite(rsi)) {
      const tag = rsi > 70 ? ' OB' : rsi < 30 ? ' OS' : '';
      const col = rsi > 70 ? 'var(--down)' : rsi < 30 ? 'var(--up)' : 'var(--text-3)';
      rsiHtml = `<span class="num" style="color:${col}">RSI ${fmt(rsi, 0)}${tag}</span>`;
    }
    const vol = h.vol_spike ? '<span class="chip chip--warn">VOL</span>' : '';
    const pats = (h.chart_patterns || []).slice(0, 4).map(cp => {
      const col = _dsSigCol(String(cp.signal || 'neutral'));
      const pct = Math.round(Math.max(0, Math.min(1, Number(cp.confidence || 0))) * 100);
      return `<div style="display:flex;align-items:center;gap:8px;margin:5px 0">
        <span style="width:8px;height:8px;border-radius:50%;background:${col};flex:none"></span>
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(cp.name || '')}</span>
        <span class="ref-bar" style="width:88px;flex:none"><span style="width:${pct}%;background:${col}"></span></span>
        <span class="num muted" style="width:36px;text-align:right;flex:none">${pct}%</span>
      </div>`;
    }).join('');
    const candles = h.candle_patterns || {};
    const chips = Object.keys(candles).slice(0, 6).map(k => {
      const sig = String(candles[k] || 'neutral');
      const cls = sig === 'bullish' ? 'chip--up' : sig === 'bearish' ? 'chip--down' : '';
      return `<span class="chip ${cls}">${esc(k)}</span>`;
    }).join(' ');
    // Mini price chart with the pattern high/low band — lazy-drawn on scroll.
    const top = (h.chart_patterns || [])[0];
    const bias = top ? (String(top.signal) === 'bullish' ? 'bull' : String(top.signal) === 'bearish' ? 'bear' : 'neutral') : 'neutral';
    const base = dsBase(h.symbol);
    const mini = base.length >= 2
      ? `<div class="ds-mini" data-mini-sym="${esc(base + 'USDT')}" data-mini-bias="${bias}" aria-hidden="true"
           style="height:64px;margin-top:var(--s2);border-radius:6px;overflow:hidden;background:rgba(63,182,255,.035);pointer-events:none"></div>
         <div class="muted small" style="display:flex;justify-content:space-between;margin-top:4px;pointer-events:none">
           <span>4h · swing range</span><span style="color:${bias === 'bull' ? 'var(--up)' : bias === 'bear' ? 'var(--down)' : 'var(--text-3)'}">${top ? esc(top.name || '') : 'last price'}</span></div>`
      : '';
    return `<div class="ds-card" data-sym="${esc(String(h.symbol || ''))}" role="button" tabindex="0" title="Open ${sym} detail" style="border:1px solid var(--line);border-radius:var(--radius);padding:var(--s3) var(--s4);margin-bottom:var(--s3);cursor:pointer">
      <div class="row" style="justify-content:space-between;align-items:center;gap:var(--s2);flex-wrap:wrap">
        <span style="display:flex;gap:8px;align-items:center">${_dsArrow(chg)} <b>${sym}</b> ${priceHtml} ${chgHtml}</span>
        <span style="display:flex;gap:8px;align-items:center">${rsiHtml} ${vol}</span>
      </div>
      ${pats ? `<div class="mt-2">${pats}</div>` : '<p class="muted small mt-1">No chart patterns.</p>'}
      ${chips ? `<div class="mt-2" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center"><span class="muted">🕯</span> ${chips}</div>` : ''}
      ${mini}
    </div>`;
  }

  // ── Symbol detail drill-down ────────────────────────────────────────────────
  // Any pattern card (Deep Scan / Markets / Signals) opens a modal with the live
  // decision picture (confluence + top voters + regime) and the pattern read for
  // that symbol — one tap from "I see a pattern" to the whole picture.
  let symA11y = null;
  function closeSymModal() {
    const m = document.getElementById('symModal');
    if (!m) return;
    m.classList.add('hidden'); m.hidden = true;
    if (symA11y) { try { symA11y.close(); } catch (e) { /* fine */ } symA11y = null; }
  }
  function _insightBlock(d) {
    if (!d || d.error || typeof d.confluence !== 'number') {
      return '<p class="muted small">No live decision picture right now (the analysis bridge may be offline).</p>';
    }
    const dir = (d.confluence - 0.5) * 2;
    const lean = dir > 0.1 ? 'Bullish' : dir < -0.1 ? 'Bearish' : 'Neutral';
    const leanCls = dir > 0.1 ? 'up' : dir < -0.1 ? 'down' : '';
    const pos = Math.max(0, Math.min(100, d.confluence * 100));
    const meter = `<div class="stat"><div class="k">Directional confluence</div>
      <div class="v big ${leanCls}" style="font-size:var(--fs-lg)">${lean} <span class="num" style="font-size:var(--fs-base)">${signed(dir * 100, 0)}</span></div>
      <div style="position:relative;height:8px;border-radius:5px;margin-top:8px;background:linear-gradient(90deg,var(--down-dim),var(--surface-3) 45% 55%,var(--up-dim))">
        <div style="position:absolute;top:-3px;left:calc(${pos}% - 2px);width:4px;height:14px;border-radius:2px;background:var(--text)"></div></div>
      <div class="d muted small mt-2">Regime <b>${esc(String(d.regime || '—').replace(/_/g, ' '))}</b> · price ${fmtPrice(d.price)} · ATR ${fmtPrice(d.atr)}</div></div>`;
    const votes = (d.votes || []).map(v => ({ n: v.name, c: (v.vote || 0) * (v.weight || 0) }))
      .filter(v => Math.abs(v.c) > 1e-6).sort((a, b) => Math.abs(b.c) - Math.abs(a.c)).slice(0, 6);
    const maxC = votes.length ? Math.max(...votes.map(v => Math.abs(v.c))) : 1;
    const rows = votes.length ? votes.map(v => {
      const w = Math.max(4, Math.round(Math.abs(v.c) / maxC * 100)), bull = v.c >= 0;
      return `<div class="kv-row" style="align-items:center">
        <span class="small" style="font-family:var(--font-data);flex:0 0 44%">${esc(String(v.n).replace(/_/g, ' ').slice(0, 24))}</span>
        <span style="flex:1;height:7px;border-radius:4px;background:var(--surface-3);position:relative;overflow:hidden">
          <span style="position:absolute;${bull ? 'left' : 'right'}:50%;width:${w / 2}%;height:100%;background:var(${bull ? '--up' : '--down'})"></span></span>
        <span class="num small ${bull ? 'up' : 'down'}" style="flex:0 0 44px;text-align:right">${signed(v.c * 100, 0)}</span></div>`;
    }).join('') : '<div class="muted small">No active voters this bar.</div>';
    return meter + `<div class="mt-3"><div class="stat mb-2"><div class="k">Why — top voters</div></div>${rows}</div>`;
  }
  async function openSymbol(rawSym) {
    const m = document.getElementById('symModal');
    if (!m) return;
    const base = dsBase(rawSym);
    if (!base) return;
    const pair = base + '/USDT';
    document.getElementById('symModalTitle').textContent = base;
    const body = document.getElementById('symModalBody');
    body.innerHTML = '<div class="skel"></div><div class="skel"></div><div class="skel"></div>';
    m.hidden = false; m.classList.remove('hidden');
    if (window.RC.modalA11y) { symA11y = window.RC.modalA11y(m); symA11y.open(document.getElementById('symModalClose')); }
    const [pat, ins, scan] = await Promise.all([
      fetchJSON('/api/patterns?symbol=' + encodeURIComponent(pair) + '&timeframe=4h', { auth: false, timeoutMs: 14000 }).catch(() => null),
      fetchJSON('/api/insight?symbol=' + encodeURIComponent(pair) + '&timeframe=4h&limit=200', { auth: false, timeoutMs: 14000 }).catch(() => null),
      getScan().catch(() => null),
    ]);
    if (m.hidden) return; // closed while loading
    const hit = deepScanIndex(scan).get(base);
    let card = null;
    const pd = pat && pat.ok && pat.data;
    if (pd && ((pd.chart_patterns || []).length || Object.keys(pd.candlestick_patterns || pd.candle_patterns || {}).length)) {
      card = deepScanCard({ symbol: pair, price: pd.price, chg: pd.change_pct, rsi: pd.rsi,
        chart_patterns: pd.chart_patterns || [], candle_patterns: pd.candlestick_patterns || pd.candle_patterns || {} });
    } else if (hit) {
      card = deepScanCard(hit);
    }
    body.innerHTML = `
      <section class="mt-1">${_insightBlock(ins && ins.data)}</section>
      <h3 class="mt-4 mb-2" style="font-size:var(--fs-md)">Pattern read</h3>
      ${card || '<p class="muted small">No chart patterns detected on ' + esc(pair) + ' right now.</p>'}
      <div class="row mt-3" style="gap:var(--s2)">
        <a class="btn btn--sm" href="#markets" id="symGoChart">View in Markets</a>
        <button class="btn btn--sm" type="button" id="symAsk">Ask the AI</button>
      </div>
      <p class="muted small mt-2">Read-only decision picture · patterns are observations, not signals.</p>`;
    mountDeepScanMinis();
    const ask = document.getElementById('symAsk');
    if (ask) ask.onclick = () => {
      closeSymModal(); location.hash = 'chat';
      // Pre-fill the chat input once the chat view has docked (no chat.js dependency).
      setTimeout(() => {
        const inp = document.getElementById('chatInput');
        if (inp) { inp.value = 'What do you think of ' + base + ' right now?'; inp.focus(); }
      }, 350);
    };
    const go = document.getElementById('symGoChart');
    if (go) go.onclick = () => closeSymModal();
  }

  async function renderDeepScan() {
    container.innerHTML = viewHead('Deep Scan', 'The engine\'s per-symbol pattern read — chart & candlestick');
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel panel--primary" id="p-dscards"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg>Deep Scan
          <span class="right muted small">chart & candle patterns</span></h2><div id="c-dscards"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel" id="p-dslook"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-target"></use></svg>Check any symbol
          <span class="right muted small">live pattern read</span></h2>
          <div class="row" style="gap:var(--s2);flex-wrap:wrap">
            <input class="input" id="dsSym" placeholder="e.g. BTC" autocomplete="off" style="max-width:200px">
            <select class="input" id="dsTf" style="max-width:120px">
              <option value="15m">15m</option><option value="1h">1h</option>
              <option value="4h" selected>4h</option><option value="1d">1d</option>
            </select>
            <button class="btn btn--primary btn--sm" id="dsGo" type="button">Scan</button>
          </div>
          <div id="dsLookOut" class="mt-3"></div></section>
      </div>`);

    renderPanel(C('dscards'), async () => {
      const scan = await getScan();
      const ds = scan && scan.deepscan;
      if (!ds || !(ds.hits || []).length) return null;
      const hdr = `<p class="muted small mb-2">${ds.count || ds.hits.length} hits · ${esc(ds.tf || '')} · chart + candle patterns${ds.generated_at ? ' · ' + esc(ds.generated_at) : ''}</p>`;
      return hdr + ds.hits.map(deepScanCard).join('')
        + `<p class="muted small mt-2">Patterns are observations, not signals.</p>`;
    }, { empty: { icon: 'icon-radar', text: 'The deep-scan pattern read appears after the engine\'s next deep scan. Meanwhile, check any symbol below.' } }).then(mountDeepScanMinis);

    const out = document.getElementById('dsLookOut');
    async function lookup() {
      const raw = (document.getElementById('dsSym')?.value || '').trim().toUpperCase();
      if (!raw) { toast('Type a symbol first.'); return; }
      const sym = raw.includes('/') ? raw : raw + '/USDT';
      const tf = document.getElementById('dsTf')?.value || '4h';
      if (out) out.innerHTML = '<div class="skel"></div>';
      const r = await fetchJSON('/api/patterns?symbol=' + encodeURIComponent(sym) + '&timeframe=' + tf,
        { auth: false, timeoutMs: 14000 }).catch(() => null);
      const d = r && r.ok && r.data;
      if (!d) {
        if (out) out.innerHTML = `<p class="muted small">No live pattern read right now — the analysis bridge may be offline. The deep scan above still shows the engine's last sweep.</p>`;
        return;
      }
      const hit = {
        symbol: d.symbol || sym, price: d.price, chg: d.change_pct, rsi: d.rsi,
        vol_spike: false, chart_patterns: d.chart_patterns || [],
        candle_patterns: d.candlestick_patterns || d.candle_patterns || {},
      };
      const empty = !hit.chart_patterns.length && !Object.keys(hit.candle_patterns).length;
      if (out) out.innerHTML = empty
        ? `<p class="muted small">No patterns detected on ${esc(sym)} (${esc(tf)}) right now.</p>`
        : deepScanCard(hit);
      if (!empty) mountDeepScanMinis();
    }
    document.getElementById('dsGo')?.addEventListener('click', lookup);
    document.getElementById('dsSym')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') lookup(); });
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
        <section class="panel" id="p-authority">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Your trading authority
            <span class="badge" style="margin-left:auto" title="A revocable, tighten-only Authority Envelope you set in plain words. Enforce mode is required before any live trade on your own keys.">custody</span></h2>
          <p style="color:var(--text-2);margin-bottom:var(--s2)">Say what your agent may do — <i>"only majors, max $500 a trade, $2,000 a day, only on bitget"</i>. It compiles to a revocable envelope that <b>caps and authorizes</b> every live order. Nothing is enforced until you switch it on.</p>
          <form class="stack" id="authForm">
            <textarea class="input" id="authText" rows="2" maxlength="600" placeholder="only majors, max $500 per trade, $2000 a day, only on bitget"></textarea>
            <div class="row" style="gap:var(--s2);flex-wrap:wrap">
              <button class="btn btn--sm" type="submit">Preview</button>
              <button class="btn btn--sm btn--primary" type="button" id="authApply">Save (shadow)</button>
              <span id="authMsg" class="small muted" aria-live="polite"></span>
            </div>
          </form>
          <div id="c-authority" style="margin-top:var(--s2)"><div class="skel"></div></div>
        </section>
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
              <div class="row" style="gap:var(--s2);flex-wrap:wrap">
                <button class="btn btn--primary" type="submit">Review trade</button>
                <button class="btn btn--sm" type="button" id="tCopilotBtn">🤖 Second opinion</button>
                <span id="tMsg" class="small muted" aria-live="polite"></span>
              </div>
              <div id="tCopilot" class="small" style="margin-top:var(--s1)"></div>
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

    // ── Authority Envelope (custody) ──────────────────────────────────
    async function drawAuthority() {
      renderPanel(C('authority'), async () => {
        const r = await fetchJSON('/api/authority', { timeoutMs: 12000 });
        const d = r.data;
        if (!r.ok || !d) return null;
        const modePill = (m) => {
          const cls = m === 'enforce' ? 'mode-badge--live' : (m === 'shadow' ? 'mode-badge--paper' : '');
          return `<span class="mode-badge ${cls}">${esc((m || 'off').toUpperCase())}</span>`;
        };
        const checks = d.live_checklist || {};
        const labels = { feature_enabled: 'Operator enabled live web trading', bot_is_live: 'Bot in live mode',
          user_opted_in: 'You enabled live for your account', has_own_keys: 'Your own exchange keys connected',
          envelope_enforcing: 'Authority Envelope in enforce mode' };
        const list = Object.keys(labels).map(k =>
          `<div class="kv-row"><span>${checks[k] ? '✅' : '⬜'} ${esc(labels[k])}</span></div>`).join('');
        const bound = d.bound
          ? `<div class="kv-row"><span>Envelope</span>${modePill(d.mode)}</div>
             <pre class="small" style="white-space:pre-wrap;color:var(--text-2)">${esc(d.human_readable || '')}</pre>
             <div class="row" style="gap:var(--s2);flex-wrap:wrap;margin:var(--s2) 0">
               <button class="btn btn--sm" data-authmode="shadow">Shadow</button>
               <button class="btn btn--sm ${d.mode === 'enforce' ? 'btn--primary' : ''}" data-authmode="enforce">Enforce</button>
               <button class="btn btn--sm" data-authmode="off">Off</button>
               <button class="btn btn--sm btn--ghost" id="authRevoke">Revoke</button>
             </div>`
          : '<p class="muted small">No envelope yet — describe your limits above and Save.</p>';
        return `${bound}
          <div style="border-top:1px solid var(--line);margin-top:var(--s2);padding-top:var(--s2)">
            <p class="small" style="margin-bottom:var(--s1)"><b>Live-on-your-own-keys checklist</b>
              ${d.live_ready ? '<span class="mode-badge mode-badge--live" style="margin-left:var(--s1)">READY</span>' : ''}</p>
            ${list}
            <p class="small muted" style="margin-top:var(--s1)">${esc(d.live_reason || '')}</p>
          </div>`;
      }, { empty: { icon: 'icon-shield', text: 'Your authority envelope appears here once saved.' } });
    }
    async function authPost(path, body) {
      const r = await fetchJSON(`/api/authority${path}`, { method: 'POST', body, timeoutMs: 15000 }).catch(() => ({ ok: false, data: null }));
      return r;
    }
    document.getElementById('authForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const m = document.getElementById('authMsg');
      const text = document.getElementById('authText').value.trim();
      if (!text) { m.textContent = 'Type your limits first.'; return; }
      m.textContent = 'Compiling…';
      const r = await authPost('/preview', { text });
      if (!r.ok || !r.data?.ok) { m.innerHTML = `<span class="neg">${esc(r.data?.detail || 'Could not compile.')}</span>`; return; }
      const parts = (r.data.matched || []);
      const pend = (r.data.pending || []);
      m.innerHTML = parts.length
        ? `Understood: ${esc(parts.join('; '))}${pend.length ? ` · <span class="muted">pending: ${esc(pend.join('; '))}</span>` : ''}`
        : '<span class="neg">No limits recognized — try the example wording.</span>';
    });
    document.getElementById('authApply').addEventListener('click', async () => {
      const m = document.getElementById('authMsg');
      const text = document.getElementById('authText').value.trim();
      if (!text) { m.textContent = 'Type your limits first.'; return; }
      m.textContent = 'Saving…';
      const r = await authPost('/apply', { text, mode: 'shadow' });
      if (!r.ok || !r.data?.ok) { m.innerHTML = `<span class="neg">${esc(r.data?.detail || r.data?.error || 'Save failed.')}</span>`; return; }
      m.textContent = 'Saved in shadow mode — switch to Enforce below to arm it.';
      drawAuthority();
    });
    document.getElementById('c-authority').addEventListener('click', async (e) => {
      const mb = e.target.closest('[data-authmode]');
      if (mb) { await authPost('/mode', { mode: mb.dataset.authmode }); drawAuthority(); toast(`Authority mode: ${mb.dataset.authmode}`); return; }
      if (e.target.closest('#authRevoke')) { await authPost('/revoke', {}); drawAuthority(); toast('Authority revoked.'); }
    });
    drawAuthority();

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

    // AI co-pilot — deterministic second opinion before you commit (advice only).
    document.getElementById('tCopilotBtn').addEventListener('click', async () => {
      const out = $('tCopilot');
      const body = {
        direction: $('tDir').value, symbol: $('tSym').value.trim().toUpperCase(),
        entry: parseFloat($('tEntry').value), sl: parseFloat($('tSl').value),
        tp: parseFloat($('tTp').value),
      };
      const mr = $('tMargin').value.trim(); if (mr !== '') body.margin = Number(mr);
      if (!body.symbol || !body.entry || !body.sl || !body.tp) { out.innerHTML = '<span class="muted">Fill in symbol, entry, stop, and target first.</span>'; return; }
      out.innerHTML = '<span class="muted">Reviewing…</span>';
      const r = await fetchJSON('/api/trade/copilot', { method: 'POST', body, timeoutMs: 12000 }).catch(() => ({ ok: false, data: null }));
      const d = r.data;
      if (!r.ok || !d || d.error) { out.innerHTML = '<span class="muted">Co-pilot unavailable right now.</span>'; return; }
      if (d.verdict === 'invalid') { out.innerHTML = `<span class="neg">⛔ ${esc((d.flags?.[0]?.msg) || 'Invalid geometry.')}</span>`; return; }
      const badge = d.verdict === 'clear'
        ? '<span class="mode-badge mode-badge--paper">CLEAR</span>'
        : '<span class="mode-badge" style="background:var(--warn,#a86)">CAUTION</span>';
      const flags = (d.flags || []).map(f => `<div class="kv-row"><span>⚠️ ${esc(f.msg)}</span></div>`).join('');
      const notes = (d.notes || []).map(n => `<div class="kv-row"><span class="muted">· ${esc(n)}</span></div>`).join('');
      out.innerHTML = `${badge} <b>score ${d.score}/100</b> · R:R ${d.rr ?? '—'} · stop ${d.stop_pct}% · target ${d.target_pct}%
        ${flags}${notes}
        <p class="muted small" style="margin-top:var(--s1)">Advice only — the risk gate (and your Authority Envelope, for live) remain the authority.</p>`;
    });

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

    // UX-4: apply a one-tap-from-signal prefill, if one was stashed. Cleared
    // immediately so a later manual visit to Trade starts blank.
    if (tradePrefill) {
      const p = tradePrefill; tradePrefill = null;
      try {
        if (p.d) $('tDir').value = (String(p.d).toUpperCase() === 'SHORT' ? 'SHORT' : 'LONG');
        if (p.sy) $('tSym').value = String(p.sy).toUpperCase();
        if (p.e != null) $('tEntry').value = p.e;
        if (p.sl != null) $('tSl').value = p.sl;
        if (p.tp != null) $('tTp').value = p.tp;
        // Trigger the live risk/reward preview the form wires to input events.
        $('tEntry').dispatchEvent(new Event('input', { bubbles: true }));
        const tk = document.getElementById('p-ticket');
        if (tk) tk.scrollIntoView({ behavior: 'smooth', block: 'start' });
        $('tMsg').innerHTML = '<span class="pos">Prefilled from the signal — review, then place your paper trade.</span>';
      } catch (_) { /* prefill is best-effort; the form still works empty */ }
    }

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
      ${live ? '' : '<p class="muted small mt-2">Executes on your paper portfolio. The risk engine re-checks everything now.</p>'}
      ${(!live && pt.live_reason) ? `<p class="muted small mt-2">🔓 To trade live on your own account: ${esc(pt.live_reason)}.</p>` : ''}`;
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
      const r = await RC.postWithStepUp('/api/trade/confirm', { trade_id: pt.trade_id }, { timeoutMs: 35000 });
      if (!r.ok) {
        const reason = r.data?.error === 'live_not_enabled'
          ? `Live trading not enabled: ${r.data?.detail || 'your toggle + operator approval needed'}.`
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
        <section class="panel" id="p-lpos">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Open positions &amp; stop-loss
            <span class="badge" style="margin-left:auto" title="Whether each stop-loss is actually live ON THE EXCHANGE (protected) or bot-managed — the same truth the Telegram bot shows. Read-only.">read-only</span></h2>
          <div id="c-lpos"><div class="skel"></div><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-intel">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>Trade intelligence
            <span class="badge" style="margin-left:auto" title="Every figure derived only from your recorded closed trades — the buy-and-hold benchmark is rebuilt from each trade's own entry/exit prices, nothing is estimated">derived</span></h2>
          <div id="c-intel"><div class="skel"></div></div>
        </section>
        <section class="panel panel--primary" id="p-networth">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Net worth — everywhere
            <span class="badge" style="margin-left:auto" title="Read-only aggregation — RUNECLAW can read these balances, never move them">read-only</span></h2>
          <div id="c-networth"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-holdings">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-wallet"></use></svg>Funds by venue &amp; wallet
            <span class="badge" style="margin-left:auto" title="Every connected exchange and on-chain wallet chain, itemised — read-only, RUNECLAW can never move them">read-only</span></h2>
          <div id="c-holdings"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-sentry">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Risk sentry
            <span class="badge" style="margin-left:auto" title="A proactive read-only watch over your standing book — envelope drift, over-cap, concentration, crowding, daily spend. It flags; it never acts.">watch-only</span></h2>
          <div id="c-sentry"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-exposure">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Exposure — everywhere
            <span class="badge" style="margin-left:auto" title="Perp positions netted against on-chain spot — intelligence only, nothing here can act">read-only</span></h2>
          <div id="c-exposure"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-wallet">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-wallet"></use></svg>On-chain wallet
            <span class="badge" style="margin-left:auto" title="Balances read straight from the chain — RUNECLAW can never move them">read-only</span></h2>
          <div id="c-wallet"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-defi">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>DeFi positions
            <span class="badge" style="margin-left:auto" title="Aave/Lido/Uniswap read straight from protocol contracts — RUNECLAW warns, it can never manage a position">read-only</span></h2>
          <div id="c-defi"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-idleyield">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-coin"></use></svg>Idle yield — best rate for idle assets
            <span class="badge" style="margin-left:auto" title="Best cross-source rate per idle asset — non-custodial preferred so you keep custody. Recommendation only, nothing is moved">read-only</span></h2>
          <div id="c-idleyield"><div class="skel"></div></div>
        </section>
        <section class="panel" id="p-replay">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-bolt"></use></svg>What-if replay
            <span class="badge" style="margin-left:auto" title="Real recorded agent trades, mirrored at your stake — hypothetical, not account history">hypothetical</span></h2>
          <p style="color:var(--text-2);margin-bottom:var(--s3)">What if you'd mirrored <b>every trade the agent closed</b> with a fixed stake?
            Replayed from real recorded results — you can also just ask the chat.</p>
          <form class="row" id="replayForm" style="gap:var(--s2);flex-wrap:wrap;margin-bottom:var(--s3)">
            <label class="small" style="align-self:center">Stake $</label>
            <input class="input" id="replayStake" type="number" min="10" max="1000000" step="any" value="1000" style="width:8rem" aria-label="Stake per trade">
            <select class="input" id="replayDays" aria-label="Period" style="width:auto">
              <option value="0">all time</option>
              <option value="90">last 90d</option>
              <option value="30">last 30d</option>
              <option value="7">last 7d</option>
            </select>
            <button class="btn btn--primary btn--sm" type="submit">Replay</button>
          </form>
          <div id="c-replay"><div class="skel"></div></div>
        </section>
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
      const ce = r.data?.capital_events || 0;
      return equitySvg(snaps)
        + (ce ? `<p class="muted small" style="margin-top:var(--s2)">Capital basis changed ${ce} time${ce === 1 ? '' : 's'}
            (deposit, withdrawal, or paper→live switch) — the curve shows the current period only, so funding changes never draw as trading losses.</p>` : '');
    }, { empty: { icon: 'icon-chart', text: 'The equity curve draws once you have a few snapshots — trade and check back.' } });

    // Open positions with STOP-LOSS PROTECTION TRUTH — the web mirror of the
    // Telegram bot's /open_positions. For each position it shows whether the
    // stop is actually live on the exchange (🛡️ protected), bot-managed (paper /
    // in-sim), or ⚠️ UNPROTECTED (a live position missing its exchange stop —
    // real risk). Read-only; nothing here places, moves, or closes an order.
    renderPanel(C('lpos'), async () => {
      const r = await fetchJSON('/api/positions', { timeoutMs: 15000 });
      const d = r.ok ? r.data : null;
      if (!d) return null;
      if (!(d.positions || []).length) return `<p class="small muted">No open positions right now. When the agent opens one, its stop-loss protection status shows here.</p>`;
      return slPositionsHtml(d);
    }, { empty: { icon: 'icon-shield', text: 'No open positions — stop-loss protection status appears here when the agent opens one.' } });

    // Trade intelligence — alpha vs holding, expectancy, payoff, drawdown,
    // streaks. All re-derived server-side from the recorded closed trades.
    renderPanel(C('intel'), async () => {
      const r = await fetchJSON('/api/portfolio/intel', { timeoutMs: 15000 });
      const d = r.data?.intel;
      if (!r.ok || !d || !d.trades) return null;
      const rows = [];
      if (d.alpha) {
        const a = d.alpha;
        const s = a.mean_alpha_pct >= 0 ? '+' : '';
        rows.push(`<div class="kv-row"><span>Alpha vs holding <span class="muted small">per trade, vs buying &amp; holding the same asset</span></span>
          <b class="num ${a.mean_alpha_pct >= 0 ? 'pos' : 'neg'}">${s}${a.mean_alpha_pct}%</b></div>`);
        rows.push(`<div class="kv-row"><span>Beat their market</span><b class="num">${a.beat_market} of ${a.priced} (${a.beat_market_pct}%)</b></div>`);
        if (a.best && a.best.alpha_pct > 0) {
          rows.push(`<div class="kv-row"><span>Cleanest edge</span><b class="num">${esc(a.best.symbol)} +${a.best.alpha_pct}%</b></div>`);
        }
      }
      rows.push(`<div class="kv-row"><span>Expectancy <span class="muted small">avg net per close</span></span>
        <b class="num ${pnlClass(d.expectancy_usd)}">${d.expectancy_usd >= 0 ? '+' : '-'}$${Math.abs(d.expectancy_usd).toFixed(2)}</b></div>`);
      if (d.payoff_ratio !== null) {
        rows.push(`<div class="kv-row"><span>Payoff ratio <span class="muted small">avg win ÷ avg loss</span></span><b class="num">${d.payoff_ratio}</b></div>`);
      }
      rows.push(`<div class="kv-row"><span>Max realized drawdown</span><b class="num">$${Math.abs(d.max_drawdown_usd).toFixed(2)}</b></div>`);
      rows.push(`<div class="kv-row"><span>Longest streaks</span><b class="num">${d.longest_win_streak}W / ${d.longest_loss_streak}L</b></div>`);
      return rows.join('')
        + `<p class="small muted" style="margin-top:var(--s2)">Over ${d.trades} recorded closes${d.skipped ? ` (${d.skipped} skipped — unusable rows are never guessed at)` : ''}.</p>`;
    }, { empty: { icon: 'icon-sparkle', text: 'Intelligence appears after your first few closed trades.' } });

    // Net worth — everywhere: connected CEX + wallet (real) with paper
    // shown separately and NEVER counted into the real total.
    renderPanel(C('networth'), async () => {
      const r = await fetchJSON('/api/networth', { timeoutMs: 35000 });
      const d = r.data;
      if (!r.ok || !d || !d.sections) return null;
      const rows = [];
      const c = d.sections.cex;
      if (c && c.connected) {
        rows.push(`<div class="kv-row"><span>🏦 ${esc((c.venue || 'exchange').toUpperCase())} <span class="muted small">connected exchange</span></span>
          <b class="num">${c.ok && c.equity_usd != null ? '$' + fmt(c.equity_usd, 2) : `<span class="muted small">${esc(c.detail || 'unreadable')}</span>`}</b></div>`);
      } else {
        rows.push('<div class="kv-row"><span>🏦 Exchange</span><span class="muted small">none connected — /connect in Telegram</span></div>');
      }
      const w = d.sections.wallet;
      if (w && w.linked) {
        rows.push(`<div class="kv-row"><span>👛 Wallet <span class="muted small">on-chain</span></span>
          <b class="num">${w.total_usd != null ? '$' + fmt(w.total_usd, 2) : '<span class="muted small">unreadable</span>'}</b></div>`);
      } else {
        rows.push('<div class="kv-row"><span>👛 Wallet</span><span class="muted small">none linked — Sign-In with Ethereum</span></div>');
      }
      const p = d.sections.paper;
      if (p && p.equity_usd != null) {
        rows.push(`<div class="kv-row"><span>📄 Paper portfolio <span class="muted small">simulated — not counted</span></span>
          <b class="num muted">$${fmt(p.equity_usd, 2)}</b></div>`);
      }
      return rows.join('')
        + `<div class="kv-row" style="border-top:1px solid var(--line);margin-top:var(--s2);padding-top:var(--s2)">
            <span><b>Real total</b></span>
            <b class="num">${d.total_real_usd != null ? '$' + fmt(d.total_real_usd, 2) : '—'}</b></div>
          <p class="small muted" style="margin-top:var(--s2)">${esc(d.note)}</p>`;
    }, { empty: { icon: 'icon-globe', text: 'Net worth aggregates once a venue or wallet is reachable.' } });

    // Funds by venue & wallet — the same real money as net worth, but itemised
    // per source: one row per connected exchange, one per on-chain chain.
    // An unreadable source shows as unreadable, never a fabricated $0.
    renderPanel(C('holdings'), async () => {
      const r = await fetchJSON('/api/holdings', { timeoutMs: 40000 });
      const d = r.data;
      if (!r.ok || !d) return null;
      const rows = [];
      // Venues (connected exchanges).
      if (d.venues && d.venues.length) {
        for (const v of d.venues) {
          const name = esc((v.venue || 'venue').toUpperCase());
          const tag = v.active ? ' <span class="muted small">active</span>' : '';
          rows.push(`<div class="kv-row"><span>🏦 ${name}${tag}</span>
            <b class="num">${v.ok && v.equity_usd != null ? '$' + fmt(v.equity_usd, 2)
              : `<span class="muted small">${esc(v.detail || 'unreadable')}</span>`}</b></div>`);
        }
      } else if (d.venues_available) {
        rows.push('<div class="kv-row"><span>🏦 Exchanges</span><span class="muted small">none connected — /connect in Telegram</span></div>');
      } else {
        rows.push('<div class="kv-row"><span>🏦 Exchanges</span><span class="muted small">gateway unavailable</span></div>');
      }
      // Wallet chains.
      const w = d.wallet || {};
      if (w.linked && w.chains && w.chains.length) {
        for (const c of w.chains) {
          const extra = c.detail ? `<span class="muted small">${esc(c.detail)}</span>`
            : (c.total_usd != null ? '$' + fmt(c.total_usd, 2) : '<span class="muted small">unpriced</span>');
          const unp = (c.unpriced ? ` <span class="muted small">${c.unpriced} unpriced</span>` : '');
          rows.push(`<div class="kv-row"><span>👛 ${esc(c.label || c.chain)} <span class="muted small">wallet</span>${unp}</span>
            <b class="num">${extra}</b></div>`);
        }
      } else if (w.linked) {
        rows.push('<div class="kv-row"><span>👛 Wallet</span><span class="muted small">no balances on tracked chains</span></div>');
      } else {
        rows.push('<div class="kv-row"><span>👛 Wallet</span><span class="muted small">none linked — Sign-In with Ethereum</span></div>');
      }
      return rows.join('')
        + `<div class="kv-row" style="border-top:1px solid var(--line);margin-top:var(--s2);padding-top:var(--s2)">
            <span><b>Real total</b>${d.partial ? ' <span class="muted small">(partial — some sources unreadable)</span>' : ''}</span>
            <b class="num">${d.total_real_usd != null ? '$' + fmt(d.total_real_usd, 2) : '—'}</b></div>
          <p class="small muted" style="margin-top:var(--s2)">${esc(d.note)}</p>`;
    }, { empty: { icon: 'icon-wallet', text: 'Funds itemise once a venue or wallet is reachable.' } });

    // Idle yield — best cross-source rate per idle wallet asset. Non-custodial
    // (Lido/Aave, live) preferred honestly; recommendation only, nothing moves.
    renderPanel(C('idleyield'), async () => {
      const r = await fetchJSON('/api/idleyield', { timeoutMs: 32000 });
      const d = r.data;
      if (!r.ok || !d || d.available === false) return null;
      if (d.wallet_linked === false) {
        return `<p class="muted">${esc(d.note || 'Link a wallet to scan idle assets for the best rate.')}</p>`;
      }
      const recd = (d.recommendations || []).filter(x => x.status === 'recommended');
      if (!recd.length) {
        return `<p class="muted">${esc(d.note || 'No idle assets matched a known rate right now.')}</p>`;
      }
      const rows = recd.slice(0, 8).map(x => {
        const b = x.best;
        const cust = b.custodial
          ? '<span class="badge" title="The venue custodies the asset">custodial</span>'
          : '<span class="badge" style="color:var(--up)" title="You keep the keys">non-custodial</span>';
        return `<div class="kv-row" style="align-items:flex-start">
            <span>💤 <b>${esc(x.asset)}</b> <span class="muted small">$${fmt(x.idle_usd, 0)} idle</span><br>
              <span class="muted small">${esc(b.source)} ${cust}</span>
              ${x.note ? `<br><span class="muted small">↳ ${esc(x.note)}</span>` : ''}</span>
            <b class="num">${fmt(b.apy, 2)}% <span class="muted small">≈$${fmt(x.est_year_usd, 2)}/yr</span></b>
          </div>`;
      }).join('');
      const nc = (d.sources && d.sources.noncustodial) || 0;
      return rows
        + `<div class="kv-row" style="border-top:1px solid var(--line);margin-top:var(--s2);padding-top:var(--s2)">
            <span><b>If all deployed</b></span>
            <b class="num">≈$${fmt(d.total_est_year_usd, 2)}/yr</b></div>
          <p class="small muted" style="margin-top:var(--s2)">${nc} non-custodial rate(s) live (Lido/Aave via DefiLlama).
            Recommendation only — RUNECLAW never moves your funds.</p>`;
    }, { empty: { icon: 'icon-coin', text: 'Idle-yield scans your wallet assets once one is reachable.' } });

    // Risk sentry — proactive watch over the standing book (envelope drift,
    // over-cap, concentration, crowding, daily spend). Detection-only.
    renderPanel(C('sentry'), async () => {
      const r = await fetchJSON('/api/sentry', { timeoutMs: 18000 });
      const d = r.data;
      if (!r.ok || !d) return null;
      if (!d.alerts || !d.alerts.length) {
        return `<div class="kv-row"><span>🟢 Nothing flagged in your current posture.</span></div>
          <p class="small muted" style="margin-top:var(--s2)">Watches for envelope drift, over-cap positions,
            concentration, correlated crowding, and daily-spend. It flags — it never closes or resizes anything.</p>`;
      }
      const icon = { warn: '🔴', caution: '🟠', info: '🔵' };
      const rows = d.alerts.map(a =>
        `<div class="kv-row" style="align-items:flex-start"><span>${icon[a.level] || '•'} ${esc(a.msg)}</span></div>`).join('');
      const worst = d.worst_level === 'warn' ? 'mode-badge--live' : (d.worst_level === 'caution' ? '' : 'mode-badge--paper');
      return `<div style="margin-bottom:var(--s2)"><span class="mode-badge ${worst}">${esc((d.worst_level || 'clear').toUpperCase())}</span>
          <span class="muted small">${d.count} flag(s)${d.envelope_bound ? '' : ' · no Authority Envelope bound'}</span></div>
        ${rows}
        <p class="small muted" style="margin-top:var(--s2)">Detection-only — RUNECLAW never closes or resizes your positions.</p>`;
    }, { empty: { icon: 'icon-shield', text: 'The risk sentry appears once you have open positions.' } });

    // Exposure — perp positions netted against wallet spot, with the flags
    // a risk desk would raise (stacked longs, hedges, concentration).
    renderPanel(C('exposure'), async () => {
      const r = await fetchJSON('/api/exposure', { timeoutMs: 20000 });
      const d = r.data;
      if (!r.ok || !d || !(d.assets || []).length) return null;
      const flagBadge = (f) => f.includes('stacked_long')
        ? '<span class="badge" style="color:var(--down)">⚠️ doubled</span>'
        : f.includes('hedged') ? '<span class="badge">🛡 hedged</span>' : '';
      const rows = d.assets.map(a => `<tr>
          <td><b>${esc(a.base)}</b> ${flagBadge(a.flags)}</td>
          <td class="num r">${a.perp_long_usd ? '$' + fmt(a.perp_long_usd, 0) : '—'}</td>
          <td class="num r">${a.perp_short_usd ? '$' + fmt(a.perp_short_usd, 0) : '—'}</td>
          <td class="num r">${a.spot_usd ? '$' + fmt(a.spot_usd, 0) : '—'}</td>
          <td class="num r ${a.net_usd >= 0 ? 'up' : 'down'}">$${fmt(a.net_usd, 0)}</td>
        </tr>`).join('');
      const warn = (d.warnings || []).map(w =>
        `<p class="small" style="color:var(--down);margin-top:var(--s1)">⚠️ ${esc(w)}</p>`).join('');
      return `<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Asset</th><th class="r">Perp long</th><th class="r">Perp short</th><th class="r">Spot</th><th class="r">Net</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        <p style="margin-top:var(--s2)">Net <b class="num">$${fmt(d.net_total_usd, 0)}</b>
          · Gross <b class="num">$${fmt(d.gross_total_usd, 0)}</b>
          ${d.cash_usd ? `· Cash (stables) <b class="num">$${fmt(d.cash_usd, 0)}</b>` : ''}</p>
        ${warn}
        <p class="small muted" style="margin-top:var(--s2)">${esc(d.note)}</p>`;
    }, { empty: { icon: 'icon-shield', text: 'Exposure appears once you have open positions or non-stable wallet holdings.' } });

    // On-chain wallet mirror (SIWE-linked; strictly read-only, multi-chain).
    renderPanel(C('wallet'), async () => {
      const r = await fetchJSON('/api/wallet/portfolio', { timeoutMs: 25000 });
      const d = r.data;
      if (!r.ok || !d) return null;
      if (!d.linked) {
        return `<p class="muted">No wallet linked. Connect one with <b>Sign-In with Ethereum</b>
          (Account view) — linking is read-only: the wallet signs a login message, never a transaction.</p>`;
      }
      const short = `${d.address.slice(0, 6)}…${d.address.slice(-4)}`;
      if (!(d.assets || []).length) {
        const unreadable = (d.chains || []).filter(c => c.error).map(c => c.label);
        return `<p class="muted"><b class="num">${esc(short)}</b> — no balances found among the tracked assets
          ${d.chains ? `across ${d.chains.length} chains` : ''}.</p>`
          + (unreadable.length ? `<p class="muted small">${esc(unreadable.join(', '))} unreadable right now (RPC).</p>` : '');
      }
      // Per-chain groups (chains with balances first, unreadable ones noted).
      const groups = (d.chains || [{ label: d.chain, assets: d.assets, total_usd: d.total_usd }])
        .filter(c => c.assets && c.assets.length)
        .map(c => `
          <p class="small" style="margin-top:var(--s2)"><b>${esc(c.label)}</b>
            <span class="num muted">· $${Number(c.total_usd || 0).toLocaleString('en-US', { maximumFractionDigits: 2 })}</span></p>
          <div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Asset</th><th class="r">Amount</th><th class="r">Value</th></tr></thead>
            <tbody>${c.assets.map(a => `<tr>
              <td><b>${esc(a.symbol)}</b></td>
              <td class="num r">${Number(a.amount).toLocaleString('en-US', { maximumFractionDigits: 6 })}</td>
              <td class="num r">${a.usd != null ? '$' + Number(a.usd).toLocaleString('en-US', { maximumFractionDigits: 2 }) : 'unpriced'}</td></tr>`).join('')}</tbody>
          </table></div>`).join('');
      const unreadable = (d.chains || []).filter(c => c.error).map(c => c.label);
      return `<p class="muted small"><b class="num">${esc(short)}</b> —
          balances read straight from the chains.</p>
        ${groups}
        <p style="margin-top:var(--s2)">Total (priced, all chains): <b class="num">$${Number(d.total_usd).toLocaleString('en-US', { maximumFractionDigits: 2 })}</b></p>
        ${unreadable.length ? `<p class="muted small">${esc(unreadable.join(', '))} unreadable right now (RPC).</p>` : ''}`;
    }, { empty: { icon: 'icon-wallet', text: 'The wallet mirror lights up when a chain RPC is reachable.' } });

    // DeFi positions: Aave health factors, Lido stETH, Uniswap LP counts —
    // read from protocol contracts, with the warnings a risk desk would raise.
    renderPanel(C('defi'), async () => {
      const r = await fetchJSON('/api/defi', { timeoutMs: 25000 });
      const d = r.data;
      if (!r.ok || !d) return null;
      if (!d.linked) {
        return `<p class="muted">No wallet linked — link one in the <a href="#account">Account view</a>
          and your Aave, Lido and Uniswap positions appear here with liquidation-risk warnings.</p>`;
      }
      const bits = [];
      for (const a of (d.aave || [])) {
        const hfCls = a.health_factor === null ? '' : a.health_factor < 1.1 ? 'down' : a.health_factor < 1.5 ? 'chip--warn' : 'up';
        bits.push(`<div class="kv-row"><span>🏦 Aave v3 · ${esc(a.label)}</span>
          <b class="num">$${fmt(a.collateral_usd, 0)} coll · $${fmt(a.debt_usd, 0)} debt ·
          ${a.health_factor === null ? '<span class="muted small">no debt</span>' : `HF <span class="${hfCls}">${a.health_factor}</span>`}</b></div>`);
      }
      if (d.lido) {
        bits.push(`<div class="kv-row"><span>🌊 Lido stETH</span>
          <b class="num">${Number(d.lido.steth_amount).toLocaleString('en-US', { maximumFractionDigits: 4 })} — ${d.lido.usd != null ? '$' + fmt(d.lido.usd, 2) : 'unpriced'}</b></div>`);
      }
      for (const u of (d.uniswap || [])) {
        bits.push(`<div class="kv-row"><span>🦄 Uniswap v3 · ${esc(u.label)}</span>
          <b class="num">${u.positions} LP position${u.positions === 1 ? '' : 's'} <span class="muted small">counted, not valued</span></b></div>`);
      }
      if (!bits.length) return null;
      const warn = (d.warnings || []).map(w =>
        `<p class="small" style="color:var(--down);margin-top:var(--s1)">⚠️ ${esc(w)}</p>`).join('');
      return bits.join('') + warn
        + `<p class="small muted" style="margin-top:var(--s2)">${esc(d.note)}</p>`;
    }, { empty: { icon: 'icon-shield', text: 'No Aave, Lido or Uniswap v3 positions found on the tracked chains.' } });

    // What-if replay: mirror every closed agent trade at a fixed stake.
    async function runReplayPanel() {
      const stake = parseFloat(document.getElementById('replayStake').value) || 1000;
      const days = document.getElementById('replayDays').value;
      await renderPanel(C('replay'), async () => {
        const r = await fetchJSON(`/api/replay?stake=${encodeURIComponent(stake)}&days=${encodeURIComponent(days)}`);
        const d = r.data;
        if (!r.ok || !d || !d.trades) return null;
        const f = d.fixed;
        const cls = f.net_pnl_usd >= 0 ? 'up' : 'down';
        const curveSvg = d.curve.length >= 2
          ? equitySvg(d.curve.map(p => ({ snapshot_at: p.t, equity: p.equity }))) : '';
        return `
          <div class="stat-row" style="margin-bottom:var(--s3)">
            <div class="stat"><div class="k">Net (fixed $${fmt(d.stake, 0)}/trade)</div>
              <div class="v ${cls}">${fmtMoney(f.net_pnl_usd)}</div>
              <div class="small muted">${f.return_pct >= 0 ? '+' : ''}${fmt(f.return_pct, 1)}% per-stake · ${d.trades} trades</div></div>
            <div class="stat"><div class="k">Win rate</div>
              <div class="v">${fmt(d.win_rate_pct, 0)}%</div>
              <div class="small muted">${d.wins}W / ${d.losses}L</div></div>
            <div class="stat"><div class="k">Max drawdown</div>
              <div class="v">${fmt(f.max_drawdown_pct, 1)}%</div>
              <div class="small muted">on the fixed-stake bankroll</div></div>
            <div class="stat"><div class="k">Compounded</div>
              <div class="v">${fmtMoney(d.compound.final_usd)}</div>
              <div class="small muted">rolling the full bankroll</div></div>
          </div>
          ${curveSvg}
          <p class="small muted" style="margin-top:var(--s2)">Hypothetical mirror of ${d.trades} real recorded agent trades${d.skipped ? ` (${d.skipped} skipped — no usable size)` : ''}. Past performance ≠ future results.</p>`;
      }, { empty: { icon: 'icon-bolt', text: 'No closed agent trades in this window yet — the replay lights up once the engine has history.' } });
    }
    document.getElementById('replayForm').onsubmit = (e) => { e.preventDefault(); runReplayPanel(); };
    runReplayPanel();

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
      let pct = null, pctTxt = '';
      if (isFinite(e0) && isFinite(x0) && e0 > 0) {
        pct = (String(dir).toUpperCase() === 'LONG' ? (x0 - e0) : (e0 - x0)) / e0 * 100;
        pctTxt = ` ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
      }
      let url = location.origin;
      try {
        const rr = await fetchJSON('/api/auth/referrals');
        if (rr.ok && rr.data?.code) url = `${location.origin}/?ref=${encodeURIComponent(rr.data.code)}`;
      } catch (_) { /* fall back to the bare origin */ }
      const text = `${dir} ${sym}${pctTxt} — traded with RUNECLAW, the autonomous AI trading agent.`;
      // Server-rendered card image (best-effort; the same percent-only data,
      // never a dollar amount). A missing image never blocks the share.
      let file = null;
      if (pct !== null) {
        try {
          const q = `symbol=${encodeURIComponent(String(sym).toUpperCase())}` +
            `&direction=${encodeURIComponent(String(dir).toUpperCase())}` +
            `&pnl_pct=${encodeURIComponent(pct.toFixed(2))}`;
          const resp = await fetch(`/api/share/card?${q}`, { headers: RC.authHeaders() });
          if (resp.ok && /image\/png/.test(resp.headers.get('content-type') || '')) {
            const blob = await resp.blob();
            file = new File([blob], `runeclaw-${sym}.png`, { type: 'image/png' });
          }
        } catch (_) { /* card is optional */ }
      }
      if (file && navigator.canShare && navigator.canShare({ files: [file] })) {
        try { await navigator.share({ files: [file], text, url }); return; } catch (_) { /* fall through */ }
      }
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
        <section class="panel" id="p-awallet"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-wallet"></use></svg>Wallet link <span class="right muted small">read-only balance mirror</span></h2><div id="c-awallet"><div class="skel"></div></div></section>
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
      // WEB-2: operator lock flow lives here too — step 1 picks a term,
      // step 2 is an inline FINAL CONFIRM that names the lock END date; the
      // gateway re-enforces both the admin role and the echoed end date.
      const ayLock = { sel: null };
      async function drawAYield() {
        await renderPanel(C('ayield'), async () => {
          const [r, lockR] = await Promise.all([
            fetchJSON('/api/reports/yield', { timeoutMs: 12000 }),
            fetchJSON('/api/staking/fixed', { timeoutMs: 30000 }).catch(() => null),
          ]);
          const y = r.data?.yield;
          if (!y || !(y.rows || []).length) return null;
          const lock = (lockR?.ok && lockR.data?.available) ? lockR.data : null;
          const lockRows = lock ? Object.fromEntries(lock.rows.map(rw => [rw.coin, rw])) : {};
          // SPOT-2: every lock term is shown with its duration — a lock is not
          // revocable until the term ends, so term vs rate must be an explicit
          // user choice, never a hidden "best fixed" number. For the OPERATOR
          // the chips become step-1 buttons of the double-confirm.
          const termChips = (row) => {
            const lr = lockRows[row.coin];
            if (lr && (lr.terms || []).length) {
              return lr.terms.map(t => `<button class="btn btn--sm" type="button"
                data-lock="${esc(JSON.stringify({ coin: lr.coin, product_id: t.product_id, days: t.days, apy: t.apy, lock_end: t.lock_end, stakeable: lr.stakeable_usd }))}"
                title="Locked until ${esc(t.lock_end)} — funds are NOT redeemable before then">🔒${esc(t.days)}d ${Number(t.apy).toFixed(2)}%</button>`).join(' ');
            }
            return (row.fixed_terms || []).slice(0, 6).map(t =>
              `<span class="badge" title="Locked for ${esc(t.days)} days — funds are NOT redeemable until the term ends">🔒${esc(t.days)}d ${Number(t.apy).toFixed(2)}%</span>`
            ).join(' ');
          };
          const sel = ayLock.sel;
          const confirmBlock = sel ? `
            <div class="mt-3" id="ayLockConfirm" style="border:1px solid var(--down);border-radius:var(--radius-sm);padding:var(--s3)">
              <p><b>⚠️ FINAL CONFIRM — fixed-term lock</b></p>
              <p class="small">Lock ≈<b class="num">$${Number(sel.stakeable).toFixed(2)}</b> <b>${esc(sel.coin)}</b> @ <b class="num">${Number(sel.apy).toFixed(2)}%</b> for <b>${esc(sel.days)} days</b>.</p>
              <p class="small"><b>⛔ NOT redeemable until ${esc(sel.lock_end)} (UTC)</b> — the funds cannot be withdrawn, traded, or used as margin before that date. The exact amount is recomputed and reserve-clamped at execution.</p>
              <div class="row" style="gap:var(--s2);flex-wrap:wrap">
                <input class="input" id="ayLockTotp" inputmode="numeric" maxlength="8" placeholder="2FA code (if enrolled)" autocomplete="one-time-code" style="width:11rem">
                <button class="btn btn--primary btn--sm" id="ayLockYes" type="button">🔒 YES — lock until ${esc(sel.lock_end)}</button>
                <button class="btn btn--sm" id="ayLockNo" type="button">Cancel</button>
              </div>
            </div>` : '';
          const actNote = lock
            ? 'Pick a 🔒 term to lock (operator) — a FINAL confirm will name the exact lock end date.'
            : 'Use /stake in Telegram to act — locked terms require an explicit double-confirm showing the lock end date.';
          return `<p class="muted small">Idle assets vs Bitget Earn — flexible redeems instantly; 🔒 fixed terms LOCK funds until the term ends (not revocable).</p>
            <div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Coin</th><th class="r">Idle</th><th class="r">Stakeable</th><th class="r">Flex APY</th><th class="r">Est/yr</th></tr></thead>
            <tbody>${y.rows.slice(0, 10).map(row => `<tr><td><b>${esc(row.coin)}</b>${row.alt_note ? ` <span class="muted small">${esc(row.alt_note)}</span>` : ''}${(row.fixed_terms || []).length || lockRows[row.coin] ? `<br>${termChips(row)}` : ''}</td>
              <td class="num r">$${Number(row.idle_usd || 0).toFixed(2)}</td>
              <td class="num r">$${Number(row.stakeable_usd || 0).toFixed(2)}</td>
              <td class="num r">${row.apy_flexible != null ? Number(row.apy_flexible).toFixed(2) + '%' : '—'}</td>
              <td class="num r">$${Number(row.est_year_usd || 0).toFixed(2)}</td></tr>`).join('')}</tbody></table></div>
            ${confirmBlock}
            <p class="small muted mt-2">Total idle <b class="num">$${Number(y.total_idle_usd || 0).toFixed(2)}</b> · est. <b class="num">$${Number(y.total_est_year_usd || 0).toFixed(2)}/yr</b> at current flexible rates. ${actNote}</p>`;
        }, { empty: { icon: 'icon-coin', text: 'Yield data arrives with the bot\'s hourly report (needs operator Earn credentials).' } });

        const host = C('ayield');
        if (!host) return;
        host.querySelectorAll('[data-lock]').forEach(b => {
          b.onclick = () => {
            try { ayLock.sel = JSON.parse(b.getAttribute('data-lock')); } catch (e) { return; }
            drawAYield().then(() => {
              const c = document.getElementById('ayLockConfirm');
              if (c) c.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            });
          };
        });
        const no = document.getElementById('ayLockNo');
        if (no) no.onclick = () => { ayLock.sel = null; drawAYield(); };
        const yes = document.getElementById('ayLockYes');
        if (yes) yes.onclick = async () => {
          const sel = ayLock.sel;
          if (!sel) return;
          yes.disabled = true;
          const r = await fetchJSON('/api/staking/fixed', {
            method: 'POST', timeoutMs: 50000,
            body: { coin: sel.coin, product_id: sel.product_id, days: sel.days,
                    confirm_lock_end: sel.lock_end,
                    totp_code: (document.getElementById('ayLockTotp')?.value || '').trim() },
          }).catch(() => null);
          if (r?.status === 401) { toast('Enter your 2FA code to lock funds.'); yes.disabled = false; return; }
          if (r?.status === 409) { toast('The lock end date changed (midnight rollover) — re-showing live terms.'); ayLock.sel = null; drawAYield(); return; }
          toast(r?.ok ? ('✅ ' + (r.data?.detail || 'Locked.')) : ('🔴 ' + (r?.data?.detail || r?.data?.error || 'Lock failed — nothing moved.')));
          ayLock.sel = null;
          drawAYield();
        };
      }
      drawAYield();
    }

    // Wallet link: attach a browser wallet to THIS account so the read-only
    // multi-chain mirror (Portfolio, net worth, exposure) lights up. SIWE-style
    // proof: the wallet signs a login message — never a transaction.
    async function drawWalletLink() {
      renderPanel(C('awallet'), async () => {
        const r = await fetchJSON('/api/wallet/portfolio', { timeoutMs: 25000 }).catch(() => null);
        const d = (r?.ok && r.data) || {};
        // Solana rides alongside the EVM link as a WATCH address — honestly
        // read-only and unauthenticated (SIWE can't sign on Solana), it only
        // ever feeds public balance reads.
        const solShort = d.sol_address ? `${d.sol_address.slice(0, 4)}…${d.sol_address.slice(-4)}` : null;
        const solBlock = d.sol_address
          ? `<div class="mt-3" style="border-top:1px solid var(--line);padding-top:var(--s3)">
              <p class="small" style="color:var(--text-2)">◎ Solana watch address <b class="num">${esc(solShort)}</b> —
                SOL and major SPL balances mirror read-only into Portfolio.</p>
              <button class="btn btn--sm" id="solUnwatch" type="button">Stop watching</button></div>`
          : `<div class="mt-3" style="border-top:1px solid var(--line);padding-top:var(--s3)">
              <p class="small muted">Also on Solana? Paste an address to watch it — read-only, no signature or permissions involved.</p>
              <div class="row" style="gap:var(--s2);flex-wrap:wrap">
                <input class="input" id="solAddr" placeholder="Solana address (base58)" autocomplete="off" style="max-width:340px">
                <button class="btn btn--sm" id="solWatch" type="button">◎ Watch</button></div></div>`;
        if (d.linked && d.address) {
          const short = `${d.address.slice(0, 6)}…${d.address.slice(-4)}`;
          return `<p class="small" style="color:var(--text-2)">✅ Wallet <b class="num">${esc(short)}</b> is linked —
              its balances mirror into Portfolio, net worth and exposure across the tracked chains.
              RUNECLAW can read them, never move them.</p>
            <button class="btn btn--sm" id="walletUnlink" type="button">Unlink wallet</button>
            ${solBlock}`;
        }
        return `<p class="small" style="color:var(--text-2)">Link a browser wallet (MetaMask or compatible) to see your
            on-chain balances inside RUNECLAW — strictly read-only: the wallet signs one login message, never a transaction.</p>
          <div class="row" style="gap:var(--s2);flex-wrap:wrap">
            <button class="btn btn--primary btn--sm" id="walletLink" type="button">🔗 Link wallet</button>
            <button class="btn btn--sm" id="walletQr" type="button">📱 Link with phone</button>
          </div>
          <div id="walletQrBox" class="mt-3" hidden></div>
          ${!window.ethereum ? '<p class="small muted mt-2">No browser wallet here? Use <b>Link with phone</b> — scan the QR with your phone and sign in your wallet app.</p>' : ''}
          ${solBlock}`;
      }, { empty: { text: 'Wallet status unavailable.' } });
    }
    // Phone linking: show the single-use QR and poll until the phone signs.
    let qrPollTimer = null;
    async function showWalletQr() {
      const box = document.getElementById('walletQrBox');
      if (!box) return;
      const r = await fetchJSON('/api/auth/wallet/link-code', { method: 'POST', body: {} }).catch(() => null);
      if (!r?.ok || !r.data?.url) {
        toast(r?.data?.error || 'Could not create a phone-link code.');
        return;
      }
      box.hidden = false;
      box.innerHTML = `${r.data.svg || ''}
        <p class="small muted mt-2" style="max-width:46ch">Scan with your phone and open the link
          <b>inside your wallet app's browser</b> (MetaMask → Browser, Trust → Discover).
          One signature links the wallet — this code works once and expires in ${Math.round((r.data.expires_in_sec || 600) / 60)} minutes.</p>`;
      // Poll for the phone-side link completing (bounded: ~2 min).
      if (qrPollTimer) clearInterval(qrPollTimer);
      let polls = 0;
      qrPollTimer = setInterval(async () => {
        if (++polls > 24) { clearInterval(qrPollTimer); return; }
        const me = await fetchJSON('/api/auth/me').catch(() => null);
        if (me?.ok && me.data?.wallet_address) {
          clearInterval(qrPollTimer);
          toast('Wallet linked from your phone.');
          drawWalletLink();
        }
      }, 5000);
      if (qrPollTimer.unref) qrPollTimer.unref();
    }
    C('awallet').addEventListener('click', async (e) => {
      if (e.target.closest('#walletQr')) { showWalletQr(); return; }
      if (e.target.closest('#solWatch')) {
        const addr = (document.getElementById('solAddr')?.value || '').trim();
        if (!addr) { toast('Paste a Solana address first.'); return; }
        const v = await fetchJSON('/api/auth/wallet/solana', { method: 'POST', body: { address: addr } }).catch(() => null);
        toast(v?.ok ? 'Solana address watched — balances mirror read-only.' : (v?.data?.error || 'Could not watch that address.'));
        drawWalletLink(); return;
      }
      if (e.target.closest('#solUnwatch')) {
        await fetchJSON('/api/auth/wallet/solana/unlink', { method: 'POST', body: {} }).catch(() => {});
        toast('Solana watch removed.');
        drawWalletLink(); return;
      }
      const link = e.target.closest('#walletLink'), unlink = e.target.closest('#walletUnlink');
      if (!link && !unlink) return;
      try {
        if (link) {
          const eth = window.RCWalletPicker ? await RCWalletPicker.pick() : window.ethereum;
          if (!eth) { toast('No browser wallet detected — install MetaMask, or use Link with phone.'); return; }
          const accounts = await eth.request({ method: 'eth_requestAccounts' });
          const address = (accounts && accounts[0] || '').trim();
          if (!address) { toast('No wallet account was shared.'); return; }
          const n = await fetchJSON('/api/auth/wallet/nonce', { method: 'POST', body: { address } });
          if (!n?.ok || !n.data?.message) { toast(n?.data?.error || 'Could not start wallet linking.'); return; }
          const signature = await eth.request({ method: 'personal_sign', params: [n.data.message, address] });
          const v = await fetchJSON('/api/auth/wallet/link', { method: 'POST', body: { address, signature } });
          toast(v?.ok ? 'Wallet linked — your on-chain balances now mirror into the dashboard.'
            : (v?.data?.error || 'Wallet link failed.'));
        } else {
          const v = await fetchJSON('/api/auth/wallet/unlink', { method: 'POST', body: {} });
          toast(v?.ok ? 'Wallet unlinked.' : 'Could not unlink the wallet.');
        }
      } catch (err) {
        toast('Wallet linking was cancelled.');
      }
      drawWalletLink();
    });
    drawWalletLink();

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
          // Follow-the-agent topics are per-account prefs (opt-in, default off).
          const prof = await fetchJSON('/api/profile').catch(() => null);
          const boardOn = !!prof?.data?.prefs?.push_board;
          return `<p class="small" style="color:var(--text-2)">✅ This device gets a notification when the agent opens or closes a trade, or raises a warning.</p>
            <label class="small" style="display:flex;gap:8px;align-items:center;margin:8px 0;color:var(--text-2)">
              <input type="checkbox" id="pushBoard" ${boardOn ? 'checked' : ''}>
              Also follow the <a href="/leaderboard">verifiable board</a> — rank moves, entries, exits (handles only, never amounts)
            </label>
            <button class="btn btn--sm" id="pushOff" type="button">Turn off on this device</button>`;
        }
        return `<p class="small" style="color:var(--text-2)">Get a notification the moment the agent opens or closes a trade, or raises a warning — even with the tab closed.</p>
          <button class="btn btn--primary btn--sm" id="pushOn" type="button">Enable on this device</button>
          ${Notification.permission === 'denied' ? '<p class="small muted mt-2">Notifications are blocked in your browser settings for this site — unblock them first.</p>' : ''}`;
      }, { empty: { text: 'Push status unavailable.' } });
    }
    C('apush').addEventListener('change', async (e) => {
      const cb = e.target.closest('#pushBoard');
      if (!cb) return;
      const r = await fetchJSON('/api/profile', {
        method: 'PUT', body: { prefs: { push_board: cb.checked } },
      }).catch(() => ({ ok: false }));
      toast(r.ok
        ? (cb.checked ? 'Following the board — rank moves will reach this account.'
                      : 'Board notifications off.')
        : 'Could not save the preference — try again.', r.ok ? 'up' : 'down');
    });
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
    const fieldsHtml = (venue) => (venue?.fields || []).map(f =>
      `<div class="field"><label for="cf-${esc(venue.id)}-${esc(f.key)}">${esc(f.label)}</label>
        <input class="input" id="cf-${esc(venue.id)}-${esc(f.key)}" data-fkey="${esc(f.key)}" type="${f.type === 'password' ? 'password' : 'text'}" autocomplete="off"></div>`
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
      if (!venuesCatalog.length) return null;
      // Multi-venue: EVERY exchange side by side with its own field form,
      // status, and independent disconnect — connecting one never touches
      // another (the bot's store merges per venue).
      const statusOf = (id) => (c.venues || []).find(v => v.venue === id);
      const pendingFor = c.pending ? c.pending_venue : null;
      const cards = venuesCatalog.map(v => {
        const st = statusOf(v.id);
        const connected = !!(st && st.connected);
        const pending = pendingFor === v.id ? c.pending : null;
        const chip = connected ? '<span class="chip chip--up">✓ connected</span>'
          : pending ? `<span class="chip chip--warn">applying ${esc(pending)}…</span>`
          : '<span class="chip">not connected</span>';
        const disc = connected
          ? `<button class="btn btn--danger btn--sm" data-discvenue="${esc(v.id)}" type="button">Disconnect</button>` : '';
        const form = connected ? '' : `
          <form class="credForm stack mt-2" data-venue="${esc(v.id)}">
            <p class="muted small">${esc(v.help || '')}</p>
            <div class="form-row">${fieldsHtml(v)}</div>
            <div class="row"><button class="btn btn--primary btn--sm" type="submit">Connect ${esc(v.label)}</button>
              <span class="small muted credMsg" aria-live="polite"></span></div>
          </form>`;
        return `<div style="border:1px solid var(--line);border-radius:var(--radius);padding:var(--s3) var(--s4);margin-bottom:var(--s3)">
          <div class="row" style="justify-content:space-between;align-items:center">
            <b>${esc(v.label)}</b><span class="row" style="gap:var(--s2)">${chip}${disc}</span></div>
          ${form}</div>`;
      }).join('');
      return cards
        + `<p class="muted small">Keys are AES-256-GCM encrypted at rest and pulled by the bot over an
           authenticated channel. Withdrawal permissions are never required. Connect as many exchanges
           as you like — each is independent, and smart per-pair venue routing builds on this next.</p>`;
    }, { empty: { text: 'Credential connect is unavailable right now.' } });
    // Each venue card has its own form (data-venue) and message span, so the
    // handlers are delegated by class, not id — every card works independently.
    container.addEventListener('submit', async (e) => {
      const f = e.target.closest('.credForm');
      if (!f) return;
      e.preventDefault();
      const msg = f.querySelector('.credMsg');
      const body = { venue: f.dataset.venue || 'bitget' };
      for (const inp of f.querySelectorAll('[data-fkey]')) body[inp.dataset.fkey] = inp.value.trim();
      if (msg) msg.textContent = 'Encrypting & queueing…';
      const r = await fetchJSON('/api/credentials', { method: 'POST', body }).catch(() => ({ ok: false }));
      if (msg) msg.textContent = r.ok ? 'Queued — the bot applies it within a minute.' : (r.data?.detail || r.data?.error || 'Failed.');
      if (r.ok) setTimeout(() => showView('account'), 1200);
    });
    container.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-discvenue]');
      if (!b) return;
      const venue = b.dataset.discvenue;
      if (!confirm(`Disconnect your ${venue} keys? Other exchanges stay connected.`)) return;
      await fetchJSON('/api/credentials?venue=' + encodeURIComponent(venue), { method: 'DELETE' }).catch(() => {});
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
        const r = await RC.postWithStepUp('/api/controls', body);
        msg.textContent = r.ok ? 'Queued — the bot applies it within a minute.' : (r.data?.detail || r.data?.error || 'Failed.');
      }
      if (e.target.id === 'ctlStop') {
        if (!confirm('Emergency stop: disable live, pause, and close your open positions. Continue?')) return;
        const r = await fetchJSON('/api/controls/stop', { method: 'POST' }).catch(() => ({ ok: false }));
        toast(r.ok ? 'Emergency stop queued — closing positions.' : 'Emergency stop failed.', r.ok ? 'warn' : 'down');
      }
    });
  }

  /* ═══════════════ LEADERBOARD ═══════════════ */
  // dApp connectors hub — a curated, READ-ONLY directory of reputable DeFi/NFT
  // dApps. Each card deep-links to the dApp's OWN official site where the user
  // connects their own wallet and signs their own tx; RUNECLAW is a launchpad,
  // it never routes or executes anything from here.
  async function renderDapps() {
    container.innerHTML = viewHead('dApps',
      'A curated launchpad of trusted DeFi & NFT apps — you connect and sign on the app\'s own site');
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-dappctl"><div id="c-dappctl"><div class="skel"></div></div></section>
        <section class="panel"><div id="c-dappgrid"><div class="skel"></div><div class="skel"></div></div></section>
        <p class="small muted" id="dappNote" style="max-width:82ch"></p>
      </div>`);

    let data = null, curCat = 'all', curChain = 'all';
    const load = async () => { const r = await fetchJSON('/api/dapps', { auth: false }); data = r.ok ? r.data : null; return data; };

    const matches = (d) => (curCat === 'all' || d.category === curCat) && (curChain === 'all' || d.chains.includes(curChain));

    function paintCtl() {
      const el = C('dappctl'); if (!el || !data) return;
      const chip = (active, val, label, kind) => `<button class="btn btn--sm ${active ? 'btn--primary' : 'btn--ghost'}" data-${kind}="${esc(val)}" type="button">${esc(label)}</button>`;
      const cats = ['all', ...(data.categories || [])];
      const chains = [{ key: 'all', label: 'All chains' }, ...((data.chains) || [])];
      el.innerHTML = `<div class="row" style="gap:6px;flex-wrap:wrap;align-items:center"><span class="small muted">Category</span>
          ${cats.map(c => chip(curCat === c, c, c === 'all' ? 'All' : c, 'dcat')).join('')}</div>
        <div class="row" style="gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px"><span class="small muted">Chain</span>
          ${chains.map(c => chip(curChain === c.key, c.key, c.label, 'dchain')).join('')}</div>`;
    }

    function paintGrid() {
      const el = C('dappgrid'); if (!el) return;
      if (!data) { el.innerHTML = `<p class="small muted">The dApp directory is unavailable right now.</p>`; return; }
      const list = (data.dapps || []).filter(matches);
      if (!list.length) { el.innerHTML = `<p class="small muted">No dApps match this filter.</p>`; return; }
      const chainBadge = (c) => `<span class="chip" style="font-size:10px;padding:1px 6px">${esc(c)}</span>`;
      el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:var(--s2)">${list.map(d => `
          <a class="dapp-card" href="${esc(d.url)}" target="_blank" rel="noopener" style="display:block;padding:var(--s2);border:1px solid rgba(128,128,128,.2);border-radius:var(--radius,12px);text-decoration:none;color:inherit">
            <div class="row" style="gap:8px;align-items:center"><span style="font-size:22px">${esc(d.emoji || '🔗')}</span><b>${esc(d.name)}</b><span class="chip chip--gold" style="font-size:10px;margin-left:auto">${esc(d.category)}</span></div>
            <div class="small muted" style="margin:6px 0;min-height:2.6em">${esc(d.blurb)}</div>
            <div class="row" style="gap:4px;flex-wrap:wrap">${(d.chains || []).slice(0, 6).map(chainBadge).join('')}</div>
            <div class="small" style="margin-top:8px;color:var(--gold-bright)">Open →</div>
          </a>`).join('')}</div>`;
    }

    await load();
    paintCtl(); paintGrid();
    const note = document.getElementById('dappNote');
    if (note && data) note.textContent = data.note || '';

    container.addEventListener('click', (e) => {
      const cb = e.target.closest && e.target.closest('[data-dcat]');
      const hb = e.target.closest && e.target.closest('[data-dchain]');
      if (cb) { curCat = cb.getAttribute('data-dcat'); paintCtl(); paintGrid(); }
      else if (hb) { curChain = hb.getAttribute('data-dchain'); paintCtl(); paintGrid(); }
    });
  }

  // Web3 Worlds — the user's on-chain identity (ENS name + avatar) and their
  // NFTs split into metaverse "worlds" (LAND / names / wearables, each linking
  // into the official world) vs the rest of their collectibles. Read-only: it
  // mirrors what the linked wallet holds and links out; it never mints/moves.
  async function renderWorlds() {
    container.innerHTML = viewHead('Worlds',
      'Your on-chain identity, NFTs & metaverse worlds — read-only, links out to the world');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend',
        `<section class="panel">${loginGate('Log in and link a wallet to see your web3 identity and metaverse worlds.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-w3id"><div id="c-w3id"><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-check"></use></svg>On-chain badges</h2><div id="c-w3badges"><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>Metaverse worlds</h2><div id="c-w3worlds"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Your collectibles</h2><div id="c-w3gallery"><div class="skel"></div><div class="skel"></div></div></section>
      </div>`);

    const KIND_ICON = { land: '🗺️', name: '🏷️', wearable: '👕' };
    const nftCard = (it) => {
      const img = it.image_url
        ? `<img src="${esc(it.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none';this.parentElement.classList.add('nft-noimg')" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:var(--radius,10px);background:rgba(128,128,128,.12)">`
        : `<div style="width:100%;aspect-ratio:1;border-radius:var(--radius,10px);background:rgba(128,128,128,.12);display:flex;align-items:center;justify-content:center;font-size:26px">🖼️</div>`;
      const label = esc(it.name || it.collection || 'Untitled');
      return `<div class="nft-card" style="min-width:0">${img}<div class="small" style="margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${label}">${label}</div></div>`;
    };
    const grid = (items) => `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:var(--s2)">${items.map(nftCard).join('')}</div>`;

    // Identity.
    (async () => {
      const el = C('w3id'); if (!el) return;
      const r = await fetchJSON('/api/web3/identity');
      const d = r.ok ? r.data : null;
      if (!d) { el.innerHTML = `<p class="small muted">Identity is unavailable right now.</p>`; return; }
      if (!d.linked) {
        el.innerHTML = `<div class="row" style="align-items:center;gap:var(--s2);flex-wrap:wrap">
            <div style="font-size:34px">🪪</div>
            <div><b>No wallet linked yet.</b><div class="small muted">Link a wallet in Portfolio to show your ENS name, avatar and NFTs here.</div></div>
            <a class="btn btn--primary btn--sm" href="#portfolio" style="margin-left:auto">Link a wallet</a>
          </div>`;
        return;
      }
      const avatar = d.avatar
        ? `<img src="${esc(d.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(document.createTextNode('🧑‍🚀'))" style="width:56px;height:56px;border-radius:50%;object-fit:cover;background:rgba(128,128,128,.15)">`
        : `<div style="width:56px;height:56px;border-radius:50%;background:var(--gold-dim,rgba(200,160,60,.2));display:flex;align-items:center;justify-content:center;font-size:26px">🧑‍🚀</div>`;
      const title = d.ens ? esc(d.ens) : esc(d.short || '');
      const sub = d.ens ? esc(d.short || '') : (d.resolved ? 'No ENS name set' : 'ENS lookup unavailable');
      el.innerHTML = `<div class="row" style="align-items:center;gap:var(--s3)">
          ${avatar}
          <div style="min-width:0"><div style="font-size:var(--fs-lg)"><b>${title}</b>${d.ens ? ' <span class="chip chip--gold">ENS</span>' : ''}</div>
            <div class="small muted num">${sub}</div></div>
          <a class="btn btn--ghost btn--sm" href="#portfolio" style="margin-left:auto">Wallet</a>
        </div>`;
    })();

    // On-chain badges (wallet-native reputation).
    (async () => {
      const el = C('w3badges'); if (!el) return;
      const r = await fetchJSON('/api/web3/profile');
      const d = r.ok ? r.data : null;
      if (!d) { el.innerHTML = `<p class="small muted">Badges are unavailable right now.</p>`; return; }
      if (!d.linked) { el.innerHTML = `<p class="small muted">Link a wallet to start earning on-chain badges.</p>`; return; }
      const badge = (b) => `<div title="${esc(b.detail || '')}" style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid rgba(128,128,128,.2);border-radius:10px;opacity:${b.earned ? '1' : '.45'}">
          <span style="font-size:20px;filter:${b.earned ? 'none' : 'grayscale(1)'}">${esc(b.emoji)}</span>
          <div style="min-width:0"><div class="small"><b>${esc(b.label)}</b>${b.earned ? ' <span class="chip chip--ok" style="font-size:9px">earned</span>' : ' <span class="chip" style="font-size:9px">locked</span>'}</div>
            <div class="small muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:26ch">${esc(b.detail || '')}</div></div>
        </div>`;
      el.innerHTML = `<div class="small muted mb-2">Earned <b>${d.earned}</b> of ${d.total} — each from what your wallet verifiably holds now.</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px">${(d.badges || []).map(badge).join('')}</div>`;
    })();

    // Collectibles → worlds + gallery.
    (async () => {
      const worldsEl = C('w3worlds'), galleryEl = C('w3gallery');
      const r = await fetchJSON('/api/web3/collectibles');
      const d = r.ok ? r.data : null;
      if (!d) { if (worldsEl) worldsEl.innerHTML = `<p class="small muted">Collectibles are unavailable right now.</p>`; if (galleryEl) galleryEl.innerHTML = ''; return; }
      if (!d.linked) {
        if (worldsEl) worldsEl.innerHTML = `<p class="small muted">Link a wallet to see your metaverse worlds.</p>`;
        if (galleryEl) galleryEl.innerHTML = `<p class="small muted">No wallet linked.</p>`;
        return;
      }
      if (!d.available) {
        const msg = d.reason === 'not_configured'
          ? 'The NFT mirror needs an operator OpenSea key (OPENSEA_API_KEY). Once set, your worlds and collectibles appear here.'
          : 'Couldn\'t read your NFTs right now — try again shortly.';
        if (worldsEl) worldsEl.innerHTML = `<p class="small muted">${esc(msg)}</p>`;
        if (galleryEl) galleryEl.innerHTML = '';
        return;
      }
      // Worlds.
      if (worldsEl) {
        if (!d.world_count) {
          worldsEl.innerHTML = `<p class="small muted">No metaverse LAND, names or wearables found in this wallet yet. Holdings from The Sandbox, Decentraland, Otherside, Voxels and Somnium Space show up here.</p>`;
        } else {
          worldsEl.innerHTML = `<div class="row" style="gap:var(--s2);flex-wrap:wrap;margin-bottom:var(--s2)">${d.summary.map(w => `
              <a class="btn btn--ghost btn--sm" href="${esc(w.url)}" target="_blank" rel="noopener">${esc(w.world)} · ${w.count} ${w.count === 1 ? 'item' : 'items'} · Enter →</a>`).join('')}</div>
            ${grid(d.worlds.map(it => ({ ...it, name: `${KIND_ICON[it.kind] || ''} ${it.name || it.kind_label}` })))}`;
        }
      }
      // Gallery (non-world collectibles).
      if (galleryEl) {
        const other = d.other || [];
        if (!other.length) galleryEl.innerHTML = `<p class="small muted">No other collectibles in this wallet.</p>`;
        else galleryEl.innerHTML = grid(other) + (d.count > (d.worlds.length + other.length) ? `<p class="small muted mt-2">Showing the first ${d.worlds.length + other.length} items.</p>` : '');
      }
    })();
  }

  // Solver & Counterparty Monitor — where the agent's real funds sit. Turns the
  // per-venue / per-chain holdings into a concentration read (custodial vs
  // self-custody, venue/chain HHI, largest counterparty, settlement issuer).
  // Advisory (flags, never a verdict); private per-user surface so real totals
  // show, consistent with the Holdings view.
  async function renderCounterparty() {
    container.innerHTML = viewHead('Counterparty',
      'Where your real funds sit — custody & venue concentration. Advisory, not a verdict');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend',
        `<section class="panel">${loginGate('Log in to see where your funds sit across custodians and chains.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-cphead"><div id="c-cphead"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Flags</h2><div id="c-cpflags"><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>Where your funds sit</h2><div id="c-cpbuckets"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Settlement issuer</h2><div id="c-cpissuers"><div class="skel"></div></div></section>
        <p class="small muted" id="cpNote" style="max-width:82ch"></p>
      </div>`);

    const SEV = { good: '✅', info: 'ℹ️', warn: '⚠️', bad: '⛔' };
    const LVL = { low: { t: 'Low', c: 'var(--up)' }, moderate: { t: 'Moderate', c: 'var(--gold-bright)' }, high: { t: 'High', c: 'var(--down)' }, none: { t: '—', c: 'var(--text-3)' } };
    const kindChip = (k) => k === 'self_custody' ? '<span class="chip chip--ok">🔑 self-custody</span>' : '<span class="chip">🏦 custodial</span>';

    const r = await fetchJSON('/api/counterparty');
    const data = r.ok ? r.data : null;

    const headEl = C('cphead');
    if (headEl) {
      if (!data) headEl.innerHTML = `<p class="small muted">The counterparty monitor is unavailable right now.</p>`;
      else if (data.unrated) headEl.innerHTML = `<p class="small muted">No real balances to assess yet — connect a venue or link a wallet, then your custody and venue concentration show here.</p>`;
      else {
        const lvl = LVL[data.concentration] || LVL.none;
        headEl.innerHTML = `<div class="row" style="align-items:center;gap:var(--s3);flex-wrap:wrap">
            <div><div class="small muted">Concentration</div><div style="font-size:var(--fs-lg);color:${lvl.c}"><b>${lvl.t}</b></div></div>
            <div><div class="small muted">Custodial vs self-custody</div><div><b>${data.custodial_pct}%</b> 🏦 · <b>${data.self_custody_pct}%</b> 🔑</div></div>
            <div><div class="small muted">Largest counterparty</div><div>${data.largest ? `<b>${esc(data.largest.label)}</b> ${data.largest.pct}%` : '—'}</div></div>
            <div><div class="small muted">Spread across</div><div class="num">${data.venue_count} venue${data.venue_count === 1 ? '' : 's'} · ${data.chain_count} chain${data.chain_count === 1 ? '' : 's'}</div></div>
          </div>
          <div class="small muted mt-2">Concentration index (HHI): <b class="num">${data.hhi}</b> / 10000 — lower is more diversified.${data.partial ? ' <span style="color:var(--gold-bright)">Partial: some venues unread.</span>' : ''}</div>`;
      }
    }

    const flagsEl = C('cpflags');
    if (flagsEl) {
      if (!data || data.unrated) flagsEl.innerHTML = `<p class="small muted">No flags yet.</p>`;
      else flagsEl.innerHTML = (data.flags || []).map(f => `<div class="small" style="margin:3px 0">${SEV[f.severity] || 'ℹ️'} ${esc(f.label)}</div>`).join('');
    }

    const bucketsEl = C('cpbuckets');
    if (bucketsEl) {
      const bk = (data && data.buckets) || [];
      if (!bk.length) bucketsEl.innerHTML = `<p class="small muted">No funds to break down.</p>`;
      else bucketsEl.innerHTML = `<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Counterparty</th><th>Type</th><th class="r">Value</th><th class="r">Share</th></tr></thead>
          <tbody>${bk.map(b => `<tr>
            <td><b>${esc(b.label)}</b></td>
            <td>${kindChip(b.kind)}</td>
            <td class="r num muted">${fmtMoney(b.usd, 0)}</td>
            <td class="r num">${b.pct}%</td>
          </tr>`).join('')}</tbody></table></div>`;
    }

    const issEl = C('cpissuers');
    if (issEl) {
      const iss = (data && data.issuers) || [];
      if (!iss.length) issEl.innerHTML = `<p class="small muted">No custodial stablecoin balances to break down.</p>`;
      else issEl.innerHTML = `<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Issuer</th><th class="r">Custodial value</th><th class="r">Share of custodial</th></tr></thead>
          <tbody>${iss.map(i => `<tr><td><b>${esc(i.issuer)}</b></td><td class="r num muted">${fmtMoney(i.usd, 0)}</td><td class="r num">${i.pct_of_custodial}%</td></tr>`).join('')}</tbody></table></div>
          <p class="small muted mt-2">Inferred from each venue's settlement coin. Wallet-held stablecoins are not yet issuer-split.</p>`;
    }

    const note = document.getElementById('cpNote');
    if (note && data) note.textContent = data.note || '';
  }

  // Outcome-Based Agent Reputation — a verifiable, confidence-adjusted score
  // computed only from the user's realized closed trades. Advisory (a heuristic
  // readout, never a verdict) and dollar-free (all ratios), so it reads honestly
  // without a fabricated starting balance and is shareable without amounts.
  async function renderReputation() {
    container.innerHTML = viewHead('Reputation',
      'Outcome-based agent score from your realized trades — advisory, not a verdict');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend',
        `<section class="panel">${loginGate('Log in to see your agent\'s outcome-based reputation.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-repscore"><div id="c-repscore"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>How it's scored</h2><div id="c-repsub"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Flags &amp; metrics</h2><div id="c-repflags"><div class="skel"></div></div></section>
        <p class="small muted" id="repNote" style="max-width:82ch"></p>
      </div>`);

    const GRADE_COLOR = { A: 'var(--up)', B: 'var(--up)', C: 'var(--gold-bright)', D: 'var(--down)', E: 'var(--down)' };
    const SEV = { good: '✅', info: 'ℹ️', warn: '⚠️', bad: '⛔' };
    const bar = (label, v) => {
      const val = v == null ? 0 : v;
      const col = val >= 70 ? 'var(--up)' : val >= 45 ? 'var(--gold-bright)' : 'var(--down)';
      return `<div style="margin:8px 0">
          <div class="row" style="justify-content:space-between"><span class="small">${esc(label)}</span><span class="small num muted">${v == null ? '—' : Math.round(v)}</span></div>
          <div style="height:8px;border-radius:6px;background:rgba(128,128,128,.18);overflow:hidden"><div style="height:100%;width:${clamp01(val)}%;background:${col}"></div></div>
        </div>`;
    };
    function clamp01(v) { return Math.max(0, Math.min(100, v)); }

    let data = null;
    const r = await fetchJSON('/api/reputation');
    data = r.ok ? r.data : null;

    const scoreEl = C('repscore');
    if (scoreEl) {
      if (!data) { scoreEl.innerHTML = `<p class="small muted">Your reputation is unavailable right now.</p>`; }
      else if (data.unrated) {
        scoreEl.innerHTML = `<div class="row" style="align-items:center;gap:var(--s3)">
            <div style="font-size:44px;font-weight:800;color:var(--text-3)">—</div>
            <div><div style="font-size:var(--fs-lg)">Unrated</div><div class="small muted">Close a round-trip to start building a reputation. Scores need real, realized outcomes.</div></div>
          </div>`;
      } else {
        const col = GRADE_COLOR[data.grade] || 'var(--text-1)';
        scoreEl.innerHTML = `<div class="row" style="align-items:center;gap:var(--s3);flex-wrap:wrap">
            <div style="text-align:center;min-width:120px">
              <div style="font-size:52px;font-weight:800;line-height:1;color:${col}">${data.score}</div>
              <div class="small muted">out of 100</div>
            </div>
            <div>
              <div style="font-size:var(--fs-lg)">Grade <b style="color:${col}">${esc(data.grade)}</b></div>
              <div class="small muted">Confidence ${data.sample.confidence}% · ${data.sample.trades} realized trade${data.sample.trades === 1 ? '' : 's'}</div>
              <div class="small muted mt-1">Thin samples are pulled toward a neutral 50 — reputation is earned, not lucky.</div>
            </div>
          </div>`;
      }
    }

    const subEl = C('repsub');
    if (subEl) {
      if (!data || data.unrated) { subEl.innerHTML = `<p class="small muted">Sub-scores appear once you have realized trades.</p>`; }
      else {
        const s = data.subscores;
        subEl.innerHTML = bar('Performance (profit quality)', s.performance)
          + bar('Risk discipline (drawdown control)', s.risk_discipline)
          + bar('Cost efficiency (fee drag)', s.cost_efficiency)
          + bar('Consistency (positive months)', s.consistency);
      }
    }

    const flagsEl = C('repflags');
    if (flagsEl) {
      if (!data || data.unrated) { flagsEl.innerHTML = `<p class="small muted">No metrics yet.</p>`; }
      else {
        const m = data.metrics;
        const flagRows = (data.flags || []).map(f => `<div class="small" style="margin:3px 0">${SEV[f.severity] || 'ℹ️'} ${esc(f.label)}</div>`).join('');
        const pf = m.profit_factor == null ? '∞' : m.profit_factor;
        flagsEl.innerHTML = `${flagRows}
          <div class="tbl-wrap mt-3"><table class="tbl"><tbody>
            <tr><td class="muted">Win rate</td><td class="r num">${m.win_rate == null ? '—' : m.win_rate + '%'}</td></tr>
            <tr><td class="muted">Profit factor</td><td class="r num">${pf}</td></tr>
            <tr><td class="muted">Expectancy (return / trade)</td><td class="r num ${pnlClass(m.expectancy_r)}">${signed(Math.round((m.expectancy_r || 0) * 1000) / 10)}%</td></tr>
            <tr><td class="muted">Max drawdown</td><td class="r num">${m.max_drawdown_pct == null ? '—' : m.max_drawdown_pct + '%'}</td></tr>
            <tr><td class="muted">Fee drag</td><td class="r num">${m.fee_drag_pct == null ? '—' : m.fee_drag_pct + '%'}</td></tr>
            <tr><td class="muted">Positive months</td><td class="r num">${m.positive_months}/${m.total_months}</td></tr>
          </tbody></table></div>`;
      }
    }

    const note = document.getElementById('repNote');
    if (note && data) note.textContent = data.note || '';
  }

  // Tax & Compliance Agent — realized-gains report from the user's OWN closed
  // trades. Every RUNECLAW trade is a discrete round-trip, so each closed trade
  // is one self-contained disposal (realized gain/loss = booked pnl, hold =
  // opened→closed, short/long-term at 365d). This is a PRIVATE per-user surface,
  // so real dollar figures belong here (the §4 no-dollars rule is about the
  // public leaderboard/community surfaces, not a user's own tax document).
  // Informational only — never tax advice.
  async function renderTax() {
    container.innerHTML = viewHead('Tax',
      'Realized-gains report from your own closed trades — informational, not tax advice');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend',
        `<section class="panel">${loginGate('Log in to build a realized-gains report from your trade history.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-taxctl"><div id="c-taxctl"><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chart"></use></svg>Realized gains</h2><div id="c-taxsum"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-target"></use></svg>Disposals</h2><div id="c-taxrows"><div class="skel"></div><div class="skel"></div></div></section>
        <p class="small muted" id="taxDisc" style="max-width:82ch"></p>
      </div>`);

    let data = null, curYear = 'all';
    const money = (v) => fmtMoney(v);
    const gl = (v) => { v = Math.round((+v || 0) * 100) / 100; return `<span class="num ${pnlClass(v)}">${v > 0 ? '+' : ''}${fmtMoney(v)}</span>`; };
    const dt = (s) => (s ? esc(String(s).slice(0, 10)) : '—');
    const shortSym = (s) => esc(String(s || '').replace(':USDT', '').replace('/USDT', ''));

    const load = async () => {
      const q = curYear === 'all' ? '' : ('?year=' + encodeURIComponent(curYear));
      const r = await fetchJSON('/api/tax/report' + q);
      data = r.ok ? r.data : null;
      return data;
    };

    // Client-side CSV so the download carries the user's auth without a second
    // authenticated request; mirrors the server's /api/tax/export.csv columns.
    const CSV_COLS = ['Symbol', 'Direction', 'Date Acquired', 'Date Sold', 'Proceeds (USD)', 'Cost Basis (USD)', 'Fees (USD)', 'Gain/Loss (USD)', 'Holding Days', 'Term'];
    function downloadCsv() {
      const rows = (data && data.disposals) || [];
      if (!rows.length) return;
      const cell = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
      const lines = [CSV_COLS.join(',')].concat(rows.map(d => [d.symbol, d.direction, d.acquired || '', d.disposed || '', d.proceeds, d.cost_basis, d.fees, d.gain_loss, d.holding_days == null ? '' : d.holding_days, d.term].map(cell).join(',')));
      const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = `runeclaw-tax-${curYear}.csv`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    }

    function paintCtl() {
      const el = C('taxctl'); if (!el) return;
      const years = (data && data.available_years) || [];
      const has = data && data.disposals && data.disposals.length;
      const chip = (y, label) => `<button class="btn btn--sm ${String(curYear) === String(y) ? 'btn--primary' : 'btn--ghost'}" data-taxyear="${esc(String(y))}" type="button">${esc(label)}</button>`;
      el.innerHTML = `<div class="row" style="gap:var(--s2);flex-wrap:wrap;align-items:center">
          <span class="small muted">Tax year</span>
          ${chip('all', 'All')}
          ${years.map(y => chip(y, String(y))).join('')}
          <button class="btn btn--sm btn--ghost" id="taxCsv" type="button" style="margin-left:auto" ${has ? '' : 'disabled'}>⬇ Export CSV</button>
        </div>`;
    }

    function paintSummary() {
      const el = C('taxsum'); if (!el) return;
      if (!data) { el.innerHTML = `<p class="small muted">Your tax report is unavailable right now.</p>`; return; }
      const t = data.totals || {};
      if (!t.disposals) { el.innerHTML = `<p class="small muted">No closed trades ${curYear === 'all' ? 'yet' : 'in ' + esc(curYear)} — realized-gain rows appear here once you close a round-trip.</p>`; return; }
      el.innerHTML = `
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:var(--s2)">
          <div><div class="small muted">Net realized</div><div style="font-size:var(--fs-lg)">${gl(t.net_gain_loss)}</div></div>
          <div><div class="small muted">Short-term</div><div>${gl(t.short_term_gain_loss)}</div></div>
          <div><div class="small muted">Long-term</div><div>${gl(t.long_term_gain_loss)}</div></div>
          <div><div class="small muted">Fees</div><div class="num">${money(t.fees)}</div></div>
          <div><div class="small muted">Proceeds</div><div class="num">${money(t.proceeds)}</div></div>
          <div><div class="small muted">Disposals</div><div class="num">${t.disposals} <span class="muted small">(${t.gains}W/${t.losses}L)</span></div></div>
        </div>
        ${(data.years && data.years.length > 1 && curYear === 'all') ? `<div class="tbl-wrap mt-3"><table class="tbl"><thead><tr><th>Year</th><th class="r">Net</th><th class="r">Short</th><th class="r">Long</th><th class="r">Fees</th><th class="r">Disposals</th></tr></thead><tbody>${data.years.map(y => `<tr><td class="num">${y.year}</td><td class="r">${gl(y.net_gain_loss)}</td><td class="r">${gl(y.short_term_gain_loss)}</td><td class="r">${gl(y.long_term_gain_loss)}</td><td class="r num muted">${money(y.fees)}</td><td class="r num muted">${y.disposals}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
    }

    function paintRows() {
      const el = C('taxrows'); if (!el) return;
      const rows = (data && data.disposals) || [];
      if (!rows.length) { el.innerHTML = `<p class="small muted">No disposals to show for this period.</p>`; return; }
      el.innerHTML = `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Symbol</th><th>Acquired</th><th>Sold</th><th class="r">Days</th><th>Term</th><th class="r">Proceeds</th><th class="r">Basis</th><th class="r">Fees</th><th class="r">Gain/Loss</th></tr></thead>
        <tbody>${rows.slice(0, 500).map(d => `<tr>
          <td><b>${shortSym(d.symbol)}</b> <span class="muted small">${esc(d.direction)}</span></td>
          <td class="small muted">${dt(d.acquired)}</td>
          <td class="small muted">${dt(d.disposed)}</td>
          <td class="r num muted">${d.holding_days == null ? '—' : d.holding_days}</td>
          <td><span class="chip">${d.term === 'long' ? 'Long' : d.term === 'short' ? 'Short' : '—'}</span></td>
          <td class="r num muted">${money(d.proceeds)}</td>
          <td class="r num muted">${money(d.cost_basis)}</td>
          <td class="r num muted">${money(d.fees)}</td>
          <td class="r">${gl(d.gain_loss)}</td>
        </tr>`).join('')}</tbody></table></div>
        ${rows.length > 500 ? `<p class="small muted mt-2">Showing the most recent 500 of ${rows.length} disposals — export CSV for the full set.</p>` : ''}`;
    }

    async function refresh() {
      await load();
      paintCtl(); paintSummary(); paintRows();
      const disc = document.getElementById('taxDisc');
      if (disc) disc.textContent = (data && data.disclaimer) || '';
    }

    container.addEventListener('click', (e) => {
      const yb = e.target.closest && e.target.closest('[data-taxyear]');
      if (yb) { curYear = yb.getAttribute('data-taxyear'); refresh(); return; }
      if (e.target.closest && e.target.closest('#taxCsv')) downloadCsv();
    });

    await refresh();
  }

  async function renderNews() {
    container.innerHTML = viewHead('News radar',
      'Breaking headlines + high-impact alerts on your positions — advisory only, never trades');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend',
        `<section class="panel">${loginGate('Log in to see the news radar and alerts on your positions.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack">
        <section class="panel" id="p-newsdd"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>On your positions</h2><div id="c-newsdd"><div class="skel"></div></div></section>
        <section class="panel" id="p-newsfeed"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Latest headlines</h2><div id="c-newsfeed"><div class="skel"></div><div class="skel"></div></div></section>
      </div>`);

    let data = null;
    const load = async () => { const r = await fetchJSON('/api/news'); data = r.ok ? r.data : null; return data; };
    const icon = (imp) => imp === 'high' ? '🔴' : imp === 'medium' ? '🟠' : '⚪';
    const ago = (s) => { s = +s || 0; return s < 90 ? Math.max(s, 1) + 's' : s < 5400 ? Math.floor(s / 60) + 'm' : s < 172800 ? Math.floor(s / 3600) + 'h' : Math.floor(s / 86400) + 'd'; };

    renderPanel(C('newsdd'), async () => {
      await load();
      if (!data) return null;
      if (!data.enabled) {
        return `<p class="small muted">The news radar is on by default (public crypto headlines, no API key) but an operator has turned it off here with <code>NEWS_RADAR_ENABLED=0</code>. When on, it gives high-impact alerts on positions you hold. <b>Advisory only</b> — news never moves or blocks a trade.</p>`;
      }
      const recs = data.standdown || [];
      if (!recs.length) return `<p class="small muted">No high-impact news on your open positions right now. ✓</p>`;
      return recs.slice(0, 6).map((r) => `
        <div class="news-alert">
          <div><span class="chip chip--down">🔴 ${esc(r.symbol)}</span> <b>${esc(r.headline)}</b></div>
          ${(r.reasons || []).length ? `<div class="small muted">${esc((r.reasons || []).slice(0, 3).join(', '))} · ${ago(r.age_sec)} ago</div>` : ''}
          <div class="small">${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">Read →</a> · ` : ''}<i>Advisory — review and decide; nothing was traded.</i></div>
        </div>`).join('');
    }, { empty: { text: 'The news radar is unavailable right now.' } });

    renderPanel(C('newsfeed'), async () => {
      if (!data) await load();
      if (!data || !data.enabled) return null;
      const items = data.recent || [];
      if (!items.length) return null;
      return items.map((it) => `
        <div class="news-item">
          <div>${icon(it.impact)} <b>${esc(it.title)}</b></div>
          <div class="small muted">${esc(it.source || '')}${(it.symbols || []).length ? ' · ' + esc((it.symbols || []).join('/')) : ''} · ${ago(it.age_sec)} ago${it.url ? ` · <a href="${esc(it.url)}" target="_blank" rel="noopener">open</a>` : ''}</div>
        </div>`).join('');
    }, { empty: { icon: 'icon-globe', text: 'No headlines yet — the radar fills on the next refresh.' } });
  }

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
        // Your REAL standing — even when you're past the top-50 window the
        // table below is capped to. Position-only, never a dollar figure (§4).
        const total = Number(data.ranked_total) || 0;
        const rankLine = data.my_rank
          ? `<p class="mt-2" style="font-size:var(--fs-lg)">You're <b class="num" style="color:var(--gold-bright)">#${data.my_rank}</b> <span class="muted">of ${total} ranked agent${total === 1 ? '' : 's'}</span></p>`
          : `<p class="small mt-2 muted">Close a trade to get ranked — your handle appears the moment you have a realized round-trip.</p>`;
        return `<p class="small" style="color:var(--text-2)">You're on the board as <span class="chip chip--gold">${esc(data.handle)}</span>. Only this handle and your % return show — never your email or balance.</p>
          ${rankLine}
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
    container.innerHTML = `
      <div class="view-head" style="display:flex;align-items:center;gap:var(--s4)">
        <div class="agent-avatar" data-rc-agent3d="avatar" aria-hidden="true"
             style="width:92px;height:92px;flex:none;border-radius:16px;overflow:hidden"></div>
        <div><h1>AI Analyst</h1><span class="sub">The same agent that runs the Telegram bot — portfolio-aware, trade-capable.</span></div>
      </div>
      <div id="chatInlineHost"></div>`;
    if (window.RCAgent3D) window.RCAgent3D.mountIfAvailable(container.querySelector('[data-rc-agent3d]'), { mode: 'avatar' });
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

  /* ═══════════════ AGENT HUB ═══════════════
     One console for every agent function: live status at a glance, one tap
     to act. Pure consolidation — every card reads the same APIs its full
     view uses and deep-links there; nothing here can trade or sign. */
  async function renderHub() {
    container.innerHTML = viewHead('Agent Hub', 'Everything your agent does — status at a glance, one tap to act');
    if (!LOGGED_IN) {
      container.insertAdjacentHTML('beforeend', `<section class="panel">${loginGate('Log in to open your agent console — tripwires, letters, replays, net worth, exposure and controls in one place.')}</section>`);
      return;
    }
    container.insertAdjacentHTML('beforeend', `
      <div class="stack" id="hubStack">
        <section class="panel panel--primary" style="display:flex;align-items:center;gap:var(--s4)">
          <div class="agent-avatar" data-rc-agent3d="avatar" aria-hidden="true"
               style="width:112px;height:112px;flex:none;border-radius:16px;overflow:hidden"></div>
          <div>
            <div class="eyebrow" style="font-family:var(--font-data);font-size:var(--fs-xs);letter-spacing:.16em;text-transform:uppercase;color:var(--up)">Your agent · live</div>
            <div style="font-family:var(--font-brand);font-size:var(--fs-lg);letter-spacing:.03em">RUNECLAW</div>
            <div class="muted small">Watching the markets — reacts to every scan, signal and fill.</div>
          </div>
        </section>
        <section class="panel panel--primary" id="p-hubstat"><div id="c-hubstat"><div class="skel"></div><div class="skel"></div></div></section>
        <section class="panel" id="p-hubask">
          <h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-chat"></use></svg>Ask in one tap
            <span class="right muted small">each opens the chat with the question ready</span></h2>
          <div class="row" style="gap:var(--s2);flex-wrap:wrap">
            ${[['📰 Market briefing', 'Give me a market briefing'],
               ['🧭 Macro backdrop', "how's the macro backdrop right now?"],
               ['✉️ This week’s letter', "this week's letter"],
               ['🌐 My net worth', 'my net worth'],
               ['🛡 My total exposure', "what's my total exposure?"],
               ['⚡ What-if $1k replay', "what if I'd taken every signal with $1k?"],
               ['🏛 RWA radar', 'rwa radar'],
               ['🪂 Airdrop radar', 'airdrop radar'],
               ['🐸 Meme radar', 'meme radar'],
               ['🖼 NFT radar', 'nft radar'],
               ['🪙 Spot market', 'spot market']]
              .map(([l, q]) => `<button class="btn btn--sm" data-ask="${esc(q)}" type="button">${l}</button>`).join('')}
          </div>
        </section>
        <div class="grid grid-2">
          <section class="panel" id="p-hubalerts"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-alert"></use></svg>Tripwires
            <span class="right"><a class="small" href="#feed">arm & manage →</a></span></h2>
            <p class="small" style="color:var(--text-2);margin-bottom:var(--s2)">One-shot price alerts → push. Or just tell the chat: <i>"alert me when BTC drops below $100k"</i>.</p>
            <div id="alertList"><div class="skel"></div></div></section>
          <section class="panel" id="p-hubletter"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-sparkle"></use></svg>The Agent Letter
            <span class="right"><a class="small" href="#home">read in Home →</a></span></h2>
            <div id="c-hubletter"><div class="skel"></div></div></section>
          <section class="panel" id="p-hubreplay"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-bolt"></use></svg>What-if replay
            <span class="right"><a class="small" href="#portfolio">full controls →</a></span></h2>
            <div id="c-hubreplay"><div class="skel"></div></div></section>
          <section class="panel" id="p-hubnw"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Net worth
            <span class="right"><a class="small" href="#portfolio">details →</a></span></h2>
            <div id="c-hubnw"><div class="skel"></div></div></section>
          <section class="panel" id="p-hubexp"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Exposure
            <span class="right"><a class="small" href="#portfolio">details →</a></span></h2>
            <div id="c-hubexp"><div class="skel"></div></div></section>
          <section class="panel" id="p-hubwatch"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg>Watchlist
            <span class="right"><a class="small" href="#markets">pin from Markets →</a></span></h2>
            <div id="c-hubwatch"><div class="skel"></div></div></section>
          <section class="panel" id="p-hublab"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-cog"></use></svg>Strategy Lab</h2>
            <div id="c-hublab"><div class="skel"></div></div></section>
          <section class="panel" id="p-hubresearch"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-globe"></use></svg>Research desk</h2>
            <div id="c-hubresearch"><div class="skel"></div></div></section>
          <section class="panel" id="p-hubtoggles"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-user"></use></svg>Voice & push</h2>
            <div id="c-hubtoggles"><div class="skel"></div></div></section>
          <section class="panel" id="p-hubmcp"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-link"></use></svg>Agent API (MCP)</h2>
            <div id="c-hubmcp"><div class="skel"></div></div></section>
          <section class="panel" id="p-hubllm"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-bolt"></use></svg>Your AI engine</h2>
            <div id="c-hubllm"><div class="skel"></div></div></section>
        </div>
      </div>`);

    // One tap → the chat, with the question ready. The hub itself never acts.
    // Delegated on the hub's own stack (recreated each render), so buttons
    // painted later by async panels are covered too.
    document.getElementById('hubStack').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-ask]');
      if (b && window.RCChat) window.RCChat.ask(b.dataset.ask);
    });
    if (window.RCAgent3D) window.RCAgent3D.mountIfAvailable(container.querySelector('#hubStack [data-rc-agent3d]'), { mode: 'avatar' });

    // ── Status strip: engine, stance, mode, positions, tripwires ──
    renderPanel(C('hubstat'), async () => {
      const [scan, pf, alertsR] = await Promise.all([
        getScan().catch(() => null),
        getPortfolio(true).catch(() => null),
        fetchJSON('/api/alerts', { timeoutMs: 8000 }).catch(() => null),
      ]);
      updateModeChip(pf);
      const at = scan?.received_at ? new Date(scan.received_at) : null;
      const fresh = at && (Date.now() - at.getTime()) < 3 * 3600 * 1000;
      const stance = String(scan?.circuit_breaker?.strategy_mode || '').toLowerCase();
      const STANCE = { defensive: '🛡 Defensive', balanced: '⚔️ Balanced',
                       aggressive: '🔥 Aggressive', manual: '🧘 Manual' }[stance];
      const live = pf && (pf.mode === 'LIVE' || pf.mode === 'MIXED');
      const armed = ((alertsR?.data?.alerts) || []).filter(a => a.active).length;
      const nOpen = (pf?.open_positions || []).length;
      const equity = (pf && pf.live_unavailable) ? 'unavailable'
        : (pf && pf.equity != null ? fmtMoney(pf.equity) : '—');
      const tile = (k, v, d) => `<div class="stat"><div class="k">${k}</div>
        <div class="v" style="font-size:var(--fs-lg)">${v}</div>${d ? `<div class="small muted">${d}</div>` : ''}</div>`;
      return `<div class="stat-row">
        ${tile('Engine', at ? (fresh ? '<span class="up">● LIVE</span>' : '<span class="chip chip--warn">STALE</span>') : '<span class="muted">OFFLINE</span>',
               at ? 'last scan ' + at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'no scan data')}
        ${tile('Stance', STANCE || '—', 'how the agent trades right now')}
        ${tile('Mode', pf ? (live ? 'LIVE' : 'PAPER') : '—', 'equity ' + equity)}
        ${tile('Open', String(nOpen), nOpen === 1 ? 'position carried' : 'positions carried')}
        ${tile('Tripwires', String(armed), armed === 1 ? 'alert armed' : 'alerts armed')}
      </div>`;
    }, { empty: { text: 'Status unavailable right now.' } });

    // Tripwires list — same element id the Feed view uses, so the shared
    // loader (list + delete handling) works unchanged here.
    loadAlertList();

    // ── Weekly letter: latest headline ──
    renderPanel(C('hubletter'), async () => {
      const r = await fetchJSON('/api/letter/latest');
      const L = r.data?.letter;
      if (!r.ok || !L) return null;
      return `<p class="muted small" style="margin-bottom:var(--s1)">${esc(L.period.start)} → ${esc(L.period.end)}</p>
        <p style="font-weight:600">${esc(L.headline)}</p>
        <button class="btn btn--sm mt-2" data-ask="this week's letter" type="button">✉️ Read it in chat</button>`;
    }, { empty: { icon: 'icon-sparkle', text: 'The first letter writes itself after the first full week of recorded activity.' } });

    // ── What-if replay: the standard $1k all-time mirror ──
    renderPanel(C('hubreplay'), async () => {
      const r = await fetchJSON('/api/replay?stake=1000&days=0');
      const d = r.data;
      if (!r.ok || !d || !d.trades) return null;
      const f = d.fixed;
      return `<p>Mirroring every closed agent trade at <b>$1,000</b>:
          <b class="num ${f.net_pnl_usd >= 0 ? 'up' : 'down'}">${fmtMoney(f.net_pnl_usd)}</b>
          over ${d.trades} trade${d.trades === 1 ? '' : 's'} · ${fmt(d.win_rate_pct, 0)}% wins</p>
        <p class="small muted mt-2">Hypothetical, from real recorded results. Past performance ≠ future results.</p>`;
    }, { empty: { icon: 'icon-bolt', text: 'No closed agent trades yet — the replay lights up once the engine has history.' } });

    // ── Net worth: the honest real total ──
    renderPanel(C('hubnw'), async () => {
      const r = await fetchJSON('/api/networth', { timeoutMs: 35000 });
      const d = r.data;
      if (!r.ok || !d || !d.sections) return null;
      const bits = [];
      const c = d.sections.cex;
      if (c && c.connected) bits.push(`🏦 ${esc((c.venue || 'CEX').toUpperCase())} ${c.ok && c.equity_usd != null ? '$' + fmt(c.equity_usd, 2) : '<span class="muted small">unreadable</span>'}`);
      const w = d.sections.wallet;
      if (w && w.linked) bits.push(`👛 wallet ${w.total_usd != null ? '$' + fmt(w.total_usd, 2) : '<span class="muted small">unreadable</span>'}`);
      return `<p class="small" style="color:var(--text-2)">${bits.length ? bits.join(' · ') : 'No exchange connected, no wallet linked yet.'}</p>
        <p style="margin-top:var(--s2)">Real total <b class="num" style="font-size:var(--fs-lg)">${d.total_real_usd != null ? '$' + fmt(d.total_real_usd, 2) : '—'}</b></p>
        <p class="small muted mt-2">Paper equity is listed in Portfolio but never counted as real.</p>`;
    }, { empty: { icon: 'icon-globe', text: 'Net worth aggregates once a venue or wallet is reachable.' } });

    // ── Exposure: net / gross + risk flags ──
    renderPanel(C('hubexp'), async () => {
      const r = await fetchJSON('/api/exposure', { timeoutMs: 20000 });
      const d = r.data;
      if (!r.ok || !d || !(d.assets || []).length) return null;
      const warn = (d.warnings || []).map(w2 =>
        `<p class="small" style="color:var(--down);margin-top:var(--s1)">⚠️ ${esc(w2)}</p>`).join('');
      return `<p>Net <b class="num">$${fmt(d.net_total_usd, 0)}</b>
          · Gross <b class="num">$${fmt(d.gross_total_usd, 0)}</b>
          · ${d.assets.length} asset${d.assets.length === 1 ? '' : 's'}</p>
        ${warn || '<p class="small muted mt-2">No risk flags — nothing stacked or concentrated.</p>'}`;
    }, { empty: { icon: 'icon-shield', text: 'Exposure appears once you have open positions or non-stable wallet holdings.' } });

    // ── Watchlist: view + quick add/remove (saved to your profile) ──
    async function drawHubWatchlist() {
      await renderPanel(C('hubwatch'), async () => {
        const prof = await getUserProfile(true);
        const wl = prof.watchlist || [];
        const chips2 = wl.map(s => `<span class="chip" style="gap:4px">${esc(String(s).replace('USDT', ''))}
            <button class="btn btn--ghost btn--sm" data-unpin="${esc(s)}" type="button" aria-label="Remove ${esc(s)}" style="padding:0 4px">✕</button></span>`).join(' ');
        return `${wl.length ? `<div class="row" style="gap:var(--s1);flex-wrap:wrap">${chips2}</div>`
            : '<p class="small muted">Nothing pinned yet — the agent watches these coins for you in chat.</p>'}
          <form class="row mt-3" id="hubWlForm" style="gap:var(--s2)">
            <input class="input" id="hubWlSym" placeholder="BTC" maxlength="12" style="width:7rem" aria-label="Symbol to watch">
            <button class="btn btn--sm" type="submit">＋ Watch</button>
          </form>`;
      }, { empty: { text: 'Watchlist unavailable.' } });
      const form = document.getElementById('hubWlForm');
      if (form) form.onsubmit = async (e) => {
        e.preventDefault();
        const raw = document.getElementById('hubWlSym').value.trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
        if (!raw) return;
        const sym = raw.endsWith('USDT') ? raw : raw + 'USDT';
        const prof = await getUserProfile(true);
        const wl = new Set(prof.watchlist || []);
        wl.add(sym);
        const ok = await saveUserProfile({ watchlist: [...wl] });
        toast(ok ? `Watching ${sym.replace('USDT', '')}.` : 'Could not save your watchlist — try again.');
        if (ok) drawHubWatchlist();
      };
      C('hubwatch').querySelectorAll('button[data-unpin]').forEach(b => {
        b.addEventListener('click', async () => {
          const prof = await getUserProfile(true);
          const wl = new Set(prof.watchlist || []);
          wl.delete(b.dataset.unpin);
          const ok = await saveUserProfile({ watchlist: [...wl] });
          toast(ok ? `Removed ${String(b.dataset.unpin).replace('USDT', '')}.` : 'Could not save your watchlist — try again.');
          if (ok) drawHubWatchlist();
        });
      });
    }
    drawHubWatchlist();

    // ── Strategy Lab shortcut ──
    renderPanel(C('hublab'), async () =>
      `<p class="small" style="color:var(--text-2)">Backtest the engine's strategies on frozen benchmark data — same code paths the live engine runs.</p>
       <div class="row mt-3" style="gap:var(--s2);flex-wrap:wrap">
         <a class="btn btn--primary btn--sm" href="#lab">🧪 Open the Lab</a>
         <button class="btn btn--sm" data-ask="backtest SOL" type="button">Backtest SOL in chat</button>
       </div>`);

    // ── Research desk: dossier on any listed coin ──
    (async () => {
      await renderPanel(C('hubresearch'), async () =>
        `<p class="small" style="color:var(--text-2)">An evidence dossier from live venue data and the agent's own recorded history — sources named, nothing invented.</p>
         <form class="row mt-3" id="hubResForm" style="gap:var(--s2)">
           <input class="input" id="hubResSym" placeholder="PENDLE" maxlength="12" style="width:9rem" aria-label="Coin to research">
           <button class="btn btn--primary btn--sm" type="submit">🔬 Research</button>
         </form>`);
      const form = document.getElementById('hubResForm');
      if (form) form.onsubmit = (e) => {
        e.preventDefault();
        const sym = document.getElementById('hubResSym').value.trim().replace(/[^a-zA-Z0-9$]/g, '');
        if (sym && window.RCChat) window.RCChat.ask('research ' + sym.toUpperCase());
      };
    })();

    // ── Voice & push toggles (honest per-device states) ──
    async function drawHubToggles() {
      await renderPanel(C('hubtoggles'), async () => {
        // Voice: same per-browser preference the chat's 🔊 button controls.
        let voice;
        if (!window.speechSynthesis) {
          voice = '<span class="muted small">not supported in this browser</span>';
        } else {
          const on = (() => { try { return localStorage.getItem('rc_tts') === '1'; } catch (e) { return false; } })();
          voice = `<button class="btn btn--sm ${on ? 'btn--primary' : ''}" id="hubTtsBtn" type="button" aria-pressed="${on}">${on ? '🔊 On — replies are spoken' : '🔇 Off'}</button>`;
        }
        // Push: real subscription state on THIS device.
        let push;
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
          push = '<span class="muted small">not supported in this browser</span>';
        } else {
          const k = await fetchJSON('/api/push/key').catch(() => null);
          if (!k?.ok || !k.data?.enabled) {
            push = '<span class="muted small">not configured on the server yet</span>';
          } else {
            const reg = await navigator.serviceWorker.ready.catch(() => null);
            const sub = reg ? await reg.pushManager.getSubscription().catch(() => null) : null;
            push = sub
              ? '<button class="btn btn--sm btn--primary" id="hubPushOff" type="button">🔔 On — turn off</button>'
              : '<button class="btn btn--sm" id="hubPushOn" type="button">🔕 Off — enable</button>';
          }
        }
        return `<div class="kv-row"><span>Spoken chat replies <span class="muted small">this browser</span></span><b>${voice}</b></div>
          <div class="kv-row"><span>Push notifications <span class="muted small">this device</span></span><b>${push}</b></div>
          <p class="small muted mt-2">Dictation lives on the chat's 🎤 button — it never auto-sends.</p>`;
      }, { empty: { text: 'Toggle states unavailable.' } });
      const tts = document.getElementById('hubTtsBtn');
      if (tts) tts.onclick = () => {
        // Route through the chat's own button so its in-memory state stays
        // in sync with the stored preference.
        const real = document.getElementById('chatTts');
        if (real) real.click();
        else { try { localStorage.setItem('rc_tts', localStorage.getItem('rc_tts') === '1' ? '0' : '1'); } catch (e) { /* fine */ } }
        drawHubToggles();
      };
      const on = document.getElementById('hubPushOn'), off = document.getElementById('hubPushOff');
      if (on) on.onclick = async () => {
        try {
          const perm = await Notification.requestPermission();
          if (perm !== 'granted') { toast('Notifications were not allowed.'); return; }
          const reg = await navigator.serviceWorker.ready;
          const k = await fetchJSON('/api/push/key');
          const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true, applicationServerKey: urlB64ToU8(k.data.public_key) });
          const r = await fetchJSON('/api/push/subscribe', { method: 'POST', body: { subscription: sub.toJSON() } });
          toast(r?.ok ? 'Push enabled — the agent can reach you here now.' : 'Could not save the subscription.');
        } catch (err) { toast('Push setup failed: ' + (err?.message || 'unknown error')); }
        drawHubToggles();
      };
      if (off) off.onclick = async () => {
        try {
          const reg = await navigator.serviceWorker.ready;
          const sub = await reg.pushManager.getSubscription();
          if (sub) {
            await fetchJSON('/api/push/unsubscribe', { method: 'POST', body: { endpoint: sub.endpoint } });
            await sub.unsubscribe();
          }
          toast('Push disabled on this device.');
        } catch (err) { toast('Push setup failed: ' + (err?.message || 'unknown error')); }
        drawHubToggles();
      };
    }
    drawHubToggles();

    // ── Agent API pointer: the read-only MCP endpoint ──
    renderPanel(C('hubmcp'), async () => {
      const ep = location.origin + '/mcp';
      return `<p class="small" style="color:var(--text-2)">Any MCP-capable agent can use RUNECLAW's public intelligence as tools — track record, signals, radar, replay. Read-only by design: no tool can touch an account or place a trade.</p>
        <div class="kv-row mt-2"><span>Endpoint</span><b class="num small">${esc(ep)}</b></div>
        <div class="row mt-2" style="gap:var(--s2);flex-wrap:wrap">
          <button class="btn btn--sm" id="hubMcpCopy" type="button">📋 Copy Claude Code command</button>
        </div>`;
    });
    setTimeout(() => {
      const b = document.getElementById('hubMcpCopy');
      if (b) b.onclick = async () => {
        const cmd = `claude mcp add --transport http runeclaw ${location.origin}/mcp`;
        try { await navigator.clipboard.writeText(cmd); toast('Copied — paste it into your terminal.'); }
        catch (e) { toast(cmd); }
      };
    }, 0);

    // ── Your AI engine: connect your OWN LLM key (WEB-1) ──
    // The key travels once to the bot's encrypted store — this page only
    // ever sees the fingerprint back. Admins also get the ULTRA toggle.
    async function drawHubLlm() {
      await renderPanel(C('hubllm'), async () => {
        const r = await fetchJSON('/api/llm', { timeoutMs: 12000 });
        if (!r?.ok) return '<p class="muted small">LLM connect is unavailable right now (bot gateway offline?).</p>';
        const d = r.data;
        const provs = d.providers || [];
        const opts = provs.map(p =>
          `<option value="${esc(p.id)}" ${p.id === (d.provider || 'gemini') ? 'selected' : ''}>${esc(p.id)}${p.free_tier ? ' · free tier' : ''}</option>`).join('');
        const status = d.connected
          ? `<div class="kv-row"><span>Connected</span><b>🟢 ${esc(d.provider)} <span class="num small muted">${esc(d.fingerprint)}</span></b></div>
             <p class="small muted mt-1">Your chat and analysis answers run on YOUR key first (your quota, your model choice).</p>`
          : `<p class="small" style="color:var(--text-2)">Plug your own AI into your agent: bring an API key from any supported provider and your chat runs on it — your quota, your model. Stored encrypted by the bot; this site never keeps it.</p>`;
        const enabledNote = d.per_user_enabled ? '' :
          '<p class="small muted mt-1">⚠️ Per-user keys are saved but not active yet — the operator has not enabled PER_USER_LLM.</p>';
        const ultra = d.is_admin ? `
          <div class="kv-row mt-2"><span>ULTRA routing <span class="muted small">operator only</span></span>
            <b><button class="btn btn--sm ${d.ultra ? 'btn--primary' : ''}" id="hubUltraBtn" type="button" aria-pressed="${!!d.ultra}">${d.ultra ? '🟣 ON — Fable 5 thesis/learning' : '⚪ OFF'}</button></b></div>
          <p class="small muted">ULTRA sends admin thesis/learning to claude-fable-5 ($10/$50 per MTok). Non-admin users are never routed to the operator key.</p>` : '';
        return `${status}
          <form class="row mt-2" id="hubLlmForm" style="gap:var(--s2);flex-wrap:wrap">
            <select class="input" id="hubLlmProv" aria-label="LLM provider" style="width:11rem">${opts}</select>
            <input class="input" id="hubLlmKey" type="password" placeholder="API key" maxlength="512" autocomplete="off" style="flex:1;min-width:12rem" aria-label="LLM API key">
            <button class="btn btn--primary btn--sm" type="submit">${d.connected ? '↻ Replace key' : '🔌 Connect'}</button>
            ${d.connected ? '<button class="btn btn--sm" id="hubLlmClear" type="button">Disconnect</button>' : ''}
          </form>
          ${enabledNote}${ultra}`;
      }, { empty: { text: 'LLM connect unavailable.' } });
      const form = document.getElementById('hubLlmForm');
      if (form) form.onsubmit = async (e) => {
        e.preventDefault();
        const provider = document.getElementById('hubLlmProv').value;
        const key = document.getElementById('hubLlmKey').value.trim();
        if (!key) { toast('Paste an API key first.'); return; }
        const r = await fetchJSON('/api/llm', { method: 'POST', body: { provider, api_key: key }, timeoutMs: 15000 });
        toast(r?.ok ? `Connected ${provider} — your agent answers on your key now.`
                    : (r?.data?.detail || 'Could not connect that key.'));
        drawHubLlm();
      };
      const clear = document.getElementById('hubLlmClear');
      if (clear) clear.onclick = async () => {
        const r = await fetchJSON('/api/llm/clear', { method: 'POST', body: {} });
        toast(r?.ok ? 'Disconnected — back to the built-in routing.' : 'Could not disconnect.');
        drawHubLlm();
      };
      const ub = document.getElementById('hubUltraBtn');
      if (ub) ub.onclick = async () => {
        const enable = ub.getAttribute('aria-pressed') !== 'true';
        const r = await fetchJSON('/api/llm/ultra', { method: 'POST', body: { enabled: enable }, timeoutMs: 15000 });
        toast(r?.ok ? (r.data?.detail || 'ULTRA updated.') : (r?.data?.detail || 'ULTRA toggle failed.'));
        drawHubLlm();
      };
    }
    drawHubLlm();
  }

  /* ═══════════════ MACRO AI ═══════════════ */
  function fmtBigUsd(n) {
    if (n == null || !isFinite(n)) return '—';
    if (n >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T';
    if (n >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B';
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
    return '$' + Math.round(n);
  }
  // Live time-to-event ("in 3d 4h" / "in 5h 20m" / "in 12m" / "now").
  function macroCountdown(iso) {
    const ms = new Date(iso).getTime() - Date.now();
    if (!isFinite(ms)) return '';
    if (ms <= 0) return 'now';
    const t = Math.floor(ms / 60000), d = Math.floor(t / 1440), h = Math.floor((t % 1440) / 60), mm = t % 60;
    return d > 0 ? `in ${d}d ${h}h` : h > 0 ? `in ${h}h ${mm}m` : `in ${mm}m`;
  }
  function updateMacroCountdowns() {
    document.querySelectorAll('[data-macro-countdown]').forEach((el) => {
      const t = macroCountdown(el.getAttribute('data-macro-countdown'));
      if (t) el.textContent = t;
    });
  }
  // Colored banner for elevated macro-event windows (quiet on NORMAL).
  function macroEventBanner(ev) {
    if (!ev || !ev.state || ev.state === 'NORMAL') return '';
    const info = {
      PRE_EVENT_CAUTION: { col: '#e0a63a', label: 'Pre-event caution', icon: '⏳' },
      EVENT_LOCKDOWN: { col: 'var(--down)', label: 'Event lockdown', icon: '🔒' },
      POST_EVENT_VOLATILITY: { col: '#e0a63a', label: 'Post-event volatility', icon: '🌊' },
      BLACKOUT: { col: 'var(--down)', label: 'Calendar blackout', icon: '⛔' },
    }[ev.state];
    if (!info) return '';
    const e = ev.active || ev.next;
    const when = e && e.scheduled_utc ? ` <span class="num" data-macro-countdown="${esc(e.scheduled_utc)}">${macroCountdown(e.scheduled_utc)}</span>` : '';
    const name = e ? esc(e.label) : 'a high-impact macro event';
    return `<div style="border:1px solid ${info.col};border-radius:var(--radius);background:rgba(224,166,58,.06);padding:var(--s2) var(--s3);margin-bottom:var(--s3)">
      <div style="font-family:var(--font-data);letter-spacing:.04em"><span>${info.icon}</span> <b style="color:${info.col}">${info.label}</b> · ${name}${when}</div>
      <div class="muted small">The engine tightens risk into high-impact macro prints.</div></div>`;
  }
  function macroBlock(m, at) {
    const band = m.band || { label: '—', tone: '' };
    const toneCol = band.tone === 'up' ? 'var(--up)' : band.tone === 'down' ? 'var(--down)' : 'var(--text-2)';
    const score = m.risk_score;
    const pos = score == null ? 50 : Math.max(0, Math.min(100, score));
    const gauge = `<div class="stat">
      <div class="k">Market posture</div>
      <div class="v big" style="font-size:var(--fs-xl);color:${toneCol}">${esc(band.label)}${score != null ? ` <span class="num" style="font-size:var(--fs-md);color:var(--text-2)">${score}/100</span>` : ''}</div>
      <div style="position:relative;height:10px;border-radius:6px;margin-top:10px;background:linear-gradient(90deg,var(--down) 0%,#e0a63a 50%,var(--up) 100%)">
        <div style="position:absolute;top:-4px;left:calc(${pos}% - 3px);width:6px;height:18px;border-radius:3px;background:var(--text);box-shadow:0 0 6px rgba(0,0,0,.6)"></div></div>
      <div class="row" style="justify-content:space-between;margin-top:4px"><span class="muted small">Risk-Off</span><span class="muted small">Risk-On</span></div></div>`;
    const fg = m.fear_greed;
    const fgCol = !fg ? 'var(--text-3)' : fg.value < 25 ? 'var(--down)' : fg.value < 45 ? '#e0a63a' : fg.value < 55 ? 'var(--text-2)' : 'var(--up)';
    const chg = m.market_cap_change_24h;
    const tile = (k, v, d, col) => `<div class="stat"><div class="k">${k}</div><div class="v" style="font-size:var(--fs-lg)${col ? `;color:${col}` : ''}">${v}</div>${d ? `<div class="small muted">${d}</div>` : ''}</div>`;
    const tiles = `<div class="stat-row">
      ${tile('Fear &amp; Greed', fg ? String(fg.value) : '—', fg ? esc(fg.classification) : 'unavailable', fgCol)}
      ${tile('Total market cap', fmtBigUsd(m.market_cap_usd), chg != null ? `${chg >= 0 ? '▲' : '▼'} ${Math.abs(chg).toFixed(1)}% 24h` : '', chg == null ? '' : chg >= 0 ? 'var(--up)' : 'var(--down)')}
      ${tile('BTC dominance', m.btc_dominance != null ? m.btc_dominance.toFixed(1) + '%' : '—', 'share of total cap')}
      ${tile('ETH dominance', m.eth_dominance != null ? m.eth_dominance.toFixed(1) + '%' : '—', 'share of total cap')}
      ${tile('Market structure', m.structure ? esc(m.structure) : '—', m.others_dominance != null ? `alts (others) ${m.others_dominance}%` : 'BTC vs ETH vs alts')}
      ${tile('24h volume', fmtBigUsd(m.volume_24h_usd), 'all crypto')}
      ${tile('Engine regime', m.regime && m.regime.label ? esc(m.regime.label) : '—', m.regime && m.regime.score != null ? `score ${Number(m.regime.score).toFixed(2)}` : 'BTC-derived')}
    </div>`;
    const briefText = m.ai_brief || m.brief;
    const briefLabel = m.ai_brief ? '🧭 RUNECLAW macro read' : '🧭 Agent macro read';
    const brief = briefText ? `<div class="mt-3" style="border:1px solid var(--line);border-left:3px solid ${toneCol};border-radius:var(--radius);padding:var(--s3) var(--s4);background:rgba(63,182,255,.04)">
      <div class="muted small" style="font-family:var(--font-data);letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px">${briefLabel}${m.ai_brief ? ' <span style="opacity:.6">· live LLM</span>' : ''}</div>
      <p style="margin:0;line-height:1.6">${esc(briefText)}</p></div>` : '';
    const foot = `<p class="muted small mt-2">Sources: Fear &amp; Greed (alternative.me) · market structure (CoinGecko) · engine BTC regime${at ? ' · ' + esc(new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })) : ''} · read-only, never trades.</p>`;
    // Always show the next high-impact event line when the calendar is synced.
    const nextLine = (m.event && m.event.next) ? `<div class="muted small mt-2">📅 Next high-impact event: <b>${esc(m.event.next.label)}</b> <span class="num" data-macro-countdown="${esc(m.event.next.scheduled_utc)}">${macroCountdown(m.event.next.scheduled_utc)}</span></div>` : '';
    return macroEventBanner(m.event) + gauge + nextLine + `<div class="mt-3">${tiles}</div>` + brief + foot;
  }
  // The agent physically reacts to the macro posture: alert on a defensive
  // backdrop, execute on a constructive one, analyze in the middle.
  function macroReact(band) {
    if (!window.RCAgent3D || !band) return;
    const clip = band.key === 'risk_off' || band.key === 'cautious' ? 'alert'
      : band.key === 'risk_on' || band.key === 'euphoric' ? 'execute' : 'analyze';
    window.RCAgent3D.react(clip);
    setTimeout(() => window.RCAgent3D && window.RCAgent3D.react(clip), 1300); // catch the avatar once it has loaded
  }
  async function renderMacro() {
    container.innerHTML = `
      <div class="view-head" style="display:flex;align-items:center;gap:var(--s4)">
        <div class="agent-avatar" data-rc-agent3d="avatar" aria-hidden="true" style="width:84px;height:84px;flex:none;border-radius:16px;overflow:hidden"></div>
        <div><h1>Macro AI</h1><span class="sub">The market’s risk backdrop — sentiment, structure &amp; the engine’s regime</span></div>
      </div>
      <div class="stack">
        <section class="panel panel--primary" id="p-macro"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-shield"></use></svg>Risk backdrop
          <span class="right muted small">sentiment · market structure · BTC regime</span></h2>
          <div id="c-macro"><div class="skel"></div><div class="skel"></div></div></section>
      </div>`;
    if (window.RCAgent3D) window.RCAgent3D.mountIfAvailable(container.querySelector('[data-rc-agent3d]'), { mode: 'avatar' });
    renderPanel(C('macro'), async () => {
      const r = await fetchJSON('/api/macro', { auth: false, timeoutMs: 16000 });
      const m = r && r.ok && r.data && r.data.macro;
      if (!m) return null;
      macroReact(m.band);
      return macroBlock(m, r.data.generated_at);
    }, { empty: { icon: 'icon-shield', text: 'Macro data is unavailable right now — check back in a moment.' } });
    every(30000, updateMacroCountdowns);   // keep the event countdown ticking
  }

  /* ═══════════════ Guardian — Agent Flight Recorder ═══════════════ */
  // A tamper-evident record of every trading decision's full provenance:
  // inputs -> voter reasoning -> model/prompt version -> risk-gate verdict ->
  // approval -> transaction -> outcome. The engine hash-chains and cryptographically
  // verifies the ledger; the website mirrors it read-only.

  function shortHash(h) {
    h = String(h || '');
    return h.length > 14 ? h.slice(0, 10) + '…' + h.slice(-4) : (h || '—');
  }

  function chainBanner(chain, win, at) {
    const ok = chain && chain.ok !== false && (!win || !win.problems || win.problems.length === 0);
    const len = (chain && chain.length != null) ? chain.length : '—';
    const tip = chain && chain.tip_hash ? shortHash(chain.tip_hash) : '—';
    const color = ok ? 'var(--up,#31c48d)' : 'var(--down,#f05252)';
    const label = ok ? 'Chain verified' : 'Chain integrity WARNING';
    const icon = ok ? 'icon-check' : 'icon-alert';
    const problems = (win && win.problems && win.problems.length)
      ? `<div class="small" style="color:${color};margin-top:4px">${win.problems.map(esc).join(' · ')}</div>` : '';
    return `<div style="display:flex;align-items:center;gap:var(--s3);flex-wrap:wrap">
        <span class="chip" style="border-color:${color};color:${color}">
          <svg class="icon" aria-hidden="true" style="width:14px;height:14px"><use href="#${icon}"></use></svg> ${label}</span>
        <span class="small muted">${len} entries · tip <code>${esc(tip)}</code>${at ? ' · synced ' + esc(fmtAgo(at)) : ''}</span>
      </div>
      <div class="small muted" style="margin-top:6px;max-width:76ch">Every decision below is sealed into a SHA-256 hash-chained,
        signed append-only ledger the engine verifies cryptographically — any edit, deletion, or reorder breaks the chain.</div>${problems}`;
  }

  function voteRow(v) {
    const col = v.direction === 'bullish' ? 'var(--up,#31c48d)' : v.direction === 'bearish' ? 'var(--down,#f05252)' : 'var(--muted,#8a94a6)';
    const mag = Math.min(100, Math.abs(Number(v.contribution) || 0) * 100);
    return `<div style="display:flex;align-items:center;gap:8px;margin:2px 0">
      <span class="small" style="width:130px;flex:none;color:var(--text)">${esc(v.name)}</span>
      <span style="flex:1;height:6px;background:var(--line,#222);border-radius:3px;overflow:hidden">
        <span style="display:block;height:100%;width:${mag}%;background:${col}"></span></span>
      <span class="small muted" style="width:52px;text-align:right;font-variant-numeric:tabular-nums">${(Number(v.contribution) || 0).toFixed(2)}</span></div>`;
  }

  function tags(list, col) {
    if (!list || !list.length) return '';
    return list.map((x) => `<span class="chip" style="font-size:11px;padding:1px 7px;border-color:${col};color:${col}">${esc(x)}</span>`).join(' ');
  }

  function outcomeBadge(rec) {
    const res = rec.result;
    if (res && res.pnl_usd != null) {
      const p = Number(res.pnl_usd);
      const col = p >= 0 ? 'var(--up,#31c48d)' : 'var(--down,#f05252)';
      const rr = res.close_reason ? ` · ${esc(res.close_reason)}` : '';
      return `<span class="chip" style="border-color:${col};color:${col}">${p >= 0 ? '+' : ''}${fmtMoney(p)}${rr}</span>`;
    }
    const o = String(rec.outcome || '');
    if (o === 'EXECUTED_LIVE') return `<span class="chip" style="border-color:var(--up,#31c48d);color:var(--up,#31c48d)">Executed · open</span>`;
    if (o.startsWith('REJECTED')) return `<span class="chip muted">Rejected on re-check</span>`;
    return `<span class="chip muted">${esc(o || 'recorded')}</span>`;
  }

  function flightCard(rec) {
    const idea = rec.idea || {};
    const risk = rec.risk || {};
    const prov = idea.provenance || {};
    const explain = idea.explain || {};
    const sym = esc(String(rec.symbol || '').replace(':USDT', '').replace('/USDT', ''));
    const dir = String(idea.direction || '').toUpperCase();
    const dirCol = dir === 'LONG' ? 'var(--up,#31c48d)' : dir === 'SHORT' ? 'var(--down,#f05252)' : 'var(--muted)';
    const conf = idea.confidence != null ? Math.round(idea.confidence * 100) + '%' : '—';
    const seq = rec.chain && rec.chain.sequence != null ? '#' + rec.chain.sequence : '';
    const votes = (idea.votes || []).slice(0, 6).map(voteRow).join('');
    const provline = [
      prov.model_provider ? `model <code>${esc(prov.model_provider)}</code>` : '',
      prov.prompt_hash ? `prompt <code>${esc(shortHash(prov.prompt_hash))}</code>` : '',
      prov.analysis_version ? `analyzer <code>${esc(prov.analysis_version)}</code>` : '',
      prov.data_bars != null ? `${prov.data_bars} bars` : '',
      prov.data_thin ? `<span style="color:var(--down,#f05252)">thin data</span>` : '',
    ].filter(Boolean).join(' · ');
    const riskLine = `<span style="color:${String(risk.verdict) === 'APPROVED' ? 'var(--up,#31c48d)' : 'var(--down,#f05252)'}">${esc(risk.verdict || '—')}</span>`
      + ` · ${risk.passed || 0} checks passed${risk.failed ? ` · ${risk.failed} failed` : ''}`
      + (risk.size_usd != null ? ` · size ${fmtMoney(risk.size_usd)}` : '')
      + (risk.checks_failed && risk.checks_failed.length ? `<div class="small" style="color:var(--down,#f05252);margin-top:2px">${risk.checks_failed.map(esc).join(', ')}</div>` : '');
    const bullish = tags(explain.top_bullish, 'var(--up,#31c48d)');
    const bearish = tags(explain.top_bearish, 'var(--down,#f05252)');

    return `<article class="panel" style="margin-bottom:var(--s3)">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <strong style="font-size:15px">${sym || '—'}</strong>
        <span class="chip" style="border-color:${dirCol};color:${dirCol}">${esc(dir || '—')}</span>
        <span class="small muted">conf ${conf}</span>
        ${outcomeBadge(rec)}
        <span class="right small muted" style="margin-left:auto;font-variant-numeric:tabular-nums">${esc(fmtAgo(rec.timestamp))} · ${esc(seq)}</span>
      </div>
      ${(rec.explanation && rec.explanation.narrative) ? `<p style="margin:8px 0 6px;color:var(--text)"><span title="Plain-English explanation drawn strictly from the sealed record">🗣️</span> ${esc(rec.explanation.narrative)}</p>` : (idea.reasoning ? `<p class="small" style="margin:8px 0 6px;color:var(--text)">${esc(idea.reasoning)}</p>` : '')}
      ${(bullish || bearish) ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin:4px 0">${bullish}${bearish}</div>` : ''}
      <details style="margin-top:8px">
        <summary class="small" style="cursor:pointer;color:var(--accent,#3fb6ff)">Provenance &amp; evidence</summary>
        <div style="margin-top:8px;display:grid;gap:10px">
          ${votes ? `<div><div class="small muted" style="margin-bottom:4px">Why — ranked voter contributions</div>${votes}</div>` : ''}
          <div><div class="small muted" style="margin-bottom:2px">Risk gate</div><div class="small">${riskLine}</div></div>
          ${provline ? `<div><div class="small muted" style="margin-bottom:2px">Model &amp; data provenance</div><div class="small">${provline}</div></div>` : ''}
          <div><div class="small muted" style="margin-bottom:2px">Trade geometry</div>
            <div class="small">entry ${fmtPrice(idea.entry)} · SL ${fmtPrice(idea.sl)} · TP ${fmtPrice(idea.tp)}${idea.rr ? ` · ${idea.rr}R` : ''}${idea.timeframe ? ` · ${esc(idea.timeframe)}` : ''}</div></div>
          <div><div class="small muted" style="margin-bottom:2px">Ledger</div>
            <div class="small">decision <code>${esc(rec.decision_id || '—')}</code> · entry hash <code>${esc(shortHash(rec.chain && rec.chain.entry_hash))}</code></div></div>
        </div>
      </details>
    </article>`;
  }

  const _RULE_LABEL = {
    max_position_pct: 'Max size / trade',
    max_symbol_exposure_pct: 'Max per-symbol', max_portfolio_exposure_pct: 'Max portfolio',
    max_open_positions: 'Max open positions', min_confidence: 'Min confidence',
    min_rr: 'Min reward:risk', max_daily_loss_pct: 'Max daily loss',
    max_drawdown_pct: 'Max drawdown', min_free_margin_pct: 'Min free margin',
    allowed_symbols: 'Only these symbols', blocked_symbols: 'Never these symbols',
    allowed_strategy_types: 'Only these strategies', direction: 'Direction',
  };
  function ruleChip(r) {
    const label = _RULE_LABEL[r.type] || r.type;
    let v = r.value;
    if (Array.isArray(v)) v = v.join(', ');
    else if (r.type === 'min_confidence') v = Math.round(Number(v) * 100) + '%';
    else if (/pct$/.test(r.type)) v = v + '%';
    else if (r.type === 'min_rr') v = v + 'R';
    else if (r.type === 'direction') v = String(v).replace('_', ' ');
    return `<span class="chip" style="font-size:11px;padding:2px 8px"><span class="muted">${esc(label)}</span>&nbsp;<strong>${esc(String(v))}</strong></span>`;
  }
  function policyCard(policy) {
    if (!policy || !Array.isArray(policy.rules) || !policy.rules.length) return '';
    const mode = String(policy.mode || 'off');
    const enabled = policy.enabled !== false;
    const active = enabled && mode === 'enforce';
    const col = active ? 'var(--up,#31c48d)' : (mode === 'shadow' ? 'var(--accent,#3fb6ff)' : 'var(--muted,#8a94a6)');
    const modeText = !enabled ? 'disabled' : (mode === 'enforce' ? 'ENFORCING' : mode === 'shadow' ? 'SHADOW (observe-only)' : mode);
    const warns = (policy.warnings || []).length
      ? `<div class="small" style="color:var(--down,#f05252);margin-top:6px">${policy.warnings.map(esc).join(' · ')}</div>` : '';
    return `<section class="panel" style="margin-bottom:var(--s4);border-color:${col}">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <strong>Intent policy</strong>
        <span class="chip" style="border-color:${col};color:${col}">${esc(modeText)}</span>
        ${policy.label ? `<span class="small muted">${esc(policy.label)}</span>` : ''}
        <span class="right small muted" style="margin-left:auto">${esc(policy.policy_id || '')} · <code>${esc(shortHash(policy.compiled_hash))}</code></span>
      </div>
      ${policy.source_text ? `<p class="small muted" style="margin:8px 0 4px;max-width:76ch">“${esc(policy.source_text)}”</p>` : ''}
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${policy.rules.map(ruleChip).join('')}</div>
      <div class="small muted" style="margin-top:8px">The AI proposes; these deterministic rules authorize. A policy can only tighten the engine's caps${mode === 'shadow' ? ' — in shadow mode, violations are recorded below but never block a trade.' : '.'}</div>${warns}
    </section>`;
  }

  const _RISK_COL = {
    none: 'var(--up,#31c48d)', low: 'var(--accent,#3fb6ff)',
    medium: 'var(--warn,#f0a848)', high: 'var(--down,#f05252)',
  };
  function riskCol(r) { return _RISK_COL[String(r || 'none')] || 'var(--muted,#8a94a6)'; }
  function moduleChip(label, risk, armed) {
    const col = riskCol(risk);
    const rk = String(risk || 'none').toUpperCase();
    const arm = armed ? '' : ' <span class="muted" style="font-size:10px">· off</span>';
    return `<span class="chip" style="font-size:11px;padding:2px 8px;border-color:${col}">
      <span class="muted">${esc(label)}</span>&nbsp;<strong style="color:${col}">${esc(rk)}</strong>${arm}</span>`;
  }
  // The Guardian console posture, mirrored from the Telegram /guardian view.
  function guardianPostureCard(gs) {
    if (!gs || typeof gs !== 'object') return '';
    const f = gs.flags || {};
    const posture = String(gs.posture || 'none');
    const col = riskCol(posture);
    const twin = (gs.twin || {}), sent = (gs.sentinel || {}), esc_ = (gs.escape || {});
    const armLabel = (on) => on ? '<strong style="color:var(--up,#31c48d)">armed</strong>' : '<span class="muted">off</span>';
    const chips = [
      moduleChip('🔮 Twin', twin.risk, f.digital_twin),
      moduleChip('🛰 Sentinel', sent.risk, f.risk_sentinel),
      moduleChip('🪂 Escape', esc_.risk, f.escape),
    ].join('');
    return `<section class="panel" style="margin-bottom:var(--s4);border-color:${col}">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <strong>🛡 Guardian posture</strong>
        <span class="chip" style="border-color:${col};color:${col}"><strong>${esc(posture.toUpperCase())}</strong></span>
        <span class="right small muted" style="margin-left:auto">
          Firewall ${armLabel(f.firewall)}${f.firewall_block ? ' <span class="small muted">(blocks HIGH)</span>' : ''}
          · Intent policy ${armLabel(f.intent_policy)}</span>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">${chips}</div>
      <div class="small muted" style="margin-top:8px">Live-book safety read across the Guardian layer${
        (twin.position_count ? ` · ${twin.position_count} open position${twin.position_count === 1 ? '' : 's'}` : ' · flat')
      }. The AI proposes · controls authorize · the recorder proves · the escape agent recovers.</div>
    </section>`;
  }

  function guardianBlock(data) {
    const recs = (data && data.records) || [];
    const head = `<div style="margin-bottom:var(--s4)">${chainBanner(data.chain, data.window, data.updated_at)}</div>`
      + guardianPostureCard(data.guardian_status)
      + policyCard(data.policy);
    if (!recs.length) {
      return head + `<div class="empty small muted" style="padding:var(--s4)">No decisions have been recorded yet. As the engine confirms or rejects trades, each one is sealed here with its full provenance.</div>`;
    }
    return head + recs.map(flightCard).join('');
  }

  async function renderGuardian() {
    container.innerHTML = `
      <div class="view-head" style="display:flex;align-items:center;gap:var(--s4)">
        <div class="agent-avatar" data-rc-agent3d="avatar" aria-hidden="true" style="width:84px;height:84px;flex:none;border-radius:16px;overflow:hidden"></div>
        <div><h1>Guardian</h1><span class="sub">The safety layer — live posture across every control, plus tamper-evident evidence for every decision</span></div>
      </div>
      <div class="stack">
        <section class="panel" id="p-author" hidden></section>
        <section class="panel panel--primary" id="p-flight"><h2 class="panel-title"><svg class="icon" aria-hidden="true"><use href="#icon-check"></use></svg>Decision ledger
          <span class="right muted small">inputs · reasoning · model · risk gate · outcome</span></h2>
          <div id="c-flight"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div></section>
      </div>`;
    if (window.RCAgent3D) window.RCAgent3D.mountIfAvailable(container.querySelector('[data-rc-agent3d]'), { mode: 'avatar' });
    // Operator-only Intent Compiler authoring (compile → preview → bind). The
    // server re-checks plan==='admin' and the bot re-verifies the caller, so this
    // client gate is only about what to SHOW.
    fetchJSON('/api/auth/me').then((me) => {
      if (me && me.ok && me.data && me.data.plan === 'admin') mountPolicyAuthoring(C('author'));
    }).catch(() => {});
    renderPanel(C('flight'), async () => {
      const r = await fetchJSON('/api/guardian/flight?limit=50', { auth: false, timeoutMs: 16000 });
      if (!r || !r.ok || !r.data) return null;
      return guardianBlock(r.data);
    }, { empty: { icon: 'icon-check', text: 'The decision ledger is unavailable right now — check back in a moment.' } });
  }

  function mountPolicyAuthoring(panel) {
    if (!panel) return;
    panel.hidden = false;
    panel.innerHTML = `
      <h2 class="panel-title">🛡 Author intent policy
        <span class="right muted small">operator only · tighten-only</span></h2>
      <p class="small muted" style="margin:0 0 8px">Describe a policy in plain language — the AI compiles it into deterministic, tighten-only rules the risk gate enforces. It can only <em>tighten</em> the engine's caps, and previews before it binds.</p>
      <textarea id="pol-text" rows="3" spellcheck="false"
        placeholder="only majors, max 5% per trade, no shorts, min confidence 70%, stop if down 8%"
        style="width:100%;box-sizing:border-box;font:inherit;padding:10px;border-radius:10px;border:1px solid var(--line,#2a3142);background:var(--bg-elev,#141a24);color:inherit;resize:vertical"></textarea>
      <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center">
        <button class="btn" id="pol-preview">Preview</button>
        <button class="btn" data-polmode="shadow">Set shadow</button>
        <button class="btn" data-polmode="enforce">Set enforce</button>
        <button class="btn" data-polmode="off">Set off</button>
        <button class="btn" id="pol-clear" style="border-color:var(--down,#f05252);color:var(--down,#f05252)">Clear</button>
        <span class="right small muted" id="pol-msg" style="margin-left:auto"></span>
      </div>
      <div id="pol-out" style="margin-top:12px"></div>`;
    const msg = (t) => { const m = panel.querySelector('#pol-msg'); if (m) m.textContent = t || ''; };
    panel.addEventListener('click', async (e) => {
      const text = () => String((panel.querySelector('#pol-text') || {}).value || '').trim();
      // Preview (compile, no bind)
      if (e.target.closest('#pol-preview')) {
        const t = text();
        if (!t) { msg('Type a policy first.'); return; }
        msg('Compiling…');
        const r = await fetchJSON('/api/controls/policy/preview', { method: 'POST', body: { text: t } }).catch(() => null);
        if (!r || !r.ok) { msg(r?.data?.detail || r?.data?.error || 'Compile failed.'); return; }
        const d = r.data || {};
        if (!d.rules || !d.rules.length) {
          panel.querySelector('#pol-out').innerHTML = `<div class="small muted">${esc(d.note || 'No rules recognised.')}</div>`;
          msg(''); return;
        }
        const warns = (d.warnings || []).length
          ? `<div class="small" style="color:var(--down,#f05252);margin-top:6px">${d.warnings.map(esc).join(' · ')}</div>` : '';
        panel.querySelector('#pol-out').innerHTML =
          `<div class="small muted" style="margin-bottom:6px">Review, then bind. Shadow logs would-be rejections without blocking; enforce adds them to the risk gate.</div>
           <pre style="white-space:pre-wrap;background:var(--bg-elev,#141a24);padding:10px;border-radius:10px;border:1px solid var(--line,#2a3142)">${esc(d.human_readable || '')}</pre>${warns}
           <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
             <button class="btn" data-apply="shadow">👁 Apply (shadow)</button>
             <button class="btn btn--primary" data-apply="enforce">🛡 Apply (enforce)</button>
           </div>`;
        msg('');
        return;
      }
      // Apply (bind) — recompiled from the text server-side
      const ab = e.target.closest('button[data-apply]');
      if (ab) {
        const t = text();
        if (!t) return;
        ab.disabled = true; msg('Binding…');
        const r = await fetchJSON('/api/controls/policy/apply', { method: 'POST', body: { text: t, mode: ab.dataset.apply } }).catch(() => null);
        ab.disabled = false;
        if (r?.ok) { toast(`Policy bound (${ab.dataset.apply}).${r.data && r.data.bound === false ? ' Saved but dormant — INTENT_POLICY_ENABLED is off.' : ''}`); showView('guardian'); }
        else { msg(r?.data?.detail || r?.data?.error || 'Bind failed.'); }
        return;
      }
      // Change mode of the existing policy
      const mb = e.target.closest('button[data-polmode]');
      if (mb) {
        mb.disabled = true;
        const r = await fetchJSON('/api/controls/policy/mode', { method: 'POST', body: { mode: mb.dataset.polmode } }).catch(() => null);
        mb.disabled = false;
        if (r?.ok) { toast(`Policy mode → ${mb.dataset.polmode}.`); showView('guardian'); }
        else toast(r?.data?.error === 'no_policy' ? 'No policy set yet — author one first.' : (r?.data?.detail || r?.data?.error || 'Mode change failed.'));
        return;
      }
      // Clear the bound policy
      if (e.target.closest('#pol-clear')) {
        const r = await fetchJSON('/api/controls/policy/clear', { method: 'POST' }).catch(() => null);
        if (r?.ok) { toast(r.data && r.data.removed ? 'Policy cleared.' : 'No policy was set.'); showView('guardian'); }
        else toast('Clear failed.');
      }
    });
  }

  /* ═══════════════ Boot ═══════════════ */
  const RENDER = { home: renderHome, chat: renderChat, hub: renderHub, markets: renderMarkets,
                   macro: renderMacro, guardian: renderGuardian,
                   signals: renderSignals, deepscan: renderDeepScan, news: renderNews,
                   feed: renderFeed, trade: renderTrade, portfolio: renderPortfolio, tax: renderTax,
                   reputation: renderReputation, counterparty: renderCounterparty, worlds: renderWorlds, dapps: renderDapps,
                   leaderboard: renderLeaderboard, lab: renderLab, engine: renderEngine,
                   account: renderAccount };

  window.addEventListener('hashchange', () => showView(location.hash.slice(1) || 'home'));

  // SSE: refresh the bits that changed, only re-render if the view shows them.
  // Live events make the 3D agent react: a fresh scan -> 'analyze', a new
  // signal -> 'alert', a trade/fill -> 'execute' (no-op unless an avatar is
  // mounted on the current view).
  const agentReact = (clip) => { if (window.RCAgent3D) window.RCAgent3D.react(clip); };
  connectStream({
    scan: () => { cache.scan = null; agentReact('analyze'); getScan().then(updateConnChip); if (currentView === 'engine' || currentView === 'deepscan') showView(currentView, { soft: true }); },
    portfolio: () => { cache.portfolio = null; if (currentView === 'home' || currentView === 'portfolio') showView(currentView, { soft: true }); },
    trade: () => { cache.portfolio = null; agentReact('execute'); toast('Trade update from the engine.'); if (currentView === 'home' || currentView === 'portfolio' || currentView === 'trade') showView(currentView, { soft: true }); },
    signals: () => { agentReact('alert'); if (currentView === 'signals') showView('signals', { soft: true }); },
    activity: onActivity,
  });

  // Drill-down: click (or Enter/Space) any [data-sym] card opens the symbol
  // detail modal. Delegated on body so it survives every view re-render.
  document.body.addEventListener('click', (e) => {
    if (e.target.closest('#symModal .modal-card')) return;      // inside the modal
    if (e.target.closest('#symModal')) { closeSymModal(); return; } // backdrop
    // UX-4: one-tap paper-trade — stash the signal geometry and jump to the
    // Trade view, which applies it on mount. Delegated so it survives every
    // signal-stream re-render.
    const pbtn = e.target.closest('[data-ptrade]');
    if (pbtn) {
      try { tradePrefill = JSON.parse(pbtn.getAttribute('data-ptrade')); } catch (_) { tradePrefill = null; }
      if (location.hash.slice(1) === 'trade') showView('trade'); else location.hash = '#trade';
      return;
    }
    const el = e.target.closest('[data-sym]');
    if (el && !e.target.closest('a, button')) openSymbol(el.getAttribute('data-sym'));
  });
  document.body.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeSymModal(); return; }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-sym][role="button"]')) {
      e.preventDefault(); openSymbol(e.target.getAttribute('data-sym'));
    }
  });
  const _symClose = document.getElementById('symModalClose');
  if (_symClose) _symClose.addEventListener('click', closeSymModal);

  getScan().then(updateConnChip);
  showView(location.hash.slice(1) || 'home');
})();

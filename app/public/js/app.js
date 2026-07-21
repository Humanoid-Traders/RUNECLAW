/**
 * RUNECLAW shared runtime — session, fetch, panel states, toasts, SSE,
 * formatters. Loaded by every page before page-specific scripts.
 * No framework, no build step: everything hangs off `window.RC`.
 */
(function () {
  'use strict';

  // ── Session ────────────────────────────────────────────────────────────
  function resolveToken() {
    const legacy = localStorage.getItem('token');
    if (legacy) return legacy;
    try {
      const s = JSON.parse(localStorage.getItem('rc_session') || 'null');
      return (s && s.token) || null;
    } catch (e) { return null; }
  }
  const TOKEN = resolveToken();
  const LOGGED_IN = !!TOKEN;

  function authHeaders() {
    return TOKEN ? { Authorization: 'Bearer ' + TOKEN } : {};
  }
  function logout() {
    localStorage.removeItem('rc_session');
    localStorage.removeItem('token');
    location.href = '/';
  }

  // ── fetchJSON: timeout + auth + typed errors ───────────────────────────
  async function fetchJSON(url, { method = 'GET', body, timeoutMs = 10000, auth = true } = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch(url, {
        method,
        signal: ctrl.signal,
        headers: {
          ...(auth ? authHeaders() : {}),
          ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      let data = null;
      try { data = await r.json(); } catch (e) { /* non-JSON body */ }
      return { ok: r.ok, status: r.status, data };
    } finally {
      clearTimeout(timer);
    }
  }

  // ── postWithStepUp: 2FA step-up on money-moving actions ────────────────
  // POST `url`; if the server demands a fresh authenticator code (HTTP 401
  // { error:'two_factor_required' } — a live-money action on a 2FA-enrolled
  // account), prompt once and retry with the code appended. A cancelled
  // prompt surfaces the original 401 so the caller can show the message.
  // Non-2FA accounts and paper actions never trigger the prompt (the server
  // only returns that status when it genuinely needs the step-up).
  async function postWithStepUp(url, body, opts = {}) {
    let r = await fetchJSON(url, { method: 'POST', body, ...opts })
      .catch(() => ({ ok: false, data: null }));
    if (r && r.status === 401 && r.data && r.data.error === 'two_factor_required') {
      const code = (window.prompt(
        r.data.detail || 'Enter your 6-digit authenticator code:') || '').trim();
      if (!code) return r;
      r = await fetchJSON(url, { method: 'POST', body: { ...body, totp_code: code }, ...opts })
        .catch(() => ({ ok: false, data: null }));
    }
    return r;
  }

  // ── Formatters ──────────────────────────────────────────────────────────
  function esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function fmt(n, d = 2) { return n != null && isFinite(n) ? Number(n).toFixed(d) : '--'; }
  function fmtMoney(n, d = 2) {
    if (n == null || !isFinite(n)) return '--';
    return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function fmtPrice(n) {
    if (n == null || !isFinite(n)) return '--';
    const v = Number(n);
    const d = v >= 1000 ? 2 : v >= 1 ? 4 : 6;
    return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: d });
  }
  function fmtK(n) {
    if (n == null || !isFinite(n)) return '--';
    n = Number(n);
    if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toFixed(0);
  }
  function signed(n, d = 2) {
    if (n == null || !isFinite(n)) return '--';
    return (Number(n) >= 0 ? '+' : '') + Number(n).toFixed(d);
  }
  function pnlClass(n) {
    const v = Number(n);
    if (n == null || !isFinite(v)) return '';  // unknown -> muted, never red
    return v >= 0 ? 'pos' : 'neg';
  }
  function fmtAgo(iso) {
    if (!iso) return '--';
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (!isFinite(s)) return '--';
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }
  // Direction chip: glyph + text, never color alone.
  function dirChip(direction) {
    const up = String(direction).toUpperCase() === 'LONG' || String(direction).toUpperCase() === 'BUY';
    return `<span class="chip ${up ? 'chip--up' : 'chip--down'}">${up ? '▲ LONG' : '▼ SHORT'}</span>`;
  }

  // ── Bot-HTML sanitizer (whitelist: b, i, code, pre, br) ─────────────────
  function sanitizeBotHtml(html) {
    const ALLOWED = { B: 'b', I: 'i', CODE: 'code', PRE: 'pre' };
    let doc;
    try { doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html'); }
    catch (e) { return esc(html); }
    const walk = (node) => {
      let out = '';
      node.childNodes.forEach(ch => {
        if (ch.nodeType === Node.TEXT_NODE) out += esc(ch.textContent);
        else if (ch.nodeType === Node.ELEMENT_NODE) {
          if (ch.tagName === 'BR') out += '<br>';
          else if (ALLOWED[ch.tagName]) out += `<${ALLOWED[ch.tagName]}>${walk(ch)}</${ALLOWED[ch.tagName]}>`;
          else out += walk(ch);
        }
      });
      return out;
    };
    return walk(doc.body.firstChild || doc.body);
  }

  // ── Toasts (aria-live region) ────────────────────────────────────────────
  function toastRegion() {
    let el = document.getElementById('toastRegion');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toastRegion';
      el.className = 'toast-region';
      el.setAttribute('aria-live', 'polite');
      document.body.appendChild(el);
    }
    return el;
  }
  function toast(message, kind = '') {
    const region = toastRegion();
    const t = document.createElement('div');
    t.className = 'toast' + (kind ? ` toast--${kind}` : '');
    t.textContent = message;
    region.appendChild(t);
    setTimeout(() => t.remove(), 5000);
    while (region.children.length > 4) region.firstChild.remove();
  }

  // ── renderPanel: the ONE loading/empty/error pattern ─────────────────────
  // renderPanel(el, loader, opts)
  //   loader: async () => html string | '' (empty) | null (empty)
  //   opts: { timeoutMs, empty: {icon, text, cta: {label, href|onClick}}, errorText }
  // States: skeleton -> data | empty (in-app CTA) | error (Retry button).
  function stateBlock({ icon = 'icon-inbox', text = 'Nothing here yet.', cta = null }) {
    return `<div class="state-block">
      <svg class="icon"><use href="#${icon}"></use></svg>
      <p>${esc(text)}</p>
      ${cta ? `<a class="btn btn--sm" href="${esc(cta.href || '#')}">${esc(cta.label)}</a>` : ''}
    </div>`;
  }
  async function renderPanel(el, loader, opts = {}) {
    if (!el) return;
    const { timeoutMs = 8000, empty = {}, errorText = "Couldn't load this panel." } = opts;
    // Refresh-in-place: once a panel has shown REAL content, periodic
    // re-renders (the every() timers, SSE nudges) keep it on screen while
    // the loader runs — no skeleton flash every 15-30s, and a transient
    // fetch failure keeps last-known data instead of blanking to an error.
    const hasContent = el.dataset.rcLoaded === '1';
    if (!hasContent) {
      el.innerHTML = '<div class="skel"></div><div class="skel"></div><div class="skel"></div>';
    }
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; fail(); }, timeoutMs);
    function fail() {
      if (hasContent) return;                 // stale beats blank
      el.innerHTML = `<div class="state-block">
        <svg class="icon"><use href="#icon-offline"></use></svg>
        <p>${esc(errorText)}</p>
        <button class="btn btn--sm" type="button">Retry</button>
      </div>`;
      const btn = el.querySelector('button');
      if (btn) btn.onclick = () => renderPanel(el, loader, opts);
    }
    try {
      const html = await loader();
      clearTimeout(timer);
      if (timedOut && hasContent) return;
      if (html == null || html === '') {
        delete el.dataset.rcLoaded;
        el.innerHTML = stateBlock(empty);
      } else {
        el.innerHTML = html;
        el.dataset.rcLoaded = '1';
      }
    } catch (e) {
      clearTimeout(timer);
      if (!timedOut) fail();
    }
  }

  // ── SSE (server push -> named callbacks) ─────────────────────────────────
  function connectStream(handlers) {
    if (typeof EventSource === 'undefined') return null;
    try {
      const es = new EventSource('/api/stream');
      Object.entries(handlers || {}).forEach(([evt, fn]) => es.addEventListener(evt, fn));
      es.onerror = () => { /* browser auto-reconnects; polling covers gaps */ };
      return es;
    } catch (e) { return null; }
  }

  // ── Modal a11y: focus-trap + inert background + focus-return ─────────────
  // Turns a role="dialog" element (a direct <body> child) into a proper modal.
  // Call open() when you show it and close() when you hide it. Handles WCAG
  // 2.4.3 (focus order) + 4.1.2: sets aria-modal, makes every OTHER top-level
  // element `inert` (SR + Tab can't reach the page behind it), cycles Tab /
  // Shift+Tab inside the dialog, and returns focus to whatever was focused when
  // it opened (the trigger). `inert` degrades gracefully where unsupported —
  // the keydown trap still keeps Tab inside.
  function modalA11y(dialog) {
    let prevFocus = null;
    const focusables = () => Array.from(dialog.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
      .filter(el => !el.disabled && el.getClientRects().length > 0);
    function onKeydown(e) {
      if (e.key !== 'Tab') return;
      const f = focusables();
      if (!f.length) { e.preventDefault(); return; }
      const first = f[0], last = f[f.length - 1], a = document.activeElement;
      if (e.shiftKey && (a === first || !dialog.contains(a))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (a === last || !dialog.contains(a))) { e.preventDefault(); first.focus(); }
    }
    function setInert(on) {
      Array.from(document.body.children).forEach(el => {
        if (el === dialog || el.tagName === 'SCRIPT') return;
        if (on) el.setAttribute('inert', '');
        else el.removeAttribute('inert');
      });
    }
    return {
      open(focusEl) {
        prevFocus = document.activeElement;
        dialog.setAttribute('aria-modal', 'true');
        setInert(true);
        document.addEventListener('keydown', onKeydown, true);
        const target = focusEl || focusables()[0] || dialog;
        if (target && target.focus) { try { target.focus(); } catch (e) { /* noop */ } }
      },
      close() {
        document.removeEventListener('keydown', onKeydown, true);
        setInert(false);
        dialog.removeAttribute('aria-modal');
        if (prevFocus && prevFocus.focus) { try { prevFocus.focus(); } catch (e) { /* noop */ } }
        prevFocus = null;
      },
    };
  }

  window.RC = {
    TOKEN, LOGGED_IN, authHeaders, logout,
    fetchJSON, postWithStepUp, esc, fmt, fmtMoney, fmtPrice, fmtK, signed, pnlClass, fmtAgo,
    dirChip, sanitizeBotHtml, toast, renderPanel, stateBlock, connectStream,
    modalA11y,
  };
})();

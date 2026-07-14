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
  function pnlClass(n) { return Number(n) >= 0 ? 'pos' : 'neg'; }
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
    el.innerHTML = '<div class="skel"></div><div class="skel"></div><div class="skel"></div>';
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; fail(); }, timeoutMs);
    function fail() {
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
      if (timedOut) return;
      if (html == null || html === '') {
        el.innerHTML = stateBlock(empty);
      } else {
        el.innerHTML = html;
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

  window.RC = {
    TOKEN, LOGGED_IN, authHeaders, logout,
    fetchJSON, esc, fmt, fmtMoney, fmtPrice, fmtK, signed, pnlClass, fmtAgo,
    dirChip, sanitizeBotHtml, toast, renderPanel, stateBlock, connectStream,
  };
})();

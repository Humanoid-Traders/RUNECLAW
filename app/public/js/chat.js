/**
 * RUNECLAW chat drawer — the same assistant as the Telegram bot, for every
 * logged-in user. Free text runs intent routing -> skills -> LLM fallback on
 * the bot; typed trades ("buy SOL 71 sl 70 tp 76") come back as pending-trade
 * cards with Confirm/Cancel wired to /api/trade/*.
 * Replies render through RC.sanitizeBotHtml (b/i/code/pre/br only).
 */
(function () {
  'use strict';
  const { LOGGED_IN, fetchJSON, esc, fmt, sanitizeBotHtml, toast } = window.RC;

  const fab = document.getElementById('chatFab');
  const drawer = document.getElementById('chatDrawer');
  const body = document.getElementById('chatBody');
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const sendBtn = document.getElementById('chatSend');
  if (!fab || !drawer) return;

  let open = false;
  let busy = false;
  let hydrated = false;

  function setOpen(v) {
    open = v;
    // Toggle BOTH the class and the native attribute: the attribute keeps the
    // overlay out of the page even if the stylesheet ever fails to load.
    drawer.classList.toggle('hidden', !open);
    drawer.hidden = !open;
    fab.classList.toggle('hidden', open);
    fab.hidden = open;
    if (open) {
      if (!hydrated) hydrate();
      input.focus();
    }
  }

  function appendMsg(role, html, cls) {
    const div = document.createElement('div');
    div.className = `chat-msg ${role}${cls ? ' ' + cls : ''}`;
    div.innerHTML = role === 'user' ? esc(html) : html;
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
    return div;
  }

  function appendTradeCard(pt) {
    const div = document.createElement('div');
    div.className = 'chat-card';
    const live = pt.mode === 'LIVE';
    div.innerHTML = `
      <span class="mode-badge ${live ? 'mode-badge--live' : 'mode-badge--paper'}">${live ? 'LIVE — REAL MONEY' : 'PAPER'}</span>
      <div class="kv-row"><span>${esc(pt.symbol)}/USDT ${esc(pt.direction)}</span><b>R:R ${fmt(pt.rr)}</b></div>
      <div class="kv-row"><span>Entry (limit)</span><b>$${fmt(pt.entry, 4)}</b></div>
      <div class="kv-row"><span>Stop · Target</span><b>$${fmt(pt.sl, 4)} · $${fmt(pt.tp, 4)}</b></div>
      <div class="row mt-3">
        <button class="btn btn--primary btn--sm" style="flex:1" type="button">Confirm</button>
        <button class="btn btn--sm" style="flex:1" type="button">Cancel</button>
      </div>`;
    const [okBtn, noBtn] = div.querySelectorAll('button');
    okBtn.onclick = async () => {
      okBtn.disabled = noBtn.disabled = true;
      const r = await fetchJSON('/api/trade/confirm', { method: 'POST', body: { trade_id: pt.trade_id }, timeoutMs: 35000 })
        .catch(() => ({ ok: false, data: null }));
      if (!r.ok) {
        const reason = r.data?.error === 'live_not_enabled'
          ? 'Live trading is not enabled for your account.'
          : (r.data?.detail || r.data?.error || 'Confirm failed.');
        appendMsg('bot', `<b>Blocked:</b> ${esc(reason)}`);
        okBtn.disabled = noBtn.disabled = false;
        return;
      }
      appendMsg('bot', sanitizeBotHtml(r.data.result_html || 'Executed.'));
      document.dispatchEvent(new CustomEvent('rc:portfolio-changed'));
    };
    noBtn.onclick = async () => {
      okBtn.disabled = noBtn.disabled = true;
      await fetchJSON('/api/trade/cancel', { method: 'POST', body: { trade_id: pt.trade_id } }).catch(() => {});
      appendMsg('bot', 'Cancelled — nothing was placed.');
    };
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
  }

  async function hydrate() {
    hydrated = true;
    const r = await fetchJSON('/api/chat/history?limit=30').catch(() => null);
    if (r && r.ok && r.data?.messages?.length) {
      r.data.messages.forEach(m => {
        if (m.role === 'user') appendMsg('user', m.content);
        else appendMsg('bot', sanitizeBotHtml(m.content));
      });
    } else if (!body.children.length) {
      appendMsg('bot', 'I\'m the same analyst that runs the Telegram bot — ask about the market, your portfolio, or type a trade like <code>buy SOL 71 sl 70 tp 76</code>.');
    }
  }

  async function send() {
    if (busy) return;
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    appendMsg('user', text);
    const typing = appendMsg('bot', 'Thinking…', 'pending');
    busy = true;
    sendBtn.disabled = true;
    try {
      const r = await fetchJSON('/api/chat', { method: 'POST', body: { text }, timeoutMs: 50000 });
      typing.remove();
      if (r.status === 429) appendMsg('bot', 'Rate limit hit — give it a few seconds.');
      else if (r.status === 503) appendMsg('bot', 'Chat isn\'t configured on this deployment yet.');
      else if (!r.ok) appendMsg('bot', `<b>Error:</b> ${esc(r.data?.detail || r.data?.error || 'chat unavailable')}`);
      else if (r.data.pending_trade) appendTradeCard(r.data.pending_trade);
      else appendMsg('bot', sanitizeBotHtml(r.data.reply_html || '…'));
    } catch (e) {
      typing.remove();
      appendMsg('bot', 'Network error — try again.');
    } finally {
      busy = false;
      sendBtn.disabled = false;
    }
  }

  // Availability: any logged-in user gets the FAB; hide only when the
  // deployment has no gateway configured (503) or the user isn't authed.
  async function init() {
    if (!LOGGED_IN) return;
    const r = await fetchJSON('/api/chat/history?limit=1').catch(() => null);
    if (r && (r.status === 503 || r.status === 401)) return;
    fab.classList.remove('hidden');
    fab.hidden = false;
  }

  fab.addEventListener('click', () => setOpen(true));
  document.getElementById('chatClose').addEventListener('click', () => setOpen(false));
  form.addEventListener('submit', (e) => { e.preventDefault(); send(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && open) setOpen(false);
  });

  init();
})();

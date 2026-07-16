/**
 * RUNECLAW chat drawer — the same assistant as the Telegram bot, for every
 * logged-in user. Free text runs intent routing -> skills -> LLM fallback on
 * the bot; typed trades ("buy SOL 71 sl 70 tp 76") come back as pending-trade
 * cards with Confirm/Cancel wired to /api/trade/*.
 * Replies render through RC.sanitizeBotHtml (b/i/code/pre/br only).
 */
(function () {
  'use strict';
  const { LOGGED_IN, fetchJSON, esc, fmt, fmtMoney, signed, sanitizeBotHtml, toast, modalA11y } = window.RC;

  // Anonymous visitors (landing page, not signed in) get the SAME drawer wired
  // to the account-free public endpoint: general market/product Q&A only, no
  // history, no portfolio, no trade cards. Signed-in users keep the full path.
  const PUBLIC = !LOGGED_IN;
  const ENDPOINT = PUBLIC ? '/api/public/chat' : '/api/chat';

  const fab = document.getElementById('chatFab');
  const drawer = document.getElementById('chatDrawer');
  const body = document.getElementById('chatBody');
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const sendBtn = document.getElementById('chatSend');
  const metaEl = document.getElementById('chatMeta');
  if (!fab || !drawer) return;

  let open = false;
  let busy = false;
  let hydrated = false;
  let inline = false;   // docked in the page flow (no overlay, no focus trap)
  const a11y = modalA11y(drawer);

  // ── Inline mode ──────────────────────────────────────────────────────────
  // The same chat, docked INTO the page (landing section / dashboard view)
  // instead of floating over it. Inline is not a modal: no inert/focus trap,
  // no FAB, no close button (CSS hides it). mountInline moves the single
  // drawer node into the host; unmountInline returns it to the floating state.
  function mountInline(host) {
    if (!host) return;
    if (open) setOpen(false);           // never both overlay and inline
    inline = true;
    host.appendChild(drawer);
    drawer.classList.add('chat--inline');
    drawer.classList.remove('hidden'); drawer.hidden = false;
    fab.classList.add('hidden'); fab.hidden = true;
    if (!hydrated) hydrate().then(renderChips); else renderChips();
    loadMeta();
  }
  function unmountInline() {
    if (!inline) return;
    inline = false;
    drawer.classList.remove('chat--inline');
    drawer.classList.add('hidden'); drawer.hidden = true;
    document.body.appendChild(drawer);  // back to its floating anchor
    fab.classList.remove('hidden'); fab.hidden = false;
  }
  // ask(text): open the chat wherever it lives (docked or drawer) and send —
  // lets any page element hand a question to the agent (journal post-mortems,
  // setup reviews). send() is a hoisted declaration below.
  function ask(text) {
    if (!text) return;
    if (!inline) setOpen(true);
    // Through the composer (not send(text)): the retry path skips appending
    // the user bubble, which is only correct when the bubble already exists.
    input.value = String(text);
    send();
  }
  window.RCChat = { mountInline, unmountInline, ask, focus: () => input.focus() };

  function setOpen(v) {
    if (inline) return;                 // docked in the page — nothing to toggle
    if (v === open) return;
    open = v;
    // Toggle BOTH the class and the native attribute: the attribute keeps the
    // overlay out of the page even if the stylesheet ever fails to load.
    if (open) {
      drawer.classList.remove('hidden'); drawer.hidden = false;
      // a11y.open captures the FAB (still focused/visible) as the return target,
      // makes the rest of the page inert, traps Tab, and focuses the input.
      a11y.open(input);
      fab.classList.add('hidden'); fab.hidden = true;
      if (!hydrated) hydrate().then(renderChips); else renderChips();
      loadMeta();  // logged-in: refresh the live portfolio strip on open
    } else {
      drawer.classList.add('hidden'); drawer.hidden = true;
      fab.classList.remove('hidden'); fab.hidden = false;
      a11y.close();  // release inert/trap + return focus to the FAB
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

  // "Trade this" — an analysis produced a concrete setup. One tap re-proposes
  // it through the SAME manual /api/trade/propose -> confirm rails (which run
  // every risk gate), then renders the normal Confirm/Cancel card. Anonymous
  // visitors never see this (they can't trade); the server never returns a
  // setup on the public endpoint anyway.
  function appendSetupAction(s) {
    if (PUBLIC || !s) return;
    const dir = String(s.direction || '').toUpperCase();
    if (!s.symbol || (dir !== 'LONG' && dir !== 'SHORT')) return;
    const div = document.createElement('div');
    div.className = 'chat-card chat-setup';
    const rr = (s.rr != null) ? `R:R ${fmt(s.rr)}` : '';
    const conf = (s.confidence != null) ? `<span class="chip chip--info">${Math.round(s.confidence * 100)}% conf</span>` : '';
    div.innerHTML = `
      <div class="kv-row"><span>${esc(s.symbol)}/USDT ${esc(dir)} ${conf}</span><b>${rr}</b></div>
      <div class="kv-row"><span>Entry · Stop · Target</span><b>$${fmt(s.entry, 4)} · $${fmt(s.sl, 4)} · $${fmt(s.tp, 4)}</b></div>
      <button class="btn btn--primary btn--sm mt-3" type="button" style="width:100%">Trade this</button>`;
    const btn = div.querySelector('button');
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = 'Setting up…';
      const r = await fetchJSON('/api/trade/propose', {
        method: 'POST',
        body: { direction: dir, symbol: s.symbol, entry: s.entry, sl: s.sl, tp: s.tp },
        timeoutMs: 20000,
      }).catch(() => ({ ok: false, data: null }));
      if (!r.ok || !r.data || !r.data.pending_trade) {
        appendMsg('bot', `<b>Couldn't set up that trade:</b> ${esc(r.data?.detail || r.data?.error || 'try again')}`);
        btn.disabled = false;
        btn.textContent = 'Trade this';
        return;
      }
      div.remove();  // replace the hint with the real confirmable trade card
      appendTradeCard(r.data.pending_trade);
    };
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
  }

  // Live portfolio strip in the chat header (logged-in only). Reuses the same
  // snapshot the dashboard shows; hides itself silently on any failure.
  async function loadMeta() {
    if (PUBLIC || !metaEl) return;
    const r = await fetchJSON('/api/portfolio').catch(() => null);
    if (!r || !r.ok || !r.data) { metaEl.hidden = true; return; }
    const d = r.data;
    const eq = (d.equity == null) ? '—'
      : (fmtMoney ? fmtMoney(d.equity) : '$' + fmt(d.equity, 2));
    const dp = Number(d.daily_pnl || 0);
    const dpTxt = signed ? signed(dp) : (dp >= 0 ? '+' + fmt(dp, 2) : fmt(dp, 2));
    const openN = (d.open_positions || []).length;
    const modeCls = d.mode === 'LIVE' ? 'mode-badge--live' : 'mode-badge--paper';
    metaEl.innerHTML =
      `<span class="mode-badge ${modeCls}">${esc(d.mode || 'PAPER')}</span>` +
      `<span class="chat-meta-item"><span class="k">Equity</span><b>${eq}</b></span>` +
      `<span class="chat-meta-item"><span class="k">Today</span><b class="${dp >= 0 ? 'up' : 'down'}">${dpTxt}</b></span>` +
      `<span class="chat-meta-item"><span class="k">Open</span><b>${openN}</b></span>`;
    metaEl.hidden = false;
  }

  async function hydrate() {
    hydrated = true;
    if (PUBLIC) {
      // Anonymous: no server-side history (nothing is stored). Just a welcome.
      if (!body.children.length) {
        appendMsg('bot', 'Hey — I\'m <b>RUNECLAW</b>, the AI trading agent. Ask me anything about crypto, markets, or how RUNECLAW works. <b>Sign in</b> and connect an exchange to unlock live scans and your own portfolio.');
      }
      return;
    }
    const r = await fetchJSON('/api/chat/history?limit=30').catch(() => null);
    if (r && r.ok && r.data?.messages?.length) {
      r.data.messages.forEach(m => {
        if (m.role === 'user') appendMsg('user', m.content);
        else appendMsg('bot', sanitizeBotHtml(m.content));
      });
    } else if (!body.children.length) {
      // First conversation ever: the agent introduces itself properly.
      appendMsg('bot',
        '👋 I\'m <b>your RUNECLAW agent</b> — the same engine that scans 60+ pairs, '
        + 'gates every idea through a 23-check risk engine, and trades autonomously.<br><br>'
        + 'Talk to me like a colleague: ask for a <b>market briefing</b>, the '
        + '<b>highest-conviction setup</b>, or a <b>post-mortem</b> of any trade. '
        + 'You can even place paper trades right here — try '
        + '<code>buy SOL 71 sl 70 tp 76</code>.');
    }
  }

  // Suggestion chips — quick prompts that mirror the Telegram quick actions.
  // Shown when the conversation is fresh; hidden once the user is chatting.
  // Anonymous visitors get market/education prompts only (no account actions).
  const CHIP_PROMPTS = PUBLIC ? [
    'What is RUNECLAW?', 'How does it manage risk?',
    'What is a liquidity sweep?', 'How does leverage work?',
    'Which exchanges are supported?',
  ] : [
    // Pro-desk workflow: brief -> find conviction -> execute -> review.
    'Give me a market briefing', "What's the highest-conviction setup right now?",
    'Backtest SOL', 'Why no trade on BTC?', 'Long ETH',
    'Alert me when BTC drops below $100k',
    "What if I'd taken every signal with $1k?", "This week's letter",
    'RWA radar', 'Show my positions', 'Post-mortem my last trade',
  ];
  const chipsEl = document.getElementById('chatChips');
  function hideChips() { if (chipsEl) chipsEl.innerHTML = ''; }
  function renderChips() {
    if (!chipsEl) return;
    // Only offer chips on an essentially-empty conversation (welcome only).
    if (body.querySelector('.chat-msg.user')) { hideChips(); return; }
    chipsEl.innerHTML = '';
    CHIP_PROMPTS.forEach((p) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip chat-chip';
      b.textContent = p;
      b.addEventListener('click', () => { send(p); });
      chipsEl.appendChild(b);
    });
  }

  // ── Voice ──────────────────────────────────────────────────────────────────
  // Mic dictation (Web Speech API) + optional spoken replies (speechSynthesis).
  // Both feature-detected: the buttons stay hidden on browsers without the API.
  const micBtn = document.getElementById('chatMic');
  const ttsBtn = document.getElementById('chatTts');

  // Dictation fills the composer and NEVER auto-sends: this chat can act
  // (propose trades, run backtests, arm alerts), so a misheard sentence must
  // never fire an action — the user reads what was heard, then presses send.
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recog = null;
  let listening = false;
  function stopMic() {
    listening = false;
    if (micBtn) {
      micBtn.classList.remove('mic--live');
      micBtn.setAttribute('aria-pressed', 'false');
      micBtn.textContent = '🎤';
    }
    if (recog) { try { recog.stop(); } catch (e) { /* already stopped */ } }
  }
  function startMic() {
    try {
      recog = new SR();
    } catch (e) { return; }
    recog.lang = navigator.language || 'en-US';
    recog.interimResults = true;
    recog.continuous = false;
    const base = input.value ? input.value.replace(/\s+$/, '') + ' ' : '';
    recog.onresult = (ev) => {
      let text = '';
      for (const res of ev.results) text += res[0].transcript;
      input.value = (base + text).slice(0, 2000);
    };
    recog.onend = () => { stopMic(); input.focus(); };
    recog.onerror = () => stopMic();
    listening = true;
    micBtn.classList.add('mic--live');
    micBtn.setAttribute('aria-pressed', 'true');
    micBtn.textContent = '⏺';
    try { recog.start(); } catch (e) { stopMic(); }
  }
  if (micBtn && SR) {
    micBtn.hidden = false;
    micBtn.addEventListener('click', () => (listening ? stopMic() : startMic()));
  }

  // Spoken replies — a per-browser preference, off by default.
  let ttsOn = false;
  try { ttsOn = localStorage.getItem('rc_tts') === '1'; } catch (e) { /* private mode */ }
  function renderTtsBtn() {
    ttsBtn.textContent = ttsOn ? '🔊' : '🔇';
    ttsBtn.setAttribute('aria-pressed', String(ttsOn));
    ttsBtn.title = ttsOn ? 'Spoken replies on — click to mute' : 'Read replies aloud';
    ttsBtn.setAttribute('aria-label', ttsBtn.title);
  }
  function speechText(html) {
    // `html` is already sanitized; a detached div never executes anything.
    const div = document.createElement('div');
    div.innerHTML = html;
    const text = (div.textContent || '').replace(/\s+/g, ' ').trim();
    // Long analyses are a chore to sit through — speak the first sentences.
    return text.length > 420 ? text.slice(0, 420) + '… more on screen.' : text;
  }
  function speakReply(html) {
    if (!ttsOn || !window.speechSynthesis) return;
    const text = speechText(html);
    if (!text) return;
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = navigator.language || 'en-US';
      u.rate = 1.05;
      window.speechSynthesis.speak(u);
    } catch (e) { /* voice is best-effort */ }
  }
  if (ttsBtn && window.speechSynthesis) {
    ttsBtn.hidden = false;
    renderTtsBtn();
    ttsBtn.addEventListener('click', () => {
      ttsOn = !ttsOn;
      try { localStorage.setItem('rc_tts', ttsOn ? '1' : '0'); } catch (e) { /* fine */ }
      if (!ttsOn) { try { window.speechSynthesis.cancel(); } catch (e) { /* fine */ } }
      renderTtsBtn();
    });
  }

  // Append a bot error bubble with a one-tap Retry, and restore the user's
  // text to the composer so a failed turn never loses what they typed.
  function appendFailure(html, text) {
    const div = appendMsg('bot', html + ' ');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn--sm';
    btn.textContent = 'Retry';
    btn.style.marginTop = '6px';
    btn.addEventListener('click', () => { div.remove(); send(text); });
    div.appendChild(btn);
    if (!input.value.trim()) input.value = text;  // don't clobber new typing
    body.scrollTop = body.scrollHeight;
  }

  async function send(retryText) {
    if (busy) return;
    const isRetry = retryText != null;
    const text = isRetry ? retryText : input.value.trim();
    if (!text) return;
    if (!isRetry) { input.value = ''; appendMsg('user', text); }
    // Animated typing indicator (three-dot) instead of a static "Thinking…".
    const typing = appendMsg('bot',
      '<span class="typing-dots" aria-label="Assistant is typing"><span></span><span></span><span></span></span>',
      'pending');
    hideChips();
    busy = true;
    sendBtn.disabled = true;
    try {
      const r = await fetchJSON(ENDPOINT, { method: 'POST', body: { text }, timeoutMs: 50000 });
      typing.remove();
      if (r.status === 429) appendFailure('Rate limit hit — give it a few seconds.', text);
      else if (r.status === 503) {
        appendMsg('bot', 'Chat isn\'t available on this deployment right now — please try again shortly.');
        // Operator hint (console only — never surfaced to visitors): the bot
        // user-gateway isn't reachable/configured.
        console.warn('[chat] gateway 503 — set WEB_GATEWAY_SECRET (same on website + bot) and BOT_GATEWAY_URL, then redeploy both.');
      }
      else if (r.data?.error === 'gateway_disabled' || r.status === 502) {
        // Pairing problem between website and bot — an operator issue, not the
        // visitor's. Say so plainly; keep the actionable detail in the console.
        appendMsg('bot', 'Chat isn\'t connected on this deployment yet — the operator is being notified. Please check back soon.');
        console.warn('[chat] bot gateway rejected the shared secret — set the SAME WEB_GATEWAY_SECRET on the bot (env or admin /setgateway) and the website.');
      }
      else if (!r.ok) appendFailure(`<b>Error:</b> ${esc(r.data?.detail || r.data?.error || 'chat unavailable')}`, text);
      else if (r.data.pending_trade) appendTradeCard(r.data.pending_trade);
      else {
        // Analysis / answer bubble, plus (when the skill surfaced a concrete
        // setup) a one-tap "Trade this" card underneath it.
        const safeHtml = sanitizeBotHtml(r.data.reply_html || '…');
        const bubble = appendMsg('bot', safeHtml);
        speakReply(safeHtml);
        // Model transparency: show WHICH model answered (the visible face of
        // tier routing — and of a runeclaw promotion). LLM replies only;
        // intent-routed skill replies carry no model.
        if (r.data.model && r.data.intent === 'chat') {
          const cap = document.createElement('div');
          cap.className = 'muted small';
          cap.style.cssText = 'margin-top:4px;opacity:.7;font-size:11px';
          cap.textContent = '🤖 ' + r.data.model;
          bubble.appendChild(cap);
        }
        if (r.data.setup) appendSetupAction(r.data.setup);
      }
    } catch (e) {
      typing.remove();
      appendFailure('Network error.', text);
    } finally {
      busy = false;
      sendBtn.disabled = false;
    }
  }

  // Availability: ALWAYS surface the FAB — for everyone, on every page. Chat
  // must never be an invisible feature. Previously a signed-in visitor's FAB
  // was hidden whenever /api/chat/history returned 503, so if the bot gateway
  // wasn't configured (WEB_GATEWAY_SECRET / BOT_GATEWAY_URL) the chat button
  // silently didn't exist — indistinguishable from "the feature isn't there".
  // Now the drawer always opens; if the gateway is unconfigured/unreachable the
  // composer says so on send (503), which is self-diagnosing instead of silent.
  async function init() {
    // A page that ships a host element gets the chat docked IN the page
    // (landing). Pages without one keep the floating FAB + drawer.
    const host = document.getElementById('chatInlineHost');
    if (host) { mountInline(host); return; }
    fab.classList.remove('hidden');
    fab.hidden = false;
  }

  fab.addEventListener('click', () => setOpen(true));
  document.getElementById('chatClose').addEventListener('click', () => setOpen(false));
  form.addEventListener('submit', (e) => { e.preventDefault(); send(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && open) setOpen(false);
  });
  // A trade confirmed here (or elsewhere on the page) changes the book — refresh
  // the portfolio strip if the drawer is open.
  document.addEventListener('rc:portfolio-changed', () => { if (open) loadMeta(); });

  init();
})();

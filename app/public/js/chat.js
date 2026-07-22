/**
 * RUNECLAW chat drawer — the same assistant as the Telegram bot, for every
 * logged-in user. Free text runs intent routing -> skills -> LLM fallback on
 * the bot; typed trades ("buy SOL 71 sl 70 tp 76") come back as pending-trade
 * cards with Confirm/Cancel wired to /api/trade/*.
 * Replies render through RC.sanitizeBotHtml (b/i/code/pre/br only).
 */
(function () {
  'use strict';
  const { LOGGED_IN, fetchJSON, postWithStepUp, esc, fmt, fmtMoney, signed, sanitizeBotHtml, toast, modalA11y } = window.RC;

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

  // ── WEB-VISION: image attachments (chart / positions screenshots) ─────────
  // Logged-in only (public chat has no vision path). The agent READS a pasted
  // or picked screenshot; the server admin-gates who may actually use it.
  // Images are downscaled client-side to keep the upload small.
  let attached = [];            // [{ media_type, data(base64, no data: prefix) }]
  let attachRow = null, fileInput = null;
  function renderAttachments() {
    if (!attachRow) return;
    if (!attached.length) { attachRow.innerHTML = ''; attachRow.hidden = true; return; }
    attachRow.hidden = false;
    attachRow.innerHTML = attached.map((a, i) =>
      '<span class="chat-attach"><img alt="attachment preview" src="data:'
      + a.media_type + ';base64,' + a.data + '">'
      + '<button type="button" data-i="' + i + '" aria-label="Remove image">×</button></span>').join('');
  }
  async function downscaleImage(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((res, rej) => {
        const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = url; });
      const max = 1600; let w = img.width, h = img.height;
      if (w > max || h > max) { const s = max / Math.max(w, h); w = Math.round(w * s); h = Math.round(h * s); }
      const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
      cv.getContext('2d').drawImage(img, 0, 0, w, h);
      return { media_type: 'image/jpeg', data: cv.toDataURL('image/jpeg', 0.82).split(',')[1] };
    } finally { URL.revokeObjectURL(url); }
  }
  async function addImageFile(file) {
    if (!file || !/^image\//.test(file.type || '')) return;
    if (attached.length >= 3) { toast('Up to 3 images at a time.'); return; }
    try { attached.push(await downscaleImage(file)); renderAttachments(); }
    catch (e) { toast('Couldn\'t read that image — try another.'); }
  }
  if (!PUBLIC && form && sendBtn) {
    fileInput = document.createElement('input');
    fileInput.type = 'file'; fileInput.accept = 'image/*'; fileInput.hidden = true;
    fileInput.addEventListener('change', () => {
      const f = fileInput.files && fileInput.files[0]; if (f) addImageFile(f); fileInput.value = ''; });
    const attachBtn = document.createElement('button');
    attachBtn.type = 'button'; attachBtn.className = 'chat-attach-btn';
    attachBtn.title = 'Attach a chart or screenshot';
    attachBtn.setAttribute('aria-label', 'Attach an image'); attachBtn.textContent = '📎';
    attachBtn.addEventListener('click', () => fileInput.click());
    sendBtn.parentNode.insertBefore(attachBtn, sendBtn);
    attachRow = document.createElement('div');
    attachRow.className = 'chat-attach-row'; attachRow.hidden = true;
    attachRow.appendChild(fileInput);
    form.parentNode.insertBefore(attachRow, form);
    attachRow.addEventListener('click', (e) => {
      const b = e.target.closest && e.target.closest('button[data-i]'); if (!b) return;
      attached.splice(Number(b.dataset.i), 1); renderAttachments();
    });
    input.addEventListener('paste', (e) => {
      const items = (e.clipboardData || {}).items || [];
      for (const it of items) {
        if (it.type && it.type.indexOf('image') === 0) {
          const f = it.getAsFile(); if (f) { addImageFile(f); e.preventDefault(); }
        }
      }
    });
  }

  let open = false;
  let busy = false;
  let pending = null;   // one-slot queue: a message sent while a turn is in flight
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

  function fmtChatTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  function appendMsg(role, html, cls, ts) {
    const div = document.createElement('div');
    div.className = `chat-msg ${role}${cls ? ' ' + cls : ''}`;
    div.innerHTML = role === 'user' ? esc(html) : html;
    // Restored history carries a timestamp from the gateway — show it as a
    // muted time cue so a reloaded conversation isn't a wall of context-free
    // bubbles. Live turns pass no ts (a model caption goes there instead).
    const t = ts ? fmtChatTime(ts) : '';
    if (t) {
      const el = document.createElement('time');
      el.className = 'chat-ts muted small';
      el.textContent = t;
      div.appendChild(el);
    }
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
      const r = await postWithStepUp('/api/trade/confirm', { trade_id: pt.trade_id }, { timeoutMs: 35000 });
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
        if (m.role === 'user') appendMsg('user', m.content, '', m.timestamp);
        else appendMsg('bot', sanitizeBotHtml(m.content), '', m.timestamp);
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
    'RWA radar', 'My net worth', "What's my total exposure?", 'Research PENDLE', 'Show my positions', 'Post-mortem my last trade',
  ];
  const chipsEl = document.getElementById('chatChips');
  function hideChips() { if (chipsEl) chipsEl.innerHTML = ''; }
  function renderChips() {
    if (!chipsEl) return;
    // Only offer chips on an essentially-empty conversation (welcome only).
    if (body.querySelector('.chat-msg.user')) { hideChips(); return; }
    chipsEl.innerHTML = '';
    // WEB-VISION discovery: a chip that opens the image picker, so users find
    // the "read my chart" capability where they already look. Logged-in only
    // (the attach UI, and the vision path, don't exist for anonymous chat).
    if (!PUBLIC && fileInput) {
      const vb = document.createElement('button');
      vb.type = 'button';
      vb.className = 'chip chat-chip chat-chip--vision';
      vb.textContent = '📎 Read a chart';
      vb.title = 'Attach or paste a chart / screenshot for the agent to read';
      vb.addEventListener('click', () => fileInput.click());
      chipsEl.appendChild(vb);
    }
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
    recog.onerror = (ev) => {
      // Silently swallowing this left permission-blocked users with a dead mic
      // and no explanation. Branch on the error: explain the blocking ones,
      // stay quiet for benign 'no-speech'/'aborted'.
      const err = ev && ev.error;
      if (err === 'not-allowed' || err === 'service-not-allowed') {
        toast('Microphone blocked — enable mic access in your browser to dictate.');
      } else if (err && err !== 'no-speech' && err !== 'aborted') {
        toast('Voice input unavailable.');
      }
      stopMic();
    };
    listening = true;
    micBtn.classList.add('mic--live');
    micBtn.setAttribute('aria-pressed', 'true');
    micBtn.textContent = '⏺';
    try { recog.start(); } catch (e) { stopMic(); toast('Voice input unavailable.'); }
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
  function appendFailure(html, text, cooldownMs) {
    const div = appendMsg('bot', html + ' ');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn--sm';
    btn.textContent = 'Retry';
    btn.style.marginTop = '6px';
    btn.addEventListener('click', () => { div.remove(); send(text); });
    div.appendChild(btn);
    // Rate-limit retries used to be instantly clickable and guaranteed to fail
    // again. When a cooldown is given, disable + count it down so Retry only
    // arms once it can actually succeed.
    if (cooldownMs && cooldownMs > 0) {
      let left = Math.ceil(cooldownMs / 1000);
      btn.disabled = true;
      btn.textContent = `Retry in ${left}s`;
      const tick = setInterval(() => {
        left -= 1;
        if (left <= 0) { clearInterval(tick); btn.disabled = false; btn.textContent = 'Retry'; }
        else btn.textContent = `Retry in ${left}s`;
      }, 1000);
    }
    if (!input.value.trim()) input.value = text;  // don't clobber new typing
    body.scrollTop = body.scrollHeight;
  }

  // Progressive reveal: stream a plain-text answer in word batches for a
  // "typing" feel. Anything with markup (code blocks, lists) or reduced-motion
  // renders instantly so tags never tear mid-reveal. Streams into a child span
  // so a later caption append (model name) is never clobbered.
  function revealInto(div, html) {
    const reduce = window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || /<[a-z!/]/i.test(html)) { div.innerHTML = html; body.scrollTop = body.scrollHeight; return; }
    const tmp = document.createElement('div'); tmp.innerHTML = html;
    const text = tmp.textContent || '';
    const parts = text.split(/(\s+)/);
    const span = document.createElement('span');
    div.appendChild(span);
    let i = 0;
    const step = () => {
      span.textContent += parts.slice(i, i + 3).join('');
      i += 3;
      body.scrollTop = body.scrollHeight;
      if (i < parts.length) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  async function send(retryText) {
    const isRetry = retryText != null;
    const text = isRetry ? retryText : input.value.trim();
    // WEB-VISION: attachments ride a fresh (non-retry) send. Snapshot them here;
    // they're cleared once the request is actually dispatched (not while queued).
    const imgs = (!isRetry && attached.length) ? attached.slice() : [];
    if (!text && !imgs.length) return;
    // A turn is already in flight: queue this one (one slot) instead of
    // silently dropping it — chip clicks and post-mortem asks used to vanish.
    // Echo the user's message now so the queue is visible; drain on finally.
    if (busy) {
      if (imgs.length) { toast('Finishing the last reply — resend your image in a moment.'); return; }
      if (pending == null) {
        pending = text;
        if (!isRetry) { input.value = ''; appendMsg('user', text); }
      } else {
        toast('One message at a time — still finishing the last one.');
      }
      return;
    }
    if (!isRetry) {
      input.value = '';
      if (imgs.length) { attached = []; renderAttachments(); }
      appendMsg('user', text || '🖼️ image');
    }
    // Animated typing indicator (three-dot) instead of a static "Thinking…",
    // with a Cancel affordance so a slow turn isn't a helpless wait.
    // Vision turns get a labelled state ("Reading your screenshot…") so the
    // wait reads as the agent actually looking at the image, not a generic hang.
    const typing = appendMsg('bot',
      (imgs.length ? '<span class="chat-vision-label">Reading your screenshot… </span>' : '')
      + '<span class="typing-dots" aria-label="Assistant is typing"><span></span><span></span><span></span></span>',
      'pending');
    const ac = new AbortController();
    let cancelled = false;
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn--sm chat-cancel';
    cancelBtn.style.marginLeft = '8px';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', () => { cancelled = true; ac.abort(); });
    typing.appendChild(cancelBtn);
    hideChips();
    busy = true;
    sendBtn.disabled = true;
    if (window.RCAgent3D) window.RCAgent3D.setThinking(true);   // agent avatar: 'analyze'
    try {
      const r = await fetchJSON(ENDPOINT, { method: 'POST', body: (imgs.length ? { text, images: imgs } : { text }), timeoutMs: 50000, signal: ac.signal });
      typing.remove();
      if (r.status === 429) appendFailure('Rate limit hit — give it a few seconds.', text, 5000);
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
        const bubble = appendMsg('bot', '');
        revealInto(bubble, safeHtml);   // progressive "streaming" reveal
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
        // Free-tier meter: show how many free questions remain so the limit is
        // visible before the wall, not only at it. Only for a real LLM answer to
        // a non-exempt (free) user; the quota-exceeded turn renders its own
        // upgrade card. quota is absent when the cap is dormant (no funded Grok).
        const _q = r.data.quota;
        if (r.data.intent === 'chat' && _q && _q.exempt === false
            && typeof _q.remaining === 'number') {
          const meter = document.createElement('div');
          meter.className = 'muted small';
          meter.style.cssText = 'margin-top:4px;opacity:.75;font-size:11px';
          meter.innerHTML = _q.remaining > 0
            ? `⚡ ${_q.remaining} of ${_q.limit} free questions left today`
            : '⚡ Last free question today — <a href="/dashboard#account/aplan">upgrade for unlimited →</a>';
          bubble.appendChild(meter);
        }
        if (r.data.setup) appendSetupAction(r.data.setup);
      }
    } catch (e) {
      typing.remove();
      // User pressed Cancel: no error bubble — just restore their text so the
      // turn can be re-sent. Anything else is a genuine network failure.
      if (cancelled) { if (!input.value.trim()) input.value = text; }
      else appendFailure('Network error.', text);
    } finally {
      busy = false;
      sendBtn.disabled = false;
      if (window.RCAgent3D) window.RCAgent3D.setThinking(false);   // back to idle
      // Drain the one-slot queue (a message sent mid-turn). Retry path: the
      // user bubble was already echoed when queued, so no double bubble.
      if (pending != null) { const t = pending; pending = null; send(t); }
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

/**
 * Custom agent alerts — user-defined "tell me when…" tripwires.
 *
 * Users arm conditions in plain chat ("tell me when BTC drops below $100k")
 * or from the Live Feed panel. A tiny engine evaluates every active alert
 * against live Bitget public tickers once a minute and, when one trips,
 * marks it (one-shot) and sends a TARGETED web push to that user only.
 *
 * Deliberately web-side: evaluation uses the same public ticker source the
 * Markets view already proxies, so alerts keep working even when the bot
 * process is down. No money path — an alert only ever sends a notification.
 */

const { pool } = require('../db');

const MAX_ACTIVE_PER_USER = 10;

// ── Symbol resolution ────────────────────────────────────────────────────────

const NAME_MAP = {
  bitcoin: 'BTC', btc: 'BTC', ethereum: 'ETH', ether: 'ETH', eth: 'ETH',
  solana: 'SOL', sol: 'SOL', dogecoin: 'DOGE', doge: 'DOGE',
  ripple: 'XRP', xrp: 'XRP', cardano: 'ADA', ada: 'ADA',
  avalanche: 'AVAX', avax: 'AVAX', chainlink: 'LINK', link: 'LINK',
  polkadot: 'DOT', dot: 'DOT', litecoin: 'LTC', ltc: 'LTC',
};
// Words that can appear between the trigger phrase and the symbol.
const STOPWORDS = new Set(['the', 'a', 'an', 'price', 'of', 'on', 'for', 'my']);

function resolveBase(word) {
  const w = String(word || '').toLowerCase().replace(/^\$/, '');
  if (NAME_MAP[w]) return NAME_MAP[w];
  if (/^[a-z0-9]{2,10}$/.test(w)) return w.toUpperCase().replace(/USDT$/, '');
  return null;
}

// ── Parser ───────────────────────────────────────────────────────────────────

const TRIGGER_RE =
  /^(?:please\s+)?(?:tell me|alert me|notify me|ping me|warn me|let me know)\s+(?:when|if)\s+(.+)$/i;
// "every time" / "whenever" arms a RECURRING alert (re-arms after a cooldown).
const RECURRING_RE =
  /^(?:please\s+)?(?:tell me|alert me|notify me|ping me|warn me|let me know)\s+(?:every time|whenever|each time)\s+(.+)$/i;
// DeFi tripwire: "…my health factor drops below 1.5" (no symbol — it reads
// the linked wallet's Aave book).
const HF_RE = /(?:my\s+)?(?:aave\s+)?health factor\s+(?:drops?|falls?|goes?|is)?\s*(?:below|under)\s*([\d.]+)/i;
// Signal watch: "…a signal fires on my watchlist" / "…a signal fires on SOL".
const SIGNAL_RE = /(?:a\s+|the\s+|any\s+)?(?:new\s+)?signal\s+(?:fires?|posts?|appears?|comes)(?:\s+(?:on|for)\s+(?:my\s+watchlist|([a-z0-9$]{2,10})))?/i;
const LIST_RE = /^(?:(?:show|list)\s+)?my\s+alerts$|^(?:show|list)\s+(?:active\s+)?alerts$/i;
const COND_RE = new RegExp(
  '(drops|falls|dips|dumps|goes|moves|rises|pumps|climbs|breaks|crosses|hits|reaches|is|trades)'
  + '\\s*(?:by\\s+)?(below|under|above|over|past|through|to)?\\s*'
  + '\\$?([\\d][\\d,]*\\.?\\d*)\\s*(k|m|%)?', 'i');

const DOWN_VERBS = new Set(['drops', 'falls', 'dips', 'dumps']);
const UP_VERBS = new Set(['rises', 'pumps', 'climbs']);

/**
 * Parse a chat message into an alert command.
 * Returns null (not an alert ask), {kind:'list'}, or
 * {kind:'create', base, metric, op, threshold, inferOp} where inferOp means
 * the direction must be resolved against the CURRENT price at create time
 * ("when BTC hits 120k" — above or below depends on where BTC is now).
 */
function parseAlertCommand(text) {
  const t = String(text || '').trim();
  if (LIST_RE.test(t)) return { kind: 'list' };
  let mode = 'once';
  let m = t.match(RECURRING_RE);
  if (m) mode = 'recurring';
  else m = t.match(TRIGGER_RE);
  if (!m) return null;
  const rest = m[1].trim();

  // DeFi + signal tripwires parse before the price grammar.
  const hf = rest.match(HF_RE);
  if (hf) {
    const th = parseFloat(hf[1]);
    if (!isFinite(th) || th <= 0) return { kind: 'unparsed' };
    return { kind: 'create', base: 'DEFI', metric: 'health_factor', op: '<', threshold: th, mode };
  }
  const sig = rest.match(SIGNAL_RE);
  if (sig) {
    const coin = sig[1] ? resolveBase(sig[1]) : null;
    // Signal watch is recurring by nature — every matching signal notifies.
    return { kind: 'create', base: coin || 'WATCHLIST', metric: 'signal', op: '>', threshold: 0, mode: 'recurring' };
  }

  const cond = rest.match(COND_RE);
  if (!cond) return { kind: 'unparsed' };

  // Symbol: last non-stopword token before the condition verb.
  const head = rest.slice(0, cond.index).trim().split(/\s+/).filter(Boolean);
  let base = null;
  for (let i = head.length - 1; i >= 0; i--) {
    if (STOPWORDS.has(head[i].toLowerCase())) continue;
    base = resolveBase(head[i]);
    break;
  }
  if (!base) return { kind: 'unparsed' };

  const verb = cond[1].toLowerCase();
  const dir = (cond[2] || '').toLowerCase();
  let threshold = parseFloat(cond[3].replace(/,/g, ''));
  const suffix = (cond[4] || '').toLowerCase();
  if (!isFinite(threshold)) return { kind: 'unparsed' };
  if (suffix === 'k') threshold *= 1e3;
  if (suffix === 'm') threshold *= 1e6;

  if (suffix === '%') {
    // 24h-change tripwire. "drops 5%" → change below -5; "pumps 5%" →
    // change above +5; "moves 5%" → |change| above 5.
    if (DOWN_VERBS.has(verb)) {
      return { kind: 'create', base, metric: 'change_24h', op: '<', threshold: -Math.abs(threshold), mode };
    }
    if (UP_VERBS.has(verb) || dir === 'above' || dir === 'over') {
      return { kind: 'create', base, metric: 'change_24h', op: '>', threshold: Math.abs(threshold), mode };
    }
    return { kind: 'create', base, metric: 'change_abs_24h', op: '>', threshold: Math.abs(threshold), mode };
  }

  // Price tripwire.
  let op = null;
  if (dir === 'below' || dir === 'under') op = '<';
  else if (dir === 'above' || dir === 'over' || dir === 'past' || dir === 'through') op = '>';
  else if (DOWN_VERBS.has(verb)) op = '<';
  else if (UP_VERBS.has(verb)) op = '>';
  if (op) return { kind: 'create', base, metric: 'price', op, threshold, mode };
  // "hits/reaches/crosses/breaks/is/trades [to] X" — direction depends on
  // where price is NOW; the route resolves it against the live ticker.
  return { kind: 'create', base, metric: 'price', op: null, threshold, inferOp: true, mode };
}

// ── Ticker source (injectable for tests) ─────────────────────────────────────

// Shared source (lib/tickers.js): one fetch + cache for alerts AND the RWA
// radar. Same map shape as before, plus a volume field the radar uses.
const defaultFetchTickers = require('./tickers').getTickers;
let fetchTickers = defaultFetchTickers;
function setTickerFetcher(fn) { fetchTickers = fn || defaultFetchTickers; }

// ── Formatting ───────────────────────────────────────────────────────────────

function fmtPrice(v) {
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  const dp = n >= 1000 ? 0 : n >= 1 ? 2 : 6;
  return '$' + n.toLocaleString('en-US', { maximumFractionDigits: dp });
}

function describeCondition(a) {
  const base = String(a.symbol || '').replace(/USDT$/, '');
  const rec = a.mode === 'recurring' ? ' (recurring)' : '';
  if (a.metric === 'change_24h') {
    return `${base} 24h change ${a.op === '<' ? 'below' : 'above'} ${Number(a.threshold).toFixed(1)}%${rec}`;
  }
  if (a.metric === 'change_abs_24h') {
    return `${base} moves more than ${Number(a.threshold).toFixed(1)}% in 24h${rec}`;
  }
  if (a.metric === 'health_factor') {
    return `Aave health factor below ${Number(a.threshold).toFixed(2)}${rec}`;
  }
  if (a.metric === 'signal') {
    return base === 'WATCHLIST'
      ? 'engine signal on a watchlist coin (recurring)'
      : `engine signal on ${base} (recurring)`;
  }
  return `${base} price ${a.op === '<' ? 'below' : 'above'} ${fmtPrice(a.threshold)}${rec}`;
}

// ── Evaluation ───────────────────────────────────────────────────────────────

/** Current metric value if the alert condition holds, else null. */
function evaluateAlert(a, tk) {
  if (!tk) return null;
  let v;
  if (a.metric === 'price') v = tk.price;
  else if (a.metric === 'change_24h') v = tk.change;
  else if (a.metric === 'change_abs_24h') v = Math.abs(tk.change);
  else return null;
  if (!isFinite(v)) return null;
  const hit = a.op === '>' ? v > Number(a.threshold) : v < Number(a.threshold);
  return hit ? v : null;
}

// ── Store operations (shared by REST routes and the chat handler) ───────────

async function listAlerts(userId) {
  const [rows] = await pool.execute(
    'SELECT * FROM user_alerts WHERE user_id = ? ORDER BY id DESC LIMIT 50', [userId]);
  return rows;
}

/**
 * Validate + insert. Returns { ok, alert?, error?, now? } — `error` is a
 * user-facing sentence, `now` the live metric value at creation.
 */
async function createAlert(userId, { base, metric, op, threshold, inferOp, mode, cooldownMin }) {
  if (!['price', 'change_24h', 'change_abs_24h', 'health_factor', 'signal'].includes(metric)) {
    return { ok: false, error: 'Unsupported alert metric.' };
  }
  const th = Number(threshold);
  if (!isFinite(th)) return { ok: false, error: 'The alert needs a numeric level.' };
  mode = mode === 'recurring' ? 'recurring' : 'once';
  // Signal watch dedupes by triggered_at, so its cooldown stays 0.
  const cooldown = metric === 'signal' ? 0
    : Math.min(1440, Math.max(5, Number(cooldownMin) || 60));

  let symbol, now = null;
  if (metric === 'health_factor') {
    symbol = 'DEFI';
    if (!(th > 0 && th <= 10)) {
      return { ok: false, error: 'A health-factor threshold between 0 and 10 makes sense (e.g. 1.5).' };
    }
    const { walletAddressOf } = require('./wallet');
    if (!(await walletAddressOf(userId))) {
      return { ok: false, error: 'Link a wallet first (Account view) so I can read your Aave health factor.' };
    }
  } else if (metric === 'signal' && String(base).toUpperCase() === 'WATCHLIST') {
    symbol = 'WATCHLIST';
  } else {
    symbol = String(base || '').toUpperCase().replace(/USDT$/, '') + 'USDT';
    if (!/^[A-Z0-9]{2,10}USDT$/.test(symbol)) {
      return { ok: false, error: 'That does not look like a symbol I can watch.' };
    }
    let tk = null;
    try {
      tk = (await fetchTickers())[symbol] || null;
    } catch (e) { /* validation degrades gracefully below */ }
    if (!tk) {
      return { ok: false, error: `I can't find a ${symbol.replace(/USDT$/, '')} perpetual on the exchange, so I can't watch it.` };
    }
    if (inferOp || !op) {
      op = metric === 'price' ? (th > tk.price ? '>' : '<') : '>';
    }
    if (metric !== 'signal') now = metric === 'price' ? tk.price : tk.change;
  }
  if (op !== '>' && op !== '<') return { ok: false, error: 'Unsupported alert direction.' };

  const [cnt] = await pool.execute(
    'SELECT COUNT(*) AS n FROM user_alerts WHERE user_id = ? AND active = 1', [userId]);
  if ((cnt[0]?.n || 0) >= MAX_ACTIVE_PER_USER) {
    return { ok: false, error: `You already have ${MAX_ACTIVE_PER_USER} active alerts — delete one first.` };
  }

  await pool.execute(
    `INSERT INTO user_alerts (user_id, symbol, metric, op, threshold, mode, cooldown_min, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [userId, symbol, metric, op, th, mode, cooldown, new Date()]);
  const alert = { symbol, metric, op, threshold: th, mode, cooldown_min: cooldown };
  return { ok: true, alert, now };
}

async function deleteAlert(userId, id) {
  const [r] = await pool.execute(
    'DELETE FROM user_alerts WHERE id = ? AND user_id = ?', [Number(id), userId]);
  return (r.affectedRows || 0) > 0;
}

// ── Engine ───────────────────────────────────────────────────────────────────

/**
 * Evaluate all active alerts once. One-shot semantics: a tripped alert is
 * deactivated in the same statement that stamps it, so a slow push can never
 * double-fire it. Returns the number of alerts tripped. Never throws.
 */
/** Fire an alert race-safely. One-shot: disarm-and-stamp. Recurring: restamp
 * only if the cooldown has passed (the WHERE guard makes racing evaluators
 * harmless). Returns true when THIS caller won the fire. */
async function fireAlert(a, value) {
  const now = new Date();
  if (a.mode === 'recurring') {
    const cooldownMs = (Number(a.cooldown_min) || 0) * 60_000;
    const cutoff = new Date(now.getTime() - cooldownMs);
    if (a.triggered_at && new Date(a.triggered_at).getTime() > cutoff.getTime()) return false;
    const [upd] = await pool.execute(
      `UPDATE user_alerts SET triggered_at = ?, trigger_price = ?
       WHERE id = ? AND active = 1 AND (triggered_at IS NULL OR triggered_at <= ?)`,
      [now, value, a.id, cutoff]);
    return (upd.affectedRows || 0) > 0;
  }
  const [upd] = await pool.execute(
    `UPDATE user_alerts SET active = 0, triggered_at = ?, trigger_price = ?
     WHERE id = ? AND active = 1`,
    [now, value, a.id]);
  return (upd.affectedRows || 0) > 0;
}

// On-chain reads (health factor) run on a slower cadence than tickers.
const ONCHAIN_INTERVAL_MS = 5 * 60_000;
let lastOnchainSweep = 0;

async function runOnce(notify) {
  let send = notify;
  if (!send) {
    const { notifySubscribers } = require('./push');
    send = notifySubscribers;
  }
  try {
    const [rows] = await pool.execute('SELECT * FROM user_alerts WHERE active = 1');
    if (!rows.length) return 0;
    const map = await fetchTickers();
    let tripped = 0;
    const doOnchain = Date.now() - lastOnchainSweep >= ONCHAIN_INTERVAL_MS;
    if (doOnchain && rows.some(a => a.metric === 'health_factor')) lastOnchainSweep = Date.now();

    for (const a of rows) {
      let v = null, bodyOverride = null;
      if (a.metric === 'signal') {
        // New engine signal on the watched coin / the user's watchlist since
        // the last fire (or the last 15 minutes for a fresh alert).
        try {
          const [sigs] = await pool.execute(
            'SELECT symbol, direction, confidence, created_at FROM signals ORDER BY created_at DESC LIMIT 50', []);
          const since = a.triggered_at
            ? new Date(a.triggered_at).getTime() : Date.now() - 15 * 60_000;
          let bases;
          if (a.symbol === 'WATCHLIST') {
            const [prows] = await pool.execute(
              'SELECT user_id, risk_pref, watchlist, prefs FROM user_profiles WHERE user_id = ?', [a.user_id]);
            let wl = [];
            try { wl = JSON.parse(prows[0]?.watchlist || '[]'); } catch (e) { /* empty */ }
            bases = new Set(wl.map(s => String(s).toUpperCase().replace(/USDT$/, '')));
          } else {
            bases = new Set([String(a.symbol).replace(/USDT$/, '')]);
          }
          const hit = sigs.find(s => {
            const b = String(s.symbol || '').toUpperCase().split('/')[0].replace(/USDT.*$/, '');
            return bases.has(b) && new Date(s.created_at).getTime() > since;
          });
          if (hit) {
            v = 0;
            const b = String(hit.symbol).split('/')[0];
            bodyOverride = `Engine signal: ${String(hit.direction || '').toUpperCase()} ${b}`
              + (isFinite(parseFloat(hit.confidence)) ? ` (${Math.round(parseFloat(hit.confidence) * 100)}% confidence)` : '')
              + ' — open Signals for the full read.';
          }
        } catch (e) { /* signals unavailable this pass */ }
      } else if (a.metric === 'health_factor') {
        if (!doOnchain) continue;
        try {
          const { walletAddressOf } = require('./wallet');
          const defi = require('./defi');
          const address = await walletAddressOf(a.user_id);
          if (!address) continue;
          const d = await defi.getDefiPositions(address);
          const hfs = (d?.aave || []).map(x => x.health_factor).filter(h => h !== null && isFinite(h));
          if (!hfs.length) continue;
          const minHf = Math.min(...hfs);
          if (minHf < Number(a.threshold)) {
            v = minHf;
            bodyOverride = `Aave health factor is ${minHf} (threshold ${Number(a.threshold).toFixed(2)}) — `
              + 'liquidation risk is rising. Review your position in your wallet.';
          }
        } catch (e) { /* chain read unavailable this pass */ }
      } else {
        v = evaluateAlert(a, map[a.symbol]);
      }
      if (v === null) continue;
      if (!(await fireAlert(a, v))) continue;   // raced / inside cooldown
      tripped++;
      const base = String(a.symbol).replace(/USDT$/, '');
      const nowTxt = a.metric === 'price' ? fmtPrice(v) : `${Number(v).toFixed(2)}%`;
      try {
        await send({
          title: a.metric === 'health_factor' ? '🏦 DeFi risk alert'
            : a.metric === 'signal' ? '📡 Signal watch'
            : `⏰ ${base} alert tripped`,
          body: bodyOverride || `${describeCondition(a)} — ${base} is now ${nowTxt}.`,
          url: a.metric === 'signal' ? '/dashboard#signals' : '/dashboard#feed',
        }, [a.user_id]);
      } catch (e) { /* push is best-effort; the row is already stamped */ }
    }
    return tripped;
  } catch (e) {
    return 0;
  }
}

let engineTimer = null;
function startAlertEngine(intervalMs = 60_000) {
  if (engineTimer) return;
  engineTimer = setInterval(() => { runOnce().catch(() => {}); }, intervalMs);
  if (engineTimer.unref) engineTimer.unref();
}

// ── Chat handler ─────────────────────────────────────────────────────────────

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * If `text` is an alert command, handle it and return a chat-shaped reply
 * ({ reply_html, intent }); otherwise return null so the caller proxies the
 * message to the bot as usual. Never throws.
 */
async function maybeHandleAlertChat(userId, text) {
  let parsed = null;
  try {
    parsed = parseAlertCommand(text);
  } catch (e) { return null; }
  if (!parsed) return null;

  try {
    if (parsed.kind === 'list') {
      const rows = await listAlerts(userId);
      if (!rows.length) {
        return {
          reply_html: 'You have no alerts yet. Try: <i>"tell me when BTC drops below $100k"</i>.',
          intent: 'alert_list',
        };
      }
      const items = rows.slice(0, 10).map((a) => {
        const state = Number(a.active)
          ? '🟢 armed'
          : `🔔 tripped${a.trigger_price != null ? ` at ${a.metric === 'price' ? fmtPrice(a.trigger_price) : Number(a.trigger_price).toFixed(2) + '%'}` : ''}`;
        return `• <b>${esc(describeCondition(a))}</b> — ${state}`;
      });
      return {
        reply_html: `⏰ <b>Your alerts</b><br>${items.join('<br>')}<br><i>Manage them in the Live Feed view.</i>`,
        intent: 'alert_list',
      };
    }

    if (parsed.kind === 'unparsed') {
      return {
        reply_html: 'I can watch a level for you, but I didn\'t catch the condition. '
          + 'Try: <i>"tell me when BTC drops below $100k"</i>, '
          + '<i>"alert me if SOL rises above $200"</i> or '
          + '<i>"let me know when ETH moves 5%"</i>.',
        intent: 'alert_help',
      };
    }

    const r = await createAlert(userId, parsed);
    if (!r.ok) return { reply_html: esc(r.error), intent: 'alert_error' };
    const nowTxt = r.alert.metric === 'price' ? fmtPrice(r.now) : `${Number(r.now).toFixed(2)}%`;
    let hint = '';
    try {
      const [subs] = await pool.execute(
        'SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id = ?', [userId]);
      if ((subs[0]?.n || 0) === 0) {
        hint = '<br><i>Enable push notifications (Account → Notifications) so this reaches you even with the tab closed.</i>';
      }
    } catch (e) { /* hint only */ }
    const semantics = r.alert.mode === 'recurring'
      ? (r.alert.metric === 'signal'
        ? 'I\'ll push you every matching signal.'
        : `I\'ll push you each time it trips (at most once per ${r.alert.cooldown_min} min).`)
      : 'I\'ll send you a push notification the moment it trips — one-shot, then it disarms.';
    return {
      reply_html: `⏰ Alert armed: <b>${esc(describeCondition(r.alert))}</b>${nowTxt !== null && r.now !== null ? ` (now ${nowTxt})` : ''}. `
        + semantics + hint,
      intent: 'alert_create',
    };
  } catch (e) {
    return { reply_html: 'Alert system hiccup — try again in a moment.', intent: 'alert_error' };
  }
}

function __testResetOnchainSweep() { lastOnchainSweep = 0; }

module.exports = {
  MAX_ACTIVE_PER_USER,
  __testResetOnchainSweep,
  parseAlertCommand,
  evaluateAlert,
  describeCondition,
  listAlerts,
  createAlert,
  deleteAlert,
  runOnce,
  startAlertEngine,
  setTickerFetcher,
  maybeHandleAlertChat,
};

'use strict';
/**
 * Practice-follow planner — mirror the engine's live signal stream into the
 * PAPER Arena account. The user picks a per-trade margin + leverage; each new
 * signal opens a paper position at the LIVE mark (never the stale signal
 * price — honesty over flattery: your fill is what you'd actually get now).
 *
 * §4 by construction: this plans PAPER opens only — the arena has no route to
 * any live venue, and enabling follow can never move real funds. Deciding is
 * pure (signals in / plan out) so every skip rule is exactly testable; the
 * route feeds live ticker marks and does the DB writes.
 */

const arena = require('./arena');

/**
 * @param {object} ctx
 *   signals   — unprocessed signal rows, OLDEST first ({ id, symbol, direction })
 *   positions — currently open arena positions ({ symbol })
 *   balance   — free balance (margins already deducted)
 *   prefs     — { margin, leverage } the follower chose
 *   marks     — live ticker map { SYM: { price } }
 * @returns { opens: [{signal_id, symbol, direction, margin, leverage, price}],
 *            skips: [{signal_id, reason}], last_id }
 */
function planFollows(ctx = {}) {
  const signals = Array.isArray(ctx.signals) ? ctx.signals : [];
  const prefs = ctx.prefs || {};
  const marks = ctx.marks || {};
  const openSymbols = new Set((ctx.positions || []).map((p) => p.symbol));
  let balance = Number(ctx.balance) || 0;
  let slots = arena.MAX_OPEN - (ctx.positions || []).length;

  const opens = [], skips = [];
  let lastId = 0;
  for (const s of signals) {
    lastId = Math.max(lastId, Number(s.id) || 0);
    const symbol = String(s.symbol || '').toUpperCase();
    const direction = String(s.direction || '').toUpperCase();
    const margin = Number(prefs.margin), leverage = Math.round(Number(prefs.leverage));
    if (direction !== 'LONG' && direction !== 'SHORT') { skips.push({ signal_id: s.id, reason: 'direction' }); continue; }
    if (openSymbols.has(symbol)) { skips.push({ signal_id: s.id, reason: 'already_open' }); continue; }
    if (slots <= 0) { skips.push({ signal_id: s.id, reason: 'no_slot' }); continue; }
    if (!(margin >= arena.MIN_MARGIN) || balance < margin) { skips.push({ signal_id: s.id, reason: 'balance' }); continue; }
    if (!(leverage >= 1 && leverage <= arena.MAX_LEVERAGE)) { skips.push({ signal_id: s.id, reason: 'leverage' }); continue; }
    const price = marks[symbol] && Number(marks[symbol].price);
    if (!(price > 0)) { skips.push({ signal_id: s.id, reason: 'no_mark' }); continue; }
    opens.push({ signal_id: s.id, symbol, direction, margin, leverage, price });
    openSymbols.add(symbol);
    balance -= margin;
    slots -= 1;
  }
  return { opens, skips, last_id: lastId };
}

/** Validate follow prefs from the UI. */
function validateFollow(input) {
  const b = input || {};
  const enabled = !!b.enabled;
  const margin = Number(b.margin), leverage = Math.round(Number(b.leverage));
  if (enabled) {
    if (!(margin >= arena.MIN_MARGIN)) return { ok: false, error: `margin must be at least ${arena.MIN_MARGIN} vUSDT` };
    if (!(leverage >= 1 && leverage <= arena.MAX_LEVERAGE)) return { ok: false, error: `leverage must be 1–${arena.MAX_LEVERAGE}` };
  }
  return { ok: true, data: { enabled, margin: margin || arena.MIN_MARGIN, leverage: leverage || 1 } };
}

module.exports = { planFollows, validateFollow };

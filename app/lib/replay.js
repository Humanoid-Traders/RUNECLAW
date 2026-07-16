/**
 * Personal what-if replay — "what if I'd taken every signal with $1k?"
 *
 * Replays the agent's REAL closed trades (the operator account's recorded
 * history — the same rows behind the public track-record page) as if the
 * user had mirrored each one with their own stake. Per trade, the recorded
 * net PnL is scaled by (stake / recorded notional), so the replay inherits
 * the agent's actual entries, exits and fee drag — nothing is simulated.
 *
 * Two honest readings are reported side by side:
 *   fixed    — $stake on every trade (the literal question)
 *   compound — start with $stake, roll the full bankroll into each trade
 *
 * Pure computation + a thin query; read-only. Hypothetical results are
 * always labelled as such in every surface that renders them.
 */

const { pool } = require('../db');

const OPERATOR_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;
const MIN_STAKE = 10;
const MAX_STAKE = 1_000_000;

function round2(v) { return Math.round(v * 100) / 100; }

/**
 * Pure replay over closed-trade rows (chronological). Each row needs
 * pnl, size_usd, closed_at; rows without a usable notional are skipped
 * and counted, never guessed at.
 */
function computeReplay(trades, stake) {
  const s = Math.min(Math.max(Number(stake) || 1000, MIN_STAKE), MAX_STAKE);
  let skipped = 0;
  const legs = [];
  for (const t of trades) {
    const pnl = parseFloat(t.pnl);
    const size = parseFloat(t.size_usd);
    if (!isFinite(pnl) || !isFinite(size) || size <= 0) { skipped++; continue; }
    // Recorded net return per dollar of notional. Clamp at -100%: a scaled
    // replay can never lose more than the stake put on the trade.
    legs.push({
      t: t.closed_at,
      symbol: String(t.symbol || ''),
      ret: Math.max(pnl / size, -1),
    });
  }

  // Fixed: $s per trade. Equity curve = stake + cumulative PnL.
  let cum = 0, peak = s, maxDdPct = 0, wins = 0, best = null, worst = null;
  const curve = [];
  // Compound: full bankroll on each trade.
  let bank = s;
  for (const leg of legs) {
    const pnl = s * leg.ret;
    cum += pnl;
    if (leg.ret > 0) wins++;
    if (best === null || pnl > best.pnl) best = { symbol: leg.symbol, pnl };
    if (worst === null || pnl < worst.pnl) worst = { symbol: leg.symbol, pnl };
    const equity = s + cum;
    peak = Math.max(peak, equity);
    if (peak > 0) maxDdPct = Math.max(maxDdPct, (peak - equity) / peak * 100);
    curve.push({ t: new Date(leg.t).getTime(), equity: round2(equity) });
    bank = Math.max(bank * (1 + leg.ret), 0);
  }

  const n = legs.length;
  return {
    stake: s,
    trades: n,
    skipped,
    wins,
    losses: n - wins,
    win_rate_pct: n ? round2(wins / n * 100) : null,
    fixed: {
      net_pnl_usd: round2(cum),
      final_usd: round2(s + cum),
      return_pct: round2(cum / s * 100),
      max_drawdown_pct: round2(maxDdPct),
      best_trade: best ? { symbol: best.symbol, pnl_usd: round2(best.pnl) } : null,
      worst_trade: worst ? { symbol: worst.symbol, pnl_usd: round2(worst.pnl) } : null,
    },
    compound: {
      final_usd: round2(bank),
      return_pct: round2((bank - s) / s * 100),
    },
    first_trade_at: n ? legs[0].t : null,
    last_trade_at: n ? legs[n - 1].t : null,
    curve,
  };
}

/** Load the agent's closed trades (optionally windowed/filtered) and replay. */
async function runReplay({ stake = 1000, days = 0, symbol = '' } = {}) {
  // Same query shape as the public track record — full closed history,
  // oldest first, straight from what the bot recorded.
  const [rows] = await pool.execute(
    `SELECT symbol, direction, pnl, fees, size_usd, opened_at, closed_at
       FROM trades
      WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
      ORDER BY closed_at ASC`, [OPERATOR_USER_ID]);
  let trades = rows;
  const d = parseInt(days) || 0;
  if (d > 0) {
    const cutoff = Date.now() - d * 86_400_000;
    trades = trades.filter(t => new Date(t.closed_at).getTime() >= cutoff);
  }
  const base = String(symbol || '').toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '');
  if (base) {
    trades = trades.filter(t =>
      String(t.symbol || '').toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/USDT.*$/, '') === base);
  }
  return computeReplay(trades, stake);
}

// ── Chat intercept ───────────────────────────────────────────────────────────

// "what if I'd taken every signal with $1k?", "what if i traded every signal
// with 500", "replay every trade with $2k".
const CHAT_RE = new RegExp(
  '^(?:what\\s+if\\s+i(?:\'?d| had)?\\s+(?:taken|took|traded|mirrored|copied)|replay)\\s+'
  + '(?:every|all|each)\\s+(?:signal|trade|position)s?'
  + '(?:.*?\\$?([\\d][\\d,]*\\.?\\d*)\\s*(k|m)?)?', 'i');

function fmtMoney(v) {
  const n = Number(v);
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

/**
 * If `text` is a what-if ask, run the replay and return a chat-shaped reply;
 * otherwise null. Never throws.
 */
async function maybeHandleReplayChat(userId, text) {
  const m = String(text || '').trim().match(CHAT_RE);
  if (!m) return null;
  try {
    let stake = 1000;
    if (m[1]) {
      stake = parseFloat(m[1].replace(/,/g, ''));
      if ((m[2] || '').toLowerCase() === 'k') stake *= 1e3;
      if ((m[2] || '').toLowerCase() === 'm') stake *= 1e6;
    }
    const r = await runReplay({ stake });
    if (!r.trades) {
      return {
        reply_html: 'No closed agent trades recorded yet — the replay will light up '
          + 'as soon as the engine has history to mirror.',
        intent: 'replay',
      };
    }
    const f = r.fixed;
    const sign = f.net_pnl_usd >= 0 ? '🟢' : '🔴';
    return {
      reply_html:
        `📽️ <b>What-if replay</b> — ${fmtMoney(r.stake)} on every agent trade `
        + `(${r.trades} closed trades, real recorded results)<br><br>`
        + `${sign} Net: <b>${fmtMoney(f.net_pnl_usd)}</b> (${f.return_pct >= 0 ? '+' : ''}${f.return_pct}% on a per-trade stake)<br>`
        + `• Win rate: <b>${r.win_rate_pct}%</b> (${r.wins}W / ${r.losses}L)<br>`
        + `• Max drawdown: ${f.max_drawdown_pct}%<br>`
        + (f.best_trade ? `• Best: ${f.best_trade.symbol} ${fmtMoney(f.best_trade.pnl_usd)} · Worst: ${f.worst_trade.symbol} ${fmtMoney(f.worst_trade.pnl_usd)}<br>` : '')
        + `• Compounding the bankroll instead: ${fmtMoney(r.stake)} → <b>${fmtMoney(r.compound.final_usd)}</b><br><br>`
        + '<i>Hypothetical mirror of real recorded trades — past performance ≠ future results. '
        + 'The Portfolio view has the full curve and filters.</i>',
      intent: 'replay',
    };
  } catch (e) {
    return { reply_html: 'Replay hiccup — try again in a moment.', intent: 'replay' };
  }
}

module.exports = { computeReplay, runReplay, maybeHandleReplayChat };

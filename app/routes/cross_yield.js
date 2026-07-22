/**
 * GET /api/crossyield — cross-chain yield move planner (read-only).
 *
 * Composes the user's REAL idle on-chain holdings + the REAL best-available APY
 * (the idle-yield gateway) with a transparent cross-chain cost model, and
 * answers, per asset: is relocating your idle capital to the better rate worth
 * the gas + bridge cost, and after how many days does it pay for itself?
 * Recommendations only — it never moves funds. Per-user private surface, so
 * dollar figures are allowed (§4).
 */

'use strict';

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 12, key: userKey }));

router.get('/', async (req, res) => {
  const uid = req.user.user_id;
  try {
    const wallet = require('../lib/wallet');
    const { buildIdleYield } = require('../lib/idle_yield');
    const { planMoves } = require('../lib/cross_yield');

    const address = await wallet.walletAddressOf(uid).catch(() => null);
    if (!address) {
      return res.json({
        read_only: true, available: true, wallet_linked: false, plans: [],
        note: 'Link a wallet (Sign-In with Ethereum) to plan cross-chain yield '
          + 'moves for your idle on-chain capital.',
      });
    }

    // Where each idle asset mostly sits (its dominant chain) — for the gas
    // anchor — plus live native-token prices to dollarise the gas estimate.
    const dominant = {};
    let nativePrices = {};
    try {
      const p = await wallet.getWalletPortfolio(address);
      const byAsset = {};
      for (const ch of (p.chains || [])) {
        for (const a of (ch.assets || [])) {
          const sym = String(a.symbol || '').toUpperCase();
          const usd = Number(a.usd) || 0;
          if (!byAsset[sym] || usd > byAsset[sym].usd) byAsset[sym] = { chain: ch.chain, usd };
        }
      }
      for (const k of Object.keys(byAsset)) dominant[k] = byAsset[k].chain;
    } catch (_) { /* dominant stays empty → cost model uses the typical anchor */ }
    try {
      const tickers = await require('../lib/tickers').getTickers();
      const px = (s) => (tickers[s] && Number(tickers[s].price)) || 0;
      nativePrices = {
        ETH: px('ETHUSDT'), BNB: px('BNBUSDT'), SOL: px('SOLUSDT'),
        POL: px('POLUSDT') || px('MATICUSDT'),
      };
    } catch (_) { /* prices empty → gas falls back to the flat anchor */ }

    const y = await buildIdleYield(await resolveBotIdentity(req).catch(() => ({ id: `web:${uid}` })), uid);
    if (!y || !y.available) {
      return res.json({
        read_only: true, available: false, plans: [],
        note: 'The yield planner is briefly unavailable — try again shortly.',
      });
    }

    const horizonDays = Math.min(365, Math.max(7, parseInt(req.query.horizon, 10) || 90));
    const items = (y.recommendations || [])
      .filter((r) => r && r.status === 'recommended' && Number(r.idle_usd) > 0 && r.best)
      .map((r) => ({
        asset: r.asset,
        amount_usd: Number(r.idle_usd) || 0,
        from_chain: dominant[String(r.asset).toUpperCase()] || null,
        current_apy: 0,                       // idle wallet cash earns ~nothing
        best_apy: Number(r.best.apy) || 0,
        best_source: r.best.source,
        custodial: !!r.best.custodial,
        lockup_days: Number(r.best.lockup_days) || 0,
      }));

    const out = planMoves(items, { nativePrices, horizonDays });
    out.available = true;
    out.wallet_linked = true;
    out.generated_at = new Date().toISOString();
    if (!items.length) {
      out.note = y.note || 'No idle assets matched a known rate right now — nothing to plan.';
    }
    return res.json(out);
  } catch (err) {
    console.error('Cross-yield planner error:', err.message);
    return res.status(502).json({ error: 'Yield planner unavailable' });
  }
});

module.exports = router;

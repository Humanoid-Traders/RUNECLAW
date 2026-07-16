/**
 * On-chain wallet portfolio — STRICTLY READ-ONLY.
 *
 * Shows the balances behind a user's SIWE-linked wallet: native ETH plus a
 * curated set of majors/RWA tokens on Ethereum mainnet, priced through the
 * same live venue tickers the rest of the app uses. Only provider READ
 * calls are made (eth_getBalance / eth_call balanceOf) — this module can
 * never sign, send, or approve anything, and no private key is ever seen.
 * Non-custodial execution remains design-only pending operator + legal
 * review; this is a mirror, not a control surface.
 */

const { pool } = require('../db');
const { getTickers } = require('./tickers');

const RPC_URL = process.env.WEB3_RPC_URL || 'https://cloudflare-eth.com';
const CACHE_MS = 60_000;

// Curated mainnet ERC-20s (symbol → contract, decimals, pricing ticker).
// Small on purpose: every entry is a token we can price honestly off the
// venue's own tickers (stables pinned at $1).
const TOKENS = [
  { symbol: 'USDT', address: '0xdAC17F958D2ee523a2206206994597C13D831ec7', decimals: 6, stable: true },
  { symbol: 'USDC', address: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6, stable: true },
  { symbol: 'WBTC', address: '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599', decimals: 8, ticker: 'BTCUSDT' },
  { symbol: 'WETH', address: '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2', decimals: 18, ticker: 'ETHUSDT' },
  { symbol: 'LINK', address: '0x514910771AF9Ca656af840dff83E8264EcF986CA', decimals: 18, ticker: 'LINKUSDT' },
  { symbol: 'AAVE', address: '0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9', decimals: 18, ticker: 'AAVEUSDT' },
  { symbol: 'ONDO', address: '0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3', decimals: 18, ticker: 'ONDOUSDT' },
  { symbol: 'PENDLE', address: '0x808507121B80c02388fAd14726482e061B8da827', decimals: 18, ticker: 'PENDLEUSDT' },
];

const ERC20_ABI = ['function balanceOf(address) view returns (uint256)'];

function round2(v) { return Math.round(v * 100) / 100; }

// ── Injectable seams (tests / alternate infra) ──────────────────────────────

function defaultProviderFactory() {
  const { ethers } = require('ethers');
  return new ethers.JsonRpcProvider(RPC_URL);
}
let providerFactory = defaultProviderFactory;
function setProviderFactory(fn) { providerFactory = fn || defaultProviderFactory; }

let fetchTickers = getTickers;
function setTickerFetcher(fn) { fetchTickers = fn || getTickers; }

// ── Portfolio read ───────────────────────────────────────────────────────────

const cache = new Map();   // address(lower) -> { at, portfolio }

async function readWallet(address) {
  const { ethers } = require('ethers');
  const provider = providerFactory();
  const assets = [];

  let tickers = {};
  try { tickers = await fetchTickers(); } catch (e) { /* price as null below */ }
  const priceOf = (t) => {
    if (t.stable) return 1;
    const tk = tickers[t.ticker];
    return tk && isFinite(tk.price) ? tk.price : null;
  };

  // Native ETH.
  try {
    const wei = await provider.getBalance(address);
    const amount = parseFloat(ethers.formatEther(wei));
    if (amount > 0) {
      const p = tickers.ETHUSDT && isFinite(tickers.ETHUSDT.price) ? tickers.ETHUSDT.price : null;
      assets.push({ symbol: 'ETH', amount, price_usd: p, usd: p !== null ? round2(amount * p) : null });
    }
  } catch (e) { /* fail-soft per asset */ }

  // Curated ERC-20s — each read fails soft; a flaky token never sinks the view.
  for (const t of TOKENS) {
    try {
      const c = new ethers.Contract(t.address, ERC20_ABI, provider);
      const raw = await c.balanceOf(address);
      const amount = parseFloat(ethers.formatUnits(raw, t.decimals));
      if (amount <= 0) continue;
      const p = priceOf(t);
      assets.push({ symbol: t.symbol, amount, price_usd: p, usd: p !== null ? round2(amount * p) : null });
    } catch (e) { /* skip */ }
  }

  assets.sort((a, b) => (b.usd || 0) - (a.usd || 0));
  const priced = assets.filter(a => a.usd !== null);
  return {
    read_only: true,
    address,
    chain: 'ethereum',
    assets,
    total_usd: round2(priced.reduce((a, x) => a + x.usd, 0)),
    unpriced: assets.length - priced.length,
    generated_at: new Date().toISOString(),
  };
}

async function getWalletPortfolio(address) {
  const key = String(address || '').toLowerCase();
  if (!/^0x[0-9a-f]{40}$/.test(key)) return null;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.portfolio;
  const portfolio = await readWallet(address);
  cache.set(key, { at: Date.now(), portfolio });
  if (cache.size > 500) cache.delete(cache.keys().next().value);
  return portfolio;
}

async function walletAddressOf(userId) {
  const [rows] = await pool.execute('SELECT * FROM users WHERE id = ?', [userId]);
  return rows.length ? (rows[0].wallet_address || null) : null;
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b(my wallet|wallet (balance|portfolio|holdings)|on[- ]chain (balance|portfolio|holdings))\b/i;

function fmtUsd(v) {
  return v == null ? '—'
    : '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

async function maybeHandleWalletChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const address = await walletAddressOf(userId);
    if (!address) {
      return {
        reply_html: 'No wallet is linked to your account yet — connect one with '
          + '<b>Sign-In with Ethereum</b> (Account view). Linking is read-only: '
          + 'the wallet only ever signs a login message, never a transaction.',
        intent: 'wallet',
      };
    }
    const p = await getWalletPortfolio(address);
    if (!p) return { reply_html: 'That wallet address doesn\'t look readable.', intent: 'wallet' };
    const short = `${address.slice(0, 6)}…${address.slice(-4)}`;
    if (!p.assets.length) {
      return {
        reply_html: `👛 <b>${short}</b> — no balances found among the tracked assets (ETH + ${TOKENS.length} majors).`,
        intent: 'wallet',
      };
    }
    const rows = p.assets.slice(0, 8).map(a =>
      `• <b>${a.symbol}</b> ${a.amount.toLocaleString('en-US', { maximumFractionDigits: 6 })} — ${fmtUsd(a.usd)}`);
    return {
      reply_html: `👛 <b>On-chain wallet ${short}</b> (read-only mirror)<br><br>`
        + rows.join('<br>')
        + `<br><br>Total (priced): <b>${fmtUsd(p.total_usd)}</b>`
        + (p.unpriced ? ` · ${p.unpriced} asset(s) unpriced` : '')
        + '<br><i>Balances read straight from the chain; RUNECLAW can never move them.</i>',
      intent: 'wallet',
    };
  } catch (e) {
    return { reply_html: 'Wallet read hiccup — the RPC may be busy; try again shortly.', intent: 'wallet' };
  }
}

module.exports = {
  TOKENS,
  getWalletPortfolio,
  walletAddressOf,
  setProviderFactory,
  setTickerFetcher,
  maybeHandleWalletChat,
};

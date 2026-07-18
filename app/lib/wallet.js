/**
 * On-chain wallet portfolio — STRICTLY READ-ONLY, now MULTI-CHAIN.
 *
 * Shows the balances behind a user's SIWE-linked wallet across Ethereum
 * mainnet and the major L2s/sidechains (Base, Arbitrum, Optimism, Polygon):
 * native coin plus a curated set of majors per chain, priced through the
 * same live venue tickers the rest of the app uses. Only provider READ
 * calls are made (eth_getBalance / eth_call balanceOf) — this module can
 * never sign, send, or approve anything, and no private key is ever seen.
 * Non-custodial execution remains design-only pending operator + legal
 * review; this is a mirror, not a control surface.
 *
 * Every chain fails soft and independently: one unreachable RPC degrades
 * that chain's section (marked unreadable), never the whole view. RPCs are
 * public defaults, each overridable via WEB3_RPC_URL_<CHAIN>; the active
 * chain set itself is controlled by WEB3_CHAINS (comma list) so a
 * deployment can trim to the chains it cares about.
 */

const { pool } = require('../db');
const { getTickers } = require('./tickers');

const CACHE_MS = 60_000;

// Curated per-chain assets (symbol → contract, decimals, pricing ticker).
// Small on purpose: every entry is a token we can price honestly off the
// venue's own tickers (stables pinned at $1). Wrapped majors price off the
// underlying (WETH→ETHUSDT, WBTC/cbBTC→BTCUSDT).
const CHAINS = [
  {
    key: 'ethereum', label: 'Ethereum', chainId: 1,
    rpcEnv: 'WEB3_RPC_URL', rpcDefault: 'https://cloudflare-eth.com',
    native: { symbol: 'ETH', ticker: 'ETHUSDT' },
    tokens: [
      { symbol: 'USDT', address: '0xdAC17F958D2ee523a2206206994597C13D831ec7', decimals: 6, stable: true },
      { symbol: 'USDC', address: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6, stable: true },
      { symbol: 'WBTC', address: '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599', decimals: 8, ticker: 'BTCUSDT' },
      { symbol: 'WETH', address: '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2', decimals: 18, ticker: 'ETHUSDT' },
      { symbol: 'LINK', address: '0x514910771AF9Ca656af840dff83E8264EcF986CA', decimals: 18, ticker: 'LINKUSDT' },
      { symbol: 'AAVE', address: '0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9', decimals: 18, ticker: 'AAVEUSDT' },
      { symbol: 'ONDO', address: '0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3', decimals: 18, ticker: 'ONDOUSDT' },
      { symbol: 'PENDLE', address: '0x808507121B80c02388fAd14726482e061B8da827', decimals: 18, ticker: 'PENDLEUSDT' },
    ],
  },
  {
    key: 'base', label: 'Base', chainId: 8453,
    rpcEnv: 'WEB3_RPC_URL_BASE', rpcDefault: 'https://mainnet.base.org',
    native: { symbol: 'ETH', ticker: 'ETHUSDT' },
    tokens: [
      { symbol: 'USDC', address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', decimals: 6, stable: true },
      { symbol: 'DAI', address: '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb', decimals: 18, stable: true },
      { symbol: 'WETH', address: '0x4200000000000000000000000000000000000006', decimals: 18, ticker: 'ETHUSDT' },
      { symbol: 'cbBTC', address: '0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf', decimals: 8, ticker: 'BTCUSDT' },
    ],
  },
  {
    key: 'arbitrum', label: 'Arbitrum', chainId: 42161,
    rpcEnv: 'WEB3_RPC_URL_ARBITRUM', rpcDefault: 'https://arb1.arbitrum.io/rpc',
    native: { symbol: 'ETH', ticker: 'ETHUSDT' },
    tokens: [
      { symbol: 'USDC', address: '0xaf88d065e77c8cC2239327C5EDb3A432268e5831', decimals: 6, stable: true },
      { symbol: 'USDT', address: '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9', decimals: 6, stable: true },
      { symbol: 'WETH', address: '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1', decimals: 18, ticker: 'ETHUSDT' },
      { symbol: 'WBTC', address: '0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f', decimals: 8, ticker: 'BTCUSDT' },
      { symbol: 'ARB', address: '0x912CE59144191C1204E64559FE8253a0e49E6548', decimals: 18, ticker: 'ARBUSDT' },
      { symbol: 'LINK', address: '0xf97f4df75117a78c1A5a0DBb814Af92458539FB4', decimals: 18, ticker: 'LINKUSDT' },
    ],
  },
  {
    key: 'optimism', label: 'Optimism', chainId: 10,
    rpcEnv: 'WEB3_RPC_URL_OPTIMISM', rpcDefault: 'https://mainnet.optimism.io',
    native: { symbol: 'ETH', ticker: 'ETHUSDT' },
    tokens: [
      { symbol: 'USDC', address: '0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85', decimals: 6, stable: true },
      { symbol: 'USDT', address: '0x94b008aA00579c1307B0EF2c499aD98a8ce58e58', decimals: 6, stable: true },
      { symbol: 'WETH', address: '0x4200000000000000000000000000000000000006', decimals: 18, ticker: 'ETHUSDT' },
      { symbol: 'WBTC', address: '0x68f180fcCe6836688e9084f035309E29Bf0A2095', decimals: 8, ticker: 'BTCUSDT' },
      { symbol: 'OP', address: '0x4200000000000000000000000000000000000042', decimals: 18, ticker: 'OPUSDT' },
    ],
  },
  {
    key: 'polygon', label: 'Polygon', chainId: 137,
    rpcEnv: 'WEB3_RPC_URL_POLYGON', rpcDefault: 'https://polygon-rpc.com',
    native: { symbol: 'POL', ticker: 'POLUSDT' },
    tokens: [
      { symbol: 'USDC', address: '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359', decimals: 6, stable: true },
      { symbol: 'USDT', address: '0xc2132D05D31c914a87C6611C10748AEb04B58e8F', decimals: 6, stable: true },
      { symbol: 'WETH', address: '0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619', decimals: 18, ticker: 'ETHUSDT' },
      { symbol: 'WBTC', address: '0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6', decimals: 8, ticker: 'BTCUSDT' },
      { symbol: 'LINK', address: '0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39', decimals: 18, ticker: 'LINKUSDT' },
    ],
  },
];

// Active chain set: WEB3_CHAINS="ethereum,base" trims the sweep. Unknown
// names are ignored; an empty/invalid list falls back to all chains.
function activeChains() {
  const raw = String(process.env.WEB3_CHAINS || '').trim();
  if (!raw) return CHAINS;
  const want = new Set(raw.toLowerCase().split(',').map(s => s.trim()).filter(Boolean));
  const picked = CHAINS.filter(c => want.has(c.key));
  return picked.length ? picked : CHAINS;
}

// Legacy export: the Ethereum token list (tests/consumers referenced it).
const TOKENS = CHAINS[0].tokens;

const ERC20_ABI = ['function balanceOf(address) view returns (uint256)'];

function round2(v) { return Math.round(v * 100) / 100; }

// ── Injectable seams (tests / alternate infra) ──────────────────────────────

const providerCache = new Map();  // chainKey -> provider
function defaultProviderFactory(chain) {
  const { ethers } = require('ethers');
  let p = providerCache.get(chain.key);
  if (!p) {
    const url = process.env[chain.rpcEnv] || chain.rpcDefault;
    p = new ethers.JsonRpcProvider(url);
    providerCache.set(chain.key, p);
  }
  return p;
}
// The factory receives the chain descriptor; a factory that ignores it (the
// pre-multichain test shape) simply serves every chain from one provider.
let providerFactory = defaultProviderFactory;
function setProviderFactory(fn) { providerFactory = fn || defaultProviderFactory; }

let fetchTickers = getTickers;
function setTickerFetcher(fn) { fetchTickers = fn || getTickers; }

// ── Portfolio read ───────────────────────────────────────────────────────────

const cache = new Map();   // address(lower) -> { at, portfolio }

/** Read one chain's balances. Never throws: an unreachable RPC returns an
 * empty section flagged `error` so the multi-chain view stays honest. */
async function readChain(chain, address, tickers) {
  const { ethers } = require('ethers');
  const assets = [];
  const priceOf = (t) => {
    if (t.stable) return 1;
    const tk = tickers[t.ticker];
    return tk && isFinite(tk.price) ? tk.price : null;
  };
  let provider;
  try { provider = providerFactory(chain); } catch (e) {
    return { chain: chain.key, label: chain.label, assets: [], total_usd: 0, unpriced: 0, error: 'rpc unavailable' };
  }
  let sawError = false;

  // Native coin.
  try {
    const wei = await provider.getBalance(address);
    const amount = parseFloat(ethers.formatEther(wei));
    if (amount > 0) {
      const tk = tickers[chain.native.ticker];
      const p = tk && isFinite(tk.price) ? tk.price : null;
      assets.push({ symbol: chain.native.symbol, chain: chain.key, amount,
        price_usd: p, usd: p !== null ? round2(amount * p) : null });
    }
  } catch (e) { sawError = true; }

  // Curated tokens — each read fails soft; a flaky token never sinks the view.
  for (const t of chain.tokens) {
    try {
      const c = new ethers.Contract(t.address, ERC20_ABI, provider);
      const raw = await c.balanceOf(address);
      const amount = parseFloat(ethers.formatUnits(raw, t.decimals));
      if (amount <= 0) continue;
      const p = priceOf(t);
      assets.push({ symbol: t.symbol, chain: chain.key, amount,
        price_usd: p, usd: p !== null ? round2(amount * p) : null });
    } catch (e) { sawError = true; }
  }

  assets.sort((a, b) => (b.usd || 0) - (a.usd || 0));
  const priced = assets.filter(a => a.usd !== null);
  return {
    chain: chain.key,
    label: chain.label,
    assets,
    total_usd: round2(priced.reduce((a, x) => a + x.usd, 0)),
    unpriced: assets.length - priced.length,
    ...(sawError && !assets.length ? { error: 'rpc unreadable' } : {}),
  };
}

async function readWallet(address) {
  let tickers = {};
  try { tickers = await fetchTickers(); } catch (e) { /* price as null below */ }

  const chains = await Promise.all(
    activeChains().map(c => readChain(c, address, tickers)));

  // Flattened view preserves the pre-multichain shape (each asset now also
  // carries its `chain`) — networth sums total_usd, exposure nets assets,
  // both unchanged.
  const assets = chains.flatMap(c => c.assets).sort((a, b) => (b.usd || 0) - (a.usd || 0));
  const priced = assets.filter(a => a.usd !== null);
  return {
    read_only: true,
    address,
    chain: 'multi',
    chains,
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

// "my wallet", "wallet balance", … with an optional trailing chain filter:
// "my wallet on base", "wallet holdings on arbitrum".
const CHAT_RE = /\b(my wallet|wallet (?:balance|portfolio|holdings)|on[- ]chain (?:balance|portfolio|holdings))\b(?:\s+on\s+([a-z]+))?/i;

function fmtUsd(v) {
  return v == null ? '—'
    : '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

async function maybeHandleWalletChat(userId, text) {
  const m = String(text || '').match(CHAT_RE);
  if (!m) return null;
  try {
    const chainFilter = m[2] ? String(m[2]).toLowerCase() : null;
    if (chainFilter && !CHAINS.some(c => c.key === chainFilter)) {
      return {
        reply_html: `I don't mirror <b>${chainFilter.replace(/[^a-z]/g, '')}</b> yet — tracked chains: `
          + CHAINS.map(c => c.label).join(', ') + '.',
        intent: 'wallet',
      };
    }
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

    const sections = (p.chains || [])
      .filter(c => !chainFilter || c.chain === chainFilter);
    const withAssets = sections.filter(c => c.assets.length);
    if (!withAssets.length) {
      const scope = chainFilter
        ? `on ${sections[0] ? sections[0].label : chainFilter}` : 'across the tracked chains';
      return {
        reply_html: `👛 <b>${short}</b> — no balances found ${scope} among the tracked assets.`,
        intent: 'wallet',
      };
    }
    const blocks = withAssets.map(c => {
      const rows = c.assets.slice(0, 6).map(a =>
        `• <b>${a.symbol}</b> ${a.amount.toLocaleString('en-US', { maximumFractionDigits: 6 })} — ${fmtUsd(a.usd)}`);
      return `<b>${c.label}</b> · ${fmtUsd(c.total_usd)}<br>${rows.join('<br>')}`;
    });
    const total = chainFilter
      ? withAssets.reduce((a, c) => a + (c.total_usd || 0), 0) : p.total_usd;
    const unreadable = sections.filter(c => c.error).map(c => c.label);
    return {
      reply_html: `👛 <b>On-chain wallet ${short}</b> (read-only mirror)<br><br>`
        + blocks.join('<br><br>')
        + `<br><br>Total (priced): <b>${fmtUsd(round2(total))}</b>`
        + (p.unpriced && !chainFilter ? ` · ${p.unpriced} asset(s) unpriced` : '')
        + (unreadable.length ? `<br><span class="muted">${unreadable.join(', ')} unreadable right now (RPC).</span>` : '')
        + '<br><i>Balances read straight from the chain; RUNECLAW can never move them.</i>',
      intent: 'wallet',
    };
  } catch (e) {
    return { reply_html: 'Wallet read hiccup — the RPC may be busy; try again shortly.', intent: 'wallet' };
  }
}

module.exports = {
  TOKENS,
  CHAINS,
  activeChains,
  getWalletPortfolio,
  walletAddressOf,
  setProviderFactory,
  setTickerFetcher,
  maybeHandleWalletChat,
};

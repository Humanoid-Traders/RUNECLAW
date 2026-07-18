/**
 * DeFi position intelligence — STRICTLY READ-ONLY.
 *
 * Reads the caller's SIWE-linked wallet's positions straight from protocol
 * contracts across the tracked chains:
 *   • Aave v3      — Pool.getUserAccountData: collateral, debt, health
 *                    factor → liquidation-risk warnings
 *   • Lido         — stETH balance on mainnet (priced as ETH, stated)
 *   • Uniswap v3   — LP position COUNT via the NonfungiblePositionManager
 *                    (honestly NOT valued: fair LP valuation needs tick
 *                    math this module doesn't pretend to do)
 *
 * Only view calls are made. This module can never sign, send, approve, or
 * manage a position — it warns, the user acts in their own wallet.
 * Every chain and every protocol read fails soft and independently.
 */

const { getTickers } = require('./tickers');
const { CHAINS, activeChains, walletAddressOf } = require('./wallet');

const CACHE_MS = 60_000;

// Canonical Aave v3 Pool per chain (absent chain → no Aave read there).
const AAVE_POOLS = {
  ethereum: '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2',
  polygon: '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
  arbitrum: '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
  optimism: '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
  base: '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5',
};
// Aave v3 base currency is USD with 8 decimals; health factor is 1e18 and
// uint256-max means "no debt" (infinite health).
const AAVE_BASE_DECIMALS = 8n;
const HF_NO_DEBT_THRESHOLD = 1e6;

const LIDO_STETH = '0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84'; // mainnet

// Uniswap v3 NonfungiblePositionManager.
const UNIV3_NPM = {
  ethereum: '0xC36442b4a4522E871399CD717aBDD847Ab11FE88',
  arbitrum: '0xC36442b4a4522E871399CD717aBDD847Ab11FE88',
  optimism: '0xC36442b4a4522E871399CD717aBDD847Ab11FE88',
  polygon: '0xC36442b4a4522E871399CD717aBDD847Ab11FE88',
  base: '0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1',
};

const AAVE_POOL_ABI = [
  'function getUserAccountData(address) view returns (uint256 totalCollateralBase, uint256 totalDebtBase, uint256 availableBorrowsBase, uint256 currentLiquidationThreshold, uint256 ltv, uint256 healthFactor)',
];
const ERC20_ABI = ['function balanceOf(address) view returns (uint256)'];

function round2(v) { return Math.round(v * 100) / 100; }

// ── Injectable seams (tests / alternate infra) ──────────────────────────────

const providerCache = new Map();
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
let providerFactory = defaultProviderFactory;
function setProviderFactory(fn) { providerFactory = fn || defaultProviderFactory; }

let fetchTickers = getTickers;
function setTickerFetcher(fn) { fetchTickers = fn || getTickers; }

// ── Reads (all view calls, all fail-soft) ───────────────────────────────────

function baseToUsd(raw) {
  // 8-decimal USD base → float dollars.
  return Number(raw / 10n ** (AAVE_BASE_DECIMALS - 2n)) / 100;
}

async function readAave(chain, address) {
  const pool = AAVE_POOLS[chain.key];
  if (!pool) return null;
  const { ethers } = require('ethers');
  const c = new ethers.Contract(pool, AAVE_POOL_ABI, providerFactory(chain));
  const d = await c.getUserAccountData(address);
  const collateral = baseToUsd(BigInt(d[0]));
  const debt = baseToUsd(BigInt(d[1]));
  if (collateral <= 0 && debt <= 0) return null;   // no position on this chain
  const hfRaw = Number(BigInt(d[5])) / 1e18;
  const healthFactor = debt <= 0 || hfRaw > HF_NO_DEBT_THRESHOLD ? null : round2(hfRaw);
  return {
    chain: chain.key,
    label: chain.label,
    collateral_usd: round2(collateral),
    debt_usd: round2(debt),
    available_borrow_usd: round2(baseToUsd(BigInt(d[2]))),
    // null health factor = no debt = nothing to liquidate.
    health_factor: healthFactor,
    ltv_pct: round2(Number(BigInt(d[4])) / 100),
  };
}

async function readLido(address, tickers) {
  const chain = CHAINS.find(c => c.key === 'ethereum');
  const { ethers } = require('ethers');
  const c = new ethers.Contract(LIDO_STETH, ERC20_ABI, providerFactory(chain));
  const raw = await c.balanceOf(address);
  const amount = parseFloat(ethers.formatUnits(raw, 18));
  if (amount <= 0) return null;
  const tk = tickers.ETHUSDT;
  const p = tk && isFinite(tk.price) ? tk.price : null;
  return {
    steth_amount: amount,
    usd: p !== null ? round2(amount * p) : null,
    pricing_note: 'priced at the ETH ticker — stETH trades ≈ ETH, not exactly',
  };
}

async function readUniswapCount(chain, address) {
  const npm = UNIV3_NPM[chain.key];
  if (!npm) return null;
  const { ethers } = require('ethers');
  const c = new ethers.Contract(npm, ERC20_ABI, providerFactory(chain));
  const n = Number(await c.balanceOf(address));
  if (!n) return null;
  return { chain: chain.key, label: chain.label, positions: n };
}

/** Compose the full read-only DeFi picture for one address. */
async function buildDefiPositions(address) {
  let tickers = {};
  try { tickers = await fetchTickers(); } catch (e) { /* lido priced null */ }

  const chains = activeChains();
  const [aaveRes, uniRes, lidoRes] = await Promise.all([
    Promise.all(chains.map(c => readAave(c, address).catch(() => undefined))),
    Promise.all(chains.map(c => readUniswapCount(c, address).catch(() => undefined))),
    chains.some(c => c.key === 'ethereum')
      ? readLido(address, tickers).catch(() => undefined) : null,
  ]);

  const aave = aaveRes.filter(Boolean);
  const uniswap = uniRes.filter(Boolean);
  const lido = lidoRes || null;

  // What a risk desk would say about the lending book.
  const warnings = [];
  for (const a of aave) {
    if (a.health_factor === null) continue;   // no debt
    if (a.health_factor < 1.1) {
      warnings.push(`CRITICAL: Aave health factor ${a.health_factor} on ${a.label} — `
        + 'liquidation is imminent on a small move. Repay debt or add collateral now.');
    } else if (a.health_factor < 1.5) {
      warnings.push(`Aave health factor ${a.health_factor} on ${a.label} is thin — `
        + 'a sharp move could put the position at liquidation risk.');
    }
  }

  return {
    read_only: true,
    address,
    aave,
    lido,
    uniswap,
    warnings,
    note: 'Read straight from protocol contracts. RUNECLAW can warn — it can never '
      + 'repay, withdraw, or manage a position; that stays in your wallet. Uniswap LPs '
      + 'are counted, not valued (fair LP valuation needs tick math we will not fake).',
    generated_at: new Date().toISOString(),
  };
}

const cache = new Map();   // address(lower) -> { at, positions }

async function getDefiPositions(address) {
  const key = String(address || '').toLowerCase();
  if (!/^0x[0-9a-f]{40}$/.test(key)) return null;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.positions;
  const positions = await buildDefiPositions(address);
  cache.set(key, { at: Date.now(), positions });
  if (cache.size > 500) cache.delete(cache.keys().next().value);
  return positions;
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b(my )?(defi( positions| status| health)?|aave( positions| health)?|health factor)\b/i;

function fmtUsd(v) {
  return v == null ? '—'
    : '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

async function maybeHandleDefiChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const address = await walletAddressOf(userId);
    if (!address) {
      return {
        reply_html: 'No wallet is linked yet — link one in the Account view '
          + '(one signed login message, never a transaction) and I can read your '
          + 'Aave, Lido and Uniswap positions straight from the chain.',
        intent: 'defi',
      };
    }
    const d = await getDefiPositions(address);
    if (!d) return { reply_html: 'That wallet address doesn\'t look readable.', intent: 'defi' };
    const short = `${address.slice(0, 6)}…${address.slice(-4)}`;
    const parts = [];
    for (const a of d.aave) {
      parts.push(`<b>Aave v3 · ${a.label}</b><br>`
        + `Collateral ${fmtUsd(a.collateral_usd)} · Debt ${fmtUsd(a.debt_usd)}`
        + (a.health_factor !== null
          ? ` · Health factor <b>${a.health_factor}</b>` : ' · no debt — nothing to liquidate'));
    }
    if (d.lido) {
      parts.push(`<b>Lido</b><br>stETH ${d.lido.steth_amount.toLocaleString('en-US', { maximumFractionDigits: 6 })}`
        + ` — ${fmtUsd(d.lido.usd)} <i>(${d.lido.pricing_note})</i>`);
    }
    for (const u of d.uniswap) {
      parts.push(`<b>Uniswap v3 · ${u.label}</b><br>${u.positions} LP position(s) — counted, not valued.`);
    }
    if (!parts.length) {
      return {
        reply_html: `🏦 <b>${short}</b> — no Aave, Lido or Uniswap v3 positions found on the tracked chains.`,
        intent: 'defi',
      };
    }
    const warn = d.warnings.length
      ? '<br><br>⚠️ ' + d.warnings.map(w => `<b>${w}</b>`).join('<br>⚠️ ') : '';
    return {
      reply_html: `🏦 <b>DeFi positions — ${short}</b> (read-only)<br><br>`
        + parts.join('<br><br>') + warn
        + `<br><br><i>${d.note}</i>`,
      intent: 'defi',
    };
  } catch (e) {
    return { reply_html: 'DeFi read hiccup — an RPC may be busy; try again shortly.', intent: 'defi' };
  }
}

module.exports = {
  AAVE_POOLS,
  buildDefiPositions,
  getDefiPositions,
  maybeHandleDefiChat,
  setProviderFactory,
  setTickerFetcher,
};

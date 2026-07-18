/**
 * Solana wallet mirror — STRICTLY READ-ONLY.
 *
 * Solana is not EVM, so it can't ride the ethers-based multi-chain registry
 * in lib/wallet.js. This module speaks raw Solana JSON-RPC over fetch (no new
 * dependency): getBalance for native SOL and getTokenAccountsByOwner for a
 * curated set of SPL majors, priced through the same live venue tickers as
 * everything else. Only read calls exist here — there is no keypair, no
 * transaction building, and nothing to sign with; the linked address is a
 * WATCH address the user types in (Sign-In with Ethereum stays EVM-only, so
 * Solana linking is honest about being unauthenticated watch-only).
 *
 * Fails soft like the EVM chains: an unreachable RPC returns a section
 * flagged `error`, never a thrown 500.
 */

const CACHE_MS = 60_000;

const RPC_ENV = 'WEB3_RPC_URL_SOLANA';
const RPC_DEFAULT = 'https://api.mainnet-beta.solana.com';
const LAMPORTS_PER_SOL = 1_000_000_000;
const TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';

// Curated SPL majors (mint → symbol/pricing). Small on purpose — every entry
// prices honestly off the venue's own tickers (stables pinned at $1).
const SPL_TOKENS = [
  { symbol: 'USDC', mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', stable: true },
  { symbol: 'USDT', mint: 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', stable: true },
  { symbol: 'JUP', mint: 'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN', ticker: 'JUPUSDT' },
  { symbol: 'WIF', mint: 'EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm', ticker: 'WIFUSDT' },
  { symbol: 'BONK', mint: 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', ticker: 'BONKUSDT' },
];

// ── Address validation (base58, 32-byte ed25519 pubkey) ─────────────────────

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const B58_MAP = Object.fromEntries([...B58].map((c, i) => [c, BigInt(i)]));

/** True iff `addr` decodes as a 32-byte base58 value (a Solana pubkey). */
function isSolanaAddress(addr) {
  const s = String(addr || '');
  if (s.length < 32 || s.length > 44) return false;
  let n = 0n;
  for (const c of s) {
    const v = B58_MAP[c];
    if (v === undefined) return false;
    n = n * 58n + v;
  }
  // Decoded byte length: leading '1's are zero bytes, the rest from the bigint.
  let bytes = 0;
  for (const c of s) { if (c === '1') bytes++; else break; }
  let m = n;
  while (m > 0n) { bytes++; m >>= 8n; }
  return bytes === 32;
}

// ── Injectable seams (tests / alternate infra) ──────────────────────────────

async function defaultRpcCall(method, params) {
  const url = process.env[RPC_ENV] || RPC_DEFAULT;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error(`solana rpc ${res.status}`);
  const j = await res.json();
  if (j.error) throw new Error(j.error.message || 'solana rpc error');
  return j.result;
}
let rpcCall = defaultRpcCall;
function setRpcCall(fn) { rpcCall = fn || defaultRpcCall; }

let fetchTickers = null;
function setTickerFetcher(fn) { fetchTickers = fn; }
function getTickerFetcher() {
  return fetchTickers || require('./tickers').getTickers;
}

// ── Portfolio read ───────────────────────────────────────────────────────────

function round2(v) { return Math.round(v * 100) / 100; }

const cache = new Map();   // address -> { at, portfolio }

/** Read the Solana section for `address`. Shape mirrors one lib/wallet.js
 * chain section so consumers can list it alongside the EVM chains. */
async function readSolana(address, tickers) {
  const assets = [];
  let sawError = false;

  try {
    const bal = await rpcCall('getBalance', [address]);
    const lamports = typeof bal === 'object' && bal !== null ? bal.value : bal;
    const amount = Number(lamports || 0) / LAMPORTS_PER_SOL;
    if (amount > 0) {
      const tk = tickers.SOLUSDT;
      const p = tk && isFinite(tk.price) ? tk.price : null;
      assets.push({ symbol: 'SOL', chain: 'solana', amount,
        price_usd: p, usd: p !== null ? round2(amount * p) : null });
    }
  } catch (e) { sawError = true; }

  try {
    const res = await rpcCall('getTokenAccountsByOwner',
      [address, { programId: TOKEN_PROGRAM }, { encoding: 'jsonParsed' }]);
    const byMint = new Map(SPL_TOKENS.map(t => [t.mint, t]));
    for (const acct of (res && res.value) || []) {
      const info = acct?.account?.data?.parsed?.info;
      const t = info && byMint.get(info.mint);
      if (!t) continue;   // only the curated majors — everything else is unpriceable noise
      const amount = Number(info.tokenAmount?.uiAmount || 0);
      if (amount <= 0) continue;
      let p = null;
      if (t.stable) p = 1;
      else {
        const tk = tickers[t.ticker];
        p = tk && isFinite(tk.price) ? tk.price : null;
      }
      assets.push({ symbol: t.symbol, chain: 'solana', amount,
        price_usd: p, usd: p !== null ? round2(amount * p) : null });
    }
  } catch (e) { sawError = true; }

  assets.sort((a, b) => (b.usd || 0) - (a.usd || 0));
  const priced = assets.filter(a => a.usd !== null);
  return {
    chain: 'solana',
    label: 'Solana',
    assets,
    total_usd: round2(priced.reduce((a, x) => a + x.usd, 0)),
    unpriced: assets.length - priced.length,
    ...(sawError && !assets.length ? { error: 'rpc unreadable' } : {}),
  };
}

async function getSolanaPortfolio(address) {
  if (!isSolanaAddress(address)) return null;
  const hit = cache.get(address);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.portfolio;
  let tickers = {};
  try { tickers = await getTickerFetcher()(); } catch (e) { /* price as null */ }
  const section = await readSolana(address, tickers);
  const portfolio = {
    read_only: true,
    address,
    chain: 'solana',
    chains: [section],
    assets: section.assets,
    total_usd: section.total_usd,
    unpriced: section.unpriced,
    ...(section.error ? { error: section.error } : {}),
    generated_at: new Date().toISOString(),
  };
  cache.set(address, { at: Date.now(), portfolio });
  if (cache.size > 500) cache.delete(cache.keys().next().value);
  return portfolio;
}

module.exports = {
  SPL_TOKENS,
  isSolanaAddress,
  getSolanaPortfolio,
  setRpcCall,
  setTickerFetcher,
};

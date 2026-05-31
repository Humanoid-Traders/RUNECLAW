# Solana Ecosystem Mode

RUNECLAW supports a dedicated **Solana ecosystem mode** that focuses the scanner on 15 high-liquidity Solana tokens traded on Bitget. All 18 risk checks apply identically -- plus additional meme-coin protections.

**Try it live:** Send `/mode solana` to [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)

---

## Activation

**Option 1 -- Environment variable:**
```bash
ASSET_UNIVERSE=solana  # in .env
```

**Option 2 -- Live switching via Telegram:**
```
/mode solana   → Focus on 15 Solana tokens
/mode all      → Scan all Bitget USDT pairs
```

No restart required. The scanner switches focus immediately.

---

## Token Universe (15 tokens)

| Token | Category | Bitget Pair | Notes |
|-------|----------|-------------|-------|
| SOL | L1 | SOL/USDT | Solana native, grouped as ALT_L1 |
| JUP | DeFi | JUP/USDT | Jupiter DEX aggregator |
| JTO | Staking | JTO/USDT | Jito MEV/staking |
| BONK | Meme | BONK/USDT | Tighter volatility guard (4% ATR) |
| WIF | Meme | WIF/USDT | Tighter volatility guard (4% ATR) |
| PYTH | Oracle | PYTH/USDT | Pyth Network oracle |
| RAY | DeFi | RAY/USDT | Raydium AMM |
| ORCA | DeFi | ORCA/USDT | Orca DEX |
| RENDER | Compute | RENDER/USDT | GPU rendering network |
| HNT | IoT | HNT/USDT | Helium network |
| MOBILE | IoT | MOBILE/USDT | Helium Mobile |
| W | Bridge | W/USDT | Wormhole cross-chain |
| JITO | Staking | JITO/USDT | Jito liquid staking |
| TENSOR | NFT | TENSOR/USDT | Tensor NFT marketplace |
| DRIFT | DeFi | DRIFT/USDT | Drift perpetuals DEX |

---

## Solana-Specific Risk Controls

### Meme-Coin Volatility Guard
BONK and WIF (classified as `MEME` correlation group) use a **tighter 4% ATR threshold** instead of the default 6%. This prevents entries during extreme volatility spikes common in meme coins.

### Ecosystem Correlation Group
Non-meme Solana tokens are grouped as `SOLANA_ECO` in the correlation matrix. The risk engine limits positions within the same correlation group (default: max 2 per group), preventing concentrated bets across tokens that tend to move together.

| Group | Tokens | Max Concurrent |
|-------|--------|----------------|
| ALT_L1 | SOL (+ AVAX, NEAR, etc.) | 2 |
| MEME | BONK, WIF (+ DOGE, PEPE, etc.) | 2 |
| SOLANA_ECO | JUP, JTO, PYTH, RAY, ORCA, JITO, TENSOR, DRIFT, HNT, MOBILE, W | 2 |

### Full 18-Check Coverage
All 18 risk checks apply identically in Solana mode. The circuit breaker, position limits, human confirmation, and audit trail are unchanged.

---

## How Scanning Works in Solana Mode

```text
1. Fetch ALL Bitget USDT tickers (324+ pairs)
2. Compute momentum and volume scores for each
3. Sort by absolute momentum (descending)
4. SOLANA FILTER: Move Solana ecosystem tokens to top
5. Fill remaining slots with other top movers
6. Return top N results (Solana-first, then others)
```

This means you still see the strongest non-Solana movers, but Solana tokens always appear first.

---

## Current Limitations

- **Centralized execution only** -- trades execute on Bitget, not on-chain
- **No DEX integration** -- no Jupiter swaps, Raydium pools, or on-chain liquidity
- **Static token list** -- 15 tokens hardcoded, no dynamic discovery
- **No on-chain data** -- no wallet tracking, DEX volume, or funding rates from Solana RPC

These are documented as extension opportunities in the roadmap.

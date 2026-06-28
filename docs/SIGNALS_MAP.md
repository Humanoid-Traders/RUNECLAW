# RUNECLAW Signals Map

Authoritative reference for how RUNECLAW turns raw market data into a trade
decision: every signal family, how they are blended (confluence), the regime /
strategy-mode layer, data sources, and the known gaps. Line numbers are anchors
at time of writing — trust the function/class names if they drift.

> **One-line model:** market data → ~60 indicators + pattern/flow detectors →
> **35+ weighted confluence voters** → regime + strategy-mode adjustment → LLM
> thesis → blended confidence → risk engine → execution.

## Pipeline (where each stage lives)

| Stage | Location |
|---|---|
| Entry point | `bot/core/analyzer.py` · `Analyzer.analyze()` (~L323) |
| Indicator computation (~60) | `analyzer.py` · `_compute_indicators()` (~L1181) |
| Regime detection | `analyzer.py` · `_detect_regime()` (~L1507) → `Regime` in `bot/core/ta_utils.py` |
| Confluence scoring (35+ voters) | `analyzer.py` · `_score_confluence()` (~L1601) |
| Strategy-mode selection + boosts | `bot/core/strategy_modes.py` · `StrategyMode` (~L33) |
| LLM thesis | `analyzer.py` · `_llm_thesis()` (~L2083) → `bot/llm/provider.py` |
| Blended confidence | `analyzer.py` (~L638): `confidence*llm_weight + confluence*confluence_weight` |
| Risk gate | `bot/risk/risk_engine.py` (23 checks) |
| Execution | `bot/core/live_executor.py` |

## Signal families

Counts are signal *families*; many subdivide. File references point at the
detector or the voter.

### Trend & momentum
- **RSI-14** (Wilder) — `analyzer.py:_compute_indicators` · voter weight ~1.5
- **MACD (12/26/9)** — histogram trend · weight ~1.0
- **ADX-14 + ±DI** — trend strength/direction · weight ~0.7
- **EMA ribbon (9/21)** — trend filter · weight ~0.5
- **Stochastic %K/%D (14,3,3)** — momentum extremes + divergences · weight ~1.2

### Volatility & mean-reversion
- **Bollinger Bands (20, 2σ)** — %B position/extremes · weight ~1.0
- **Keltner Channels (EMA20 ± 2·ATR)** — squeeze detection · weight ~0.7
- **ATR-14** (true range) — foundation for SL/TP sizing
- **Volatility Squeeze (BB-in-KC)** — compression → breakout
- **Session range (24-bar H/L)** — intraday context

### Volume & order flow
- **OBV** — volume trend · weight ~0.6
- **Volume oscillator (5/20 EMA)** — volume momentum
- **Taker buy/sell imbalance** — aggressor bias · weight ~0.5
- **Volume spike** — confirmation · weight ~0.8
- **Capitulation volume** — 3×avg + large candle, reversal · weight ~0.9
- **Whale prints** — `bot/core/order_flow.py`, adaptive >95th pct + $25k · weight ~1.2
- **CVD + CVD/price divergence** — `order_flow.py` · weight ~0.8
- **Order-book imbalance** — `order_flow.py` (bid/ask depth ratio)
- **Funding-rate extremes / OI changes** — `order_flow.py`, `bot/core/smart_money.py`

### Price action & levels
- **Pin bar / inside bar** — `analyzer.py` reversal/compression · weight ~0.9
- **Fibonacci retracement zones** — 23.6–78.6% · weight ~0.5
- **VWAP** (full + rolling 20/50) — `analyzer.py` · weight ~0.5
- **Volume Profile POC/VAH/VAL** — `bot/core/volume_profile.py` · weight ~0.6
- **Donchian 20/55 (Turtle)** — `analyzer.py` breakout · weight ~1.0 (2.0 in TURTLE_BREAKOUT)

### Chart patterns — `bot/core/chart_patterns.py`
- **Head & Shoulders / Inverse** · **Double Top/Bottom** · **Flags** · **Triangles**
  (asc/desc/sym) · **Wedges** · **Elliott** (impulse / corrective / diagonal) ·
  **Wyckoff phases** · **Harmonic** (Gartley/Butterfly/Bat/Crab). Voter weights ~0.55–0.8.

### Advanced structure
- **Liquidity sweeps** (stop-hunt reversal) — `bot/core/liquidity_sweep.py` · weight ~1.2
- **Supply/Demand zones** (fresh/tested/broken) — `bot/core/supply_demand.py` · weight ~0.6–1.0
- **Market structure HH/HL/LH/LL, BOS, CHoCH** — `bot/core/multi_timeframe.py`
- **Divergence scanner** (regular/hidden, RSI/MACD/OBV) — `bot/core/divergence.py` · weight ~0.4–1.0
- **Smart-money composite** (whale concentration, book depth, cascade risk) — `smart_money.py` · weight ~1.0–1.5

### Sentiment, macro, cross-asset
- **Sentiment / Fear & Greed** — `bot/core/sentiment.py` · weight ~0.6 — ⚠️ *price-derived only; external feed stubbed*
- **Macro events / blackout windows** — `bot/core/macro_events.py` (severity-tiered size modulation)
- **Cross-asset** — `bot/core/cross_asset.py`: BTC dominance, ETH/BTC ratio, DXY proxy, alt-BTC correlation regime

## Confluence scoring

`_score_confluence()` (~L1601). 35+ independent voters each cast a vote in
`[-1, +1]`; the result is a weighted, normalized conviction:

```
confluence = (Σ(voteᵢ · weightᵢ) / Σ weightᵢ + 1) / 2      # → [0,1], 0.5 = neutral
```

- **Fail-closed:** conviction < 0.5 is rejected.
- **Blended confidence** (~L638): `LLM_confidence · CONFIG.analyzer.llm_weight +
  confluence · CONFIG.analyzer.confluence_weight` (weights are configurable, not
  hardcoded). Then trend-alignment bonus/penalty and volume confirmation adjust it.
- **Mean-reversion family cap:** RSI / %B / Stochastic / Fib co-fire on the same
  "price extreme." When `CONFIG.confluence.family_cap_enabled`, their combined
  weight is capped (`mr_oscillator_weight_cap`) so an oscillator cluster can't
  inflate conviction. Only actively-voting members count toward the cap.
- **Strategy-mode boosts** (`strategy_modes.py`): each mode amplifies the voters
  that matter for it (e.g. MEAN_REVERSION boosts RSI/%B/Stoch/CVD-div; TURTLE_BREAKOUT
  boosts Donchian 2.0×; BREAKOUT boosts MTF-BOS/book/whale).
- **Regime penalties:** in RANGE raw confluence must clear ~0.70, in CHOP ~0.75,
  else a penalty is applied.

## Regimes & strategy modes

**Regimes** — `Regime` in `ta_utils.py`, detected in `_detect_regime()` with
2-of-3 smoothing to avoid whipsaw:

| Regime | Rough condition | Mode | Bias |
|---|---|---|---|
| TREND_UP / TREND_DOWN | ADX > 25, ±DI direction | TREND_CONTINUATION | ride pullbacks, wider stops |
| EXPANSION | ADX 18–35, BB inside KC | BREAKOUT | breakout entries |
| RANGE | ADX < 20 | MEAN_REVERSION | fade extremes, tight stops |
| CHOP | ADX 20–25 | CONSERVATIVE | safest params |

**Strategy modes** — `StrategyMode` in `strategy_modes.py`:
`TREND_CONTINUATION`, `BREAKOUT`, `TURTLE_BREAKOUT`, `MEAN_REVERSION`,
`LIQUIDITY_SWEEP`, `CONSERVATIVE` (default/uncertain). Each sets its own R:R,
stop width, and confluence boost profile.

## Timeframes & universe

- **Entry timeframe:** 1H (~100 candles). **HTF context:** 4H + 1D for
  multi-timeframe alignment.
- **Asset universe** (`ASSET_UNIVERSE`, default `all_markets`): `all_markets`
  (crypto + all futures incl. metals/commodities/pre-IPO/ETFs/TradFi), `all`
  (crypto spot w/ futures), `solana`, `stocks`, `hybrid`, and futures-only
  buckets (`metals`, `commodities`, `pre_ipo`, `etfs`, `tradfi`). Only USDT pairs
  with a futures market qualify.

## Data sources

- **Price/ticker:** Bitget WS v3 (`bot/core/ws_feed.py`) — last/bid/ask, 24h vol,
  funding, mark/index, open interest.
- **Microstructure:** `bot/core/order_flow.py` / `exchange_flow.py` — order book
  (25 levels), recent trades + aggressor side, whale prints, CVD, funding, OI.
- **Candles:** OHLCV 1H/4H/1D via CCXT.
- **External:** Fear & Greed (partial), macro calendar (dates only).

## Gaps & opportunities

What is **missing or shallow** today — ranked by leverage. The theme: the
analytical breadth is already large; the highest-value work is *closing loops
that are 80% built* rather than adding signals.

1. **The learning loop doesn't feed back.** `experience.py`, `reflection.py`,
   `feedback.py` and `llm_calibration.jsonl` record outcomes but nothing
   recalibrates decisions. Highest leverage:
   - **Confidence calibration** — fit realized-win-rate vs raw confidence from
     `llm_calibration.jsonl` so a "0.85" actually means 85% historical win rate
     (and the admin auto-trade threshold is trustworthy).
   - **Voter-weight learning** — voter weights are hand-tuned ("guesses, not
     backtested"); `bot/core/metrics.py` already has per-signal attribution to
     drive data-based reweighting (shadow-mode first).
   - **Per-setup expectancy in the gate** — `experience.get_similar_setups()`
     feeds the `/analyze` prompt but not the decision gate.
2. **Sentiment voter is stubbed** — votes with ~zero real signal; wiring a real
   (even LLM-based) headline/social source makes a dead voter live.
3. **Funding/basis capture** — extreme funding is *detected* (`smart_money.py`)
   but never *executed*; uncorrelated carry alpha for a perps bot.
4. **Execution quality** — no adaptive maker-first routing or partial-fill
   handling; `slippage.py` already measures the leak.
5. **Walk-forward auto-tuning** — backtester (`bot/backtest/`) exists but params
   are hand-set; walk-forward optimization (with overfitting guards) would let
   every change above be A/B-validated.
6. **On-chain / real smart-money** — exchange-only today; a $25k print can't be
   told from a liquidation. Highest ceiling, highest cost (Nansen/Arkham/Glassnode).

**Not present at all:** on-chain flows, real-time news/social, full live
correlation matrix, L2/L3 tick microstructure, options Greeks/IV, statistical
anomaly/change-point detection.

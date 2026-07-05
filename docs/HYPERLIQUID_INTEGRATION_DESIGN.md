# Hyperliquid Multi-Exchange Support — Design Doc

**Date:** 2026-07-05
**Status:** 📋 **PROPOSED — nothing built yet.** This is the "plan before code"
deliverable; no phase below should start without a green light on this doc.

## What was asked

> "investigate how we can add hyperliquid as exchange to trade on als whit bot"

Scope decision (asked, answered): write this design doc first, phased like the
per-user credential store work (`docs/PER_USER_LIVE_READINESS.md`), before
touching any code.

## Why this is a real project, not a config swap

The bot is **Bitget-hardcoded today, not exchange-agnostic**. There is no
`Exchange` interface anywhere in `bot/` — `ccxt.bitget(...)` is instantiated
independently in ~9 places (`live_executor.py`, `market_scanner.py`,
`exchange_credentials.py`, `data_loader.py`, `chart_renderer.py`, plus ad-hoc
scripts), each duplicating its own auth/timeout/`defaultType` options. On top
of ccxt there's a hand-rolled, HMAC-signed Bitget REST client
(`bot/core/bitget_v3_client.py`) and a fully custom WebSocket client hardwired
to `wss://ws.bitget.com` (`bot/core/ws_feed.py`) — neither goes through ccxt at
all.

Hyperliquid's account model is **not a different flavor of the same thing**:

| | Bitget (today) | Hyperliquid |
|---|---|---|
| Auth | API key + secret + passphrase | EIP-712 wallet signing (private key, or a delegated "agent wallet" with trading-only, no-withdraw permissions) — **no API keys at all** |
| Settlement currency | USDT | USDC |
| Margin model | isolated/cross toggle, `productType=USDT-FUTURES` | asset-based clearinghouse margin, no Bitget-style product-type/hedge-mode split |
| Position mode | one-way vs hedge (`holdMode`, detected via a Bitget-proprietary endpoint) | no equivalent concept |
| Asset universe | crypto perps **+** tokenized stocks/metals/ETFs (own market-hours rules in `order_rules.py`) | crypto perps only |
| ccxt support | `ccxt.bitget` | `ccxt.hyperliquid` — **already present** in the pinned `ccxt==4.5.56` (verified: no ccxt upgrade needed) |

The auth model difference is the crux: the entire per-user encrypted
credential store (`bot/core/exchange_credentials.py`, Fernet-encrypted
key/secret/passphrase triples) was built around "three secret strings." A
wallet private key (or agent-wallet key) is a different secret *shape*, and
signing every request client-side with EIP-712 is a different code path than
handing ccxt a key/secret/passphrase and letting it sign HMAC requests
internally. That store's encryption-at-rest guarantee still applies, but its
schema and the signing call sites need to generalize.

## Design principle: abstraction first, Hyperliquid second

Building a Hyperliquid adapter directly against today's Bitget-hardcoded call
sites means writing Hyperliquid-specific branches into `live_executor.py`
forever, and re-doing this work for exchange #3. Instead:

1. Introduce a narrow `Exchange` protocol that captures exactly the operations
   the bot actually calls today (enumerated below) — no more, no less. Every
   Bitget-specific quirk (hold-mode detection, `productType` params, the v3
   HMAC client, the WS symbol format) moves **behind** this interface, not
   duplicated beside it.
2. Bitget becomes the first implementation of that interface — refactored,
   not rewritten. This phase alone should be **byte-identical behavior**,
   proven by the existing test suite passing unchanged plus the frozen
   backtest benchmark reproducing exactly (same discipline as every other
   change this session).
3. Hyperliquid becomes the second implementation, added once the interface
   is proven not to leak Bitget assumptions.

Skipping step 2 (going straight to "add Hyperliquid alongside the existing
Bitget code") would still work, but bakes in a second generation of
hardcoded-per-exchange branches instead of paying down the abstraction debt
once.

## The `Exchange` interface — draft surface

Derived from every ccxt/bespoke call site found in the codebase survey. Draft
only; finalized during Phase 1.

```
class Exchange(Protocol):
    # Market data
    async def fetch_ticker(symbol) -> Ticker
    async def fetch_ohlcv(symbol, timeframe, since, limit) -> list[Bar]
    async def fetch_order_book(symbol, limit) -> OrderBook
    async def fetch_funding_rate(symbol) -> FundingRate
    async def fetch_positions() -> list[Position]
    async def fetch_balance() -> Balance

    # Order lifecycle
    async def create_order(symbol, side, type, amount, price, params) -> Order
    async def cancel_order(order_id, symbol) -> None
    async def fetch_open_orders(symbol) -> list[Order]

    # Account/risk config
    async def set_leverage(leverage, symbol) -> None
    async def set_margin_mode(mode, symbol) -> None          # no-op on Hyperliquid
    async def get_position_mode() -> PositionMode             # ONE_WAY on Hyperliquid, always

    # Real-time feed
    async def watch_ticker(symbol) -> AsyncIterator[Ticker]
    async def watch_orders() -> AsyncIterator[Order]

    # Identity
    @property
    def id(self) -> str                                        # "bitget" | "hyperliquid"
    @property
    def settlement_currency(self) -> str                       # "USDT" | "USDC"
```

Every method already exists on the Bitget side today in some form (ccxt
unified call or a bespoke helper) — this is a reshaping, not new capability.

## Phased plan (mirrors the per-user credential store's 5 phases)

**Phase 1 — `Exchange` protocol + Bitget refactor (behavior-preserving)**
Define the interface above. Move every `ccxt.bitget(...)` call site, the
`bitget_v3_client.py` HMAC logic, and the `ws_feed.py` WebSocket client behind
a `BitgetExchange` implementation. `LiveExecutor` and friends call the
interface, never ccxt directly. **Acceptance: zero behavior change** — full
test suite green, frozen benchmark reproduces the exact current numbers,
`git diff` on trading outcomes is empty.

**Phase 2 — credential-store schema generalization**
Extend `exchange_credentials.py`'s encrypted store to hold a discriminated
union: `{"exchange": "bitget", "key", "secret", "passphrase"}` vs
`{"exchange": "hyperliquid", "wallet_address", "agent_private_key"}`. Encrypted
at rest exactly as today (Fernet). `/connect` gains an exchange-select step.
Still Bitget-only in practice — this phase just widens the schema so Phase 4
doesn't need a migration.

**Phase 3 — `HyperliquidExchange` adapter, market-data only**
Implement the read side of the interface (`fetch_ticker`, `fetch_ohlcv`,
`fetch_order_book`, `fetch_funding_rate`, `watch_ticker`) against
`ccxt.hyperliquid`. No order placement yet. This lets Hyperliquid's crypto
universe feed signals/backtests/paper-trade simulation without touching the
live money path at all — the safest possible increment, and independently
useful even if later phases stall.

**Phase 4 — `HyperliquidExchange` order placement (paper-trade validated first)**
Implement `create_order`/`cancel_order`/`set_leverage`/position management
against Hyperliquid's EIP-712 signing (via ccxt's built-in signer — no need to
hand-roll ECDSA). Validate end-to-end in `SIMULATION_MODE=true` (paper trades
against live Hyperliquid market data) before any real-money path opens. Gated
behind a new `HYPERLIQUID_LIVE_TRADING_ENABLED` flag, default OFF, additive to
every existing kill switch (`SIMULATION_MODE`, `CONFIG.is_live()`, `/golive`,
compliance locks) — never loosens them.

**Phase 5 — asset-universe mapping + risk-model reconciliation + enablement**
Hyperliquid lists crypto perps only, no tokenized stocks/metals/ETFs — the
per-exchange universe needs its own filtered list (`order_rules.py`'s
market-hours logic simply doesn't apply to a Hyperliquid-only symbol).
Reconcile margin-mode assumptions (Hyperliquid has no isolated/cross toggle
the way Bitget does) into the risk engine's sizing/cap logic so it fails safe
rather than silently assuming Bitget semantics. Final runbook + readiness
report, matching `PER_USER_LIVE_READINESS.md`'s format.

## Defense-in-depth (unchanged, Hyperliquid only ever adds a gate)

Every existing kill switch stays authoritative and this work can only add
another gate in front of it, never loosen one:

1. `SIMULATION_MODE=false` hard veto.
2. `CONFIG.is_live()`.
3. Per-session `/golive CONFIRM` arming.
4. Compliance locks.
5. Per-user eligibility (existing, from the credential-store work).
6. **New**: per-exchange enablement flag (`HYPERLIQUID_LIVE_TRADING_ENABLED`,
   default OFF) + wallet-key-present check, analogous to gate 5.

## Open questions for the user before Phase 1 starts

- **Wallet custody model**: does "trade on Hyperliquid" mean the operator's
  own wallet (one account, like today's Bitget operator account), or
  per-user wallets (mirroring the per-user Bitget credential store)? This
  changes Phase 2's schema shape and whether agent-wallet delegation
  (recommended — no withdrawal rights) is mandatory or optional.
- **Testnet first?** Hyperliquid has a public testnet; Phase 4 could validate
  against it before any mainnet wallet is involved, independent of
  `SIMULATION_MODE`.
- **Priority vs the standing backtest/signal-tuning work**: this is a
  different kind of effort (new capability, not a signal A/B) — should it run
  in parallel with the round-3 backtest items, or take priority?

## Test coverage this plan implies (to be written per-phase, not up front)

- `test_exchange_protocol.py` — the interface itself, plus a fake in-memory
  implementation used by other tests (mirrors how `test_live_executor.py`
  already mocks ccxt today, formalized).
- `test_bitget_exchange_refactor.py` — Phase 1's byte-identical-behavior proof.
- `test_exchange_credentials_multi.py` — Phase 2's schema migration + backward
  compatibility with existing single-exchange encrypted records.
- `test_hyperliquid_market_data.py` — Phase 3, against ccxt's hyperliquid
  sandbox/mock responses.
- `test_hyperliquid_order_placement.py` — Phase 4, paper-trade path only
  initially.

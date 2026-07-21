# Security & Permissions

RUNECLAW handles exchange API keys, Telegram bot tokens, and optional LLM credentials. This page documents the security model, permission boundaries, and operational practices.

---

## Credential Management

### No Hardcoded Secrets

The codebase has been audited to confirm zero hardcoded API keys, tokens, or passwords. All credentials are loaded from environment variables at startup via `python-dotenv`.

```text
.env              ← Your credentials (NEVER committed)
.env.example      ← Template with safe defaults (committed)
.gitignore        ← Lists .env to prevent accidental commit
```

### Credential Inventory

| Credential | Variable | Purpose | Required |
|---|---|---|---|
| Bitget API key | `BITGET_API_KEY` | Market data and order execution | For exchange features |
| Bitget API secret | `BITGET_API_SECRET` | Request signing | For exchange features |
| Bitget passphrase | `BITGET_PASSPHRASE` | Additional auth layer | For exchange features |
| Telegram bot token | `TELEGRAM_BOT_TOKEN` | Bot authentication | For Telegram mode |
| Telegram chat ID | `TELEGRAM_CHAT_ID` | Authorized user whitelist | Recommended |
| LLM API key | `LLM_API_KEY` | AI-powered analysis | Optional |

### Key Rotation

- Rotate API keys regularly, especially after sharing access or suspected exposure
- Revoke keys immediately on the [Bitget API dashboard](https://www.bitget.com/account/newapi) if compromised
- Telegram bot tokens can be revoked via [@BotFather](https://t.me/BotFather) → `/revoke`

---

## Permission Boundaries

### Bitget API Permissions

The market scanner uses **public endpoints only** and does not pass API credentials for scanning or analysis operations. Authenticated API keys are only required for order execution in live trading mode.

RUNECLAW requires different API permission levels depending on mode:

| Mode | Required Permissions | Recommendation |
|---|---|---|
| Paper trading (default) | **Read-only** -- market data only | Use read-only API keys |
| Live trading | **Read + Trade** -- market data + order placement | Enable trade permission only when ready |
| Withdrawal | **Never required** | Never enable withdrawal permissions |

> **Best practice:** Create a dedicated API key for RUNECLAW with the minimum permissions needed. Never reuse keys across applications.

### Telegram Authorization

The Telegram handler uses a **fail-closed** authorization model:

```text
TELEGRAM_CHAT_ID set        → Only listed chat IDs can interact
TELEGRAM_CHAT_ID empty +
  TELEGRAM_ALLOW_OPEN=true  → Any user can interact (dev only)
TELEGRAM_CHAT_ID empty +
  TELEGRAM_ALLOW_OPEN unset → ALL commands rejected (fail-closed)
```

Authorization applies to both slash commands and inline keyboard callbacks (confirm/reject buttons). An unauthorized user cannot execute any action.

### LLM API

- The LLM key is optional. Without it, the analyzer uses a free rule-based fallback.
- Each `/analyze` call consumes approximately $0.01-0.03 at GPT-4o pricing.
- No user data or trade history is sent to the LLM -- only technical indicator values and market context for the current analysis.

---

## Data Flow Security

### What data leaves the system

| Destination | Data sent | Purpose |
|---|---|---|
| Bitget API | Public market data requests (tickers, OHLCV candles) | Fetch market data for scanning and analysis. No credentials passed for public endpoints. |
| Telegram API | Bot token + message content | Send scan results, trade ideas, confirmations |
| OpenAI API (optional) | Technical indicators + market context | Generate directional thesis |

### What data stays local

| Data | Storage | Retention |
|---|---|---|
| Portfolio state | JSON file (`data/portfolio_state.json`) | Persistent, auto-saved |
| Audit logs | `logs/*.jsonl` files | Persistent on disk |
| Risk engine state | JSON file (`data/risk_state.json`) | Persisted for circuit breaker |
| Trade history | JSON file (within portfolio state) | Persistent |

### What is never sent externally

- Full trade history
- Portfolio balances or PnL
- Other users' data (single-operator system)
- Raw API keys (keys are used for signing, never transmitted in plaintext)

---

## Thread Safety

All shared mutable state is protected by `threading.RLock`:

| Component | Lock | Purpose |
|---|---|---|
| `PortfolioTracker` | `_lock` | Position lifecycle, balance updates |
| `RiskEngine` | `_lock` | Circuit breaker state, loss tracking |
| `MarketScanner` | `_lock` | Volume history, signal cache |
| `TelegramHandler` | `RateLimiter._lock` | Per-user rate counters |

RUNECLAW uses single-threaded asyncio, so lock contention is minimal. The locks are defensive -- they protect against any future threading or concurrent access patterns.

---

## Rate Limiting

### Telegram

Per-user rate limiting prevents abuse:

| Parameter | Default | Behavior |
|---|---|---|
| `rate_limit_per_minute` | 20 | Sliding window per user ID |
| Exceeded | -- | Command rejected with warning |

### Bitget API

The scanner respects Bitget's API rate limits by:
- Fetching tickers in bulk (single call for all pairs)
- Throttling OHLCV requests to avoid 429 responses
- Using sandbox mode by default (separate rate limits)

---

## Audit & Compliance

Every security-relevant action is logged:

| Event | Log channel | Example |
|---|---|---|
| Unauthorized Telegram command | `system.jsonl` | `"auth_check" → "REJECTED"` |
| Rate limit exceeded | `system.jsonl` | `"rate_limit" → "EXCEEDED"` |
| Trade confirmed | `trade.jsonl` | `"trade_confirmed" → trade_id` |
| Trade rejected (risk) | `risk.jsonl` | `"risk_check" → "REJECTED" + failed_check` |
| Circuit breaker tripped | `risk.jsonl` | `"circuit_breaker" → "TRIPPED"` |
| Circuit breaker reset | `risk.jsonl` | `"circuit_breaker" → "RESET"` |
| Telegram callback | `system.jsonl` | `"telegram_callback" → callback_data` |

All log entries include UTC timestamps and are append-only (JSONL format).

---

## Deployment Checklist

Before deploying RUNECLAW (even in paper mode):

- [ ] `.env` file created with your credentials (not `.env.example`)
- [ ] `.env` is listed in `.gitignore` (default: yes)
- [ ] `TELEGRAM_CHAT_ID` set to your chat ID (fail-closed if empty)
- [ ] `BITGET_SANDBOX=true` for initial testing
- [ ] `SIMULATION_MODE=true` (default)
- [ ] `LIVE_TRADING_ENABLED=false` (default)
- [ ] API key permissions: read-only for paper mode
- [ ] No secrets in git history (`git log --all -p | grep -i "api_key"`)
- [ ] Logs directory exists and is writable

---

## Known Limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| No encryption at rest | Audit logs and portfolio state are plaintext JSON | Deploy on encrypted volumes |
| Single-operator | No multi-user access control | `TELEGRAM_CHAT_ID` restricts to one operator |
| No TLS pinning | Standard HTTPS to Bitget/Telegram/OpenAI | Relies on system certificate store |
| No key vault | Credentials in `.env` file | Use secrets manager in production |

---

## Security Hardening Log (Audit v3.0)

All findings from the RUNECLAW Deep Audit v3.0 have been addressed:

### Critical Fixes

| ID | Issue | Fix |
|----|-------|-----|
| C1 | Frozen dataclass mutation via `object.__setattr__` | Created thread-safe `RuntimeState` class; `/mode` command and scanner read from `RUNTIME` instead of mutating frozen CONFIG |
| C3 | No log redaction — API keys could appear in logs/tracebacks | Added `_redact_dict()` and `_redact_string()` in logger.py; regex scrubs sensitive keys and inline secrets before write |
| C5 | MCP server exposed tools without authentication | Added `MCP_AUTH_TOKEN` env var; `call_tool()` requires bearer token when set; uses `hmac.compare_digest` for timing-safe comparison |

### Warning Fixes

| ID | Issue | Fix |
|----|-------|-----|
| W1 | CostTracker never reset daily — `llm_cost_usd` accumulated forever | Added UTC day boundary detection; daily auto-reset with separate `snapshot_lifetime()` for cumulative stats |
| W5 | Cache key truncated to 16-char SHA-256 — collision risk | Changed to full 64-char SHA-256 hex digest |
| W6 | Walk-forward backtest leaked temp directories | Added explicit `cleanup()` calls after each fold's train and test engines |

### Hardening

| Area | Change |
|------|--------|
| Input validation | `/approve` rejects non-numeric Telegram IDs; `/analyze` rejects non-alphanumeric symbols via regex whitelist |
| Encapsulation | Risk engine uses `portfolio.get_position_value()` public API instead of accessing private `_last_prices` |
| Source availability (BUSL-1.1) | `/start` and `/help` responses include source repository link |
| Financial disclaimer | `/start` and `/help` include "Not financial advice" notice |
| Portfolio corruption | `load_state()` logs at CRITICAL level on corrupted state files instead of silent fallback |
| Traceback redaction | MCP server redacts tracebacks before logging via `_redact_string()` |

### Security Test Suite

**29 dedicated tests** in `tests/test_security.py`:
- Log redaction (dict scrubbing, inline secrets, nested values, depth limits)
- MCP auth (token required, wrong token rejected, no-auth-when-unset)
- RuntimeState (valid/invalid modes, thread safety with 4 concurrent threads)
- Cache key collision prevention (full SHA-256 length verification)
- CostTracker daily reset and lifetime accumulation
- Portfolio corruption handling and public API
- Backtest temp directory cleanup
- Input validation regex (symbol injection, Telegram ID format)

# Skills & Commands

RUNECLAW uses a modular skill system. Every capability is registered as a self-contained skill that can be invoked through the Telegram bot interface.

**Try it live:** [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)

## Telegram Commands

| Command | Description | Access |
|---------|-------------|--------|
| `/start` | Register with the bot, request access | Everyone |
| `/help` | List all commands and your role | Everyone |
| `/scan` | Scan Bitget for top movers and volume spikes | Trader+ |
| `/analyze <SYMBOL>` | AI + technical analysis on a specific asset | Trader+ |
| `/portfolio` | Paper portfolio summary with PnL waterfall | Trader+ |
| `/trade` | View pending trade ideas with confirm/reject | Trader+ |
| `/risk` | Risk metrics, circuit breaker, exposure gauges | Viewer+ |
| `/rejected` | Recently rejected trades with failure reasons | Viewer+ |
| `/costs` | Agent economics -- LLM costs, PnL waterfall, ROI drag | Trader+ |
| `/backtest` | Run backtest with synthetic data | Trader+ |
| `/macro` | Macro event calendar and current risk state | Viewer+ |
| `/learn` | AI learning dashboard, strategy tiers, reflections | Viewer+ |
| `/patterns` | Detected recurring market patterns | Viewer+ |
| `/proposals` | Strategy improvement proposals from AI | Viewer+ |
| `/optimize` | LLM token optimizer stats, cache hit rates | Viewer+ |
| `/halt` | Emergency kill-switch (trip breaker) | Trader+ |
| `/reset` | Reset circuit breaker after halt | Admin |
| `/status` | Bot mode, engine state, equity snapshot | Viewer+ |
| `/approve <ID>` | Approve a pending user | Admin |
| `/revoke <ID>` | Revoke user access | Admin |
| `/users` | List all registered users and roles | Admin |

## Role-Based Access

| Role | Access Level |
|------|-------------|
| **Admin** | All commands, user management |
| **Trader** | Trading commands, analysis, portfolio |
| **Viewer** | Read-only access to risk, learning, status |
| **Pending** | `/start` and `/help` only, awaiting approval |

New users are auto-registered as **pending** when they send `/start`. Admins receive a notification and can approve with `/approve <user_id>`.

## AI Chat

Users can send free-text messages (not commands) and the bot responds as an AI trading assistant. The AI is scoped to crypto/trading context and powered by Groq LLM.

## Dashboard Navigation

The `/status` command opens an interactive dashboard with inline keyboard pane navigation:

- **Status** -- Mode, state, equity, circuit breaker
- **Portfolio** -- Holdings, PnL, open positions
- **Risk** -- Exposure gauges, drawdown, streak
- **Costs** -- LLM spend, token usage, ROI impact
- **Learning** -- AI modules, strategy tiers
- **Market** -- Recent scan results

Navigation uses `edit_message_text` (edit-in-place) so the chat stays clean.

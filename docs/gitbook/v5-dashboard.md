# V5 Dashboard & AI Chat

RUNECLAW's Telegram interface uses a **v5 dashboard system** with HTML-formatted cards, inline keyboard navigation, visual gauges, and built-in AI chat.

**Try it live:** [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)

---

## Dashboard Navigation

Send `/dashboard` or `/status` to open the interactive dashboard. The bot displays a status card with an inline keyboard for navigating between panes:

| Pane | Content |
|------|---------|
| **Status** | Mode (SIM/LIVE), engine state, equity, circuit breaker, macro state |
| **Portfolio** | Holdings, PnL waterfall, open positions, win rate |
| **Risk** | Exposure gauges (drawdown, position count, loss streak), risk gate status |
| **Costs** | LLM spend by category, token usage, cost per trade, ROI drag |
| **Macro** | Event calendar, current risk state (Normal/Caution/Lockdown/Blackout) |
| **Learning** | AI module stats, strategy tiers, pending proposals |
| **Scan** | Latest market scan results |

Navigation uses `edit_message_text` -- the bot **edits the same message** instead of sending new ones. This keeps the chat clean and responsive.

---

## Visual Design System

### Status Banner
Every response starts with a one-line banner:
```
SIM • 0 open • 🟢 OK • 🟢 Normal
```
Shows mode, position count, circuit breaker status, and macro state at a glance.

### Visual Gauges
Risk metrics use progress bar gauges:
```
Drawdown [████░░░░░░] 3.2% / 10%
Exposure [██░░░░░░░░] 1.8% / 10%
Streak   [░░░░░░░░░░] 0 / 5
```

### Sectioned Cards
Each pane uses emoji-headed sections with consistent formatting:
```
💰 CAPITAL
  Balance      $10,000.00
  Equity       $10,150.00
  Daily PnL       +$75.00

📊 POSITIONS
  Open              2 / 5
  Win Rate          60.0%
```

### Status Icons
| Icon | Meaning |
|------|---------|
| 🟢 | OK / Safe / Passing |
| 🟡 | Warning / Caution |
| 🔴 | Critical / Failed / Halted |
| ⚪ | Neutral / Inactive |

---

## AI Chat

Users can send **free-text messages** (not commands) and the bot responds as a crypto trading assistant.

### How It Works
- Any message that isn't a `/command` is routed to the LLM
- Uses Groq (llama-3.3-70b-versatile) for fast responses
- System prompt scopes the AI to crypto/trading context
- Responses are concise (under 200 words)
- The bot suggests relevant commands when appropriate

### Example
```
User: What does RSI mean and when should I use it?

Bot: RSI (Relative Strength Index) measures momentum on
a 0-100 scale. Below 30 = oversold (potential buy zone),
above 70 = overbought (potential sell zone).

RUNECLAW uses RSI-14 as one of 10 confluence voters.
It's most useful in ranging markets — in strong trends,
RSI can stay overbought/oversold for extended periods.

Try /analyze BTC to see RSI in action with full
confluence scoring.
```

### Safety
- The AI never gives financial advice -- always notes it provides analysis, not recommendations
- Non-crypto questions get brief answers but steer back to trading
- Chat costs are tracked in the `/costs` breakdown under the "chat" category

---

## User Management

### Role-Based Access
| Role | Access |
|------|--------|
| **Admin** | All commands + user management (/approve, /revoke, /users) |
| **Trader** | Full trading commands + analysis + mode switching |
| **Viewer** | Read-only access to status, risk, portfolio, learning |
| **Pending** | /start and /help only -- awaiting admin approval |

### Onboarding Flow
1. New user sends `/start`
2. Bot auto-registers them as `pending`
3. All admins receive a notification with the user's name and ID
4. Admin sends `/approve <user_id>` to grant trader access
5. User can now use all trading commands

### Admin Commands
| Command | Description |
|---------|-------------|
| `/approve <ID>` | Promote pending user to trader |
| `/revoke <ID>` | Demote user back to pending |
| `/users` | List all registered users with roles |

---

## Technical Implementation

- **Parse mode:** HTML (not Markdown) for reliable formatting
- **Fallback:** If HTML parsing fails, strips tags and resends as plain text
- **Rate limiting:** 20 requests per minute per user
- **User store:** JSON file-backed with threading.Lock for safety
- **Inline keyboards:** CallbackQueryHandler with `pane:` prefix for navigation

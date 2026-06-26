# RUNECLAW — Security Boundaries

## Two Auth Stacks: Node.js (public) vs Python FastAPI (operator)

RUNECLAW intentionally runs two authentication stacks. This is a documented
architectural boundary, not a defect.

### Node.js Stack (`app/auth.js`)
- **Audience**: End users (public web dashboard)
- **Deployment**: Mule Pages (`y9z5438h.mule.page`)
- **Auth**: JWT (bcrypt passwords, 1h expiry, ≥32-char secret)
- **Tokens**: Issued by `app/auth.js`, verified by `app/routes/trades.js`
- **Transport**: HTTPS (Mule Pages TLS)

### Python FastAPI Stack (`api_bridge.py`)
- **Audience**: Single operator (dashboard-pro, Telegram bot)
- **Deployment**: Docker Compose behind nginx (VPS)
- **Auth**: Bearer token (`DASHBOARD_TOKEN` env var, constant-time HMAC)
- **Tokens**: Static secret, not JWT — incompatible by design
- **Transport**: HTTPS (Let's Encrypt via nginx)

### Invariants (must hold)

1. **Neither stack accepts the other's tokens.** Node JWT tokens are not valid
   Bearer tokens for the Python bridge, and vice versa. There is no shared
   secret or token format between them.

2. **The Python bridge is never exposed directly to end users.** It sits behind
   nginx on the VPS and requires `DASHBOARD_TOKEN` for all state-changing
   endpoints. Read endpoints (`/portfolio`, `/risk/status`) also require the
   token as of the F-08/F-09 fix.

3. **One owner is accountable for both.** Security fixes must be applied to
   both stacks when relevant. This document serves as the coordination point.

### Exposure Matrix

| Endpoint | Stack | Auth Required | Internet-Facing |
|----------|-------|---------------|-----------------|
| `/api/auth/*` | Node | Public (register/login) | Yes (Mule Pages) |
| `/api/trades/*` | Node | JWT | Yes (Mule Pages) |
| `/api/bot/sync/*` | Node | X-Bot-Secret | Yes (Mule Pages) |
| `/api/market/*` | Node | None (public data) | Yes (Mule Pages) |
| `/health` | Python | None | VPS only |
| `/scan` | Python | None (rate-limited) | VPS only |
| `/portfolio` | Python | Bearer token | VPS only |
| `/risk/status` | Python | Bearer token | VPS only |
| `/analyze` | Python | Bearer token | VPS only |
| `/confirm` | Python | Bearer token | VPS only |
| `/risk/halt` | Python | Bearer token | VPS only |

### Docs/Schema Disabled (F-08/F-09)

The Python FastAPI bridge has `docs_url=None`, `redoc_url=None`, and
`openapi_url=None` set to prevent schema exposure via `/docs`, `/redoc`,
or `/openapi.json`.

---

*Last updated: 2026-06-18. Review on any auth or deployment change.*

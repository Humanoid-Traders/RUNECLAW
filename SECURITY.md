# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in RUNECLAW, **do not open a public issue**.

1. Email **security@humanoid-traders.dev** with a detailed description and reproduction steps.
2. You will receive an acknowledgement within **48 hours**.
3. We will work with you to validate and patch the issue, targeting a fix within **7 days**.
4. A coordinated disclosure timeline will be agreed upon before any public announcement.

We appreciate responsible reports and will credit researchers (with permission) in the changelog.

## Security Architecture

RUNECLAW enforces a **fail-closed** design — if any safety check is ambiguous or fails, the trade is rejected.

- **21-Check Risk Engine** — every order must pass all pre-trade validations before execution. Of these, 16 are strict fail-closed (any failure = rejection), 1 is fail-open (#17 LIQUIDITY: no order-book data = pass), and 4 gracefully skip when data is insufficient (#18 MACRO, #19 MTF, #20 PCA, #21 VaR). See `config/risk_manifest.yaml` for the authoritative list.
- **Bearer Token Authentication** — state-changing API endpoints (`/confirm`, `/portfolio/close`, `/risk/halt`, `/analyze`) require a `DASHBOARD_TOKEN` bearer token. Read-only endpoints (`/health`, `/scan`, `/portfolio`, `/risk/status`) do not require authentication.
- **Simulation by Default** — live trading requires two explicit flags: `SIMULATION_MODE=false` and `LIVE_TRADING_ENABLED=true`. Both default to safe values.
- **Risk Limits** — position sizing uses percentage-based limits (MAX_POSITION_PCT=2.0%, MAX_SYMBOL_EXPOSURE_PCT=20.0%) applied to current equity. On the default $10,000 paper balance, this yields a ~$200 risk budget and $2,000 max per-symbol exposure.
- **Tamper-Evident Audit Chain** — every decision, rejection, and execution is logged with a chained SHA-256 hash, making post-hoc log tampering detectable. Ed25519 attestation available when `cryptography` package is installed.
- **Human-in-the-Loop** — all trade executions require explicit human confirmation; the AI agent cannot autonomously place orders.
- **Non-Root Container** — Docker image runs as `runeclaw` user (uid 1001).
- **Redis Security** — Redis requires `REDIS_PASSWORD` (no default; compose fails fast if unset), port not exposed to host.

## API Key Handling

- API keys and secrets are loaded exclusively from a `.env` file, which is **gitignored** by default.
- Keys are never logged, serialized, or included in audit output. Log redaction layer strips secrets from tracebacks.
- Credentials are passed to the Bitget SDK at runtime only and are not persisted beyond process memory.
- Contributors must **never** commit `.env`, API keys, or secrets.

## Security Audit Status

- **Internal AI-assisted audit** (v3.0) completed with all critical findings fixed and 29 dedicated security tests added.
- **No independent third-party audit** has been performed. The "Security Scan Passed" badge is self-asserted, not CI-backed.
- This is appropriate for a hackathon prototype; it should not be interpreted as production security assurance.

## Responsible Disclosure

We follow a coordinated disclosure model. Please allow us reasonable time to address reported issues before publishing details.

---

**Repository:** [github.com/Humanoid-Traders/RUNECLAW](https://github.com/Humanoid-Traders/RUNECLAW)
**License:** AGPL-3.0-or-later

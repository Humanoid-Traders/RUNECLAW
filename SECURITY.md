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

- **19-Check Risk Engine** — every order must pass all 19 independent pre-trade validations (position size, total exposure, drawdown, volatility, etc.) before execution. A single failure blocks the order.
- **Frozen Config Dataclass** — safety limits are defined in an immutable `@dataclass(frozen=True)`. Runtime code cannot alter thresholds such as max position size ($10) or total exposure ($50).
- **Tamper-Evident Audit Chain** — every decision, rejection, and execution is logged with a chained SHA-256 hash, making post-hoc log tampering detectable.
- **Human-in-the-Loop** — all trade executions require explicit human confirmation; the AI agent cannot autonomously place orders.
- **68 Learning Safety Tests** — the test suite formally proves that AI suggestions cannot bypass, weaken, or override the risk engine.

## API Key Handling

- API keys and secrets are loaded exclusively from a `.env` file, which is **gitignored** by default.
- Keys are never logged, serialized, or included in audit output.
- Credentials are passed to the Bitget SDK at runtime only and are not persisted beyond process memory.
- Contributors must **never** commit `.env`, API keys, or secrets. Pre-commit checks help enforce this.

## Micro-Test Safety Limits

RUNECLAW ships with conservative defaults designed for hackathon and testnet use:

| Limit              | Value |
| ------------------ | ----- |
| Max position size  | $10   |
| Max total exposure | $50   |

These limits are enforced at the frozen-config level and cannot be changed without a code modification and full test-suite pass.

## Responsible Disclosure

We follow a coordinated disclosure model. Please allow us reasonable time to address reported issues before publishing details. We commit to transparency and will publish post-mortems for any confirmed vulnerability.

---

**Repository:** [github.com/Humanoid-Traders/RUNECLAW](https://github.com/Humanoid-Traders/RUNECLAW)

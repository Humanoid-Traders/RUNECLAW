# Data Durability — backup & restore runbook (MH4)

## What is irreplaceable

| Path | Why it cannot be regenerated |
|---|---|
| `logs/audit_chain.jsonl` | the tamper-evident decision ledger — losing it breaks the hash-chain's history |
| `data/attestation_key.bin` | the Ed25519 signing identity; a new key STALES the on-chain anchor (by design) |
| `data/anchor_state.json` | confirmed on-chain anchor records |
| `data/proofofpnl_publication.json` | the latest sealed publication |
| `data/learning/*`, `data/portfolio_*`, `data/risk_state_*` | learned weights and live risk state |
| `data/runeclaw.db`, `data/secrets_vault.enc` | local DB + encrypted operator secrets |

## Backups

- Automatic: one rotating archive per `BACKUP_INTERVAL_H` (default 24h),
  triggered opportunistically by the publish scheduler; `BACKUP_KEEP`
  (default 14) archives retained in `BACKUP_DIR` (default `data/backups/`).
- Manual: Telegram `/backup` (admin) — also `/backup list`, `/backup verify <name>`.
- Every archive has a sidecar manifest of per-file SHA-256 hashes;
  `verify` re-derives every hash from the archive bytes — same rule as
  Proof-of-PnL: re-derive, don't trust.
- **Copy archives off the host.** A backup on the same disk protects
  against bad deploys, not dead disks: `rsync data/backups/ <offhost>:...`

## Restore (manual, deliberate)

The bot never overwrites its own live state from an archive.

1. Stop the bot.
2. Verify first: `python -c "from bot.utils.backup import verify_backup; print(verify_backup('data/backups/<name>.tar.gz'))"` — expect `(True, [])`.
3. Extract over the repo root: `tar -xzf data/backups/<name>.tar.gz -C /path/to/RUNECLAW`
4. Restart, then check: `/status` page components, `/anchor` (identity still VERIFIED
   — proves the attestation key survived), and the audit chain tip via the
   flight-recorder view.

The web DB (MySQL/TiDB via `DATABASE_URL`) is external state — use your
provider's snapshot/PITR; this runbook covers the bot host only.

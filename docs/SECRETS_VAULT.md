# Secrets Vault — operator keys survive a wiped `.env`

## The problem it solves
The operator's money/auth-critical secrets (`BITGET_API_KEY` / `BITGET_API_SECRET` /
`BITGET_PASSPHRASE`, `TELEGRAM_BOT_TOKEN`, LLM + other venue keys) live in `.env`.
A redeploy that **wipes `.env`** left the bot unable to authenticate → Bitget
`40012` → open positions could not be protected (the recurring "naked position"
incident).

## How it works
On boot, right after `.env` is loaded and **before** config is read
(`bot/config.py`), `bot/core/secrets_vault.py::seed_and_restore()` runs:

- **secret present in the environment** → encrypt + persist it to
  `data/secrets_vault.enc` (keeps the vault fresh every start);
- **secret missing but in the vault** → decrypt + inject it back into the
  environment (self-heal), with a loud `CRITICAL` audit log.

The Fernet master key is shared with the per-user `/connect` store and is
**persisted to `data/.exchange_secret.key`** — even when it comes from
`RUNECLAW_SECRETS_KEY` — so wiping `.env` (which removes that env var) falls back
to the same key on disk and never orphans the ciphertext.

## The one requirement: `data/` must persist
The vault recovers secrets **as long as `data/` survives a redeploy**. If a
redeploy wipes **both** `.env` and `data/`, the secrets are gone and nothing can
recover them. So pair the vault with a persistent data dir:

```bash
./deploy.sh                 # moves .env + data/ to $PERSIST_DIR and symlinks them back
# or mount a volume at ./data and keep .env outside the wiped path
```

`RUNECLAW_SECRETS_KEY` is the belt-and-braces: set it in the environment (a
secret manager, systemd `EnvironmentFile`, etc.) and the vault is recoverable
even if `data/` is also lost — because the master key comes from outside.

## Config
| Var | Default | Meaning |
|-----|---------|---------|
| `SECRETS_VAULT_ENABLED` | `true` | Master switch. |
| `RUNECLAW_VAULT_KEYS` | — | Comma-separated extra env keys to manage beyond the built-in set. |
| `RUNECLAW_SECRETS_KEY` | — | Fernet master key; if set it's also mirrored to `data/.exchange_secret.key`. |
| `RUNECLAW_STATE_DIR` | `data` | Where the vault + master key live. |

## Safety
No-op and creates no files when disabled, when `cryptography` is unavailable, or
when there is nothing to seed/restore. Never raises — a vault error can never
block startup. The encrypted file and master key are `0600` and gitignored
(`data/*`). Log lines only ever contain key **names**, never values.

Covered by `tests/test_secrets_vault.py` (seed→restore round-trip, wiped-env
self-heal, master-key-survives-`RUNECLAW_SECRETS_KEY`-wipe, and the no-op paths).

"""
Operator secrets vault — survive a wiped .env.

The operator's money/auth-critical secrets (BITGET_API_KEY/SECRET/PASSPHRASE,
TELEGRAM_BOT_TOKEN, LLM/provider keys, other venue keys) live in .env. A
redeploy that wipes .env leaves the bot unable to authenticate — the recurring
Bitget 40012 "naked position" incident. ``data/`` persists across redeploys
(deploy.sh symlinks it out of the repo / it's a mounted volume), so this mirrors
those secrets there, ENCRYPTED at rest, and restores any the environment has
lost on the next boot — before CONFIG reads the environment.

Boot flow — call ``seed_and_restore()`` right after ``load_dotenv`` (see
bot/config.py). For each managed key:

    present in env      -> encrypt + persist to the vault  (keeps it fresh)
    absent but in vault -> decrypt + inject into os.environ (self-heal)

Guarantee: recovery works **as long as ``data/`` persists**. If BOTH .env and
``data/`` are wiped, the secrets are gone and nothing can recover them — so pair
this with a persistent data dir (deploy.sh's PERSIST_DIR / a mounted volume).
The Fernet master key is shared with the per-user credential store and is
persisted to ``data/`` even when it comes from ``RUNECLAW_SECRETS_KEY``, so an
env wipe never orphans the ciphertext.

Safety: gated by ``SECRETS_VAULT_ENABLED`` (default on). Fully no-op-safe — it
creates no files and imports no crypto when the feature is disabled,
cryptography is unavailable, or there is simply nothing to seed or restore
(keeps tests and fresh checkouts clean). Never raises.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("runeclaw.secrets_vault")

# Basename of the shared Fernet master key (matches bot.core.exchange_credentials
# so the vault and the per-user store use ONE key under the same data dir).
_MASTER_KEY_BASENAME = ".exchange_secret.key"
_VAULT_BASENAME = "secrets_vault.enc"

# Money/auth-critical operator secrets whose loss breaks the bot. Extend at
# runtime with RUNECLAW_VAULT_KEYS (comma-separated).
_DEFAULT_MANAGED = (
    "BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE",
    "TELEGRAM_BOT_TOKEN",
    "LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
    "GROQ_API_KEY", "DEEPSEEK_API_KEY", "ALIBABA_API_KEY", "MISTRAL_API_KEY",
    "TOGETHER_API_KEY", "OPENROUTER_API_KEY",
    "HYPERLIQUID_API_KEY", "HYPERLIQUID_API_SECRET", "HYPERLIQUID_WALLET_ADDRESS",
    "BYBIT_API_KEY", "BYBIT_API_SECRET",
    "BINGX_API_KEY", "BINGX_API_SECRET",
    "ONCHAIN_API_KEY",
)


def _state_dir() -> str:
    return os.environ.get("RUNECLAW_STATE_DIR", "data")


def _vault_file() -> str:
    return os.path.join(_state_dir(), _VAULT_BASENAME)


def _key_file() -> str:
    return os.path.join(_state_dir(), _MASTER_KEY_BASENAME)


def _enabled() -> bool:
    return os.environ.get("SECRETS_VAULT_ENABLED", "true").strip().lower() \
        in ("1", "true", "yes", "on")


def _managed_keys() -> tuple[str, ...]:
    extra = os.environ.get("RUNECLAW_VAULT_KEYS", "")
    extras = tuple(k.strip() for k in extra.split(",") if k.strip())
    # De-dup while preserving order.
    seen: dict[str, None] = {}
    for k in _DEFAULT_MANAGED + extras:
        seen.setdefault(k, None)
    return tuple(seen)


def _cipher():
    """Fernet cipher on the shared master key, or None if crypto is unavailable.
    Reuses the per-user store's key loader (now wipe-hardened) so both share one
    key, resolved from the CURRENT state dir."""
    try:
        from cryptography.fernet import Fernet
        from bot.core.exchange_credentials import _load_or_create_master_key
    except Exception as exc:  # pragma: no cover - crypto is an optional extra
        log.debug("secrets vault: cryptography unavailable (%s)", exc)
        return None
    try:
        return Fernet(_load_or_create_master_key(key_file=_key_file()))
    except Exception as exc:
        log.warning("secrets vault: master key load failed: %s", exc)
        return None


def _load_vault(cipher) -> dict[str, str]:
    p = Path(_vault_file())
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        log.error("secrets vault file unreadable — ignoring it")
        return {}
    out: dict[str, str] = {}
    for k, ct in (raw.items() if isinstance(raw, dict) else []):
        try:
            out[k] = cipher.decrypt(str(ct).encode()).decode()
        except Exception:
            log.warning("secrets vault: could not decrypt %s (stale master key?)", k)
    return out


def _save_vault(cipher, plain: dict[str, str]) -> None:
    p = Path(_vault_file())
    enc = {k: cipher.encrypt(v.encode()).decode() for k, v in plain.items()}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(enc, indent=2), encoding="utf-8")
    try:
        os.chmod(str(tmp), 0o600)
    except OSError:
        pass
    tmp.rename(p)


def seed_and_restore() -> dict[str, list[str]]:
    """Mirror present env secrets into the vault; restore absent ones from it.

    Returns ``{"seeded": [...], "restored": [...]}`` (key NAMES only — never
    values) for logging/tests. No-op + no files created when disabled, crypto is
    absent, or there is nothing to do. Never raises."""
    summary: dict[str, list[str]] = {"seeded": [], "restored": []}
    try:
        if not _enabled():
            return summary
        keys = _managed_keys()
        present = any(os.environ.get(k, "").strip() for k in keys)
        vault_exists = Path(_vault_file()).exists()
        # Fast path: nothing in env AND no vault -> do nothing (no master key
        # created), so fresh checkouts / tests stay clean.
        if not present and not vault_exists:
            return summary
        cipher = _cipher()
        if cipher is None:
            return summary
        stored = _load_vault(cipher)
        changed = False
        for k in keys:
            env_val = os.environ.get(k, "").strip()
            if env_val:
                if stored.get(k) != env_val:
                    stored[k] = env_val
                    changed = True
                    summary["seeded"].append(k)
            elif stored.get(k):
                os.environ[k] = stored[k]
                summary["restored"].append(k)
        if changed:
            try:
                _save_vault(cipher, stored)
            except OSError as exc:
                log.error("secrets vault: save failed: %s", exc)
        if summary["restored"]:
            log.critical(
                "SECRETS VAULT restored %d secret(s) missing from the "
                "environment: %s — the bot is running on vault-backed "
                "credentials. Restore your .env and ensure data/ persists "
                "across redeploys.",
                len(summary["restored"]), ", ".join(summary["restored"]))
        if summary["seeded"]:
            log.info("secrets vault: mirrored %d secret(s) from the environment",
                     len(summary["seeded"]))
    except Exception as exc:  # pragma: no cover - must never block startup
        log.debug("secrets vault: seed_and_restore skipped: %s", exc)
    return summary

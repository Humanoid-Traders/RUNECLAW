"""
LLM key health — registry, validation, and deterministic key selection.

Recurring live incident (2026-07-11): the bot has SEVERAL writable slots for
the Anthropic key — /setllm runtime BYOK, ANTHROPIC_API_KEY, the primary
LLM_API_KEY, per-tier LLM_TIER_*_KEY — and different resolution paths read
different slots depending on provider coincidences. A stale/typo'd key in ANY
slot could capture the autonomous call path after a restart or /setllm, and
nothing showed WHICH slot was live or whether its key even worked. The
operator chased three different key fingerprints through one afternoon.

This module makes keys first-class citizens:
  - a registry of key fingerprints -> valid / invalid / unchecked
    (in-memory; keys marked invalid on real 401s, valid on real successes)
  - a cheap real-call validator (1 output token) for preflighting a key
    BEFORE it is stored or used
  - deterministic candidate ordering + selection for the operator's
    Anthropic key, skipping known-invalid keys (auto-healing: one 401 and
    the resolver moves to the next candidate instead of failing forever)

Never logs a full key — fingerprints only (same format as
LLMConfig.key_fingerprint).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Optional

_LOCK = threading.RLock()
# fingerprint -> {"status": "valid"|"invalid"|"unchecked", "error": str,
#                 "checked_at": float, "source": str}
_REGISTRY: dict[str, dict] = {}

VALID = "valid"
INVALID = "invalid"
UNCHECKED = "unchecked"


def fp(key: str) -> str:
    """Safe display fingerprint — first 6 chars + sha256[:8]. Never the key."""
    if not key:
        return "NOT SET"
    return f"{key[:6]}...{hashlib.sha256(key.encode()).hexdigest()[:8]}"


def looks_like_auth_error(err: str) -> bool:
    """True when an exception string is an authentication failure — the
    signature that a KEY (not the model, not quota) is bad."""
    e = (err or "").lower()
    return ("authentication_error" in e or "invalid x-api-key" in e
            or "401" in e or "invalid api key" in e)


def mark_invalid(key: str, error: str = "", source: str = "") -> None:
    if not key:
        return
    with _LOCK:
        _REGISTRY[fp(key)] = {
            "status": INVALID, "error": str(error)[:200],
            "checked_at": time.time(), "source": source,
        }


def mark_valid(key: str, source: str = "") -> None:
    if not key:
        return
    with _LOCK:
        _REGISTRY[fp(key)] = {
            "status": VALID, "error": "",
            "checked_at": time.time(), "source": source,
        }


def is_known_invalid(key: str) -> bool:
    if not key:
        return False
    with _LOCK:
        return _REGISTRY.get(fp(key), {}).get("status") == INVALID


def status_of(key: str) -> str:
    if not key:
        return UNCHECKED
    with _LOCK:
        return _REGISTRY.get(fp(key), {}).get("status", UNCHECKED)


def snapshot() -> dict[str, dict]:
    with _LOCK:
        return {k: dict(v) for k, v in _REGISTRY.items()}


def reset() -> None:
    """Test hook — clear the registry."""
    with _LOCK:
        _REGISTRY.clear()


def validate_anthropic_key(key: str, model: str = "claude-sonnet-4-6",
                           timeout: float = 15.0) -> tuple[str, str]:
    """Preflight a key with ONE real 1-output-token call.

    Returns (status, detail): "valid" (answered), "invalid" (auth/model
    rejected — safe to refuse storing this key), or "unchecked" (transient/
    network — do NOT condemn the key). Records the outcome in the registry.
    Costs ~$0.0001; blocking — call via asyncio.to_thread from async code.
    """
    if not key:
        return UNCHECKED, "no key"
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=timeout)
        client.messages.create(
            model=model, max_tokens=1,
            messages=[{"role": "user", "content": "ping"}])
        mark_valid(key, source="preflight")
        return VALID, "key answered"
    except Exception as exc:  # noqa: BLE001 — classify by shape, never raise
        err = str(exc)
        low = err.lower()
        if looks_like_auth_error(err):
            mark_invalid(key, err, source="preflight")
            return INVALID, err[:200]
        if "not_found" in low or "404" in low:
            # Key may be fine but the MODEL id is wrong — still unusable as
            # configured; report invalid so the operator fixes it now.
            mark_invalid(key, err, source="preflight")
            return INVALID, err[:200]
        # Rate limit / network / SDK missing: unknown, not condemned.
        return UNCHECKED, err[:200]


def anthropic_candidates(
        primary_config=None, runtime_config=None) -> list[tuple[str, str]]:
    """Ordered, fingerprint-deduped candidate keys for the operator's
    Anthropic routing:
      1. runtime BYOK (/setllm this session — explicit operator intent)
      2. ANTHROPIC_API_KEY (the dedicated env slot)
      3. the primary .env key, when the primary provider is Anthropic
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(source: str, key: Optional[str]) -> None:
        if not key:
            return
        f = fp(key)
        if f in seen:
            return
        seen.add(f)
        out.append((source, key))

    rc = runtime_config
    if rc is not None and getattr(rc, "api_key", ""):
        prov = getattr(rc, "provider", None)
        prov_val = getattr(prov, "value", prov)
        if str(prov_val) == "anthropic":
            _add("runtime (BYOK)", rc.api_key)
    _add("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))
    pc = primary_config
    if pc is not None and getattr(pc, "api_key", ""):
        prov = getattr(pc, "provider", None)
        prov_val = getattr(prov, "value", prov)
        if str(prov_val) == "anthropic":
            _add("primary (.env)", pc.api_key)
    return out


def pick_anthropic_key(primary_config=None,
                       runtime_config=None) -> tuple[str, str]:
    """First candidate NOT known-invalid; if every candidate is condemned,
    return the first anyway (fail-open — the error will re-surface and keep
    the operator informed rather than silently going keyless)."""
    cands = anthropic_candidates(primary_config, runtime_config)
    if not cands:
        return "", ""
    for source, key in cands:
        if not is_known_invalid(key):
            return source, key
    return cands[0]

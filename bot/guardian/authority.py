"""Scoped, revocable, non-custodial Authority Envelope — Guardian's custody layer.

    Nothing moves user funds without a human-set, revocable authority envelope.
    The AI proposes. Deterministic controls authorize. The wallet enforces.

Where ``intent_policy`` governs *what trades are allowed* (strategy discipline),
this module governs *what the credential is even permitted to do* (custody). An
**Authority Envelope** is a human-set, versioned, content-hashed grant that bounds
a linked exchange key / wallet session: which venues, which market types, a
per-trade and a rolling-daily notional ceiling, whether it may withdraw at all
(default NO — a hard line), when it self-expires, and a human kill-switch.

Two invariants, enforced mechanically:

1. **Tighten-only.** ``compile_envelope`` clamps every ceiling against the engine's
   authoritative cap and the venues the platform actually supports. A compiled
   envelope is always *at least as restrictive* as the engine — the AI can never
   author itself more authority than a human already granted.
2. **Fail-closed.** Unlike the fail-*open* telemetry modules, ``authorize`` returns
   ``deny`` for a missing/expired/revoked envelope, an unknown action kind, a
   malformed action, or any ceiling breach. ``allow`` is returned only when EVERY
   check passes. Withdrawal is denied by default and needs a double opt-in
   (``withdraw_allowed=true`` AND an allowlisted destination).

Pure and dependency-light (no engine, config, exchange, clock or network import),
so the whole layer is trivially unit-testable and the authorization path can never
be broken by an envelope artifact or a bad import.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

ENVELOPE_VERSION = 1

VALID_MODES = ("off", "shadow", "enforce")

# Action kinds an agent (or a human) can ask to perform through a credential.
# Only ``trade`` is ever fund-*bounded* rather than fund-*moving-out*; withdraw and
# transfer move value OUT of the account and are denied unless doubly opted in.
_TRADE_KINDS = ("trade",)
_EXFIL_KINDS = ("withdraw", "transfer")

# Numeric ceilings, with the engine-cap key each clamps against (None → a NEW
# restriction with no engine equivalent, so it only ever tightens). All are
# "max" direction: the envelope value may not exceed the engine cap.
_CEILINGS: dict[str, Optional[str]] = {
    "max_notional_per_trade_usd": "max_notional_per_trade_usd",
    "max_notional_daily_usd":     "max_notional_daily_usd",
}


# ── coercion helpers (never raise) ────────────────────────────────────

def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _pos(x: Any) -> Optional[float]:
    """A strictly-positive finite number, else None (a non-positive ceiling is
    meaningless and is dropped rather than trusted)."""
    v = _num(x)
    return v if (v is not None and v > 0) else None


def _base_symbol(sym: Any) -> str:
    """Normalise 'BTC/USDT:USDT' / 'BTCUSDT' → 'BTC' for symbol matching."""
    s = str(sym or "").upper().strip()
    s = s.split(":", 1)[0]
    for q in ("/USDT", "/USDC", "/USD"):
        s = s.replace(q, "")
    for q in ("USDT", "USDC", "USD"):
        if s.endswith(q) and len(s) > len(q):
            s = s[: -len(q)]
    return s


def _addr(x: Any) -> str:
    """Normalise an address/destination for allowlist comparison (lowercased)."""
    return str(x or "").strip().lower()


def _strlist(items: Any, *, lower: bool = True) -> list[str]:
    out: list[str] = []
    for x in (items or []):
        s = str(x).strip()
        if not s:
            continue
        out.append(s.lower() if lower else s)
    return sorted(set(out))


# ── canonical hashing (order-independent, whitespace-stable) ───────────

# The fields that constitute an envelope's ENFORCEABLE identity. Cosmetic fields
# (label, source_text, mode) are excluded so the hash is stable across a
# write→reload round-trip and across a shadow→enforce mode flip.
_IDENTITY_FIELDS = (
    "allowed_venues", "allowed_market_types",
    "max_notional_per_trade_usd", "max_notional_daily_usd",
    "withdraw_allowed", "withdraw_allowlist",
    "symbol_allowlist", "symbol_blocklist",
    "expiry_ts", "revoked",
)


def _canon(env: dict) -> str:
    core = {k: env.get(k) for k in _IDENTITY_FIELDS}
    return json.dumps({"v": ENVELOPE_VERSION, "core": core},
                      sort_keys=True, default=str)


def envelope_hash(env: dict) -> str:
    return hashlib.sha256(_canon(env).encode("utf-8")).hexdigest()


# ── compile: validate + clamp-to-tighten + hash ───────────────────────

def compile_envelope(spec: dict,
                     engine_caps: Optional[dict] = None,
                     venue_universe: Optional[list[str]] = None) -> dict:
    """Validate a raw authority spec and produce a compiled, hashed envelope.

    ``spec`` = ``{label?, source_text?, mode?, allowed_venues, allowed_market_types,
    max_notional_per_trade_usd, max_notional_daily_usd, withdraw_allowed,
    withdraw_allowlist, symbol_allowlist, symbol_blocklist, expiry_ts, revoked}``.

    ``engine_caps`` maps ceiling keys to the engine's authoritative limit; each
    ceiling is CLAMPED so the envelope can only tighten. ``venue_universe``, when
    given, restricts ``allowed_venues`` to venues the platform actually supports
    (unknown venues are dropped with a warning — you cannot be granted authority
    over a venue that does not exist).

    Withdrawal defaults to DENIED: ``withdraw_allowed`` is only ``True`` when the
    spec sets it truthy AND supplies a non-empty ``withdraw_allowlist`` — a hard
    double opt-in. Returns a compiled envelope dict.
    """
    engine_caps = engine_caps or {}
    warnings: list[str] = []

    venues = _strlist(spec.get("allowed_venues"))
    if venue_universe is not None:
        universe = {str(v).lower() for v in venue_universe}
        kept = [v for v in venues if v in universe]
        for v in venues:
            if v not in universe:
                warnings.append(f"venue '{v}' is not a supported venue — dropped")
        venues = kept

    market_types = _strlist(spec.get("allowed_market_types"))

    ceilings: dict[str, Optional[float]] = {}
    for key, cap_key in _CEILINGS.items():
        val = _pos(spec.get(key))
        if val is None:
            if spec.get(key) is not None:
                warnings.append(f"{key}: non-positive/invalid value dropped")
            ceilings[key] = None
            continue
        if cap_key and cap_key in engine_caps:
            cap = _pos(engine_caps.get(cap_key))
            if cap is not None and val > cap:
                warnings.append(
                    f"{key} {val:g} exceeds engine cap {cap:g} — clamped to {cap:g}")
                val = cap
        ceilings[key] = round(val, 2)

    # Withdrawal: default-deny, double opt-in.
    allowlist = _strlist(spec.get("withdraw_allowlist"))
    withdraw_flag = bool(spec.get("withdraw_allowed"))
    withdraw_allowed = withdraw_flag and bool(allowlist)
    if withdraw_flag and not allowlist:
        warnings.append("withdraw_allowed set but no withdraw_allowlist — "
                        "withdrawal stays DENIED (a destination allowlist is required)")

    symbol_allow = _strlist([_base_symbol(x) for x in (spec.get("symbol_allowlist") or [])])
    symbol_block = _strlist([_base_symbol(x) for x in (spec.get("symbol_blocklist") or [])])

    expiry_ts = _num(spec.get("expiry_ts"))
    expiry_ts = int(expiry_ts) if expiry_ts is not None else None

    mode = spec.get("mode")
    if mode not in VALID_MODES:
        mode = "off"   # safe default: an authored-but-unwired envelope blocks nothing

    env: dict[str, Any] = {
        "version": ENVELOPE_VERSION,
        "label": str(spec.get("label") or "").strip()[:80] or "Untitled authority",
        "source_text": str(spec.get("source_text") or "").strip()[:500],
        "mode": mode,
        "allowed_venues": venues,
        "allowed_market_types": market_types,
        "max_notional_per_trade_usd": ceilings["max_notional_per_trade_usd"],
        "max_notional_daily_usd": ceilings["max_notional_daily_usd"],
        "withdraw_allowed": withdraw_allowed,
        "withdraw_allowlist": [_addr(a) for a in allowlist],
        "symbol_allowlist": symbol_allow,
        "symbol_blocklist": symbol_block,
        "expiry_ts": expiry_ts,
        "revoked": bool(spec.get("revoked")),
        "warnings": warnings,
    }
    h = envelope_hash(env)
    env["envelope_id"] = "env_" + h[:8]
    env["compiled_hash"] = h
    return env


def revoke(env: dict) -> dict:
    """Return a copy of the envelope with the human kill-switch set. Revocation
    changes identity (the hash), so a revoked envelope can never be confused with
    its live predecessor."""
    out = dict(env)
    out["revoked"] = True
    h = envelope_hash(out)
    out["envelope_id"] = "env_" + h[:8]
    out["compiled_hash"] = h
    return out


# ── authorize: deterministic, FAIL-CLOSED decision ────────────────────

def authorize(envelope: Optional[dict], action: dict, *,
              now_ts: int, spent_today_usd: Any = 0.0) -> dict:
    """Decide whether ``action`` is within ``envelope``. FAIL-CLOSED.

    ``action`` = ``{kind, venue?, market_type?, notional_usd?, asset?, dest?}``
    where ``kind`` ∈ {trade, withdraw, transfer}. ``now_ts`` is the caller's clock
    (ms or s epoch — compared only against ``expiry_ts`` in the same unit).
    ``spent_today_usd`` is the rolling 24h notional the caller has already spent
    under this authority (the module holds no state).

    Returns ``{decision, reasons, envelope_id, hash, kind, checked}`` where
    ``decision`` is ``"allow"`` only when there are zero deny reasons.
    """
    result: dict[str, Any] = {
        "decision": "deny",
        "reasons": [],
        "envelope_id": (envelope or {}).get("envelope_id"),
        "hash": (envelope or {}).get("compiled_hash"),
        "kind": str((action or {}).get("kind") or "").lower().strip(),
        "checked": 0,
    }
    reasons: list[str] = result["reasons"]

    # 0) No envelope → no authority. (Fail-closed: absence never means "allow".)
    if not envelope or not isinstance(envelope, dict):
        reasons.append("no authority envelope — nothing is authorized")
        return result
    if not isinstance(action, dict):
        reasons.append("malformed action")
        return result

    # 1) Kill-switch and expiry apply to EVERY kind, first.
    if envelope.get("revoked"):
        reasons.append("authority is revoked")
    exp = _num(envelope.get("expiry_ts"))
    now = _num(now_ts)
    if exp is not None and now is not None and now > exp:
        reasons.append(f"authority expired (now {int(now)} > expiry {int(exp)})")

    kind = result["kind"]

    # 2) Withdraw / transfer: OUT-of-account value movement. Denied unless the
    #    envelope doubly opted in AND the destination is allowlisted.
    if kind in _EXFIL_KINDS:
        result["checked"] += 1
        if not envelope.get("withdraw_allowed"):
            reasons.append(f"{kind} is not permitted by this authority "
                           "(withdraw_allowed is false)")
        else:
            dest = _addr((action or {}).get("dest"))
            allowlist = [_addr(a) for a in (envelope.get("withdraw_allowlist") or [])]
            if not dest:
                reasons.append(f"{kind} requires a destination")
            elif dest not in allowlist:
                reasons.append(f"{kind} destination {dest} is not on the withdraw allowlist")
        result["decision"] = "allow" if not reasons else "deny"
        return result

    # 3) Trade: bounded by venue / market-type / symbol / notional ceilings.
    if kind in _TRADE_KINDS:
        venue = str(action.get("venue") or "").lower().strip()
        allowed_venues = envelope.get("allowed_venues") or []
        result["checked"] += 1
        if not allowed_venues:
            reasons.append("no venue is authorized (allowed_venues is empty)")
        elif venue and venue not in allowed_venues:
            reasons.append(f"venue '{venue}' is not authorized ({', '.join(allowed_venues)})")
        elif not venue:
            reasons.append("trade action is missing its venue")

        mtypes = envelope.get("allowed_market_types") or []
        mt = str(action.get("market_type") or "").lower().strip()
        if mtypes:
            result["checked"] += 1
            if not mt:
                reasons.append("trade action is missing its market_type")
            elif mt not in mtypes:
                reasons.append(f"market type '{mt}' is not authorized ({', '.join(mtypes)})")

        asset = _base_symbol(action.get("asset"))
        allow = envelope.get("symbol_allowlist") or []
        block = envelope.get("symbol_blocklist") or []
        if asset:
            if allow:
                result["checked"] += 1
                if asset not in allow:
                    reasons.append(f"{asset} is not in the authorized symbol set ({', '.join(allow)})")
            if block and asset in block:
                result["checked"] += 1
                reasons.append(f"{asset} is on the authority's blocklist")

        notional = _num(action.get("notional_usd"))
        per_trade = _num(envelope.get("max_notional_per_trade_usd"))
        if per_trade is not None:
            result["checked"] += 1
            if notional is None:
                reasons.append("trade notional is unknown — cannot authorize against the per-trade cap")
            elif notional > per_trade + 1e-9:
                reasons.append(f"trade notional ${notional:,.2f} exceeds per-trade cap ${per_trade:,.2f}")

        daily = _num(envelope.get("max_notional_daily_usd"))
        if daily is not None:
            result["checked"] += 1
            spent = _num(spent_today_usd) or 0.0
            n = notional if notional is not None else 0.0
            if spent + n > daily + 1e-9:
                reasons.append(
                    f"daily notional ${spent + n:,.2f} would exceed the daily cap "
                    f"${daily:,.2f} (already spent ${spent:,.2f})")

        result["decision"] = "allow" if not reasons else "deny"
        return result

    # 4) Unknown kind → fail closed.
    reasons.append(f"unknown action kind '{kind}'")
    return result


# ── human-readable rendering ──────────────────────────────────────────

def _money(v: Any) -> str:
    n = _num(v)
    return f"${n:,.2f}" if n is not None else "—"


def human_readable(env: Optional[dict]) -> str:
    """Plain-text (no markup) render of an envelope for operator review."""
    if not env or not isinstance(env, dict):
        return "No authority envelope (the credential is not authorized for any action)."
    lines: list[str] = []
    lines.append(f"{env.get('label', 'Untitled authority')}  ·  mode: "
                 f"{env.get('mode', 'off')}  ·  {env.get('envelope_id', '')}")
    if env.get("revoked"):
        lines.append("⛔ REVOKED — every action is denied.")
    venues = env.get("allowed_venues") or []
    lines.append(f"• Venues: {', '.join(venues) if venues else 'none authorized'}")
    mtypes = env.get("allowed_market_types") or []
    if mtypes:
        lines.append(f"• Market types: {', '.join(mtypes)}")
    lines.append(f"• Per-trade cap: {_money(env.get('max_notional_per_trade_usd'))}"
                 if env.get("max_notional_per_trade_usd") is not None
                 else "• Per-trade cap: none set")
    lines.append(f"• Daily cap: {_money(env.get('max_notional_daily_usd'))}"
                 if env.get("max_notional_daily_usd") is not None
                 else "• Daily cap: none set")
    if env.get("symbol_allowlist"):
        lines.append(f"• Only symbols: {', '.join(env['symbol_allowlist'])}")
    if env.get("symbol_blocklist"):
        lines.append(f"• Never symbols: {', '.join(env['symbol_blocklist'])}")
    if env.get("withdraw_allowed"):
        lines.append(f"• Withdraw: ALLOWED to {len(env.get('withdraw_allowlist') or [])} "
                     "allowlisted destination(s)")
    else:
        lines.append("• Withdraw: DENIED (non-custodial — funds cannot leave the account)")
    exp = env.get("expiry_ts")
    lines.append(f"• Expires at: {exp}" if exp is not None else "• Expires at: no expiry set")
    warnings = env.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Adjusted to stay within engine caps / supported venues:")
        for w in warnings[:8]:
            lines.append("  – " + str(w))
    return "\n".join(lines)

"""Formal Strategy Intent Compiler — the deterministic policy layer of Guardian.

    The AI proposes. Deterministic controls authorize.

A user states an intent in plain language ("keep me under 3x leverage, never more
than 20% in one coin, only majors, stop if I'm down 8% this week"). ``compile_nl``
turns that into a **compiled policy**: a versioned, content-hashed list of typed
rules. ``evaluate_policy`` then checks a candidate trade against that policy
**deterministically** — no LLM, no network, pure functions of the inputs — and
returns the violation strings the risk gate turns into rejections.

Two invariants make this safe on a live-money engine:

* **A policy may only TIGHTEN, never loosen.** ``compile_policy`` clamps every
  numeric rule against the engine's authoritative cap (a ``max_*`` rule can't be
  set higher than the engine cap; a ``min_*`` rule can't be set lower). The
  engine's own 23 checks always still run — the policy is an *additional* gate.
* **Enforcement is deterministic and fail-open per rule.** A rule that can't be
  evaluated (missing runtime data, malformed value) is skipped, never crashes —
  the engine's own caps remain the floor, so a policy bug can't halt trading.

Everything here is pure and dependency-light (no engine, config, or network
import), so the whole compiler is trivially unit-testable and the enforcement
path can never be broken by a policy artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

POLICY_VERSION = 1

# ── Rule catalogue ────────────────────────────────────────────────────
# Numeric rules carry a direction so the compiler knows how to clamp them to the
# engine cap ("max" → policy value must be ≤ engine cap; "min" → policy value
# must be ≥ engine cap) and the evaluator knows which way a violation points.
# `cap` names the engine-cap key the compiler clamps against (None → the rule is
# a NEW restriction with no engine equivalent, so it only ever tightens).
# `max_notional_pct` (not `max_position_pct`): at the risk-gate hook the value
# compared is `position_usd / sizing_equity` — position_usd is the final
# macro-adjusted, execution-capped *notional*, so the rule is a notional cap, not
# a risk-to-stop cap. Naming it honestly keeps the human-reviewable artifact true
# to what it enforces. (The engine cap it clamps against is still max_position_pct.)
_NUMERIC_RULES: dict[str, dict] = {
    "max_leverage":                {"dir": "max", "cap": "max_leverage",              "unit": "x"},
    "max_notional_pct":            {"dir": "max", "cap": "max_position_pct",          "unit": "%"},
    "max_symbol_exposure_pct":     {"dir": "max", "cap": "max_symbol_exposure_pct",   "unit": "%"},
    "max_portfolio_exposure_pct":  {"dir": "max", "cap": "max_portfolio_exposure_pct","unit": "%"},
    "max_open_positions":          {"dir": "max", "cap": "max_open_positions",        "unit": ""},
    "min_confidence":              {"dir": "min", "cap": "min_confidence",            "unit": ""},
    "min_rr":                      {"dir": "min", "cap": "min_risk_reward",           "unit": "R"},
    "max_daily_loss_pct":          {"dir": "max", "cap": "max_daily_loss_pct",        "unit": "%"},
    "max_drawdown_pct":            {"dir": "max", "cap": "max_drawdown_pct",          "unit": "%"},
    "min_free_margin_pct":         {"dir": "min", "cap": None,                        "unit": "%"},
}
_LIST_RULES = {"allowed_symbols", "blocked_symbols", "allowed_strategy_types"}
_ENUM_RULES = {"direction"}   # value ∈ {"long_only", "short_only"}

VALID_MODES = ("off", "shadow", "enforce")

# Majors shorthand used by the NL compiler ("only majors").
_MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP"]


# ── coercion helpers (never raise) ────────────────────────────────────

def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _base_symbol(sym: Any) -> str:
    """Normalise 'BTC/USDT:USDT' / 'BTCUSDT' → 'BTC' for symbol matching."""
    s = str(sym or "").upper().strip()
    s = s.split(":", 1)[0]
    s = s.replace("/USDT", "").replace("/USD", "").replace("/USDC", "")
    for q in ("USDT", "USDC", "USD"):
        if s.endswith(q) and len(s) > len(q):
            s = s[: -len(q)]
    return s


def _canon(rules: list[dict], meta: dict) -> str:
    """Canonical JSON for hashing — order-independent, whitespace-stable."""
    norm = sorted(
        (
            {
                "type": r.get("type"),
                "value": (sorted(_base_symbol(x) for x in r["value"])
                          if isinstance(r.get("value"), list) else r.get("value")),
            }
            for r in rules
        ),
        key=lambda r: (str(r["type"]), json.dumps(r["value"], sort_keys=True, default=str)),
    )
    return json.dumps({"v": POLICY_VERSION, "meta": meta, "rules": norm},
                      sort_keys=True, default=str)


def policy_hash(rules: list[dict], meta: Optional[dict] = None) -> str:
    return hashlib.sha256(_canon(rules, meta or {}).encode("utf-8")).hexdigest()


# ── compile: validate + clamp-to-tighten + hash ───────────────────────

def compile_policy(spec: dict, engine_caps: Optional[dict] = None) -> dict:
    """Validate a raw policy spec and produce a compiled, hashed policy.

    ``spec`` = ``{label?, source_text?, mode?, rules: [{type, value}, ...]}``.
    ``engine_caps`` maps cap keys (see ``_NUMERIC_RULES``) to the engine's
    authoritative limit; each numeric rule is CLAMPED so the policy can only
    tighten. Unknown rule types and unparseable values are dropped into
    ``warnings`` rather than raising — a bad rule can never yield an
    enforceable-but-wrong policy.

    Returns a policy dict: ``{version, policy_id, label, source_text, mode,
    rules, warnings, compiled_hash}``.
    """
    engine_caps = engine_caps or {}
    warnings: list[str] = []
    out_rules: list[dict] = []
    seen: set[str] = set()

    for raw in (spec.get("rules") or []):
        if not isinstance(raw, dict):
            continue
        rtype = raw.get("type")
        if rtype in seen:
            warnings.append(f"duplicate rule '{rtype}' ignored")
            continue

        if rtype in _NUMERIC_RULES:
            val = _num(raw.get("value"))
            if val is None:
                warnings.append(f"{rtype}: non-numeric value dropped")
                continue
            spec_meta = _NUMERIC_RULES[rtype]
            cap_key = spec_meta["cap"]
            if cap_key is not None and cap_key in engine_caps:
                cap = _num(engine_caps.get(cap_key))
                if cap is not None:
                    if spec_meta["dir"] == "max" and val > cap:
                        warnings.append(
                            f"{rtype} {val:g} loosens the engine cap {cap:g} — clamped to {cap:g}")
                        val = cap
                    elif spec_meta["dir"] == "min" and val < cap:
                        warnings.append(
                            f"{rtype} {val:g} loosens the engine floor {cap:g} — clamped to {cap:g}")
                        val = cap
            out_rules.append({"type": rtype, "value": round(val, 6)})
            seen.add(rtype)

        elif rtype in _LIST_RULES:
            items = raw.get("value")
            if not isinstance(items, list) or not items:
                warnings.append(f"{rtype}: empty/invalid list dropped")
                continue
            if rtype == "allowed_strategy_types":
                vals = [str(x).lower().strip() for x in items if str(x).strip()]
            else:
                vals = sorted({_base_symbol(x) for x in items if _base_symbol(x)})
            if not vals:
                warnings.append(f"{rtype}: no valid entries")
                continue
            out_rules.append({"type": rtype, "value": vals})
            seen.add(rtype)

        elif rtype in _ENUM_RULES:
            v = str(raw.get("value") or "").lower().strip()
            if v not in ("long_only", "short_only"):
                warnings.append(f"direction: '{v}' not one of long_only/short_only")
                continue
            out_rules.append({"type": "direction", "value": v})
            seen.add(rtype)
        else:
            warnings.append(f"unknown rule type '{rtype}' dropped")

    mode = spec.get("mode")
    if mode not in VALID_MODES:
        mode = "shadow"   # safe default: observe, don't block
    meta = {"label": str(spec.get("label") or "").strip()[:80]}
    h = policy_hash(out_rules, meta)
    return {
        "version": POLICY_VERSION,
        "policy_id": "pol_" + h[:8],
        "label": meta["label"] or "Untitled policy",
        "source_text": str(spec.get("source_text") or "").strip()[:500],
        "mode": mode,
        "rules": out_rules,
        "warnings": warnings,
        "compiled_hash": h,
    }


# ── evaluate: deterministic violation check (no LLM, no network) ───────

def evaluate_policy(policy: Optional[dict], ctx: dict) -> dict:
    """Check a candidate trade context against a compiled policy.

    ``ctx`` provides the runtime facts a rule inspects (all optional — a missing
    fact makes that rule SKIP, never fail, so the engine's own caps stay the
    floor)::

        {leverage, position_pct, symbol_exposure_pct, portfolio_exposure_pct,
         confidence, rr, daily_loss_pct, drawdown_pct, free_margin_pct,
         asset, strategy_type, direction}

    Returns ``{policy_id, hash, mode, verdict, violations, checked, skipped}``.
    ``verdict`` is ``"reject"`` when any rule is violated, else ``"pass"``. The
    caller decides what to do with it (enforce → reject; shadow → log only).
    """
    result = {
        "policy_id": (policy or {}).get("policy_id"),
        "hash": (policy or {}).get("compiled_hash"),
        "mode": (policy or {}).get("mode", "off"),
        "verdict": "pass",
        "violations": [],
        "checked": 0,
        "skipped": 0,
    }
    if not policy or not policy.get("rules"):
        return result

    viols: list[str] = result["violations"]

    def _cmp_max(key, rule_val, label, unit=""):
        v = _num(ctx.get(key))
        if v is None:
            result["skipped"] += 1
            return
        result["checked"] += 1
        if v > rule_val + 1e-9:
            viols.append(f"{label} {v:g}{unit} exceeds limit {rule_val:g}{unit}")

    def _cmp_min(key, rule_val, label, unit=""):
        v = _num(ctx.get(key))
        if v is None:
            result["skipped"] += 1
            return
        result["checked"] += 1
        if v < rule_val - 1e-9:
            viols.append(f"{label} {v:g}{unit} below required {rule_val:g}{unit}")

    for rule in policy["rules"]:
        try:
            rtype = rule.get("type")
            val = rule.get("value")
            if rtype == "max_leverage":
                _cmp_max("leverage", val, "leverage", "x")
            elif rtype == "max_notional_pct":
                _cmp_max("notional_pct", val, "position notional", "%")
            elif rtype == "max_open_positions":
                # "adding one more would exceed the cap": violate when the
                # current open count already meets/exceeds the limit. Uses the
                # effective (live-aware) count the caller supplies in ctx.
                n = _num(ctx.get("open_positions"))
                if n is None:
                    result["skipped"] += 1
                else:
                    result["checked"] += 1
                    if n >= val - 1e-9:
                        viols.append(f"open positions {n:g} at/over limit {val:g}")
            elif rtype == "max_symbol_exposure_pct":
                _cmp_max("symbol_exposure_pct", val, "symbol exposure", "%")
            elif rtype == "max_portfolio_exposure_pct":
                _cmp_max("portfolio_exposure_pct", val, "portfolio exposure", "%")
            elif rtype == "min_confidence":
                _cmp_min("confidence", val, "confidence")
            elif rtype == "min_rr":
                _cmp_min("rr", val, "reward:risk", "R")
            elif rtype == "max_daily_loss_pct":
                _cmp_max("daily_loss_pct", val, "daily loss", "%")
            elif rtype == "max_drawdown_pct":
                _cmp_max("drawdown_pct", val, "drawdown", "%")
            elif rtype == "min_free_margin_pct":
                _cmp_min("free_margin_pct", val, "free margin", "%")
            elif rtype == "allowed_symbols":
                asset = _base_symbol(ctx.get("asset"))
                if not asset:
                    result["skipped"] += 1
                    continue
                result["checked"] += 1
                if asset not in val:
                    viols.append(f"{asset} is not in the allowed set ({', '.join(val)})")
            elif rtype == "blocked_symbols":
                asset = _base_symbol(ctx.get("asset"))
                if not asset:
                    result["skipped"] += 1
                    continue
                result["checked"] += 1
                if asset in val:
                    viols.append(f"{asset} is on the blocked list")
            elif rtype == "allowed_strategy_types":
                st = str(ctx.get("strategy_type") or "").lower().strip()
                if not st:
                    result["skipped"] += 1
                    continue
                result["checked"] += 1
                if st not in val:
                    viols.append(f"strategy '{st}' is not in the allowed set ({', '.join(val)})")
            elif rtype == "direction":
                d = str(ctx.get("direction") or "").upper().strip()
                if not d:
                    result["skipped"] += 1
                    continue
                result["checked"] += 1
                if val == "long_only" and d == "SHORT":
                    viols.append("policy is long-only; this is a SHORT")
                elif val == "short_only" and d == "LONG":
                    viols.append("policy is short-only; this is a LONG")
        except Exception:
            # A single broken rule must never crash enforcement — skip it.
            result["skipped"] += 1

    result["verdict"] = "reject" if viols else "pass"
    return result


# ── NL → rules (deterministic parser) ─────────────────────────────────

_PCT = r"(\d+(?:\.\d+)?)\s*%?"


def compile_nl(text: str) -> dict:
    """Deterministically extract policy rules from a plain-language intent.

    Covers the common phrasings a trader actually writes. Returns
    ``{rules, matched, unparsed}`` — ``matched`` are human-readable notes on what
    was understood, ``unparsed`` echoes the raw text so the UI can show what
    still needs a manual rule. This is the "AI proposes" step: its output is a
    plain rule list the user reviews and confirms before it ever compiles into an
    enforceable policy.
    """
    t = " " + (text or "").lower().strip() + " "
    rules: list[dict] = []
    matched: list[str] = []

    def add(rule, note):
        rules.append(rule)
        matched.append(note)

    # leverage: "under 3x", "max leverage 3", "no more than 3x leverage"
    m = re.search(r"(?:under|max(?:imum)?|below|no more than|less than)\s*"
                  r"(\d+(?:\.\d+)?)\s*x?\s*(?:leverage|lev)", t) \
        or re.search(r"(\d+(?:\.\d+)?)\s*x\s*(?:leverage|lev|max)", t)
    if m:
        add({"type": "max_leverage", "value": float(m.group(1))},
            f"max leverage {m.group(1)}x")

    # per-coin / per-symbol exposure: "20% in one coin/symbol/asset/position"
    m = re.search(_PCT + r"\s*(?:in|per|max(?:imum)?)?\s*(?:any\s*)?"
                  r"(?:one|single|per)?\s*(?:coin|symbol|asset|token|name)", t)
    if m:
        add({"type": "max_symbol_exposure_pct", "value": float(m.group(1))},
            f"max {m.group(1)}% per symbol")

    # total / portfolio exposure: "60% total/portfolio/invested/deployed"
    m = re.search(_PCT + r"\s*(?:total|portfolio|overall|invested|deployed|exposure)", t)
    if m:
        add({"type": "max_portfolio_exposure_pct", "value": float(m.group(1))},
            f"max {m.group(1)}% total exposure")

    # per-trade size: "5% per trade/position size"
    m = re.search(_PCT + r"\s*(?:per|a|each)\s*(?:trade|position|entry)", t) \
        or re.search(r"position\s*size\s*(?:of\s*)?" + _PCT, t)
    if m:
        add({"type": "max_notional_pct", "value": float(m.group(1))},
            f"max {m.group(1)}% notional per trade")

    # open-position cap: "cap 3 open / max 3 positions / at most 3 open trades"
    m = re.search(r"(?:cap|max(?:imum)?|at most|no more than|up to)\s*(\d+)\s*"
                  r"(?:open\s*)?(?:positions?|trades?|open)", t)
    if m:
        add({"type": "max_open_positions", "value": int(m.group(1))},
            f"max {m.group(1)} open positions")

    # free margin: "leave 30% free/cash/dry"
    m = re.search(r"(?:leave|keep|hold|reserve)\s*(?:at least\s*)?" + _PCT
                  + r"\s*(?:free|cash|dry|reserve|margin|uninvested)", t)
    if m:
        add({"type": "min_free_margin_pct", "value": float(m.group(1))},
            f"keep {m.group(1)}% free margin")

    # weekly / overall drawdown: "down 8% this week / drawdown / stop if down 8%"
    m = re.search(r"(?:down|lose|drawdown|drop|below)\s*" + _PCT
                  + r"\s*(?:this\s*week|overall|total|drawdown|from\s*peak|peak)", t) \
        or re.search(r"(?:max(?:imum)?\s*)?drawdown\s*(?:of\s*)?" + _PCT, t)
    if m:
        add({"type": "max_drawdown_pct", "value": float(m.group(1))},
            f"max drawdown {m.group(1)}%")

    # daily loss: "down 4% today / per day / daily"
    m = re.search(r"(?:down|lose|loss)\s*(?:of\s*)?" + _PCT
                  + r"\s*(?:today|a\s*day|per\s*day|daily|in\s*a\s*day)", t)
    if m:
        add({"type": "max_daily_loss_pct", "value": float(m.group(1))},
            f"max daily loss {m.group(1)}%")

    # confidence floor: "confidence above 72% / only high-confidence 0.7"
    m = re.search(r"confidence\s*(?:above|over|at least|>=?)?\s*" + _PCT, t) \
        or re.search(_PCT + r"\s*confidence", t)
    if m:
        c = float(m.group(1))
        add({"type": "min_confidence", "value": (c / 100.0 if c > 1 else c)},
            f"min confidence {c:g}{'%' if c > 1 else ''}")

    # reward:risk floor: "at least 1.5:1 / 2R / risk reward 2"
    m = re.search(r"(?:at least\s*)?(\d+(?:\.\d+)?)\s*(?::\s*1|r\b|reward)", t) \
        or re.search(r"(?:risk[\s\-/]*reward|rr)\s*(?:of\s*)?(\d+(?:\.\d+)?)", t)
    if m:
        add({"type": "min_rr", "value": float(m.group(1))}, f"min {m.group(1)}R")

    # direction
    if re.search(r"long[\s\-]*only|no\s*shorts?|don'?t\s*short", t):
        add({"type": "direction", "value": "long_only"}, "long only")
    elif re.search(r"short[\s\-]*only|no\s*longs?", t):
        add({"type": "direction", "value": "short_only"}, "short only")

    # majors / symbol whitelist
    if re.search(r"only\s*majors?|majors?\s*only|blue[\s\-]*chip", t):
        add({"type": "allowed_symbols", "value": list(_MAJORS)},
            f"only majors ({', '.join(_MAJORS)})")
    else:
        m = re.search(r"only\s+(?:trade\s+)?([a-z0-9,\s/and]+?)"
                      r"(?:\.|,\s*(?:never|keep|max|min|and stop)|$)", t)
        if m:
            syms = [_base_symbol(s) for s in re.split(r"[,\s]+|and", m.group(1)) if _base_symbol(s)]
            syms = [s for s in syms if 2 <= len(s) <= 6]
            if syms:
                add({"type": "allowed_symbols", "value": sorted(set(syms))},
                    f"only {', '.join(sorted(set(syms)))}")

    # blocked: "no memecoins / avoid DOGE / never trade X"
    if re.search(r"no\s*meme|avoid\s*meme|no\s*shitcoin", t):
        matched.append("note: 'no memecoins' needs an explicit blocked list — add symbols")

    # strategy types: "swing only / no scalps"
    if re.search(r"swing[\s\-]*only|only\s*swing", t):
        add({"type": "allowed_strategy_types", "value": ["swing", "position"]}, "swing/position only")
    elif re.search(r"no\s*scalp", t):
        add({"type": "allowed_strategy_types", "value": ["intraday", "swing", "position"]}, "no scalps")

    return {"rules": rules, "matched": matched,
            "unparsed": (text or "").strip()[:500]}

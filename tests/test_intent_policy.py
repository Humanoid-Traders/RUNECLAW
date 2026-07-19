"""Formal Strategy Intent Compiler — deterministic policy core.

The compiler turns a plain-language intent into a hashed, typed policy that can
only TIGHTEN the engine's caps, and the evaluator checks a trade against it with
pure functions. Enforcement rides on this being exhaustively correct and
fail-safe, so these tests pin: clamp-to-tighten, hash determinism, every rule's
pass/reject boundary, the missing-data → skip (not fail) contract, the NL
parser's common phrasings, and the full NL→compile→evaluate round-trip.
"""
from bot.guardian import intent_policy as ip


# ── compile: validation + clamp-to-tighten + hashing ──────────────────

ENGINE_CAPS = {
    "max_leverage": 10,
    "max_position_pct": 10,
    "max_symbol_exposure_pct": 30,
    "max_portfolio_exposure_pct": 80,
    "max_open_positions": 5,
    "min_confidence": 0.55,
    "min_risk_reward": 1.2,
    "max_daily_loss_pct": 10,
    "max_drawdown_pct": 20,
}


def test_compile_clamps_max_rules_to_engine_cap():
    # A policy asking for a LOOSER max (higher) is clamped down to the engine cap.
    pol = ip.compile_policy(
        {"rules": [{"type": "max_symbol_exposure_pct", "value": 50}]}, ENGINE_CAPS)
    rule = pol["rules"][0]
    assert rule["value"] == 30           # clamped from 50 → engine cap 30
    assert any("loosens" in w for w in pol["warnings"])


def test_compile_keeps_tighter_max_rules():
    pol = ip.compile_policy(
        {"rules": [{"type": "max_symbol_exposure_pct", "value": 20}]}, ENGINE_CAPS)
    assert pol["rules"][0]["value"] == 20   # tighter than cap → kept
    assert pol["warnings"] == []


def test_compile_clamps_min_rules_up_to_engine_floor():
    # A min_confidence BELOW the engine floor loosens it → clamped up.
    pol = ip.compile_policy(
        {"rules": [{"type": "min_confidence", "value": 0.40}]}, ENGINE_CAPS)
    assert pol["rules"][0]["value"] == 0.55
    assert any("loosens" in w for w in pol["warnings"])
    # A tighter (higher) min_confidence is kept.
    pol2 = ip.compile_policy(
        {"rules": [{"type": "min_confidence", "value": 0.75}]}, ENGINE_CAPS)
    assert pol2["rules"][0]["value"] == 0.75


def test_compile_drops_unknown_and_nonnumeric_and_dupes():
    pol = ip.compile_policy({"rules": [
        {"type": "make_me_rich", "value": 1},
        {"type": "max_leverage", "value": "abc"},
        {"type": "max_leverage", "value": 3},
        {"type": "max_leverage", "value": 5},   # duplicate
    ]}, ENGINE_CAPS)
    types = [r["type"] for r in pol["rules"]]
    assert types == ["max_leverage"]
    assert pol["rules"][0]["value"] == 3
    assert any("unknown rule" in w for w in pol["warnings"])
    assert any("non-numeric" in w for w in pol["warnings"])
    assert any("duplicate" in w for w in pol["warnings"])


def test_compile_normalises_symbol_lists_and_direction():
    pol = ip.compile_policy({"rules": [
        {"type": "allowed_symbols", "value": ["BTC/USDT:USDT", "ethusdt", "SOL"]},
        {"type": "direction", "value": "LONG_ONLY"},
        {"type": "direction", "value": "sideways"},   # invalid enum
    ]}, ENGINE_CAPS)
    allowed = next(r for r in pol["rules"] if r["type"] == "allowed_symbols")
    assert allowed["value"] == ["BTC", "ETH", "SOL"]
    direction = [r for r in pol["rules"] if r["type"] == "direction"]
    assert len(direction) == 1 and direction[0]["value"] == "long_only"


def test_compile_defaults_mode_shadow_and_derives_id():
    pol = ip.compile_policy({"rules": [{"type": "max_leverage", "value": 3}]})
    assert pol["mode"] == "shadow"                       # safe default
    assert pol["policy_id"] == "pol_" + pol["compiled_hash"][:8]
    assert pol["version"] == ip.POLICY_VERSION


def test_hash_is_order_independent_and_content_stable():
    a = ip.compile_policy({"rules": [
        {"type": "max_leverage", "value": 3},
        {"type": "min_rr", "value": 1.5},
    ]})
    b = ip.compile_policy({"rules": [
        {"type": "min_rr", "value": 1.5},
        {"type": "max_leverage", "value": 3},
    ]})
    assert a["compiled_hash"] == b["compiled_hash"]      # order doesn't change hash
    c = ip.compile_policy({"rules": [{"type": "max_leverage", "value": 4}]})
    assert c["compiled_hash"] != a["compiled_hash"]      # value change → new hash


# ── evaluate: per-rule boundaries + verdict ───────────────────────────

def _pol(*rules):
    return ip.compile_policy({"mode": "enforce", "rules": list(rules)})


def test_evaluate_none_policy_passes():
    assert ip.evaluate_policy(None, {"asset": "BTC"})["verdict"] == "pass"
    assert ip.evaluate_policy({"rules": []}, {})["verdict"] == "pass"


def test_evaluate_numeric_max_and_min_boundaries():
    pol = _pol({"type": "max_symbol_exposure_pct", "value": 20})
    assert ip.evaluate_policy(pol, {"symbol_exposure_pct": 25})["verdict"] == "reject"
    assert ip.evaluate_policy(pol, {"symbol_exposure_pct": 20})["verdict"] == "pass"
    conf = _pol({"type": "min_confidence", "value": 0.7})
    assert ip.evaluate_policy(conf, {"confidence": 0.65})["verdict"] == "reject"
    assert ip.evaluate_policy(conf, {"confidence": 0.70})["verdict"] == "pass"
    # notional cap compares position_usd/equity = ctx.notional_pct
    notl = _pol({"type": "max_notional_pct", "value": 5})
    assert ip.evaluate_policy(notl, {"notional_pct": 6})["verdict"] == "reject"
    assert ip.evaluate_policy(notl, {"notional_pct": 5})["verdict"] == "pass"


def test_evaluate_max_open_positions_uses_effective_count():
    # "at most 3 open" → a 4th (current=3) is blocked; current=2 is allowed.
    pol = _pol({"type": "max_open_positions", "value": 3})
    assert ip.evaluate_policy(pol, {"open_positions": 3})["verdict"] == "reject"
    assert ip.evaluate_policy(pol, {"open_positions": 2})["verdict"] == "pass"
    # absent count → skip, not fail (engine's own MAX_POSITIONS stays the floor)
    assert ip.evaluate_policy(pol, {})["skipped"] == 1


def test_evaluate_symbol_direction_strategy_rules():
    allow = _pol({"type": "allowed_symbols", "value": ["BTC", "ETH"]})
    assert ip.evaluate_policy(allow, {"asset": "DOGE/USDT"})["verdict"] == "reject"
    assert ip.evaluate_policy(allow, {"asset": "BTC/USDT:USDT"})["verdict"] == "pass"
    block = _pol({"type": "blocked_symbols", "value": ["DOGE"]})
    assert ip.evaluate_policy(block, {"asset": "DOGE"})["verdict"] == "reject"
    lo = _pol({"type": "direction", "value": "long_only"})
    assert ip.evaluate_policy(lo, {"direction": "SHORT"})["verdict"] == "reject"
    assert ip.evaluate_policy(lo, {"direction": "LONG"})["verdict"] == "pass"
    strat = _pol({"type": "allowed_strategy_types", "value": ["swing", "position"]})
    assert ip.evaluate_policy(strat, {"strategy_type": "scalp"})["verdict"] == "reject"
    assert ip.evaluate_policy(strat, {"strategy_type": "swing"})["verdict"] == "pass"


def test_evaluate_missing_data_skips_never_fails():
    # A rule whose ctx fact is absent must SKIP (engine cap stays the floor),
    # never fabricate a violation.
    pol = _pol(
        {"type": "max_symbol_exposure_pct", "value": 20},
        {"type": "min_confidence", "value": 0.7},
        {"type": "allowed_symbols", "value": ["BTC"]},
    )
    r = ip.evaluate_policy(pol, {})     # empty ctx
    assert r["verdict"] == "pass"
    assert r["checked"] == 0 and r["skipped"] == 3


def test_evaluate_collects_multiple_violations():
    pol = _pol(
        {"type": "max_leverage", "value": 3},
        {"type": "direction", "value": "long_only"},
    )
    r = ip.evaluate_policy(pol, {"leverage": 5, "direction": "SHORT"})
    assert r["verdict"] == "reject"
    assert len(r["violations"]) == 2


def test_evaluate_never_raises_on_garbage_rule():
    # A malformed rule value must not crash enforcement.
    bad = {"policy_id": "x", "compiled_hash": "y", "mode": "enforce",
           "rules": [{"type": "max_leverage", "value": None},
                     {"type": "allowed_symbols", "value": "not-a-list"}]}
    r = ip.evaluate_policy(bad, {"leverage": 5, "asset": "BTC"})
    assert isinstance(r["violations"], list)   # no exception


# ── NL parser ─────────────────────────────────────────────────────────

def test_nl_extracts_common_intent():
    out = ip.compile_nl(
        "Keep me under 3x leverage, never more than 20% in one coin, "
        "only majors, and stop if I'm down 8% this week. Min confidence 70%.")
    by = {r["type"]: r["value"] for r in out["rules"]}
    assert by["max_leverage"] == 3
    assert by["max_symbol_exposure_pct"] == 20
    assert by["max_drawdown_pct"] == 8
    assert by["min_confidence"] == 0.70
    assert by["allowed_symbols"] == ip._MAJORS


def test_nl_direction_and_rr_and_free_margin():
    out = ip.compile_nl("long only, at least 2:1 reward, keep 30% free margin")
    by = {r["type"]: r["value"] for r in out["rules"]}
    assert by["direction"] == "long_only"
    assert by["min_rr"] == 2
    assert by["min_free_margin_pct"] == 30


def test_nl_notional_and_open_positions():
    out = ip.compile_nl("max 2% per trade, cap 3 open positions")
    by = {r["type"]: r["value"] for r in out["rules"]}
    assert by["max_notional_pct"] == 2
    assert by["max_open_positions"] == 3


def test_nl_confidence_decimal_form():
    out = ip.compile_nl("only take setups with confidence above 0.65")
    by = {r["type"]: r["value"] for r in out["rules"]}
    assert by["min_confidence"] == 0.65


# ── end-to-end: NL → compile → evaluate ───────────────────────────────

def test_round_trip_nl_to_enforcement():
    nl = ip.compile_nl("under 3x leverage, only majors, min confidence 70%")
    pol = ip.compile_policy(
        {"mode": "enforce", "source_text": "under 3x...", "rules": nl["rules"]},
        ENGINE_CAPS)
    # A compliant trade passes.
    ok = ip.evaluate_policy(pol, {"leverage": 2, "asset": "BTC/USDT", "confidence": 0.8})
    assert ok["verdict"] == "pass"
    # A DOGE short at 5x, low confidence violates three rules.
    bad = ip.evaluate_policy(pol, {"leverage": 5, "asset": "DOGE/USDT", "confidence": 0.6})
    assert bad["verdict"] == "reject"
    assert len(bad["violations"]) == 3

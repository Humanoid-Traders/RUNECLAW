"""Memecoin Solana/Jupiter planner — preflight predictions X1-X8.

The planner NEVER signs (would_execute always False). A buy needs feature flag +
envelope + safety gate, all fail-closed; a sell (exit) skips the safety gate but
still needs the flag + envelope. Jupiter request legs are USDC<->token.
"""
from bot.core import meme_executor as mx


def _buy_intent(**over):
    d = {"side": "buy", "token_mint": "TokenMint1111", "symbol": "WIF",
         "size_usd": 100, "slippage_bps": 100}
    d.update(over)
    return d


def _market(**over):
    d = {"liquidity_usd": 120_000, "age_hours": 240, "sells_24h": 210, "buys_24h": 300}
    d.update(over)
    return d


def _safe():
    return {"verdict": "safe"}


def test_x1_feature_off_blocks_even_when_everything_else_ok():
    p = mx.plan_swap(intent=_buy_intent(), safety_report=_safe(),
                     radar_risk={"tier": "high"}, market=_market(),
                     envelope_authorized=True, feature_on=False)
    assert p["allowed"] is False
    assert p["would_execute"] is False
    assert "feature_enabled" in p["reason"]


def test_x2_full_pass_produces_plan_but_never_executes():
    p = mx.plan_swap(intent=_buy_intent(), safety_report=_safe(),
                     radar_risk={"tier": "high"}, market=_market(),
                     envelope_authorized=True, feature_on=True)
    assert p["allowed"] is True
    assert p["would_execute"] is False           # planner never signs
    assert p["jupiter_request"]["inputMint"] == mx.USDC_MINT   # buy: USDC -> token
    assert p["jupiter_request"]["outputMint"] == "TokenMint1111"
    assert p["venue"] == "solana:jupiter"


def test_x3_no_envelope_authority_fails_closed():
    p = mx.plan_swap(intent=_buy_intent(), safety_report=_safe(),
                     radar_risk={"tier": "high"}, market=_market(),
                     envelope_authorized=None, feature_on=True)
    assert p["allowed"] is False
    assert any(c["name"] == "envelope_authorized" and not c["ok"] for c in p["preconditions"])


def test_x4_unsafe_token_blocks_buy_via_gate():
    p = mx.plan_swap(intent=_buy_intent(), safety_report={"verdict": "danger"},
                     radar_risk={"tier": "extreme"}, market=_market(liquidity_usd=3000),
                     envelope_authorized=True, feature_on=True)
    assert p["allowed"] is False
    assert p["gate"] is not None and p["gate"]["allowed"] is False
    assert any(c["name"] == "safety_gate" and not c["ok"] for c in p["preconditions"])


def test_x5_sell_is_an_exit_and_skips_the_safety_gate():
    # Even a rug (danger, extreme, thin) must be SELLABLE — exiting is safety.
    p = mx.plan_swap(intent={"side": "sell", "token_mint": "TokenMint1111",
                             "symbol": "RUG", "size_usd": 50},
                     safety_report={"verdict": "danger"}, radar_risk={"tier": "extreme"},
                     market=_market(liquidity_usd=1000, sells_24h=0),
                     envelope_authorized=True, feature_on=True)
    assert p["allowed"] is True
    assert p["gate"] is None                       # gate not run for sells
    assert p["jupiter_request"]["inputMint"] == "TokenMint1111"  # sell: token -> USDC
    assert p["jupiter_request"]["outputMint"] == mx.USDC_MINT


def test_x6_malformed_intent_blocks():
    for bad in ({"side": "buy", "token_mint": "", "size_usd": 100},
                {"side": "buy", "token_mint": "M", "size_usd": 0},
                {"side": "chaos", "token_mint": "M", "size_usd": 10}):
        p = mx.plan_swap(intent=bad, safety_report=_safe(),
                         radar_risk={"tier": "high"}, market=_market(),
                         envelope_authorized=True, feature_on=True)
        assert p["allowed"] is False
        assert any(c["name"] == "intent_valid" and not c["ok"] for c in p["preconditions"])


def test_x7_feature_flag_reads_env(monkeypatch):
    monkeypatch.delenv("MEME_TRADING_ENABLED", raising=False)
    assert mx.feature_enabled() is False
    monkeypatch.setenv("MEME_TRADING_ENABLED", "true")
    assert mx.feature_enabled() is True
    monkeypatch.setenv("MEME_TRADING_ENABLED", "nope")
    assert mx.feature_enabled() is False


def test_x8_human_readable_states_no_signing():
    txt = mx.human_readable(mx.plan_swap(
        intent=_buy_intent(), safety_report=_safe(), radar_risk={"tier": "high"},
        market=_market(), envelope_authorized=True, feature_on=True))
    assert "would_execute: False" in txt and "no signing" in txt

"""
Funding confluence-vote scale fix (deep-audit medium).

OrderFlowAnalyzer.to_confluence_votes normalised the funding rate by 0.03 (3%),
but funding rates are tiny (typically ±0.0001–0.0005), so the contrarian funding
vote was ~60x too small to ever move confluence — effectively dead. The correct
scale is 0.0005 (= OrderFlowConfig.funding_extreme, and what the smart-money
scorer already uses): |funding| ≥ 0.05% saturates the vote to ±1.

The fix is gated by OF_FUNDING_VOTE_FIXED_SCALE (default OFF → legacy 0.03 scale,
byte-identical confluence).
"""

from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig, OrderFlowSignal

_resolve = OrderFlowAnalyzer._resolve_funding_scale
_DEAD = OrderFlowAnalyzer._FUNDING_SCALE_DEAD      # 0.03
_FIXED = OrderFlowAnalyzer._FUNDING_SCALE_FIXED    # 0.0005


def _funding_vote(sig, **kw):
    votes, weights, labels = OrderFlowAnalyzer.to_confluence_votes(sig, **kw)
    i = labels.index("of_funding")
    return votes[i]


def _sig(funding_rate=0.0005):
    # confidence > 0 so to_confluence_votes doesn't early-return neutral.
    return OrderFlowSignal(symbol="BTC/USDT", funding_rate=funding_rate, confidence=0.8)


class TestResolveScale:
    def test_explicit_value_wins(self):
        assert _resolve(0.01) == 0.01

    def test_none_off_is_dead_scale(self, monkeypatch):
        monkeypatch.delenv("OF_FUNDING_VOTE_FIXED_SCALE", raising=False)
        assert _resolve(None) == _DEAD

    def test_none_on_is_fixed_scale(self, monkeypatch):
        monkeypatch.setenv("OF_FUNDING_VOTE_FIXED_SCALE", "1")
        assert _resolve(None) == _FIXED


class TestFundingVoteMagnitude:
    def test_dead_scale_vote_is_negligible(self):
        # 0.0005 / 0.03 ≈ 0.0167 → contrarian (negative for positive funding).
        v = _funding_vote(_sig(0.0005), funding_extreme=_DEAD)
        assert abs(v) < 0.02

    def test_fixed_scale_vote_saturates(self):
        # 0.0005 / 0.0005 = 1.0 → contrarian vote -1.0 (crowded longs → bearish).
        v = _funding_vote(_sig(0.0005), funding_extreme=_FIXED)
        assert v == -1.0

    def test_fixed_scale_negative_funding_is_bullish(self):
        # Crowded shorts (negative funding) → contrarian bullish (+1.0).
        v = _funding_vote(_sig(-0.0005), funding_extreme=_FIXED)
        assert v == 1.0

    def test_fixed_scale_is_60x_stronger(self):
        dead = abs(_funding_vote(_sig(0.0003), funding_extreme=_DEAD))
        fixed = abs(_funding_vote(_sig(0.0003), funding_extreme=_FIXED))
        assert fixed == round(dead * 60, 10) or fixed > dead * 50


class TestGating:
    def test_default_off_uses_dead_scale(self, monkeypatch):
        # No explicit override, flag off → byte-identical dead behaviour.
        monkeypatch.delenv("OF_FUNDING_VOTE_FIXED_SCALE", raising=False)
        v = _funding_vote(_sig(0.0005))
        assert abs(v) < 0.02

    def test_flag_on_uses_fixed_scale(self, monkeypatch):
        monkeypatch.setenv("OF_FUNDING_VOTE_FIXED_SCALE", "1")
        v = _funding_vote(_sig(0.0005))
        assert v == -1.0

    def test_config_flag_defaults_off(self):
        assert OrderFlowConfig().funding_vote_fixed_scale is False

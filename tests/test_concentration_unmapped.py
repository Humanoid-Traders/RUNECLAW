"""
Concentration hardening (roadmap risk-depth #4 / the 4-day live-report's
many-correlated-alts exposure).

Symbols not in _CORRELATION_GROUPS used to each become their OWN singleton
group, so a basket of unmapped alts collectively dodged the per-group
correlation cap entirely. They are now pooled into ONE shared bucket
(_UNMAPPED_GROUP) with its own (more generous) cap, max_unmapped_correlated,
while mapped groups keep the tighter max_correlation_per_group.
"""

from types import SimpleNamespace
from unittest.mock import patch

from bot.risk.risk_engine import _UNMAPPED_GROUP, RiskEngine


def _pos(asset, direction="LONG"):
    return SimpleNamespace(asset=asset, direction=SimpleNamespace(value=direction))


def _idea(asset, direction="LONG"):
    return SimpleNamespace(asset=asset, direction=SimpleNamespace(value=direction))


def _engine(positions):
    """A RiskEngine with just enough state wired to exercise the correlation
    check in isolation (no portfolio/persistence machinery)."""
    eng = RiskEngine.__new__(RiskEngine)
    eng._portfolio = SimpleNamespace(
        open_positions=list(positions),
        _positions={f"t{i}": p for i, p in enumerate(positions)},
    )
    eng._price_history = {}  # keep the V2 rolling-correlation path dormant
    return eng


class TestCorrelationGroupMapping:
    def test_mapped_symbols(self):
        eng = _engine([])
        assert eng._correlation_group("BTC/USDT") == "BTC"
        assert eng._correlation_group("SOL/USDT") == "ALT_L1"
        assert eng._correlation_group("DOGE/USDT") == "MEME"

    def test_bare_base_is_normalized(self):
        eng = _engine([])
        assert eng._correlation_group("BTC") == "BTC"  # -> BTC/USDT

    def test_unmapped_symbols_share_one_bucket(self):
        eng = _engine([])
        assert eng._correlation_group("RANDOMX/USDT") == _UNMAPPED_GROUP
        assert eng._correlation_group("WEIRDCOIN/USDT") == _UNMAPPED_GROUP
        assert eng._correlation_group("ZZZ") == _UNMAPPED_GROUP

    def test_ccxt_perp_default_pools_into_unmapped_bucket(self):
        # Round 7: perp ids ("SOL/USDT:USDT") don't match the spot-keyed map.
        # The corrected per-group mapping is gated OFF by default (it loosens
        # aggregate correlated exposure and raised drawdown on the dense A/B),
        # so by default every perp still pools into the one unmapped bucket —
        # which bounds TOTAL correlated exposure. Lock in that default.
        eng = _engine([])
        with patch("bot.risk.risk_engine.CONFIG") as cfg:
            cfg.risk.correlation_perp_group_mapping_enabled = False
            for s in ("SOL/USDT:USDT", "AAVE/USDT:USDT", "DOGE/USDT:USDT",
                      "BTC/USDT:USDT", "ETH/USDT:USDT"):
                assert eng._correlation_group(s) == _UNMAPPED_GROUP

    def test_ccxt_perp_maps_to_group_when_enabled(self):
        # With the opt-in flag on, the ':SETTLE' suffix is stripped and each perp
        # resolves to its real group; a genuinely unmapped perp still pools.
        eng = _engine([])
        with patch("bot.risk.risk_engine.CONFIG") as cfg:
            cfg.risk.correlation_perp_group_mapping_enabled = True
            assert eng._correlation_group("SOL/USDT:USDT") == "ALT_L1"
            assert eng._correlation_group("AAVE/USDT:USDT") == "DEFI"
            assert eng._correlation_group("DOGE/USDT:USDT") == "MEME"
            assert eng._correlation_group("BTC/USDT:USDT") == "BTC"
            assert eng._correlation_group("ETH/USDT:USDT") == "ETH"
            assert eng._correlation_group("WEIRDCOIN/USDT:USDT") == _UNMAPPED_GROUP


class TestUnmappedBucketCap:
    def test_basket_of_unmapped_alts_is_capped(self):
        # Three DIFFERENT unmapped alts already open; with the default cap of 3
        # a fourth (also unmapped) must be rejected — it used to slip through
        # because each was its own group.
        eng = _engine([_pos("AAA/USDT"), _pos("BBB/USDT"), _pos("CCC/USDT")])
        result = eng._check_correlation(_idea("DDD/USDT"))
        assert result is not None
        assert _UNMAPPED_GROUP in result

    def test_under_cap_is_allowed(self):
        eng = _engine([_pos("AAA/USDT"), _pos("BBB/USDT")])  # 2 < cap 3
        assert eng._check_correlation(_idea("CCC/USDT")) is None

    def test_no_open_positions_allows(self):
        assert _engine([])._check_correlation(_idea("AAA/USDT")) is None

    def test_high_cap_effectively_disables(self):
        eng = _engine([_pos("AAA/USDT"), _pos("BBB/USDT"), _pos("CCC/USDT")])
        with patch("bot.risk.risk_engine.CONFIG") as cfg:
            cfg.risk.max_unmapped_correlated = 100
            cfg.risk.max_correlation_per_group = 2
            assert eng._check_correlation(_idea("DDD/USDT")) is None


class TestMappedGroupsUnchanged:
    def test_mapped_group_keeps_its_tighter_cap(self):
        # Two ALT_L1 already open; a third ALT_L1 rejected at the default cap 2.
        eng = _engine([_pos("SOL/USDT"), _pos("AVAX/USDT")])
        result = eng._check_correlation(_idea("NEAR/USDT"))
        assert result is not None
        assert "ALT_L1" in result

    def test_mapped_group_under_cap_allowed(self):
        eng = _engine([_pos("SOL/USDT")])  # 1 < cap 2
        assert eng._check_correlation(_idea("AVAX/USDT")) is None


class TestBucketsAreIndependent:
    def test_unmapped_positions_do_not_block_a_mapped_idea(self):
        # A pile of unmapped alts must not count against a BTC trade.
        eng = _engine([_pos("AAA/USDT"), _pos("BBB/USDT"), _pos("CCC/USDT")])
        assert eng._check_correlation(_idea("BTC/USDT")) is None

    def test_mapped_positions_do_not_block_an_unmapped_idea(self):
        eng = _engine([_pos("SOL/USDT"), _pos("AVAX/USDT")])  # ALT_L1 x2
        assert eng._check_correlation(_idea("AAA/USDT")) is None  # unmapped, count 0

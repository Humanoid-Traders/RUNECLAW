"""Cross-asset regime voter — dark by default (§4 new-voter rule).

The CrossAssetTracker has always been fed and has always adjusted
confidence/size POST-score; this gives its context a VOTER face in the
confluence electorate — regime (risk_on/off/rotation), DXY-proxy dollar
wind, ETH/BTC alt-season — gated behind CROSS_ASSET_VOTER_ENABLED (default
OFF). A dataless context votes nothing, per the dilution-guard doctrine.
"""
from __future__ import annotations

import inspect

from bot.config import CONFIG
from bot.core.cross_asset import CrossAssetContext


def _votes_by_name(ctx, symbol):
    return {name: (vote, weight) for name, vote, weight in ctx.to_confluence_votes(symbol)}


class TestFlag:
    def test_ships_dark(self):
        assert CONFIG.analyzer.cross_asset_voter_enabled is False


class TestContextVotes:
    def test_dataless_context_votes_nothing(self):
        # Fresh defaults: regime "normal", every trend "neutral".
        assert CrossAssetContext().to_confluence_votes("SOLUSDT") == []
        assert CrossAssetContext().to_confluence_votes("BTCUSDT") == []

    def test_risk_off_is_bearish_alts_more_than_btc(self):
        ctx = CrossAssetContext(market_regime="risk_off")
        alt = _votes_by_name(ctx, "SOLUSDT")["cross_regime"]
        btc = _votes_by_name(ctx, "BTCUSDT")["cross_regime"]
        assert alt[0] < btc[0] < 0

    def test_rotation_splits_by_symbol_type(self):
        ctx = CrossAssetContext(market_regime="rotation")
        assert _votes_by_name(ctx, "SOLUSDT")["cross_regime"][0] < 0   # alts bleed
        assert _votes_by_name(ctx, "BTCUSDT")["cross_regime"][0] > 0   # BTC receives

    def test_risk_on_is_bullish_both(self):
        ctx = CrossAssetContext(market_regime="risk_on")
        assert _votes_by_name(ctx, "SOLUSDT")["cross_regime"][0] > 0
        assert _votes_by_name(ctx, "BTCUSDT")["cross_regime"][0] > 0

    def test_dollar_wind_blows_on_every_symbol(self):
        strong = CrossAssetContext(dxy_proxy_trend="strengthening")
        weak = CrossAssetContext(dxy_proxy_trend="weakening")
        for sym in ("BTCUSDT", "SOLUSDT"):
            assert _votes_by_name(strong, sym)["dollar_wind"][0] < 0
            assert _votes_by_name(weak, sym)["dollar_wind"][0] > 0

    def test_alt_season_votes_alts_only(self):
        ctx = CrossAssetContext(eth_btc_trend="rising")
        assert _votes_by_name(ctx, "SOLUSDT")["alt_season"][0] > 0
        assert "alt_season" not in _votes_by_name(ctx, "BTCUSDT")
        down = CrossAssetContext(eth_btc_trend="falling")
        assert _votes_by_name(down, "SOLUSDT")["alt_season"][0] < 0

    def test_weights_stay_in_the_minor_voter_band(self):
        ctx = CrossAssetContext(market_regime="risk_off",
                                dxy_proxy_trend="strengthening",
                                eth_btc_trend="falling")
        for _name, vote, weight in ctx.to_confluence_votes("SOLUSDT"):
            assert 0 < weight <= 0.6
            assert -1.0 <= vote <= 1.0


class TestWiring:
    def test_score_confluence_takes_the_context_and_votes_it(self):
        from bot.core.analyzer import Analyzer
        src = inspect.getsource(Analyzer._score_confluence)
        assert "cross_asset_context=None" in src
        assert "cross_asset_context.to_confluence_votes" in src

    def test_the_caller_gates_on_the_flag_like_onchain(self):
        # Mirror of the onchain pattern: analyze() resolves the context only
        # when the flag is on, then passes it into the static scorer.
        import bot.core.analyzer as analyzer_mod
        src = inspect.getsource(analyzer_mod)
        assert "cross_asset_voter_enabled" in src
        assert "cross_asset_context=_ca_context" in src

    def test_engine_hands_the_tracker_to_the_analyzer(self):
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine.__init__)
        assert "self.analyzer.cross_asset_tracker = self.cross_asset" in src

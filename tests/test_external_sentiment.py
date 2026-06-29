"""
Real external sentiment: the alternative.me Fear & Greed index blended into the
sentiment voter as a bounded contrarian signal.

No network here — we set the cached external value directly (as a successful
fetch would) and assert the math + that refresh() no-ops when the cache is fresh.
"""

import pytest

from bot.core.sentiment import SentimentEngine, _EXT_FG_MAX_ADJUSTMENT


def test_ext_adjustment_is_contrarian_and_bounded():
    e = SentimentEngine()
    e._fear_greed_value = None
    assert e._ext_fg_adjustment() == 0.0          # no data -> neutral
    e._fear_greed_value = 0.0                       # extreme fear
    assert abs(e._ext_fg_adjustment() - _EXT_FG_MAX_ADJUSTMENT) < 1e-9   # contrarian bullish
    e._fear_greed_value = 100.0                     # extreme greed
    assert abs(e._ext_fg_adjustment() + _EXT_FG_MAX_ADJUSTMENT) < 1e-9   # contrarian bearish
    e._fear_greed_value = 50.0                      # neutral
    assert abs(e._ext_fg_adjustment()) < 1e-9
    # Bounded across the whole range.
    for fg in range(0, 101, 5):
        e._fear_greed_value = float(fg)
        assert -_EXT_FG_MAX_ADJUSTMENT - 1e-9 <= e._ext_fg_adjustment() <= _EXT_FG_MAX_ADJUSTMENT + 1e-9


def test_external_value_shifts_the_vote():
    e = SentimentEngine()
    # One update so `latest` exists (otherwise get_confluence_vote returns 0 early).
    e.update(symbol="BTCUSDT", price=100.0, volume=1000.0, price_change_pct=0.0)
    e._fear_greed_value = None
    base = e.get_confluence_vote("BTCUSDT")
    e._fear_greed_value = 0.0          # extreme fear -> contrarian bullish nudge
    fear = e.get_confluence_vote("BTCUSDT")
    e._fear_greed_value = 100.0        # extreme greed -> contrarian bearish nudge
    greed = e.get_confluence_vote("BTCUSDT")
    assert fear > base >= greed or fear > greed   # fear more bullish than greed
    assert -1.0 <= fear <= 1.0 and -1.0 <= greed <= 1.0


def test_to_confluence_votes_shape():
    e = SentimentEngine()
    e.update(symbol="BTCUSDT", price=100.0, volume=1000.0, price_change_pct=1.0)
    votes = e.to_confluence_votes()
    assert isinstance(votes, list) and len(votes) == 1
    name, vote, weight = votes[0]
    assert name == "sentiment_composite"
    assert -1.0 <= vote <= 1.0 and weight > 0.0


@pytest.mark.asyncio
async def test_refresh_noop_when_cache_fresh(monkeypatch):
    import time
    e = SentimentEngine()
    e._fear_greed_value = 42.0
    e._fear_greed_ts = time.monotonic()    # just fetched -> fresh

    async def _boom():
        raise AssertionError("must not fetch when cache is fresh")

    monkeypatch.setattr(e, "_fetch_fear_greed", _boom)
    assert await e.refresh_fear_greed() == 42.0

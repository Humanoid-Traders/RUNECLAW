"""NEWS-1 completion — proactive PUSH of the held-position stand-down nudge.

The news radar's stand-down (a FRESH high-impact headline on an asset the book
holds) was pull-only: shown in /news and the web view, but never pushed. The
proactive monitor now emits it as an advisory alert — once per headline — while
keeping news strictly advisory (it never blocks/sizes/moves an order).

These exercise the check directly against a lightweight fake engine: no network,
no full engine. The radar is seeded in-memory.
"""

import time
import types

import pytest

from bot.core.proactive_monitor import ProactiveMonitor
from bot.core.news import NewsRadar, NewsItem, Impact


def _engine_holding(asset="BTC/USDT", radar=None):
    eng = types.SimpleNamespace()
    eng.user_portfolios = None                          # → falls to shared portfolio
    eng.portfolio = types.SimpleNamespace(
        open_positions=[types.SimpleNamespace(asset=asset)])
    if radar is not None:
        eng._news_radar = radar
    return eng


def _seeded_radar(symbol="BTC", fresh=True, impact=Impact.HIGH):
    radar = NewsRadar()
    published = time.time() - (120 if fresh else 7200)   # 2m vs 2h old
    radar.ingest([NewsItem(
        title="Regulator sues over token", url="https://news.example/1",
        source="coindesk", published_ts=published, impact=impact,
        impact_reasons=("lawsuit",), symbols=(symbol,))])
    return radar


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("NEWS_STANDDOWN_ALERTS", raising=False)
    monkeypatch.setenv("NEWS_RADAR_ENABLED", "1")


def test_fresh_high_impact_headline_on_holding_pushes_once():
    m = ProactiveMonitor(_engine_holding(radar=_seeded_radar()))
    a = m._check_news_standdown()
    assert len(a) == 1
    assert a[0].alert_type == "NEWS_STANDDOWN"
    assert a[0].severity == "WARNING"
    assert "BTC" in a[0].title
    # advisory framing — never claims it acted.
    assert "Advisory only" in a[0].body
    # the SAME headline does not re-alert (once-only via _news_alerted).
    assert m._check_news_standdown() == []


def test_stale_or_low_impact_headline_does_not_push():
    # 2h-old HIGH → past the 1h freshness window.
    m = ProactiveMonitor(_engine_holding(radar=_seeded_radar(fresh=False)))
    assert m._check_news_standdown() == []
    # fresh but only MEDIUM impact → not a stand-down.
    m2 = ProactiveMonitor(_engine_holding(radar=_seeded_radar(impact=Impact.MEDIUM)))
    assert m2._check_news_standdown() == []


def test_headline_on_an_unheld_asset_does_not_push():
    # hold ETH, news names BTC.
    m = ProactiveMonitor(_engine_holding(asset="ETH/USDT", radar=_seeded_radar("BTC")))
    assert m._check_news_standdown() == []


def test_disable_flag_suppresses_the_push():
    import os
    os.environ["NEWS_STANDDOWN_ALERTS"] = "0"
    try:
        m = ProactiveMonitor(_engine_holding(radar=_seeded_radar()))
        assert m._check_news_standdown() == []
    finally:
        del os.environ["NEWS_STANDDOWN_ALERTS"]


def test_no_radar_or_flat_book_is_a_safe_noop():
    # radar present, but no open positions → nothing to alert on.
    flat = types.SimpleNamespace(
        user_portfolios=None,
        portfolio=types.SimpleNamespace(open_positions=[]),
        _news_radar=_seeded_radar())
    assert ProactiveMonitor(flat)._check_news_standdown() == []
    # holding, but the radar was never created → no crash, no alert.
    no_radar = _engine_holding()
    assert ProactiveMonitor(no_radar)._check_news_standdown() == []


def test_held_base_assets_reads_the_shared_portfolio():
    m = ProactiveMonitor(_engine_holding(asset="SOL/USDT"))
    assert m._held_base_assets() == ["SOL/USDT"]

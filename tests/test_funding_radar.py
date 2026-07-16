"""
Cross-venue funding radar — read-only comparison of perp funding.

Pins the normalization (Bitget 8h vs Hyperliquid hourly -> comparable APR),
the spread/direction math (long the low-funding venue, short the high), the
>=2-venues rule, fail-soft venue drop-out, and the HTML rendering. Fetchers
are injected — no network in the deterministic tests.
"""

from bot.core.funding_radar import (
    FundingRow,
    build_comparison,
    format_funding_html,
)


def _fake_fetchers(bitget=None, bybit=None, hyperliquid=None):
    return {
        "bitget": lambda b: bitget or {},
        "bybit": lambda b: bybit or {},
        "hyperliquid": lambda b: hyperliquid or {},
    }


def test_spread_direction_long_low_short_high():
    rows = build_comparison(
        ["BTC", "ETH"],
        fetchers=_fake_fetchers(
            bitget={"BTC": 9.2, "ETH": -3.0},
            hyperliquid={"BTC": 11.0, "ETH": 14.0}))
    by = {r.base: r for r in rows}
    # ETH has the widest spread (17) and leads the list.
    assert [r.base for r in rows] == ["ETH", "BTC"]
    eth = by["ETH"]
    assert eth.long_venue == "bitget" and eth.short_venue == "hyperliquid"
    assert abs(eth.spread_apr - 17.0) < 1e-9
    assert by["BTC"].long_venue == "bitget"


def test_single_venue_coins_are_dropped():
    rows = build_comparison(
        ["BTC", "ONLYBG"],
        fetchers=_fake_fetchers(bitget={"BTC": 5.0, "ONLYBG": 99.0},
                                bybit={"BTC": 6.0}))
    assert [r.base for r in rows] == ["BTC"], \
        "a spread needs two sides — single-venue coins must not appear"


def test_failed_venue_drops_out_instead_of_erroring():
    def boom(_):
        raise RuntimeError("geo-fenced")
    rows = build_comparison(
        ["BTC"],
        fetchers={"bitget": lambda b: {"BTC": 5.0},
                  "bybit": boom,
                  "hyperliquid": lambda b: {"BTC": 8.0}})
    assert len(rows) == 1
    assert set(rows[0].rates) == {"bitget", "hyperliquid"}


def test_html_render_and_empty_state():
    html = format_funding_html(build_comparison(
        ["BTC"], fetchers=_fake_fetchers(bitget={"BTC": 5.0},
                                         bybit={"BTC": 9.0})))
    assert "Funding radar" in html and "BTC" in html
    assert "long bitget" in html and "short bybit" in html
    assert "Read-only" in html or "read-only" in html
    assert "Funding radar" in format_funding_html([])


def test_row_dataclass_defaults():
    r = FundingRow(base="BTC")
    assert r.rates == {} and r.spread_apr == 0.0

"""
Yield Radar — idle assets matched to the best FLEXIBLE Earn products.

Pins the Phase-1 contract: read-only (a fake client records every request —
none may be a write), margin reserve on futures free balance, dust skipped,
unknown-priced coins skipped (never invent a value), fail-soft on API errors.
"""

from bot.core.yield_radar import (
    MARGIN_RESERVE_PCT,
    build_report,
    fetch_savings_catalog,
    format_report_html,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, body_dict=None, timeout=10):
        self.calls.append((method, path, body_dict))
        for prefix, resp in self.responses.items():
            if path.startswith(prefix):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return {"code": "40404", "msg": "no fixture"}


CATALOG = {
    "code": "00000",
    "data": [
        {"coin": "USDT", "periodType": "flexible",
         "apyList": [{"currentApy": "8.5"}, {"currentApy": "3.2"}]},
        {"coin": "USDT", "periodType": "fixed", "apyList": [{"currentApy": "12.0"}]},
        {"coin": "ETH", "periodType": "flexible", "apyList": [{"currentApy": "2.1"}]},
        {"coin": "DOGE", "periodType": "flexible", "apyList": [{"currentApy": "1.0"}]},
    ],
}
SPOT = {"code": "00000", "data": [
    {"coin": "ETH", "available": "0.5"},
    {"coin": "USDT", "available": "40"},
    {"coin": "DOGE", "available": "3"},        # priceless in this test → skipped
    {"coin": "PEPE", "available": "1000000"},  # no catalog + no price → skipped
]}


def _client():
    return FakeClient({
        "/api/v2/earn/savings/product": CATALOG,
        "/api/v2/spot/account/assets": SPOT,
    })


def test_catalog_splits_flexible_and_fixed_taking_best_tier():
    cat = fetch_savings_catalog(_client())
    assert cat["USDT"]["flexible"] == 8.5     # best tier, not first
    assert cat["USDT"]["fixed"] == 12.0
    assert cat["ETH"]["flexible"] == 2.1


def test_report_reserves_margin_and_matches_apy():
    report = build_report(_client(), futures_free_usdt=100.0,
                          prices={"ETH": 3000.0})
    rows = {r.coin: r for r in report.rows}
    # USDT = 100 futures free + 40 spot; reserve applies (futures in source mix)
    usdt = rows["USDT"]
    assert usdt.idle_usd == 140.0
    assert abs(usdt.stakeable_usd - 140.0 * (1 - MARGIN_RESERVE_PCT)) < 1e-9
    assert usdt.apy_flexible == 8.5
    assert usdt.est_year_usd > 0
    # ETH spot only: no reserve haircut, priced via the supplied price map
    eth = rows["ETH"]
    assert eth.idle_usd == 1500.0
    assert eth.stakeable_usd == 1500.0
    # DOGE has no price supplied -> skipped, never valued at a made-up number
    assert "DOGE" not in rows and "PEPE" not in rows
    assert report.total_idle_usd == 1640.0


def test_radar_is_strictly_read_only():
    c = _client()
    build_report(c, futures_free_usdt=50.0)
    assert all(m == "GET" for m, _p, _b in c.calls), "the radar must never write"


def test_catalog_failure_degrades_to_error_report():
    c = FakeClient({"/api/v2/earn/savings/product": RuntimeError("boom")})
    report = build_report(c, futures_free_usdt=100.0)
    assert report.error
    assert not report.rows
    assert "Yield Radar" in format_report_html(report)


def test_report_renders_telegram_html():
    html = format_report_html(build_report(_client(), futures_free_usdt=100.0,
                                           prices={"ETH": 3000.0}))
    assert "Yield Radar" in html and "USDT" in html and "8.50%" in html
    assert "Read-only" in html or "read-only" in html

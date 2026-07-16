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


# ─── Phase 2: the stake/redeem money path ────────────────────────────────────

OK = {"code": "00000", "data": {"orderId": "1"}}
CATALOG_WITH_IDS = {
    "code": "00000",
    "data": [
        {"coin": "USDT", "periodType": "flexible", "productId": "7001",
         "apyList": [{"currentApy": "8.5"}]},
        {"coin": "ETH", "periodType": "flexible", "productId": "7002",
         "apyList": [{"currentApy": "2.1"}]},
    ],
}
ASSETS = {"code": "00000", "data": {"resultList": [
    {"productId": "7001", "productCoin": "USDT", "holdAmount": "98",
     "apy": "8.5"},
]}}


def _money_client(subscribe=OK, transfer=OK, redeem=OK, assets=ASSETS):
    return FakeClient({
        "/api/v2/earn/savings/product": CATALOG_WITH_IDS,
        "/api/v2/earn/savings/assets": assets,
        "/api/v2/earn/savings/subscribe": subscribe,
        "/api/v2/earn/savings/redeem": redeem,
        "/api/v2/spot/account/assets": SPOT,
        "/api/v2/spot/wallet/transfer": transfer,
    })


def test_stake_recomputes_clamps_and_tops_up_spot_only_by_shortfall():
    from bot.core.yield_radar import execute_stake
    c = _money_client()
    res = execute_stake(c, "USDT", futures_free_usdt=100.0)
    assert res.ok, res.message
    # 100 futures + 40 spot = 140 idle -> 98 stakeable after 30% reserve.
    # Spot already holds 40, so only the 58 shortfall leaves futures margin.
    transfer = next(b for m, p, b in c.calls
                    if p == "/api/v2/spot/wallet/transfer")
    assert transfer["fromType"] == "usdt_futures" and transfer["toType"] == "spot"
    assert transfer["amount"] == "58.00"
    sub = next(b for m, p, b in c.calls
               if p == "/api/v2/earn/savings/subscribe")
    assert sub == {"productId": "7001", "periodType": "flexible", "amount": "98.00"}


def test_stake_is_stables_only_and_never_calls_out_for_other_coins():
    from bot.core.yield_radar import execute_stake
    c = _money_client()
    res = execute_stake(c, "ETH", futures_free_usdt=100.0)
    assert not res.ok
    assert not any(m == "POST" for m, _p, _b in c.calls), \
        "a refused stake must not touch the account"


def test_stake_reports_stranded_spot_funds_when_subscribe_fails():
    from bot.core.yield_radar import execute_stake
    c = _money_client(subscribe={"code": "40915", "msg": "product sold out"})
    res = execute_stake(c, "USDT", futures_free_usdt=100.0)
    assert not res.ok
    assert "sold out" in res.message and "spot" in res.message


def test_unstake_redeems_full_and_returns_stables_to_futures_margin():
    from bot.core.yield_radar import execute_unstake
    c = _money_client()
    res = execute_unstake(c, "7001")
    assert res.ok, res.message
    redeem = next(b for m, p, b in c.calls if p == "/api/v2/earn/savings/redeem")
    assert redeem["productId"] == "7001" and redeem["amount"] == "98"
    back = next(b for m, p, b in c.calls if p == "/api/v2/spot/wallet/transfer")
    assert back["fromType"] == "spot" and back["toType"] == "usdt_futures"
    assert back["amount"] == "98.00"


def test_unstake_unknown_position_moves_nothing():
    from bot.core.yield_radar import execute_unstake
    c = _money_client()
    res = execute_unstake(c, "9999")
    assert not res.ok
    assert not any(m == "POST" for m, _p, _b in c.calls)

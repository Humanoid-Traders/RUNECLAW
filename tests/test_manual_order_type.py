"""WEB-SIGNALS: manual/web trades can be MARKET (open now) or LIMIT (rest at
entry). The order type threads through build_manual_idea; default stays 'limit'
so the platform's historical maker-only behaviour is unchanged.
"""

from bot.skills.manual_trade import build_manual_idea, normalize_order_type


def test_normalize_order_type_only_market_or_limit():
    assert normalize_order_type("market") == "market"
    assert normalize_order_type("MARKET") == "market"
    assert normalize_order_type("limit") == "limit"
    # Anything unrecognised (incl. None/empty/garbage) falls back to limit.
    for bad in (None, "", "  ", "stop", "foo", 123):
        assert normalize_order_type(bad) == "limit"


def test_build_manual_idea_defaults_to_limit():
    idea = build_manual_idea("LONG", "SOL", 100.0, 95.0, 110.0)
    assert idea.order_type == "limit"


def test_build_manual_idea_honours_market():
    idea = build_manual_idea("SHORT", "ETH", 1721.0, 1760.0, 1642.0,
                             order_type="market")
    assert idea.order_type == "market"


def test_build_manual_idea_bad_order_type_falls_back_to_limit():
    idea = build_manual_idea("LONG", "BTC", 60000.0, 59000.0, 63000.0,
                             order_type="banana")
    assert idea.order_type == "limit"

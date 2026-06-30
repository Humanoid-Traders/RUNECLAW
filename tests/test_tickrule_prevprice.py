"""
Tick-rule aggressor inference advances prev_price every trade (deep-audit low #31).

When the exchange omits a trade's side, the aggressor is inferred by the tick
rule (uptick = buyer-initiated). The update `prev_price = price` used to live
INSIDE the side-missing branch, so exchange-sided trades did not advance it — a
later side-less trade then compared against a stale price from the last side-less
trade (possibly many trades ago), mislabeling or dropping the aggressor. It now
advances every iteration, matching _fill_whale_metrics.
"""

from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowSignal


def _trades():
    # Chronological. t3/t4 have NO side → inferred from the immediately
    # preceding trade's price.
    return [
        {"timestamp": 1, "price": 100.0, "side": "buy", "cost": 100.0},
        {"timestamp": 2, "price": 110.0, "side": "sell", "cost": 110.0},
        {"timestamp": 3, "price": 105.0, "cost": 105.0},   # 105 < 110 → sell
        {"timestamp": 4, "price": 120.0, "cost": 120.0},   # 120 > 105 → buy
    ]


def test_tick_rule_uses_immediately_preceding_trade():
    an = OrderFlowAnalyzer()
    sig = OrderFlowSignal(symbol="BTC/USDT")
    an._fill_trade_metrics(sig, _trades(), "BTC/USDT")
    # t1 buy(100) + t4 tick-buy(120) = 220; t2 sell(110) + t3 tick-sell(105) = 215.
    # The buggy version left prev_price=None through the sided trades, so t3/t4
    # could not be classified and these totals would not include them.
    assert sig.buy_volume_usd == 220.0
    assert sig.sell_volume_usd == 215.0


def test_all_sided_trades_unaffected():
    # When every trade carries a side, the tick rule never runs → unchanged.
    an = OrderFlowAnalyzer()
    sig = OrderFlowSignal(symbol="ETH/USDT")
    trades = [
        {"timestamp": 1, "price": 50.0, "side": "buy", "cost": 50.0},
        {"timestamp": 2, "price": 51.0, "side": "sell", "cost": 30.0},
    ]
    an._fill_trade_metrics(sig, trades, "ETH/USDT")
    assert sig.buy_volume_usd == 50.0
    assert sig.sell_volume_usd == 30.0

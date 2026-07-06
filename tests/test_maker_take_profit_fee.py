"""
Maker take-profit fee modelling (backtest).

The partial-TP ladder (TP1/TP2) is where most take-profits happen. When
MAKER_TAKE_PROFIT_ENABLED is on, the backtest charges the TP-exit leg at the
maker rate (maker_fee_pct) instead of the taker market rate (commission_pct),
quantifying the fee saving. Default OFF — byte-identical to today, and the flag
does NOT change live order placement (that wiring is a separate change).
"""

from bot.config import CONFIG


def test_flag_defaults_off():
    # Safety: must default OFF so it never silently alters live/backtest fees.
    assert CONFIG.limit_orders.maker_take_profit_enabled is False


def test_maker_cheaper_than_taker():
    # The premise: maker fee must be below the taker/commission rate, else the
    # feature would be a no-op or worse.
    assert CONFIG.risk.maker_fee_pct < CONFIG.risk.taker_fee_pct
    assert CONFIG.risk.maker_fee_pct < CONFIG.risk.commission_pct

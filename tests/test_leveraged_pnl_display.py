"""Live position cards must show the REAL dollar P&L, not an unleveraged
fraction of it. A 10x position printing a −28.6% ROE while showing only −$0.43
(the real loss was ~−$4.3) makes the operator wildly under-read their risk — the
percentage was leveraged but the dollar wasn't. `_leveraged_pnl_usd` puts the two
on the same basis: dollars = ROE × margin = price-move × notional.
"""
import inspect

from bot.skills.telegram_handler import _leveraged_pnl_usd


def test_matches_real_loss_not_unleveraged_fraction():
    # BLESS live card from the field report: entry 0.009114 -> 0.008853, LONG,
    # $15.07 margin, 10x. The card showed −$0.43; the real loss is ~−$4.3.
    v = _leveraged_pnl_usd(0.009114, 0.008853, "LONG", 15.07, 10)
    assert round(v, 2) == -4.32
    # NOT the old unleveraged price-move × margin (~−$0.43).
    assert abs(v - (-0.43)) > 3.0


def test_reconciles_with_realized_close():
    # Closed at entry 0.009146 -> exit 0.008871, $15.21 margin, 10x. Realized
    # was −$4.72 including $0.18 fees, so gross ≈ −$4.57.
    gross = _leveraged_pnl_usd(0.009146, 0.008871, "LONG", 15.21, 10)
    assert -4.7 < gross < -4.4


def test_equals_roe_times_margin():
    # By construction: dollars == (roe_fraction) × margin.
    entry, last, margin, lev = 100.0, 108.0, 50.0, 3
    roe_frac = ((last - entry) / entry) * lev  # +24%
    assert abs(_leveraged_pnl_usd(entry, last, "LONG", margin, lev) - roe_frac * margin) < 1e-9


def test_short_direction_sign():
    # SHORT profits when price falls.
    assert _leveraged_pnl_usd(100, 95, "SHORT", 10, 5) == 2.5
    assert _leveraged_pnl_usd(100, 105, "SHORT", 10, 5) == -2.5


def test_missing_leverage_falls_back_to_1x():
    assert _leveraged_pnl_usd(100, 90, "LONG", 10, 0) == -1.0
    assert _leveraged_pnl_usd(100, 90, "LONG", 10, None) == -1.0


def test_guards_non_positive_inputs():
    assert _leveraged_pnl_usd(0, 95, "LONG", 10, 5) == 0.0
    assert _leveraged_pnl_usd(100, 0, "LONG", 10, 5) == 0.0
    assert _leveraged_pnl_usd(100, 95, "LONG", 0, 5) == 0.0


def test_all_live_card_paths_use_the_helper():
    # The three live-position card data paths must route dollar P&L through the
    # helper — no path may reintroduce the unleveraged (price-move × quantity /
    # price-move × margin) formula.
    src = inspect.getfile(_leveraged_pnl_usd)
    text = open(src).read()
    assert text.count("_leveraged_pnl_usd(") >= 4  # def + 3 call sites
    # The buggy patterns are gone.
    assert "(last_price - pos.entry_price) * pos.quantity" not in text
    assert "_qty * (last_px - _entry)" not in text

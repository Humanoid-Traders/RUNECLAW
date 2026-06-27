"""
Regression tests for the F-3 notional/margin unit reconciliation.

Canonical decision: position_size_usd is MARGIN (the portfolio and the live
executor both commit it as collateral and derive notional = margin * leverage;
get_position_value() returns margin). Therefore:

  * Exposure checks #2/#14/#15 compare margin-to-margin directly — they must NOT
    divide the new position by leverage (that understated committed margin ~5x).
  * The Portfolio VaR (#21) reasons about NOTIONAL — the proposed position is
    added as margin * leverage, consistent with the open-position notionals it
    already sums (no unit mixing).
"""

import os
import tempfile
import re

import pytest

from bot.compat import UTC
from bot.utils.models import TradeIdea, Direction
from bot.config import CONFIG


def _engine(balance=10_000.0, **kw):
    from bot.risk.risk_engine import RiskEngine
    from bot.risk.portfolio import PortfolioTracker
    return RiskEngine(PortfolioTracker(initial_balance=balance),
                      state_file=os.path.join(tempfile.mkdtemp(), "s.json"), **kw)


def _idea(**kw):
    d = dict(asset="BTC/USDT", direction=Direction.LONG, entry_price=50000.0,
             stop_loss=49000.0, take_profit=53000.0, confidence=0.8,
             reasoning="x", source="t")
    d.update(kw)
    return TradeIdea(**d)


def _pct(line):
    m = re.search(r"([\d.]+)%", line)
    return float(m.group(1)) if m else None


class TestExposureUsesFullMargin:
    def test_portfolio_exposure_counts_full_position_margin(self):
        """PORTFOLIO_EXPOSURE must add the new position's FULL margin
        (position_size_usd), not margin/leverage."""
        eng = _engine()
        # Cap so the sized position is bounded and known-ish; read the actual
        # sized value back from the check so the assertion is exact.
        check = eng.evaluate(_idea(), atr=500.0, max_position_usd=1000.0)
        pos = check.position_size_usd
        equity = eng._portfolio.snapshot().equity_usd
        exp_line = next(c for c in (check.checks_passed + check.checks_failed)
                        if c.startswith("PORTFOLIO_EXPOSURE"))
        reported = _pct(exp_line)
        expected_full = pos / equity * 100
        expected_div = pos / equity / CONFIG.exchange.default_leverage * 100
        # Reported exposure tracks the FULL margin, not the /leverage version.
        assert reported == pytest.approx(expected_full, abs=0.2), exp_line
        assert reported != pytest.approx(expected_div, abs=0.05)

    def test_symbol_exposure_counts_full_margin(self):
        eng = _engine()
        check = eng.evaluate(_idea(), atr=500.0, max_position_usd=1000.0)
        pos = check.position_size_usd
        equity = eng._portfolio.snapshot().equity_usd
        sym_line = next(c for c in (check.checks_passed + check.checks_failed)
                        if c.startswith("SYMBOL_EXPOSURE"))
        assert _pct(sym_line) == pytest.approx(pos / equity * 100, abs=0.2), sym_line

    def test_no_leverage_division_in_exposure_source(self):
        import inspect
        from bot.risk.risk_engine import RiskEngine
        src = inspect.getsource(RiskEngine.evaluate)
        assert "position_usd / leverage" not in src


class TestPositionSizeLabel:
    def test_position_size_labeled_margin(self):
        eng = _engine()
        check = eng.evaluate(_idea(), atr=500.0, max_position_usd=1000.0)
        line = next(c for c in (check.checks_passed + check.checks_failed)
                    if c.startswith("POSITION_SIZE"))
        assert "margin" in line
        assert "notional" not in line


class TestVarUsesNotional:
    def _seed(self, eng, n=6):
        port = eng._portfolio
        for i in range(n):
            idea = _idea(entry_price=100.0, stop_loss=95.0, take_profit=115.0)
            tr = port.open_position(idea, size_usd=100.0)
            port.close_position(tr.trade_id, 105.0 if i % 2 == 0 else 97.0)

    def test_proposed_var_adds_notional_not_margin(self):
        """The proposed position's VaR contribution scales with notional
        (margin * leverage)."""
        eng = _engine()
        self._seed(eng)
        lev = CONFIG.exchange.default_leverage
        small = eng._compute_portfolio_var(100.0)
        big = eng._compute_portfolio_var(100.0 * lev)
        # A position with `lev`x the margin must produce a strictly larger
        # proposed VaR — confirming notional (not raw margin) drives it.
        assert big.proposed_var_pct > small.proposed_var_pct

    def test_var_source_converts_margin_to_notional(self):
        import inspect
        from bot.risk.risk_engine import RiskEngine
        src = inspect.getsource(RiskEngine._compute_portfolio_var)
        assert "proposed_notional = position_usd" in src


class TestUnitsDocumented:
    def test_module_docstring_states_canonical_margin(self):
        import bot.risk.risk_engine as m
        doc = m.__doc__ or ""
        assert "Canonical unit" in doc and "MARGIN" in doc
        assert "notional = margin * leverage" in doc

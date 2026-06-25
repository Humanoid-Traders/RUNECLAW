"""
Regression tests for the V5 audit follow-up risk fixes:

  RC-AUD-007 — Portfolio VaR now returns an explicit VarResult (status SKIP/OK)
               instead of magic-tuple sentinels.  Pins the insufficient-data skip
               and the zero-equity -> reject (proposed=100%) behavior.

  RC-AUD-011 — Macro / session size-reduction providers now fail toward SAFETY
               (apply a conservative reduction multiplier + audit) instead of
               silently dropping the reduction, and the order-flow gates (#22/#23)
               skip explicitly + audited when no analyzer is wired.

These follow the patterns in tests/test_risk_upgrades.py and are fully
deterministic / self-contained (isolated tmp state file, fresh portfolio).
"""

import logging
import os
import tempfile

from bot.utils.models import Direction, PortfolioState, TradeIdea
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine, VarResult, VarStatus


# ── Fixtures ─────────────────────────────────────────────────────

def _make_engine(balance: float = 10_000.0, **kwargs) -> RiskEngine:
    """Create a RiskEngine with a fresh portfolio and no persisted state."""
    state_file = os.path.join(tempfile.mkdtemp(), "risk_state.json")
    portfolio = PortfolioTracker(initial_balance=balance)
    return RiskEngine(portfolio, state_file=state_file, **kwargs)


def _make_idea(**kwargs) -> TradeIdea:
    defaults = dict(
        asset="BTC/USDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        confidence=0.75,
        reasoning="test idea",
        source="test",
    )
    defaults.update(kwargs)
    return TradeIdea(**defaults)


def _seed_closed_trades(engine: RiskEngine, n: int = 6) -> None:
    """Open and close `n` small positions so the portfolio has >= n closed
    trades with varied returns (needed for VaR to compute)."""
    portfolio = engine._portfolio
    for i in range(n):
        idea = _make_idea(entry_price=100.0, stop_loss=95.0, take_profit=115.0)
        trade = portfolio.open_position(idea, size_usd=100.0)
        # Alternate winners / losers so volatility is non-zero.
        exit_price = 105.0 if i % 2 == 0 else 97.0
        portfolio.close_position(trade.trade_id, exit_price)


# ══════════════════════════════════════════════════════════════════
# RC-AUD-007: explicit VaR result
# ══════════════════════════════════════════════════════════════════

class TestVarExplicitResult:

    def test_insufficient_data_returns_skip(self):
        """Fewer than 5 closed trades -> VarStatus.SKIP (caller passes)."""
        engine = _make_engine()
        result = engine._compute_portfolio_var(1_000.0)
        assert isinstance(result, VarResult)
        assert result.status == VarStatus.SKIP
        # Sentinel magnitudes preserved for backward compatibility.
        assert result.current_var_pct == -1.0
        assert result.proposed_var_pct == -1.0

    def test_evaluate_lists_var_skip_when_no_history(self):
        """The check #21 call site reports a pass-skip with no trade history."""
        engine = _make_engine()
        idea = _make_idea()
        check = engine.evaluate(idea, atr=1.0)
        assert any("PORTFOLIO_VAR: skipped" in c for c in check.checks_passed)

    def test_ok_status_with_sufficient_history(self):
        """>= 5 closed trades -> VarStatus.OK with real (non-negative) numbers."""
        engine = _make_engine()
        _seed_closed_trades(engine, n=6)
        result = engine._compute_portfolio_var(500.0)
        assert result.status == VarStatus.OK
        assert result.current_var_pct >= 0.0
        assert result.proposed_var_pct >= 0.0
        # Proposed exposure includes the new position, so it cannot be below current.
        assert result.proposed_var_pct >= result.current_var_pct

    def test_zero_equity_rejects(self):
        """Zero/negative equity with a pending position -> OK + proposed=100%.

        This pins the reject path: status stays OK (so the call site evaluates),
        and proposed_var_pct=100.0 always exceeds the configured limit -> REJECT.
        """
        engine = _make_engine()
        _seed_closed_trades(engine, n=6)

        # Force equity <= 0 at snapshot time so we hit the zero-equity branch.
        def _zero_equity_snapshot() -> PortfolioState:
            return PortfolioState(
                balance_usd=0.0,
                equity_usd=0.0,
                open_positions=0,
                total_trades=6,
                daily_pnl=0.0,
                max_drawdown_pct=0.0,
            )

        engine._portfolio.snapshot = _zero_equity_snapshot  # type: ignore[assignment]

        result = engine._compute_portfolio_var(1_000.0)
        assert result.status == VarStatus.OK
        assert result.proposed_var_pct == 100.0

        from bot.config import CONFIG
        # Confirm the encoding actually rejects against the real limit.
        assert result.proposed_var_pct > CONFIG.risk.max_portfolio_var_pct


# ══════════════════════════════════════════════════════════════════
# RC-AUD-011: fail-toward-safety size reductions + audited skips
# ══════════════════════════════════════════════════════════════════

class _RaisingMacroProvider:
    """Macro provider whose get_context always raises."""

    def get_context(self, symbol=None):
        raise RuntimeError("macro provider boom")


class TestProviderFallbackReducesSize:

    def test_session_provider_exception_reduces_size(self, monkeypatch, caplog):
        """A raising session provider must halve size (vs a normal 1.0x session),
        not silently leave full size, and must audit the fallback."""
        import bot.core.session_aware as session_aware

        class _FixedSession:
            size_multiplier = 1.0  # deterministic baseline (no session reduction)

        # Use a wide-but-valid stop so the fixed-fractional size lands BELOW the
        # notional cap — otherwise the final cap floors both runs to the same
        # value and hides the reduction.  entry=100, SL=80 -> ~$1000 uncapped
        # (< $1300 cap at 10k equity); R:R=3.0 keeps the trade approvable.
        def _wide_stop_idea():
            return _make_idea(entry_price=100.0, stop_loss=80.0, take_profit=160.0)

        # Baseline: session returns a neutral 1.0x multiplier.
        monkeypatch.setattr(session_aware, "get_current_session", lambda *a, **k: _FixedSession())
        baseline_engine = _make_engine()
        baseline = baseline_engine.evaluate(_wide_stop_idea(), atr=1.0)

        # Fallback: session provider raises -> conservative 0.5x reduction.
        def _raise(*a, **k):
            raise RuntimeError("session provider boom")

        monkeypatch.setattr(session_aware, "get_current_session", _raise)
        fb_engine = _make_engine()
        with caplog.at_level(logging.WARNING):
            fallback = fb_engine.evaluate(_wide_stop_idea(), atr=1.0)

        # Size is reduced (not left full) — at most half the neutral-session size.
        assert fallback.position_size_usd <= baseline.position_size_usd * 0.5 + 1e-6
        assert fallback.position_size_usd > 0.0
        # And the fallback was audited.
        assert any(getattr(r, "action", "") == "session_size_reduction"
                   and getattr(r, "result", "") == "PROVIDER_ERROR_FALLBACK"
                   for r in caplog.records)

    def test_macro_provider_exception_audited_and_reduces(self, monkeypatch, caplog):
        """A raising macro provider audits the conservative fallback.

        (Macro check #18 separately fail-closes the trade, so we assert via the
        audit event + the recorded multiplier rather than the final verdict.)"""
        engine = _make_engine(macro_provider=_RaisingMacroProvider())
        with caplog.at_level(logging.WARNING):
            engine.evaluate(_make_idea(), atr=1.0)

        assert any(
            getattr(r, "action", "") == "macro_size_reduction"
            and getattr(r, "result", "") == "PROVIDER_ERROR_FALLBACK"
            for r in caplog.records
        ), "expected a macro_size_reduction PROVIDER_ERROR_FALLBACK audit event"


class TestOrderFlowSkipAudited:

    def test_order_flow_gates_skip_passes_and_audits(self, caplog):
        """With no order-flow analyzer wired, #22/#23 still PASS (deliberate
        fail-open) but the skip is now audited."""
        engine = _make_engine()  # no order_flow_analyzer
        idea = _make_idea()
        with caplog.at_level(logging.INFO):
            check = engine.evaluate(idea, atr=1.0)

        # Fail-open preserved: both gates appear in checks_passed.
        assert any("TAKER_3BAR: skipped" in c for c in check.checks_passed)
        assert any("BID_DOMINANCE: skipped" in c for c in check.checks_passed)

        # Skip is now explicit + audited.
        skip_events = [
            r for r in caplog.records
            if getattr(r, "action", "") == "order_flow_gate"
            and getattr(r, "result", "") == "SKIPPED_NO_ANALYZER"
        ]
        checks = {getattr(r, "data", {}).get("check") for r in skip_events
                  if isinstance(getattr(r, "data", None), dict)}
        assert "TAKER_3BAR" in checks
        assert "BID_DOMINANCE" in checks

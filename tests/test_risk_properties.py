"""
Property/fuzz tests (hypothesis) for the money path: TradeIdea construction and
RiskEngine.evaluate(). Unit tests check specific cases; these assert *invariants*
over thousands of generated inputs — the class of edge cases (tiny stop distances,
boundary confidence, future/stale timestamps, every strategy type) that
hand-written examples miss.

Invariants asserted:
  • TradeIdea rejects wrong-side / non-finite / non-positive geometry at build.
  • evaluate() is TOTAL (fail-closed): it never raises — any un-evaluable input
    becomes a REJECTED verdict, never an exception.
  • Margin cap has authority: position_pct never exceeds max_position_pct (the
    hard cap), and position_size_usd is always finite and ≥ 0.
  • APPROVED ⟺ no failed checks; APPROVED ⇒ margin within the symbol cap.
  • REJECTED ⇒ at least one named failed check (no silent rejects).
  • Determinism: two fresh engines return the same verdict for the same idea.
"""

import math
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bot.compat import UTC
from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, RiskVerdict, TradeIdea


def _isolated_engine(balance: float = 10_000.0) -> RiskEngine:
    """A RiskEngine with its own state file (never the shared/ /dev/null path)."""
    state = os.path.join(tempfile.mkdtemp(prefix="rc-prop-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=balance), state_file=state)


# Module-global engines reused across examples (evaluate() records no losses, so
# the circuit breaker never trips and verdicts stay input-determined).
_ENGINE_A = _isolated_engine()
_ENGINE_B = _isolated_engine()


@st.composite
def valid_ideas(draw):
    """A directionally-valid TradeIdea spanning the full input surface."""
    direction = draw(st.sampled_from([Direction.LONG, Direction.SHORT]))
    entry = draw(st.floats(min_value=1e-3, max_value=1e6,
                           allow_nan=False, allow_infinity=False))
    sl_frac = draw(st.floats(min_value=1e-3, max_value=0.5))   # 0.1%–50% stop
    tp_frac = draw(st.floats(min_value=1e-3, max_value=0.9))   # profit target
    if direction == Direction.LONG:
        stop = entry * (1.0 - sl_frac)
        take = entry * (1.0 + draw(st.floats(min_value=1e-3, max_value=2.0)))
    else:
        stop = entry * (1.0 + sl_frac)
        take = entry * (1.0 - tp_frac)
    # Keep prices strictly positive and distinct from entry after arithmetic.
    if stop <= 0 or take <= 0 or stop == entry or take == entry:
        return draw(valid_ideas())
    age_s = draw(st.integers(min_value=-120, max_value=7200))
    ts = datetime.now(UTC) - timedelta(seconds=age_s)
    return TradeIdea(
        asset="BTC/USDT",
        direction=direction,
        entry_price=entry,
        stop_loss=stop,
        take_profit=take,
        confidence=draw(st.floats(min_value=0.0, max_value=1.0)),
        reasoning="property",
        source=draw(st.sampled_from(["scan", "manual"])),
        timestamp=ts,
        order_type=draw(st.sampled_from(["market", "limit"])),
        strategy_type=draw(st.sampled_from(["scalp", "intraday", "swing", "position"])),
    )


class TestTradeIdeaConstruction:
    @given(
        entry=st.floats(min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False),
        bad_frac=st.floats(min_value=1e-3, max_value=0.9),
        direction=st.sampled_from([Direction.LONG, Direction.SHORT]),
    )
    @settings(max_examples=200, deadline=None)
    def test_wrong_side_stop_never_constructs(self, entry, bad_frac, direction):
        # Put the stop on the PROFIT side — the model must refuse it.
        if direction == Direction.LONG:
            bad_stop = entry * (1.0 + bad_frac)   # stop above entry on a long
            good_tp = entry * (1.0 + bad_frac + 0.01)
        else:
            bad_stop = entry * (1.0 - bad_frac)   # stop below entry on a short
            good_tp = entry * (1.0 - bad_frac - 0.001)
        with pytest.raises(Exception):
            TradeIdea(asset="X/USDT", direction=direction, entry_price=entry,
                      stop_loss=bad_stop, take_profit=good_tp, confidence=0.5,
                      reasoning="x")

    @given(bad=st.sampled_from([float("nan"), float("inf"), -float("inf"), 0.0, -5.0]))
    @settings(max_examples=20, deadline=None)
    def test_non_finite_or_nonpositive_entry_rejected(self, bad):
        with pytest.raises(Exception):
            TradeIdea(asset="X/USDT", direction=Direction.LONG, entry_price=bad,
                      stop_loss=1.0, take_profit=2.0, confidence=0.5, reasoning="x")

    @given(idea=valid_ideas())
    @settings(max_examples=200, deadline=None)
    def test_valid_idea_has_finite_nonneg_rr(self, idea):
        rr = idea.risk_reward_ratio
        assert math.isfinite(rr)
        assert rr >= 0.0


class TestEvaluateInvariants:
    @given(idea=valid_ideas())
    @settings(max_examples=300, deadline=None)
    def test_evaluate_is_total_and_bounded(self, idea):
        # 1. Totality / fail-closed: never raises, always a valid verdict.
        check = _ENGINE_A.evaluate(idea)
        assert check.verdict in (RiskVerdict.APPROVED, RiskVerdict.REJECTED)

        # 2. Size is always a sane number.
        assert math.isfinite(check.position_size_usd)
        assert check.position_size_usd >= 0.0
        assert math.isfinite(check.position_pct)

        # 3. The hard margin cap has authority for EVERY result (equity > 0):
        #    position_pct can never exceed the effective per-trade cap. With the
        #    per-strategy notional cap enabled (now the default), that cap is the
        #    idea's strategy ceiling (e.g. position 15%); otherwise it's the global
        #    max_position_pct.
        if CONFIG.risk.per_strategy_notional_cap_enabled:
            _cap = CONFIG.strategy_types.get_max_position_pct(
                getattr(idea, "strategy_type", "swing"), CONFIG.risk.max_position_pct)
        else:
            _cap = CONFIG.risk.max_position_pct
        assert check.position_pct <= _cap + 1e-6

        # 4. Verdict ⟺ failed-check list agree (no silent disagreement).
        if check.verdict == RiskVerdict.APPROVED:
            assert check.checks_failed == []
            # Approved margin must sit within the symbol-exposure cap.
            assert check.position_pct <= CONFIG.risk.max_symbol_exposure_pct + 1e-6
        else:
            assert len(check.checks_failed) >= 1
            assert check.reason

    @given(idea=valid_ideas())
    @settings(max_examples=150, deadline=None)
    def test_verdict_is_deterministic_across_fresh_engines(self, idea):
        a = _ENGINE_A.evaluate(idea)
        b = _ENGINE_B.evaluate(idea)
        assert a.verdict == b.verdict
        # Sizing is a pure function of (idea, equity) → identical to the cent.
        assert a.position_size_usd == pytest.approx(b.position_size_usd, abs=0.01)

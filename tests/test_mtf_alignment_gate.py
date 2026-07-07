"""
MTF-alignment gate (opt-in): reject counter-trend entries using the analyzer's
higher-timeframe trend (idea.htf_trend). Previously the gate was DEAD — it
parsed "MTF:1h=UP" strings from signals_used that nothing ever produced, so it
skipped every trade. This suite pins the revived, direction-aware behaviour and
the byte-identical OFF path.
"""
import os, tempfile
from contextlib import contextmanager
from datetime import datetime

from bot.compat import UTC
from bot.config import CONFIG
from bot.risk.risk_engine import RiskEngine
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import TradeIdea, Direction


@contextmanager
def _gate(enabled):
    old = CONFIG.risk.mtf_alignment_gate_enabled
    object.__setattr__(CONFIG.risk, "mtf_alignment_gate_enabled", enabled)
    try:
        yield
    finally:
        object.__setattr__(CONFIG.risk, "mtf_alignment_gate_enabled", old)


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-mtf-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea(direction=Direction.LONG, htf_trend="", signals=None):
    # SL/TP must sit on the correct side of entry for each direction.
    if direction == Direction.LONG:
        sl, tp = 97.5, 105.0
    else:
        sl, tp = 102.5, 95.0
    return TradeIdea(
        asset="BTC/USDT", direction=direction, entry_price=100.0,
        stop_loss=sl, take_profit=tp, confidence=0.9,
        reasoning="mtf gate test", source="scan",
        signals_used=signals or ["rsi", "macd"],
        htf_trend=htf_trend, timestamp=datetime.now(UTC),
    )


# ── OFF path: byte-identical dead-skip ────────────────────────────────
def test_off_never_rejects_even_counter_trend():
    eng = _engine()
    with _gate(False):
        # LONG into a bearish HTF would be rejected if ON — must pass when OFF.
        assert eng._check_mtf_alignment(_idea(Direction.LONG, "bearish")) is None
        assert eng._check_mtf_alignment(_idea(Direction.SHORT, "bullish")) is None


# ── ON path: reject counter-trend ────────────────────────────────────
def test_on_rejects_long_into_bearish_htf():
    eng = _engine()
    with _gate(True):
        reason = eng._check_mtf_alignment(_idea(Direction.LONG, "bearish"))
    assert reason is not None and "MTF_ALIGNMENT" in reason


def test_on_rejects_short_into_bullish_htf():
    eng = _engine()
    with _gate(True):
        reason = eng._check_mtf_alignment(_idea(Direction.SHORT, "bullish"))
    assert reason is not None and "MTF_ALIGNMENT" in reason


# ── ON path: allow with-trend and neutral ────────────────────────────
def test_on_allows_long_with_bullish_htf():
    eng = _engine()
    with _gate(True):
        assert eng._check_mtf_alignment(_idea(Direction.LONG, "bullish")) is None


def test_on_allows_short_with_bearish_htf():
    eng = _engine()
    with _gate(True):
        assert eng._check_mtf_alignment(_idea(Direction.SHORT, "bearish")) is None


def test_on_skips_neutral_and_unknown_htf():
    eng = _engine()
    with _gate(True):
        assert eng._check_mtf_alignment(_idea(Direction.LONG, "neutral")) is None
        assert eng._check_mtf_alignment(_idea(Direction.LONG, "")) is None


# ── ON path: legacy signals_used fallback ────────────────────────────
def test_on_uses_legacy_mtf_tag_fallback():
    eng = _engine()
    # No htf_trend, but two bearish MTF tags → derived bearish → LONG rejected.
    idea = _idea(Direction.LONG, "", signals=["MTF:4h=DOWN", "MTF:1d=DOWN", "rsi"])
    with _gate(True):
        reason = eng._check_mtf_alignment(idea)
    assert reason is not None and "MTF_ALIGNMENT" in reason


# ── Full evaluate() surfaces the rejection ───────────────────────────
def test_evaluate_surfaces_mtf_rejection():
    eng = _engine()
    with _gate(True):
        check = eng.evaluate(_idea(Direction.LONG, "bearish"), atr=2.0)
    assert any("MTF_ALIGNMENT" in c for c in check.checks_failed)

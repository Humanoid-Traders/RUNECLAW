"""
Re-entry cooldown: throttle a fresh entry on a symbol within
REENTRY_COOLDOWN_SECONDS of the last REAL fill on that symbol. Unlike the
loss-only cooldown (check #13), this fires after ANY close and curbs fee churn.
Gated, default OFF; the stamp happens at the real fill (note_symbol_entry), the
check is read-only in evaluate(); skips manual trades. Byte-identical when OFF.
"""
import os, tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta

from bot.compat import UTC
from bot.config import CONFIG
from bot.risk.risk_engine import RiskEngine
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import TradeIdea, Direction


@contextmanager
def _cooldown(enabled, seconds):
    # CONFIG.risk is a frozen dataclass — toggle via object.__setattr__ + restore.
    old_en = CONFIG.risk.reentry_cooldown_enabled
    old_sec = CONFIG.risk.reentry_cooldown_seconds
    object.__setattr__(CONFIG.risk, "reentry_cooldown_enabled", enabled)
    object.__setattr__(CONFIG.risk, "reentry_cooldown_seconds", float(seconds))
    try:
        yield
    finally:
        object.__setattr__(CONFIG.risk, "reentry_cooldown_enabled", old_en)
        object.__setattr__(CONFIG.risk, "reentry_cooldown_seconds", old_sec)


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-reentry-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea(asset="BTC/USDT", source="scan"):
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.5, take_profit=105.0, confidence=0.9,
                     reasoning="reentry cooldown test", source=source,
                     timestamp=datetime.now(UTC))


def _has_reentry_reject(check):
    return any("REENTRY_COOLDOWN" in c for c in check.checks_failed)


T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_stamp_noop_when_flag_off():
    """note_symbol_entry must not touch the ledger while the flag is off."""
    eng = _engine()
    with _cooldown(False, 3600):
        eng.note_symbol_entry("BTC/USDT", as_of=T0)
    assert eng._last_entry_by_symbol == {}


def test_off_never_rejects_even_with_stale_ledger():
    """With the flag off, a populated ledger must not gate anything."""
    eng = _engine()
    # Force a stamp into the ledger directly, then evaluate with the flag OFF.
    eng._last_entry_by_symbol["BTC/USDT"] = T0.timestamp()
    with _cooldown(False, 3600):
        check = eng.evaluate(_idea(), atr=2.0, as_of=T0 + timedelta(seconds=10))
    assert not _has_reentry_reject(check)


def test_on_blocks_immediate_reentry():
    eng = _engine()
    with _cooldown(True, 3600):
        eng.note_symbol_entry("BTC/USDT", as_of=T0)
        check = eng.evaluate(_idea(), atr=2.0, as_of=T0 + timedelta(seconds=600))
    assert _has_reentry_reject(check)


def test_on_allows_after_window_elapses():
    eng = _engine()
    with _cooldown(True, 3600):
        eng.note_symbol_entry("BTC/USDT", as_of=T0)
        check = eng.evaluate(_idea(), atr=2.0, as_of=T0 + timedelta(seconds=3700))
    assert not _has_reentry_reject(check)


def test_on_does_not_block_a_different_symbol():
    eng = _engine()
    with _cooldown(True, 3600):
        eng.note_symbol_entry("BTC/USDT", as_of=T0)
        check = eng.evaluate(_idea(asset="ETH/USDT"), atr=2.0,
                             as_of=T0 + timedelta(seconds=60))
    assert not _has_reentry_reject(check)


def test_on_skips_manual_trades():
    eng = _engine()
    with _cooldown(True, 3600):
        eng.note_symbol_entry("BTC/USDT", as_of=T0)
        check = eng.evaluate(_idea(source="manual"), atr=2.0,
                             as_of=T0 + timedelta(seconds=60))
    assert not _has_reentry_reject(check)


def test_zero_seconds_is_noop():
    eng = _engine()
    with _cooldown(True, 0.0):
        eng.note_symbol_entry("BTC/USDT", as_of=T0)
        check = eng.evaluate(_idea(), atr=2.0, as_of=T0 + timedelta(seconds=1))
    assert not _has_reentry_reject(check)

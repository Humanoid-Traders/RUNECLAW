"""When an exchange stop-loss can't be placed, the operator's UNPROTECTED alert
must name the venue reason (Bitget code + msg) instead of a bare "could not be
placed" — that reason is the difference between "manually place a stop" and
knowing WHY the bot couldn't (precision / min-distance / no-position / …).

`_place_sl_tp` swallows venue errors and returns (None, None), so the reason has
to be captured out-of-band on the executor and surfaced in the alert. These tests
lock in the capture/clear/read helpers and that the alert paths consult them.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from bot.core.live_executor import LiveExecutor, normalize_symbol


def _bare_exec():
    """A LiveExecutor without running __init__ (no network/creds) — enough to
    exercise the pure diagnostic helpers."""
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._last_sltp_error = {}
    return ex


def test_note_then_read_by_symbol():
    ex = _bare_exec()
    ex._note_sltp_error("TAG/USDT:USDT", "25606: trigger price does not meet precision")
    assert "25606" in ex._last_sltp_reason("TAG/USDT:USDT")


def test_symbol_key_is_normalized():
    # The alert reads by pos.symbol; placement may record a differently-formatted
    # symbol. Both must resolve to the same normalized key.
    ex = _bare_exec()
    ex._note_sltp_error("TAG/USDT:USDT", "boom")
    assert ex._last_sltp_reason("TAG/USDT:USDT") == "boom"
    assert normalize_symbol("TAG/USDT:USDT")  # sanity: normalizer available


def test_clear_on_success():
    ex = _bare_exec()
    ex._note_sltp_error("TAG/USDT:USDT", "boom")
    ex._clear_sltp_error("TAG/USDT:USDT")
    assert ex._last_sltp_reason("TAG/USDT:USDT") == ""


def test_reason_is_truncated():
    ex = _bare_exec()
    ex._note_sltp_error("X/USDT:USDT", "e" * 500)
    assert 0 < len(ex._last_sltp_reason("X/USDT:USDT")) <= 180


def test_missing_symbol_returns_empty_string():
    assert _bare_exec()._last_sltp_reason("NOPE/USDT:USDT") == ""


def test_helpers_never_raise_without_store():
    # Defensive: even if _last_sltp_error is absent, helpers must not raise.
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._note_sltp_error("A/USDT:USDT", "x")  # must not raise
    assert ex._last_sltp_reason("A/USDT:USDT") in ("x", "")


# ── source invariants: the alert paths surface the reason ────────────────

def _src() -> str:
    return Path(inspect.getfile(LiveExecutor)).read_text()


def test_guardian_alert_surfaces_venue_reason():
    src = _src()
    assert "Venue reason" in src, "guardian UNPROTECTED alert must include the venue reason"
    assert "_last_sltp_reason(pos.symbol)" in src


def test_v3_and_classic_failures_record_reason():
    src = _src()
    # Both placement paths must record the rejection so it can be surfaced.
    assert src.count("_note_sltp_error(") >= 4
    # And success clears it so a stale reason doesn't linger.
    assert "_clear_sltp_error(" in src

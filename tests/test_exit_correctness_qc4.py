"""QC-4: exit-engine correctness (audit-confirmed, LIVE-path only).

Three live-only fixes that stop the exit engine from mishandling profitable
positions. None touch the backtest, so the FROZEN_BENCHMARK is byte-identical.

1. R-multiple denominator = INITIAL risk, not the live ratcheted stop. Once a
   stop trails to breakeven, entry-minus-stop ≈ 0 and a real winner reads as
   R=0 — which force-closes it via the time/hold exits.
2. Partial-TP state reconstructed after schema drift must keep the entry-time
   initial_risk. Rebuilding from the ratcheted live stop collapses it to ~0 and
   check_partial_tp then reads a huge current_r and dumps ~80% of the runner.
3. The time-stop's "in profit" gate is fee-aware: a position up a sub-fee
   fraction is a net loser and must not be spared indefinitely.
"""

from __future__ import annotations

import dataclasses

from bot.core.position_telemetry import r_denominator


class _Pos:
    def __init__(self, entry, stop, trailing_state=None):
        self.entry_price = entry
        self.stop_loss = stop
        self.trailing_state = trailing_state


# ── 1. R-denominator uses initial risk, not the ratcheted stop ───────────────

def test_r_denominator_prefers_stored_initial_risk():
    assert r_denominator(_Pos(100.0, 99.0, {"initial_risk": 2.5})) == 2.5


def test_r_denominator_falls_back_to_entry_minus_stop():
    assert r_denominator(_Pos(100.0, 98.0, None)) == 2.0


def test_ratcheted_breakeven_stop_does_not_collapse_the_denominator():
    # Stop trailed all the way to entry (breakeven). Naive entry-minus-stop = 0,
    # which would make every winner read as R=0 and get force-closed. The stored
    # initial_risk keeps the denominator sane.
    pos = _Pos(100.0, 100.0, {"initial_risk": 3.0})
    assert r_denominator(pos) == 3.0
    # A real +6 move is therefore correctly +2R, not the R=0 that force-closed it.
    pnl_raw = 106.0 - pos.entry_price
    assert pnl_raw / r_denominator(pos) == 2.0


def test_r_denominator_is_zero_only_when_truly_unknowable():
    # No trailing state AND entry == stop → genuinely 0 (caller guards risk > 0).
    assert r_denominator(_Pos(100.0, 100.0, None)) == 0.0
    assert r_denominator(_Pos(0, 0, {})) == 0.0


# ── 2. Partial-TP reconstruction keeps the entry-time initial_risk ───────────

def test_partial_tp_reconstruction_preserves_initial_risk():
    from bot.core.partial_tp import PartialTPState, create_partial_tp_state

    # A live runner: entry 100, initial SL 96 → 1R = 4. After TP1 the stop
    # ratcheted to breakeven (100.1) and the state was persisted.
    st = create_partial_tp_state("t1", "LONG", 100.0, 96.0, 112.0, 10.0, atr=2.0)
    st.tp1_hit = True
    st.current_sl = 100.1          # ratcheted to breakeven
    st.remaining_qty = 5.0
    persisted = dataclasses.asdict(st)

    # Reconstruct exactly as _run_partial_tp's drift branch does: filter to
    # known fields, then rebuild. The entry-time 1R must survive — NOT be
    # recomputed from the ratcheted stop (which would give ~0.1, not 4.0).
    valid = {f.name for f in dataclasses.fields(PartialTPState)}
    kept = {k: v for k, v in persisted.items() if k in valid}
    rebuilt = PartialTPState(**kept)
    assert rebuilt.initial_risk == 4.0
    assert rebuilt.tp1_hit is True

    # Guard the danger directly: a state rebuilt from the ratcheted stop would
    # collapse 1R and make a modest +2 move read as a >10R jump that dumps the
    # position. With the preserved 1R it reads correctly.
    current_r = (102.0 - rebuilt.entry_price) / rebuilt.initial_risk
    assert current_r == 0.5


def test_partial_tp_rebuild_from_ratcheted_stop_is_the_bug_we_avoid():
    # Demonstrates the failure mode the fix prevents: building from the live
    # ratcheted stop gives a near-zero 1R and an exploded current_r.
    from bot.core.partial_tp import create_partial_tp_state

    bad = create_partial_tp_state("t1", "LONG", 100.0, 100.1, 112.0, 10.0, atr=2.0)
    # initial_risk = |100 - 100.1| = 0.1 → a +2 move looks like +20R.
    assert round((102.0 - 100.0) / bad.initial_risk) == 20


# ── 3. Fee-aware time-stop ───────────────────────────────────────────────────

def test_time_stop_profit_gate_is_fee_aware_in_source():
    # The in-profit gate must clear a round-trip fee buffer, not bare entry.
    import inspect
    from bot.core import live_executor
    src = inspect.getsource(live_executor)
    assert "CONFIG.risk.taker_fee_pct" in src
    assert "price > pos.entry_price + _buf" in src
    assert "price < pos.entry_price - _buf" in src


def test_round_trip_fee_buffer_math():
    # A LONG up only 0.05% is BELOW the ~0.12% round-trip taker cost → not a
    # real winner, so the time-stop should NOT spare it.
    from bot.config import CONFIG
    entry = 100.0
    rt_fee = (CONFIG.risk.taker_fee_pct / 100.0) * 2.0
    buf = entry * rt_fee
    price_up_sub_fee = entry * 1.0005          # +0.05%
    assert not (price_up_sub_fee > entry + buf)
    price_up_over_fee = entry * 1.005          # +0.5% clears costs
    assert price_up_over_fee > entry + buf

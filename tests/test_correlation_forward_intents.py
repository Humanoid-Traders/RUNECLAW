"""
Round 7 Phase 1 — forward-looking correlation cap (pending-intent ledger).

The per-group correlation cap counts only already-OPEN positions, so a correlated
cluster that all signal on the same bar each see zero open group members and all
pass — silently bypassing max_correlation_per_group. The ledger records
APPROVED-but-not-yet-filled intents so the cap binds forward-looking. Gated by
CORRELATION_FORWARD_INTENTS_ENABLED (default OFF → ledger stays empty).
"""

import threading
from types import SimpleNamespace
from unittest.mock import patch

from bot.risk.risk_engine import RiskEngine


def _engine(open_positions=()):
    eng = RiskEngine.__new__(RiskEngine)
    eng._portfolio = SimpleNamespace(
        open_positions=list(open_positions),
        _positions={},
    )
    eng._price_history = {}
    eng._pending_intents = {}
    eng._lock = threading.RLock()
    eng._sim_now = None
    return eng


def _idea(asset, tid, direction="LONG"):
    return SimpleNamespace(id=tid, asset=asset,
                           direction=SimpleNamespace(value=direction))


def _cfg(enabled=True, cap=2, ttl=7200.0):
    cfg = patch("bot.risk.risk_engine.CONFIG")
    mock = cfg.start()
    mock.risk.correlation_forward_intents_enabled = enabled
    mock.risk.correlation_intent_ttl_sec = ttl
    mock.risk.max_correlation_per_group = cap
    mock.risk.max_unmapped_correlated = 3
    mock.risk.max_correlation = 0.85
    return cfg, mock


# ── Ledger mechanics ─────────────────────────────────────────────────


def test_flag_off_register_is_noop():
    eng = _engine()
    cfg, _ = _cfg(enabled=False)
    try:
        eng.register_pending_intent(_idea("SOL/USDT", "A"))
        assert eng._pending_intents == {}  # ledger stays empty when gated off
    finally:
        cfg.stop()


def test_register_counts_by_group_and_excludes_self():
    eng = _engine()
    cfg, _ = _cfg(enabled=True)
    try:
        eng.register_pending_intent(_idea("SOL/USDT", "A"))
        eng.register_pending_intent(_idea("AVAX/USDT", "B"))  # same ALT_L1 group
        # both count for a third, unrelated evaluation
        assert eng._pending_intent_group_count("ALT_L1", exclude_id="C") == 2
        # the idea being evaluated never counts itself
        assert eng._pending_intent_group_count("ALT_L1", exclude_id="A") == 1
        # different group is unaffected
        assert eng._pending_intent_group_count("BTC", exclude_id="C") == 0
    finally:
        cfg.stop()


def test_clear_removes_intent():
    eng = _engine()
    cfg, _ = _cfg(enabled=True)
    try:
        eng.register_pending_intent(_idea("SOL/USDT", "A"))
        eng.clear_pending_intent("A")
        assert eng._pending_intent_group_count("ALT_L1", exclude_id="Z") == 0
        eng.clear_pending_intent("A")  # idempotent — no raise
    finally:
        cfg.stop()


def test_ttl_prunes_leaked_intent():
    eng = _engine()
    cfg, _ = _cfg(enabled=True, ttl=100.0)
    try:
        eng._sim_now = 1_000.0
        eng.register_pending_intent(_idea("SOL/USDT", "A"))  # stamped at t=1000
        assert eng._pending_intent_group_count("ALT_L1", exclude_id="Z") == 1
        eng._sim_now = 1_000.0 + 100.0 + 1.0  # past TTL
        assert eng._pending_intent_group_count("ALT_L1", exclude_id="Z") == 0
    finally:
        cfg.stop()


# ── Integration with _check_correlation ──────────────────────────────


def test_pending_cluster_blocks_third_same_bar_entry():
    """Two approved-but-unfilled ALT_L1 intents + cap 2 → the third same-group
    idea is rejected, even though NO position is open yet (the bug's core case)."""
    eng = _engine(open_positions=[])  # nothing open — only pending intents exist
    cfg, _ = _cfg(enabled=True, cap=2)
    try:
        eng.register_pending_intent(_idea("SOL/USDT", "A"))
        eng.register_pending_intent(_idea("AVAX/USDT", "B"))
        reason = eng._check_correlation(_idea("NEAR/USDT", "C"))
        assert reason is not None and "CORRELATION" in reason
    finally:
        cfg.stop()


def test_flag_off_third_same_bar_entry_passes():
    """With the flag off, the same pending cluster is invisible: the third entry
    passes (the pre-existing bypass this feature closes)."""
    eng = _engine(open_positions=[])
    # register under a flag-on cfg so the ledger is populated...
    cfg_on, _ = _cfg(enabled=True, cap=2)
    try:
        eng.register_pending_intent(_idea("SOL/USDT", "A"))
        eng.register_pending_intent(_idea("AVAX/USDT", "B"))
    finally:
        cfg_on.stop()
    # ...then evaluate with the flag OFF: intents must be ignored.
    cfg_off, _ = _cfg(enabled=False, cap=2)
    try:
        assert eng._check_correlation(_idea("NEAR/USDT", "C")) is None
    finally:
        cfg_off.stop()

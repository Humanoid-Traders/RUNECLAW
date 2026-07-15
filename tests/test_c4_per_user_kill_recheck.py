"""
C4: the pre-execute kill re-check must consult the EXECUTING account's engine.

_confirm_trade_inner re-checks the kill switch under the per-symbol entry lock,
right before placing the order, to catch a halt/breaker that trips in the race
window after the main risk gate. It checked only self.risk (the shared operator
engine); a per-user breaker that trips in that window (e.g. the user hit their
own daily-loss limit) opens only THEIR engine, so the order would slip through.
Now it also checks risk_for(user_id) — the user's own engine (the shared engine
for the operator/default path).

Guarded by source inspection (the check lives inside a ~large method).
"""

import inspect

from bot.core.engine import RuneClawEngine


def _preexec_block() -> str:
    src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
    i = src.index("Kill-switch fail-closed re-check")
    j = src.index("Trade REJECTED: engine halted (kill-switch)", i)
    return src[i:j]


def test_preexecute_recheck_consults_executing_account_engine():
    block = _preexec_block()
    assert "risk_for(user_id).circuit_breaker_active" in block


def test_preexecute_recheck_still_checks_global_halt_and_shared_breaker():
    block = _preexec_block()
    assert "self._halted" in block
    assert "self.risk.circuit_breaker_active" in block

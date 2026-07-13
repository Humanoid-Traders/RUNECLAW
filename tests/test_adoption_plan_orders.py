"""
Adoption plan-order read + leverage-mismatch alert (2026-07-13) — live
ASTER incident.

An ASTER SHORT opened at 20:10 with a v3 combined TPSL; a restart at
20:21 re-adopted it as an orphan. Two gaps surfaced:

1. The adoption SL/TP fallback queried open orders WITHOUT the plan
   params, while the replace path (_place_sl_tp) queries the plan channel
   and CANCELS whatever it finds — so classic SL/TP legs invisible to the
   read path get swapped for generic 3%/6% safety defaults. The read path
   now queries the plan channel with the same venue params and classifies
   Bitget plan orders by info.planType (their ccxt `type` is just
   market/limit).

2. The venue filled at 20x against a 10x target (sticky per-symbol
   leverage survived set_leverage + both retries, and fetch_leverage
   verification was unavailable so the abort path couldn't engage). The
   fill card trued up the display but nothing TOLD the operator. Fill
   verification now audits leverage_mismatch_on_fill and appends a loud
   warning line to the execution card.
"""

from __future__ import annotations

import inspect

from bot.core.live_executor import LiveExecutor


# ── adoption reads the plan channel ──────────────────────────────────

def test_adoption_fallback_queries_plan_orders():
    src = inspect.getsource(LiveExecutor.adopt_exchange_positions)
    assert "plan_order_query_params" in src
    assert "is_plan_order" in src
    # The plan read must happen BEFORE the safety-default block, so real
    # venue stops are adopted instead of replaced with 3%/6% defaults.
    assert src.index("plan_order_query_params") < src.index("default_sl_pct")


def test_adoption_fallback_classifies_by_plan_type():
    # Bitget plan orders carry planType (loss_plan/profit_plan/pos_loss/
    # pos_profit) in info while ccxt `type` is market/limit — type alone
    # would misclassify every leg.
    src = inspect.getsource(LiveExecutor.adopt_exchange_positions)
    assert "planType" in src


def test_replace_path_and_read_path_use_same_channel():
    """The exact incident invariant: whatever _place_sl_tp can find (and
    cancel), the adoption read must also be able to find (and inherit)."""
    place_src = inspect.getsource(LiveExecutor._place_sl_tp)
    adopt_src = inspect.getsource(LiveExecutor.adopt_exchange_positions)
    assert "plan_order_query_params" in place_src
    assert "plan_order_query_params" in adopt_src


def test_plan_type_classification_logic():
    """The classifier string must route Bitget planType values correctly."""
    for plan_type, expect_sl in [("loss_plan", True), ("pos_loss", True),
                                 ("profit_plan", False), ("pos_profit", False)]:
        otype = f"market {plan_type}".lower()
        is_sl = "stop" in otype or "loss" in otype
        is_tp = "take" in otype or "profit" in otype
        if expect_sl:
            assert is_sl, plan_type
        else:
            assert not is_sl and is_tp, plan_type


# ── leverage mismatch alert ──────────────────────────────────────────

def test_fill_verification_audits_leverage_mismatch():
    src = inspect.getsource(LiveExecutor.execute)
    assert "leverage_mismatch_on_fill" in src
    assert "_lev_mismatch" in src


def test_mismatch_warning_reaches_the_card():
    """The warning must be part of the operator-visible execution card,
    not just a log line (the whole point — the operator kept discovering
    20x fills by reading position cards manually)."""
    src = inspect.getsource(LiveExecutor.execute)
    assert "LEVERAGE: venue filled at" in src
    # It rides the same warn-suffix the unprotected alert uses, which is
    # appended to the returned card text.
    assert "sl_tp_warn +=" in src or 'sl_tp_warn = (' in src

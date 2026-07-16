"""Web reports builder + web-queued stance pull (PR L: web↔Telegram parity).

Contracts under test:
- build_reports_payload: every section fails soft to None independently; the
  parity section keeps headline keys only (no bucket dumps on the web).
- pull_and_apply_stance: applies ONLY for a valid mode requested by a user
  whose tier in the bot's own UserStore is 'admin'; everything else is
  rejected but still ACKED so a bad request can't retry forever.
"""

from __future__ import annotations

import bot.core.web_reports as wr
import bot.utils.control_pull as ctl
from bot.config import RUNTIME


# ── build_reports_payload ─────────────────────────────────────────────

def test_sections_fail_soft_independently(monkeypatch):
    monkeypatch.setattr(wr, "_funding_section",
                        lambda: {"rows": [{"base": "BTC"}]})
    monkeypatch.setattr(wr, "_arb_section",
                        lambda: (_ for _ in ()).throw(RuntimeError("venue down")))
    monkeypatch.setattr(wr, "_parity_section", lambda engine: {"trades": 5})
    monkeypatch.setattr(wr, "_yield_section", lambda engine: None)
    payload = wr.build_reports_payload(engine=object())
    assert payload["funding"] == {"rows": [{"base": "BTC"}]}
    assert payload["arb"] is None            # crash -> None, not propagation
    assert payload["parity"] == {"trades": 5}
    assert payload["yield"] is None
    assert payload["generated_at"]


def test_engine_sections_null_without_engine(monkeypatch):
    monkeypatch.setattr(wr, "_funding_section", lambda: None)
    monkeypatch.setattr(wr, "_arb_section", lambda: None)
    called = []
    monkeypatch.setattr(wr, "_parity_section",
                        lambda engine: called.append("parity"))
    monkeypatch.setattr(wr, "_yield_section",
                        lambda engine: called.append("yield"))
    payload = wr.build_reports_payload(engine=None)
    assert payload["parity"] is None and payload["yield"] is None
    assert called == []                      # engine-needing builders not run


def test_parity_section_keeps_headline_keys_only(monkeypatch, tmp_path):
    trades_file = tmp_path / "closed.jsonl"
    trades_file.write_text("")
    engine = type("E", (), {})()
    engine.live_executor = type("X", (), {"_closed_trades_file": str(trades_file)})()
    monkeypatch.setattr("bot.backtest.parity.load_closed_trades",
                        lambda p: [{"x": 1}])
    monkeypatch.setattr(
        "bot.backtest.parity.parity_summary",
        lambda t, c: {"trades": 3, "win_rate": 0.66, "net_pnl": 1.2, "pf": 2.0,
                      "total_fees": 0.1, "realized_fee_rate": 0.001,
                      "modeled_fee_rate": 0.002, "fee_vs_model": 0.5,
                      "inferred_fills": 1, "excluded_non_fills": 2,
                      "by_signal_type": {"huge": "bucket"},
                      "by_setup": {}, "by_exit_reason": {}})
    section = wr._parity_section(engine)
    assert section["trades"] == 3 and section["fee_vs_model"] == 0.5
    assert "by_signal_type" not in section   # buckets stay in Telegram


# ── pull_and_apply_stance ─────────────────────────────────────────────

class _TierStore:
    def __init__(self, tier):
        self._tier = tier

    def get_tier(self, tg):
        return self._tier


def _wire(monkeypatch, pending_row, tier):
    """Stub the sync channel; return the list of ack payloads sent."""
    sent = []

    def fake_request(path, body=None):
        if path.endswith("/pending"):
            return {"pending": pending_row}
        sent.append(body)
        return {"ok": True}

    monkeypatch.setattr(ctl, "_request", fake_request)
    monkeypatch.setattr(ctl, "SYNC_SECRET", "s" * 48)
    return sent, _TierStore(tier)


def test_admin_stance_applies_and_acks(monkeypatch):
    sent, store = _wire(monkeypatch,
                        {"mode": "defensive", "telegram_id": "111"}, "admin")
    before = RUNTIME.strategy_mode
    try:
        assert ctl.pull_and_apply_stance(store=store) is True
        assert RUNTIME.strategy_mode == "defensive"
        assert sent and sent[0]["applied"] is True
    finally:
        RUNTIME.strategy_mode = before


def test_non_admin_stance_rejected_but_acked(monkeypatch):
    sent, store = _wire(monkeypatch,
                        {"mode": "aggressive", "telegram_id": "222"}, "pro")
    before = RUNTIME.strategy_mode
    try:
        assert ctl.pull_and_apply_stance(store=store) is False
        assert RUNTIME.strategy_mode == before          # nothing applied
        assert sent and sent[0]["applied"] is False     # row still cleared
    finally:
        RUNTIME.strategy_mode = before


def test_invalid_mode_rejected_but_acked(monkeypatch):
    sent, store = _wire(monkeypatch,
                        {"mode": "yolo", "telegram_id": "111"}, "admin")
    before = RUNTIME.strategy_mode
    try:
        assert ctl.pull_and_apply_stance(store=store) is False
        assert RUNTIME.strategy_mode == before
        assert sent and sent[0]["applied"] is False
    finally:
        RUNTIME.strategy_mode = before


def test_no_pending_row_is_a_noop(monkeypatch):
    sent, store = _wire(monkeypatch, None, "admin")
    assert ctl.pull_and_apply_stance(store=store) is False
    assert sent == []                                   # no ack traffic

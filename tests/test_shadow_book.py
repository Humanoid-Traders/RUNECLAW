"""
Shadow book — counterfactual ledger of gate-rejected trades.

Covers: record-time validation and geometry, marketable-at-record fills,
limit-touch fill semantics with the 4h window, pessimistic SL-first exits,
TP R math, 7-day expiry at mark, the gate_report sign convention
(net_r > 0 = the gate blocked winners), persistence round trips,
corrupt-state fail-open, cap pruning — and source pins on the engine /
scanner / telegram wiring so the recording sites can't silently vanish.
"""

import inspect
import json
from types import SimpleNamespace

from bot.core.shadow_book import (
    FILL_WINDOW_SEC,
    TRADE_HORIZON_SEC,
    ShadowBook,
    _base,
)
from bot.utils.models import Direction, TradeIdea

T0 = 1_750_000_000.0  # fixed epoch base — no real-clock dependence


def _idea(direction=Direction.LONG, entry=100.0, sl=95.0, tp=110.0,
          asset="BTC/USDT:USDT", idea_id="TI-sb"):
    return TradeIdea(
        id=idea_id, asset=asset, direction=direction,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        confidence=0.8, risk_reward_ratio=2.0, reasoning="shadow test",
    )


def _book(tmp_path):
    return ShadowBook(state_file=str(tmp_path / "sb.json"))


def _tick(px):
    return {"BTC/USDT:USDT": {"last": px}}


# ── recording ─────────────────────────────────────────────────────────

class TestRecord:
    def test_records_pending_trade_with_primary_gate(self, tmp_path):
        sb = _book(tmp_path)
        tr = sb.record_rejection(_idea(), ["MAX_POSITIONS", "CORRELATION"],
                                 "too many positions", now_ts=T0)
        assert tr is not None
        assert tr["status"] == "pending"
        assert tr["gate"] == "MAX_POSITIONS"          # first failed gate is charged
        assert tr["gates"] == ["MAX_POSITIONS", "CORRELATION"]
        assert tr["direction"] == "LONG"

    def test_degenerate_levels_are_skipped(self, tmp_path):
        # TradeIdea's own validators forbid inverted levels, but ideas from
        # other sources (adoption, manual) may not be pydantic — the book
        # must validate geometry itself. Use raw namespaces to prove it.
        sb = _book(tmp_path)
        def raw(**kw):
            base = dict(id="x", asset="BTC/USDT:USDT",
                        direction=Direction.LONG, entry_price=100.0,
                        stop_loss=95.0, take_profit=110.0, strategy_type="")
            base.update(kw)
            return SimpleNamespace(**base)
        # missing stop, inverted long geometry, inverted short geometry
        assert sb.record_rejection(raw(stop_loss=0), ["G"], "x", now_ts=T0) is None
        assert sb.record_rejection(raw(stop_loss=105.0), ["G"], "x", now_ts=T0) is None
        assert sb.record_rejection(
            raw(direction=Direction.SHORT), ["G"], "x", now_ts=T0) is None
        assert sb.counts() == {}

    def test_marketable_at_record_fills_immediately(self, tmp_path):
        sb = _book(tmp_path)
        # LONG limit at 100 with market already at/below 100 → instant fill
        tr = sb.record_rejection(_idea(), ["G"], "x", ref_price=99.5, now_ts=T0)
        assert tr["status"] == "open"
        assert tr["fill_ts"] == T0

    def test_empty_gate_list_gets_placeholder(self, tmp_path):
        sb = _book(tmp_path)
        tr = sb.record_rejection(_idea(), [], "x", now_ts=T0)
        assert tr["gate"] == "(unspecified)"

    def test_record_never_raises_on_garbage_idea(self, tmp_path):
        sb = _book(tmp_path)
        assert sb.record_rejection(object(), ["G"], "x", now_ts=T0) is None


# ── fills ─────────────────────────────────────────────────────────────

class TestFills:
    def test_pending_fills_on_touch(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_rejection(_idea(), ["G"], "x", ref_price=101.0, now_ts=T0)
        assert sb.counts() == {"pending": 1}
        # price stays above entry — still pending
        sb.update(_tick(100.5), now_ts=T0 + 60)
        assert sb.counts() == {"pending": 1}
        # touches the limit → open
        assert sb.update(_tick(100.0), now_ts=T0 + 120) == 1
        assert sb.counts() == {"open": 1}

    def test_short_pending_fills_when_price_rises_to_entry(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_rejection(
            _idea(direction=Direction.SHORT, entry=100.0, sl=105.0, tp=90.0),
            ["G"], "x", ref_price=99.0, now_ts=T0)
        sb.update(_tick(100.2), now_ts=T0 + 60)
        assert sb.counts() == {"open": 1}

    def test_untouched_entry_becomes_never_filled_after_window(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_rejection(_idea(), ["G"], "x", ref_price=101.0, now_ts=T0)
        sb.update(_tick(101.0), now_ts=T0 + FILL_WINDOW_SEC + 1)
        assert sb.counts() == {"never_filled": 1}
        # never_filled is excluded from the scoreboard
        assert sb.gate_report() == {}


# ── exits ─────────────────────────────────────────────────────────────

class TestExits:
    def _open_long(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_rejection(_idea(), ["G"], "x", ref_price=99.5, now_ts=T0)
        return sb

    def test_stop_hit_is_minus_one_r(self, tmp_path):
        sb = self._open_long(tmp_path)
        sb.update(_tick(94.0), now_ts=T0 + 60)
        (tr,) = [t for t in sb._trades if t["status"] == "closed"]
        assert tr["outcome"] == "sl"
        assert tr["r"] == -1.0
        assert tr["exit_price"] == 95.0   # booked at the stop, not the tick

    def test_tp_r_is_reward_over_risk(self, tmp_path):
        sb = self._open_long(tmp_path)
        sb.update(_tick(111.0), now_ts=T0 + 60)
        (tr,) = [t for t in sb._trades if t["status"] == "closed"]
        assert tr["outcome"] == "tp"
        assert tr["r"] == 2.0             # (110-100)/(100-95)

    def test_tick_through_both_levels_takes_stop_first(self, tmp_path):
        # Pessimistic tie-break: a tick that satisfies both books the loss.
        sb = _book(tmp_path)
        sb.record_rejection(
            _idea(direction=Direction.SHORT, entry=100.0, sl=101.0, tp=99.5),
            ["G"], "x", ref_price=100.5, now_ts=T0)
        sb.update(_tick(102.0), now_ts=T0 + 60)  # above SL for a short
        (tr,) = [t for t in sb._trades if t["status"] == "closed"]
        assert tr["outcome"] == "sl" and tr["r"] == -1.0

    def test_horizon_expiry_closes_at_mark_signed(self, tmp_path):
        sb = self._open_long(tmp_path)
        # drift to 102 without touching either level, then age past 7d
        sb.update(_tick(102.0), now_ts=T0 + TRADE_HORIZON_SEC + 61)
        (tr,) = [t for t in sb._trades if t["status"] == "closed"]
        assert tr["outcome"] == "expired"
        assert tr["r"] == 0.4             # (102-100)/(100-95)

    def test_short_expiry_r_is_signed_correctly(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_rejection(
            _idea(direction=Direction.SHORT, entry=100.0, sl=105.0, tp=90.0),
            ["G"], "x", ref_price=100.5, now_ts=T0)
        sb.update(_tick(102.0), now_ts=T0 + TRADE_HORIZON_SEC + 61)
        (tr,) = [t for t in sb._trades if t["status"] == "closed"]
        assert tr["r"] == -0.4            # short, price rose → negative


# ── scoreboard ────────────────────────────────────────────────────────

class TestGateReport:
    def test_positive_net_r_means_gate_blocked_winners(self, tmp_path):
        sb = _book(tmp_path)
        # EDGE_EATER blocks a trade that goes on to hit TP (+2R)
        sb.record_rejection(_idea(idea_id="a"), ["EDGE_EATER"], "x",
                            ref_price=99.5, now_ts=T0)
        # SAVIOR blocks two trades that go on to stop out (−1R each)
        sb.record_rejection(_idea(idea_id="b", asset="ETH/USDT:USDT"),
                            ["SAVIOR"], "x", ref_price=99.5, now_ts=T0)
        sb.record_rejection(_idea(idea_id="c", asset="SOL/USDT:USDT"),
                            ["SAVIOR"], "x", ref_price=99.5, now_ts=T0)
        sb.update({"BTC/USDT:USDT": {"last": 111.0},
                   "ETH/USDT:USDT": {"last": 94.0},
                   "SOL/USDT:USDT": {"last": 94.0}}, now_ts=T0 + 60)
        rep = sb.gate_report()
        assert rep["EDGE_EATER"]["net_r"] == 2.0
        assert rep["EDGE_EATER"]["wins"] == 1
        assert rep["SAVIOR"]["net_r"] == -2.0
        assert rep["SAVIOR"]["losses"] == 2
        assert rep["SAVIOR"]["avg_r"] == -1.0
        # worst offender (most positive net_r) sorts first
        assert list(rep)[0] == "EDGE_EATER"

    def test_render_report_empty_and_populated(self, tmp_path):
        sb = _book(tmp_path)
        assert "No closed shadow trades yet" in sb.render_report()
        sb.record_rejection(_idea(), ["MAX_POSITIONS"], "x",
                            ref_price=99.5, now_ts=T0)
        sb.update(_tick(111.0), now_ts=T0 + 60)
        out = sb.render_report()
        assert "MAX_POSITIONS" in out and "net +2.0R" in out


# ── persistence ───────────────────────────────────────────────────────

class TestPersistence:
    def test_round_trip_across_instances(self, tmp_path):
        path = str(tmp_path / "sb.json")
        sb1 = ShadowBook(state_file=path)
        sb1.record_rejection(_idea(), ["G"], "x", ref_price=99.5, now_ts=T0)
        sb2 = ShadowBook(state_file=path)
        sb2.update(_tick(111.0), now_ts=T0 + 60)
        assert sb2.gate_report()["G"]["net_r"] == 2.0

    def test_corrupt_state_starts_fresh(self, tmp_path):
        path = tmp_path / "sb.json"
        path.write_text("{not json", encoding="utf-8")
        sb = ShadowBook(state_file=str(path))
        assert sb.counts() == {}
        tr = sb.record_rejection(_idea(), ["G"], "x", now_ts=T0)
        assert tr is not None  # and the write repairs the file
        assert json.loads(path.read_text())["trades"]

    def test_closed_history_is_capped(self, tmp_path):
        path = str(tmp_path / "sb.json")
        sb = ShadowBook(state_file=path)
        sb._loaded = True
        sb._trades = [
            {"id": f"SB-{i}", "status": "closed", "r": 1.0, "gate": "G",
             "symbol": "BTC/USDT:USDT", "direction": "LONG",
             "entry": 100.0, "sl": 95.0, "tp": 110.0}
            for i in range(2100)
        ]
        sb._save()
        assert len(json.loads(open(path).read())["trades"]) == 2000


# ── helpers ───────────────────────────────────────────────────────────

def test_base_symbol_matching():
    assert _base("BTC/USDT:USDT") == "BTC"
    assert _base("btc/usdt") == "BTC"
    assert _base("BTCUSDT") == "BTCUSDT"   # no mid-string strip, by design


# ── wiring pins: the sites that feed/read the ledger must stay wired ──

class TestWiring:
    def test_config_flag_exists(self):
        from bot.config import CONFIG
        assert isinstance(CONFIG.shadow_book_enabled, bool)

    def test_engine_rejection_site_records(self):
        from bot.core import engine as eng_mod
        src = inspect.getsource(eng_mod)
        assert "SHADOW_BOOK.record_rejection" in src
        assert "review_rejection" in src  # sits at the risk-REJECTED path

    def test_scanner_tick_updates(self):
        from bot.core import market_scanner as ms_mod
        src = inspect.getsource(ms_mod)
        assert "SHADOW_BOOK.update(futures_result)" in src

    def test_telegram_registers_shadow_command(self):
        from bot.skills import telegram_handler as th_mod
        src = inspect.getsource(th_mod)
        assert '("shadow", self._cmd_shadow)' in src

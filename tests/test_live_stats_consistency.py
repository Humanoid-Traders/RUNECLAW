"""One source of truth for live win-rate + one viewer executor.

Reported: the /start card showed win rate 38% while the Portfolio card showed
52% for the same moment. Cause: the two cards (a) computed win rate with
duplicated, drifted inline logic, and (b) read from two DIFFERENT executors
(caller-routed vs hard-coded operator). This locks both down: identical
computation via bot.skills.live_stats, and both cards route through the same
engine.viewer_executor.
"""

from types import SimpleNamespace

from bot.skills.live_stats import live_win_stats, real_closed_trades


def _t(trade_id="TI-1", pnl=1.0, reason="tp"):
    return SimpleNamespace(trade_id=trade_id, pnl_usd=pnl, close_reason=reason)


class TestLiveWinStats:
    def test_win_rate_counts_only_positive_pnl(self):
        trades = [_t(pnl=5), _t(pnl=-2), _t(pnl=3), _t(pnl=0)]  # 2 wins / 4
        s = live_win_stats(trades)
        assert s["total"] == 4 and s["wins"] == 2 and s["win_rate"] == 50.0

    def test_breakeven_is_not_a_win(self):
        assert live_win_stats([_t(pnl=0.0)])["wins"] == 0

    def test_excludes_adopted_and_injected_artifacts(self):
        trades = [_t(trade_id="TI-adopted-x", pnl=9), _t(trade_id="TI-injected-y", pnl=9),
                  _t(trade_id="TI-real", pnl=9)]
        s = live_win_stats(trades)
        assert s["total"] == 1  # only the real one

    def test_excludes_never_filled_orders(self):
        trades = [_t(pnl=0, reason="canceled"), _t(pnl=0, reason="expired"),
                  _t(pnl=0, reason="price_drift"), _t(pnl=4, reason="tp")]
        s = live_win_stats(trades)
        assert s["total"] == 1 and s["win_rate"] == 100.0

    def test_empty_is_zero_not_crash(self):
        s = live_win_stats([])
        assert s["total"] == 0 and s["win_rate"] == 0.0

    def test_two_cards_same_input_same_number(self):
        """The core guarantee: identical input → identical win rate, so /start
        and Portfolio can never diverge when reading the same account."""
        trades = [_t(pnl=1), _t(pnl=-1), _t(pnl=2), _t(trade_id="TI-adopted-z", pnl=1),
                  _t(pnl=0, reason="rejected")]
        # 3 real trades (adopted + rejected excluded), 2 wins → 66.7%.
        a = live_win_stats(trades)["win_rate"]
        b = live_win_stats(trades)["win_rate"]
        assert a == b and round(a, 1) == 66.7

    def test_real_closed_trades_filter_shape(self):
        assert len(real_closed_trades([_t(), _t(trade_id="TI-injected-1")])) == 1


class _FakeEngine:
    """Minimal stand-in that borrows the REAL viewer_executor method."""
    from bot.core.engine import RuneClawEngine as _RC
    viewer_executor = _RC.viewer_executor

    def __init__(self, executor_for, operator_ids=("777",), live_exec="OP"):
        self.live_executor = live_exec
        self._ef = executor_for
        self._ops = operator_ids

    def _executor_for(self, uid=""):
        return self._ef

    def _is_operator_user(self, uid):
        return str(uid) in self._ops


class TestViewerExecutor:
    """engine.viewer_executor is the shared account resolver both cards use."""

    def _set_per_user(self, monkeypatch, value):
        # CONFIG is a frozen dataclass; swap the engine module's CONFIG ref for a
        # lightweight stand-in exposing just the flag viewer_executor reads.
        from bot.core import engine as eng_mod
        monkeypatch.setattr(eng_mod, "CONFIG",
                            SimpleNamespace(per_user_live_enabled=value))

    def test_per_user_off_always_operator(self, monkeypatch):
        self._set_per_user(monkeypatch, False)
        assert _FakeEngine(executor_for="OP").viewer_executor("anyone") == "OP"

    def test_per_user_on_own_account_returned(self, monkeypatch):
        self._set_per_user(monkeypatch, True)
        assert _FakeEngine(executor_for="USER_EX").viewer_executor("123") == "USER_EX"

    def test_per_user_on_nonoperator_fallback_is_blocked(self, monkeypatch):
        """A non-operator whose resolution only falls back to the operator
        executor must see None — never the operator's book."""
        self._set_per_user(monkeypatch, True)
        e = _FakeEngine(executor_for="OP", operator_ids=("777",), live_exec="OP")
        assert e.viewer_executor("123") is None       # not operator → blocked
        assert e.viewer_executor("777") == "OP"        # operator → allowed

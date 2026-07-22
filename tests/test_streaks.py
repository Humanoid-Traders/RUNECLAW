"""TG-2: win/loss streak surfaced next to the win rate.

current_streak walks back from the newest real closed trade; streak_badge
renders a compact chip only for a run of 2+.
"""

from types import SimpleNamespace

from bot.skills.live_stats import current_streak, streak_badge, live_win_stats


def _t(pnl, trade_id="T1", close_reason="tp"):
    return SimpleNamespace(pnl_usd=pnl, trade_id=trade_id, close_reason=close_reason)


class TestCurrentStreak:
    def test_empty(self):
        assert current_streak([]) == {"kind": None, "count": 0}

    def test_win_run_counts_back_from_newest(self):
        # oldest → newest: L, W, W, W  → 3-win streak
        s = current_streak([_t(-5), _t(10), _t(3), _t(7)])
        assert s == {"kind": "win", "count": 3}

    def test_loss_run(self):
        s = current_streak([_t(9), _t(-2), _t(-4)])
        assert s == {"kind": "loss", "count": 2}

    def test_single_trade_is_a_run_of_one(self):
        assert current_streak([_t(10)]) == {"kind": "win", "count": 1}

    def test_breakeven_newest_breaks_the_streak(self):
        s = current_streak([_t(10), _t(10), _t(0)])
        assert s == {"kind": None, "count": 0}

    def test_non_fills_and_artifacts_are_excluded(self):
        # A canceled non-fill and an adopted artifact must not interrupt or
        # pad the streak — they are filtered out before counting.
        trades = [
            _t(10),
            _t(0, close_reason="canceled"),          # non-fill, dropped
            _t(5, trade_id="TI-adopted-1"),          # artifact, dropped
            _t(8),
        ]
        assert current_streak(trades) == {"kind": "win", "count": 2}


class TestStreakBadge:
    def test_no_badge_below_two(self):
        assert streak_badge({"kind": "win", "count": 1}) == ""
        assert streak_badge({"kind": None, "count": 0}) == ""
        assert streak_badge(None) == ""

    def test_win_and_loss_chips(self):
        assert streak_badge({"kind": "win", "count": 3}) == "🔥 3W"
        assert streak_badge({"kind": "loss", "count": 4}) == "🧊 4L"


class TestLiveWinStatsIncludesStreak:
    def test_streak_field_present(self):
        stats = live_win_stats([_t(10), _t(5)])
        assert stats["streak"] == {"kind": "win", "count": 2}
        assert stats["total"] == 2 and stats["wins"] == 2

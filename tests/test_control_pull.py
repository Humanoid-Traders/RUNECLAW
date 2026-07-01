"""
Bot-side pull of website-queued live-control changes (Stage 3a of web wallet).

Users set live on/off, a per-trade margin cap, and pause-to-paper on the website;
the bot pulls and applies them via the UserStore. These cover process_pending_controls:
each control maps to the right UserStore mutator, NULL means unchanged, the applied
state + allowlist flag are reported back, and enabling live still depends on the
operator allowlist (surfaced separately).
"""

import bot.utils.control_pull as ctl


class _FakeStore:
    def __init__(self, allowlisted=False):
        self.live = False
        self.paused = False
        self.margin = None
        self.calls = []
        self._allowlisted = allowlisted

    def set_live_trading(self, tg, enabled):
        self.calls.append(("live", enabled)); self.live = enabled; return True

    def set_sim_opt_in(self, tg, enabled):
        self.calls.append(("paused", enabled)); self.paused = enabled; return True

    def set_max_margin(self, tg, usd):
        self.calls.append(("margin", usd)); self.margin = usd; return True

    # getters used to build the applied-state ack
    def can_trade_live(self, tg):
        return self.live

    def sim_opt_in(self, tg):
        return self.paused

    def max_margin(self, tg):
        return self.margin


def _row(uid=1, tg="999", live=None, margin=None, paused=None):
    return {"user_id": uid, "telegram_id": tg, "live_enabled": live,
            "max_margin": margin, "paused": paused}


class TestProcessControls:
    def test_enable_live(self):
        s = _FakeStore()
        changed = []
        acks = ctl.process_pending_controls([_row(live=1)], s, on_change=changed.append)
        assert ("live", True) in s.calls
        assert acks[0]["live_enabled"] is True and acks[0]["ok"] is True
        assert changed == ["999"]

    def test_disable_live(self):
        s = _FakeStore(); s.live = True
        acks = ctl.process_pending_controls([_row(live=0)], s)
        assert ("live", False) in s.calls
        assert acks[0]["live_enabled"] is False

    def test_margin_cap_set_and_cleared(self):
        s = _FakeStore()
        ctl.process_pending_controls([_row(margin=250)], s)
        assert ("margin", 250.0) in s.calls and s.margin == 250.0
        # 0 clears the cap (-> None)
        ctl.process_pending_controls([_row(margin=0)], s)
        assert ("margin", None) in s.calls

    def test_pause(self):
        s = _FakeStore()
        acks = ctl.process_pending_controls([_row(paused=1)], s)
        assert ("paused", True) in s.calls
        assert acks[0]["paused"] is True

    def test_null_fields_left_unchanged(self):
        s = _FakeStore()
        ctl.process_pending_controls([_row()], s)  # all None
        assert s.calls == []  # nothing mutated

    def test_allowlist_reported_separately(self):
        s = _FakeStore()
        # User flips live on but is NOT allowlisted -> live_enabled True (flag),
        # allowlisted False (operator hasn't pre-approved). UI shows "pending".
        acks = ctl.process_pending_controls([_row(live=1)], s,
                                            allowlist_check=lambda tg: False)
        assert acks[0]["live_enabled"] is True
        assert acks[0]["allowlisted"] is False
        acks2 = ctl.process_pending_controls([_row(live=1)], s,
                                             allowlist_check=lambda tg: True)
        assert acks2[0]["allowlisted"] is True

    def test_missing_telegram_id_skipped(self):
        s = _FakeStore()
        assert ctl.process_pending_controls([_row(tg="", live=1)], s) == []
        assert s.calls == []

    def test_combined_change(self):
        s = _FakeStore()
        acks = ctl.process_pending_controls([_row(live=1, margin=100, paused=1)], s)
        kinds = [c[0] for c in s.calls]
        assert set(kinds) == {"live", "margin", "paused"}
        a = acks[0]
        assert a["live_enabled"] and a["paused"] and a["max_margin"] == 100.0


class TestPullGate:
    def test_no_store_is_noop(self):
        assert ctl.pull_and_apply_controls(store=None) == 0

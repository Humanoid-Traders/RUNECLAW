"""
Opt-in 'playbook' trail rule for update_trailing_stop (config-gated, default OFF).

Trails the SL exactly playbook_atr_mult·ATR behind the MARK, tighten-only, with
NO 1R activation gate — matching the external Playbook geometry
(ratchet fires when mark ± atr_mult·ATR crosses the SL). The default
"multistage" rule is unchanged.
"""

import pytest

from bot.utils.trailing import make_trailing_state, update_trailing_stop


def _state(entry, direction, atr, init_risk):
    return make_trailing_state(entry, direction, init_risk, atr)


class TestPlaybookTrail:
    def test_short_ratchets_when_mark_2atr_below_sl(self):
        st = _state(0.4413, "SHORT", atr=0.0078, init_risk=0.0108)
        # mark 0.4300 → candidate 0.4300 + 2·0.0078 = 0.4456 < SL 0.4521 → ratchet.
        sl, active = update_trailing_stop(st, 0.4300, 0.4521, "SHORT",
                                          rule="playbook", playbook_atr_mult=2.0)
        assert active is True
        assert sl == pytest.approx(0.4300 + 2 * 0.0078)
        assert sl < 0.4521

    def test_short_no_ratchet_when_geometry_not_demanded(self):
        st = _state(0.4413, "SHORT", atr=0.0078, init_risk=0.0108)
        # mark 0.4385 → candidate 0.4541 > SL 0.4521 → SL frozen (matches the
        # readout's "GEOMETRY NOT DEMANDED" for this exact state).
        sl, active = update_trailing_stop(st, 0.4385, 0.4521, "SHORT", rule="playbook")
        assert active is False
        assert sl == pytest.approx(0.4521)

    def test_long_ratchets_up(self):
        st = _state(100.0, "LONG", atr=1.0, init_risk=2.0)
        # mark 105 → candidate 103 > SL 98 → ratchet up to 103.
        sl, active = update_trailing_stop(st, 105.0, 98.0, "LONG",
                                          rule="playbook", playbook_atr_mult=2.0)
        assert active is True
        assert sl == pytest.approx(103.0)

    def test_tighten_only_never_widens(self):
        st = _state(100.0, "LONG", atr=1.0, init_risk=2.0)
        sl, _ = update_trailing_stop(st, 105.0, 98.0, "LONG", rule="playbook")  # → 103
        # Pullback to 104 → candidate 102 < current SL 103 → must NOT widen.
        sl2, _ = update_trailing_stop(st, 104.0, sl, "LONG", rule="playbook")
        assert sl2 == pytest.approx(103.0)

    def test_short_tighten_only(self):
        st = _state(100.0, "SHORT", atr=1.0, init_risk=2.0)
        sl, _ = update_trailing_stop(st, 95.0, 102.0, "SHORT", rule="playbook")  # → 97
        # Bounce to 96 → candidate 98 > current SL 97 → no widen.
        sl2, _ = update_trailing_stop(st, 96.0, sl, "SHORT", rule="playbook")
        assert sl2 == pytest.approx(97.0)

    def test_no_atr_no_ratchet(self):
        st = _state(100.0, "LONG", atr=0.0, init_risk=2.0)
        sl, active = update_trailing_stop(st, 105.0, 98.0, "LONG", rule="playbook")
        assert sl == pytest.approx(98.0)
        assert active is False

    def test_invalid_price_unchanged(self):
        st = _state(100.0, "LONG", atr=1.0, init_risk=2.0)
        sl, active = update_trailing_stop(st, 0.0, 98.0, "LONG", rule="playbook")
        assert sl == pytest.approx(98.0)


class TestDefaultRuleUnchanged:
    def test_multistage_still_default_and_active_at_1r(self):
        # No rule arg → multistage; +1.5R activates stage 1 (existing behaviour).
        st = _state(100.0, "LONG", atr=1.0, init_risk=2.0)
        sl, active = update_trailing_stop(st, 103.0, 98.0, "LONG")
        assert active is True

    def test_config_default_is_multistage(self):
        from bot.config import CONFIG
        assert CONFIG.trailing.trail_rule == "multistage"

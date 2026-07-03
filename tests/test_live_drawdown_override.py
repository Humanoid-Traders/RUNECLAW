"""Runtime-adjustable LIVE max-drawdown backstop.

An admin can temporarily loosen (or tighten) the live drawdown cap at runtime
without a redeploy, so the operator can keep testing live after the account has
drawn down past the default cap. The override is BOUNDED (never disables the
breaker), only bites on live, and reverts cleanly. Paper/backtest are unaffected.
"""

from bot.config import RUNTIME, CONFIG


class TestRuntimeSetterBounds:
    def teardown_method(self):
        RUNTIME.clear_live_drawdown_override()

    def test_default_is_none(self):
        RUNTIME.clear_live_drawdown_override()
        assert RUNTIME.live_drawdown_override_pct is None

    def test_set_within_band(self):
        RUNTIME.live_drawdown_override_pct = 15.0
        assert RUNTIME.live_drawdown_override_pct == 15.0

    def test_clamped_to_ceiling(self):
        RUNTIME.live_drawdown_override_pct = 999.0
        assert RUNTIME.live_drawdown_override_pct == RUNTIME.LIVE_DRAWDOWN_OVERRIDE_MAX

    def test_clamped_to_floor(self):
        RUNTIME.live_drawdown_override_pct = 0.0
        assert RUNTIME.live_drawdown_override_pct == RUNTIME.LIVE_DRAWDOWN_OVERRIDE_MIN

    def test_ceiling_never_disables_breaker(self):
        # The whole point: no operator command can push the cap so high the
        # breaker is effectively off.
        assert RUNTIME.LIVE_DRAWDOWN_OVERRIDE_MAX <= 30.0

    def test_clear_reverts(self):
        RUNTIME.live_drawdown_override_pct = 20.0
        RUNTIME.clear_live_drawdown_override()
        assert RUNTIME.live_drawdown_override_pct is None

    def test_set_none_reverts(self):
        RUNTIME.live_drawdown_override_pct = 20.0
        RUNTIME.live_drawdown_override_pct = None
        assert RUNTIME.live_drawdown_override_pct is None


class TestEffectiveLimitWiring:
    """_effective_max_drawdown_pct consults the override only under live
    hardening; paper is byte-identical regardless of the override."""

    def teardown_method(self):
        RUNTIME.clear_live_drawdown_override()

    def _engine(self):
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        return RiskEngine(PortfolioTracker(initial_balance=10_000.0))

    def test_paper_ignores_override(self, monkeypatch):
        eng = self._engine()
        monkeypatch.setattr(eng, "_live_hardening", lambda: False)
        RUNTIME.live_drawdown_override_pct = 25.0
        assert eng._effective_max_drawdown_pct() == CONFIG.risk.max_drawdown_pct

    def test_live_uses_config_when_no_override(self, monkeypatch):
        eng = self._engine()
        monkeypatch.setattr(eng, "_live_hardening", lambda: True)
        RUNTIME.clear_live_drawdown_override()
        assert eng._effective_max_drawdown_pct() == CONFIG.risk.live_max_drawdown_pct

    def test_live_uses_override_when_set(self, monkeypatch):
        eng = self._engine()
        monkeypatch.setattr(eng, "_live_hardening", lambda: True)
        RUNTIME.live_drawdown_override_pct = 18.0
        assert eng._effective_max_drawdown_pct() == 18.0

    def test_override_still_bounded_via_effective(self, monkeypatch):
        eng = self._engine()
        monkeypatch.setattr(eng, "_live_hardening", lambda: True)
        RUNTIME.live_drawdown_override_pct = 500.0
        # Even a huge request is clamped by the setter, so the effective limit
        # can never exceed the hard ceiling.
        assert eng._effective_max_drawdown_pct() == RUNTIME.LIVE_DRAWDOWN_OVERRIDE_MAX


class TestPersistenceAcrossRestart:
    """The admin override must survive a restart: it is serialized into the
    risk state file and reapplied to RUNTIME on load (so it doesn't snap back
    to the default when the bot is redeployed mid-testing)."""

    def teardown_method(self):
        RUNTIME.clear_live_drawdown_override()

    def _engine(self, state_file):
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                          state_file=str(state_file))

    def test_override_round_trips_through_state_file(self, tmp_path):
        sf = tmp_path / "risk_state.json"
        eng = self._engine(sf)
        RUNTIME.live_drawdown_override_pct = 16.0
        eng._save_state()
        # Simulate a restart: clear in-memory, build a fresh engine on the same
        # file, and confirm the override is reapplied.
        RUNTIME.clear_live_drawdown_override()
        assert RUNTIME.live_drawdown_override_pct is None
        eng2 = self._engine(sf)
        eng2._load_state()
        assert RUNTIME.live_drawdown_override_pct == 16.0

    def test_cleared_override_round_trips_as_none(self, tmp_path):
        sf = tmp_path / "risk_state.json"
        eng = self._engine(sf)
        RUNTIME.live_drawdown_override_pct = 12.0
        eng._save_state()
        RUNTIME.clear_live_drawdown_override()
        eng._save_state()  # persist the cleared state
        RUNTIME.live_drawdown_override_pct = 25.0  # dirty in-memory
        self._engine(sf)._load_state()
        assert RUNTIME.live_drawdown_override_pct is None

    def test_persisted_override_reclamped_on_load(self, tmp_path):
        import json
        sf = tmp_path / "risk_state.json"
        # A hand-tampered file with an out-of-band value must be re-clamped on
        # load — persistence can never be a backdoor around the safe ceiling.
        sf.write_text(json.dumps({"circuit_open": False,
                                  "live_drawdown_override_pct": 999.0}))
        self._engine(sf)._load_state()
        assert RUNTIME.live_drawdown_override_pct == RUNTIME.LIVE_DRAWDOWN_OVERRIDE_MAX

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

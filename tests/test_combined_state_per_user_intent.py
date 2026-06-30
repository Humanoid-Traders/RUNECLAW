"""
Combined-state file is operator-only by design; per-user trackers persist
per-file (deep-audit medium #12).

RuneClawEngine._save_combined_state writes ONLY the operator's portfolio + risk
into combined_state.json. Per-user paper portfolios and per-user RiskEngines are
deliberately NOT folded in — each persists independently and atomically to its
own per-user file (data/portfolio_{user}.json, data/risk_state_{user}.json) and
is restored on startup. Folding them in would create a second source of truth for
the same account. These tests lock that intent:

  1. the combined file contains operator portfolio+risk only (no per-user keys);
  2. a per-user portfolio's balance/positions round-trip through its own file;
  3. a per-user RiskEngine's safety state round-trips through its own file.

A future change that tries to merge per-user state into combined_state.json (or
that breaks per-file persistence) trips test 1 (resp. 2/3), forcing a deliberate
decision rather than silent write-skew / data loss.
"""

import json

from bot.core.engine import RuneClawEngine
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea


def _idea(entry=100.0):
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=entry,
                     stop_loss=entry * 0.95, take_profit=entry * 1.1,
                     confidence=0.7, reasoning="test")


class _StubComponent:
    def __init__(self, payload):
        self._payload = payload

    def _export_state_dict(self):
        return dict(self._payload)


class TestCombinedFileIsOperatorOnly:
    def test_only_operator_portfolio_and_risk_written(self, tmp_path):
        combined = str(tmp_path / "combined_state.json")
        fake = type("F", (), {})()
        fake.portfolio = _StubComponent({"balance": 1000.0})
        fake.risk = _StubComponent({"circuit_open": False})
        fake._combined_state_file = combined

        # Call the real saver with a lightweight self.
        RuneClawEngine._save_combined_state(fake)

        with open(combined) as f:
            data = json.load(f)
        # Exactly the operator bundle — no per-user portfolios / risk engines.
        assert set(data.keys()) == {"version", "portfolio", "risk", "written_at"}
        assert data["portfolio"] == {"balance": 1000.0}
        assert data["risk"] == {"circuit_open": False}
        # Defensive: nothing user-scoped leaked into the keys.
        assert not any("user" in k.lower() for k in data.keys())


class TestPerUserPortfolioRoundTrips:
    def test_balance_and_position_survive_reload_via_own_file(self, tmp_path):
        path = str(tmp_path / "portfolio_u42.json")
        # First session: open a position, which auto-saves to the per-user file.
        pf = PortfolioTracker(initial_balance=5000.0, state_file=path)
        pf.open_position(_idea(), size_usd=500.0)
        assert len(pf._positions) == 1
        first_balance = pf.balance

        # Second session: a fresh tracker over the SAME file restores everything,
        # with no combined_state.json involved at all.
        restored = PortfolioTracker(initial_balance=None, state_file=path)
        assert len(restored._positions) == 1
        assert restored.balance == first_balance


class TestPerUserRiskRoundTrips:
    def test_breaker_state_survives_reload_via_own_file(self, tmp_path):
        path = str(tmp_path / "risk_state_u42.json")
        pf = PortfolioTracker(initial_balance=5000.0)
        risk = RiskEngine(pf, state_file=path)
        risk._consecutive_losses = 3
        risk._circuit_open = True
        risk._save_state_individual()

        restored = RiskEngine(PortfolioTracker(initial_balance=5000.0), state_file=path)
        assert restored._consecutive_losses == 3
        assert restored._circuit_open is True

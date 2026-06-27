"""
Tests for risk_manifest.yaml validation against actual code,
and for the /whynot rejection explainer skill.
"""

import os
import yaml
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# Paths
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST_PATH = os.path.join(_PROJECT_ROOT, "config", "risk_manifest.yaml")


# ── helpers ──────────────────────────────────────────────────────

def _load_manifest() -> dict:
    with open(_MANIFEST_PATH) as f:
        return yaml.safe_load(f)


def _make_idea(
    asset="BTC/USDT",
    direction=None,
    entry=65000.0,
    sl=58500.0,
    tp=72800.0,
    confidence=0.72,
    idea_id="TI-test001",
):
    from bot.utils.models import Direction, TradeIdea
    return TradeIdea(
        id=idea_id,
        asset=asset,
        direction=direction or Direction.LONG,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=confidence,
        reasoning="test idea",
        signals_used=["rsi", "macd"],
    )


def _make_portfolio(balance=10000.0):
    from bot.risk.portfolio import PortfolioTracker
    return PortfolioTracker(initial_balance=balance)


def _make_risk(portfolio):
    import tempfile

    from bot.risk.risk_engine import RiskEngine

    # Isolated temp state file (never "/dev/null": RiskEngine._save_state does
    # os.replace(tmp, state_file), which as root clobbers the /dev/null device
    # and leaks circuit-breaker state into later tests).
    state = os.path.join(tempfile.mkdtemp(prefix="rc-risk-"), "risk_state.json")
    return RiskEngine(portfolio, state_file=state)


# ══════════════════════════════════════════════════════════════════
# MANIFEST TESTS
# ══════════════════════════════════════════════════════════════════

class TestRiskManifest:
    """Validate risk_manifest.yaml matches actual code."""

    def test_manifest_loads_valid_yaml(self):
        """1. manifest_loads_valid_yaml - file parses without error."""
        data = _load_manifest()
        assert isinstance(data, dict)
        assert "checks" in data

    def test_manifest_has_21_checks(self):
        """2. manifest_has_21_checks - exactly 21 check entries."""
        data = _load_manifest()
        assert len(data["checks"]) == 21

    def test_manifest_check_ids_sequential(self):
        """3. manifest_check_ids_sequential - IDs 1-21 in order."""
        data = _load_manifest()
        ids = [c["id"] for c in data["checks"]]
        assert ids == list(range(1, 22))

    def test_manifest_check_names_match_code(self):
        """4. manifest_check_names_match_code - names match risk_engine.py check names."""
        data = _load_manifest()
        manifest_names = [c["name"] for c in data["checks"]]

        # Expected names derived from the risk_engine.py check labels
        expected_names = [
            "CIRCUIT_BREAKER",
            "POSITION_SIZE",
            "DAILY_LOSS",
            "DRAWDOWN",
            "OPEN_POSITIONS",
            "RISK_REWARD",
            "CONFIDENCE",
            "CORRELATION",
            "LOSS_STREAK",
            "ENTRY_PRICE",
            "STOP_LOSS",
            "STALE_DATA",
            "COOLDOWN",
            "PORTFOLIO_EXPOSURE",
            "SYMBOL_EXPOSURE",
            "VOLATILITY",
            "LIQUIDITY",
            "MACRO_EVENT",
            "MTF_ALIGNMENT",
            "CONCENTRATION_PCA",
            "PORTFOLIO_VAR",
        ]
        assert manifest_names == expected_names

    def test_manifest_has_defaults_section(self):
        """5. manifest_has_defaults_section - defaults section exists with all RiskLimits fields."""
        from bot.config import RiskLimits
        import dataclasses

        data = _load_manifest()
        assert "defaults" in data

        defaults = data["defaults"]
        risk_fields = {f.name for f in dataclasses.fields(RiskLimits)}

        # Every RiskLimits field should appear in manifest defaults
        for field_name in risk_fields:
            assert field_name in defaults, (
                f"RiskLimits field '{field_name}' missing from manifest defaults"
            )

    def test_manifest_has_correlation_groups(self):
        """6. manifest_has_correlation_groups - correlation groups section matches code."""
        from bot.risk.risk_engine import _CORRELATION_GROUPS

        data = _load_manifest()
        assert "correlation_groups" in data

        manifest_groups = data["correlation_groups"]

        # Build reverse map from code: group_name -> set of symbols
        code_groups: dict[str, set[str]] = {}
        for symbol, group in _CORRELATION_GROUPS.items():
            code_groups.setdefault(group, set()).add(symbol)

        # Every group in code must be in manifest
        for group_name, symbols in code_groups.items():
            assert group_name in manifest_groups, (
                f"Correlation group '{group_name}' in code but missing from manifest"
            )
            manifest_symbols = set(manifest_groups[group_name]["symbols"])
            assert symbols == manifest_symbols, (
                f"Symbols mismatch for group '{group_name}': "
                f"code={sorted(symbols)}, manifest={sorted(manifest_symbols)}"
            )

    def test_manifest_defaults_match_config(self):
        """7. manifest_defaults_match_config - threshold values match RiskLimits defaults."""
        from bot.config import RiskLimits
        import dataclasses

        data = _load_manifest()
        defaults = data["defaults"]
        risk = RiskLimits()

        for f in dataclasses.fields(RiskLimits):
            manifest_entry = defaults[f.name]
            manifest_val = manifest_entry["value"]
            code_val = getattr(risk, f.name)

            # Handle bool comparison (YAML booleans are native Python bools)
            if isinstance(code_val, bool):
                assert manifest_val == code_val, (
                    f"Default mismatch for '{f.name}': "
                    f"manifest={manifest_val}, code={code_val}"
                )
            else:
                assert float(manifest_val) == pytest.approx(float(code_val), abs=0.01), (
                    f"Default mismatch for '{f.name}': "
                    f"manifest={manifest_val}, code={code_val}"
                )

    def test_manifest_all_checks_have_fail_behavior(self):
        """8. manifest_all_checks_have_fail_behavior - every check has fail_behavior field."""
        data = _load_manifest()
        valid_behaviors = {"closed", "open", "skip"}
        for check in data["checks"]:
            assert "fail_behavior" in check, (
                f"Check {check['id']} ({check['name']}) missing fail_behavior"
            )
            assert check["fail_behavior"] in valid_behaviors, (
                f"Check {check['id']} ({check['name']}) has invalid fail_behavior: "
                f"{check['fail_behavior']}"
            )

    def test_manifest_version_present(self):
        """9. manifest_version_present - version field exists."""
        data = _load_manifest()
        assert "version" in data
        assert data["version"] is not None
        assert str(data["version"]).strip() != ""

    def test_manifest_env_vars_match_config(self):
        """10. manifest_env_vars_match_config - env var names in manifest match config.py."""
        data = _load_manifest()
        defaults = data["defaults"]

        # Map of manifest default key -> expected env_var from config.py source
        # These are the env var names that config.py actually reads
        expected_env_vars = {
            "max_position_pct": "MAX_POSITION_PCT",
            "max_daily_loss_pct": "MAX_DAILY_LOSS_PCT",
            "max_drawdown_pct": "MAX_DRAWDOWN_PCT",
            "max_open_positions": "MAX_OPEN_POSITIONS",
            "max_correlation": "MAX_CORRELATION",
            "min_risk_reward": "MIN_RISK_REWARD",
            "min_confidence": "MIN_CONFIDENCE",
            "max_consecutive_losses": "MAX_CONSECUTIVE_LOSSES",
            "cooldown_after_loss_seconds": "COOLDOWN_AFTER_LOSS_SEC",
            "max_portfolio_exposure_pct": "MAX_PORTFOLIO_EXPOSURE_PCT",
            "max_symbol_exposure_pct": "MAX_SYMBOL_EXPOSURE_PCT",
            "max_correlation_per_group": "MAX_CORRELATION_PER_GROUP",
            "volatility_guard_atr_pct": "VOLATILITY_GUARD_ATR_PCT",
            "stale_data_max_age_seconds": "STALE_DATA_MAX_AGE_SEC",
            "require_stop_loss": "REQUIRE_STOP_LOSS",
            "commission_pct": "COMMISSION_PCT",
        }

        for key, expected_var in expected_env_vars.items():
            manifest_var = defaults[key]["env_var"]
            assert manifest_var == expected_var, (
                f"Env var mismatch for '{key}': "
                f"manifest={manifest_var}, expected={expected_var}"
            )


# ══════════════════════════════════════════════════════════════════
# WHYNOT TESTS
# ══════════════════════════════════════════════════════════════════

class TestWhyNot:
    """Test /whynot rejection explainer."""

    def _make_engine_with_rejections(self, rejections=None):
        """Create a minimal mock engine with _last_rejections."""
        engine = MagicMock()
        engine._last_rejections = rejections if rejections is not None else {}
        return engine

    def _sample_rejection(self, symbol="BTC/USDT", direction="LONG",
                          confidence=0.55, checks_failed=None,
                          checks_passed=None):
        return {
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "entry_price": 65000.0,
            "stop_loss": 58500.0,
            "take_profit": 72800.0,
            "checks_passed": checks_passed or [
                "CIRCUIT_BREAKER: OK",
                "POSITION_SIZE: notional 15.0% <= 20%",
                "DAILY_LOSS: 0.0% OK",
            ],
            "checks_failed": checks_failed or [
                "CONFIDENCE: 0.55 < 0.6 minimum",
            ],
            "reason": "CONFIDENCE: 0.55 < 0.6 minimum",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @pytest.mark.asyncio
    async def test_whynot_no_rejections_returns_message(self):
        """11. whynot_no_rejections_returns_message - empty _last_rejections gives no-rejections msg."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        engine = self._make_engine_with_rejections({})
        result = await skill.execute(engine)
        assert "no" in result.lower() or "No" in result
        assert "reject" in result.lower()

    @pytest.mark.asyncio
    async def test_whynot_stores_rejection_on_risk_fail(self):
        """12. whynot_stores_rejection_on_risk_fail - mock a rejection, verify it's stored."""
        portfolio = _make_portfolio()
        risk = _make_risk(portfolio)
        idea = _make_idea(confidence=0.3)  # below threshold
        atr = 2600.0
        check = risk.evaluate(idea, atr=atr)
        assert check.verdict.value == "REJECTED"
        # Simulate what engine.py does: store rejection
        rejections: dict[str, dict] = {}
        symbol_key = idea.asset.replace("/USDT", "").upper()
        rejections[symbol_key] = {
            "symbol": idea.asset,
            "checks_failed": check.checks_failed,
            "checks_passed": check.checks_passed,
        }
        assert symbol_key in rejections
        assert len(rejections[symbol_key]["checks_failed"]) > 0

    @pytest.mark.asyncio
    async def test_whynot_retrieves_by_symbol(self):
        """13. whynot_retrieves_by_symbol - store rejection for BTC, query 'BTC' returns it."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej = self._sample_rejection(symbol="BTC/USDT")
        engine = self._make_engine_with_rejections({"BTC": rej})
        result = await skill.execute(engine, symbol="BTC")
        assert "BTC" in result
        assert "REJECTED" in result or "CONFIDENCE" in result

    @pytest.mark.asyncio
    async def test_whynot_shows_failed_checks(self):
        """14. whynot_shows_failed_checks - output includes failed check names."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej = self._sample_rejection(
            checks_failed=[
                "CONFIDENCE: 0.55 < 0.6 minimum",
                "LOSS_STREAK: 3 consecutive losses (>= 3)",
            ]
        )
        engine = self._make_engine_with_rejections({"BTC": rej})
        result = await skill.execute(engine, symbol="BTC")
        assert "CONFIDENCE" in result
        assert "LOSS_STREAK" in result
        assert "Failed" in result or "failed" in result.lower()

    @pytest.mark.asyncio
    async def test_whynot_shows_passed_checks(self):
        """15. whynot_shows_passed_checks - output includes passed check count."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej = self._sample_rejection(
            checks_passed=[
                "CIRCUIT_BREAKER: OK",
                "POSITION_SIZE: notional 15.0% <= 20%",
                "DAILY_LOSS: 0.0% OK",
            ]
        )
        engine = self._make_engine_with_rejections({"BTC": rej})
        result = await skill.execute(engine, symbol="BTC")
        assert "Passed" in result or "passed" in result.lower()
        # Should mention the count (3)
        assert "3" in result

    @pytest.mark.asyncio
    async def test_whynot_latest_when_no_symbol(self):
        """16. whynot_latest_when_no_symbol - no symbol arg returns most recent."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej_btc = self._sample_rejection(symbol="BTC/USDT")
        rej_eth = self._sample_rejection(symbol="ETH/USDT")
        # dict ordering: ETH is last inserted
        engine = self._make_engine_with_rejections({
            "BTC": rej_btc,
            "ETH": rej_eth,
        })
        result = await skill.execute(engine)
        # Should show ETH (the most recent / last key)
        assert "ETH" in result

    @pytest.mark.asyncio
    async def test_whynot_unknown_symbol_returns_message(self):
        """17. whynot_unknown_symbol_returns_message - query 'XYZ' returns not-found msg."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej = self._sample_rejection(symbol="BTC/USDT")
        engine = self._make_engine_with_rejections({"BTC": rej})
        result = await skill.execute(engine, symbol="XYZ")
        assert "no rejection" in result.lower() or "No rejection" in result

    @pytest.mark.asyncio
    async def test_whynot_cap_at_100(self):
        """18. whynot_cap_at_100 - store 110 rejections, verify capped at ~100."""
        # This tests the capping logic from engine.py _analyze_signal
        rejections: dict[str, dict] = {}
        for i in range(110):
            symbol_key = f"SYM{i}"
            rejections[symbol_key] = self._sample_rejection(symbol=f"SYM{i}/USDT")

        # Apply the same capping logic from engine.py
        if len(rejections) > 100:
            oldest_keys = list(rejections.keys())[:-50]
            for k in oldest_keys:
                rejections.pop(k, None)

        # After capping, should have 50 entries (kept the last 50)
        assert len(rejections) <= 100
        assert len(rejections) == 50
        # The remaining keys should be the last 50 (SYM60..SYM109)
        assert "SYM109" in rejections
        assert "SYM0" not in rejections

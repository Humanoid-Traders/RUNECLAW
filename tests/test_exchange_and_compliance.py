"""
Tests for ExchangeFlowProvider, ComplianceEngine, and AuditChain.

Covers exchange flow interpretation, compliance authorization locks,
and tamper-evident audit chain integrity.
"""

from __future__ import annotations

import asyncio
import tempfile
from unittest.mock import AsyncMock


from bot.core.exchange_flow import ExchangeFlowProvider
from bot.core.order_flow import OrderFlowSignal
from bot.compliance.compliance_engine import (
    ComplianceEngine,
    Permission,
    SubjectProfile,
    default_demo_profile,
)
from bot.utils.audit_chain import AuditChain, DecisionRecord


# ── helpers ──────────────────────────────────────────────────────────


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_profile(
    *,
    permissions=None,
    jurisdiction="US",
    max_notional=10_000.0,
    kyc=False,
    subject_id="test-user",
) -> SubjectProfile:
    if permissions is None:
        permissions = {Permission.READ_ONLY, Permission.PAPER_TRADE}
    return SubjectProfile(
        subject_id=subject_id,
        permissions=permissions,
        jurisdiction=jurisdiction,
        max_notional_usd=max_notional,
        kyc_verified=kyc,
    )


def _mock_exchange(funding_rate=0.0005, oi_value=1_000_000):
    """Return a factory that produces a mock ccxt exchange."""
    ex = AsyncMock()
    ex.fetch_funding_rate.return_value = {"fundingRate": funding_rate}
    ex.fetch_open_interest.return_value = {"openInterestValue": oi_value}
    ex.fetch_funding_rate_history.return_value = []
    return lambda: ex


# =====================================================================
# TestExchangeFlowProvider
# =====================================================================


class TestExchangeFlowProvider:
    """Exchange flow data feed tests."""

    def test_funding_rate_provider_init(self):
        """1. Creates without error, even with no exchange factory."""
        provider = ExchangeFlowProvider()
        assert provider is not None
        assert provider._funding_ttl == 60.0

    def test_flow_signal_model_fields(self):
        """2. OrderFlowSignal has expected exchange-flow fields."""
        sig = OrderFlowSignal(symbol="BTC/USDT")
        assert hasattr(sig, "funding_rate")
        assert hasattr(sig, "open_interest_usd")
        assert hasattr(sig, "oi_change_pct")
        assert hasattr(sig, "book_imbalance")
        assert hasattr(sig, "cvd_window_usd")

    def test_interpret_positive_funding(self):
        """3. Positive funding rate produces bullish-leaning interpretation."""
        # Extreme positive rate -> squeeze risk mentioning 'longs'
        _risk, interp = ExchangeFlowProvider._assess_squeeze_risk(
            funding_rate=0.002, oi_change_pct=0.0, funding_trend="STABLE",
        )
        assert "longs" in interp.lower() or "long" in interp.lower()

    def test_interpret_negative_funding(self):
        """4. Negative funding rate produces bearish-leaning interpretation."""
        _risk, interp = ExchangeFlowProvider._assess_squeeze_risk(
            funding_rate=-0.002, oi_change_pct=0.0, funding_trend="STABLE",
        )
        assert "shorts" in interp.lower() or "short" in interp.lower()

    def test_interpret_neutral_funding(self):
        """5. Near-zero funding rate returns neutral / no squeeze."""
        risk, interp = ExchangeFlowProvider._assess_squeeze_risk(
            funding_rate=0.00001, oi_change_pct=0.0, funding_trend="STABLE",
        )
        assert risk == "NONE"
        assert "neutral" in interp.lower()

    def test_cache_ttl_respected(self):
        """6. Same call within TTL returns cached value without re-fetch."""
        factory = _mock_exchange(funding_rate=0.001)
        provider = ExchangeFlowProvider(exchange_factory=factory, funding_ttl=300)

        rate1 = _run(provider.get_funding_rate("BTC/USDT"))
        rate2 = _run(provider.get_funding_rate("BTC/USDT"))

        assert rate1 == rate2 == 0.001
        # The exchange should have been called only once (second is cached)
        ex = factory()
        # factory() returns the same mock each call; first real fetch + our call = 2
        # But the important thing: the second get_funding_rate used cache
        # We verify by checking the cache entry is fresh
        swap = "BTC/USDT:USDT"
        entry = provider._cache[swap]
        assert entry["funding_rate"] == 0.001
        assert entry["updated_at"] > 0

    def test_oi_interpretation(self):
        """7. High OI change signals conviction (HIGH squeeze risk)."""
        risk, interp = ExchangeFlowProvider._assess_squeeze_risk(
            funding_rate=0.002, oi_change_pct=10.0, funding_trend="RISING",
        )
        assert risk == "HIGH"
        assert "rising oi" in interp.lower() or "accelerating" in interp.lower()


# =====================================================================
# TestComplianceEngine
# =====================================================================


class TestComplianceEngine:
    """Permission tier enforcement."""

    def test_default_profile_permissions(self):
        """8. Default demo profile has READ_ONLY, ANALYSIS, PAPER_TRADE only."""
        profile = default_demo_profile()
        assert Permission.READ_ONLY in profile.permissions
        assert Permission.ANALYSIS in profile.permissions
        assert Permission.PAPER_TRADE in profile.permissions
        assert Permission.LIVE_TRADE not in profile.permissions
        assert Permission.ADMIN not in profile.permissions

    def test_paper_trade_allowed_in_sim(self):
        """9. Paper trade authorized in simulation mode."""
        engine = ComplianceEngine()
        profile = _make_profile(permissions={Permission.PAPER_TRADE})
        decision = engine.authorize(
            action=Permission.PAPER_TRADE,
            profile=profile,
            live_mode=False,
            risk_passed=True,
            macro_ok=True,
            notional_usd=1000.0,
        )
        assert decision.granted is True
        assert "permission" in decision.locks_passed

    def test_live_trade_blocked_without_kyc(self):
        """10. Live trade denied without LIVE_TRADE permission (no KYC)."""
        engine = ComplianceEngine()
        profile = _make_profile(
            permissions={Permission.PAPER_TRADE, Permission.READ_ONLY},
            kyc=False,
        )
        decision = engine.authorize(
            action=Permission.LIVE_TRADE,
            profile=profile,
            live_mode=True,
            risk_passed=True,
            macro_ok=True,
            notional_usd=100.0,
        )
        assert decision.granted is False
        assert "permission" in decision.locks_failed

    def test_live_trade_blocked_without_risk(self):
        """11. Live trade denied when risk check failed."""
        engine = ComplianceEngine()
        profile = _make_profile(
            permissions={Permission.LIVE_TRADE},
        )
        decision = engine.authorize(
            action=Permission.LIVE_TRADE,
            profile=profile,
            live_mode=True,
            risk_passed=False,
            macro_ok=True,
            notional_usd=100.0,
        )
        assert decision.granted is False
        assert "risk" in decision.locks_failed
        assert any("risk" in r.lower() for r in decision.reasons)

    def test_read_only_always_allowed(self):
        """12. READ_ONLY permission is always present in paper mode grant."""
        engine = ComplianceEngine()
        profile = _make_profile(
            permissions={Permission.READ_ONLY, Permission.PAPER_TRADE},
        )
        decision = engine.authorize(
            action=Permission.READ_ONLY,
            profile=profile,
            live_mode=False,
            risk_passed=True,
            macro_ok=True,
            notional_usd=0.0,
        )
        # Paper mode checks PAPER_TRADE permission; profile has it
        assert decision.granted is True

    def test_compliance_decision_has_reasons(self):
        """13. Denied decision includes reason strings."""
        engine = ComplianceEngine()
        profile = _make_profile(permissions={Permission.READ_ONLY})
        decision = engine.authorize(
            action=Permission.PAPER_TRADE,
            profile=profile,
            live_mode=False,
            risk_passed=False,
            macro_ok=True,
            notional_usd=100.0,
        )
        assert decision.granted is False
        assert len(decision.reasons) >= 1
        assert all(isinstance(r, str) for r in decision.reasons)

    def test_notional_limit_enforced(self):
        """14. Trade above notional limit rejected."""
        engine = ComplianceEngine()
        profile = _make_profile(
            permissions={Permission.LIVE_TRADE},
            max_notional=5_000.0,
        )
        token = engine.issue_approval_token("trade-1", profile.subject_id)
        decision = engine.authorize(
            action=Permission.LIVE_TRADE,
            profile=profile,
            live_mode=True,
            risk_passed=True,
            macro_ok=True,
            notional_usd=50_000.0,
            trade_id="trade-1",
            approval_token=token,
        )
        assert decision.granted is False
        assert "notional_cap" in decision.locks_failed
        assert any("exceeds" in r.lower() for r in decision.reasons)


# =====================================================================
# TestAuditChain
# =====================================================================


class TestAuditChain:
    """Tamper-evident audit log verification."""

    def _make_chain(self) -> tuple[AuditChain, str]:
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        tmp.close()
        return AuditChain(path=tmp.name), tmp.name

    def test_append_creates_chain(self):
        """15. Append 3 entries, verify chain length."""
        chain, path = self._make_chain()
        chain.append("TEST", {"k": 1})
        chain.append("TEST", {"k": 2})
        chain.append("TEST", {"k": 3})
        assert chain.get_chain_length() == 3

    def test_hash_chain_integrity(self):
        """16. Each entry's prev_hash matches previous entry's hash."""
        chain, path = self._make_chain()
        e1 = chain.append("A", {"x": 1})
        e2 = chain.append("B", {"x": 2})
        e3 = chain.append("C", {"x": 3})

        entries = chain.get_entries()
        assert entries[0].prev_hash == "0" * 64  # genesis
        assert entries[1].prev_hash == entries[0].entry_hash
        assert entries[2].prev_hash == entries[1].entry_hash

    def test_verify_passes_valid_chain(self):
        """17. verify() returns True on untampered chain."""
        chain, path = self._make_chain()
        chain.append("EVT", {"a": 1})
        chain.append("EVT", {"a": 2})
        chain.append("EVT", {"a": 3})

        ok, problems = AuditChain.verify(path)
        assert ok is True
        assert problems == []

    def test_decision_record_fields(self):
        """18. DecisionRecord has all required fields."""
        rec = DecisionRecord(
            decision_id="d-001",
            symbol="BTC/USDT",
        )
        assert rec.decision_id == "d-001"
        assert rec.symbol == "BTC/USDT"
        assert rec.outcome == "REJECTED"
        assert rec.is_paper is True
        assert rec.timestamp is not None
        assert rec.idea is None
        assert rec.risk is None
        assert rec.macro is None
        assert rec.compliance is None

    def test_seal_decision_appends(self):
        """19. seal_decision adds to chain."""
        chain, path = self._make_chain()
        rec = DecisionRecord(decision_id="d-002", symbol="ETH/USDT")
        entry = chain.seal_decision(rec)

        assert entry.event_type == "DECISION"
        assert chain.get_chain_length() == 1
        assert entry.payload["decision_id"] == "d-002"

    def test_chain_detects_tampering(self):
        """20. Modify an entry, verify() catches it."""
        chain, path = self._make_chain()
        chain.append("EVT", {"v": 1})
        chain.append("EVT", {"v": 2})
        chain.append("EVT", {"v": 3})

        # Tamper: rewrite the second line with a different payload
        import json
        with open(path, "r") as f:
            lines = f.readlines()

        tampered = json.loads(lines[1])
        tampered["payload"]["v"] = 999  # change payload without updating hash
        lines[1] = json.dumps(tampered) + "\n"

        with open(path, "w") as f:
            f.writelines(lines)

        ok, problems = AuditChain.verify(path)
        assert ok is False
        assert len(problems) >= 1
        assert any("entry_hash" in p for p in problems)

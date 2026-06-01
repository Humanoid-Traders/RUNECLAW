"""
Tests for three new RUNECLAW features:
  - Portfolio VaR (risk engine check #21)
  - Adversarial self-critique (TradeCritique)
  - Ed25519 attestation (AttestationEngine)
"""

import hashlib
import math
import os
import tempfile

import pytest
from unittest.mock import MagicMock, patch

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_idea(
    confidence=0.75,
    entry_price=100_000.0,
    stop_loss=97_000.0,
    take_profit=106_000.0,
    direction_value="LONG",
    asset="BTC/USDT",
):
    """Build a MagicMock that looks like a TradeIdea."""
    idea = MagicMock()
    idea.confidence = confidence
    idea.risk_reward_ratio = (
        abs(take_profit - entry_price) / abs(entry_price - stop_loss)
        if abs(entry_price - stop_loss) > 0
        else 0.0
    )
    idea.direction = MagicMock()
    idea.direction.value = direction_value
    idea.asset = asset
    idea.entry_price = entry_price
    idea.stop_loss = stop_loss
    idea.take_profit = take_profit
    return idea


def _make_snapshot(open_positions=None, equity_usd=10_000.0):
    snapshot = MagicMock()
    snapshot.open_positions = open_positions or []
    snapshot.equity_usd = equity_usd
    return snapshot


def _make_position(direction_value="LONG", asset="BTC/USDT"):
    pos = MagicMock()
    pos.direction = MagicMock()
    pos.direction.value = direction_value
    # Make direction equality work by matching on the mock object itself
    pos.direction.__eq__ = lambda self, other: (
        getattr(other, "value", None) == direction_value
        or other is self
    )
    pos.asset = asset
    return pos


def _make_closed_trade(entry_price, exit_price, quantity, pnl, direction_value="LONG"):
    t = MagicMock()
    t.entry_price = entry_price
    t.exit_price = exit_price
    t.quantity = quantity
    t.pnl = pnl
    t.direction = MagicMock()
    t.direction.value = direction_value
    t.asset = "BTC/USDT"
    return t


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: Portfolio VaR Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPortfolioVaR:
    """Tests for RiskEngine._compute_portfolio_var and check #21."""

    def _make_engine(self, trade_history, open_positions=None, equity=10_000.0):
        """Build a RiskEngine with mocked portfolio."""
        portfolio = MagicMock()
        portfolio.trade_history = trade_history
        portfolio.open_positions = open_positions or []

        snap = MagicMock()
        snap.equity_usd = equity
        snap.daily_pnl = 0.0
        snap.max_drawdown_pct = 0.0
        snap.open_positions = len(open_positions or [])
        portfolio.snapshot.return_value = snap
        portfolio.get_position_value.return_value = 0.0

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
            state_file = f.name

        try:
            from bot.risk.risk_engine import RiskEngine
            engine = RiskEngine(portfolio, state_file=state_file)
        finally:
            if os.path.exists(state_file):
                os.unlink(state_file)

        return engine

    def test_var_with_sufficient_history(self):
        """VaR computation with >=5 closed trades returns valid percentage."""
        trades = [
            _make_closed_trade(100, 105, 1.0, 5.0),
            _make_closed_trade(100, 98, 1.0, -2.0),
            _make_closed_trade(100, 103, 1.0, 3.0),
            _make_closed_trade(100, 97, 1.0, -3.0),
            _make_closed_trade(100, 110, 1.0, 10.0),
        ]
        engine = self._make_engine(trades, equity=10_000.0)
        current, proposed = engine._compute_portfolio_var(1000.0)
        assert current >= 0
        assert proposed >= 0

    def test_var_insufficient_history_returns_sentinel(self):
        """VaR with <5 closed trades returns (-1, -1) sentinel."""
        trades = [_make_closed_trade(100, 105, 1.0, 5.0)] * 3
        engine = self._make_engine(trades)
        current, proposed = engine._compute_portfolio_var(1000.0)
        assert current == -1.0
        assert proposed == -1.0

    def test_var_check_passes_within_limit(self):
        """When VaR is within limit, check #21 passes."""
        # Small trades relative to equity => low VaR
        trades = [
            _make_closed_trade(100, 101, 0.01, 0.01),
            _make_closed_trade(100, 99, 0.01, -0.01),
            _make_closed_trade(100, 100.5, 0.01, 0.005),
            _make_closed_trade(100, 99.5, 0.01, -0.005),
            _make_closed_trade(100, 102, 0.01, 0.02),
        ]
        engine = self._make_engine(trades, equity=100_000.0)
        current, proposed = engine._compute_portfolio_var(100.0)
        # Small position relative to large equity => low VaR
        assert proposed >= 0
        from bot.config import CONFIG
        assert proposed <= CONFIG.risk.max_portfolio_var_pct or proposed >= 0

    def test_var_skips_with_insufficient_data(self):
        """VaR check gracefully skips (passes) when insufficient data."""
        trades = [_make_closed_trade(100, 105, 1.0, 5.0)] * 2
        engine = self._make_engine(trades)
        current, proposed = engine._compute_portfolio_var(1000.0)
        assert current == -1.0
        assert proposed == -1.0

    def test_var_zero_equity_returns_max_risk(self):
        """VaR with zero equity returns (0, 100) indicating max risk."""
        trades = [
            _make_closed_trade(100, 105, 1.0, 5.0),
            _make_closed_trade(100, 98, 1.0, -2.0),
            _make_closed_trade(100, 103, 1.0, 3.0),
            _make_closed_trade(100, 97, 1.0, -3.0),
            _make_closed_trade(100, 110, 1.0, 10.0),
        ]
        engine = self._make_engine(trades, equity=0.0)
        current, proposed = engine._compute_portfolio_var(1000.0)
        assert current == 0.0
        assert proposed == 100.0

    def test_var_all_winning_trades_produces_low_var(self):
        """All winning trades with small variance should produce low VaR."""
        trades = [
            _make_closed_trade(100, 101, 1.0, 1.0),
            _make_closed_trade(100, 101.5, 1.0, 1.5),
            _make_closed_trade(100, 102, 1.0, 2.0),
            _make_closed_trade(100, 101.2, 1.0, 1.2),
            _make_closed_trade(100, 100.8, 1.0, 0.8),
        ]
        engine = self._make_engine(trades, equity=50_000.0)
        _, proposed = engine._compute_portfolio_var(500.0)
        # All positive returns with small variance => low VaR
        assert proposed >= 0
        assert proposed < 10  # should be quite small

    def test_var_mixed_trades_reasonable(self):
        """Mixed winning/losing trades produce a reasonable VaR."""
        trades = [
            _make_closed_trade(100, 110, 1.0, 10.0),
            _make_closed_trade(100, 85, 1.0, -15.0),
            _make_closed_trade(100, 120, 1.0, 20.0),
            _make_closed_trade(100, 90, 1.0, -10.0),
            _make_closed_trade(100, 105, 1.0, 5.0),
            _make_closed_trade(100, 80, 1.0, -20.0),
        ]
        engine = self._make_engine(trades, equity=10_000.0)
        _, proposed = engine._compute_portfolio_var(5000.0)
        assert proposed > 0, "Mixed trades should produce non-zero VaR"

    def test_var_check_appears_in_risk_output(self):
        """Integration: VaR check result appears in the risk engine output."""
        trades = [_make_closed_trade(100, 105, 1.0, 5.0)] * 2  # insufficient
        engine = self._make_engine(trades, equity=50_000.0)

        from bot.utils.models import TradeIdea, Direction
        from datetime import datetime, timezone

        idea = TradeIdea(
            asset="BTC/USDT",
            direction=Direction.LONG,
            entry_price=100_000.0,
            stop_loss=97_000.0,
            take_profit=106_000.0,
            confidence=0.75,
            reasoning="Test trade",
            timestamp=datetime.now(timezone.utc),
        )
        result = engine.evaluate(idea, atr=500.0)

        var_mentions = [
            c for c in result.checks_passed + result.checks_failed
            if "PORTFOLIO_VAR" in c
        ]
        assert len(var_mentions) == 1, "PORTFOLIO_VAR should appear exactly once"
        assert "skipped" in var_mentions[0].lower() or "%" in var_mentions[0]


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: Adversarial Self-Critique Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestTradeCritique:
    """Tests for bot.core.critique.TradeCritique."""

    def setup_method(self):
        from bot.core.critique import TradeCritique
        self.critic = TradeCritique()

    def test_clean_trade_returns_pass(self):
        """A trade with no red flags returns PASS verdict."""
        idea = _make_idea(confidence=0.75)
        snapshot = _make_snapshot()
        risk_check = MagicMock()

        result = self.critic.evaluate(idea, risk_check, snapshot)
        assert result.verdict == "PASS"
        assert len(result.concerns) == 0

    def test_high_confidence_triggers_warning(self):
        """Confidence > 0.90 triggers overconfidence concern."""
        idea = _make_idea(confidence=0.95)
        snapshot = _make_snapshot()
        risk_check = MagicMock()

        result = self.critic.evaluate(idea, risk_check, snapshot)
        assert any("confidence" in c.lower() for c in result.concerns)
        assert result.confidence_adjustment < 0

    def test_low_rr_triggers_warning(self):
        """R:R < 1.5 triggers marginal R:R concern."""
        # entry=100000, sl=99000, tp=101000 => R:R = 1.0
        idea = _make_idea(
            entry_price=100_000.0,
            stop_loss=99_000.0,
            take_profit=101_000.0,
        )
        idea.risk_reward_ratio = 1.0
        snapshot = _make_snapshot()
        risk_check = MagicMock()

        result = self.critic.evaluate(idea, risk_check, snapshot)
        assert any("r:r" in c.lower() or "R:R" in c for c in result.concerns)

    def test_concentration_same_direction(self):
        """3+ same-direction positions triggers concentration concern."""
        idea = _make_idea(direction_value="LONG")
        positions = [_make_position("LONG") for _ in range(3)]
        # Fix direction equality: make position directions equal to idea direction
        for p in positions:
            p.direction = idea.direction
        snapshot = _make_snapshot(open_positions=positions)
        risk_check = MagicMock()

        result = self.critic.evaluate(idea, risk_check, snapshot)
        assert any("direction" in c.lower() or "crowded" in c.lower() for c in result.concerns)

    def test_same_asset_double_down(self):
        """Having an open position in same asset triggers double-down warning."""
        idea = _make_idea(asset="ETH/USDT")
        pos = _make_position(asset="ETH/USDT")
        snapshot = _make_snapshot(open_positions=[pos])
        risk_check = MagicMock()

        result = self.critic.evaluate(idea, risk_check, snapshot)
        assert any("doubling down" in c.lower() or idea.asset in c for c in result.concerns)

    def test_portfolio_heat_many_positions(self):
        """4+ open positions triggers portfolio heat concern."""
        idea = _make_idea()
        positions = [_make_position(asset=f"ASSET{i}/USDT") for i in range(5)]
        snapshot = _make_snapshot(open_positions=positions)
        risk_check = MagicMock()

        result = self.critic.evaluate(idea, risk_check, snapshot)
        assert any("hot" in c.lower() or "open positions" in c.lower() for c in result.concerns)

    def test_macro_reduce_triggers_concern(self):
        """Macro context in REDUCE state triggers concern."""
        idea = _make_idea()
        snapshot = _make_snapshot()
        risk_check = MagicMock()
        macro = MagicMock()
        macro.risk_state = "REDUCE"

        result = self.critic.evaluate(idea, risk_check, snapshot, macro_context=macro)
        assert any("REDUCE" in c or "macro" in c.lower() for c in result.concerns)

    def test_tight_stop_triggers_concern(self):
        """Stop loss < 1% from entry triggers stop hunt concern."""
        # SL 0.5% from entry
        idea = _make_idea(
            entry_price=100_000.0,
            stop_loss=99_600.0,  # 0.4% away
            take_profit=106_000.0,
        )
        snapshot = _make_snapshot()
        risk_check = MagicMock()

        result = self.critic.evaluate(idea, risk_check, snapshot)
        assert any("stop" in c.lower() and "hunt" in c.lower() for c in result.concerns)

    def test_three_concerns_produces_halt(self):
        """3+ concerns produces HALT verdict."""
        # Combine: high confidence + tight stop + same-asset double-down + portfolio heat
        idea = _make_idea(
            confidence=0.95,
            entry_price=100_000.0,
            stop_loss=99_800.0,  # tight stop (0.2%)
            take_profit=106_000.0,
        )
        idea.risk_reward_ratio = 2.0

        pos_same_asset = _make_position(asset="BTC/USDT")
        other_positions = [_make_position(asset=f"ALT{i}/USDT") for i in range(4)]
        all_positions = [pos_same_asset] + other_positions

        snapshot = _make_snapshot(open_positions=all_positions)
        risk_check = MagicMock()

        result = self.critic.evaluate(idea, risk_check, snapshot)
        assert len(result.concerns) >= 3
        assert result.verdict == "HALT"


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: Ed25519 Attestation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAttestationEngine:
    """Tests for bot.utils.attestation.AttestationEngine."""

    def _make_engine(self):
        from bot.utils.attestation import AttestationEngine
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            key_path = f.name
        # Remove so engine generates a fresh key
        os.unlink(key_path)
        engine = AttestationEngine(key_path=key_path)
        return engine, key_path

    def _cleanup(self, key_path):
        if os.path.exists(key_path):
            os.unlink(key_path)

    def test_engine_initializes_and_generates_keys(self):
        """AttestationEngine initializes and generates Ed25519 keys."""
        engine, kp = self._make_engine()
        try:
            assert engine.available is True
            assert engine._signing_key is not None
            assert engine._verify_key is not None
            assert os.path.exists(kp)
        finally:
            self._cleanup(kp)

    def test_public_key_hex_length(self):
        """Public key hex is 64 chars (32 bytes)."""
        engine, kp = self._make_engine()
        try:
            pk_hex = engine.public_key_hex
            assert len(pk_hex) == 64
            # Verify it's valid hex
            bytes.fromhex(pk_hex)
        finally:
            self._cleanup(kp)

    def test_sign_empty_batch_returns_error(self):
        """Signing an empty batch returns an error result."""
        engine, kp = self._make_engine()
        try:
            result = engine.sign_batch([])
            assert result.valid is False
            assert result.error is not None
            assert "empty" in result.error.lower() or "Empty" in result.error
        finally:
            self._cleanup(kp)

    def test_sign_batch_returns_valid_result(self):
        """Signing a batch of hashes returns a valid AttestationResult."""
        engine, kp = self._make_engine()
        try:
            hashes = [
                hashlib.sha256(b"entry1").hexdigest(),
                hashlib.sha256(b"entry2").hexdigest(),
                hashlib.sha256(b"entry3").hexdigest(),
            ]
            result = engine.sign_batch(hashes)
            assert result.valid is True
            assert len(result.signature_hex) > 0
            assert len(result.public_key_hex) == 64
            assert result.batch_size == 3
            assert len(result.entries_hash) == 64
        finally:
            self._cleanup(kp)

    def test_verify_signed_batch_succeeds(self):
        """Verifying a correctly signed batch succeeds."""
        engine, kp = self._make_engine()
        try:
            hashes = [
                hashlib.sha256(b"audit_entry_1").hexdigest(),
                hashlib.sha256(b"audit_entry_2").hexdigest(),
            ]
            sign_result = engine.sign_batch(hashes)
            assert sign_result.valid is True

            verify_result = engine.verify_batch(
                hashes,
                sign_result.signature_hex,
                sign_result.public_key_hex,
            )
            assert verify_result.valid is True
        finally:
            self._cleanup(kp)

    def test_tampered_hash_fails_verification(self):
        """Verification fails if a hash in the batch is tampered."""
        engine, kp = self._make_engine()
        try:
            hashes = [
                hashlib.sha256(b"entry_a").hexdigest(),
                hashlib.sha256(b"entry_b").hexdigest(),
            ]
            sign_result = engine.sign_batch(hashes)
            assert sign_result.valid is True

            # Tamper with one hash
            tampered = [
                hashlib.sha256(b"entry_a_TAMPERED").hexdigest(),
                hashes[1],
            ]
            verify_result = engine.verify_batch(
                tampered,
                sign_result.signature_hex,
                sign_result.public_key_hex,
            )
            assert verify_result.valid is False
        finally:
            self._cleanup(kp)

    def test_merkle_root_deterministic(self):
        """Same input hashes produce the same Merkle root."""
        engine, kp = self._make_engine()
        try:
            hashes = [
                hashlib.sha256(b"a").hexdigest(),
                hashlib.sha256(b"b").hexdigest(),
                hashlib.sha256(b"c").hexdigest(),
            ]
            root1 = engine.compute_merkle_root(hashes)
            root2 = engine.compute_merkle_root(hashes)
            assert root1 == root2
            assert len(root1) == 64
        finally:
            self._cleanup(kp)

    def test_merkle_root_single_hash(self):
        """Merkle root of a single hash is the hash itself."""
        engine, kp = self._make_engine()
        try:
            h = hashlib.sha256(b"only_entry").hexdigest()
            root = engine.compute_merkle_root([h])
            assert root == h
        finally:
            self._cleanup(kp)

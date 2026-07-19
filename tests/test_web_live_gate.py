"""Web live-trading gate — pre-registered predictions G1–G5.

G1 default is paper (feature off); G2 all-five-hold → allowed; G3 fail-closed
first-unmet-reason; G4 ordered checklist; G5 the dedicated user-store flag is
separate from can_trade_live and web-only. The gate is pure; the store flag is
covered directly.
"""
import pytest

from bot.web import web_live_gate as g


def _all_on():
    return dict(feature_enabled=True, bot_is_live=True, user_opted_in=True,
                has_own_keys=True, envelope_enforcing=True)


# ── G1 — default is paper ─────────────────────────────────────────────

def test_g1_feature_off_denies():
    d = g.evaluate(**{**_all_on(), "feature_enabled": False})
    assert d.allowed is False
    assert "not enabled by the operator" in d.reason


def test_g1_feature_flag_env_default_off(monkeypatch):
    monkeypatch.delenv("WEB_LIVE_TRADING_ENABLED", raising=False)
    assert g.feature_enabled(env={}) is False
    for v in ("1", "true", "YES", "on"):
        assert g.feature_enabled(env={"WEB_LIVE_TRADING_ENABLED": v}) is True
    for v in ("0", "false", "", "no", "paper"):
        assert g.feature_enabled(env={"WEB_LIVE_TRADING_ENABLED": v}) is False


# ── G2 — all five preconditions → allowed ─────────────────────────────

def test_g2_all_hold_allows():
    d = g.evaluate(**_all_on())
    assert d.allowed is True
    assert all(d.checklist.values())


# ── G3 — fail-closed, first-unmet reason ──────────────────────────────

@pytest.mark.parametrize("missing,needle", [
    ("bot_is_live", "paper mode"),
    ("user_opted_in", "enabled live trading"),
    ("has_own_keys", "connect your own exchange keys"),
    ("envelope_enforcing", "Authority Envelope in enforce mode"),
])
def test_g3_each_missing_precondition_denies(missing, needle):
    d = g.evaluate(**{**_all_on(), missing: False})
    assert d.allowed is False
    assert needle in d.reason
    assert d.checklist[missing] is False


def test_g3_first_unmet_wins_precedence():
    # feature off AND keys missing → the feature reason surfaces first (order).
    d = g.evaluate(feature_enabled=False, bot_is_live=True, user_opted_in=True,
                   has_own_keys=False, envelope_enforcing=False)
    assert "not enabled by the operator" in d.reason


# ── G4 — checklist reflects every input ───────────────────────────────

def test_g4_checklist_shape():
    d = g.evaluate(feature_enabled=True, bot_is_live=False, user_opted_in=True,
                   has_own_keys=False, envelope_enforcing=True)
    assert d.checklist == {"feature_enabled": True, "bot_is_live": False,
                           "user_opted_in": True, "has_own_keys": False,
                           "envelope_enforcing": True}


# ── G5 — the store flag is separate + web-only ────────────────────────

def test_g5_store_flag_is_separate_and_web_only():
    from bot.utils.user_store import UserStore
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        store = UserStore(path)
        # A telegram user: web_live_enabled is always False (web-only concept).
        store.register("12345", name="tg")
        store.authorize("12345", role="trader")
        assert store.set_web_live_enabled("12345", True) is False
        assert store.web_live_enabled("12345") is False
        # A web user: the flag works and is independent of can_trade_live.
        store.register("web:7", name="webby")
        store.authorize("web:7", role="trader")
        assert store.web_live_enabled("web:7") is False        # default off
        assert store.set_web_live_enabled("web:7", True) is True
        assert store.web_live_enabled("web:7") is True
        # can_trade_live stays structurally False for the web id regardless.
        assert store.can_trade_live("web:7") is False
    finally:
        os.unlink(path)

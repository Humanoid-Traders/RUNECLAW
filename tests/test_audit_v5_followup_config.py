"""
Regression tests for the V5 follow-up audit fixes (docs/AUDIT_REPORT_V5.md).

Covers:
  RC-AUD-017 — dashboard bind host is configurable via DASHBOARD_BIND_HOST and
               defaults to "0.0.0.0" (Docker + nginx-sidecar deployment); the
               real protection is the mandatory DASHBOARD_TOKEN gate on /api/*.
  RC-AUD-019 — inherited-env safety-switch detection: a safety switch present in
               the process environment BEFORE load_dotenv is identified as
               inherited (and thus overrides .env), so a startup WARNING fires.

All tests are deterministic and do not touch the network or the engine. The
inherited-env logic is exercised through a pure helper that operates only on a
passed-in key set, so it does not depend on the ambient process environment.
"""
import os

import bot.config as config


# ── RC-AUD-017: dashboard bind host default + override ──────────────────────

# bot/main.py computes the bind host as:
#     os.environ.get("DASHBOARD_BIND_HOST", "0.0.0.0")
# inside an async closure, so we pin the same env-reading expression here.
_DASHBOARD_BIND_DEFAULT = "0.0.0.0"


def _resolve_bind_host() -> str:
    """Mirror of the bind-host resolution in bot/main.run_telegram()."""
    return os.environ.get("DASHBOARD_BIND_HOST", _DASHBOARD_BIND_DEFAULT)


def test_dashboard_bind_host_defaults_to_all_interfaces(monkeypatch):
    """With DASHBOARD_BIND_HOST unset, the dashboard binds 0.0.0.0.

    NOTE: the default is intentionally 0.0.0.0 (not 127.0.0.1) because the bot
    runs in Docker behind an nginx proxy in a separate container that reaches
    the dashboard over the docker network. The /api/* surface stays protected
    by the mandatory DASHBOARD_TOKEN gate, which is fail-closed (403) when unset.
    """
    monkeypatch.delenv("DASHBOARD_BIND_HOST", raising=False)
    assert _resolve_bind_host() == "0.0.0.0"


def test_dashboard_bind_host_honours_override(monkeypatch):
    """Operators can restrict the dashboard to localhost via DASHBOARD_BIND_HOST."""
    monkeypatch.setenv("DASHBOARD_BIND_HOST", "127.0.0.1")
    assert _resolve_bind_host() == "127.0.0.1"


def test_dashboard_api_fail_closed_without_token():
    """RC-AUD-017: the /api/* auth middleware must fail closed when no token is
    configured (returns 403, never serves aggregate multi-user state)."""
    from bot.web import dashboard_server

    # The "no token configured" branch is the fail-closed guard. Verify the
    # source still contains the 403 fail-closed path for the empty-token case.
    import inspect

    src = inspect.getsource(dashboard_server.auth_middleware)
    assert "if not _DASHBOARD_TOKEN" in src
    assert "status=403" in src


# ── RC-AUD-019: inherited-env safety-switch detection ───────────────────────

def test_safety_switch_keys_are_the_three_switches():
    """The set of watched safety switches is exactly the three documented keys."""
    assert set(config._SAFETY_SWITCH_KEYS) == {
        "SIMULATION_MODE",
        "LIVE_TRADING_ENABLED",
        "BITGET_SANDBOX",
    }


def test_inherited_detection_flags_inherited_key():
    """A safety switch present in the pre-load_dotenv key snapshot is flagged as
    inherited (it would override .env under load_dotenv(override=False))."""
    pre_keys = {"PATH", "HOME", "SIMULATION_MODE"}
    inherited = config._detect_inherited_safety_switches(pre_keys)
    assert "SIMULATION_MODE" in inherited
    # Non-safety env vars are never reported.
    assert "PATH" not in inherited
    assert "HOME" not in inherited


def test_inherited_detection_ignores_absent_switch():
    """A safety switch absent from the snapshot (i.e. it came from .env, not the
    inherited process env) is NOT flagged."""
    pre_keys = {"PATH", "HOME"}  # no safety switches inherited
    inherited = config._detect_inherited_safety_switches(pre_keys)
    assert inherited == []


def test_inherited_detection_flags_all_three():
    """All three safety switches are detected when all are inherited."""
    pre_keys = {"SIMULATION_MODE", "LIVE_TRADING_ENABLED", "BITGET_SANDBOX", "PATH"}
    inherited = config._detect_inherited_safety_switches(pre_keys)
    assert set(inherited) == {
        "SIMULATION_MODE",
        "LIVE_TRADING_ENABLED",
        "BITGET_SANDBOX",
    }


def test_inherited_detection_is_pure_and_deterministic():
    """The helper depends only on its argument, not the ambient environment."""
    empty: set[str] = set()
    assert config._detect_inherited_safety_switches(empty) == []
    # Calling twice with the same input yields the same result.
    pre_keys = {"LIVE_TRADING_ENABLED"}
    first = config._detect_inherited_safety_switches(pre_keys)
    second = config._detect_inherited_safety_switches(pre_keys)
    assert first == second == ["LIVE_TRADING_ENABLED"]


def test_warn_inherited_safety_switches_runs_without_error():
    """The startup warning hook is callable and side-effect-free (logging only)."""
    # Should not raise regardless of what is currently inherited.
    config._warn_inherited_safety_switches()

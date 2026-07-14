"""
Credential-loading hardening (2026-07-14 env-wipe / 40006 incident).

A redeploy wiped .env; the Bitget key was re-entered wearing quotes or a
trailing space, sent verbatim, and rejected 40006 — leaving a live
position unprotected. `_env_secret` strips exactly those two footguns so
a hand-edited .env can no longer silently break auth. A boot-time
credential preflight surfaces any residual auth failure loudly.
"""

import importlib
import inspect

import pytest


def _reload_config():
    import bot.config as cfg
    importlib.reload(cfg)
    return cfg


class TestEnvSecret:
    def test_strips_surrounding_double_quotes(self, monkeypatch):
        monkeypatch.setenv("RC_TEST_SECRET", '"bg_abc123"')
        cfg = _reload_config()
        assert cfg._env_secret("RC_TEST_SECRET") == "bg_abc123"

    def test_strips_surrounding_single_quotes(self, monkeypatch):
        monkeypatch.setenv("RC_TEST_SECRET", "'bg_abc123'")
        cfg = _reload_config()
        assert cfg._env_secret("RC_TEST_SECRET") == "bg_abc123"

    def test_strips_whitespace_and_trailing_newline(self, monkeypatch):
        monkeypatch.setenv("RC_TEST_SECRET", "  bg_abc123 \n")
        cfg = _reload_config()
        assert cfg._env_secret("RC_TEST_SECRET") == "bg_abc123"

    def test_quotes_and_whitespace_together(self, monkeypatch):
        monkeypatch.setenv("RC_TEST_SECRET", '  "bg_abc123"  ')
        cfg = _reload_config()
        assert cfg._env_secret("RC_TEST_SECRET") == "bg_abc123"

    def test_interior_characters_untouched(self, monkeypatch):
        # a real key can contain quotes/spaces INSIDE — never strip those
        monkeypatch.setenv("RC_TEST_SECRET", 'ab"c d')
        cfg = _reload_config()
        assert cfg._env_secret("RC_TEST_SECRET") == 'ab"c d'

    def test_mismatched_quotes_not_stripped(self, monkeypatch):
        monkeypatch.setenv("RC_TEST_SECRET", '"bg_abc123')  # only leading
        cfg = _reload_config()
        assert cfg._env_secret("RC_TEST_SECRET") == '"bg_abc123'

    def test_only_quotes_collapses_to_empty(self, monkeypatch):
        monkeypatch.setenv("RC_TEST_SECRET", '""')
        cfg = _reload_config()
        assert cfg._env_secret("RC_TEST_SECRET") == ""

    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("RC_TEST_SECRET", raising=False)
        cfg = _reload_config()
        assert cfg._env_secret("RC_TEST_SECRET", "fallback") == "fallback"

    def test_credential_fields_use_env_secret(self):
        # source pin: the money keys must go through the hardened loader,
        # not raw _env (which would re-open the 40006 footgun).
        import bot.config as cfg
        src = inspect.getsource(cfg)
        for var in ("BITGET_API_KEY", "BITGET_API_SECRET",
                    "BITGET_PASSPHRASE", "TELEGRAM_BOT_TOKEN"):
            assert f'_env_secret("{var}")' in src, var
            assert f'_env("{var}")' not in src, f"{var} still uses raw _env"


class TestPreflightWiring:
    def test_preflight_exists_and_is_wired(self):
        import bot.main as m
        assert hasattr(m, "_credential_preflight")
        run_src = inspect.getsource(m.run_telegram)
        assert "_credential_preflight(engine" in run_src

    def test_preflight_diagnoses_40006(self):
        import bot.main as m
        src = inspect.getsource(m._credential_preflight)
        # the actionable hint must name all three 40006 causes
        assert "40006" in src
        assert "quotes" in src.lower()
        assert "BITGET_SANDBOX" in src

    def test_preflight_never_blocks_startup(self):
        # it must be fail-open: sim/paper returns early, errors are swallowed
        import bot.main as m
        src = inspect.getsource(m._credential_preflight)
        assert "simulation_mode" in src
        assert "never raises" in src.lower() or "never blocks" in src.lower()


@pytest.fixture(autouse=True)
def _restore_config():
    # other suites import the module-level CONFIG; reload once after these
    # reload-heavy tests so they see the pristine module.
    yield
    _reload_config()

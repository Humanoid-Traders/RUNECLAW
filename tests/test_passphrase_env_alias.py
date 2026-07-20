"""
Bitget passphrase env-name alias.

Live incident (2026-07): an operator's .env had the passphrase under the legacy
name BITGET_API_PASSPHRASE while BITGET_PASSPHRASE was blank. The code only read
BITGET_PASSPHRASE, so the engine account silently failed auth ("bitget requires
password") and live positions were unprotected. _env_secret_any accepts either
spelling so the passphrase loads regardless.
"""

from bot.config import _env_secret_any

from tests._env_subprocess import run_py


def test_prefers_canonical_name(monkeypatch):
    monkeypatch.setenv("BITGET_PASSPHRASE", "canonical")
    monkeypatch.setenv("BITGET_API_PASSPHRASE", "legacy")
    assert _env_secret_any("BITGET_PASSPHRASE", "BITGET_API_PASSPHRASE") == "canonical"


def test_falls_back_to_legacy_name(monkeypatch):
    # The incident exactly: canonical blank, value under the legacy name.
    monkeypatch.setenv("BITGET_PASSPHRASE", "")
    monkeypatch.setenv("BITGET_API_PASSPHRASE", "corneliaXXXX")
    assert _env_secret_any("BITGET_PASSPHRASE", "BITGET_API_PASSPHRASE") == "corneliaXXXX"


def test_blank_when_neither_set(monkeypatch):
    monkeypatch.delenv("BITGET_PASSPHRASE", raising=False)
    monkeypatch.delenv("BITGET_API_PASSPHRASE", raising=False)
    assert _env_secret_any("BITGET_PASSPHRASE", "BITGET_API_PASSPHRASE") == ""


def test_strips_quotes_and_whitespace_like_env_secret(monkeypatch):
    monkeypatch.delenv("BITGET_PASSPHRASE", raising=False)
    monkeypatch.setenv("BITGET_API_PASSPHRASE", '  "quoted-pass"  ')
    assert _env_secret_any("BITGET_PASSPHRASE", "BITGET_API_PASSPHRASE") == "quoted-pass"


def test_exchange_config_uses_alias():
    # ExchangeConfig computes its default at class-definition time, so the
    # alias must be tested against a FRESH import — in a subprocess, never via
    # importlib.reload (reloading bot.config in-process replaces its class
    # objects and silently breaks later tests' monkeypatches).
    out = run_py(
        "from bot.config import ExchangeConfig\n"
        "print(ExchangeConfig().passphrase)",
        env_overrides={"BITGET_API_PASSPHRASE": "legacy-pass"},
        env_removals=("BITGET_PASSPHRASE",))
    assert out == "legacy-pass"

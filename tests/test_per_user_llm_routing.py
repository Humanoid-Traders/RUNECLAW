"""
Per-user BYOK LLM routing (BYOK Phase 2 — interactive /analyze).

When PER_USER_LLM_ENABLED is ON, the thesis for a command a user runs by hand
routes through THAT user's own provider key (from their encrypted settings)
instead of the operator's. Fail-open: no key / bad provider / un-buildable
client all fall back to the operator config. The continuous background scan is
unaffected. Tests cover the pure resolution + gating helpers in isolation.
"""

from types import SimpleNamespace
from unittest.mock import patch

from bot.core.analyzer import Analyzer
from bot.llm.provider import LLMConfig


def _analyzer():
    return Analyzer.__new__(Analyzer)


def _settings(provider="groq", key="gsk_userkey"):
    return SimpleNamespace(llm_provider=provider, llm_api_key=key)


class TestResolveUserConfig:
    def test_builds_config_from_user_key(self):
        with patch("bot.db.models.get_user_settings", return_value=_settings()):
            cfg = Analyzer._resolve_user_llm_config(123)
        assert isinstance(cfg, LLMConfig)
        assert cfg.api_key == "gsk_userkey"
        assert cfg.provider.value == "groq"

    def test_no_key_returns_none(self):
        with patch("bot.db.models.get_user_settings", return_value=_settings(key="")):
            assert Analyzer._resolve_user_llm_config(123) is None

    def test_unknown_provider_returns_none(self):
        with patch("bot.db.models.get_user_settings",
                   return_value=_settings(provider="not-a-provider")):
            assert Analyzer._resolve_user_llm_config(123) is None

    def test_db_error_returns_none(self):
        with patch("bot.db.models.get_user_settings", side_effect=RuntimeError("db down")):
            assert Analyzer._resolve_user_llm_config(123) is None


class TestMaybeUserClient:
    def _cfg(self, enabled):
        p = patch("bot.core.analyzer.CONFIG")
        m = p.start()
        m.analyzer.per_user_llm_enabled = enabled
        return p

    def test_disabled_returns_none(self):
        p = self._cfg(enabled=False)
        try:
            assert _analyzer()._maybe_user_client(123) == (None, None)
        finally:
            p.stop()

    def test_no_user_id_returns_none(self):
        p = self._cfg(enabled=True)
        try:
            assert _analyzer()._maybe_user_client(None) == (None, None)
        finally:
            p.stop()

    def test_routes_when_enabled_and_resolvable(self):
        p = self._cfg(enabled=True)
        fake_cfg = SimpleNamespace(provider=SimpleNamespace(value="groq"), model="x")
        sentinel = object()
        a = _analyzer()
        try:
            with patch.object(Analyzer, "_resolve_user_llm_config", staticmethod(lambda uid: fake_cfg)), \
                 patch.object(Analyzer, "_build_client_for_config", staticmethod(lambda cfg: sentinel)):
                client, cfg = a._maybe_user_client(123)
            assert client is sentinel
            assert cfg is fake_cfg
        finally:
            p.stop()

    def test_unbuildable_client_falls_back(self):
        p = self._cfg(enabled=True)
        fake_cfg = SimpleNamespace(provider=SimpleNamespace(value="groq"), model="x")
        a = _analyzer()
        try:
            with patch.object(Analyzer, "_resolve_user_llm_config", staticmethod(lambda uid: fake_cfg)), \
                 patch.object(Analyzer, "_build_client_for_config", staticmethod(lambda cfg: None)):
                assert a._maybe_user_client(123) == (None, None)
        finally:
            p.stop()

    def test_resolver_error_fails_open(self):
        p = self._cfg(enabled=True)
        a = _analyzer()
        try:
            def _boom(uid):
                raise RuntimeError("resolve boom")
            with patch.object(Analyzer, "_resolve_user_llm_config", staticmethod(_boom)):
                assert a._maybe_user_client(123) == (None, None)
        finally:
            p.stop()

"""
LLM key health — registry, candidate selection, auto-healing (2026-07-11).

The recurring incident: multiple writable key slots (runtime BYOK /
ANTHROPIC_API_KEY / primary .env / tier keys), different resolution paths
reading different slots, and a stale key in any slot capturing the autonomous
call path forever with no visibility. key_health makes keys first-class:
validated up front, condemned on real 401s, and the admin Anthropic resolver
picks the first non-condemned candidate deterministically.
"""
import pytest

from bot.llm import key_health as kh
from bot.llm.provider import (BYOK, LLMConfig, LLMProvider, LLMTier,
                              resolve_tier_config)


@pytest.fixture(autouse=True)
def _clean():
    kh.reset()
    BYOK.reset()
    yield
    kh.reset()
    BYOK.reset()


# ── registry ─────────────────────────────────────────────────────────
def test_mark_and_query():
    kh.mark_invalid("sk-ant-bad", "401 invalid x-api-key")
    assert kh.is_known_invalid("sk-ant-bad") is True
    assert kh.status_of("sk-ant-bad") == kh.INVALID
    kh.mark_valid("sk-ant-good")
    assert kh.status_of("sk-ant-good") == kh.VALID
    assert kh.status_of("sk-ant-never-seen") == kh.UNCHECKED
    assert kh.is_known_invalid("") is False


def test_auth_error_classifier():
    assert kh.looks_like_auth_error(
        "Error code: 401 - {'type': 'authentication_error', "
        "'message': 'invalid x-api-key'}") is True
    assert kh.looks_like_auth_error("429 rate_limit_error") is False
    assert kh.looks_like_auth_error("gemini-2.5-pro quota exceeded") is False
    assert kh.looks_like_auth_error("") is False


def test_fingerprint_never_reveals_key():
    f = kh.fp("sk-ant-api03-SECRETSECRETSECRET")
    assert "SECRETSECRETSECRET" not in f
    assert f.startswith("sk-ant")


# ── candidate ordering + dedup ───────────────────────────────────────
def _anthropic_cfg(key):
    return LLMConfig(provider=LLMProvider.ANTHROPIC, api_key=key)


def test_candidate_order_byok_env_primary(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    runtime = _anthropic_cfg("sk-ant-runtime")
    primary = _anthropic_cfg("sk-ant-primary")
    cands = kh.anthropic_candidates(primary, runtime)
    assert [c[1] for c in cands] == ["sk-ant-runtime", "sk-ant-env", "sk-ant-primary"]


def test_candidates_dedup_same_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-same")
    primary = _anthropic_cfg("sk-ant-same")
    cands = kh.anthropic_candidates(primary, None)
    assert len(cands) == 1


def test_non_anthropic_runtime_excluded(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runtime = LLMConfig(provider=LLMProvider.GROQ, api_key="gsk-groq")
    assert kh.anthropic_candidates(None, runtime) == []


# ── auto-healing pick ────────────────────────────────────────────────
def test_pick_skips_condemned_key(monkeypatch):
    """THE incident: a bad runtime BYOK key 401s; the resolver must move to
    the valid env key instead of failing forever."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-good")
    runtime = _anthropic_cfg("sk-ant-runtime-bad")
    kh.mark_invalid("sk-ant-runtime-bad", "401")
    src, key = kh.pick_anthropic_key(None, runtime)
    assert key == "sk-ant-env-good"
    assert src == "ANTHROPIC_API_KEY"


def test_pick_fail_open_when_all_condemned(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-only")
    kh.mark_invalid("sk-ant-only", "401")
    src, key = kh.pick_anthropic_key(None, None)
    assert key == "sk-ant-only"   # fail-open: error re-surfaces, never keyless


# ── tier resolution wiring ───────────────────────────────────────────
def test_admin_tier_resolves_via_key_health(monkeypatch):
    """Primary provider == Anthropic used to SKIP the admin table entirely
    (step-2 guard), binding every tier to whatever key sat in the primary
    slot. Now the admin route picks the first healthy candidate."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-good")
    primary = _anthropic_cfg("sk-ant-primary-bad")
    kh.mark_invalid("sk-ant-primary-bad", "401 invalid x-api-key")
    cfg = resolve_tier_config(LLMTier.SCAN, primary, is_admin=True)
    assert cfg.provider == LLMProvider.ANTHROPIC
    assert cfg.api_key == "sk-ant-env-good"


def test_admin_tier_prefers_runtime_byok_when_healthy(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    BYOK._runtime_config = _anthropic_cfg("sk-ant-runtime")
    primary = _anthropic_cfg("sk-ant-primary")
    cfg = resolve_tier_config(LLMTier.THESIS, primary, is_admin=True)
    assert cfg.api_key == "sk-ant-runtime"


def test_non_admin_still_blocked(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    primary = LLMConfig(provider=LLMProvider.GEMINI, api_key="g-key")
    cfg = resolve_tier_config(LLMTier.SCAN, primary, is_admin=False)
    assert cfg.provider != LLMProvider.ANTHROPIC


# ── validator: Models API path (free, checks account model list) ─────
def _fake_models_sdk(monkeypatch, model_ids=None, list_exc=None):
    """Install a fake anthropic SDK whose client.models.list() returns the
    given model ids (or raises list_exc)."""
    import sys
    import types

    class _Model:
        def __init__(self, mid):
            self.id = mid

    class _Models:
        @staticmethod
        def list(**kw):
            if list_exc is not None:
                raise list_exc
            page = types.SimpleNamespace()
            page.data = [_Model(m) for m in (model_ids or [])]
            return page

    class _Client:
        def __init__(self, **kw):
            self.models = _Models()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", fake)


def test_validator_models_api_success(monkeypatch):
    _fake_models_sdk(monkeypatch, model_ids=["claude-sonnet-4-6",
                                             "claude-opus-4-8"])
    status, detail = kh.validate_anthropic_key(
        "sk-ant-good", model="claude-sonnet-4-6")
    assert status == kh.VALID
    assert kh.status_of("sk-ant-good") == kh.VALID


def test_validator_models_api_model_not_on_account(monkeypatch):
    """The claude-sonnet-4-20250514 incident: key authenticates but the
    configured model does not exist on this account. Config is refused
    (INVALID) but the KEY is not condemned — it's fine with a real model."""
    _fake_models_sdk(monkeypatch, model_ids=["claude-sonnet-4-6"])
    status, detail = kh.validate_anthropic_key(
        "sk-ant-good", model="claude-sonnet-4-20250514")
    assert status == kh.INVALID
    assert "not available" in detail
    assert "claude-sonnet-4-6" in detail          # shows what IS available
    assert kh.is_known_invalid("sk-ant-good") is False
    assert kh.status_of("sk-ant-good") == kh.VALID


def test_validator_models_api_auth_error(monkeypatch):
    _fake_models_sdk(monkeypatch, list_exc=Exception(
        "Error code: 401 - {'type': 'authentication_error', "
        "'message': 'invalid x-api-key'}"))
    status, _ = kh.validate_anthropic_key("sk-ant-bad")
    assert status == kh.INVALID
    assert kh.is_known_invalid("sk-ant-bad") is True


# ── validator classification (old SDK fallback: messages.create) ─────
def test_validator_classifies_auth_error(monkeypatch):
    class _Boom:
        def __init__(self, **kw): ...
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                raise Exception(
                    "Error code: 401 - {'type': 'authentication_error'}")
    import sys, types
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **kw: _Boom()
    _Boom.messages = _Boom.messages
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    status, detail = kh.validate_anthropic_key("sk-ant-bad")
    assert status == kh.INVALID
    assert kh.is_known_invalid("sk-ant-bad") is True


def test_validator_transient_error_not_condemned(monkeypatch):
    class _Net:
        def __init__(self, **kw): ...
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                raise Exception("connection timed out")
    import sys, types
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **kw: _Net()
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    status, _ = kh.validate_anthropic_key("sk-ant-maybe")
    assert status == kh.UNCHECKED
    assert kh.is_known_invalid("sk-ant-maybe") is False


def test_validator_success_marks_valid(monkeypatch):
    class _OK:
        def __init__(self, **kw): ...
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                return object()
    import sys, types
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **kw: _OK()
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    status, _ = kh.validate_anthropic_key("sk-ant-good")
    assert status == kh.VALID
    assert kh.status_of("sk-ant-good") == kh.VALID


# ── analyzer auto-condemn wiring (source pin) ────────────────────────
def test_analyzer_marks_key_on_auth_failure():
    import inspect
    from bot.core.analyzer import Analyzer
    src = inspect.getsource(Analyzer)
    assert "looks_like_auth_error" in src
    assert "mark_invalid" in src

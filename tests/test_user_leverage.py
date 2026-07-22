"""NB3: per-user leverage preference for BYOK live users.

resolve_user_leverage is reduce-only (a user pref never exceeds the operator
cap); the store persists per-user with fail-safe reads/writes.
"""

import os

import pytest

from bot.core.leverage import resolve_user_leverage, describe_user_leverage
from bot.core import user_leverage_store as store


class TestResolve:
    def test_none_pref_uses_cap(self):
        assert resolve_user_leverage(None, 5) == 5

    def test_user_can_reduce(self):
        assert resolve_user_leverage(3, 5) == 3
        assert resolve_user_leverage(1, 5) == 1

    def test_user_cannot_exceed_cap(self):
        assert resolve_user_leverage(10, 5) == 5
        assert resolve_user_leverage(125, 5) == 5

    def test_min_floor(self):
        assert resolve_user_leverage(1, 5, min_lev=2) == 2
        # min is itself capped so it can never exceed the operator cap
        assert resolve_user_leverage(1, 3, min_lev=10) == 3

    def test_garbage_pref_falls_back_to_cap(self):
        for bad in ("x", None, "", -4, 0, [1]):
            assert resolve_user_leverage(bad, 5) == 5

    def test_garbage_cap_is_safe(self):
        assert resolve_user_leverage(3, "nope") == 1

    def test_describe(self):
        assert "operator default" in describe_user_leverage(None, 5)
        assert describe_user_leverage(3, 5) == "3x (your preference)"
        assert "capped" in describe_user_leverage(10, 5)


class TestStore:
    @pytest.fixture(autouse=True)
    def _tmp_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
        yield

    def test_set_get_roundtrip(self):
        assert store.set_pref("u1", 3) == 3
        assert store.get("u1") == 3

    def test_get_absent_is_none(self):
        assert store.get("nobody") is None

    def test_set_rejects_bad_values(self):
        assert store.set_pref("u2", "abc") is None
        assert store.set_pref("u2", 0) is None
        assert store.set_pref("", 3) is None
        assert store.get("u2") is None

    def test_clear(self):
        store.set_pref("u3", 4)
        assert store.get("u3") == 4
        assert store.clear("u3") is True
        assert store.get("u3") is None
        assert store.clear("u3") is False  # already gone

    def test_persists_across_calls_same_dir(self, tmp_path):
        store.set_pref("u4", 2)
        # a fresh read hits the same file (no in-memory cache to trust)
        assert store.get("u4") == 2
        assert os.path.exists(os.path.join(str(tmp_path), "user_leverage.json"))

"""
Per-user margin cap.

An operator can cap how much margin a regular user may commit to a single live
trade (`/setcap <id> <usd|off>`). It is tighten-only: folded into the existing
position cap with a min(), so it can only REDUCE the size the risk engine already
sized and capped — never raise it above the global micro cap. Applies only under
per-user live to a regular (non-operator) user; default (no cap set) is a no-op.
"""

import os
import tempfile
from unittest.mock import patch

from bot.core.engine import RuneClawEngine
from bot.utils.user_store import UserStore


# ── UserStore storage ───────────────────────────────────────────────

def _store():
    path = os.path.join(tempfile.mkdtemp(), "users.json")
    return UserStore(path=path)


class TestUserStoreMaxMargin:
    def test_unset_returns_none(self):
        s = _store()
        s.register("123", name="u")
        assert s.max_margin("123") is None

    def test_set_and_get(self):
        s = _store()
        s.register("123", name="u")
        assert s.set_max_margin("123", 50.0) is True
        assert s.max_margin("123") == 50.0

    def test_clear_with_none(self):
        s = _store()
        s.register("123", name="u")
        s.set_max_margin("123", 50.0)
        s.set_max_margin("123", None)
        assert s.max_margin("123") is None

    def test_set_unknown_user_fails(self):
        s = _store()
        assert s.set_max_margin("999", 50.0) is False

    def test_non_positive_stored_value_returns_none(self):
        s = _store()
        s.register("123", name="u")
        s.set_max_margin("123", 0.0)  # stored, but treated as "no cap"
        assert s.max_margin("123") is None

    def test_persists_across_reload(self):
        path = os.path.join(tempfile.mkdtemp(), "users.json")
        s1 = UserStore(path=path)
        s1.register("123", name="u")
        s1.set_max_margin("123", 75.0)
        s2 = UserStore(path=path)
        assert s2.max_margin("123") == 75.0


# ── engine._per_user_margin_cap ─────────────────────────────────────

class _FakeStore:
    def __init__(self, caps=None, raises=False):
        self._caps = dict(caps or {})
        self._raises = raises

    def max_margin(self, uid):
        if self._raises:
            raise RuntimeError("store boom")
        return self._caps.get(str(uid))


def _cfg(per_user=True):
    p = patch("bot.core.engine.CONFIG")
    m = p.start()
    m.per_user_live_enabled = per_user
    return p


def _engine(store=None, operator_ids=()):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._user_store = store
    eng._is_operator_user = lambda uid: str(uid) in {str(x) for x in operator_ids}
    return eng


class TestEngineMarginCap:
    def test_returns_cap_for_regular_user(self):
        p = _cfg(per_user=True)
        try:
            eng = _engine(_FakeStore({"alice": 50.0}))
            assert eng._per_user_margin_cap("alice") == 50.0
        finally:
            p.stop()

    def test_none_when_unset(self):
        p = _cfg(per_user=True)
        try:
            assert _engine(_FakeStore({}))._per_user_margin_cap("alice") is None
        finally:
            p.stop()

    def test_none_when_per_user_off(self):
        p = _cfg(per_user=False)
        try:
            eng = _engine(_FakeStore({"alice": 50.0}))
            assert eng._per_user_margin_cap("alice") is None
        finally:
            p.stop()

    def test_none_for_operator_user(self):
        p = _cfg(per_user=True)
        try:
            eng = _engine(_FakeStore({"999": 50.0}), operator_ids=["999"])
            assert eng._per_user_margin_cap("999") is None
        finally:
            p.stop()

    def test_none_for_auto_and_empty(self):
        p = _cfg(per_user=True)
        try:
            eng = _engine(_FakeStore({"auto": 50.0}))
            assert eng._per_user_margin_cap("auto") is None
            assert eng._per_user_margin_cap("") is None
        finally:
            p.stop()

    def test_fail_open_on_store_error(self):
        p = _cfg(per_user=True)
        try:
            eng = _engine(_FakeStore(raises=True))
            assert eng._per_user_margin_cap("alice") is None
        finally:
            p.stop()

    def test_none_when_no_store(self):
        p = _cfg(per_user=True)
        try:
            assert _engine(None)._per_user_margin_cap("alice") is None
        finally:
            p.stop()

    def test_tighten_only_semantics(self):
        # The cap is combined with the global cap via min() at the call site;
        # verify the helper returns the raw cap so min() can only reduce.
        p = _cfg(per_user=True)
        try:
            eng = _engine(_FakeStore({"alice": 10.0}))
            cap = eng._per_user_margin_cap("alice")
            global_cap = 100.0
            assert min(global_cap, cap) == 10.0   # user cap tightens
            assert min(5.0, cap) == 5.0            # global already tighter wins
        finally:
            p.stop()

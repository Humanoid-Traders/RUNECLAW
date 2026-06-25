"""
Regression tests for the V5 follow-up auth fixes (docs/AUDIT_REPORT_V5.md):

  RC-AUD-020 — JWT revocation / refresh-reuse:
    * /auth/logout bumps a per-user token epoch, so a token issued before the
      bump fails _verify while a token issued after still passes.
    * a refresh token can be exchanged exactly once; a replay is rejected.
  RC-AUD-026 — per-account (per-email) failed-login lockout:
    * N failed attempts against one account → that account is locked (429),
      while an untouched account is unaffected; a success resets the counter.

Tests call the helpers in bot/api/auth_routes.py directly (no DB / TestClient).
"""
import os

# auth_routes raises RuntimeError at import time if JWT_SECRET is unset or is the
# hardcoded default, so it MUST be set before the import below.
os.environ.setdefault("JWT_SECRET", "0" * 64)

import pytest  # noqa: E402

from bot.api import auth_routes as ar  # noqa: E402


# Use unique user ids / emails per test: the revocation epoch dict, the
# consumed-jti set, and the per-account failure dicts are module-level and
# persist for the whole test process, so sharing keys would leak state.
_uid_counter = [9_000_000]


def _fresh_uid() -> int:
    _uid_counter[0] += 1
    return _uid_counter[0]


# ── RC-AUD-020: token round-trip baseline ───────────────────────────

def test_access_token_round_trips_with_jti_and_ver():
    uid = _fresh_uid()
    tok = ar.create_jwt(uid, token_type="access")
    payload = ar._verify(tok)
    assert payload is not None
    assert payload["sub"] == uid
    assert payload["type"] == "access"
    assert payload.get("jti")            # unique token id present
    assert "ver" in payload             # epoch/version stamped


# ── RC-AUD-020: logout (epoch bump) revokes outstanding tokens ──────

def test_logout_revokes_existing_tokens():
    uid = _fresh_uid()
    tok = ar.create_jwt(uid, token_type="access")
    assert ar._verify(tok) is not None   # valid before logout

    # /auth/logout body: bump the user's token epoch.
    new_epoch = ar._revoke_user_tokens(uid)
    assert new_epoch >= 1

    # The previously-issued token is now stale (ver < epoch) → rejected.
    assert ar._verify(tok) is None

    # A token minted AFTER the bump carries the new epoch and still verifies.
    tok2 = ar.create_jwt(uid, token_type="access")
    fresh = ar._verify(tok2)
    assert fresh is not None
    assert fresh["sub"] == uid


def test_logout_is_scoped_to_one_user():
    uid_a = _fresh_uid()
    uid_b = _fresh_uid()
    tok_a = ar.create_jwt(uid_a, token_type="access")
    tok_b = ar.create_jwt(uid_b, token_type="access")

    ar._revoke_user_tokens(uid_a)        # only A logs out

    assert ar._verify(tok_a) is None     # A's token revoked
    assert ar._verify(tok_b) is not None  # B's token unaffected


# ── RC-AUD-020: refresh-token reuse detection ───────────────────────

def test_refresh_reuse_rejected():
    uid = _fresh_uid()
    rt = ar.create_jwt(uid, token_type="refresh")
    payload = ar._verify(rt)
    assert payload is not None
    assert payload["type"] == "refresh"

    # First exchange is allowed and records the jti as consumed.
    assert ar._check_and_record_refresh(payload) is True
    # A replay of the same refresh token is rejected.
    assert ar._check_and_record_refresh(payload) is False


def test_distinct_refresh_tokens_both_consumable_once():
    uid = _fresh_uid()
    rt1 = ar.create_jwt(uid, token_type="refresh")
    rt2 = ar.create_jwt(uid, token_type="refresh")
    p1 = ar._verify(rt1)
    p2 = ar._verify(rt2)
    assert p1 and p2 and p1["jti"] != p2["jti"]
    assert ar._check_and_record_refresh(p1) is True
    assert ar._check_and_record_refresh(p2) is True
    # Each is now spent.
    assert ar._check_and_record_refresh(p1) is False
    assert ar._check_and_record_refresh(p2) is False


# ── RC-AUD-026: per-account failed-login lockout ────────────────────

def test_per_account_lockout_after_threshold():
    email = f"stuffing-{_fresh_uid()}@example.com"

    # Not locked initially.
    ar._check_account_lockout(email)  # should not raise

    # Record failures up to the threshold. The final recording raises 429
    # (threshold crossed). Earlier ones do not raise.
    raised = False
    for _ in range(ar._ACCT_MAX_FAILURES):
        try:
            ar._record_account_failure(email)
        except Exception as exc:  # HTTPException at the threshold-crossing call
            assert getattr(exc, "status_code", None) == 429
            raised = True
    assert raised, "expected a 429 once the per-account threshold was reached"

    # Subsequent lockout checks now raise 429 for this account.
    with pytest.raises(Exception) as ei:
        ar._check_account_lockout(email)
    assert getattr(ei.value, "status_code", None) == 429


def test_per_account_lockout_does_not_affect_other_accounts():
    locked = f"locked-{_fresh_uid()}@example.com"
    other = f"other-{_fresh_uid()}@example.com"

    for _ in range(ar._ACCT_MAX_FAILURES):
        try:
            ar._record_account_failure(locked)
        except Exception:
            pass

    # Locked account is throttled...
    with pytest.raises(Exception) as ei:
        ar._check_account_lockout(locked)
    assert getattr(ei.value, "status_code", None) == 429

    # ...but an untouched account is fine.
    ar._check_account_lockout(other)  # must not raise


def test_per_account_success_resets_counter():
    email = f"resetme-{_fresh_uid()}@example.com"

    # A couple of failures below the threshold.
    ar._record_account_failure(email)
    ar._record_account_failure(email)

    # Successful login clears the counter + any lockout.
    ar._reset_account_failures(email)

    # Counter is back to empty: it now takes the FULL threshold again to lock.
    raised_early = False
    for _ in range(ar._ACCT_MAX_FAILURES - 1):
        try:
            ar._record_account_failure(email)
        except Exception:
            raised_early = True
    assert not raised_early, "reset did not clear prior failures"
    # Still not locked at threshold-1.
    ar._check_account_lockout(email)  # must not raise


def test_email_normalization_shares_one_bucket():
    base = f"Case-{_fresh_uid()}@Example.com"
    variants = [base, base.lower(), f"  {base}  "]
    # Distribute failures across case/whitespace variants of the same email.
    raised = False
    for i in range(ar._ACCT_MAX_FAILURES):
        try:
            ar._record_account_failure(variants[i % len(variants)])
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 429
            raised = True
    assert raised, "case/whitespace variants must share one lockout bucket"
    # Any-cased lookup is now locked.
    with pytest.raises(Exception) as ei:
        ar._check_account_lockout(base.upper())
    assert getattr(ei.value, "status_code", None) == 429


# ── RC-AUD-020 (V5.2): durable TokenStore — Redis path + fallback ────

class _FakeRedis:
    """Minimal in-memory stand-in for the sync redis.Redis surface we use."""

    def __init__(self):
        self.kv: dict = {}

    def get(self, k):
        return self.kv.get(k)

    def incr(self, k):
        self.kv[k] = int(self.kv.get(k, 0)) + 1
        return self.kv[k]

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    def ping(self):
        return True


def _store_with_redis(redis_client):
    """Build a TokenStore with an injected redis client (bypass env connect)."""
    import collections

    from bot.api.token_store import TokenStore
    store = TokenStore.__new__(TokenStore)
    store._epoch = collections.defaultdict(int)
    store._consumed_jti = set()
    store._redis = redis_client
    return store


def test_token_store_uses_redis_when_present():
    fake = _FakeRedis()
    store = _store_with_redis(fake)
    assert store.backend == "redis"
    assert store.get_epoch(123) == 0
    assert store.bump_epoch(123) == 1
    assert store.get_epoch(123) == 1
    assert store.try_consume_jti("jti-x", 60) is True
    assert store.try_consume_jti("jti-x", 60) is False
    # State actually lives in the (fake) redis — proves the redis path was taken.
    assert fake.kv.get("rc:jwt:epoch:123") == 1
    assert fake.kv.get("rc:jwt:jti:jti-x") == "1"


def test_token_store_falls_back_when_redis_errors():
    class _BrokenRedis:
        def get(self, k):
            raise RuntimeError("redis down")

        def incr(self, k):
            raise RuntimeError("redis down")

        def set(self, k, v, nx=False, ex=None):
            raise RuntimeError("redis down")

    store = _store_with_redis(_BrokenRedis())
    # Every op falls back to in-process WITHOUT raising (fail toward availability).
    assert store.get_epoch(7) == 0
    assert store.bump_epoch(7) == 1
    assert store.get_epoch(7) == 1
    assert store.try_consume_jti("j", 60) is True
    assert store.try_consume_jti("j", 60) is False

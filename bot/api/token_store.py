"""RC-AUD-020: durable JWT-revocation store with optional Redis backing.

The auth layer keeps two pieces of revocation state:
  - a per-user token *epoch* (bumped on logout → invalidates every prior token);
  - the set of *consumed refresh-token jtis* (single-use refresh / replay guard).

In-process dicts are correct for a single process but do NOT span multiple
uvicorn workers / replicas and do NOT survive a restart. This module backs that
state with Redis (already provisioned in docker-compose) **when a Redis endpoint
is configured**, and otherwise — and on any Redis error — falls back to the
in-process dicts.

Design notes:
  * Synchronous on purpose: the auth helpers (`_verify`, `create_jwt`,
    `_revoke_user_tokens`, `_check_and_record_refresh`) are sync, so this uses the
    sync `redis.Redis` client with short socket timeouts. The auth path is
    low-frequency, so the brief blocking call is acceptable.
  * **Fail toward availability:** revocation durability is best-effort; if a Redis
    call raises (e.g. Redis briefly down) we fall back to the in-process value for
    that call and log — auth must never hard-break on a Redis blip. The
    consequence is that, *during a Redis outage*, a revoke performed against Redis
    may not be enforced by a worker that fell back to its (empty) in-process
    state. That is the explicit trade-off vs. failing auth closed.
  * With no Redis configured (the default, and in tests), the in-process dicts are
    the sole backend and behave exactly as before this change.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

_EPOCH_KEY = "rc:jwt:epoch:"   # + user_id  → integer epoch (INCR/GET)
_JTI_KEY = "rc:jwt:jti:"       # + jti      → "1" with TTL (SET NX EX)


class TokenStore:
    """Per-user token epoch + consumed-jti store, Redis-backed when configured."""

    def __init__(self) -> None:
        # In-process backend — the default, the test backend, and the fallback.
        self._epoch: dict[int, int] = defaultdict(int)
        self._consumed_jti: set[str] = set()
        self._redis = self._maybe_connect_redis()

    @staticmethod
    def _maybe_connect_redis():
        """Return a connected sync Redis client, or None to use in-process only."""
        url = os.getenv("REDIS_URL", "").strip()
        host = os.getenv("REDIS_HOST", "").strip()
        if not url and not host:
            return None  # Redis not configured → in-process only (default).
        try:
            import redis  # sync client; optional dependency
        except Exception as exc:  # pragma: no cover - import guard
            logger.warning(
                "redis package not installed — JWT revocation stays in-process: %s", exc
            )
            return None
        try:
            if url:
                client = redis.Redis.from_url(
                    url, socket_timeout=2, socket_connect_timeout=2,
                    decode_responses=True,
                )
            else:
                client = redis.Redis(
                    host=host or "localhost",
                    port=int(os.getenv("REDIS_PORT", "6379") or 6379),
                    password=os.getenv("REDIS_PASSWORD") or None,
                    socket_timeout=2, socket_connect_timeout=2,
                    decode_responses=True,
                )
            client.ping()
            logger.info("JWT revocation store: Redis backend active")
            return client
        except Exception as exc:
            logger.warning(
                "Redis unavailable — JWT revocation falls back to in-process: %s", exc
            )
            return None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "in-process"

    def get_epoch(self, user_id: int) -> int:
        """Current token epoch for a user (0 if never revoked)."""
        if self._redis is not None:
            try:
                v = self._redis.get(f"{_EPOCH_KEY}{user_id}")
                return int(v) if v is not None else 0
            except Exception as exc:
                logger.warning("Redis get_epoch failed, using in-process: %s", exc)
        return self._epoch.get(user_id, 0)

    def bump_epoch(self, user_id: int) -> int:
        """Increment the user's epoch (revokes all prior tokens). Returns new epoch."""
        if self._redis is not None:
            try:
                return int(self._redis.incr(f"{_EPOCH_KEY}{user_id}"))
            except Exception as exc:
                logger.warning("Redis bump_epoch failed, using in-process: %s", exc)
        self._epoch[user_id] += 1
        return self._epoch[user_id]

    def try_consume_jti(self, jti: str, ttl_seconds: int) -> bool:
        """Record a refresh jti as consumed. Returns True if newly consumed,
        False if it was already consumed (a replay)."""
        ttl = max(1, int(ttl_seconds))
        if self._redis is not None:
            try:
                # SET key 1 NX EX ttl → truthy if newly set, None if it existed.
                ok = self._redis.set(f"{_JTI_KEY}{jti}", "1", nx=True, ex=ttl)
                return bool(ok)
            except Exception as exc:
                logger.warning("Redis try_consume_jti failed, using in-process: %s", exc)
        if jti in self._consumed_jti:
            return False
        self._consumed_jti.add(jti)
        return True


_store: Optional[TokenStore] = None


def get_token_store() -> TokenStore:
    """Process-wide singleton token store."""
    global _store
    if _store is None:
        _store = TokenStore()
    return _store


def ttl_from_exp(payload: dict) -> int:
    """Seconds until a token's `exp`, floored at 1 (used to expire consumed jtis)."""
    return max(1, int(payload.get("exp", 0) - time.time()))

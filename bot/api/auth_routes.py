"""
RUNECLAW -- Auth API routes.
File: bot/api/auth_routes.py

Mount into api_bridge.py:
    from bot.api.auth_routes import auth_router
    app.include_router(auth_router, prefix="/auth")

Endpoints:
  POST /auth/register    -- create account, return JWT
  POST /auth/login       -- verify credentials, return JWT
  POST /auth/refresh     -- rotate refresh token -> new access+refresh pair
  POST /auth/logout      -- revoke all of the caller's tokens (authenticated)
  POST /auth/link-token  -- generate a Telegram link token (authenticated)
  GET  /auth/me          -- return current user info (authenticated)
  POST /auth/unlink      -- disconnect Telegram from account (authenticated)
"""

from __future__ import annotations
import os, time, hmac, hashlib, json, base64, secrets
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from bot.db.models import (
    create_user, authenticate_user, get_user_by_id,
    create_link_token, get_user_portfolio, get_user_settings,
    unlink_telegram,
)

auth_router = APIRouter()
security = HTTPBearer(auto_error=False)

# -- AUDIT FIX: Auth rate-limiter & failed-login lockout ----------------------
#
# NOTE (multi-worker caveat): all of the dicts/sets below are *in-process*. They
# do NOT span multiple uvicorn workers / replicas and do NOT survive a restart.
# For a real multi-worker / multi-replica deployment, back these with a shared
# store -- Redis is already provisioned in docker-compose.yml -- so that limits,
# lockouts and token revocation are shared and persistent.

_AUTH_WINDOW_SEC = 60          # sliding window
_AUTH_MAX_ATTEMPTS = 5         # max attempts per IP per window
_LOCKOUT_SEC = 300             # 5-min lockout after exceeding
_auth_attempts: dict[str, list[float]] = defaultdict(list)
_auth_lockouts: dict[str, float] = {}

# RC-AUD-026: per-ACCOUNT (per-email) failed-login throttle. The per-IP limiter
# above does nothing against distributed / rotating-IP credential stuffing aimed
# at a *single* account, so we additionally track failures keyed by email.
_ACCT_MAX_FAILURES = 5         # failed logins per email per window before lockout
_acct_failures: dict[str, list[float]] = defaultdict(list)
_acct_lockouts: dict[str, float] = {}


def _check_auth_rate_limit(request: Request) -> None:
    """Per-IP rate limit for auth endpoints. Raises 429 if exceeded."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    # Check lockout
    if ip in _auth_lockouts:
        if now < _auth_lockouts[ip]:
            raise HTTPException(
                status_code=429,
                detail=f"Too many attempts. Try again in {int(_auth_lockouts[ip] - now)}s.",
            )
        else:
            del _auth_lockouts[ip]
    # Prune old attempts
    _auth_attempts[ip] = [t for t in _auth_attempts[ip] if now - t < _AUTH_WINDOW_SEC]
    if len(_auth_attempts[ip]) >= _AUTH_MAX_ATTEMPTS:
        _auth_lockouts[ip] = now + _LOCKOUT_SEC
        _auth_attempts[ip].clear()
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Locked out for {_LOCKOUT_SEC}s.",
        )
    _auth_attempts[ip].append(now)


def _norm_email(email: str) -> str:
    """Normalize an email the same way the DB layer does, so the per-account
    counter cannot be split into separate buckets by case/whitespace."""
    return (email or "").lower().strip()


def _check_account_lockout(email: str) -> None:
    """RC-AUD-026: raise 429 if this account is currently locked out. Checks
    only -- it does NOT record an attempt (recording happens on failure so a
    successful login never accrues toward a lockout)."""
    key = _norm_email(email)
    now = time.time()
    if key in _acct_lockouts:
        if now < _acct_lockouts[key]:
            raise HTTPException(
                status_code=429,
                detail=f"Account temporarily locked. Try again in {int(_acct_lockouts[key] - now)}s.",
            )
        else:
            del _acct_lockouts[key]


def _record_account_failure(email: str) -> None:
    """RC-AUD-026: record a failed login for this account. If failures within
    the sliding window exceed the threshold, lock the account and raise 429."""
    key = _norm_email(email)
    now = time.time()
    _acct_failures[key] = [t for t in _acct_failures[key] if now - t < _AUTH_WINDOW_SEC]
    _acct_failures[key].append(now)
    if len(_acct_failures[key]) >= _ACCT_MAX_FAILURES:
        _acct_lockouts[key] = now + _LOCKOUT_SEC
        _acct_failures[key].clear()
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed logins for this account. Locked for {_LOCKOUT_SEC}s.",
        )


def _reset_account_failures(email: str) -> None:
    """RC-AUD-026: clear failure counter + lockout on a successful login."""
    key = _norm_email(email)
    _acct_failures.pop(key, None)
    _acct_lockouts.pop(key, None)

# -- JWT (stdlib only -- no PyJWT dependency) --------------------------------

_HARDCODED_DEFAULT = "change_this_to_a_random_secret_in_env"
JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET or JWT_SECRET == _HARDCODED_DEFAULT:
    raise RuntimeError(
        "FATAL: JWT_SECRET environment variable is not set or still uses the "
        "hardcoded default. The server CANNOT start without a secure secret. "
        "Generate one with:\n"
        "  python3 -c \"import secrets; print(secrets.token_hex(32))\"\n"
        "and set it as JWT_SECRET in your .env file."
    )
JWT_ACCESS_TTL = 60 * 60          # 1 hour
JWT_REFRESH_TTL = 60 * 60 * 24 * 7  # 7 days

# -- RC-AUD-020: in-process token revocation -------------------------------
#
# Base verification only checks signature + `exp`, so a leaked token is valid
# until expiry and a 7-day refresh token can be rolled forward forever with no
# reuse detection. We add three pragmatic, dependency-free mechanisms:
#
#   1. Per-user "token epoch" (a monotonically increasing integer). Every issued
#      token is stamped with the user's current epoch in a `ver` claim. _verify
#      rejects any token whose `ver` is BELOW the user's current epoch. Bumping
#      the epoch (see /auth/logout) therefore revokes ALL of that user's
#      outstanding access + refresh tokens at once.
#   2. A unique `jti` on every token (token id), used for refresh-reuse tracking.
#   3. Refresh-token rotation + reuse detection: when a refresh token is
#      exchanged we record its `jti` as consumed; a replayed refresh token whose
#      jti is already consumed is rejected.
#
# RC-AUD-020 (V5.2): the per-user epoch + consumed-jti state now lives in
# bot/api/token_store.py, which is Redis-backed when a Redis endpoint is
# configured (durable across uvicorn workers / replicas / restarts) and falls
# back to in-process dicts otherwise. The helpers below delegate to it; their
# signatures/semantics are unchanged.
from bot.api.token_store import get_token_store, ttl_from_exp


def _revoke_user_tokens(user_id: int) -> int:
    """Bump the user's token epoch, invalidating every previously-issued token
    for that user (used by /auth/logout). Returns the new epoch."""
    return get_token_store().bump_epoch(user_id)


def _check_and_record_refresh(payload: dict) -> bool:
    """RC-AUD-020: refresh-reuse detection. Returns True if this refresh token
    may be consumed (and records its jti as consumed); returns False if the jti
    has already been consumed (a replayed/rotated-away refresh token).

    Tokens minted before this change may lack a `jti`; such tokens are allowed
    once but cannot be replay-detected (best effort, documented limitation)."""
    jti = payload.get("jti")
    if not jti:
        return True
    return get_token_store().try_consume_jti(jti, ttl_from_exp(payload))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(payload: dict) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps(payload).encode())
    sig = hmac.new(
        JWT_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256
    ).digest()
    return f"{header}.{body}.{_b64(sig)}"


def _verify(token: str) -> Optional[dict]:
    try:
        header, body, sig = token.split(".")
        expected = hmac.new(
            JWT_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_b64(expected), sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=="))
        if payload.get("exp", 0) < time.time():
            return None
        # RC-AUD-020: reject tokens issued before the user's current epoch.
        # A token whose `ver` is below the user's epoch was revoked (e.g. by a
        # logout that bumped the epoch). Missing `ver` is treated as 0, so a
        # legacy/untouched token still verifies until its user's epoch is bumped
        # -- this keeps existing token behavior intact until an explicit revoke.
        sub = payload.get("sub")
        if sub is not None and payload.get("ver", 0) < get_token_store().get_epoch(sub):
            return None
        return payload
    except Exception:
        return None


def create_jwt(user_id: int, *, token_type: str = "access") -> str:
    ttl = JWT_ACCESS_TTL if token_type == "access" else JWT_REFRESH_TTL
    return _sign({
        "sub": user_id,
        "type": token_type,
        "exp": int(time.time()) + ttl,
        "iat": int(time.time()),
        # RC-AUD-020: unique token id (for refresh-reuse detection) + the user's
        # current token epoch/version (for bulk revocation via /auth/logout).
        "jti": secrets.token_urlsafe(16),
        "ver": get_token_store().get_epoch(user_id),
    })


def get_current_user_id(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> int:
    if not creds:
        raise HTTPException(401, "Not authenticated")
    payload = _verify(creds.credentials)
    if not payload:
        raise HTTPException(401, "Token invalid or expired")
    if payload.get("type") != "access":
        raise HTTPException(401, "Expected an access token")
    return payload["sub"]


def _user_response(user_id: int, token: str, refresh_token: str) -> dict:
    """Build the JSON body the website dashboard needs."""
    user = get_user_by_id(user_id)
    pf = get_user_portfolio(user_id)
    return {
        "token": token,
        "refresh_token": refresh_token,
        "expires_in": JWT_ACCESS_TTL,
        "user_id": user_id,
        "email": user.email,
        "plan": user.plan,
        "equity": pf["equity"],
        "telegram_linked": user.telegram_chat_id is not None,
    }


# -- Request schemas --------------------------------------------------------

class AuthIn(BaseModel):
    email: str  # validated in DB layer (lowercase + strip)
    password: str

    def model_post_init(self, __context) -> None:
        if len(self.password) > 128:
            raise ValueError("Password too long")
        if len(self.email) > 254:
            raise ValueError("Email too long")


# -- POST /auth/register ---------------------------------------------------

@auth_router.post("/register")
async def register(body: AuthIn, request: Request):
    _check_auth_rate_limit(request)
    try:
        user_id = create_user(body.email, body.password)
    except ValueError:
        raise HTTPException(400, "Registration failed. Check email and password (8+ chars).")
    token = create_jwt(user_id, token_type="access")
    refresh = create_jwt(user_id, token_type="refresh")
    return _user_response(user_id, token, refresh)


# -- POST /auth/login ------------------------------------------------------

@auth_router.post("/login")
async def login(body: AuthIn, request: Request):
    # RC-AUD-026: per-account lockout (checked first) defends a single account
    # against distributed/rotating-IP credential stuffing; the per-IP limiter
    # still applies on top for noisy single-source abuse.
    _check_account_lockout(body.email)
    _check_auth_rate_limit(request)
    user = authenticate_user(body.email, body.password)
    if not user:
        # Record the failure for this account; this may itself raise 429 once the
        # per-account threshold is crossed.
        _record_account_failure(body.email)
        raise HTTPException(401, "Invalid email or password")
    # Successful login: clear this account's failure counter + lockout.
    _reset_account_failures(body.email)
    token = create_jwt(user.id, token_type="access")
    refresh = create_jwt(user.id, token_type="refresh")
    return _user_response(user.id, token, refresh)


# -- GET /auth/me -----------------------------------------------------------

@auth_router.get("/me")
async def me(user_id: int = Depends(get_current_user_id)):
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    token = create_jwt(user_id, token_type="access")
    refresh = create_jwt(user_id, token_type="refresh")
    return _user_response(user_id, token, refresh)


# -- POST /auth/refresh ----------------------------------------------------

class RefreshIn(BaseModel):
    refresh_token: str


@auth_router.post("/refresh")
async def refresh(body: RefreshIn):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    payload = _verify(body.refresh_token)
    if not payload:
        raise HTTPException(401, "Refresh token invalid or expired")
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Expected a refresh token")
    # RC-AUD-020: rotation + reuse detection. A refresh token may be exchanged
    # exactly once; a replay of an already-consumed refresh token is rejected.
    if not _check_and_record_refresh(payload):
        raise HTTPException(401, "Refresh token already used")
    user_id = payload["sub"]
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    token = create_jwt(user_id, token_type="access")
    new_refresh = create_jwt(user_id, token_type="refresh")
    return _user_response(user_id, token, new_refresh)


# -- POST /auth/logout -----------------------------------------------------

@auth_router.post("/logout")
async def logout(user_id: int = Depends(get_current_user_id)):
    """RC-AUD-020: revoke ALL of the caller's outstanding tokens by bumping the
    user's token epoch. Every previously-issued access/refresh token now has a
    `ver` below the new epoch and is rejected by _verify."""
    _revoke_user_tokens(user_id)
    return {"ok": True}


# -- POST /auth/link-token -------------------------------------------------

@auth_router.post("/link-token")
async def get_link_token(user_id: int = Depends(get_current_user_id)):
    """Generate a short-lived token the user pastes into the Telegram bot."""
    token = create_link_token(user_id)
    return {
        "token": token,
        "expires_in": 600,
        "instruction": f"/link {token}",
    }


# -- POST /auth/unlink -----------------------------------------------------

@auth_router.post("/unlink")
async def api_unlink(user_id: int = Depends(get_current_user_id)):
    unlink_telegram(user_id)
    return {"ok": True}

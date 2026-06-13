"""
RUNECLAW -- Auth API routes.
File: bot/api/auth_routes.py

Mount into api_bridge.py:
    from bot.api.auth_routes import auth_router
    app.include_router(auth_router, prefix="/auth")

Endpoints:
  POST /auth/register    -- create account, return JWT
  POST /auth/login       -- verify credentials, return JWT
  POST /auth/link-token  -- generate a Telegram link token (authenticated)
  GET  /auth/me          -- return current user info (authenticated)
  POST /auth/unlink      -- disconnect Telegram from account (authenticated)
"""

from __future__ import annotations
import os, time, hmac, hashlib, json, base64
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

_AUTH_WINDOW_SEC = 60          # sliding window
_AUTH_MAX_ATTEMPTS = 5         # max attempts per IP per window
_LOCKOUT_SEC = 300             # 5-min lockout after exceeding
_auth_attempts: dict[str, list[float]] = defaultdict(list)
_auth_lockouts: dict[str, float] = {}


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
    _check_auth_rate_limit(request)
    user = authenticate_user(body.email, body.password)
    if not user:
        raise HTTPException(401, "Invalid email or password")
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
    user_id = payload["sub"]
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    token = create_jwt(user_id, token_type="access")
    new_refresh = create_jwt(user_id, token_type="refresh")
    return _user_response(user_id, token, new_refresh)


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

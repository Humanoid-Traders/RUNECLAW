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
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from bot.db.models import (
    create_user, authenticate_user, get_user_by_id,
    create_link_token, get_user_portfolio, get_user_settings,
    unlink_telegram,
)

auth_router = APIRouter()
security = HTTPBearer(auto_error=False)

# -- JWT (stdlib only -- no PyJWT dependency) --------------------------------

_default_secret = "change_this_to_a_random_secret_in_env"
JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET or JWT_SECRET == _default_secret:
    import warnings
    JWT_SECRET = _default_secret
    warnings.warn(
        "JWT_SECRET not set or uses default value. "
        "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\" "
        "and set it in .env",
        stacklevel=1,
    )
JWT_TTL = 60 * 60 * 24 * 30  # 30 days


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


def create_jwt(user_id: int) -> str:
    return _sign({
        "sub": user_id,
        "exp": int(time.time()) + JWT_TTL,
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
    return payload["sub"]


def _user_response(user_id: int, token: str) -> dict:
    """Build the JSON body the website dashboard needs."""
    user = get_user_by_id(user_id)
    pf = get_user_portfolio(user_id)
    return {
        "token": token,
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
async def register(body: AuthIn):
    try:
        user_id = create_user(body.email, body.password)
    except ValueError:
        raise HTTPException(400, "Registration failed. Check email and password (8+ chars).")
    token = create_jwt(user_id)
    return _user_response(user_id, token)


# -- POST /auth/login ------------------------------------------------------

@auth_router.post("/login")
async def login(body: AuthIn):
    user = authenticate_user(body.email, body.password)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    token = create_jwt(user.id)
    return _user_response(user.id, token)


# -- GET /auth/me -----------------------------------------------------------

@auth_router.get("/me")
async def me(user_id: int = Depends(get_current_user_id)):
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    token = create_jwt(user_id)
    return _user_response(user_id, token)


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

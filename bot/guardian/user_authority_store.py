"""Per-user Authority Envelope store — binds one compiled envelope per user.

This is the missing piece that lets the web live gate (`bot/web/web_live_gate`)
see an ENFORCE-mode envelope for a web user: a user authors their envelope in
plain words, it is compiled + clamped by `authority.compile_envelope`, bound
here, and read back by the gate's `envelope_enforcing` precondition and by the
per-user executor at trade time.

Simple JSON persistence keyed by user id (Telegram or ``web:<id>``), one
envelope per user. Mode transitions are tighten-first: a fresh envelope starts
in ``shadow`` (observe) and only reaches ``enforce`` by an explicit, separate
call — never as a side effect of authoring. Revocation is preserved (a revoked
envelope authorises nothing).
"""

from __future__ import annotations

import json
import os
import threading
from typing import Optional

from bot.guardian.authority import VALID_MODES, revoke as _revoke_env

_DEFAULT_PATH = os.environ.get(
    "USER_AUTHORITY_STORE_PATH", "data/user_authority.json")


class UserAuthorityStore:
    """Thread-safe, JSON-backed per-user envelope binding."""

    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._envelopes: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._envelopes = {str(k): v for k, v in data.items()
                                   if isinstance(v, dict)}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._envelopes = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._envelopes, fh, separators=(",", ":"))
            os.replace(tmp, self._path)
        except OSError:
            pass

    # ── reads ─────────────────────────────────────────────────────────

    def get(self, user_id) -> Optional[dict]:
        """The user's bound envelope, or None."""
        with self._lock:
            env = self._envelopes.get(str(user_id))
            return dict(env) if env else None

    def mode(self, user_id) -> str:
        """The bound envelope's mode ('off' when none/revoked)."""
        env = self.get(user_id)
        if not env or env.get("revoked"):
            return "off"
        m = str(env.get("mode", "off")).lower()
        return m if m in VALID_MODES else "off"

    def is_enforcing(self, user_id) -> bool:
        """True only when a non-revoked envelope is bound in enforce mode."""
        return self.mode(user_id) == "enforce"

    # ── writes ────────────────────────────────────────────────────────

    def bind(self, user_id, envelope: dict) -> bool:
        """Bind (replace) the user's compiled envelope. Returns True."""
        if not isinstance(envelope, dict) or not envelope.get("envelope_id"):
            return False
        with self._lock:
            self._envelopes[str(user_id)] = dict(envelope)
            self._save()
            return True

    def set_mode(self, user_id, mode: str) -> bool:
        """Flip the bound envelope's mode (off/shadow/enforce). No envelope → False."""
        mode = str(mode).lower()
        if mode not in VALID_MODES:
            return False
        with self._lock:
            env = self._envelopes.get(str(user_id))
            if not env:
                return False
            env["mode"] = mode
            self._save()
            return True

    def revoke(self, user_id) -> bool:
        """Human kill-switch: revoke (keeps the record, authorises nothing)."""
        with self._lock:
            env = self._envelopes.get(str(user_id))
            if not env:
                return False
            self._envelopes[str(user_id)] = _revoke_env(env)
            self._save()
            return True

    def clear(self, user_id) -> bool:
        """Remove the binding entirely."""
        with self._lock:
            if str(user_id) in self._envelopes:
                del self._envelopes[str(user_id)]
                self._save()
                return True
            return False


_STORE: Optional[UserAuthorityStore] = None
_STORE_LOCK = threading.Lock()


def get_user_authority_store() -> UserAuthorityStore:
    """Process-wide singleton."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = UserAuthorityStore()
        return _STORE

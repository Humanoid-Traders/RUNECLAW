"""
RUNECLAW User Store — file-backed user management with roles.
Persists to data/users.json. Thread-safe with file locking.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from bot.compat import UTC
from pathlib import Path
from typing import Optional

from bot.utils.logger import audit, system_log

# Roles: admin > trader > viewer > pending
ROLES = ("admin", "trader", "viewer", "pending")
# Commands each role can access
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},  # everything
    "trader": {
        "start", "help", "dashboard", "scan", "analyze", "portfolio",
        "trade", "risk", "status", "rejected", "halt", "reset", "macro",
        "backtest", "walkforward", "journal", "costs", "run", "learn",
        "patterns", "proposals", "optimize", "mode",
    },
    "viewer": {
        "start", "help", "dashboard", "scan", "status", "risk",
        "portfolio", "macro", "journal", "costs", "learn", "patterns",
    },
    "pending": {"start", "help"},
}


class UserStore:
    """JSON-file backed user database."""

    def __init__(self, path: str | Path = "data/users.json") -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._users: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    self._users = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._users = {}
        else:
            self._users = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self._users, f, indent=2, default=str)
        tmp.rename(self._path)

    # ── Public API ─────────────────────────────────────────────

    def get(self, telegram_id: int | str) -> Optional[dict]:
        """Get user record or None."""
        with self._lock:
            return self._users.get(str(telegram_id))

    def register(self, telegram_id: int | str, name: str = "",
                 auto_role: str = "pending") -> dict:
        """Register a new user or return existing. Never overwrites role."""
        key = str(telegram_id)
        with self._lock:
            if key in self._users:
                # Update last_seen and name
                self._users[key]["last_seen"] = datetime.now(UTC).isoformat()
                if name and not self._users[key].get("name"):
                    self._users[key]["name"] = name
                self._save()
                return self._users[key]

            user = {
                "telegram_id": key,
                "name": name,
                "role": auto_role,
                "authorized": auto_role not in ("pending",),
                "created_at": datetime.now(UTC).isoformat(),
                "last_seen": datetime.now(UTC).isoformat(),
            }
            self._users[key] = user
            self._save()
            audit(system_log, f"New user registered: {key} ({name}) as {auto_role}",
                  action="user_register", result="OK")
            return user

    def authorize(self, telegram_id: int | str, role: str = "trader") -> bool:
        """Promote a user to an authorized role. Returns True on success."""
        key = str(telegram_id)
        if role not in ROLES or role == "pending":
            return False
        with self._lock:
            if key not in self._users:
                # Auto-create if approving unknown ID
                self._users[key] = {
                    "telegram_id": key,
                    "name": "",
                    "role": role,
                    "authorized": True,
                    "created_at": datetime.now(UTC).isoformat(),
                    "last_seen": datetime.now(UTC).isoformat(),
                }
            else:
                self._users[key]["role"] = role
                self._users[key]["authorized"] = True
            self._save()
            audit(system_log, f"User authorized: {key} as {role}",
                  action="user_authorize", result="OK")
            return True

    def revoke(self, telegram_id: int | str) -> bool:
        """Revoke a user's access (set to pending)."""
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            self._users[key]["role"] = "pending"
            self._users[key]["authorized"] = False
            self._save()
            audit(system_log, f"User revoked: {key}",
                  action="user_revoke", result="OK")
            return True

    def is_authorized(self, telegram_id: int | str) -> bool:
        """Check if user exists and is authorized."""
        user = self.get(telegram_id)
        return user is not None and user.get("authorized", False)

    def has_permission(self, telegram_id: int | str, command: str) -> bool:
        """Check if user has permission for a specific command."""
        user = self.get(telegram_id)
        if not user:
            return command in ROLE_PERMISSIONS.get("pending", set())
        role = user.get("role", "pending")
        perms = ROLE_PERMISSIONS.get(role, set())
        return "*" in perms or command in perms

    def list_users(self) -> list[dict]:
        """List all registered users."""
        with self._lock:
            return list(self._users.values())

    def count(self) -> dict[str, int]:
        """Count users by role."""
        with self._lock:
            counts: dict[str, int] = {}
            for u in self._users.values():
                r = u.get("role", "pending")
                counts[r] = counts.get(r, 0) + 1
            return counts

    def seed_admin(self, admin_ids: str) -> None:
        """Seed admin users from comma-separated TELEGRAM_CHAT_ID."""
        if not admin_ids:
            return
        for cid in admin_ids.split(","):
            cid = cid.strip()
            if cid:
                key = str(cid)
                with self._lock:
                    if key not in self._users:
                        self._users[key] = {
                            "telegram_id": key,
                            "name": "Admin",
                            "role": "admin",
                            "authorized": True,
                            "created_at": datetime.now(UTC).isoformat(),
                            "last_seen": datetime.now(UTC).isoformat(),
                        }
                    elif self._users[key].get("role") != "admin":
                        self._users[key]["role"] = "admin"
                        self._users[key]["authorized"] = True
                self._save()

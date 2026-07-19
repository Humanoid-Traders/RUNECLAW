"""
RUNECLAW User Store — file-backed user management with roles and tiers.
Persists to data/users.json. Thread-safe with file locking.

Roles control access (what you CAN do):
  admin > trader > viewer > pending

Tiers control features (what you GET):
  admin > elite > pro > basic

New users are auto-approved as trader/basic with paper trading.
Only admins (or users explicitly granted) can trade live.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from bot.compat import UTC
from pathlib import Path
from typing import Optional

from bot.utils.logger import audit, system_log

# ── Roles: access control ──────────────────────────────────────
ROLES = ("admin", "trader", "viewer", "pending")

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},  # everything
    "trader": {
        "start", "help", "dashboard", "scan", "deepscan", "analyze", "portfolio",
        "trade", "risk", "status", "rejected", "halt", "reset", "macro",
        "backtest", "walkforward", "journal", "costs", "run", "learn",
        "patterns", "proposals", "optimize", "mode", "playbook",
    },
    "viewer": {
        "start", "help", "dashboard", "scan", "deepscan", "status", "risk",
        "portfolio", "macro", "journal", "costs", "learn", "patterns",
    },
    "pending": {"start", "help"},
}

# ── Tiers: feature gating ──────────────────────────────────────
# Each tier inherits all features from lower tiers.
# Tier hierarchy: basic < pro < elite < admin
TIERS = ("basic", "pro", "elite", "admin")

TIER_FEATURES: dict[str, set[str]] = {
    "basic": {
        # Free tier: all features in paper mode (no live trading)
        "paper_trading",
        "scan", "deepscan",
        "analyze",
        "dashboard",
        "portfolio",
        "risk_status",
        "macro_view",
        "backtest",
        "walkforward",
        "journal",
        "patterns",
        "proposals",
        "optimize",
        "strategy_presets",
        "chart_alerts",
        "order_flow",
        "priority_signals",
        "early_access",
    },
    "pro": {
        # Pro tier: same as basic for now (reserved for future differentiation)
        "paper_trading",
        "scan", "deepscan",
        "analyze",
        "dashboard",
        "portfolio",
        "risk_status",
        "macro_view",
        "backtest",
        "walkforward",
        "journal",
        "patterns",
        "proposals",
        "optimize",
        "strategy_presets",
        "chart_alerts",
        "order_flow",
        "priority_signals",
        "early_access",
    },
    "elite": {
        # Elite tier: everything + live trading eligible
        "paper_trading",
        "live_trading_eligible",  # can be granted live by admin
        "scan", "deepscan",
        "analyze",
        "dashboard",
        "portfolio",
        "risk_status",
        "macro_view",
        "backtest",
        "walkforward",
        "journal",
        "patterns",
        "proposals",
        "optimize",
        "strategy_presets",
        "chart_alerts",
        "order_flow",
        "priority_signals",
        "early_access",
    },
    "admin": {
        "*",  # everything
    },
}

# Default tier for new auto-approved users
DEFAULT_TIER = "basic"
# Default role for new auto-approved users
DEFAULT_AUTO_ROLE = "trader"


class UserStore:
    """JSON-file backed user database with roles and tiers."""

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
                 auto_role: str = "") -> dict:
        """Register a new user or return existing.

        New users are auto-approved as trader/basic with paper trading.
        Never overwrites role/tier on existing users.
        """
        if not auto_role:
            auto_role = DEFAULT_AUTO_ROLE
        if auto_role not in ROLES:
            auto_role = DEFAULT_AUTO_ROLE
        key = str(telegram_id)
        with self._lock:
            if key in self._users:
                # Update last_seen and name
                self._users[key]["last_seen"] = datetime.now(UTC).isoformat()
                if name and not self._users[key].get("name"):
                    self._users[key]["name"] = name
                # Backfill tier for legacy users without one
                if "tier" not in self._users[key]:
                    role = self._users[key].get("role", "pending")
                    self._users[key]["tier"] = "admin" if role == "admin" else DEFAULT_TIER
                # Auto-upgrade legacy pending users on interaction
                if self._users[key].get("role") == "pending":
                    self._users[key]["role"] = auto_role
                    self._users[key]["authorized"] = True
                    self._users[key]["can_trade_live"] = False
                    if "tier" not in self._users[key] or self._users[key]["tier"] == "pending":
                        self._users[key]["tier"] = DEFAULT_TIER
                    audit(system_log,
                          f"Legacy pending user auto-upgraded on interaction: {key}",
                          action="user_auto_upgrade", result="OK")
                self._save()
                return self._users[key]

            # New user: auto-approve as trader with basic tier
            user = {
                "telegram_id": key,
                "name": name,
                "role": auto_role,
                "tier": DEFAULT_TIER,
                "authorized": True,  # auto-approved
                "can_trade_live": False,  # paper only by default
                "sim_opt_in": False,  # per-user PAPER (sim) practice mode opt-in
                "created_at": datetime.now(UTC).isoformat(),
                "last_seen": datetime.now(UTC).isoformat(),
            }
            self._users[key] = user
            self._save()
            audit(system_log,
                  f"New user auto-approved: {key} ({name}) role={auto_role} tier={DEFAULT_TIER}",
                  action="user_auto_approve", result="OK")
            return user

    def authorize(self, telegram_id: int | str, role: str = "trader") -> bool:
        """Promote a user to an authorized role. Returns True on success."""
        key = str(telegram_id)
        if role not in ROLES or role == "pending":
            return False
        with self._lock:
            if key not in self._users:
                # Auto-create if approving unknown ID
                tier = "admin" if role == "admin" else DEFAULT_TIER
                self._users[key] = {
                    "telegram_id": key,
                    "name": "",
                    "role": role,
                    "tier": tier,
                    "authorized": True,
                    "can_trade_live": role == "admin",
                    "created_at": datetime.now(UTC).isoformat(),
                    "last_seen": datetime.now(UTC).isoformat(),
                }
            else:
                self._users[key]["role"] = role
                self._users[key]["authorized"] = True
                # Auto-set tier for admin role
                if role == "admin":
                    self._users[key]["tier"] = "admin"
                    self._users[key]["can_trade_live"] = True
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
            self._users[key]["can_trade_live"] = False
            self._save()
            audit(system_log, f"User revoked: {key}",
                  action="user_revoke", result="OK")
            return True

    def is_authorized(self, telegram_id: int | str) -> bool:
        """Check if user exists and is authorized."""
        user = self.get(telegram_id)
        return user is not None and user.get("authorized", False)

    # ── Live trading permission ────────────────────────────────

    def web_live_enabled(self, telegram_id: int | str) -> bool:
        """Dedicated per-user opt-in for WEB live trading (web:<id> only).

        Deliberately separate from ``can_trade_live``: that flag stays
        structurally False for web ids (below), so a stale/legacy
        ``can_trade_live`` value can never open the web live path. This is the
        one flag the web live gate reads, and it only ever means anything for a
        web-only identity that also passes every other precondition (operator
        feature switch, own keys, enforce-mode Authority Envelope).
        """
        if not str(telegram_id).startswith("web:"):
            return False
        user = self.get(telegram_id)
        if not user or not user.get("authorized", False):
            return False
        return bool(user.get("web_live_enabled", False))

    def set_web_live_enabled(self, telegram_id: int | str, enabled: bool) -> bool:
        """Set the web live opt-in flag (web:<id> only). Returns True on success."""
        key = str(telegram_id)
        if not key.startswith("web:"):
            return False
        with self._lock:
            if key not in self._users:
                return False
            self._users[key]["web_live_enabled"] = bool(enabled)
            self._save()
            audit(system_log,
                  f"Web live trading {'enabled' if enabled else 'disabled'} for {key}",
                  action="web_live_permission", result="OK")
            return True

    def can_trade_live(self, telegram_id: int | str) -> bool:
        """Check if user is allowed to execute live trades.

        Only admins can trade live by default. Users with explicit
        'can_trade_live' flag override this. Non-admins always get
        paper execution even when the bot is in live mode.
        """
        # Web-only identities ("web:<id>", provisioned by the web gateway)
        # are structurally paper-only — even an explicit flag can't override.
        # (Web live execution rides the SEPARATE web_live_enabled flag + gate.)
        if str(telegram_id).startswith("web:"):
            return False
        user = self.get(telegram_id)
        if not user or not user.get("authorized", False):
            return False
        # Explicit flag takes priority
        if "can_trade_live" in user:
            return bool(user["can_trade_live"])
        # Default: only admins can trade live
        return user.get("role") == "admin"

    def set_live_trading(self, telegram_id: int | str, enabled: bool) -> bool:
        """Grant or revoke live trading permission for a user."""
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            self._users[key]["can_trade_live"] = enabled
            self._save()
            audit(system_log,
                  f"Live trading {'enabled' if enabled else 'disabled'} for user {key}",
                  action="live_trading_permission", result="OK")
            return True

    def max_margin(self, telegram_id: int | str) -> Optional[float]:
        """Operator-set max margin (USD) a user may commit to a single live trade,
        or None if unset. Used by the engine to tighten the per-user position cap."""
        user = self.get(telegram_id)
        if not user:
            return None
        v = user.get("max_margin_usd")
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None

    def set_max_margin(self, telegram_id: int | str, usd: Optional[float]) -> bool:
        """Set (or clear, when usd is None) a user's per-trade max margin cap.
        Returns True on success, False if the user does not exist."""
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            if usd is None:
                self._users[key].pop("max_margin_usd", None)
            else:
                self._users[key]["max_margin_usd"] = float(usd)
            self._save()
            audit(system_log,
                  f"Max margin {'cleared' if usd is None else f'set to ${usd:.2f}'} "
                  f"for user {key}",
                  action="user_max_margin", result="OK")
            return True

    def sim_opt_in(self, telegram_id: int | str) -> bool:
        """Whether this user has opted into PAPER (sim) practice mode. When True
        (and PAPER_SIM_OPT_IN_ENABLED), their confirmed trades are simulated into
        their paper portfolio instead of sent to the exchange."""
        user = self.get(telegram_id)
        if not user or not user.get("authorized", False):
            return False
        return bool(user.get("sim_opt_in", False))

    def set_sim_opt_in(self, telegram_id: int | str, enabled: bool) -> bool:
        """Opt a user into or out of PAPER (sim) practice mode."""
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            self._users[key]["sim_opt_in"] = enabled
            self._save()
            audit(system_log,
                  f"Paper sim mode {'enabled' if enabled else 'disabled'} for user {key}",
                  action="sim_opt_in", result="OK")
            return True

    # ── Tier management ────────────────────────────────────────

    def get_tier(self, telegram_id: int | str) -> str:
        """Get user's current tier. Returns 'basic' for unknown users."""
        user = self.get(telegram_id)
        if not user:
            return DEFAULT_TIER
        return user.get("tier", DEFAULT_TIER)

    def set_tier(self, telegram_id: int | str, tier: str) -> bool:
        """Set a user's tier. Admin only operation."""
        if tier not in TIERS:
            return False
        key = str(telegram_id)
        with self._lock:
            if key not in self._users:
                return False
            old_tier = self._users[key].get("tier", DEFAULT_TIER)
            self._users[key]["tier"] = tier
            self._save()
            audit(system_log,
                  f"User tier changed: {key} {old_tier} -> {tier}",
                  action="tier_change", result="OK")
            return True

    def has_feature(self, telegram_id: int | str, feature: str) -> bool:
        """Check if a user's tier grants access to a specific feature.

        Usage:
            if users.has_feature(uid, "backtest"):
                # run backtest
            else:
                # "Upgrade to Pro to unlock backtesting"
        """
        user = self.get(telegram_id)
        if not user:
            return feature in TIER_FEATURES.get(DEFAULT_TIER, set())
        tier = user.get("tier", DEFAULT_TIER)
        features = TIER_FEATURES.get(tier, set())
        return "*" in features or feature in features

    def tier_label(self, telegram_id: int | str) -> str:
        """Human-readable tier label with icon."""
        tier = self.get_tier(telegram_id)
        labels = {
            "basic": "\U0001f7e2 Basic",
            "pro": "\U0001f535 Pro",
            "elite": "\U0001f7e1 Elite",
            "admin": "\U0001f534 Admin",
        }
        return labels.get(tier, "\U0001f7e2 Basic")

    # ── Command permission check ───────────────────────────────

    def has_permission(self, telegram_id: int | str, command: str) -> bool:
        """Check if user has permission for a specific command.

        F-14 FIX: Sensitive commands (trade, halt, reset, mode, golive)
        require the user to have been active within the last 24 hours.
        If the session is stale, only read-only commands are permitted.
        """
        user = self.get(telegram_id)
        if not user:
            return command in ROLE_PERMISSIONS.get("pending", set())
        role = user.get("role", "pending")
        perms = ROLE_PERMISSIONS.get(role, set())
        if "*" not in perms and command not in perms:
            return False
        # F-14: session timeout for sensitive commands
        _SENSITIVE_CMDS = {"trade", "halt", "reset", "mode", "golive", "approve", "revoke"}
        if command in _SENSITIVE_CMDS:
            last_seen = user.get("last_seen", "")
            if last_seen:
                try:
                    from datetime import datetime as _dt
                    last_dt = _dt.fromisoformat(last_seen)
                    if (datetime.now(UTC) - last_dt).total_seconds() > 86400:
                        return False  # stale session — require /start to refresh
                except (ValueError, TypeError):
                    pass
        return True

    # ── Listing and counting ───────────────────────────────────

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

    def all_tiers(self) -> dict[str, str]:
        """{telegram_id: tier} for every registered user — the payload the
        website tier sync pushes so the web 'plan' mirrors the bot's tier
        authority."""
        with self._lock:
            return {k: u.get("tier", DEFAULT_TIER)
                    for k, u in self._users.items()}

    def count_by_tier(self) -> dict[str, int]:
        """Count users by tier."""
        with self._lock:
            counts: dict[str, int] = {}
            for u in self._users.values():
                t = u.get("tier", DEFAULT_TIER)
                counts[t] = counts.get(t, 0) + 1
            return counts

    def migrate_pending_users(self) -> int:
        """One-time migration: upgrade legacy 'pending' users to auto-approved.

        Users registered before auto-approve was added are stuck as pending.
        This promotes them to trader/basic with paper trading, matching what
        new users get automatically.

        Returns the number of users migrated.
        """
        migrated = 0
        with self._lock:
            for key, user in self._users.items():
                if user.get("role") == "pending":
                    user["role"] = DEFAULT_AUTO_ROLE
                    user["authorized"] = True
                    user["can_trade_live"] = False
                    if "tier" not in user:
                        user["tier"] = DEFAULT_TIER
                    migrated += 1
                    audit(system_log,
                          f"Legacy user migrated: {key} ({user.get('name', '')}) "
                          f"pending -> {DEFAULT_AUTO_ROLE}/{DEFAULT_TIER}",
                          action="user_migrate", result="OK")
            if migrated:
                self._save()
        return migrated

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
                            "tier": "admin",
                            "authorized": True,
                            "can_trade_live": True,
                            "created_at": datetime.now(UTC).isoformat(),
                            "last_seen": datetime.now(UTC).isoformat(),
                        }
                    else:
                        if self._users[key].get("role") != "admin":
                            self._users[key]["role"] = "admin"
                        self._users[key]["tier"] = "admin"
                        self._users[key]["authorized"] = True
                        self._users[key]["can_trade_live"] = True
                    self._save()

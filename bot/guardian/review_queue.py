"""Guardian pre-trade review queue + the tighten-only envelope operation.

The safe step between the on-chain PREVIEW slice (admin-only dry-run, no signer)
and any future signing: every proposed high-risk action is RECORDED here so a
human can review it before a signer slice ever acts on it, and — if the review
finds the standing Authority Envelope too permissive — a reviewer can TIGHTEN
that envelope. Tightening can only ever make the envelope MORE restrictive; it
can never authorize or loosen anything. Recording is observe-only and never
blocks or alters the action it records.

Two pieces:

    tighten_envelope(current, tighten)  — a pure, conservative intersect. For
        every field it takes the more-restrictive of the current envelope and
        the reviewer's tightening, so the result authorizes a strict SUBSET of
        what ``current`` did. Re-hashes like ``authority.revoke`` so the tightened
        envelope has its own identity. A runtime guard asserts no monotone field
        loosened — belt-and-suspenders on top of the property test.

    ReviewQueue — an append-only, JSON-persisted log of proposed actions with a
        pending/reviewed status, mirrored read-only to the web Guardian surface.

The hard invariant (property-tested): for every action,
``authorize(tighten_envelope(env, t), action) == allow`` implies
``authorize(env, action) == allow``. Tightening never grants new authority.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

from bot.guardian.authority import _addr, _base_symbol, envelope_hash


# ── numeric helper (mirrors authority._num semantics) ─────────────────

def _num(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None   # NaN → None
    except (TypeError, ValueError):
        return None


def _rehash(env: dict) -> dict:
    """Stamp a fresh identity onto a mutated envelope (as ``authority.revoke`` does)."""
    h = envelope_hash(env)
    env["envelope_id"] = "env_" + h[:8]
    env["compiled_hash"] = h
    return env


# ── tighten-only envelope operation ───────────────────────────────────

def _tighter_cap(cur, prop) -> Optional[float]:
    """More-restrictive ceiling. None = unbounded. Result never exceeds ``cur``."""
    c, p = _num(cur), _num(prop)
    if p is None or p <= 0:
        return c                       # no (or invalid) proposed cap → keep current
    if c is None:
        return round(p, 2)             # current unbounded → adding a cap tightens
    return round(min(c, p), 2)         # both set → the smaller wins


def _assert_tighter(cur: dict, out: dict) -> None:
    """Fail-closed guard: raise if any monotone dimension got LOOSER. This backs
    up the property test — a coding error can never silently widen authority."""
    for k in ("max_notional_per_trade_usd", "max_notional_daily_usd"):
        c, o = _num(cur.get(k)), _num(out.get(k))
        if o is None and c is not None:
            raise AssertionError(f"tighten removed the {k} cap")
        if o is not None and c is not None and o > c + 1e-9:
            raise AssertionError(f"tighten raised the {k} cap")
    if not set(cur.get("symbol_blocklist") or []) <= set(out.get("symbol_blocklist") or []):
        raise AssertionError("tighten shrank the symbol blocklist")
    if out.get("withdraw_allowed") and not cur.get("withdraw_allowed"):
        raise AssertionError("tighten enabled withdrawal")
    if cur.get("revoked") and not out.get("revoked"):
        raise AssertionError("tighten cleared a revocation")
    ce, oe = _num(cur.get("expiry_ts")), _num(out.get("expiry_ts"))
    if ce is not None and (oe is None or oe > ce + 1e-9):
        raise AssertionError("tighten extended the expiry")


def tighten_envelope(current: dict, tighten: dict) -> dict:
    """Return a copy of ``current`` tightened by the (partial) ``tighten`` spec.

    ``tighten`` may carry any of: ``max_notional_per_trade_usd``,
    ``max_notional_daily_usd`` (lowered / added), ``allowed_venues`` /
    ``allowed_market_types`` / ``symbol_allowlist`` / ``withdraw_allowlist``
    (narrowed by intersection), ``symbol_blocklist`` (grown by union),
    ``withdraw_allowed`` (only a falsey value acts — revokes withdrawal),
    ``expiry_ts`` (brought sooner), ``revoked`` (truthy → revoke). Any field the
    reviewer omits is left exactly as ``current`` has it. Enforcement ``mode`` is
    never changed here. Re-hashed so the tightened envelope has its own identity.
    """
    if not isinstance(current, dict):
        raise ValueError("current envelope required")
    t = tighten if isinstance(tighten, dict) else {}
    out = dict(current)

    # Numeric ceilings — smaller (or newly-added) only.
    for k in ("max_notional_per_trade_usd", "max_notional_daily_usd"):
        if k in t:
            out[k] = _tighter_cap(current.get(k), t.get(k))

    # Venues: empty = deny-all in authorize(), so intersection is the tighten;
    # an omitted/empty proposal leaves the current set untouched.
    pv = [str(v).lower().strip() for v in (t.get("allowed_venues") or []) if str(v).strip()]
    if pv:
        cur_v = [str(v).lower().strip() for v in (current.get("allowed_venues") or [])]
        out["allowed_venues"] = sorted(set(cur_v) & set(pv))

    # Market types & symbol allowlist: empty = ALLOW-ALL in authorize(), so an
    # empty current is the universe — narrowing it to the proposal is a tighten.
    pm = [str(v).lower().strip() for v in (t.get("allowed_market_types") or []) if str(v).strip()]
    if pm:
        cur_m = [str(v).lower().strip() for v in (current.get("allowed_market_types") or [])]
        out["allowed_market_types"] = sorted(set(cur_m) & set(pm)) if cur_m else sorted(set(pm))

    pa = [s for s in (_base_symbol(x) for x in (t.get("symbol_allowlist") or [])) if s]
    if pa:
        cur_a = [s for s in (current.get("symbol_allowlist") or []) if s]
        out["symbol_allowlist"] = sorted(set(cur_a) & set(pa)) if cur_a else sorted(set(pa))

    # Blocklist grows (union) — more blocked is tighter.
    pb = [s for s in (_base_symbol(x) for x in (t.get("symbol_blocklist") or [])) if s]
    if pb:
        cur_b = [s for s in (current.get("symbol_blocklist") or []) if s]
        out["symbol_blocklist"] = sorted(set(cur_b) | set(pb))

    # Withdrawal: never enabled here; a falsey proposal revokes it.
    if "withdraw_allowed" in t and not t.get("withdraw_allowed"):
        out["withdraw_allowed"] = False

    # Withdraw allowlist: narrow the permitted destinations (intersection).
    pwl = [a for a in (_addr(a) for a in (t.get("withdraw_allowlist") or [])) if a]
    if pwl:
        cur_wl = [a for a in (_addr(a) for a in (current.get("withdraw_allowlist") or [])) if a]
        out["withdraw_allowlist"] = sorted(set(cur_wl) & set(pwl))

    # Expiry: sooner only.
    if "expiry_ts" in t:
        pe, ce = _num(t.get("expiry_ts")), _num(current.get("expiry_ts"))
        if pe is not None:
            out["expiry_ts"] = int(pe if ce is None else min(ce, pe))

    # Revoke: one-way kill-switch.
    if t.get("revoked"):
        out["revoked"] = True

    out["label"] = (str(current.get("label") or "authority")[:60] + " · tightened")[:80]
    _assert_tighter(current, out)
    return _rehash(out)


# ── the append-only review queue ──────────────────────────────────────

_DEFAULT_PATH = os.environ.get(
    "GUARDIAN_REVIEW_QUEUE_PATH", "data/guardian_review_queue.json")
_MAX_ITEMS = 500


class ReviewQueue:
    """Thread-safe, JSON-backed, append-only proposed-action log (newest kept)."""

    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._items: list[dict] = []
        self._seq = 0
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                self._items = [x for x in data if isinstance(x, dict)]
                self._seq = max((int(x.get("seq") or 0) for x in self._items), default=0)
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            self._items = []

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._items, fh, separators=(",", ":"))
            os.replace(tmp, self._path)
        except OSError:
            pass

    def record(self, entry: dict) -> dict:
        """Append a proposed action. Stamps ``seq``/``id``/``ts``/``status``.
        Never raises on a malformed entry — recording must not break the caller."""
        entry = entry if isinstance(entry, dict) else {}
        with self._lock:
            self._seq += 1
            ts = _num(entry.get("ts"))
            item = {
                "seq": self._seq,
                "id": f"rq_{self._seq:08d}",
                "ts": ts if ts is not None else time.time(),
                "user_id": str(entry.get("user_id") or ""),
                "kind": str(entry.get("kind") or "proposed"),
                "network": str(entry.get("network") or ""),
                "action": entry.get("action") if isinstance(entry.get("action"), dict) else {},
                "envelope_id": entry.get("envelope_id"),
                "status": "pending",
                "note": "",
            }
            self._items.append(item)
            if len(self._items) > _MAX_ITEMS:
                self._items = self._items[-_MAX_ITEMS:]
            self._save()
            return dict(item)

    def list(self, limit: int = 50, user_id=None) -> list[dict]:
        """Newest-first view, optionally scoped to one user."""
        with self._lock:
            items = self._items
            if user_id is not None:
                items = [x for x in items if x.get("user_id") == str(user_id)]
            out = [dict(x) for x in reversed(items)]
            return out[:max(1, min(int(limit or 50), _MAX_ITEMS))]

    def pending_count(self, user_id=None) -> int:
        with self._lock:
            return sum(1 for x in self._items
                       if x.get("status") == "pending"
                       and (user_id is None or x.get("user_id") == str(user_id)))

    def mark_reviewed(self, user_id, note: str = "") -> int:
        """Mark every pending entry for ``user_id`` reviewed. Returns the count."""
        n = 0
        with self._lock:
            for x in self._items:
                if x.get("user_id") == str(user_id) and x.get("status") == "pending":
                    x["status"] = "reviewed"
                    x["note"] = str(note or "")[:200]
                    n += 1
            if n:
                self._save()
        return n


_QUEUE: Optional[ReviewQueue] = None
_QUEUE_LOCK = threading.Lock()


def get_review_queue() -> ReviewQueue:
    """Process-wide singleton."""
    global _QUEUE
    with _QUEUE_LOCK:
        if _QUEUE is None:
            _QUEUE = ReviewQueue()
        return _QUEUE

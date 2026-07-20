"""Public verifiable leaderboard — pure ranking over sealed Proof-of-PnL.

The leaderboard the thesis makes uniquely credible: every row is an anonymous,
opted-in agent ranked by its *cryptographically re-verifiable* record — not a
self-reported number a memecoin bot could fake.

Two hard rules, enforced here:

* **Re-verified or excluded.** Each entry's ``publish_hash`` is re-derived and
  its public-safety re-checked (via ``verify_publication``) before it can rank;
  a tampered or unsafe publication is dropped, never shown.
* **No dollar magnitudes, ever.** Ranking uses SIZE-AGNOSTIC, fills-derived
  metrics only — profit factor (primary), round-trips and Sharpe for context.
  ``net_pnl`` / ``fees`` / ``max_dd`` carry account-size information and are
  never surfaced. This mirrors the existing leaderboard's privacy contract,
  now backed by sealed statements instead of a paper stake.

This module is pure ranking + a thread-safe handle-keyed registry. It gathers
no fills and publishes nothing (that is the opt-in producer's job); it only
ranks whatever sealed, public-safe publications have been registered.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Iterable, Optional

from bot.proofofpnl.publish import verify_publication

HANDLE_MAX = 20


def _metrics_of(pub: Optional[dict]) -> dict:
    return (((pub or {}).get("bundle") or {}).get("statement") or {}).get("metrics") or {}


def _pf_sort_value(pf_str: Any) -> float:
    """Profit factor as a sortable float: 'inf' (no losing round-trips) sorts
    above every finite value; anything unparseable sorts at 0."""
    if pf_str == "inf":
        return float("inf")
    try:
        return float(pf_str)
    except (TypeError, ValueError):
        return 0.0


def _num(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def build_row(handle: Any, pub: Optional[dict]) -> Optional[dict]:
    """A verified, anonymous, size-agnostic leaderboard row from a sealed
    publication — or ``None`` if the publication fails re-verification. The row
    carries only ratios/counts + the hash needed to re-verify it, never a
    dollar figure."""
    ok, _problems = verify_publication(pub)
    if not ok or not isinstance(pub, dict):
        return None
    m = _metrics_of(pub)
    return {
        "handle": (str(handle or "").strip()[:HANDLE_MAX] or "anon"),
        "profit_factor": m.get("pf"),            # ratio string or 'inf' — size-agnostic
        "sharpe": m.get("sharpe"),
        "round_trips": _int(m.get("round_trips")),
        "trust_tier": pub.get("trust_tier"),
        "reconciliation": pub.get("reconciliation"),
        "publish_hash": pub.get("publish_hash"),
        "published_at": pub.get("published_at"),
        "verified": True,
        # private sort keys — stripped before return
        "_pf": _pf_sort_value(m.get("pf")),
        "_sharpe": _num(m.get("sharpe")),
    }


def rank_entries(entries: Optional[Iterable[dict]], *,
                 min_round_trips: int = 1, limit: int = 50) -> list[dict]:
    """Rank ``{handle, publication}`` entries into an anonymous, verified board.

    Highest profit factor first (tie-break: more round-trips, then higher
    Sharpe). Entries that fail re-verification, carry no handle, duplicate an
    earlier handle, or have fewer than ``min_round_trips`` are excluded. The
    returned rows carry no dollar magnitudes — only ratios, counts, and each
    row's ``publish_hash`` so anyone can re-derive it."""
    rows: list[dict] = []
    seen: set[str] = set()
    for e in entries or []:
        handle = (e or {}).get("handle")
        pub = (e or {}).get("publication")
        key = str(handle or "").strip().lower()
        if not key or key in seen:
            continue
        row = build_row(handle, pub)
        if row is None or row["round_trips"] < int(min_round_trips):
            continue
        seen.add(key)
        rows.append(row)
    rows.sort(key=lambda r: (r["_pf"], r["round_trips"], r["_sharpe"]), reverse=True)
    out: list[dict] = []
    for i, r in enumerate(rows[: int(limit)], start=1):
        r = {k: v for k, v in r.items() if not k.startswith("_")}
        r["rank"] = i
        out.append(r)
    return out


class LeaderboardRegistry:
    """Thread-safe JSON store: ``handle`` → its latest sealed publication.

    Kept deliberately separate from the single-operator ``PublicationStore`` so
    the leaderboard never disturbs the operator's ``/proof`` feed. ``put``
    refuses anything that does not re-verify (public-safe + hash intact), so a
    leaky or tampered bundle can never enter the board."""

    def __init__(self, path: str = "data/proofofpnl_leaderboard.json") -> None:
        self._path = path
        self._lock = threading.RLock()

    def _read_raw(self) -> dict:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write_raw(self, data: dict) -> bool:
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"))
            os.replace(tmp, self._path)
            return True
        except OSError:
            return False

    def put(self, handle: str, publication: dict) -> bool:
        """Register/refresh a member's latest publication. Refuses a bad handle
        or a publication that does not re-verify (defense in depth)."""
        h = str(handle or "").strip()
        if not h:
            return False
        ok, _ = verify_publication(publication)
        if not ok:
            return False
        with self._lock:
            data = self._read_raw()
            data[h] = publication
            return self._write_raw(data)

    def remove(self, handle: str) -> bool:
        """Opt-out — drop a member from the board."""
        h = str(handle or "").strip()
        with self._lock:
            data = self._read_raw()
            if h in data:
                del data[h]
                return self._write_raw(data)
            return True

    def all_entries(self) -> list[dict]:
        """Every registered member as ``{handle, publication}`` — ready for
        :func:`rank_entries`."""
        with self._lock:
            data = self._read_raw()
        return [{"handle": h, "publication": p} for h, p in data.items()]

    def ranked(self, *, min_round_trips: int = 1, limit: int = 50) -> list[dict]:
        return rank_entries(self.all_entries(),
                            min_round_trips=min_round_trips, limit=limit)


_REGISTRY: Optional[LeaderboardRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_leaderboard_registry() -> LeaderboardRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = LeaderboardRegistry(
                os.environ.get("PROOFOFPNL_LEADERBOARD_PATH",
                               "data/proofofpnl_leaderboard.json"))
        return _REGISTRY


def reset_leaderboard_registry() -> None:
    """Test hook — drop the cached registry singleton."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None

"""Verifiable seasons — time-boxed standings frozen from sealed statements.

A season is a calendar month (UTC). While its window is open, every board
member's LATEST statement sealed inside the window is frozen into the season
store; when the month ends, the last freeze stands as the season's final
standings — a competition table that persists after the live board moves on.

No new trust surface, by construction:

* A season row is ranked by :func:`bot.proofofpnl.leaderboard.rank_entries`
  over the FROZEN publications — the same re-verified-or-excluded, size-
  agnostic path as the live board. A tampered frozen bundle is dropped at
  read time, never shown; no dollar magnitude can appear in a season row.
* Honest labeling matters: a season ranks statements *as sealed during the
  window* (``published_at`` inside it). It is a snapshot competition — "who
  held the strongest verified record during 2026-07" — NOT a claim about
  per-window PnL, which rolling-lookback statements cannot honestly support.
* Only the CURRENT season is ever written; past seasons are immutable.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Optional

from bot.proofofpnl.leaderboard import rank_entries

_SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")


def season_id_for(ts: float) -> str:
    """Calendar-month season id ('2026-07') for a unix timestamp, UTC."""
    d = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def season_window(season_id: str) -> Optional[tuple[int, int]]:
    """(start_ts, end_ts_exclusive) for a season id, or None when malformed."""
    m = _SEASON_RE.match(str(season_id or ""))
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 2020 <= year <= 2100):
        return None
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12
           else datetime(year, month + 1, 1, tzinfo=timezone.utc))
    return int(start.timestamp()), int(end.timestamp())


class SeasonStore:
    """Thread-safe JSON store: season_id -> {handle: frozen publication}."""

    def __init__(self, path: str = "data/proofofpnl_seasons.json") -> None:
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

    def record_current(self, entries: list[dict], now_ts: float) -> int:
        """Freeze in-window statements into the CURRENT season only.

        For each ``{handle, publication}``, the statement is frozen (upserted)
        when its ``published_at`` falls inside the current season's window —
        so the freshest in-window seal always stands, and when the month ends
        the last one is final. Statements sealed OUTSIDE the window (e.g. a
        stale registry entry from last month) never enter this season. Past
        seasons are never touched. Returns the number of handles frozen.
        """
        sid = season_id_for(now_ts)
        window = season_window(sid)
        if window is None:
            return 0
        start, end = window
        frozen = 0
        with self._lock:
            data = self._read_raw()
            season = data.get(sid)
            if not isinstance(season, dict):
                season = {}
            for e in entries or []:
                handle = str((e or {}).get("handle") or "").strip()
                pub = (e or {}).get("publication")
                if not handle or not isinstance(pub, dict):
                    continue
                try:
                    at = int(pub.get("published_at") or 0)
                except (TypeError, ValueError):
                    continue
                if start <= at < end:
                    season[handle] = pub
                    frozen += 1
            if frozen:
                data[sid] = season
                self._write_raw(data)
        return frozen

    def season_ids(self) -> list[str]:
        """Season ids, newest first."""
        with self._lock:
            data = self._read_raw()
        return sorted((k for k in data if _SEASON_RE.match(str(k))), reverse=True)

    def ranked(self, season_id: str, *, min_round_trips: int = 1,
               limit: int = 50) -> list[dict]:
        """The season's standings — same re-verify-or-exclude, size-agnostic
        ranking as the live board, over the frozen publications."""
        if season_window(season_id) is None:
            return []
        with self._lock:
            data = self._read_raw()
        season = data.get(str(season_id))
        if not isinstance(season, dict):
            return []
        entries = [{"handle": h, "publication": p} for h, p in season.items()]
        return rank_entries(entries, min_round_trips=min_round_trips, limit=limit)


_STORE: Optional[SeasonStore] = None
_STORE_LOCK = threading.Lock()


def get_season_store() -> SeasonStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = SeasonStore(
                os.environ.get("PROOFOFPNL_SEASONS_PATH",
                               "data/proofofpnl_seasons.json"))
        return _STORE


def reset_season_store() -> None:
    """Test hook — drop the cached store singleton."""
    global _STORE
    with _STORE_LOCK:
        _STORE = None

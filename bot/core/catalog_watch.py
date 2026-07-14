"""
Exchange catalog watch — the "smart periodic update check" for new listings.

What already keeps up automatically (no operator action needed):
  - New CRYPTO perps: futures-first discovery iterates the full
    USDT-FUTURES ticker map every scan — a new coin enters the universe
    the first cycle it clears the volume floor.
  - New *STOCK-suffix equity perps: auto-classified as Stock and admitted
    by the TradFi pass without a config release.
  - Hyperliquid builder markets: the venue-native overlay (when trading
    on HL).

What could NOT keep up: bare-ticker TradFi listings (Bitget adding e.g.
"KO/USDT:USDT") classify as Crypto — wrong volume floor, no session-risk
sizing, wrong /classpf bucket — and nothing TOLD the operator the catalog
changed. Bitget's own market metadata is no help (equity perps carry the
exact same fields as crypto: verified 2026-07-12), so the durable answer
is visibility: detect every new listing within one scan cycle and alert
the operator with the classification the bot chose, flagging the ones
that need a human glance.

Zero extra API calls: the watch diffs the futures ticker map the scanner
ALREADY fetched each cycle. The first observation seeds the seen-set
silently (no alert flood for the existing ~700 symbols). State survives
restarts via data/catalog_seen.json (atomic writes).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
DEFAULT_STATE_FILE = os.path.join(_STATE_DIR, "catalog_seen.json")


class CatalogWatch:
    """Persistent diff of the exchange futures catalog."""

    def __init__(self, state_file: Optional[str] = None) -> None:
        self.state_file = state_file or DEFAULT_STATE_FILE
        self._seen: set[str] = set()
        self._pending: list[dict] = []   # new-listing events awaiting alert
        self._recent: list[dict] = []    # last N events, kept AFTER drain (dashboard view)
        self._loaded = False

    # ── persistence ───────────────────────────────────────────────
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self.state_file, encoding="utf-8") as f:
                data = json.load(f)
            self._seen = set(data.get("seen", []))
            self._pending = list(data.get("pending", []))
            self._recent = list(data.get("recent", []))
        except FileNotFoundError:
            pass
        except Exception as exc:  # corrupt state must never break the scan
            logger.warning("catalog_seen.json unreadable (%s) — reseeding", exc)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"seen": sorted(self._seen),
                           "pending": self._pending[-100:],
                           "recent": self._recent[-20:]}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_file)
        except Exception as exc:
            logger.debug("catalog watch save failed: %s", exc)

    # ── observation ───────────────────────────────────────────────
    def observe(self, symbols: set[str],
                tickers: Optional[dict] = None) -> list[dict]:
        """Diff the current futures catalog against the persisted seen-set.

        Returns (and queues for the proactive monitor) the NEW listings,
        each as {symbol, category, vol_usd}. The first-ever observation
        seeds silently. Never raises.
        """
        try:
            self._load()
            if not symbols:
                return []
            if not self._seen:
                self._seen = set(symbols)
                self._save()
                logger.info("catalog watch seeded with %d symbols", len(symbols))
                return []
            new = sorted(symbols - self._seen)
            if not new:
                return []
            from bot.core.market_scanner import _classify_symbol
            events: list[dict] = []
            for sym in new:
                vol = 0.0
                try:
                    t = (tickers or {}).get(sym) or {}
                    vol = float(t.get("quoteVolume") or 0)
                except Exception:
                    pass
                events.append({"symbol": sym,
                               "category": _classify_symbol(sym),
                               "vol_usd": vol})
            self._seen |= set(new)
            self._pending.extend(events)
            self._recent = (self._recent + events)[-20:]
            self._save()
            logger.info("catalog watch: %d new listing(s): %s",
                        len(new), ", ".join(new[:10]))
            return events
        except Exception as exc:  # noqa: BLE001 — watch must never break scans
            logger.debug("catalog watch observe failed: %s", exc)
            return []

    def drain_pending(self) -> list[dict]:
        """Hand pending new-listing events to the alerting side, clearing
        the queue. Never raises."""
        try:
            self._load()
            out, self._pending = self._pending, []
            if out:
                self._save()
            return out
        except Exception:
            return []

    def recent(self, n: int = 10) -> list[dict]:
        """Non-destructive view of the most recent new-listing events (kept
        after drain) — the website dashboard reads this without stealing the
        proactive monitor's alert queue. Never raises."""
        try:
            self._load()
            return list(self._recent[-n:])
        except Exception:
            return []

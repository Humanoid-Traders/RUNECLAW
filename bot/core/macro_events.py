"""
RUNECLAW Enhanced Macro-Event Intelligence Provider.

Builds on top of bot.macro.calendar.MacroCalendar and bot.macro.models.*
to provide severity-aware blackout windows, funding-rate synthetic events,
fail-closed guarantees, and Telegram-formatted summaries.

Standard-library only: datetime, json, enum, dataclasses, pathlib, hashlib, logging.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from bot.compat import UTC
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity enum
# ---------------------------------------------------------------------------

class MacroSeverity(str, Enum):
    """Severity tiers for macro events, each with its own blackout geometry."""
    CRITICAL = "CRITICAL"  # Rate decisions, liquidation cascades
    HIGH = "HIGH"          # CPI, NFP
    MEDIUM = "MEDIUM"      # PCE, PMI
    LOW = "LOW"            # Speeches, minutes


# ---------------------------------------------------------------------------
# Severity classification map (keyed on MacroEventType.value strings)
# ---------------------------------------------------------------------------

_SEVERITY_MAP: dict[str, MacroSeverity] = {
    "FOMC_DECISION": MacroSeverity.CRITICAL,
    "CPI": MacroSeverity.HIGH,
    "NFP": MacroSeverity.HIGH,
    "CORE_PCE": MacroSeverity.MEDIUM,
    "ISM_PMI": MacroSeverity.MEDIUM,
    "PPI": MacroSeverity.MEDIUM,
    "GDP": MacroSeverity.MEDIUM,
    "RETAIL_SALES": MacroSeverity.MEDIUM,
    "JOBLESS_CLAIMS": MacroSeverity.LOW,
    "FED_SPEECH": MacroSeverity.LOW,
}

# ---------------------------------------------------------------------------
# Blackout window definitions (seconds relative to event time)
# ---------------------------------------------------------------------------

# Each entry: (block_before, block_after, reduce_before, reduce_after)
# *_before / *_after are timedelta offsets from the event.
# reduce window is the outer ring where size_multiplier = 0.5.
# block window is the inner ring where size_multiplier = 0.0.

@dataclass(frozen=True)
class _BlackoutSpec:
    block_before: timedelta
    block_after: timedelta
    reduce_before: Optional[timedelta] = None
    reduce_after: Optional[timedelta] = None


_BLACKOUT: dict[MacroSeverity, _BlackoutSpec] = {
    MacroSeverity.CRITICAL: _BlackoutSpec(
        block_before=timedelta(minutes=60),
        block_after=timedelta(minutes=30),
        reduce_before=timedelta(minutes=180),
        reduce_after=timedelta(minutes=0),  # reduce ring ends where block starts
    ),
    MacroSeverity.HIGH: _BlackoutSpec(
        block_before=timedelta(minutes=30),
        block_after=timedelta(minutes=15),
        reduce_before=timedelta(minutes=120),
        reduce_after=timedelta(minutes=0),
    ),
    MacroSeverity.MEDIUM: _BlackoutSpec(
        block_before=timedelta(minutes=15),
        block_after=timedelta(minutes=10),
    ),
    MacroSeverity.LOW: _BlackoutSpec(
        block_before=timedelta(seconds=0),
        block_after=timedelta(seconds=0),
    ),
}


# ---------------------------------------------------------------------------
# MacroContext — the main output dataclass
# ---------------------------------------------------------------------------

@dataclass
class MacroContext:
    """Point-in-time macro intelligence payload returned by MacroEventProvider."""
    risk_state: str = "CLEAR"
    severity: Optional[MacroSeverity] = None
    active_event: Optional[dict] = None
    next_event: Optional[dict] = None
    size_multiplier: float = 1.0
    explanation: str = ""
    window: Optional[str] = None
    is_stale: bool = False
    is_blind: bool = False


# ---------------------------------------------------------------------------
# MacroEventProvider
# ---------------------------------------------------------------------------

class MacroEventProvider:
    """
    Enhanced macro-event intelligence layer.

    Resolution order for calendar data:
      1. ``live_feed`` callable (returns list[dict] with at least
         ``event_type``, ``scheduled_utc``, ``label`` keys)
      2. Seed JSON file at ``seed_path``
      3. Fail closed — no calendar → BLOCK_NEW_ENTRIES

    Parameters
    ----------
    seed_path:
        Optional filesystem path to a JSON seed calendar.  Expected schema::

            {
                "generated_utc": "2026-05-30T12:00:00+00:00",
                "events": [ { "event_type": "CPI", "scheduled_utc": "...", "label": "..." }, ... ]
            }

    live_feed:
        Optional async-free callable ``() -> list[dict]``.  Each dict must
        contain at minimum ``event_type``, ``scheduled_utc`` (ISO-8601),
        ``label``.

    funding_provider:
        Optional callable ``(symbol: str) -> float`` returning the current
        funding rate as a fraction per 8 h (e.g. 0.001 = 0.10 %).

    max_stale_hours:
        If the seed calendar's ``generated_utc`` is older than this many
        hours, treat it as stale and fail closed.  Default 72.
    """

    FUNDING_CRITICAL_THRESHOLD = 0.001  # 0.10 % per 8 h

    def __init__(
        self,
        seed_path: Optional[Path] = None,
        live_feed: Optional[Callable[[], list[dict]]] = None,
        funding_provider: Optional[Callable[[str], float]] = None,
        max_stale_hours: int = 72,
        failsafe: Optional[bool] = None,
    ) -> None:
        self._seed_path = Path(seed_path) if seed_path is not None else None
        self._live_feed = live_feed
        self._funding_provider = funding_provider
        self._max_stale_hours = max_stale_hours

        # MACRO_FAILSAFE: when False, blind/stale calendar does NOT block trades.
        # Default True (fail-closed) unless explicitly disabled via env or param.
        if failsafe is not None:
            self._failsafe = failsafe
        else:
            import os
            self._failsafe = os.getenv("MACRO_FAILSAFE", "true").lower() in ("true", "1", "yes")

        # Internal cache
        self._events: list[dict] = []
        self._calendar_loaded: bool = False
        self._calendar_stale: bool = False
        self._calendar_blind: bool = False
        self._generated_utc: Optional[datetime] = None

        # Attempt initial load
        self._load_calendar()

    # ------------------------------------------------------------------
    # Calendar loading
    # ------------------------------------------------------------------

    def _load_calendar(self) -> None:
        """Load events from live feed or seed file.  Sets blind/stale flags."""
        # Try live feed first
        if self._live_feed is not None:
            try:
                raw = self._live_feed()
                if raw and isinstance(raw, list):
                    self._events = self._normalise_events(raw)
                    self._calendar_loaded = True
                    self._calendar_stale = False
                    self._calendar_blind = False
                    self._generated_utc = datetime.now(UTC)
                    logger.info("Macro calendar loaded from live feed (%d events)", len(self._events))
                    return
            except Exception:
                logger.warning("Live feed failed, falling back to seed calendar", exc_info=True)

        # Try seed file
        if self._seed_path is not None and self._seed_path.exists():
            try:
                data = json.loads(self._seed_path.read_text(encoding="utf-8"))
                gen_str = data.get("generated_utc")
                events_raw = data.get("events", [])

                if gen_str:
                    self._generated_utc = datetime.fromisoformat(gen_str.replace("Z", "+00:00"))
                    if self._generated_utc.tzinfo is None:
                        self._generated_utc = self._generated_utc.replace(tzinfo=UTC)
                    age = datetime.now(UTC) - self._generated_utc
                    if age > timedelta(hours=self._max_stale_hours):
                        logger.warning(
                            "Seed calendar is %.1f h old (max %d h) — marking stale",
                            age.total_seconds() / 3600,
                            self._max_stale_hours,
                        )
                        self._calendar_stale = True
                        self._calendar_blind = False
                        self._calendar_loaded = True
                        self._events = self._normalise_events(events_raw)
                        return

                self._events = self._normalise_events(events_raw)
                self._calendar_loaded = True
                self._calendar_stale = False
                self._calendar_blind = False
                logger.info("Macro calendar loaded from seed (%d events)", len(self._events))
                return
            except Exception:
                logger.warning("Failed to parse seed calendar", exc_info=True)

        # Neither source available
        self._calendar_blind = True
        self._calendar_loaded = False
        logger.warning("No macro calendar available — operating blind (fail-closed)")

    @staticmethod
    def _normalise_events(raw: list[dict]) -> list[dict]:
        """Ensure every event dict has parsed datetime and sort by time."""
        out: list[dict] = []
        for ev in raw:
            ev = dict(ev)  # shallow copy
            sched = ev.get("scheduled_utc")
            if isinstance(sched, str):
                dt = datetime.fromisoformat(sched.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                ev["scheduled_utc"] = dt
            elif isinstance(sched, datetime):
                if sched.tzinfo is None:
                    ev["scheduled_utc"] = sched.replace(tzinfo=UTC)
            else:
                continue  # skip malformed
            out.append(ev)
        out.sort(key=lambda e: e["scheduled_utc"])
        return out

    # ------------------------------------------------------------------
    # Severity helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _severity_for(event: dict) -> MacroSeverity:
        etype = event.get("event_type", "")
        if isinstance(etype, Enum):
            etype = etype.value
        return _SEVERITY_MAP.get(str(etype), MacroSeverity.LOW)

    # ------------------------------------------------------------------
    # Funding-rate synthetic event
    # ------------------------------------------------------------------

    def _check_funding(self, symbol: Optional[str], now: datetime) -> Optional[dict]:
        """If funding rate is extreme, return a synthetic CRITICAL event dict."""
        if self._funding_provider is None or symbol is None:
            return None
        try:
            rate = self._funding_provider(symbol)
            if rate >= self.FUNDING_CRITICAL_THRESHOLD:
                return {
                    "event_type": "LIQUIDATION_RISK",
                    "scheduled_utc": now,
                    "label": f"Liquidation risk — funding {rate*100:.3f}%/8h on {symbol}",
                    "impact": "CRITICAL",
                    "synthetic": True,
                    "funding_rate": rate,
                }
        except Exception:
            logger.debug("Funding provider error for %s", symbol, exc_info=True)
        return None

    # ------------------------------------------------------------------
    # Core public API
    # ------------------------------------------------------------------

    def get_context(
        self,
        symbol: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> MacroContext:
        """
        Compute a point-in-time macro context.

        Fail-closed: any unhandled exception returns BLOCK_NEW_ENTRIES.
        """
        try:
            return self._get_context_inner(symbol, now)
        except Exception:
            logger.error("MacroEventProvider.get_context crashed — failing closed", exc_info=True)
            return MacroContext(
                risk_state="BLOCK_NEW_ENTRIES",
                size_multiplier=0.0,
                explanation="Internal error evaluating macro context — blocking new entries as precaution.",
                is_stale=self._calendar_stale,
                is_blind=self._calendar_blind,
            )

    def _get_context_inner(
        self,
        symbol: Optional[str],
        now: Optional[datetime],
    ) -> MacroContext:
        if now is None:
            now = datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        # ---- Fail-closed: blind (no calendar) ----
        if self._calendar_blind:
            if self._failsafe:
                return MacroContext(
                    risk_state="BLOCK_NEW_ENTRIES",
                    size_multiplier=0.0,
                    explanation="No macro calendar available — blocking new entries (fail-closed).",
                    is_blind=True,
                )
            else:
                logger.info("Macro failsafe disabled — allowing trades despite no calendar")

        # ---- Fail-closed: stale calendar ----
        if self._calendar_stale:
            if self._failsafe:
                return MacroContext(
                    risk_state="BLOCK_NEW_ENTRIES",
                    size_multiplier=0.0,
                    explanation=(
                        f"Macro calendar is stale (generated {self._generated_utc.isoformat() if self._generated_utc else '?'}, "
                        f"max age {self._max_stale_hours}h) — blocking new entries."
                    ),
                    is_stale=True,
                )
            else:
                logger.info("Macro failsafe disabled — allowing trades despite stale calendar")

        # ---- Check funding-rate synthetic event ----
        funding_event = self._check_funding(symbol, now)
        if funding_event is not None:
            return MacroContext(
                risk_state="BLOCK_NEW_ENTRIES",
                severity=MacroSeverity.CRITICAL,
                active_event=funding_event,
                next_event=self._next_event(now),
                size_multiplier=0.0,
                explanation=funding_event["label"],
                window="BLACKOUT",
            )

        # ---- Evaluate scheduled events ----
        active, window_type, severity, multiplier, explanation = self._evaluate_windows(now)
        next_ev = self._next_event(now)

        if window_type == "BLACKOUT":
            risk_state = "BLOCK_NEW_ENTRIES"
        elif window_type == "PRE_EVENT":
            risk_state = "REDUCE"
        elif window_type == "POST_EVENT":
            risk_state = "REDUCE"
        else:
            risk_state = "CLEAR"

        return MacroContext(
            risk_state=risk_state,
            severity=severity,
            active_event=active,
            next_event=next_ev,
            size_multiplier=multiplier,
            explanation=explanation,
            window=window_type,
        )

    def _evaluate_windows(
        self, now: datetime,
    ) -> tuple[Optional[dict], Optional[str], Optional[MacroSeverity], float, str]:
        """
        Walk all events and check blackout / reduce windows.

        Returns (active_event, window_type, severity, size_multiplier, explanation).
        Most restrictive window wins.
        """
        best_mult = 1.0
        best_window: Optional[str] = None
        best_event: Optional[dict] = None
        best_severity: Optional[MacroSeverity] = None
        best_expl = "No macro events affecting current window."

        for ev in self._events:
            sched: datetime = ev["scheduled_utc"]
            delta = (now - sched).total_seconds()  # positive = after event
            sev = self._severity_for(ev)
            spec = _BLACKOUT[sev]

            block_start = -spec.block_before.total_seconds()
            block_end = spec.block_after.total_seconds()

            # --- BLOCK window ---
            if block_start <= delta <= block_end:
                if 0.0 < best_mult:
                    best_mult = 0.0
                    best_window = "BLACKOUT"
                    best_event = ev
                    best_severity = sev
                    if delta < 0:
                        mins = abs(delta) / 60
                        best_expl = f"BLACKOUT: {ev.get('label', '?')} in {mins:.0f} min — blocking new entries."
                    else:
                        mins = delta / 60
                        best_expl = f"BLACKOUT: {ev.get('label', '?')} occurred {mins:.0f} min ago — still in blackout."
                continue

            # --- REDUCE window (outer ring before block) ---
            if spec.reduce_before is not None:
                reduce_start = -spec.reduce_before.total_seconds()
                reduce_end = block_start
                if reduce_start <= delta < reduce_end:
                    if 0.5 < best_mult:
                        best_mult = 0.5
                        best_window = "PRE_EVENT"
                        best_event = ev
                        best_severity = sev
                        mins = abs(delta) / 60
                        best_expl = (
                            f"PRE_EVENT: {ev.get('label', '?')} in {mins:.0f} min — "
                            f"reducing position size to 50%."
                        )

            # --- POST_EVENT advisory for MEDIUM+ ---
            if sev in (MacroSeverity.CRITICAL, MacroSeverity.HIGH, MacroSeverity.MEDIUM):
                post_end = block_end + timedelta(minutes=30).total_seconds()
                if block_end < delta <= post_end:
                    if 0.5 < best_mult:
                        best_mult = 0.5
                        best_window = "POST_EVENT"
                        best_event = ev
                        best_severity = sev
                        best_expl = (
                            f"POST_EVENT: {ev.get('label', '?')} released — "
                            f"elevated volatility, reducing size."
                        )

        return best_event, best_window, best_severity, best_mult, best_expl

    def _next_event(self, now: datetime) -> Optional[dict]:
        for ev in self._events:
            if ev["scheduled_utc"] > now:
                return ev
        return None

    # ------------------------------------------------------------------
    # get_upcoming_events
    # ------------------------------------------------------------------

    def get_upcoming_events(self, hours: int = 24) -> list[dict]:
        """Return events within the next *hours* hours."""
        now = datetime.now(UTC)
        cutoff = now + timedelta(hours=hours)
        return [
            ev for ev in self._events
            if now < ev["scheduled_utc"] <= cutoff
        ]

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    def format_for_telegram(self) -> str:
        """Return an HTML-formatted summary suitable for Telegram's parse_mode='HTML'."""
        try:
            return self._format_telegram_inner()
        except Exception:
            return "<b>⚠ Macro Status:</b> evaluation error — assume elevated risk."

    def _format_telegram_inner(self) -> str:
        ctx = self.get_context()
        parts: list[str] = []

        # Header
        if ctx.risk_state == "BLOCK_NEW_ENTRIES":
            parts.append("<b>🔴 MACRO: BLOCK NEW ENTRIES</b>")
        elif ctx.risk_state == "REDUCE":
            parts.append("<b>🟡 MACRO: REDUCE SIZE</b>")
        else:
            parts.append("<b>🟢 MACRO: CLEAR</b>")

        # Explanation
        parts.append(f"<i>{_html_escape(ctx.explanation)}</i>")

        # Active event
        if ctx.active_event:
            label = ctx.active_event.get("label", "Unknown")
            parts.append(f"\n<b>Active:</b> {_html_escape(label)}")

        # Severity + multiplier
        if ctx.severity:
            parts.append(f"<b>Severity:</b> {ctx.severity.value}")
        parts.append(f"<b>Size multiplier:</b> {ctx.size_multiplier:.1f}x")

        # Next event
        if ctx.next_event:
            label = ctx.next_event.get("label", "Unknown")
            sched = ctx.next_event.get("scheduled_utc")
            if isinstance(sched, datetime):
                delta = sched - datetime.now(UTC)
                h, remainder = divmod(int(delta.total_seconds()), 3600)
                m = remainder // 60
                parts.append(f"\n<b>Next:</b> {_html_escape(label)} (in {h}h {m}m)")
            else:
                parts.append(f"\n<b>Next:</b> {_html_escape(label)}")

        # Upcoming 24 h
        upcoming = self.get_upcoming_events(24)
        if upcoming:
            parts.append("\n<b>Upcoming 24h:</b>")
            for ev in upcoming[:5]:
                label = ev.get("label", "?")
                sched = ev["scheduled_utc"]
                sev = self._severity_for(ev).value
                parts.append(f"  • [{sev}] {_html_escape(label)} — {sched.strftime('%H:%M UTC')}")

        # Flags
        flags: list[str] = []
        if ctx.is_stale:
            flags.append("STALE")
        if ctx.is_blind:
            flags.append("BLIND")
        if flags:
            parts.append(f"\n<b>Flags:</b> {', '.join(flags)}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Force-reload the calendar from live feed / seed file."""
        self._load_calendar()

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def calendar_hash(self) -> str:
        """SHA-256 fingerprint of the loaded calendar for change detection."""
        raw = json.dumps(
            [
                {
                    "t": ev.get("event_type", ""),
                    "s": ev["scheduled_utc"].isoformat(),
                }
                for ev in self._events
            ],
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _html_escape(text: str) -> str:
    """Minimal HTML escaping for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

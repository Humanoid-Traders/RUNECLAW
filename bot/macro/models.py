"""
RUNECLAW Macro Calendar Models -- Pydantic schemas for macro events and risk states.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MacroRiskState(str, Enum):
    """Risk state derived from proximity to scheduled macro events."""
    NORMAL = "NORMAL"                                # No event within 24h
    PRE_EVENT_CAUTION = "PRE_EVENT_CAUTION"          # Within 24h before event
    EVENT_LOCKDOWN = "EVENT_LOCKDOWN"                # 30min before to 30min after
    POST_EVENT_VOLATILITY = "POST_EVENT_VOLATILITY"  # 30min to 4h after
    BLACKOUT = "BLACKOUT"                            # Calendar evaluation failed


class MacroEventType(str, Enum):
    """Categories of macro events tracked by the calendar."""
    FOMC_DECISION = "FOMC_DECISION"
    CPI = "CPI"
    CORE_PCE = "CORE_PCE"
    NFP = "NFP"
    PPI = "PPI"
    GDP = "GDP"
    ISM_PMI = "ISM_PMI"
    RETAIL_SALES = "RETAIL_SALES"
    JOBLESS_CLAIMS = "JOBLESS_CLAIMS"
    FED_SPEECH = "FED_SPEECH"


class MacroEvent(BaseModel):
    """A single scheduled macro event."""
    event_type: MacroEventType
    scheduled_utc: datetime
    label: str
    impact: str = "HIGH"
    previous: Optional[str] = None
    forecast: Optional[str] = None
    actual: Optional[str] = None

    model_config = ConfigDict(frozen=True)


class MacroStateSnapshot(BaseModel):
    """Point-in-time macro risk assessment."""
    state: MacroRiskState
    next_event: Optional[MacroEvent] = None
    time_until_next: Optional[timedelta] = None
    active_event: Optional[MacroEvent] = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

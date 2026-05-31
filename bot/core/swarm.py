"""
RUNECLAW Multi-Agent Swarm Protocol -- composable agent collaboration via MCP.

Demonstrates the Agent Hub vision: multiple specialized RUNECLAW agents
operating as a coordinated swarm where each agent has a single responsibility.

Swarm roles:
  - SCANNER:  Perceives market state, emits signals
  - ANALYST:  Generates trade theses from signals
  - RISK:     Evaluates and gates every trade idea
  - EXECUTOR: Manages positions and portfolio
  - SENTINEL: Monitors for Black Swan anomalies

Communication is via a shared SwarmBus (in-process message queue).
In production, this maps to MCP tool calls between Agent Hub agents.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Swarm message protocol
# ---------------------------------------------------------------------------

class SwarmRole(str, Enum):
    """Agent roles in the swarm."""
    SCANNER = "SCANNER"
    ANALYST = "ANALYST"
    RISK = "RISK"
    EXECUTOR = "EXECUTOR"
    SENTINEL = "SENTINEL"
    COORDINATOR = "COORDINATOR"


class SwarmMessageType(str, Enum):
    """Message types exchanged between agents."""
    SIGNAL = "SIGNAL"               # Scanner → Analyst: market signal detected
    TRADE_IDEA = "TRADE_IDEA"       # Analyst → Risk: trade thesis generated
    RISK_VERDICT = "RISK_VERDICT"   # Risk → Executor: approved/rejected
    EXECUTION = "EXECUTION"         # Executor → Coordinator: trade opened/closed
    ANOMALY = "ANOMALY"             # Sentinel → Coordinator: black swan detected
    HALT = "HALT"                   # Sentinel/Coordinator → All: stop trading
    HEARTBEAT = "HEARTBEAT"         # Any → Coordinator: alive signal
    STATUS = "STATUS"               # Coordinator → Any: status request/response


class SwarmMessage(BaseModel):
    """A message in the swarm protocol."""
    id: str = Field(default_factory=lambda: f"MSG-{datetime.now(UTC).strftime('%H%M%S%f')}")
    msg_type: SwarmMessageType
    sender: SwarmRole
    recipient: SwarmRole
    payload: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def __str__(self) -> str:
        return f"[{self.sender.value}→{self.recipient.value}] {self.msg_type.value}: {list(self.payload.keys())}"


# ---------------------------------------------------------------------------
# Swarm communication bus
# ---------------------------------------------------------------------------

class SwarmBus:
    """
    In-process message bus for agent communication.

    In production, this would be replaced by MCP tool calls between
    separate Agent Hub agents. The bus abstraction makes the swap trivial.
    """

    def __init__(self) -> None:
        self._subscribers: dict[SwarmRole, list[Callable[[SwarmMessage], None]]] = {}
        self._message_log: list[SwarmMessage] = []
        self._max_log: int = 1000

    def subscribe(self, role: SwarmRole, handler: Callable[[SwarmMessage], None]) -> None:
        """Register a handler for messages sent to a specific role."""
        self._subscribers.setdefault(role, []).append(handler)

    def publish(self, message: SwarmMessage) -> None:
        """Send a message to its recipient's handlers."""
        self._message_log.append(message)
        if len(self._message_log) > self._max_log:
            self._message_log = self._message_log[-500:]

        handlers = self._subscribers.get(message.recipient, [])
        for handler in handlers:
            handler(message)

    def broadcast(self, message: SwarmMessage) -> None:
        """Send a message to ALL agents (e.g., HALT signals)."""
        self._message_log.append(message)
        for handlers in self._subscribers.values():
            for handler in handlers:
                handler(message)

    @property
    def message_count(self) -> int:
        return len(self._message_log)

    @property
    def log(self) -> list[SwarmMessage]:
        return list(self._message_log)


# ---------------------------------------------------------------------------
# Agent base class
# ---------------------------------------------------------------------------

class SwarmAgent:
    """Base class for swarm agents."""

    role: SwarmRole = SwarmRole.COORDINATOR
    _name: str = "base"

    def __init__(self, bus: SwarmBus) -> None:
        self._bus = bus
        self._inbox: list[SwarmMessage] = []
        self._alive = True
        bus.subscribe(self.role, self._on_message)
        # Also subscribe to broadcast halt messages
        bus.subscribe(SwarmRole.COORDINATOR, self._on_broadcast)

    def _on_message(self, msg: SwarmMessage) -> None:
        self._inbox.append(msg)
        self.handle(msg)

    def _on_broadcast(self, msg: SwarmMessage) -> None:
        if msg.msg_type == SwarmMessageType.HALT:
            self._alive = False

    def handle(self, msg: SwarmMessage) -> None:
        """Override in subclasses to process incoming messages."""
        pass

    def send(self, msg_type: SwarmMessageType, recipient: SwarmRole,
             payload: dict | None = None) -> SwarmMessage:
        """Send a message via the bus."""
        msg = SwarmMessage(
            msg_type=msg_type,
            sender=self.role,
            recipient=recipient,
            payload=payload or {},
        )
        self._bus.publish(msg)
        return msg

    @property
    def inbox_count(self) -> int:
        return len(self._inbox)


# ---------------------------------------------------------------------------
# Specialized agents
# ---------------------------------------------------------------------------

class ScannerAgent(SwarmAgent):
    """Perceives market state and emits signals to the Analyst."""
    role = SwarmRole.SCANNER
    _name = "scanner"

    def emit_signal(self, symbol: str, price: float, change_pct: float,
                    volume: float, momentum: float) -> SwarmMessage:
        return self.send(
            SwarmMessageType.SIGNAL,
            SwarmRole.ANALYST,
            {
                "symbol": symbol,
                "price": price,
                "change_pct_24h": change_pct,
                "volume_usd_24h": volume,
                "momentum_score": momentum,
            },
        )


class AnalystAgent(SwarmAgent):
    """Generates trade theses from scanner signals."""
    role = SwarmRole.ANALYST
    _name = "analyst"

    def __init__(self, bus: SwarmBus) -> None:
        super().__init__(bus)
        self._ideas_generated: int = 0

    def handle(self, msg: SwarmMessage) -> None:
        if msg.msg_type == SwarmMessageType.SIGNAL and self._alive:
            # Generate a trade idea and forward to Risk
            self._ideas_generated += 1
            self.send(
                SwarmMessageType.TRADE_IDEA,
                SwarmRole.RISK,
                {
                    "trade_id": f"SWARM-{self._ideas_generated}",
                    "asset": msg.payload.get("symbol", "UNKNOWN"),
                    "momentum": msg.payload.get("momentum_score", 0),
                    "source_signal_id": msg.id,
                },
            )


class RiskAgent(SwarmAgent):
    """Evaluates trade ideas and gates execution."""
    role = SwarmRole.RISK
    _name = "risk"

    def __init__(self, bus: SwarmBus) -> None:
        super().__init__(bus)
        self._approved: int = 0
        self._rejected: int = 0

    def handle(self, msg: SwarmMessage) -> None:
        if msg.msg_type == SwarmMessageType.TRADE_IDEA and self._alive:
            # Simulate risk evaluation
            momentum = abs(msg.payload.get("momentum", 0))
            approved = momentum >= 0.3  # simplified threshold
            if approved:
                self._approved += 1
                verdict = "APPROVED"
            else:
                self._rejected += 1
                verdict = "REJECTED"

            recipient = SwarmRole.EXECUTOR if approved else SwarmRole.COORDINATOR
            self.send(
                SwarmMessageType.RISK_VERDICT,
                recipient,
                {
                    "trade_id": msg.payload.get("trade_id"),
                    "verdict": verdict,
                    "asset": msg.payload.get("asset"),
                },
            )


class ExecutorAgent(SwarmAgent):
    """Manages position lifecycle."""
    role = SwarmRole.EXECUTOR
    _name = "executor"

    def __init__(self, bus: SwarmBus) -> None:
        super().__init__(bus)
        self._executions: int = 0

    def handle(self, msg: SwarmMessage) -> None:
        if msg.msg_type == SwarmMessageType.RISK_VERDICT and self._alive:
            if msg.payload.get("verdict") == "APPROVED":
                self._executions += 1
                self.send(
                    SwarmMessageType.EXECUTION,
                    SwarmRole.COORDINATOR,
                    {
                        "trade_id": msg.payload.get("trade_id"),
                        "asset": msg.payload.get("asset"),
                        "status": "EXECUTED",
                        "execution_number": self._executions,
                    },
                )


class SentinelAgent(SwarmAgent):
    """Monitors for Black Swan anomalies and can halt the swarm."""
    role = SwarmRole.SENTINEL
    _name = "sentinel"

    def __init__(self, bus: SwarmBus) -> None:
        super().__init__(bus)
        self._anomalies_detected: int = 0

    def report_anomaly(self, anomaly_type: str, severity: float,
                       symbol: str, description: str) -> SwarmMessage:
        self._anomalies_detected += 1
        msg = self.send(
            SwarmMessageType.ANOMALY,
            SwarmRole.COORDINATOR,
            {
                "anomaly_type": anomaly_type,
                "severity": severity,
                "symbol": symbol,
                "description": description,
            },
        )
        # If severity is critical, broadcast HALT to entire swarm
        if severity >= 0.8:
            self._bus.broadcast(SwarmMessage(
                msg_type=SwarmMessageType.HALT,
                sender=self.role,
                recipient=SwarmRole.COORDINATOR,
                payload={"reason": f"Critical anomaly: {anomaly_type}", "severity": severity},
            ))
        return msg


# ---------------------------------------------------------------------------
# Swarm coordinator
# ---------------------------------------------------------------------------

class SwarmCoordinator:
    """
    Orchestrates the multi-agent swarm.

    Provides a high-level API to run the full pipeline:
    signal → analysis → risk → execution, with sentinel monitoring.

    In production, each agent would be a separate process/container
    communicating via MCP tool calls through the Bitget Agent Hub.
    """

    def __init__(self) -> None:
        self.bus = SwarmBus()
        self.scanner = ScannerAgent(self.bus)
        self.analyst = AnalystAgent(self.bus)
        self.risk = RiskAgent(self.bus)
        self.executor = ExecutorAgent(self.bus)
        self.sentinel = SentinelAgent(self.bus)
        self._halted = False

    def process_signal(self, symbol: str, price: float, change_pct: float,
                       volume: float, momentum: float) -> dict:
        """
        Run a signal through the full swarm pipeline.

        Returns a summary of what happened at each stage.
        """
        if self._halted:
            return {"status": "HALTED", "reason": "Swarm is halted due to anomaly"}

        self.scanner.emit_signal(symbol, price, change_pct, volume, momentum)

        return {
            "status": "PROCESSED",
            "messages": self.bus.message_count,
            "ideas_generated": self.analyst._ideas_generated,
            "approved": self.risk._approved,
            "rejected": self.risk._rejected,
            "executed": self.executor._executions,
            "anomalies": self.sentinel._anomalies_detected,
        }

    def inject_anomaly(self, anomaly_type: str, severity: float,
                       symbol: str, description: str) -> dict:
        """Sentinel detects an anomaly — may halt the swarm."""
        self.sentinel.report_anomaly(anomaly_type, severity, symbol, description)
        if severity >= 0.8:
            self._halted = True
        return {
            "anomaly_reported": True,
            "severity": severity,
            "swarm_halted": self._halted,
        }

    def status(self) -> dict:
        """Return swarm health summary."""
        return {
            "halted": self._halted,
            "agents": {
                "scanner": self.scanner._alive,
                "analyst": self.analyst._alive,
                "risk": self.risk._alive,
                "executor": self.executor._alive,
                "sentinel": self.sentinel._alive,
            },
            "stats": {
                "total_messages": self.bus.message_count,
                "ideas_generated": self.analyst._ideas_generated,
                "risk_approved": self.risk._approved,
                "risk_rejected": self.risk._rejected,
                "trades_executed": self.executor._executions,
                "anomalies": self.sentinel._anomalies_detected,
            },
        }

    def reset(self) -> None:
        """Reset the swarm after a halt."""
        self._halted = False
        self.scanner._alive = True
        self.analyst._alive = True
        self.risk._alive = True
        self.executor._alive = True
        self.sentinel._alive = True

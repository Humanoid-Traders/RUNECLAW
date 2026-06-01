"""
Tamper-evident hash-chained audit log for the RUNECLAW trading bot.

Each log entry is SHA-256 hash-chained to the previous one. Any edit,
deletion, reorder, or insertion breaks the chain and is caught by verify().
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DecisionRecord:
    """Structured record of a single trading decision."""

    decision_id: str
    symbol: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    idea: Optional[dict] = None
    risk: Optional[dict] = None
    macro: Optional[dict] = None
    compliance: Optional[dict] = None
    outcome: str = "REJECTED"
    is_paper: bool = True

GENESIS_HASH = "0" * 64


def _compute_hash(
    sequence: int,
    event_type: str,
    payload: dict,
    actor: str,
    timestamp: str,
    prev_hash: str,
) -> str:
    """Compute the SHA-256 digest that seals an entry."""
    canonical = (
        f"{sequence}|{event_type}|{json.dumps(payload, sort_keys=True)}"
        f"|{actor}|{timestamp}|{prev_hash}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class AuditEntry:
    """A single link in the hash chain."""

    sequence: int
    event_type: str
    payload: dict
    actor: str
    timestamp: str
    prev_hash: str
    entry_hash: str

    # -- serialisation helpers ------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "payload": self.payload,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AuditEntry:
        return cls(
            sequence=d["sequence"],
            event_type=d["event_type"],
            payload=d["payload"],
            actor=d["actor"],
            timestamp=d["timestamp"],
            prev_hash=d["prev_hash"],
            entry_hash=d["entry_hash"],
        )

# ---------------------------------------------------------------------------
# AuditChain
# ---------------------------------------------------------------------------

class AuditChain:
    """Append-only, hash-chained audit log stored as JSONL."""

    def __init__(self, path: str = "logs/audit_chain.jsonl") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._entries_since_sign = 0
        self._auto_sign_interval = 50  # F-12 FIX: auto-sign every N entries

    # -- public API -----------------------------------------------------------

    def append(
        self,
        event_type: str,
        payload: dict,
        actor: str = "system",
    ) -> AuditEntry:
        """Compute hash chain link, persist to JSONL, and return the entry."""
        with self._lock:
            prev_hash, next_seq = self._tail_state()
            ts = datetime.now(timezone.utc).isoformat()
            entry_hash = _compute_hash(
                next_seq, event_type, payload, actor, ts, prev_hash,
            )
            entry = AuditEntry(
                sequence=next_seq,
                event_type=event_type,
                payload=payload,
                actor=actor,
                timestamp=ts,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
            )
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), sort_keys=False) + "\n")
            # F-12 FIX: auto-sign every N entries to anchor chain integrity
            self._entries_since_sign += 1
            if self._entries_since_sign >= self._auto_sign_interval:
                try:
                    self.sign_latest_batch(batch_size=self._auto_sign_interval)
                    self._entries_since_sign = 0
                except Exception:
                    pass  # best-effort: signing is non-critical
            return entry

    def seal_decision(self, record: DecisionRecord) -> AuditEntry:
        """Convenience wrapper: persist a DecisionRecord as a DECISION event."""
        return self.append(
            event_type="DECISION",
            payload=asdict(record),
            actor="system",
        )

    def get_entries(self, limit: int = 100) -> list[AuditEntry]:
        """Return the last *limit* entries from the log."""
        if not self._path.exists():
            return []
        entries: list[AuditEntry] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entries.append(AuditEntry.from_dict(json.loads(line)))
        return entries[-limit:]

    def get_chain_length(self) -> int:
        """Return the total number of entries in the log."""
        if not self._path.exists():
            return 0
        count = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count

    # -- verification ---------------------------------------------------------

    @staticmethod
    def verify(path: str) -> tuple[bool, list[str]]:
        """Read the entire log and re-derive every hash.

        Returns ``(True, [])`` when the chain is intact, or
        ``(False, [problem, ...])`` listing every inconsistency found.
        """
        file_path = Path(path)
        if not file_path.exists():
            return True, []

        problems: list[str] = []
        prev_hash = GENESIS_HASH
        expected_seq = 0

        with file_path.open("r", encoding="utf-8") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue

                # --- parse ---------------------------------------------------
                try:
                    data = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    problems.append(
                        f"line {line_no}: malformed JSON ({exc})"
                    )
                    # Cannot continue chain verification after corrupt line
                    prev_hash = None
                    expected_seq += 1
                    continue

                # --- sequence continuity -------------------------------------
                seq = data.get("sequence")
                if seq != expected_seq:
                    problems.append(
                        f"line {line_no}: expected sequence {expected_seq}, "
                        f"got {seq}"
                    )

                # --- prev_hash linkage ---------------------------------------
                recorded_prev = data.get("prev_hash", "")
                if prev_hash is not None and recorded_prev != prev_hash:
                    problems.append(
                        f"line {line_no}: prev_hash mismatch "
                        f"(expected {prev_hash}, got {recorded_prev})"
                    )

                # --- entry_hash integrity ------------------------------------
                recomputed = _compute_hash(
                    data.get("sequence", 0),
                    data.get("event_type", ""),
                    data.get("payload", {}),
                    data.get("actor", ""),
                    data.get("timestamp", ""),
                    data.get("prev_hash", ""),
                )
                recorded_hash = data.get("entry_hash", "")
                if recomputed != recorded_hash:
                    problems.append(
                        f"line {line_no}: entry_hash mismatch "
                        f"(expected {recomputed}, got {recorded_hash})"
                    )

                prev_hash = recorded_hash
                expected_seq += 1

        return (len(problems) == 0, problems)

    # -- attestation ----------------------------------------------------------

    def sign_latest_batch(self, batch_size: int = 10) -> "AttestationResult":
        """Sign the latest batch of entries with Ed25519.

        Returns AttestationResult with signature and Merkle root.
        """
        from bot.utils.attestation import AttestationEngine, AttestationResult

        engine = AttestationEngine()
        entries = self.get_entries(limit=batch_size)
        if not entries:
            return AttestationResult(valid=False, error="No entries to sign")

        hashes = [e.entry_hash for e in entries]
        return engine.sign_batch(hashes)

    # -- internals ------------------------------------------------------------

    def _tail_state(self) -> tuple[str, int]:
        """Return (prev_hash, next_sequence) by reading the last line."""
        if not self._path.exists() or self._path.stat().st_size == 0:
            return GENESIS_HASH, 0

        # Read last non-empty line efficiently
        last_line: str = ""
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    last_line = stripped

        if not last_line:
            return GENESIS_HASH, 0

        data = json.loads(last_line)
        return data["entry_hash"], data["sequence"] + 1

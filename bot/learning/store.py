"""RUNECLAW AI Learning — Persistent Data Store.

JSONL-based append-only stores for all learning data.
Every write is atomic (write-to-temp, rename). Every read is validated.
No record may be deleted or overwritten — append only.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from .models import (
    DecisionMemory,
    HumanFeedback,
    ImprovementProposal,
    MacroEventMemory,
    ModelComparison,
    PromptVersion,
    ReflectionMemory,
    StrategyScorecard,
)

logger = logging.getLogger("runeclaw.learning.store")

T = TypeVar("T", bound=BaseModel)

# Default data directory
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "learning")


class LearningStore:
    """Append-only JSONL store for all learning data.

    Files:
    - decision_memory.jsonl      — every trading decision
    - reflection_memory.jsonl    — post-trade reflections
    - strategy_scorecard.json    — strategy rankings (overwrite OK)
    - macro_event_memory.jsonl   — macro event reactions
    - prompt_versions.json       — prompt version registry (overwrite OK)
    - model_comparison.jsonl     — model-vs-model comparisons
    - human_feedback.jsonl       — user feedback
    - improvement_backlog.json   — proposed improvements (overwrite OK)
    """

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = Path(data_dir or DEFAULT_DATA_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._files = {
            "decision": self._dir / "decision_memory.jsonl",
            "reflection": self._dir / "reflection_memory.jsonl",
            "scorecard": self._dir / "strategy_scorecard.json",
            "macro": self._dir / "macro_event_memory.jsonl",
            "prompts": self._dir / "prompt_versions.json",
            "comparison": self._dir / "model_comparison.jsonl",
            "feedback": self._dir / "human_feedback.jsonl",
            "backlog": self._dir / "improvement_backlog.json",
        }

    # ── Append (JSONL) ─────────────────────────────────────────────

    def _append_jsonl(self, key: str, record: BaseModel) -> None:
        """Atomic append to a JSONL file."""
        path = self._files[key]
        try:
            line = record.model_dump_json() + "\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.error("Failed to append to %s: %s", path, e)

    def _read_jsonl(self, key: str, model_cls: Type[T]) -> list[T]:
        """Read all records from a JSONL file."""
        path = self._files[key]
        if not path.exists():
            return []
        records: list[T] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(model_cls.model_validate_json(line))
                    except Exception as e:
                        logger.warning("Skipping corrupt line %d in %s: %s", line_num, path, e)
        except Exception as e:
            logger.error("Failed to read %s: %s", path, e)
        return records

    # ── Overwrite (JSON) ───────────────────────────────────────────

    def _write_json(self, key: str, data: dict | list) -> None:
        """Atomic overwrite of a JSON file (for scorecard, prompts, backlog)."""
        path = self._files[key]
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, path)
        except Exception as e:
            logger.error("Failed to write %s: %s", path, e)
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

    def _read_json(self, key: str) -> dict | list:
        """Read a JSON file."""
        path = self._files[key]
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to read %s: %s", path, e)
            return {}

    # ── Public API: Decision Memory ────────────────────────────────

    def record_decision(self, decision: DecisionMemory) -> None:
        self._append_jsonl("decision", decision)
        logger.info("Decision recorded: %s → %s", decision.audit_id, decision.decision)

    def get_decisions(self, symbol: Optional[str] = None, limit: int = 100) -> list[DecisionMemory]:
        records = self._read_jsonl("decision", DecisionMemory)
        if symbol:
            records = [r for r in records if r.symbol == symbol]
        return records[-limit:]

    # ── Public API: Reflection Memory ──────────────────────────────

    def record_reflection(self, reflection: ReflectionMemory) -> None:
        self._append_jsonl("reflection", reflection)
        logger.info("Reflection recorded: %s", reflection.audit_id)

    def get_reflections(self, limit: int = 50) -> list[ReflectionMemory]:
        return self._read_jsonl("reflection", ReflectionMemory)[-limit:]

    # ── Public API: Strategy Scorecard ─────────────────────────────

    def update_scorecard(self, scorecard: StrategyScorecard) -> None:
        data = self._read_json("scorecard")
        if not isinstance(data, dict):
            data = {}
        data[scorecard.strategy_name] = json.loads(scorecard.model_dump_json())
        self._write_json("scorecard", data)

    def get_scorecards(self) -> dict[str, StrategyScorecard]:
        data = self._read_json("scorecard")
        if not isinstance(data, dict):
            return {}
        result = {}
        for name, raw in data.items():
            try:
                result[name] = StrategyScorecard.model_validate(raw)
            except Exception:
                pass
        return result

    # ── Public API: Macro Event Memory ─────────────────────────────

    def record_macro_event(self, event: MacroEventMemory) -> None:
        self._append_jsonl("macro", event)

    def get_macro_events(self, event_type: Optional[str] = None, limit: int = 50) -> list[MacroEventMemory]:
        records = self._read_jsonl("macro", MacroEventMemory)
        if event_type:
            records = [r for r in records if r.event_type == event_type]
        return records[-limit:]

    # ── Public API: Prompt Versions ────────────────────────────────

    def record_prompt_version(self, pv: PromptVersion) -> None:
        data = self._read_json("prompts")
        if not isinstance(data, dict):
            data = {}
        data[pv.version_id] = json.loads(pv.model_dump_json())
        self._write_json("prompts", data)

    def get_prompt_versions(self) -> dict[str, PromptVersion]:
        data = self._read_json("prompts")
        if not isinstance(data, dict):
            return {}
        result = {}
        for vid, raw in data.items():
            try:
                result[vid] = PromptVersion.model_validate(raw)
            except Exception:
                pass
        return result

    # ── Public API: Model Comparison ───────────────────────────────

    def record_comparison(self, comparison: ModelComparison) -> None:
        self._append_jsonl("comparison", comparison)

    def get_comparisons(self, limit: int = 50) -> list[ModelComparison]:
        return self._read_jsonl("comparison", ModelComparison)[-limit:]

    # ── Public API: Human Feedback ─────────────────────────────────

    def record_feedback(self, feedback: HumanFeedback) -> None:
        self._append_jsonl("feedback", feedback)
        logger.info("Feedback recorded: %s type=%s", feedback.audit_id, feedback.feedback_type)

    def get_feedback(self, limit: int = 50) -> list[HumanFeedback]:
        return self._read_jsonl("feedback", HumanFeedback)[-limit:]

    # ── Public API: Improvement Backlog ────────────────────────────

    def record_proposal(self, proposal: ImprovementProposal) -> None:
        data = self._read_json("backlog")
        if not isinstance(data, list):
            data = []
        data.append(json.loads(proposal.model_dump_json()))
        self._write_json("backlog", data)
        logger.info("Proposal recorded: %s class=%s", proposal.audit_id, proposal.classification)

    def get_proposals(self, status: Optional[str] = None) -> list[ImprovementProposal]:
        data = self._read_json("backlog")
        if not isinstance(data, list):
            return []
        result = []
        for raw in data:
            try:
                p = ImprovementProposal.model_validate(raw)
                if status is None or p.status == status:
                    result.append(p)
            except Exception:
                pass
        return result

    # ── Stats ──────────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        """Return record counts per store."""
        return {
            "decisions": len(self._read_jsonl("decision", DecisionMemory)),
            "reflections": len(self._read_jsonl("reflection", ReflectionMemory)),
            "scorecards": len(self.get_scorecards()),
            "macro_events": len(self._read_jsonl("macro", MacroEventMemory)),
            "prompt_versions": len(self.get_prompt_versions()),
            "comparisons": len(self._read_jsonl("comparison", ModelComparison)),
            "feedback": len(self._read_jsonl("feedback", HumanFeedback)),
            "proposals": len(self.get_proposals()),
        }

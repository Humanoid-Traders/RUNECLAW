"""
RUNECLAW — nightly LLM self-audit (advisory only, human merge gate).

Once a night the bot reads its own evidence — recent closed trades, the
shadow book's per-gate price tags, governor/throttle state — and asks the
LLM: "given THIS, which of the allowlisted knobs would you turn?" Every
proposal is then MEASURED on a frozen benchmark before the operator sees
it: the report that lands in Telegram carries the rationale, the measured
delta, and the exact env diff to apply.

Nothing is ever applied automatically. The audit cannot invent knobs (a
fixed allowlist with type/bounds validation), cannot exceed bounds, and
cannot skip measurement — an unmeasured proposal is reported as such and
marked NOT VERIFIED. The human is the merge gate, exactly like a PR.

Cost/safety posture: runs at a configured quiet hour, at most once per
~24h (persisted stamp survives restarts), skips silently when no LLM is
configured, caps proposals, runs benchmark subprocesses sequentially, and
every stage fails open — a broken audit can never touch trading.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess  # nosec B404 — fixed argv, no shell, own runner module
import sys
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
DEFAULT_STATE_FILE = os.path.join(_STATE_DIR, "self_audit_state.json")

_MIN_INTERVAL_SEC = 20 * 3600.0   # once a night, restart-proof
_BACKTEST_TIMEOUT_SEC = 900.0     # per benchmark run

# The ONLY knobs the audit may propose. Everything else the LLM suggests
# is dropped at validation. Bounds are deliberately tighter than the
# config's own env bounds — the audit proposes tweaks, not regime changes.
ALLOWED_FLAGS: dict[str, dict[str, Any]] = {
    "EQUITY_THROTTLE_ENABLED": {"type": "bool"},
    "ENTRY_TIMING_ENABLED": {"type": "bool"},
    "STRUCTURE_TRAIL_ENABLED": {"type": "bool"},
    "CANDLE_ENTRY_VETO_ENABLED": {"type": "bool"},
    "REENTRY_COOLDOWN_SECONDS": {"type": "float", "min": 0, "max": 14400},
    "TREND_UP_SIZE_MULT": {"type": "float", "min": 0.3, "max": 1.2},
    "LIVE_PERF_REDUCE_WINRATE": {"type": "float", "min": 0.25, "max": 0.55},
    "LIVE_PERF_REDUCE_MULT": {"type": "float", "min": 0.25, "max": 0.75},
    "VOLATILITY_GUARD_ATR_PCT": {"type": "float", "min": 0.03, "max": 0.15},
    "SYMBOL_LOSS_STREAK_THRESHOLD": {"type": "float", "min": 2, "max": 6},
}

_SYSTEM_PROMPT = """You are the nightly self-audit of RUNECLAW, a live \
crypto perpetuals trading bot. You receive the bot's own recent evidence \
and may propose changes ONLY to the allowlisted environment flags given, \
within their bounds. Propose a change only when the evidence supports it; \
an empty list is a good answer. Respond with STRICT JSON only — an array \
of at most {max_proposals} objects, no prose, no markdown fences:
[{{"flag": "<ALLOWLISTED_FLAG>", "value": <bool|number>, \
"rationale": "<one sentence tied to the evidence>"}}]"""


def validate_proposals(raw: list, current_env: Optional[dict] = None,
                       max_proposals: int = 2) -> list[dict]:
    """Filter LLM proposals to allowlisted flags with in-bounds values.

    Drops: unknown flags, wrong types, out-of-bounds values, duplicates,
    and no-ops (value equals the current env setting when provided).
    Pure — no I/O — so the gate is unit-testable."""
    out: list[dict] = []
    seen: set[str] = set()
    env = current_env if current_env is not None else {}
    for p in (raw or []):
        try:
            flag = str(p.get("flag", "")).strip().upper()
            spec = ALLOWED_FLAGS.get(flag)
            if spec is None or flag in seen:
                continue
            value = p.get("value")
            if spec["type"] == "bool":
                if not isinstance(value, bool):
                    continue
                norm = "1" if value else "0"
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                v = float(value)
                if not (spec["min"] <= v <= spec["max"]):
                    continue
                norm = f"{v:g}"
            if env.get(flag) is not None and str(env.get(flag)) == norm:
                continue  # no-op
            seen.add(flag)
            out.append({"flag": flag, "value": norm,
                        "rationale": str(p.get("rationale", ""))[:240]})
            if len(out) >= max_proposals:
                break
        except Exception:
            continue
    return out


def parse_llm_json(text: str) -> list:
    """Extract the first JSON array from an LLM response, tolerating
    markdown fences and surrounding prose. Returns [] when unparseable."""
    try:
        m = re.search(r"\[.*\]", text or "", re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _parse_metrics(stdout: str) -> dict:
    """Pull the runner's headline metrics out of its stdout."""
    out: dict[str, float] = {}
    pats = {"return_pct": r"Total Return:\s*([+-]?[\d.]+)%",
            "trades": r"Total Trades:\s*(\d+)",
            "pf": r"Profit Factor:\s*([\d.]+|inf)",
            "max_dd_pct": r"Max Drawdown:\s*([\d.]+)%"}
    for key, pat in pats.items():
        m = re.search(pat, stdout or "")
        if m:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                pass
    return out


def run_benchmark(dataset: str, env_overrides: Optional[dict] = None) -> dict:
    """Run one frozen-benchmark backtest in a subprocess and return its
    headline metrics ({} on any failure). Blocking — callers off-load."""
    try:
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (env_overrides or {}).items()})
        proc = subprocess.run(  # nosec B603 — fixed argv, no shell
            [sys.executable, "-m", "bot.backtest.runner",
             "--dataset", os.path.join("data", "benchmark", dataset),
             "--honest"],
            capture_output=True, text=True, env=env,
            timeout=_BACKTEST_TIMEOUT_SEC)
        return _parse_metrics(proc.stdout)
    except Exception as exc:
        logger.warning("self-audit benchmark run failed: %s", exc)
        return {}


class SelfAudit:
    """Nightly evidence -> LLM proposals -> measured verdicts -> report."""

    def __init__(self, state_file: Optional[str] = None,
                 run_backtest: Optional[Callable[..., dict]] = None) -> None:
        self.state_file = state_file or DEFAULT_STATE_FILE
        self._run_backtest = run_backtest or run_benchmark
        self._running = False
        self._pending: list[dict] = []
        self._last_report: str = ""

    # ── persistence ───────────────────────────────────────────────
    def _load_state(self) -> dict:
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return dict(json.load(f))
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, self.state_file)
        except Exception as exc:
            logger.debug("self-audit state save failed: %s", exc)

    def last_report(self) -> str:
        if self._last_report:
            return self._last_report
        return str(self._load_state().get("last_report", ""))

    def drain_pending(self) -> list[dict]:
        out, self._pending = self._pending, []
        return out

    # ── scheduling ────────────────────────────────────────────────
    def due(self, now_ts: Optional[float] = None) -> bool:
        """True when the audit should run: inside the configured UTC hour
        and at least ~24h since the last run (persisted)."""
        from bot.config import CONFIG
        if not getattr(CONFIG, "self_audit_enabled", False) or self._running:
            return False
        now = float(now_ts if now_ts is not None else time.time())
        hour = int(time.gmtime(int(now)).tm_hour)
        if hour != int(getattr(CONFIG, "self_audit_hour_utc", 4)):
            return False
        last = float(self._load_state().get("last_run_ts", 0) or 0)
        return (now - last) >= _MIN_INTERVAL_SEC

    def maybe_spawn(self, engine, now_ts: Optional[float] = None) -> bool:
        """Fire-and-forget the nightly run when due. Never raises."""
        try:
            if not self.due(now_ts):
                return False
            asyncio.get_running_loop().create_task(self.run(engine))
            return True
        except Exception as exc:
            logger.debug("self-audit spawn skipped: %s", exc)
            return False

    # ── evidence ──────────────────────────────────────────────────
    def gather_evidence(self, engine) -> dict:
        """Snapshot the bot's own recent record. Every piece fail-open."""
        ev: dict[str, Any] = {}
        try:
            ex = getattr(engine, "live_executor", None)
            closed = list(getattr(ex, "_closed_trades", []) or [])[-40:]
            trades = []
            for t in closed:
                trades.append({
                    "symbol": getattr(t, "symbol", "?"),
                    "dir": getattr(t, "direction", "?"),
                    "strategy": getattr(t, "strategy_type", "") or "",
                    "net_pnl": round(float(getattr(t, "net_pnl", 0) or 0), 3),
                    "reason": str(getattr(t, "close_reason", "") or "")[:40],
                })
            ev["closed_trades"] = trades
            pnls = [float(t["net_pnl"]) for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [-p for p in pnls if p < 0]
            ev["summary"] = {
                "n": len(pnls),
                "win_rate": round(len(wins) / len(pnls), 3) if pnls else None,
                "net_pnl": round(sum(pnls), 2),
                "pf": round(sum(wins) / sum(losses), 2) if losses and sum(losses) > 0 else None,
            }
        except Exception:
            ev["closed_trades"] = []
        try:
            from bot.core.shadow_book import SHADOW_BOOK
            ev["shadow_gates"] = SHADOW_BOOK.gate_report()
            ev["shadow_counts"] = SHADOW_BOOK.counts()
        except Exception:
            pass
        try:
            risk = getattr(engine, "risk", None)
            if risk is not None:
                ev["governor"] = risk.live_performance_state()
                ev["throttle"] = risk.equity_throttle_state()
        except Exception:
            pass
        return ev

    # ── the run ───────────────────────────────────────────────────
    async def run(self, engine) -> Optional[str]:
        """Full audit cycle. Returns the report text, or None if skipped."""
        from bot.config import CONFIG
        if self._running:
            return None
        self._running = True
        try:
            analyzer = getattr(engine, "analyzer", None)
            cfg = analyzer._resolve_llm_config() if analyzer else None
            client = (analyzer._build_client_for_config(cfg)
                      if analyzer and cfg else None)
            if client is None or cfg is None:
                logger.info("self-audit skipped: no LLM configured")
                return None

            evidence = self.gather_evidence(engine)
            max_props = int(getattr(CONFIG, "self_audit_max_proposals", 2))
            bounds = {k: ({"type": "bool"} if v["type"] == "bool" else
                          {"type": "number", "min": v["min"], "max": v["max"]})
                      for k, v in ALLOWED_FLAGS.items()}
            user_prompt = (
                "ALLOWLISTED FLAGS AND BOUNDS:\n"
                + json.dumps(bounds, indent=1)
                + "\n\nCURRENT ENV OVERRIDES (unset = default):\n"
                + json.dumps({k: os.environ.get(k) for k in ALLOWED_FLAGS
                              if os.environ.get(k) is not None})
                + "\n\nEVIDENCE:\n" + json.dumps(evidence, default=str)[:12000])

            from bot.llm.provider import llm_complete
            text = await llm_complete(
                client, cfg,
                _SYSTEM_PROMPT.format(max_proposals=max_props),
                user_prompt)
            proposals = validate_proposals(
                parse_llm_json(text),
                current_env={k: os.environ.get(k) for k in ALLOWED_FLAGS},
                max_proposals=max_props)

            dataset = str(getattr(CONFIG, "self_audit_dataset", "alts_1h"))
            baseline: dict = {}
            results: list[dict] = []
            if proposals:
                baseline = await asyncio.to_thread(self._run_backtest, dataset)
            for p in proposals:
                measured = await asyncio.to_thread(
                    self._run_backtest, dataset, {p["flag"]: p["value"]})
                results.append({**p, "measured": measured})

            report = self.render_report(evidence, results, baseline, dataset)
            self._last_report = report
            self._pending.append({"report": report, "ts": time.time()})
            self._save_state({"last_run_ts": time.time(),
                              "last_report": report})
            return report
        except Exception as exc:
            logger.warning("self-audit run failed: %s", exc)
            return None
        finally:
            self._running = False

    # ── reporting ─────────────────────────────────────────────────
    @staticmethod
    def render_report(evidence: dict, results: list[dict],
                      baseline: dict, dataset: str) -> str:
        s = evidence.get("summary") or {}
        lines = ["\U0001f9fe <b>Nightly self-audit</b>", "─" * 16]
        if s.get("n"):
            pf = s.get("pf")
            wr = s.get("win_rate")
            lines.append(f"Live window: {s['n']} closes"
                         + (f" · win {wr*100:.0f}%" if wr is not None else "")
                         + (f" · PF {pf}" if pf is not None else "")
                         + f" · net ${s.get('net_pnl', 0):,.2f}")
        gates = evidence.get("shadow_gates") or {}
        worst = next(iter(gates.items()), None)
        if worst and worst[1].get("net_r", 0) > 0.5:
            lines.append(f"Shadow book: <code>{worst[0][:28]}</code> is the "
                         f"costliest gate (net {worst[1]['net_r']:+.1f}R "
                         f"over {worst[1]['n']} blocked trades)")
        if not results:
            lines.append("")
            lines.append("No changes proposed — the evidence supports the "
                         "current configuration. (An empty audit is a pass, "
                         "not a failure.)")
            return "\n".join(lines)
        base_ret = baseline.get("return_pct")
        base_s = (f"{base_ret:+.2f}% / PF {baseline.get('pf', '?')}"
                  if base_ret is not None else "unavailable")
        lines.append(f"\nBenchmark <code>{dataset}</code> baseline: {base_s}")
        for r in results:
            m = r.get("measured") or {}
            ret = m.get("return_pct")
            if ret is None or base_ret is None:
                verdict = "⬜ NOT VERIFIED (benchmark run failed)"
            else:
                delta = ret - base_ret
                icon = "\U0001f7e9" if delta > 0 else (
                    "\U0001f7e5" if delta < 0 else "⬜")
                verdict = (f"{icon} measured {ret:+.2f}% "
                           f"({delta:+.2f}pp vs baseline) · "
                           f"PF {m.get('pf', '?')} · {int(m.get('trades', 0))}tr")
            lines.append(f"\n<b>{r['flag']}={r['value']}</b>\n"
                         f"  {r['rationale']}\n"
                         f"  {verdict}\n"
                         f"  Apply: <code>{r['flag']}={r['value']}</code> "
                         f"(env + restart) — nothing auto-applied")
        return "\n".join(lines)


# Shared singleton (same pattern as SHADOW_BOOK / catalog watch).
SELF_AUDIT = SelfAudit()

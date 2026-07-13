"""
RUNECLAW — Counterfactual shadow book.

Every idea a gate REJECTS becomes a paper trade in this ledger, filled
and exited off the live ticker stream the scanner already fetches. Every
gate then carries a live, continuously-updated price tag: a gate whose
blocked trades NET POSITIVE R is eating edge; one whose blocked trades
net negative is saving money. This is the substrate for the equity-curve
throttle and the nightly self-audit — and it retro-answers "should this
gate be on?" questions without a backtest.

Recording-only: nothing here ever places, sizes, or influences a real
order. Zero extra API calls — update() rides the futures ticker map the
scanner fetches each cycle (same pattern as the catalog watch).

Accounting is in R-multiples (risk units), not dollars: a shadow trade
has no real size, so its outcome is (exit − entry) / (entry − stop),
signed by direction. Fill semantics mirror live limit entries: a shadow
trade FILLS only when price touches its entry within the limit-expiry
window; untouched entries become "never_filled" and are excluded — the
same non-fill hygiene the parity report applies to real records.

V1 simplifications (documented, conservative): exits use last-price
ticks (no intrabar wicks — misses some SL and some TP touches alike),
static SL/TP only (no trailing), 7-day hard expiry closed at mark.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
DEFAULT_STATE_FILE = os.path.join(_STATE_DIR, "shadow_book.json")

# Mirror live limit-entry expiry (4h) and a hard trade horizon (7d).
FILL_WINDOW_SEC = 14400.0
TRADE_HORIZON_SEC = 7 * 86400.0

_MAX_LIVE = 300      # pending + open cap (newest kept)
_MAX_CLOSED = 2000   # closed-history cap


def _base(symbol: str) -> str:
    s = (symbol or "").upper()
    i = s.find("/")
    return s[:i] if i > 0 else s


class ShadowBook:
    """Persistent counterfactual ledger of gate-rejected trades."""

    def __init__(self, state_file: Optional[str] = None) -> None:
        self.state_file = state_file or DEFAULT_STATE_FILE
        self._trades: list[dict] = []
        self._loaded = False

    # ── persistence ───────────────────────────────────────────────
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self.state_file, encoding="utf-8") as f:
                self._trades = list(json.load(f).get("trades", []))
        except FileNotFoundError:
            pass
        except Exception as exc:  # corrupt state must never break anything
            logger.warning("shadow_book.json unreadable (%s) — starting fresh", exc)

    def _save(self) -> None:
        try:
            live = [t for t in self._trades if t["status"] in ("pending", "open")]
            done = [t for t in self._trades if t["status"] not in ("pending", "open")]
            self._trades = done[-_MAX_CLOSED:] + live[-_MAX_LIVE:]
            os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"trades": self._trades}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_file)
        except Exception as exc:
            logger.debug("shadow book save failed: %s", exc)

    # ── recording ─────────────────────────────────────────────────
    def record_rejection(self, idea, gates, reason: str,
                         ref_price: float = 0.0,
                         now_ts: Optional[float] = None) -> Optional[dict]:
        """Enter a rejected idea into the ledger. Never raises.

        ``gates`` is the risk check's failed-gate list; the FIRST entry is
        the primary gate charged with the outcome. Degenerate ideas
        (missing/inverted levels) are skipped — nothing to simulate.
        """
        try:
            self._load()
            entry = float(getattr(idea, "entry_price", 0) or 0)
            sl = float(getattr(idea, "stop_loss", 0) or 0)
            tp = float(getattr(idea, "take_profit", 0) or 0)
            direction = getattr(getattr(idea, "direction", None), "value",
                                str(getattr(idea, "direction", "")))
            if entry <= 0 or sl <= 0 or tp <= 0:
                return None
            is_long = direction == "LONG"
            if is_long and not (sl < entry < tp):
                return None
            if (not is_long) and not (tp < entry < sl):
                return None
            gate_list = [str(g) for g in (gates or [])][:5] or ["(unspecified)"]
            now = float(now_ts if now_ts is not None else time.time())
            trade = {
                "id": f"SB-{uuid.uuid4().hex[:8]}",
                "idea_id": str(getattr(idea, "id", "")),
                "symbol": str(getattr(idea, "asset", "")),
                "direction": direction,
                "entry": entry, "sl": sl, "tp": tp,
                "gate": gate_list[0], "gates": gate_list,
                "reason": str(reason or "")[:160],
                "strategy_type": str(getattr(idea, "strategy_type", "") or ""),
                "created_ts": now,
                "status": "pending",   # fills only if price touches entry
                "fill_ts": None, "exit_ts": None,
                "exit_price": None, "outcome": None, "r": None,
            }
            # Marketable at record time (entry at/through the current price)
            # fills immediately — mirrors a market/instantly-fillable limit.
            if ref_price > 0:
                if (is_long and ref_price <= entry) or \
                        ((not is_long) and ref_price >= entry):
                    trade["status"] = "open"
                    trade["fill_ts"] = now
            self._trades.append(trade)
            self._save()
            return trade
        except Exception as exc:  # noqa: BLE001
            logger.debug("shadow book record failed: %s", exc)
            return None

    # ── tick update ───────────────────────────────────────────────
    def update(self, tickers: dict, now_ts: Optional[float] = None) -> int:
        """Advance the ledger one tick using a {symbol: ticker} map (the
        scanner's futures map — keys in ccxt form). Returns the number of
        state changes. Never raises."""
        try:
            self._load()
            now = float(now_ts if now_ts is not None else time.time())
            # Index last prices by base for tolerant symbol matching.
            last_by_base: dict[str, float] = {}
            for sym, t in (tickers or {}).items():
                try:
                    px = float((t or {}).get("last") or 0)
                    if px > 0:
                        last_by_base[_base(sym)] = px
                except Exception:
                    continue
            changed = 0
            for tr in self._trades:
                if tr["status"] not in ("pending", "open"):
                    continue
                last = last_by_base.get(_base(tr["symbol"]))
                is_long = tr["direction"] == "LONG"
                if tr["status"] == "pending":
                    if now - tr["created_ts"] > FILL_WINDOW_SEC:
                        tr["status"] = "never_filled"
                        tr["exit_ts"] = now
                        changed += 1
                        continue
                    if last is None:
                        continue
                    if (is_long and last <= tr["entry"]) or \
                            ((not is_long) and last >= tr["entry"]):
                        tr["status"] = "open"
                        tr["fill_ts"] = now
                        changed += 1
                    continue
                # open
                risk = abs(tr["entry"] - tr["sl"])
                if risk <= 0:
                    tr["status"] = "closed"
                    tr["outcome"] = "void"
                    tr["r"] = 0.0
                    tr["exit_ts"] = now
                    changed += 1
                    continue
                if last is not None:
                    hit_sl = last <= tr["sl"] if is_long else last >= tr["sl"]
                    hit_tp = last >= tr["tp"] if is_long else last <= tr["tp"]
                    if hit_sl:      # pessimistic: stop checked first
                        tr.update(status="closed", outcome="sl",
                                  exit_price=tr["sl"], exit_ts=now, r=-1.0)
                        changed += 1
                        continue
                    if hit_tp:
                        r = abs(tr["tp"] - tr["entry"]) / risk
                        tr.update(status="closed", outcome="tp",
                                  exit_price=tr["tp"], exit_ts=now,
                                  r=round(r, 3))
                        changed += 1
                        continue
                if now - (tr["fill_ts"] or tr["created_ts"]) > TRADE_HORIZON_SEC:
                    px = last if last is not None else tr["entry"]
                    signed = (px - tr["entry"]) if is_long else (tr["entry"] - px)
                    tr.update(status="closed", outcome="expired",
                              exit_price=px, exit_ts=now,
                              r=round(signed / risk, 3))
                    changed += 1
            if changed:
                self._save()
            return changed
        except Exception as exc:  # noqa: BLE001
            logger.debug("shadow book update failed: %s", exc)
            return 0

    # ── reporting ─────────────────────────────────────────────────
    def gate_report(self) -> dict:
        """Per-gate scoreboard over CLOSED shadow trades.

        net_r POSITIVE = the gate blocked profitable trades (it is eating
        edge); NEGATIVE = the gate saved money. never_filled excluded."""
        self._load()
        out: dict[str, dict] = {}
        for tr in self._trades:
            if tr["status"] != "closed" or tr.get("r") is None:
                continue
            g = out.setdefault(tr["gate"], {
                "n": 0, "wins": 0, "losses": 0, "net_r": 0.0})
            g["n"] += 1
            g["net_r"] = round(g["net_r"] + float(tr["r"]), 3)
            if float(tr["r"]) > 0:
                g["wins"] += 1
            elif float(tr["r"]) < 0:
                g["losses"] += 1
        for g in out.values():
            g["avg_r"] = round(g["net_r"] / g["n"], 3) if g["n"] else 0.0
        return dict(sorted(out.items(), key=lambda kv: kv[1]["net_r"],
                           reverse=True))

    def counts(self) -> dict:
        self._load()
        c: dict[str, int] = {}
        for tr in self._trades:
            c[tr["status"]] = c.get(tr["status"], 0) + 1
        return c

    def render_report(self) -> str:
        """Telegram-ready gate scoreboard."""
        rep = self.gate_report()
        c = self.counts()
        lines = ["<b>Shadow book — what the gates cost</b>",
                 "─" * 16,
                 f"Tracked: {c.get('pending', 0)} pending · "
                 f"{c.get('open', 0)} open · {c.get('closed', 0)} closed · "
                 f"{c.get('never_filled', 0)} never filled", ""]
        if not rep:
            lines.append("No closed shadow trades yet — the ledger fills "
                         "as gates reject ideas.")
            return "\n".join(lines)
        lines.append("net R > 0 = the gate is BLOCKING winners:")
        for gate, g in list(rep.items())[:12]:
            icon = "\U0001f7e5" if g["net_r"] > 0.5 else (
                "\U0001f7e9" if g["net_r"] < -0.5 else "⬜")
            lines.append(
                f"{icon} <code>{gate[:32]}</code> — {g['n']}tr · "
                f"net {g['net_r']:+.1f}R · avg {g['avg_r']:+.2f}R")
        return "\n".join(lines)


# Shared singleton (same pattern as CROSS_VENUE): the engine records into
# it, the scanner ticks it with the cycle's ticker map.
SHADOW_BOOK = ShadowBook()

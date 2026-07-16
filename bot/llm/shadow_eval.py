"""
LLM shadow A/B — benchmark a challenger model against the primary on the
SAME live prompts, without letting it anywhere near a trading decision.

Why: the in-house runeclaw model (or any new provider) should earn tier
routing with evidence, not vibes. When enabled, every primary thesis call
also fires the identical prompt at the SHADOW provider in the background;
both answers land in ``data/learning/llm_shadow.jsonl``. Later, /llmab
joins those records with realized closed trades and reports each model's
directional hit rate on the same events — a like-for-like comparison on
live market data.

Isolation guarantees:
  - the shadow answer is NEVER read by the trading path — records only;
  - fire-and-forget with a bounded in-flight count, so a slow or dead
    shadow endpoint cannot delay a scan;
  - fail-silent: any shadow error increments a counter and is dropped.

Enable with:
    LLM_SHADOW_ENABLED=true
    LLM_SHADOW_PROVIDER=runeclaw            # any provider from the catalog
    LLM_SHADOW_MODEL=runeclaw-v6            # optional (provider default)
    LLM_SHADOW_SAMPLE_PCT=100               # optional throttle
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from bot.compat import UTC

logger = logging.getLogger(__name__)

_RECORD_FILE = (Path(__file__).resolve().parent.parent.parent
                / "data" / "learning" / "llm_shadow.jsonl")
_MAX_IN_FLIGHT = 2


def _enabled() -> bool:
    return (os.environ.get("LLM_SHADOW_ENABLED", "").strip().lower()
            in ("1", "true", "yes", "on")
            and bool(os.environ.get("LLM_SHADOW_PROVIDER", "").strip()))


class ShadowEval:
    """Singleton-ish runner holding the shadow client + in-flight guard."""

    def __init__(self) -> None:
        self._client = None
        self._cfg = None
        self._client_key = ""       # provider|model the cached client was built for
        self._in_flight = 0
        self.errors = 0
        self.recorded = 0

    # ── client management ─────────────────────────────────────────
    def _resolve(self, analyzer):
        """(client, cfg) for the shadow provider, cached until env changes."""
        from bot.llm.provider import LLMConfig, LLMProvider
        provider_s = os.environ.get("LLM_SHADOW_PROVIDER", "").strip().lower()
        model = os.environ.get("LLM_SHADOW_MODEL", "").strip()
        key = f"{provider_s}|{model}"
        if self._client is not None and key == self._client_key:
            return self._client, self._cfg
        try:
            provider = LLMProvider(provider_s)
        except ValueError:
            logger.warning("shadow eval: unknown provider %r", provider_s)
            return None, None
        cfg = LLMConfig(
            provider=provider, model=model,
            api_key=os.environ.get("LLM_SHADOW_API_KEY", "")
            or os.environ.get(f"{provider_s.upper()}_LLM_API_KEY", "")
            or os.environ.get(f"{provider_s.upper()}_API_KEY", ""),
            base_url=os.environ.get("LLM_SHADOW_BASE_URL", ""),
        )
        client = analyzer._build_client_for_config(cfg)
        if client is None:
            logger.warning("shadow eval: could not build client for %s", provider_s)
            return None, None
        self._client, self._cfg, self._client_key = client, cfg, key
        return client, cfg

    # ── the hook the analyzer calls ───────────────────────────────
    def maybe_spawn(self, analyzer, prompt: str, prompt_hash: str,
                    symbol: str, primary: dict) -> None:
        """Fire the shadow call in the background. Cheap no-op when disabled,
        over the in-flight cap, or sampled out. Never raises."""
        try:
            if not _enabled() or self._in_flight >= _MAX_IN_FLIGHT:
                return
            sample = float(os.environ.get("LLM_SHADOW_SAMPLE_PCT", "100") or 100)
            if sample < 100 and random.random() * 100 >= sample:
                return
            loop = asyncio.get_running_loop()
            loop.create_task(self._run(analyzer, prompt, prompt_hash,
                                       symbol, dict(primary)))
        except Exception as exc:
            logger.debug("shadow eval spawn skipped: %s", exc)

    async def _run(self, analyzer, prompt: str, prompt_hash: str,
                   symbol: str, primary: dict) -> None:
        self._in_flight += 1
        t0 = time.monotonic()
        try:
            client, cfg = self._resolve(analyzer)
            if client is None:
                self.errors += 1
                return
            from bot.llm.provider import LLMProvider
            sys_content = ("You are a disciplined crypto trading analyst. "
                           "Respond ONLY with JSON: {\"direction\": \"LONG|SHORT\", "
                           "\"confidence\": 0.0-1.0, \"reasoning\": \"...\"}")
            if cfg.provider == LLMProvider.ANTHROPIC:
                resp = await asyncio.wait_for(
                    client.messages.create(
                        model=cfg.model, max_tokens=512, system=sys_content,
                        messages=[{"role": "user", "content": prompt}]),
                    timeout=25)
                raw = ""
                for block in (resp.content or []):
                    if getattr(block, "type", "") == "text":
                        raw = block.text
                        break
            else:
                resp = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=cfg.model,
                        messages=[{"role": "system", "content": sys_content},
                                  {"role": "user", "content": prompt}],
                        temperature=0.3, max_tokens=512),
                    timeout=25)
                raw = resp.choices[0].message.content or ""
            parsed = analyzer._parse_llm_response(raw)
            if not parsed.pop("_parsed", False):
                self.errors += 1
                return
            record = {
                "ts": datetime.now(UTC).isoformat(),
                "symbol": symbol,
                "prompt_hash": prompt_hash,
                "primary_model": primary.get("model_used", ""),
                "primary_direction": primary.get("direction"),
                "primary_confidence": round(float(primary.get("confidence", 0) or 0), 4),
                "shadow_model": cfg.model,
                "shadow_direction": parsed.get("direction"),
                "shadow_confidence": round(float(parsed.get("confidence", 0) or 0), 4),
                "shadow_latency_ms": int((time.monotonic() - t0) * 1000),
            }
            _RECORD_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_RECORD_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")
            self.recorded += 1
        except Exception as exc:
            self.errors += 1
            logger.debug("shadow eval call failed: %s", exc)
        finally:
            self._in_flight -= 1


SHADOW = ShadowEval()


# ─── Scoring: join shadow records with realized closed trades ────────────────

def load_records(path: Optional[Path] = None) -> list[dict]:
    p = Path(path) if path else _RECORD_FILE
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _base(sym: Any) -> str:
    return str(sym or "").split("/")[0].split(":")[0].upper()


def _parse_ts(v: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(v))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def score_against_trades(records: list[dict], trades: list[dict],
                         match_window_min: float = 45.0) -> dict:
    """Join each shadow record to a REAL trade opened on the same coin within
    the window, and score both models' directions against the realized PnL.

    correct := (said the trade's direction and it won) or
               (said the opposite and it lost).
    Zero-PnL trades and unmatched records are excluded from scoring (counted).
    """
    window = timedelta(minutes=match_window_min)
    indexed: list[tuple[str, datetime, dict]] = []
    for t in trades:
        ts = _parse_ts(t.get("opened_at"))
        if ts is not None:
            indexed.append((_base(t.get("symbol")), ts, t))

    def _correct(direction: Any, trade: dict) -> Optional[bool]:
        pnl = float(trade.get("pnl_usd", 0) or 0)
        if pnl == 0 or direction not in ("LONG", "SHORT"):
            return None
        won = pnl > 0
        same = str(direction).upper() == str(trade.get("direction", "")).upper()
        return same == won

    stats = {"records": len(records), "matched": 0, "agreement": 0,
             "primary_correct": 0, "shadow_correct": 0, "scored": 0,
             "primary_model": "", "shadow_model": ""}
    for r in records:
        if r.get("primary_direction") == r.get("shadow_direction"):
            stats["agreement"] += 1
        stats["primary_model"] = r.get("primary_model") or stats["primary_model"]
        stats["shadow_model"] = r.get("shadow_model") or stats["shadow_model"]
        rts = _parse_ts(r.get("ts"))
        if rts is None:
            continue
        base = _base(r.get("symbol"))
        trade = next((t for b, ts, t in indexed
                      if b == base and abs(ts - rts) <= window), None)
        if trade is None:
            continue
        stats["matched"] += 1
        p_ok = _correct(r.get("primary_direction"), trade)
        s_ok = _correct(r.get("shadow_direction"), trade)
        if p_ok is None or s_ok is None:
            continue
        stats["scored"] += 1
        stats["primary_correct"] += int(p_ok)
        stats["shadow_correct"] += int(s_ok)
    return stats


def format_ab_html(stats: dict) -> str:
    n, scored = stats["records"], stats["scored"]
    if not n:
        return ("🟡 <b>LLM shadow A/B</b>\nNo shadow records yet. Enable with "
                "<code>LLM_SHADOW_ENABLED=true</code> + "
                "<code>LLM_SHADOW_PROVIDER=runeclaw</code> and let it run "
                "through some scans.")
    agree_pct = stats["agreement"] / n * 100
    lines = [
        "🥊 <b>LLM shadow A/B — same prompts, live market</b>",
        f"Prompts shadowed: <b>{n}</b> · direction agreement "
        f"<code>{agree_pct:.0f}%</code>",
        f"Primary: <code>{stats['primary_model'] or '?'}</code> · "
        f"Shadow: <code>{stats['shadow_model'] or '?'}</code>",
    ]
    if scored:
        p = stats["primary_correct"] / scored * 100
        s = stats["shadow_correct"] / scored * 100
        verdict = ("shadow ahead — consider promoting a tier" if s > p + 5 else
                   "primary ahead — keep routing as is" if p > s + 5 else
                   "statistical tie so far — keep collecting")
        lines.append(
            f"Scored on <b>{scored}</b> realized trades:\n"
            f"- primary directional hit rate <code>{p:.0f}%</code>\n"
            f"- shadow directional hit rate <code>{s:.0f}%</code>\n"
            f"→ <i>{verdict}</i>")
    else:
        lines.append("<i>No records matched a realized trade yet — scoring "
                     "needs closes on shadowed symbols.</i>")
    lines.append("<i>The shadow model never influences trading — records "
                 "only. Promote it via LLM_TIER_*_PROVIDER once it wins.</i>")
    return "\n\n".join(lines)

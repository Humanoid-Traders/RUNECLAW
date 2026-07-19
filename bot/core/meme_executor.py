"""Memecoin DEX executor — Solana / Jupiter — PLANNER + PREFLIGHT (no signing).

Scope: "trade, don't launch." This first slice is the executor's BRAIN — it
turns a buy/sell intent into a fail-closed, fully-preconditioned Jupiter swap
*plan*. It NEVER signs or broadcasts a transaction: ``would_execute`` is always
False here. Signing on the user's own key is a later, separately-gated slice.

Every buy must clear, in order (all fail-closed):
  1. feature flag ``MEME_TRADING_ENABLED`` (default OFF),
  2. the human-set Authority Envelope authorizing this trade,
  3. the meme-buy safety gate (rug/honeypot + liquidity/age/exit + sizing).

SELLS are treated as EXITS and are never blocked by the safety gate — being
able to dump a rug is itself the safety property. Sells still require the
feature flag + envelope authority.

Custody: RUNECLAW never holds funds; the plan is executed (in a future slice)
by signing on the user's OWN key. The agent never mints tokens.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from bot.core import meme_gate

# Canonical Solana mints (base58). USDC is the quote leg for buys/sells.
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

BUY = "buy"
SELL = "sell"


def feature_enabled(env: Optional[dict] = None) -> bool:
    """Master switch for any memecoin trade — default OFF (fail-closed)."""
    e = env if env is not None else os.environ
    return str(e.get("MEME_TRADING_ENABLED", "")).strip().lower() in ("1", "true", "yes", "on")


def _num(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if (v == v and v not in (float("inf"), float("-inf"))) else None
    except (TypeError, ValueError):
        return None


def plan_swap(*, intent: Optional[dict] = None,
              safety_report: Optional[dict] = None,
              radar_risk: Optional[dict] = None,
              market: Optional[dict] = None,
              envelope_authorized: Optional[bool] = None,
              feature_on: Optional[bool] = None,
              gate_params: Optional[dict] = None,
              env: Optional[dict] = None) -> dict:
    """Build a fail-closed Jupiter swap plan for a memecoin intent.

    ``intent`` = {side: 'buy'|'sell', token_mint, symbol?, size_usd,
    slippage_bps?}. ``market`` carries the live read the buy-gate needs
    (liquidity_usd, age_hours, sells_24h, buys_24h). Returns::

        {allowed, would_execute: False, side, reason, preconditions:[...],
         gate, jupiter_request:{inputMint,outputMint,amount,slippageBps}|None}

    ``allowed`` is True only when every precondition passes; ``would_execute`` is
    always False (this slice plans; it never signs)."""
    intent = intent or {}
    market = market or {}
    side = str(intent.get("side") or "").strip().lower()
    token_mint = str(intent.get("token_mint") or "").strip()
    size_usd = _num(intent.get("size_usd"))
    slippage_bps = int(_num(intent.get("slippage_bps")) or 100)   # 1% default
    feat = feature_enabled(env) if feature_on is None else bool(feature_on)

    pre: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        pre.append({"name": name, "ok": bool(ok), "detail": detail})

    # 0. Well-formed intent.
    valid_side = side in (BUY, SELL)
    add("intent_valid",
        valid_side and bool(token_mint) and size_usd is not None and size_usd > 0
        and 1 <= slippage_bps <= 2000,
        f"side={side or '?'} mint={'set' if token_mint else 'MISSING'} "
        f"size=${size_usd if size_usd is not None else '?'} slip={slippage_bps}bps")

    # 1. Feature flag — default OFF.
    add("feature_enabled", feat,
        "MEME_TRADING_ENABLED" + (" on" if feat else " OFF (default)"))

    # 2. Human-set Authority Envelope authorizes this trade.
    add("envelope_authorized", envelope_authorized is True,
        "authorized by envelope" if envelope_authorized is True
        else "no envelope authority (fail-closed)")

    # 3. Safety gate — BUYS only. Sells are exits and must never be blocked.
    gate = None
    if side == BUY:
        gate = meme_gate.evaluate_meme_buy(
            safety_report=safety_report, radar_risk=radar_risk,
            liquidity_usd=market.get("liquidity_usd"),
            age_hours=market.get("age_hours"),
            sells_24h=market.get("sells_24h"), buys_24h=market.get("buys_24h"),
            size_usd=size_usd, params=gate_params)
        add("safety_gate", gate.get("allowed") is True,
            gate.get("reason", "gate evaluated"))
    else:
        add("safety_gate", True, "sell is an exit — safety gate does not block exits")

    allowed = all(c["ok"] for c in pre)

    jup = None
    if valid_side and token_mint and size_usd is not None:
        # Buy: USDC -> token. Sell: token -> USDC. amount is a UI hint only
        # (base units require token decimals, resolved at execution time).
        in_mint = USDC_MINT if side == BUY else token_mint
        out_mint = token_mint if side == BUY else USDC_MINT
        jup = {
            "inputMint": in_mint, "outputMint": out_mint,
            "amount_usd": size_usd, "slippageBps": slippage_bps,
            "swapMode": "ExactIn",
            "note": "quote/amount in base units resolved at execution time",
        }

    return {
        "allowed": allowed,
        "would_execute": False,          # planner only — never signs here
        "side": side,
        "symbol": intent.get("symbol"),
        "token_mint": token_mint or None,
        "reason": ("plan ready — all preconditions passed" if allowed
                   else "blocked by: " + ", ".join(c["name"] for c in pre if not c["ok"])),
        "preconditions": pre,
        "gate": gate,
        "jupiter_request": jup,
        "venue": "solana:jupiter",
    }


def human_readable(plan: Optional[dict]) -> str:
    if not isinstance(plan, dict):
        return "No plan."
    head = ("✅ swap plan ready" if plan.get("allowed") else "⛔ swap blocked")
    head += f" ({plan.get('side', '?')} {plan.get('symbol') or plan.get('token_mint') or '?'})"
    lines = [head + " — " + str(plan.get("reason", "")),
             "   would_execute: False (planner only — no signing in this slice)"]
    for c in plan.get("preconditions", []):
        lines.append(f"   {'✓' if c['ok'] else '✗'} {c['name']}: {c['detail']}")
    return "\n".join(lines)

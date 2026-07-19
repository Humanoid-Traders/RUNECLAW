"""Common Statement Format v0 — the deterministic, provenance-honest core.

Pure: no engine, exchange, clock, or network. A ``Fill`` and an ``Epoch`` are
plain dicts (JSON-native) so ``verify.py`` can re-hash them byte-for-byte without
importing this package. Numbers are carried as **decimal strings** (never floats)
so the canonical form — and therefore every hash — is reproducible on any machine.

Two invariants this module enforces mechanically:

1. **Determinism.** ``canonical(obj)`` → the same bytes for the same logical value
   on any platform (sorted keys, no whitespace, decimal-string numbers). ``fill_hash``
   and the Merkle root are functions of those bytes only.
2. **Trust-tier honesty.** ``epoch_tier(fills)`` is the *minimum* tier across the
   fills. A weaker tier can never render as a stronger one — the headline tier is
   the epoch minimum, full stop.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

CSF_VERSION = "v0"

# Trust tiers, strongest → weakest. The integer is the ordering key; the epoch
# headline tier is the MINIMUM across its fills.
TRUST_TIERS = {
    "onchain_public": 3,       # re-derivable from public chain data
    "cex_tee_attested": 2,     # CEX fills fetched inside a TEE, attested
    "cex_operator_signed": 1,  # CEX fills signed by the operator only (weakest)
}


def _dec(x: Any) -> Optional[Decimal]:
    """Parse to Decimal, or None if not a finite number. Accepts int/float/str."""
    if x is None or isinstance(x, bool):
        return None
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not d.is_finite():
        return None
    return d


def _numstr(x: Any) -> Optional[str]:
    """Canonical decimal string for a number: plain (no exponent), no trailing
    ``.0`` ambiguity. ``None`` passes through as None. Used for every numeric
    field so the hash is float-free and reproducible."""
    d = _dec(x)
    if d is None:
        return None
    # format 'f' → plain decimal notation; normalize the zero case.
    s = format(d, "f")
    return s


# Numeric fields that must be carried as canonical decimal strings in a Fill.
_FILL_NUMERIC = ("price", "qty", "fee")
# The full ordered field set of a canonical Fill (excluding fill_hash).
_FILL_FIELDS = ("venue", "venue_type", "market", "side", "price", "qty",
                "fee", "fee_ccy", "ts", "source_ref", "trust_tier")


def make_fill(venue: str, venue_type: str, market: str, side: str,
              price: Any, qty: Any, fee: Any, fee_ccy: str, ts: int,
              source_ref: str, trust_tier: str) -> dict:
    """Build a canonical Fill dict (with its ``fill_hash``). Numbers → decimal
    strings; ``side`` normalized to buy/sell; ``ts`` an integer ms epoch."""
    if trust_tier not in TRUST_TIERS:
        raise ValueError(f"unknown trust_tier {trust_tier!r}")
    if venue_type not in ("cex", "onchain"):
        raise ValueError(f"unknown venue_type {venue_type!r}")
    fill = {
        "venue": str(venue),
        "venue_type": str(venue_type),
        "market": str(market),
        "side": "sell" if str(side).lower().startswith("s") else "buy",
        "price": _numstr(price),
        "qty": _numstr(qty),
        # Unknown fee stays None → the fill is INCOMPLETE (cannot reconcile).
        # Callers pass fee=0 explicitly only when the fee is known to be zero.
        "fee": _numstr(fee),
        "fee_ccy": str(fee_ccy or ""),
        "ts": int(ts),
        "source_ref": str(source_ref),
        "trust_tier": str(trust_tier),
    }
    fill["fill_hash"] = fill_hash(fill)
    return fill


def canonical(obj: Any) -> bytes:
    """Deterministic bytes for any JSON-native value: sorted keys, no whitespace,
    UTF-8. Numbers must already be strings (we never hash floats)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def fill_hash(fill: dict) -> str:
    """SHA-256 over the canonical fill (excluding the ``fill_hash`` field itself)."""
    core = {k: fill.get(k) for k in _FILL_FIELDS}
    return hashlib.sha256(canonical(core)).hexdigest()


def fill_is_complete(fill: dict) -> bool:
    """A fill is metrics-complete only if it carries a usable price, qty and fee.
    Incomplete fills force the epoch to ``INCOMPLETE`` (they cannot reconcile)."""
    return (_dec(fill.get("price")) is not None
            and _dec(fill.get("qty")) is not None
            and _dec(fill.get("fee")) is not None)


def canonical_order(fills: list[dict]) -> list[dict]:
    """Canonical fill ordering: (ts, source_ref). Stable + reproducible."""
    return sorted(fills, key=lambda f: (int(f.get("ts", 0)), str(f.get("source_ref", ""))))


def merkle_root(fills: list[dict]) -> str:
    """Merkle root over the fills' ``fill_hash`` values, in canonical order.
    Uses the same SHA-256 bottom-up construction as ``bot.utils.attestation``
    (odd node duplicated), re-implemented here so ``verify.py`` needs no bot deps."""
    leaves = [f["fill_hash"] for f in canonical_order(fills)]
    if not leaves:
        return "0" * 64
    nodes = [bytes.fromhex(h) for h in leaves]
    while len(nodes) > 1:
        nxt = []
        for i in range(0, len(nodes), 2):
            left = nodes[i]
            right = nodes[i + 1] if i + 1 < len(nodes) else left
            nxt.append(hashlib.sha256(left + right).digest())
        nodes = nxt
    return nodes[0].hex()


def epoch_tier(fills: list[dict]) -> str:
    """The headline trust tier = the MINIMUM tier across all fills. This is the
    honesty invariant: a statement can never claim more than its weakest fill."""
    if not fills:
        return "cex_operator_signed"
    tiers = [str(f.get("trust_tier", "cex_operator_signed")) for f in fills]
    return min(tiers, key=lambda t: TRUST_TIERS.get(t, 0))


# ── fills-only metrics ────────────────────────────────────────────────

def compute_metrics(fills: list[dict]) -> dict:
    """Derive metrics from fills ONLY (never a summary): realized PnL via FIFO lot
    matching per market, fees, profit factor, round-trip count, and Sharpe / max
    drawdown from the realized-PnL equity curve. Quote-currency PnL.

    Returns decimal-string numbers. Raises nothing; unusable fills are skipped and
    reflected by ``round_trips`` being lower than the fill count (the caller's
    completeness gate is what refuses to publish)."""
    ordered = canonical_order(fills)
    # market -> [[qty, price, fee_per_unit], ...] open long lots (FIFO)
    lots: dict[str, list[list[Decimal]]] = {}
    realized: list[Decimal] = []                # per-close NET realized PnL (quote)
    total_fees = Decimal(0)
    gross_win = Decimal(0)
    gross_loss = Decimal(0)

    for f in ordered:
        price, qty = _dec(f.get("price")), _dec(f.get("qty"))
        fee = _dec(f.get("fee")) or Decimal(0)
        total_fees += fee
        if price is None or qty is None or qty <= 0:
            continue
        market = str(f.get("market", ""))
        book = lots.setdefault(market, [])
        if f.get("side") == "buy":
            fee_per_unit = (fee / qty) if qty > 0 else Decimal(0)
            book.append([qty, price, fee_per_unit])
            continue
        # sell → match FIFO against open longs. Charge BOTH the matched entry fees
        # (carried on the lot) and this exit fill's fee, pro-rated by matched qty,
        # so Σ(net realized) == Σ(gross) − Σ(all fees) for a flat→flat epoch.
        remaining = qty
        pnl = Decimal(0)
        while remaining > 0 and book:
            lot = book[0]
            take = min(remaining, lot[0])
            entry_fee = take * lot[2]
            exit_fee = fee * (take / qty) if qty > 0 else Decimal(0)
            pnl += take * (price - lot[1]) - entry_fee - exit_fee
            lot[0] -= take
            remaining -= take
            if lot[0] <= 0:
                book.pop(0)
        realized.append(pnl)
        if pnl >= 0:
            gross_win += pnl
        else:
            gross_loss += -pnl

    net_pnl = sum(realized, Decimal(0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (Decimal(0) if gross_win == 0 else Decimal(-1))
    sharpe = _sharpe(realized)
    max_dd = _max_drawdown([sum(realized[:i + 1], Decimal(0)) for i in range(len(realized))])

    return {
        "net_pnl": _numstr(net_pnl),
        "fees": _numstr(total_fees),
        "funding": "0",     # spot: no funding. Perp funding: PENDING (not in v0 data).
        "pf": (_numstr(pf) if pf >= 0 else "inf"),
        "sharpe": _numstr(sharpe),
        "max_dd": _numstr(max_dd),
        "round_trips": len(realized),
    }


def _sharpe(returns: list[Decimal]) -> Decimal:
    """Sharpe of the per-close realized-PnL series (0 risk-free, unannualized).
    Deterministic: mean/stdev via Decimal. Returns 0 when < 2 closes or zero var."""
    n = len(returns)
    if n < 2:
        return Decimal(0)
    mean = sum(returns, Decimal(0)) / n
    var = sum(((r - mean) ** 2 for r in returns), Decimal(0)) / (n - 1)
    if var <= 0:
        return Decimal(0)
    std = var.sqrt()
    return (mean / std) if std > 0 else Decimal(0)


def _max_drawdown(curve: list[Decimal]) -> Decimal:
    """Max peak-to-trough drawdown (absolute quote units) of a cumulative curve."""
    if not curve:
        return Decimal(0)
    peak = curve[0]
    mdd = Decimal(0)
    for v in curve:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > mdd:
            mdd = dd
    return mdd

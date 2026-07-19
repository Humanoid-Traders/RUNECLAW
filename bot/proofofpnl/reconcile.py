"""CEX selective-omission defense — completeness + balance-delta.

The weakness of a CEX (Bitget) track record: an outsider cannot re-fetch the
fills, so a dishonest operator could sign only winning trades and drop the
losers. This module makes that attack fail loudly.

An epoch may reach ``published`` only if BOTH hold:

1. **Completeness** — every fill carries a usable price, qty AND fee. A fill with
   an unknown fee or price cannot contribute a reconcilable PnL, so the epoch is
   ``INCOMPLETE``. (This is exactly why the legacy ``live_trade_proof.json`` — no
   fees, no prices on 2 of 3 round-trips — cannot be published.)
2. **Balance-delta reconciliation** — the net realized PnL derived *from the
   fills* must equal the signed ``close − open`` balance delta within a tight
   tolerance. Omitting a losing fill inflates fills-PnL above the real balance
   change, so the reconciliation fails and the epoch stays ``INCOMPLETE``.

Pure: takes fills + two balance numbers. The balances come from *signed
snapshots* (see ``statement.py``), never from an exchange ``summary`` field.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from bot.proofofpnl.csf import _dec, compute_metrics, fill_is_complete

# Default reconciliation tolerance (quote units). The balance delta should equal
# fills-net-PnL exactly for a flat→flat epoch; a few cents of slack absorbs
# venue-side rounding. Fees are already inside net_pnl, so this is tight.
DEFAULT_TOLERANCE = Decimal("0.01")


def completeness(fills: list[dict]) -> tuple[bool, list[str]]:
    """(ok, reasons). ok only if there is ≥1 fill and every fill is metrics-complete."""
    if not fills:
        return False, ["no fills"]
    reasons = []
    for f in fills:
        if not fill_is_complete(f):
            missing = [k for k in ("price", "qty", "fee") if _dec(f.get(k)) is None]
            reasons.append(f"{f.get('source_ref', '?')}: missing {', '.join(missing)}")
    return (len(reasons) == 0), reasons


def reconcile(fills: list[dict],
              open_balance: Optional[object],
              close_balance: Optional[object],
              tolerance: Decimal = DEFAULT_TOLERANCE) -> dict:
    """Run the full CEX defense. Returns::

        { "status": "published" | "INCOMPLETE",
          "reasons": [str, ...],
          "fills_net_pnl": str, "balance_delta": str|None, "residual": str|None }

    ``open_balance``/``close_balance`` are the signed-snapshot quote balances.
    Missing snapshots → cannot reconcile → INCOMPLETE (this is the honest outcome
    for any record whose only balances live in an untrusted ``summary``)."""
    reasons: list[str] = []

    ok, why = completeness(fills)
    if not ok:
        reasons.extend(why)

    metrics = compute_metrics(fills)
    fills_net = _dec(metrics["net_pnl"]) or Decimal(0)

    ob, cb = _dec(open_balance), _dec(close_balance)
    balance_delta = None
    residual = None
    if ob is None or cb is None:
        reasons.append("missing signed open/close balance snapshot (no summary allowed)")
    else:
        balance_delta = cb - ob
        residual = (balance_delta - fills_net)
        if abs(residual) > tolerance:
            reasons.append(
                f"unreconciled: balance_delta {balance_delta} vs fills_net_pnl "
                f"{fills_net} (residual {residual} > tol {tolerance})")

    status = "published" if not reasons else "INCOMPLETE"
    return {
        "status": status,
        "reasons": reasons,
        "fills_net_pnl": str(fills_net),
        "balance_delta": (str(balance_delta) if balance_delta is not None else None),
        "residual": (str(residual) if residual is not None else None),
    }

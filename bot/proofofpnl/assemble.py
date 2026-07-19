"""Assemble a public, verifiable track-record bundle from raw venue data.

One documented entry point that closes the loop:

    raw CCXT fills + signed balance snapshots
        → CSF statement (reconciled, trust-tiered, Ed25519-signed)
        → outcome-based reputation + ERC-8004 identity card
        → a verification MANIFEST telling a third party exactly how to check it.

The bundle is the payload the public track-record surface (web / MCP) serves. It
is deliberately public-safe: it carries only the CSF statement, the identity card,
and a manifest — never credentials, and never an exchange ``summary`` field (the
whole point is that a verifier re-derives everything from the fills).

An INCOMPLETE bundle is a first-class, honest outcome: if the data is not
fills-grade (missing fees/prices, no signed snapshots), the statement reconciles
to ``INCOMPLETE``, the identity card is ``unbacked``, and the manifest says so.
That is the thesis working — a weaker-but-real result over a stronger-looking fake.
"""

from __future__ import annotations

from typing import Any, Optional

from bot.proofofpnl import erc8004
from bot.proofofpnl.ingest_cex import fills_from_ccxt_trades
from bot.proofofpnl.statement import build_epoch

BUNDLE_FORMAT = "proof_of_pnl_bundle_v0"


def _snapshot(balance: Any, ccy: str, ts: int) -> Optional[dict]:
    """A signed-balance snapshot dict, or None when the balance is absent (→ the
    epoch cannot reconcile → INCOMPLETE, honestly)."""
    if balance is None:
        return None
    return {"balance": str(balance), "ccy": str(ccy or ""), "ts": int(ts)}


def _manifest(statement: dict, card: Optional[dict]) -> dict:
    """The third-party verification manifest — how anyone checks this bundle with
    no RUNECLAW server."""
    status = statement.get("status")
    return {
        "format": BUNDLE_FORMAT,
        "status": status,
        "trust_tier": statement.get("trust_tier"),
        "merkle_root": statement.get("merkle_root"),
        "identity_card_present": card is not None,
        "card_hash": (card or {}).get("card_hash"),
        "how_to_verify": [
            "1. Save the bundle's 'statement' object to statement.json.",
            "2. Run:  python verify.py statement.json",
            "3. Exit 0 = PASS — a valid, published, fills-grade proof; the printed "
            "trust_tier/root/net_pnl match this manifest.",
            "4. On-chain fills: verify.py re-fetches each receipt from a public RPC "
            "and confirms the identical fill_hash (no RUNECLAW server).",
            "5. Identity card: recompute erc8004.card_hash over "
            "{identity, reputation, custody} and check the Ed25519 signature.",
        ],
        "disclaimer": (
            "Reputation is derived from raw fills only, never self-reported. "
            "status=INCOMPLETE means the data is NOT fills-grade evidence and is "
            "not a published proof — the pipeline refuses to dress it up as one."
        ),
    }


def assemble_track_record(ccxt_trades: list[dict], *,
                          account_ids: list[str],
                          open_balance: Any = None,
                          close_balance: Any = None,
                          balance_ccy: str = "USDT",
                          range_start: int = 0,
                          range_end: int = 0,
                          venue: str = "bitget",
                          trust_tier: str = "cex_operator_signed",
                          agent_address: Optional[str] = None,
                          envelope: Optional[dict] = None,
                          engine: Any = None,
                          sign: bool = True) -> dict:
    """Build a public, verifiable track-record bundle from raw CCXT trade dicts.

    ``open_balance``/``close_balance`` are the operator's signed-snapshot quote
    balances (the reconciliation anchors); omit them and the epoch honestly
    reconciles to INCOMPLETE. When ``agent_address`` is given, an ERC-8004 identity
    card is attached — ``backed`` only if the statement published.

    Returns ``{format, statement, identity_card, manifest}`` — public-safe (no
    credentials, no ``summary`` field).
    """
    fills = fills_from_ccxt_trades(ccxt_trades, venue=venue, trust_tier=trust_tier)
    statement = build_epoch(
        fills, account_ids=account_ids,
        open_snapshot=_snapshot(open_balance, balance_ccy, range_start),
        close_snapshot=_snapshot(close_balance, balance_ccy, range_end),
        range_start=range_start, range_end=range_end,
        engine=engine, sign=sign)

    card = None
    if agent_address:
        card = erc8004.build_identity_card(
            agent_address, statement, envelope=envelope, engine=engine, sign=sign)

    return {
        "format": BUNDLE_FORMAT,
        "statement": statement,
        "identity_card": card,
        "manifest": _manifest(statement, card),
    }


def is_public_safe(bundle: Any) -> bool:
    """A bundle must never carry an exchange ``summary`` field anywhere in its
    path (the same rule ``verify.py`` enforces on a statement)."""
    def _walk(obj: Any) -> bool:
        if isinstance(obj, dict):
            if "summary" in obj:
                return False
            return all(_walk(v) for v in obj.values())
        if isinstance(obj, list):
            return all(_walk(v) for v in obj)
        return True
    return _walk(bundle)


def human_readable(bundle: Optional[dict]) -> str:
    """Plain-text one-screen summary of a bundle for operator/web review."""
    if not bundle or not isinstance(bundle, dict):
        return "No track-record bundle."
    st = bundle.get("statement") or {}
    m = st.get("metrics") or {}
    lines = [
        f"Track record · status: {st.get('status')} · tier: {st.get('trust_tier')}",
        f"  net_pnl={m.get('net_pnl')}  pf={m.get('pf')}  sharpe={m.get('sharpe')}  "
        f"max_dd={m.get('max_dd')}  round_trips={m.get('round_trips')}",
        f"  merkle_root={(st.get('merkle_root') or '')[:16]}…",
    ]
    card = bundle.get("identity_card")
    if card:
        lines.append(f"  identity: {card.get('card_id')} ({card.get('status')}) — "
                     f"anchor {((card.get('anchor') or {}).get('status'))}")
    if st.get("status") != "published":
        lines.append("  ⚠ NOT a published proof — data is not fills-grade evidence.")
    return "\n".join(lines)

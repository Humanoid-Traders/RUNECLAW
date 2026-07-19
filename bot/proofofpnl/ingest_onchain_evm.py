"""Re-derive an on-chain (EVM/Base) fill from a public transaction receipt.

The strongest trust tier: a third party fetches the same receipt from a public RPC
and reconstructs the identical fill. We derive the fill by **netting ERC-20
``Transfer`` logs to/from the wallet** — program-agnostic (works for any
router/aggregator), measuring what the wallet actually received and paid, rather
than decoding a specific DEX's ``Swap`` layout.

Constants are VERIFIED against real Base chain data (not a web search):
* ERC-20 ``Transfer(address,address,uint256)`` topic0 =
  ``0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef``
* Uniswap-v3 ``Swap(...)`` topic0 =
  ``0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67``
  (the web-sourced ``0x7a6f9cbb…`` was WRONG — confirmed via eth_getLogs on the
  Base WETH/USDC pool).

Pure: takes a receipt dict (as returned by ``eth_getTransactionReceipt``) plus the
wallet + a token registry. No network here — the RPC fetch lives in ``verify.py``.
"""

from __future__ import annotations

import json
import os
import urllib.request
from decimal import Decimal
from typing import Optional

from bot.proofofpnl.csf import make_fill

DEFAULT_BASE_RPC = "https://mainnet.base.org"

ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
UNIV3_SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

# Minimal Base token registry (address → symbol, decimals). Unknown tokens →
# the fill cannot be normalized to human units → caller marks it UNVERIFIED.
BASE_TOKENS = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6),
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": ("USDbC", 6),
    "0x4200000000000000000000000000000000000006": ("WETH", 18),
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": ("DAI", 18),
    "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22": ("cbETH", 18),
}
# Quote assets (stablecoins) — the leg we price *in*.
_QUOTES = {"USDC", "USDbC", "DAI"}
_NATIVE = ("ETH", 18)   # gas fee currency on Base


def _addr(topic: str) -> str:
    """32-byte topic → 20-byte address (lowercased 0x-hex)."""
    return "0x" + topic[-40:].lower()


def _u256(data: str) -> int:
    return int(data, 16) if data and data != "0x" else 0


def fill_from_evm_receipt(receipt: dict, wallet: str, *, chain: str = "base",
                          venue: str = "base:uniswap-v3",
                          token_meta: Optional[dict] = None,
                          trust_tier: str = "onchain_public") -> Optional[dict]:
    """Reconstruct a single swap fill for ``wallet`` from a receipt. Returns a CSF
    fill, or ``None`` if the tx is not a clean one-in/one-out swap for the wallet
    or a token is unknown (→ caller treats None as UNVERIFIED, never a fake fill).
    """
    tokens = token_meta or BASE_TOKENS
    w = wallet.lower()
    net: dict[str, int] = {}     # token addr → signed raw amount for the wallet
    for log in receipt.get("logs", []):
        topics = log.get("topics") or []
        if not topics or topics[0].lower() != ERC20_TRANSFER_TOPIC or len(topics) < 3:
            continue
        frm, to = _addr(topics[1]), _addr(topics[2])
        val = _u256(log.get("data", "0x"))
        token = str(log.get("address", "")).lower()
        if to == w:
            net[token] = net.get(token, 0) + val
        if frm == w:
            net[token] = net.get(token, 0) - val

    moved = {t: v for t, v in net.items() if v != 0}
    if len(moved) != 2:
        return None   # not a clean 2-leg swap for this wallet

    legs = []
    for token, raw in moved.items():
        meta = tokens.get(token)
        if meta is None:
            return None   # unknown token → cannot normalize → UNVERIFIED
        symbol, decimals = meta
        legs.append((token, symbol, decimals, raw))

    # Identify base vs quote: the stablecoin leg is the quote.
    quote = next((lg for lg in legs if lg[1] in _QUOTES), None)
    if quote is None:
        return None   # no stable leg (token/token swap) → v0 does not price it
    base = next(lg for lg in legs if lg is not quote)

    q_amt = Decimal(quote[3]) / (Decimal(10) ** quote[2])   # signed human
    b_amt = Decimal(base[3]) / (Decimal(10) ** base[2])
    if b_amt == 0:
        return None
    # base received (+) & quote paid (−) → BUY ; base paid (−) & quote received (+) → SELL
    side = "buy" if b_amt > 0 else "sell"
    price = abs(q_amt) / abs(b_amt)
    qty = abs(b_amt)

    # Gas fee (native ETH), from the receipt.
    gas = _u256(receipt.get("gasUsed", "0x")) * _u256(receipt.get("effectiveGasPrice", "0x"))
    fee_eth = Decimal(gas) / (Decimal(10) ** _NATIVE[1])

    market = f"{base[1]}/{quote[1]}"
    txh = receipt.get("transactionHash", "")
    return make_fill(
        venue=venue, venue_type="onchain", market=market, side=side,
        price=price, qty=qty, fee=fee_eth, fee_ccy=_NATIVE[0],
        ts=int(receipt.get("blockNumber", "0x0"), 16),   # block number as the ordinal ts
        source_ref=str(txh), trust_tier=trust_tier,
    )


def fetch_receipt_evm(txhash: str, rpc_url: Optional[str] = None,
                      *, timeout: float = 15.0) -> dict:
    """Fetch a transaction receipt from a public EVM RPC (``eth_getTransactionReceipt``).

    This is the ONLY network call in the package, and it lives on the verifier's
    side: a third party fetches the same receipt from the public chain and
    reconstructs the identical fill. No API key, no RUNECLAW server. Raises on any
    transport/RPC error so the caller can mark the fill UNVERIFIED (never PASS).
    """
    url = rpc_url or os.environ.get("WEB3_RPC_URL_BASE") or DEFAULT_BASE_RPC
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionReceipt", "params": [txhash],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https RPC)
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error"):
        raise RuntimeError(f"RPC error: {body['error']}")
    result = body.get("result")
    if not result:
        raise RuntimeError(f"no receipt for {txhash}")
    return dict(result)

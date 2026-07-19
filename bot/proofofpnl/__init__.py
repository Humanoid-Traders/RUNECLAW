"""Proof-of-PnL — fills-first, tamper-evident track-record statements.

    Don't trust the dashboard — verify the fills.

A track record a third party can check without trusting RUNECLAW's servers.
Positions are reconciled from *raw fills* (never a `summary` field); each epoch is
a deterministic, canonically-hashed statement with a Merkle root, a signature,
and an explicit **trust tier** that states — and can never overstate — how much
the number can be trusted.

Modules:
* ``csf``          — Common Statement Format v0: schema, canonicalization, hashing,
                     trust-tier ordering, and fills-only metrics. Pure.
* ``reconcile``    — CEX selective-omission defense: completeness + balance-delta.
* ``statement``    — build an epoch, Merkle-root + Ed25519-sign it (reuses
                     ``bot.utils.attestation``).
* ``ingest_cex``   — normalize CCXT ``fetch_my_trades`` output into CSF fills.
* ``ingest_onchain_evm`` — re-derive an EVM/Base fill by netting ERC-20 ``Transfer``
                     logs in a public tx receipt (``onchain_public``, the strongest
                     tier — re-checkable by anyone from a public RPC).

The verifier lives at repo root: ``verify.py``.
"""

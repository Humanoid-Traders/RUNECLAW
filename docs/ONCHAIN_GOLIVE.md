# On-chain go-live — the two transactions only the operator can send

Everything is built and tested; two on-chain claims stay honestly
UNVERIFIED / unregistered until **you** sign a transaction from your own
wallet. The bot and server never hold a key, never sign, never broadcast —
these steps are yours by design.

## Prerequisites (once, in `.env`)

```
PROOFOFPNL_AGENT_ADDRESS=0x…   # the agent wallet you control
TOOL_CREATOR_ADDRESS=0x…       # usually the same address
APP_BASE_URL=https://…         # public site origin (manifest URI must resolve)
```

## 1. ERC-8004 identity anchor (Base — root anchor)

1. Telegram: `/anchor` → shows the DRY-RUN plan: a **0-value self-transaction**
   on Base whose calldata commits to `{agent_address, signing pubkey}`.
2. Send it from the agent wallet (MetaMask hex-data or the shown `cast send`).
   Sending FROM that address is what proves key control.
3. `/anchor confirm <tx_hash>` → the bot verifies on-chain (mined, succeeded,
   calldata carries the commitment, correct sender) and only then records it.
4. Check: `/proof` and `/agent/<address>` flip to **VERIFIED** with a
   basescan link. Rotating the signing key later honestly reads STALE.
5. Promotion path (only if the identity becomes canonical): re-run with
   `ANCHOR_CHAIN_ID=1` + a mainnet RPC — per-chain records coexist, Base
   stays the root.

## 2. ERC-8257 Agent Tool Registry (Base)

1. Open `GET /api/tool/registration-plan` — it contains the metadata URI
   (`/.well-known/ai-tool/runeclaw-intel.json`), the keccak256 manifest
   hash, and ready-to-send `registerTool` calldata for the canonical
   ToolRegistry `0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1`.
   `ready:true` means the prerequisites above are set.
2. Send it from your wallet (value 0) — or the shown `cast send` command.
3. The tool is registered **free and open**: zero-address access predicate,
   no pricing block. Per-call charging is x402 machinery and stays behind
   the four gates in `docs/INTEROP.md` §4.
4. Important: the served manifest must stay byte-identical for the on-chain
   hash to keep verifying — after any manifest-affecting change, re-check
   the plan and update the registration.

Both surfaces restate it, and the tests pin it: **non-custodial, always.**

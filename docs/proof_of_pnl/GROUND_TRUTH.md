# GROUND_TRUTH.md — §1 verification

Verdicts against the actual tree. ✅ confirmed · ⚠️ confirmed-with-caveat · ❌ refuted.
Every row cites file:line. Where a claim in the prompt could not be found in
committed code, it is marked ❌ **not-in-repo** and treated as an external claim
this audit cannot reproduce.

## Repo structure

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| A | Python bot under `bot/` ~90k LOC | ✅ | `bot/**/*.py` = **90,112** lines total |
| A | Largest modules as listed | ✅ | `skills/telegram_handler.py` 9705 · `core/live_executor.py` 7936 · `core/engine.py` 5021 · `core/analyzer.py` 4999 · `risk/risk_engine.py` 2993 (these are the 5 largest) |
| B | `guardian/` stack: firewall, risk_sentinel, intent_policy, escape_agent, flight_recorder, digital_twin | ✅ | all six present in `bot/guardian/`; docstrings confirm roles (`firewall.py:1`, `risk_sentinel.py:1`, `intent_policy.py:1`, `escape_agent.py:1`, `flight_recorder.py:1`, `digital_twin.py:1`) |
| C | Truthful-accounting primitives (`equity_basis.js`, `operator_equity_truthful.test.js`, `reports_parity.test.js`) | ⚠️ | all three exist. **Caveat:** `equity_basis.js` reconciles equity snapshots against *closed-trade `pnl`* (`equity_basis.js:36-45`), **not raw fills** — it consumes aggregated trade records, so it is a partial oracle for a fills-first pipeline, not a drop-in. Test oracles are strong: `operator_equity_truthful.test.js:85,96` (equity is real-or-`null`, never fabricated), `reports_parity.test.js:76` (`pf === 2.24` parity). Note `reports_parity.test.js:47` already carries an `inferred_fills`/`excluded_non_fills` notion. |
| D | `skill_registry.py` / `getclaw_wrapper.py` skills pattern | ✅ | `bot/skills/skill_registry.py:225` `class SkillRegistry` (register/get/dispatch); `bot/skills/getclaw_wrapper.py:15` `class GetClawAdapter` — **note:** the wrapper is an SDK *adapter* over `risk.evaluate`, not itself a registry |
| E | `app/lib/` wallet/venues/dex/defi/solana/rwa present | ✅ | all six exist. **Provenance for Phase-1 on-chain path:** `solana.js:20` public RPC `api.mainnet-beta.solana.com`; `wallet.js:32` public EVM RPC; `defi.js` view-calls via wallet RPCs. `dex.js:14` reads the **Hyperliquid info API** (not raw RPC); `rwa.js` derives from tickers (no chain). |

## Claim-vs-data gaps (the important ones)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| F1 | `live_trade_proof.json` is `mode:spot`, 3 BTC/ETH round-trips, **not** perp | ⚠️ **confirmed gap** | `live_trade_proof.json:5` `"mode": "spot"`; `:45` `"total_round_trips": 3`; `:46` `["BTC/USDT","ETH/USDT"]`; base-asset spot sizes (`:17` `6.8e-05` BTC, `:37` `0.0025` ETH); no leverage/funding/contract fields |
| F1b | "perp/futures" framing elsewhere contradicts the spot proof | ⚠️ **mismatch confirmed** | `README.md:712` "Bitget USDT-M Futures"; `README.md:709` "live trading on Bitget futures"; `README.md:743` "live on Bitget futures … 5x leverage". The only committed live proof is **spot**. This is a real claim-vs-data divergence to correct. |
| F2 | `backtest_audit.py` runs on synthetic data; README concedes it | ⚠️ **confirmed** | `backtest_audit.py:48` `DataLoader.generate_synthetic(`; def at `bot/backtest/data_loader.py:176`. Disclaimer `README.md:53` "Backtest results use synthetic data and do not predict future performance." (reinforced `README.md:400,415,763`). Any "backtest performance" is synthetic. |
| F3 | Owner's Kraken 11-yr "no directional edge" (~50% acc, −0.118R, 4372 trades) | ❌ **not-in-repo** | No committed script/report/notebook reproduces it. Searches for `4372`, `0.118`, `OHLCVT`, `Kraken`, `no directional edge` found only coincidental substrings. **This audit cannot reproduce or verify the finding from committed code.** Per rule 1 it is treated as an owner-supplied external result — not contradicted, but not confirmable here. |

## Two prompt claims that do not match the tree (flagged per rule 3)

| # | Claim (from prompt) | Verdict | Evidence |
|---|---|---|---|
| G1 | §4 references "the owner's existing `meme_atr_awareness` eval check" | ❌ **not-in-repo** | No match for `meme_atr_awareness` (or loosened variants) anywhere in the tree. Phase-4's social-filter would be **net-new**, not an extension of an existing eval. |
| G2 | §2/Phase-2: "extend `red_team.py`" implying it red-teams prompt injection | ⚠️ **partial** | `bot/core/red_team.py` exists but stress-tests the **risk envelope only** (`red_team.py:122` `run_stress_test`; scenarios: flash_crash, liquidity_drain, correlated_selloff, circuit_breaker_evasion, malformed_input, …). **No prompt-injection/jailbreak scenarios.** Injection red-teaming would be net-new (though `bot/guardian/firewall.py` already detects injection on the chat surface). |

## Design-positive findings (reduce Phase-1 scope)

| # | Finding | Evidence |
|---|---|---|
| H1 | Raw CEX fill ingestion **already exists** (fees + realised PnL) | `bot/core/live_executor.py:3062` `fetch_my_trades(symbol, limit=10)` (fill-verification path `:3056-3095`); also `:6060`, `:6438`; reconciliation module `bot/core/exchange_sync.py:225`. **Caveat:** `fetch_orders` (plural, in the prompt) is **not** used anywhere in `bot/`; the code uses `fetch_my_trades` + `fetch_order` (singular) fallback + `fetch_positions`. Phase-1 should standardize on `fetch_my_trades` (+ `fetch_closed_orders` — PENDING confirm) as the fills source. |
| H2 | Tamper-evidence primitives **already exist, production-shaped** | SHA-256 hash chain `bot/utils/audit_chain.py:42-55`; **SHA-256 Merkle root** `bot/utils/attestation.py:122-141`; **Ed25519** keygen `attestation.py:64-100` + batch signing over the Merkle root `attestation.py:145-183`; verify `attestation.py:185-228`. Phase-1's "Merkle root + sign" is **reuse, not new build**. |
| H3 | Flight Recorder already seals `DECISION`/`OUTCOME` provenance records onto that chain | `bot/guardian/flight_recorder.py:1-6` — a Proof-of-PnL statement can be built as a specialized OUTCOME/epoch sealing over the same chain + attestation. |

## Net read

The repo already contains ~70% of Phase-1's *plumbing* (raw-fill ingest, SHA-256
Merkle, Ed25519 signing, a truthfulness test culture). The **genuinely new** work
is: (1) a deterministic **Common Statement Format** + `verify.py` re-computer;
(2) the **CEX selective-omission defense** (contiguity + balance-delta
reconciliation); (3) the **on-chain fill re-derivation** path (Solana/Base),
which has *no* existing implementation in the bot. Two of the prompt's own
premises (`meme_atr_awareness` eval, committed Kraken analysis) are **not in the
repo** and are flagged rather than assumed.

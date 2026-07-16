# RUNECLAW LLM — plugging in the in-house fine-tuned model

The bot has a first-class `runeclaw` provider for the in-house fine-tuned
model (Llama 3.1 8B Instruct + LoRA, trained with Unsloth). This guide covers
serving the checkpoint, wiring it into the bot, and the safe rollout path.

## 1. Serve the model (pick one)

Both options expose an OpenAI-compatible API, which is all the bot needs.

### Option A — vLLM (recommended for a GPU server)

vLLM can serve the LoRA adapter directly on top of the base model — no merge
step needed, and you can hot-swap adapter versions:

```bash
pip install vllm
vllm serve meta-llama/Meta-Llama-3.1-8B-Instruct \
  --enable-lora \
  --lora-modules runeclaw-v6=/path/to/lora_adapter \
  --max-lora-rank 64 \
  --port 8000
```

The model name the bot requests is `runeclaw-v6` (the `--lora-modules` alias).
Quantized serving (AWQ/GPTQ of a merged checkpoint) also works if VRAM is
tight.

### Option B — Ollama (simplest, CPU-tolerant)

Merge the LoRA into the base and export GGUF — Unsloth does this in one call
at the end of the training notebook:

```python
model.save_pretrained_gguf("runeclaw-v6", tokenizer,
                           quantization_method="q4_k_m")
```

Then:

```bash
ollama create runeclaw-v6 -f Modelfile   # FROM ./runeclaw-v6.gguf
ollama run runeclaw-v6 "sanity check"
```

## 2. Point the bot at it

Environment (`.env` on the bot host):

```bash
# vLLM (default port assumed if unset)
RUNECLAW_LLM_BASE_URL=http://localhost:8000/v1
# Ollama instead:
# RUNECLAW_LLM_BASE_URL=http://localhost:11434/v1
RUNECLAW_LLM_MODEL=runeclaw-v6
# Only if the endpoint is remote and secured:
# RUNECLAW_LLM_API_KEY=...   (vault-managed — survives .env wipes)
```

No key is required for a local endpoint. If you do set a key via
`/setllm runeclaw <key>`, it is stored encrypted in the vault like every
other provider key.

## 3. Route work to it

**Per-tier (recommended)** — send the high-frequency, low-stakes tiers to the
in-house model and keep the stronger hosted model on the tiers that pick
trades:

```bash
LLM_TIER_SCAN_PROVIDER=runeclaw    # high-frequency scans → free + private
LLM_TIER_CHAT_PROVIDER=runeclaw    # user Q&A → free + private
# THESIS / LEARNING stay on the default routing until the eval says otherwise
```

(Keyless local providers are honored in tier routing — no `_KEY` needed.)

**Everything at once (trial)** — as admin: `/setllm runeclaw`. Revert with
`/llmreset`. Check with `/llmtiers` and `/llmstatus`.

## 4. Rollout gate — prove it before it votes on money

The fine-tune writes in RUNECLAW's voice; whether it *picks better trades*
than the current routing is an empirical question. Do not let it drive
THESIS until it wins on evidence:

1. **Replay A/B** (no market risk): run the recorded-LLM replay harness with
   the thesis tier pointed at `runeclaw` vs the incumbent, on the frozen
   benchmark snapshots. Compare PF / expectancy / Sharpe, not vibes.
2. **Paper shadow**: route `SCAN`+`CHAT` to it live (paper impact only) for a
   few days; watch `/llmstatus` for degradation streaks and latency.
3. **Promote by tier**: only after (1) and (2) look good, move `THESIS`
   — and keep `LEARNING` on a large-context hosted model (8K-context 8B
   models are weak at long reflection).

Guardrails that stay ON regardless of model: the 23-check risk gate, the
70% signal-message threshold, the 85% auto-confirm threshold, and every
circuit breaker. The LLM proposes; the risk engine disposes.

## 5. Retraining loop

The dataset builder that produced the 147K samples can be re-run on new
closed trades; version the adapters (`runeclaw-v7`, ...) and switch with
`RUNECLAW_LLM_MODEL` (vLLM serves multiple adapters side by side, so A/B
between adapter versions is a config flag, not a redeploy).

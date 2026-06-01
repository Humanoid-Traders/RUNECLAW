# Partner Integrations

RUNECLAW is built for extensibility across the Bitget AI Base Camp ecosystem. This page documents integration-ready support for hackathon partner technologies.

---

## Alibaba Qwen (LLM Provider)

RUNECLAW supports Alibaba Qwen models as a drop-in replacement for OpenAI via the OpenAI-compatible DashScope API. No code changes required -- just set two environment variables.

### Setup

```bash
# .env configuration for Qwen
LLM_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=your-dashscope-api-key
LLM_MODEL=qwen-max    # or qwen-plus, qwen-turbo, qwen-flash
```

### Available Models

| Model | Best For | Cost (approx) |
|-------|----------|---------------|
| `qwen-max` | Flagship analysis, complex trade thesis | $2.50/M input |
| `qwen-plus` | Balanced, production workloads | Lower |
| `qwen-turbo` | Fast inference, cost-optimized | Lowest |
| `qwen-flash` | Ultra-fast, simple classification | Minimal |

### Alternative Providers (Qwen Models)

Qwen models are also available via third-party OpenAI-compatible providers:

```bash
# OpenRouter (cheapest: $0.15/M input)
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=qwen/qwen3.6-35b-a3b

# Together AI
LLM_BASE_URL=https://api.together.xyz/v1
LLM_MODEL=Qwen/Qwen3.5-397B

# Fireworks AI
LLM_BASE_URL=https://api.fireworks.ai/inference/v1
LLM_MODEL=accounts/fireworks/models/qwen3-6-plus

# Local (vLLM or Ollama)
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL=qwen3-32b
```

### How It Works

The `Analyzer` class initializes its LLM client with:

```python
from openai import AsyncOpenAI

llm_kwargs = {"api_key": CONFIG.llm.api_key}
if CONFIG.llm.base_url:
    llm_kwargs["base_url"] = CONFIG.llm.base_url
self._llm = AsyncOpenAI(**llm_kwargs)
```

All existing features work identically with Qwen:
- 10-voter confluence scoring
- Trade thesis generation with structured output parsing
- Rule-based fallback when LLM is unavailable
- Token optimization (semantic cache, tiered pipeline, smart batching)
- Cost tracking per analysis call

### Verification

```bash
# Test that Qwen config is wired correctly
python -m pytest tests/test_core.py::TestQwenIntegration -v
```

---

## Solana Foundation (Ecosystem Assets)

RUNECLAW includes a dedicated Solana ecosystem mode that prioritizes Solana-based tokens in market scanning. All 15 tracked tokens trade on Bitget as USDT pairs with full risk engine coverage.

### Setup

```bash
# .env configuration for Solana focus
ASSET_UNIVERSE=solana
```

### Tracked Tokens (15)

| Token | Symbol | Category |
|-------|--------|----------|
| Solana | SOL/USDT | L1 |
| Jupiter | JUP/USDT | DEX Aggregator |
| Jito | JTO/USDT | MEV / Staking |
| Bonk | BONK/USDT | Memecoin |
| dogwifhat | WIF/USDT | Memecoin |
| Pyth Network | PYTH/USDT | Oracle |
| Raydium | RAY/USDT | AMM |
| Orca | ORCA/USDT | AMM |
| Render | RENDER/USDT | GPU / DePIN |
| Helium | HNT/USDT | DePIN |
| Helium Mobile | MOBILE/USDT | DePIN |
| Wormhole | W/USDT | Bridge |
| Jito (Governance) | JITO/USDT | Governance |
| Tensor | TENSOR/USDT | NFT Marketplace |
| Drift | DRIFT/USDT | Perps DEX |

### How It Works

When `ASSET_UNIVERSE=solana`, the `MarketScanner` prioritizes Solana ecosystem tokens:

1. **All USDT pairs** are still scanned from Bitget (full market coverage)
2. **Solana tokens** are sorted to the top of results, regardless of momentum ranking
3. **Remaining slots** are filled with highest-momentum non-Solana assets
4. **Risk engine** applies identically -- all 20 checks, trailing stops, circuit breaker

This ensures Solana ecosystem exposure while maintaining the full risk framework.

### Verification

```bash
# Test Solana ecosystem configuration
python -m pytest tests/test_core.py::TestSolanaEcosystem -v
```

### Post-Hackathon Roadmap: On-Chain Integration

After S1, planned Solana on-chain integrations include:

| Component | Provider | Purpose |
|-----------|----------|---------|
| DEX Routing | Jupiter Swap API | Best-price execution across Solana DEXs |
| Price Feeds | Birdeye API | Real-time token prices, liquidity depth |
| RPC Node | Alchemy / Helius | Account state, transaction submission |
| Market Data | DexScreener | Trending tokens, new listings |
| On-Chain Analysis | Helius DAS | Token metadata, holder distribution |

Architecture: the existing `MarketScanner` would gain a `SolanaDataProvider` alongside the `ccxt` Bitget provider, feeding both CEX and DEX signals into the same analysis pipeline.

---

## Dune (Analytics -- Planned)

Post-hackathon integration with Dune Analytics for on-chain intelligence:

- **Whale tracking queries** -- monitor large wallet movements on Solana
- **DEX volume analytics** -- compare CEX vs DEX volume for signal confirmation
- **Token holder distribution** -- concentration risk assessment
- **Custom dashboards** -- RUNECLAW performance metrics published to Dune

---

## Integration Architecture

```
                    ┌─────────────────┐
                    │   LLM Provider  │
                    │  (Qwen / GPT)   │
                    └────────┬────────┘
                             │
 ┌───────────┐    ┌──────────▼──────────┐    ┌──────────────┐
 │  Bitget   │───▶│     RUNECLAW        │───▶│  Telegram    │
 │  Exchange │    │   Engine + Risk     │    │  Bot         │
 └───────────┘    └──────────┬──────────┘    └──────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼───┐  ┌──────▼─────┐  ┌─────▼──────┐
     │  Solana    │  │   Dune     │  │  Jupiter   │
     │  RPC/Data  │  │  Analytics │  │  DEX API   │
     │ (planned)  │  │ (planned)  │  │ (planned)  │
     └────────────┘  └────────────┘  └────────────┘
```

All integrations follow the same pattern: data providers feed into the existing analysis pipeline, risk engine applies identically, and human confirmation is always required.

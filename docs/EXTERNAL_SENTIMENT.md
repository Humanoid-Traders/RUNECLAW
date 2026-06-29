# External Sentiment — live Fear & Greed (real sentiment voter)

> **Status: BUILT, default OFF** (`EXTERNAL_SENTIMENT_ENABLED=false`). Until
> enabled, the sentiment voter is purely price-derived and makes **no external
> network call** — behaviour is unchanged.

## Why

The sentiment voter (`bot/core/sentiment.py`) already had a real external source
wired in — the **alternative.me Crypto Fear & Greed index** — but
`refresh_fear_greed()` was never called in the live analyze path, so the external
value stayed `None` and the voter was purely *price-derived* (momentum / volume /
volatility). That largely echoes the other technical voters. Activating the
external feed adds genuine **market-wide crowd sentiment**, a contrarian signal
independent of the symbol's own price action.

## How it works

- When `EXTERNAL_SENTIMENT_ENABLED` is on, `analyze()` calls
  `SentimentEngine.refresh_fear_greed()` before scoring. It fetches the index
  (free, no key), **cached with a TTL** so it isn't refetched every analysis, and
  is **fail-open** — a network error keeps the last/None value (zero external
  adjustment).
- The index blends in as a **bounded contrarian** adjustment (`±0.3`): extreme
  fear → contrarian-bullish, extreme greed → contrarian-bearish, neutral → 0
  (`_ext_fg_adjustment()`), then folded into the sentiment voter's confluence
  vote (clamped to `[-1, 1]`).

## Safety

- **Default OFF** — no external call and identical behaviour until enabled.
- **Bounded** (`±0.3` on a `[-1,1]` vote with weight ~0.6) — it can only shade the
  sentiment voter, not dominate confluence.
- **Fail-open + cached** — network failures degrade gracefully to the prior
  price-derived behaviour; the TTL bounds request rate.

## Enable

Set `EXTERNAL_SENTIMENT_ENABLED=true`.

## Future (needs a data source / key)

LLM-based **news / social** sentiment (headline scoring via the existing LLM
BYOK) needs a headline/social feed and is deferred alongside the on-chain
provider work — same BYOK-style, default-OFF, graceful-without-key pattern.

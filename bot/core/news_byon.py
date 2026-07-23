"""
NEWS-2 — Bring-Your-Own-News (BYON): per-user paid news API keys.

The shared NewsRadar (bot/core/news.py) reads PUBLIC, key-free RSS. A user who
has their own paid news-API key can plug it in here to enrich THEIR agent's news
with a richer, personalised feed — without the operator paying for it or every
user seeing it.

§4 compliance is the spine: this NEVER scrapes a paywalled article body. It only
maps the fields the provider's own API returns (headline + source + link +
timestamp) — exactly the public-surface data the RSS radar already uses. If a
provider only exposes full text behind the paywall, we take the HEADLINE, never
the text. Fail-soft: any error → an empty list, never an exception, never the
user's key in a log or error string (F-15).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Supported BYON providers. Each maps a symbol list + key into a public-headline
# request and a response parser. Start with CryptoPanic (a key-based aggregator
# whose API returns headlines + source + link + votes — no article bodies).
_PROVIDERS: dict[str, dict[str, Any]] = {
    "cryptopanic": {
        "label": "CryptoPanic",
        "key_re": re.compile(r"^[A-Za-z0-9]{16,64}$"),
        "url": "https://cryptopanic.com/api/developer/v2/posts/",
    },
}

_MAX_KEY_LEN = 128
_MAX_ITEMS = 20


def providers() -> list[dict[str, str]]:
    """The BYON provider catalogue for the UI — id + human label only."""
    return [{"id": pid, "label": p["label"]} for pid, p in _PROVIDERS.items()]


def validate_provider(provider: str) -> bool:
    return str(provider or "").strip().lower() in _PROVIDERS


def validate_key(provider: str, key: str) -> bool:
    """Charset/length sanity for a provider key — never a network call. Catches
    copy-paste damage (spaces, newlines, truncation) before we store it."""
    p = _PROVIDERS.get(str(provider or "").strip().lower())
    key = str(key or "").strip()
    if not p or not key or len(key) > _MAX_KEY_LEN:
        return False
    return bool(p["key_re"].match(key))


def key_fingerprint(key: str) -> str:
    """A short, non-reversible fingerprint so the UI can show 'a key is set'
    without ever echoing it (F-15). Last 4 chars, masked."""
    k = str(key or "").strip()
    return ("…" + k[-4:]) if len(k) >= 4 else "set"


def _base_asset(symbol: str) -> str:
    s = str(symbol or "").upper()
    for sep in ("/", "-", ":"):
        if sep in s:
            s = s.split(sep)[0]
    return re.sub(r"(USDT|USDC|USD|PERP)$", "", s) or s


async def fetch_byon_news(provider: str, key: str, symbols: Iterable[str],
                          now: float, session: Optional[Any] = None,
                          limit: int = _MAX_ITEMS) -> list[dict]:
    """Fetch enriched headlines from the user's own news provider, shaped like
    NewsRadar items: ``[{title, url, source, published_ts, symbols}]``.

    §4: maps ONLY the API's public fields (headline/source/link) — never an
    article body. Fail-soft: returns ``[]`` on a bad provider/key, a network
    error, or a malformed response. NEVER raises; NEVER surfaces the key."""
    pid = str(provider or "").strip().lower()
    p = _PROVIDERS.get(pid)
    if not p or not validate_key(pid, key):
        return []
    bases = [_base_asset(s) for s in (symbols or []) if s]
    try:
        import aiohttp
        params = {"auth_token": str(key).strip(), "public": "true"}
        if bases:
            params["currencies"] = ",".join(sorted(set(bases))[:20])
        owns = session is None
        sess = session or aiohttp.ClientSession()
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with sess.get(p["url"], params=params, timeout=timeout) as r:
                if r.status != 200:
                    return []
                data = await r.json()
        finally:
            if owns:
                await sess.close()
    except Exception:
        return []  # network/parse failure — fall back to the public radar
    return _parse_cryptopanic(data, now, limit)


def _parse_cryptopanic(data: Any, now: float, limit: int) -> list[dict]:
    """Parse CryptoPanic's response into NewsRadar-shaped items. Tolerant of a
    missing field; drops anything without a title. Never raises."""
    out: list[dict] = []
    try:
        results = (data or {}).get("results") if isinstance(data, dict) else None
        for it in (results or [])[:limit]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            src = it.get("source") or {}
            source = (src.get("title") if isinstance(src, dict) else None) or "CryptoPanic"
            syms = []
            for c in (it.get("currencies") or []):
                code = c.get("code") if isinstance(c, dict) else None
                if code:
                    syms.append(str(code).upper())
            out.append({
                "title": title[:400],
                "url": str(it.get("url") or "").strip()[:600],
                "source": str(source)[:80],
                "published_at": str(it.get("published_at") or "").strip(),
                "symbols": syms,
                "byon": True,             # provenance: this came from the user's key
            })
    except Exception:
        return out
    return out

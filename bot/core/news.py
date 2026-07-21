"""Realtime news radar (NEWS-1).

Pulls headlines from PUBLIC crypto RSS feeds — no API key, no paywall, no
credentials (§4-compliant; the paid/BYO-key path is NEWS-2). Each headline is
scored for:

  • relevance   — which held/watched symbols it mentions, and
  • impact      — a HEURISTIC flag (LOW / MEDIUM / HIGH) from keyword classes,

and a stand-down RECOMMENDATION is surfaced for held positions when a fresh,
high-impact headline names them.

Everything here is ADVISORY. Per §4 the impact/stand-down reads are heuristic
flags, never verdicts, and NOTHING in this module blocks, sizes, or moves an
order — it only surfaces "you may want to look at X". The stand-down is a
recommendation the operator acts on, deliberately NOT an auto-halt (the live
money-path stays fail-open; see the leverage/volatility posture).

Pure helpers (classify_impact, match_symbols, standdown_for_holdings) take
primitives and are fully unit-tested with no network. The async fetch is gated
behind NEWS_RADAR_ENABLED (default OFF) and is best-effort.
"""

from __future__ import annotations

import html
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional
from xml.etree import ElementTree as ET

from bot.utils.logger import system_log


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


# Public, key-free crypto RSS feeds (headlines + links only — never the body,
# so no paywall is ever touched). Override with NEWS_FEEDS (comma-separated).
_DEFAULT_FEEDS = (
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
)


class Impact(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Keyword classes → impact. HIGH = the kind of headline you want to see the
# instant it breaks on a position you hold. Word-boundary matched, lower-cased.
_HIGH_KEYWORDS = (
    "hack", "hacked", "exploit", "exploited", "drained", "breach", "stolen",
    "rug", "rugpull", "insolvent", "insolvency", "bankrupt", "bankruptcy",
    "halt", "halted", "delist", "delisted", "depeg", "depegged", "sec sues",
    "lawsuit", "subpoena", "indicted", "seized", "frozen", "collapse",
)
_MEDIUM_KEYWORDS = (
    "sec", "lawsuit", "regulation", "regulator", "investigation", "probe",
    "listing", "partnership", "upgrade", "mainnet", "hard fork", "unlock",
    "outage", "downtime", "vulnerability", "whale", "etf",
)


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    source: str
    published_ts: float           # epoch seconds (0 if unknown)
    impact: Impact = Impact.LOW
    impact_reasons: tuple = ()     # matched keywords
    symbols: tuple = ()            # matched symbols (base assets, upper-case)

    def age_sec(self, now: float) -> float:
        return max(0.0, now - self.published_ts) if self.published_ts else 0.0


def classify_impact(title: str) -> tuple[Impact, tuple]:
    """Heuristic impact flag from keyword classes. Returns (impact, reasons).
    HIGH wins over MEDIUM; reasons lists the matched keywords for transparency
    (the read is a flag, never a verdict — always show WHY)."""
    t = (title or "").lower()

    def _hits(words: Iterable[str]) -> list[str]:
        out = []
        for w in words:
            # word-boundary match so "sec" doesn't fire inside "second".
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t):
                out.append(w)
        return out

    high = _hits(_HIGH_KEYWORDS)
    if high:
        return Impact.HIGH, tuple(high)
    med = _hits(_MEDIUM_KEYWORDS)
    if med:
        return Impact.MEDIUM, tuple(med)
    return Impact.LOW, ()


def _base_asset(symbol: str) -> str:
    """BTC/USDT:USDT → BTC ; ETHUSDT → ETH-ish (best-effort base extraction)."""
    s = (symbol or "").upper().split(":")[0]
    if "/" in s:
        return s.split("/")[0]
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


# Common name → ticker aliases so "Ethereum ETF" matches an ETH holding.
_NAME_ALIASES = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP",
    "cardano": "ADA", "dogecoin": "DOGE", "polygon": "MATIC", "avalanche": "AVAX",
    "chainlink": "LINK", "polkadot": "DOT", "litecoin": "LTC", "binance": "BNB",
    "arbitrum": "ARB", "optimism": "OP", "aptos": "APT", "sui": "SUI",
}


def match_symbols(title: str, symbols: Iterable[str]) -> tuple:
    """Return the subset of `symbols` (as base assets) named in the headline,
    by ticker OR common name. Word-boundary matched, case-insensitive."""
    t = (title or "").lower()
    out: list[str] = []
    seen: set = set()
    for sym in symbols:
        base = _base_asset(sym)
        if not base or base in seen:
            continue
        hit = re.search(r"(?<![a-z0-9])" + re.escape(base.lower()) + r"(?![a-z0-9])", t)
        if not hit:
            for name, tk in _NAME_ALIASES.items():
                if tk == base and re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", t):
                    hit = True
                    break
        if hit:
            seen.add(base)
            out.append(base)
    return tuple(out)


def standdown_for_holdings(
    items: Iterable[NewsItem], held_symbols: Iterable[str], now: float,
    max_age_sec: int = 3600,
) -> list[dict]:
    """Advisory stand-down recommendations: for each held base asset with a
    FRESH high-impact headline naming it, emit a review nudge. Recommendation
    only — never an auto-action.

    Returns a list of {symbol, headline, url, reasons, age_sec}, newest first.
    """
    held = {_base_asset(s) for s in held_symbols if _base_asset(s)}
    recs: list[dict] = []
    for it in items:
        if it.impact != Impact.HIGH:
            continue
        if it.age_sec(now) > max_age_sec:
            continue
        for base in it.symbols:
            if base in held:
                recs.append({
                    "symbol": base,
                    "headline": it.title,
                    "url": it.url,
                    "source": it.source,
                    "reasons": list(it.impact_reasons),
                    "age_sec": int(it.age_sec(now)),
                    "recommendation":
                        f"High-impact news on a position you hold ({base}) — "
                        f"review it; consider tightening the stop or reducing. "
                        f"Advisory only, no action taken.",
                })
                break
    recs.sort(key=lambda r: r["age_sec"])
    return recs


_IMPACT_ICON = {Impact.HIGH: "🔴", Impact.MEDIUM: "🟠", Impact.LOW: "⚪"}


def _fmt_age(sec: float) -> str:
    sec = int(sec)
    if sec < 90:
        return f"{max(sec, 1)}s ago"
    if sec < 5400:
        return f"{sec // 60}m ago"
    if sec < 172800:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_news_digest(recent, standdown_recs, now, limit=6) -> str:
    """Pure Telegram-HTML digest: stand-down nudges for held positions first
    (advisory), then the freshest headlines with their impact flag. No I/O."""
    lines = ["📰 <b>News radar</b>"]

    if standdown_recs:
        lines.append("\n⚠️ <b>On your positions:</b>")
        for r in standdown_recs[:5]:
            why = ", ".join(r.get("reasons", [])[:3])
            lines.append(
                f"🔴 <b>{_esc(r['symbol'])}</b> — {_esc(r['headline'])[:120]}"
                + (f"\n    <i>{_esc(why)}</i>" if why else "")
                + f" · {_fmt_age(r.get('age_sec', 0))}")
        lines.append("<i>Advisory only — review and decide; nothing was traded.</i>")

    items = list(recent)[:limit]
    if items:
        lines.append("\n<b>Latest headlines:</b>")
        for it in items:
            icon = _IMPACT_ICON.get(it.impact, "⚪")
            syms = (" · " + "/".join(it.symbols)) if it.symbols else ""
            lines.append(
                f"{icon} {_esc(it.title)[:130]}"
                f"\n    <i>{_esc(it.source)}{syms} · {_fmt_age(it.age_sec(now))}</i>")
    elif not standdown_recs:
        lines.append("\nNo headlines yet — the radar fills on the next refresh.")

    return "\n".join(lines)


def parse_rss(xml_text: str, source: str, symbols: Iterable[str], now: float) -> list[NewsItem]:
    """Parse an RSS/Atom document into scored NewsItems. Tolerant of the two
    common shapes (RSS <item> and Atom <entry>); returns [] on any parse error
    so a malformed feed never breaks the radar."""
    syms = list(symbols)
    out: list[NewsItem] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:
        system_log.debug("news: RSS parse failed for %s: %s", source, exc)
        return out

    def _text(node, *tags) -> str:
        for tag in tags:
            el = node.find(tag)
            if el is not None and (el.text or "").strip():
                return html.unescape(el.text.strip())
            # Atom links carry the url in an attribute.
            if el is not None and el.get("href"):
                return el.get("href").strip()
        return ""

    # RSS items live at channel/item; Atom entries at the root.
    nodes = root.iter("item")
    nodes = list(nodes) or [n for n in root.iter() if n.tag.endswith("entry")]
    for node in nodes:
        title = _text(node, "title", "{http://www.w3.org/2005/Atom}title")
        if not title:
            continue
        link = _text(node, "link", "guid", "{http://www.w3.org/2005/Atom}link")
        ts = _parse_pubdate(_text(
            node, "pubDate", "published", "updated",
            "{http://www.w3.org/2005/Atom}published",
            "{http://www.w3.org/2005/Atom}updated"))
        impact, reasons = classify_impact(title)
        matched = match_symbols(title, syms)
        out.append(NewsItem(
            title=title, url=link, source=source, published_ts=ts or now,
            impact=impact, impact_reasons=reasons, symbols=matched))
    return out


_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _parse_pubdate(raw: str) -> float:
    """Best-effort RFC-822 / ISO-8601 → epoch seconds. 0 on failure (the caller
    substitutes 'now' so the item is still radar-visible)."""
    raw = (raw or "").strip()
    if not raw:
        return 0.0
    # RFC-822: "Mon, 21 Jul 2026 15:04:05 GMT"
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})", raw)
    if m:
        day, mon, yr, hh, mm, ss = m.groups()
        mon_i = _MONTHS.get(mon.title())
        if mon_i:
            try:
                import calendar
                return float(calendar.timegm(
                    (int(yr), mon_i, int(day), int(hh), int(mm), int(ss), 0, 0, 0)))
            except Exception:
                return 0.0
    # ISO-8601: "2026-07-21T15:04:05Z"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", raw)
    if m:
        try:
            import calendar
            return float(calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0)))
        except Exception:
            return 0.0
    return 0.0


@dataclass
class NewsRadar:
    """Holds recent, de-duplicated news items and answers per-symbol / holdings
    queries. Fetching is gated + best-effort; the store is pure in-memory."""
    max_items: int = 200
    _items: deque = field(default_factory=lambda: deque(maxlen=200))
    _seen: set = field(default_factory=set)
    _last_fetch: float = 0.0

    def ingest(self, items: Iterable[NewsItem]) -> int:
        """Add new items (de-duped by url|title). Returns the count added."""
        added = 0
        for it in items:
            key = (it.url or "") + "|" + it.title
            if key in self._seen:
                continue
            self._seen.add(key)
            self._items.appendleft(it)
            added += 1
        # Keep _seen from growing unbounded alongside the capped deque.
        if len(self._seen) > self.max_items * 4:
            self._seen = {(it.url or "") + "|" + it.title for it in self._items}
        return added

    def recent(self, limit: int = 20) -> list[NewsItem]:
        return list(self._items)[:limit]

    def for_symbol(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        base = _base_asset(symbol)
        return [it for it in self._items if base in it.symbols][:limit]

    def high_impact(self, limit: int = 10) -> list[NewsItem]:
        return [it for it in self._items if it.impact == Impact.HIGH][:limit]

    def standdown(self, held_symbols: Iterable[str], now: float) -> list[dict]:
        return standdown_for_holdings(self._items, held_symbols, now)

    @staticmethod
    def enabled() -> bool:
        return _env_bool("NEWS_RADAR_ENABLED", False)

    @staticmethod
    def feeds() -> list[str]:
        raw = os.getenv("NEWS_FEEDS", "")
        if raw.strip():
            return [u.strip() for u in raw.split(",") if u.strip()]
        return list(_DEFAULT_FEEDS)

    async def refresh(self, symbols: Iterable[str], session=None, now: Optional[float] = None) -> int:
        """Fetch + ingest the configured public RSS feeds. Gated + best-effort:
        returns 0 (no-op) when disabled or on any error, and never raises."""
        if not self.enabled():
            return 0
        now = time.time() if now is None else now
        min_gap = _env_int("NEWS_REFRESH_MIN_SEC", 120)
        if self._last_fetch and (now - self._last_fetch) < min_gap:
            return 0
        self._last_fetch = now
        try:
            import aiohttp
        except Exception:
            return 0
        own = session is None
        if own:
            session = aiohttp.ClientSession()
        added = 0
        try:
            for url in self.feeds():
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status != 200:
                            continue
                        text = await r.text()
                    src = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
                    added += self.ingest(parse_rss(text, src, symbols, now))
                except Exception as exc:
                    system_log.debug("news: feed fetch failed %s: %s", url, exc)
        finally:
            if own:
                try:
                    await session.close()
                except Exception:
                    pass
        return added

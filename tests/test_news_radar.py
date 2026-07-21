"""NEWS-1: realtime news radar core (headline impact + relevance + stand-down).

All pure logic — no network. The async fetch is gated (NEWS_RADAR_ENABLED,
default OFF) and best-effort; only the parsing/scoring/recommendation logic is
exercised here. Everything is ADVISORY: the stand-down is a recommendation,
never an auto-action (§4 — heuristic flags, never verdicts; and the live
money-path stays fail-open).
"""

from __future__ import annotations

from bot.core.news import (
    Impact, NewsItem, NewsRadar,
    classify_impact, match_symbols, parse_rss, standdown_for_holdings,
)


# ── impact classification (heuristic flag, with reasons) ─────────────────────

def test_high_impact_keywords_flag_high_with_reasons():
    impact, reasons = classify_impact("Major exchange hacked, $200M drained overnight")
    assert impact is Impact.HIGH
    assert "hacked" in reasons and "drained" in reasons


def test_medium_impact_keywords_flag_medium():
    impact, reasons = classify_impact("Bitcoin ETF sees record inflows")
    assert impact is Impact.MEDIUM
    assert "etf" in reasons


def test_benign_headline_is_low_impact():
    impact, reasons = classify_impact("Weekly market wrap: quiet tape into the weekend")
    assert impact is Impact.LOW
    assert reasons == ()


def test_impact_is_word_boundary_matched():
    # "sec" must not fire inside "second" / "security".
    impact, _ = classify_impact("A second look at the market this week")
    assert impact is Impact.LOW


# ── symbol relevance (ticker + common name) ──────────────────────────────────

def test_matches_by_ticker_and_normalizes_perp_symbols():
    assert match_symbols("Solana network halts amid outage",
                         ["SOL/USDT:USDT", "BTC/USDT:USDT"]) == ("SOL",)


def test_matches_by_common_name_alias():
    assert match_symbols("Ethereum upgrade goes live tonight", ["ETH/USDT"]) == ("ETH",)


def test_no_false_symbol_match():
    assert match_symbols("Gold rallies on macro fears", ["SOL/USDT", "BTC/USDT"]) == ()


# ── stand-down recommendation (advisory only) ────────────────────────────────

def _item(title, syms, impact, age, now):
    imp, reasons = classify_impact(title)
    return NewsItem(title=title, url="u", source="s", published_ts=now - age,
                    impact=impact, impact_reasons=reasons, symbols=tuple(syms))


def test_standdown_fires_for_fresh_high_impact_on_a_held_symbol():
    now = 1_000_000.0
    items = [_item("SOL exploit drains funds", ["SOL"], Impact.HIGH, 60, now)]
    recs = standdown_for_holdings(items, ["SOL/USDT:USDT"], now)
    assert len(recs) == 1
    assert recs[0]["symbol"] == "SOL"
    assert "Advisory only" in recs[0]["recommendation"]


def test_standdown_ignores_symbols_not_held():
    now = 1_000_000.0
    items = [_item("SOL exploit", ["SOL"], Impact.HIGH, 60, now)]
    assert standdown_for_holdings(items, ["BTC/USDT"], now) == []


def test_standdown_ignores_stale_and_low_impact_news():
    now = 1_000_000.0
    stale = _item("SOL exploit", ["SOL"], Impact.HIGH, 99_999, now)   # too old
    low = _item("SOL weekly recap", ["SOL"], Impact.LOW, 60, now)     # not high
    assert standdown_for_holdings([stale, low], ["SOL/USDT"], now) == []


# ── RSS parsing (tolerant, scored) ───────────────────────────────────────────

_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Solana halted after exploit</title><link>http://x/1</link>
<pubDate>Mon, 21 Jul 2026 15:04:05 GMT</pubDate></item>
<item><title>Weekly recap</title><link>http://x/2</link></item>
</channel></rss>"""


def test_parse_rss_scores_and_matches_each_item():
    items = parse_rss(_RSS, "coindesk.com", ["SOL/USDT:USDT"], now=1_753_110_000.0)
    assert len(items) == 2
    top = items[0]
    assert top.impact is Impact.HIGH and top.symbols == ("SOL",)
    assert top.published_ts > 0            # pubDate parsed
    assert items[1].impact is Impact.LOW


def test_parse_rss_never_raises_on_garbage():
    assert parse_rss("<not valid xml", "s", [], now=0.0) == []


# ── radar store: dedup, per-symbol, gating ───────────────────────────────────

def test_radar_ingests_dedups_and_queries_by_symbol():
    radar = NewsRadar()
    items = parse_rss(_RSS, "coindesk.com", ["SOL/USDT"], now=1_753_110_000.0)
    assert radar.ingest(items) == 2
    assert radar.ingest(items) == 0                       # same items → deduped
    assert [i.title for i in radar.for_symbol("SOL/USDT")] == ["Solana halted after exploit"]
    assert len(radar.high_impact()) == 1


def test_radar_fetch_is_disabled_by_default():
    assert NewsRadar.enabled() is False


def test_radar_feeds_are_public_and_keyless():
    for url in NewsRadar.feeds():
        assert url.startswith("https://")
        # No API-key query params baked into the default feeds.
        assert "apikey" not in url.lower() and "token=" not in url.lower()

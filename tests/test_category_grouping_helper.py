"""Shared category-grouping primitive used by every scan/signal renderer.

`group_by_category` / `category_icon` / `category_for_symbol` live in
bot.core.market_scanner and are the single source of truth for how scan results
and signals are clustered by asset class across /scan, /deepscan, /fullscan,
/scalp, /latest_signal and /signals.
"""
from types import SimpleNamespace

from bot.core.market_scanner import (
    CATEGORY_META,
    category_for_symbol,
    category_icon,
    category_sort_key,
    group_by_category,
)


def test_category_icon_and_sort_key_match_meta():
    for cat, (icon, prio) in CATEGORY_META.items():
        assert category_icon(cat) == icon
        assert category_sort_key(cat) == prio
    # Unknown category falls back (crypto coin icon, lowest priority).
    assert category_sort_key("Nonsense") == 99
    assert category_icon("Nonsense") == "\U0001f4b0"


def test_category_for_symbol_classifies_tradfi_and_crypto():
    assert category_for_symbol("BTC/USDT") == "Crypto"
    assert category_for_symbol("XAU/USDT:USDT") == "Metal"
    assert category_for_symbol("TSLA/USDT:USDT") == "Stock"
    assert category_for_symbol("CL/USDT:USDT") == "Commodity"


def test_group_orders_categories_by_display_priority():
    items = [
        SimpleNamespace(sym="TSLA", cat="Stock"),
        SimpleNamespace(sym="BTC", cat="Crypto"),
        SimpleNamespace(sym="XAU", cat="Metal"),
        SimpleNamespace(sym="ETH", cat="Crypto"),
    ]
    grouped = group_by_category(items, lambda x: x.cat)
    # Crypto (0) before Metal (1) before Stock (5), regardless of input order.
    assert list(grouped.keys()) == ["Crypto", "Metal", "Stock"]
    # Within a category, input order is preserved (insertion-stable).
    assert [i.sym for i in grouped["Crypto"]] == ["BTC", "ETH"]


def test_group_works_on_tradeidea_like_objects_via_symbol():
    ideas = [
        SimpleNamespace(asset="TSLA/USDT:USDT"),
        SimpleNamespace(asset="BTC/USDT"),
        SimpleNamespace(asset="XAU/USDT:USDT"),
    ]
    grouped = group_by_category(ideas, lambda i: category_for_symbol(i.asset))
    assert list(grouped.keys()) == ["Crypto", "Metal", "Stock"]


def test_empty_input_gives_empty_grouping():
    assert group_by_category([], lambda x: x) == {}

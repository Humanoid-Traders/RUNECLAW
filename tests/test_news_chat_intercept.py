"""INCIDENT fix: free-text "news" must route to the RSS radar, not the
tool-less chat LLM (which denied having a feed on both surfaces).

Covers the shared detector `looks_like_news_request` that the web gateway and
the Telegram handler both use, so the two surfaces behave identically.
"""

from bot.core.news import looks_like_news_request


def test_news_led_phrases_match():
    for t in ("news", "News", "  news  ", "any news", "latest news",
              "crypto news", "market news", "headlines", "latest headlines",
              "show me news", "get news", "news on BTC?", "any news today?"):
        assert looks_like_news_request(t), t


def test_non_news_or_substantive_asks_do_not_match():
    for t in ("", "   ",
              "how do I read the order book",
              "what's my pnl",
              # contains 'news' but is a long, non-news-led analytical ask:
              "what is driving the news-cycle selloff on ETH and exactly "
              "where are the key levels right now please",
              # 'newsletter' must NOT trip the \\bnews\\b boundary:
              "sign me up for the newsletter please when you can"):
        assert not looks_like_news_request(t), t


def test_short_news_dominated_message_matches():
    # A short message whose dominant word is news/headlines counts, even if not
    # strictly news-led.
    assert looks_like_news_request("got any news?")
    assert looks_like_news_request("BTC news?")

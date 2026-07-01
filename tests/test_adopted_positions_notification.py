"""
Adopted-position notifications must batch into a real list, not iterate a
single message string character-by-character.

Real incident: a Telegram notification showed "Found 41 position(s) on the
exchange that were not tracked locally" followed by 41 single-character
bullets spelling out "Adopted orphan limit order: ENA/USDT:USDT" (41 is the
exact character count of that one string). Root cause: the periodic sync
loop called the adopt-notify callback ONCE PER "Adopted" message, passing a
single string each time. The callback's signature/rendering
(`adopted_symbols: list[str]`, `len(adopted_symbols)`, `for sym in
adopted_symbols`) expects the FULL list of adopted-position descriptions in
one call, so len()/iteration over a bare string treated it as a sequence of
characters.
"""

from bot.core.engine import filter_adopted_messages


class TestFilterAdoptedMessages:
    def test_extracts_only_adopted_messages(self):
        msgs = [
            "Adopted orphan limit order: ENA/USDT:USDT",
            "Ghost position cleared: XYZ/USDT",
            "Orphan detected: ABC/USDT",
            "Adopted orphan position: BTC/USDT:USDT",
        ]
        result = filter_adopted_messages(msgs)
        assert result == [
            "Adopted orphan limit order: ENA/USDT:USDT",
            "Adopted orphan position: BTC/USDT:USDT",
        ]

    def test_returns_a_real_list_not_a_joined_string(self):
        # The exact regression: len() and iteration must reflect element
        # count, not character count of some concatenated string.
        msgs = ["Adopted orphan limit order: ENA/USDT:USDT"]
        result = filter_adopted_messages(msgs)
        assert isinstance(result, list)
        assert len(result) == 1  # NOT 41 (the string's character count)
        assert list(result) == ["Adopted orphan limit order: ENA/USDT:USDT"]

    def test_empty_input_returns_empty_list(self):
        assert filter_adopted_messages([]) == []

    def test_no_adopted_messages_returns_empty_list(self):
        msgs = ["Ghost position cleared: XYZ/USDT", "Orphan detected: ABC/USDT"]
        assert filter_adopted_messages(msgs) == []

    def test_multiple_adopted_messages_all_kept_in_order(self):
        msgs = [f"Adopted orphan limit order: SYM{i}/USDT:USDT" for i in range(5)]
        result = filter_adopted_messages(msgs)
        assert len(result) == 5
        assert result == msgs

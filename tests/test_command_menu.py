"""The "/" command menus must be valid Telegram commands AND real handlers.

Guards two ways the menu could rot: a bad command string Telegram rejects
(uppercase, too long, illegal chars), and a menu entry pointing at a command
the handler no longer registers (dead entry → tapping it does nothing).
"""

import os
import re

from bot.skills.command_menu import (
    ADMIN_EXTRA_MENU,
    DEFAULT_MENU,
    admin_commands,
    default_commands,
)

_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _registered_commands() -> set:
    """Command names the handler actually registers, parsed from source so the
    test needs no bot token / running app."""
    src = os.path.join(os.path.dirname(__file__), "..", "bot", "skills",
                       "telegram_handler.py")
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    # Registration tuples look like ("start", self._cmd_start) or ("me", _cmd_me)
    return set(re.findall(r'\(\s*"([a-z0-9_]+)"\s*,\s*(?:self\.)?_cmd', text))


class TestMenuFormat:
    def _all(self):
        return DEFAULT_MENU + ADMIN_EXTRA_MENU

    def test_command_names_are_valid_telegram_commands(self):
        for name, _ in self._all():
            assert _NAME_RE.match(name), f"illegal command name: {name!r}"

    def test_descriptions_within_telegram_limit(self):
        for name, desc in self._all():
            assert 1 <= len(desc) <= 256, f"{name}: description length {len(desc)}"

    def test_no_duplicate_commands_within_a_menu(self):
        for menu in (default_commands(), admin_commands()):
            names = [n for n, _ in menu]
            assert len(names) == len(set(names))

    def test_menus_are_non_empty_and_bounded(self):
        # Telegram caps a scope at 100 commands; keep the default short.
        assert 0 < len(default_commands()) <= 15
        assert 0 < len(admin_commands()) <= 100


class TestMenuMatchesHandlers:
    def test_every_menu_command_is_registered(self):
        registered = _registered_commands()
        assert registered, "failed to parse registered commands"
        for name, _ in DEFAULT_MENU + ADMIN_EXTRA_MENU:
            assert name in registered, f"menu command /{name} is not registered"

    def test_admin_menu_starts_with_the_essentials(self):
        # Operators get the essentials first, then their extra controls.
        assert admin_commands()[:len(default_commands())] == default_commands()

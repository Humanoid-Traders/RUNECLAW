"""
Registration policy consistency (deep-audit web findings).

The signup password rule was inconsistent: the web client accepted >=8,
the Node server required >=10, and the Python create_user required >=8 —
so an 8-9 char password passed client validation then failed server-side
with a confusing error. All three must now agree on 10. Plus: /register
must be rate-limited (was the only unthrottled auth route), and the
product-app footer must carry the real social links (they existed only on
the throwaway marketing site).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── password policy: one number, everywhere ───────────────────────────

class TestPasswordPolicy:
    def test_python_create_user_requires_10(self):
        from bot.db.models import create_user
        with __import__("pytest").raises(ValueError):
            create_user("x@y.com", "short9chr")  # 9 chars → rejected

    def test_all_three_surfaces_agree_on_10(self):
        # Python model
        assert "len(password) < 10" in _read("bot/db/models.py")
        # Node server
        assert "password.length < 10" in _read("app/auth.js")
        # web client validation + placeholder
        html = _read("app/public/index.html")
        assert "pass.length<10" in html
        assert "Min 10 characters" in html
        # no stale "8 characters" copy left in the register UI
        assert "at least 8 characters" not in html


# ── register rate-limiting ─────────────────────────────────────────────

class TestRegisterRateLimit:
    def test_register_is_rate_limited(self):
        src = _read("app/auth.js")
        # find the register handler block and assert it throttles
        m = re.search(r"router\.post\('/register'.*?router\.post\('/login'",
                      src, re.DOTALL)
        assert m, "register handler not found"
        block = m.group(0)
        assert "checkRateLimit(clientIp)" in block
        assert "429" in block


# ── social links on the product app ───────────────────────────────────

class TestSocialLinks:
    def test_product_footer_has_real_social_links(self):
        html = _read("app/public/index.html")
        assert "github.com/Humanoid-Traders/RUNECLAW" in html   # GitHub
        assert "t.me/+VRNgsmkR5pszZTdk" in html                 # community
        assert "t.me/HTRUNECLAW_bot" in html                    # bot (kept)

    def test_no_placeholder_or_missing_register_page(self):
        # the #367-369 redesign already fixed the broken /register route —
        # it now redirects rather than serving a nonexistent file.
        bridge = _read("api_bridge.py")
        assert "legacy_page_redirect" in bridge
        assert "RedirectResponse" in bridge

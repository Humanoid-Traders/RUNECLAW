"""
Python 3.10 compatibility shim.

datetime.UTC was added in Python 3.11. This module re-exports it
as timezone.utc for older interpreters, so every file can do:

    from bot.compat import UTC

instead of:

    from datetime import UTC   # Python 3.11+ only
"""

from datetime import timezone

try:
    from datetime import UTC  # Python 3.11+
except ImportError:
    UTC = timezone.utc  # Python 3.10 fallback

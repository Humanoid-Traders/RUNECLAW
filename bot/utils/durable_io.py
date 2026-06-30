"""Durability helper: fsync a file's parent directory after an atomic rename.

``os.replace(tmp, target)`` is atomic, but on a crash / power loss the rename
itself can still be lost unless the PARENT DIRECTORY entry is flushed to disk —
fsync'ing the tmp file's *contents* (which the save paths already do before the
replace) does not persist the directory entry that points at it. This fsyncs the
directory *after* the replace so the rename survives.

Best-effort by design: directory fsync is not supported on every platform /
filesystem (notably Windows, where ``os.open`` on a directory fails), so any
error is swallowed — durability is improved where supported and never breaks the
save where it is not.
"""

from __future__ import annotations

import os


def fsync_dir(path: str) -> None:
    """Best-effort fsync of the parent directory of ``path``.

    Call immediately after ``os.replace(tmp, path)`` so the rename is durable.
    Silent no-op when directory fsync is unsupported or errors.
    """
    try:
        dir_path = os.path.dirname(os.path.abspath(path)) or "."
        fd = os.open(dir_path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        pass  # dir fsync unsupported (e.g. Windows) or transient — non-fatal

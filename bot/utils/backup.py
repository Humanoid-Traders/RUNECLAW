"""Data durability (MH4) — rotating, verifiable backups of irreplaceable state.

What cannot be regenerated if lost: the audit hash-chain (tamper-evidence),
the Ed25519 attestation key (identity — a new key stales the on-chain
anchor), sealed publications, anchor records, learning state, and the local
DB/vault files. Everything else (market data, caches) rebuilds itself.

Design:
- ``create_backup()`` tars the critical set into ``data/backups/`` with a
  sidecar manifest of per-file SHA-256 hashes, then rotates (oldest beyond
  BACKUP_KEEP deleted). Fail-soft: a missing file is recorded as absent,
  never fabricated.
- ``verify_backup()`` re-hashes every member against the manifest — the same
  "re-derive, don't trust" rule as Proof-of-PnL.
- Restore is deliberately MANUAL (docs/DURABILITY.md): the bot never
  overwrites its own live state from an archive.
- ``maybe_daily_backup()`` is called opportunistically (publish scheduler);
  throttled to one per BACKUP_INTERVAL_H via a stamp file.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import tarfile
import time
from pathlib import Path
from typing import Optional

_CRITICAL = [
    "logs/audit_chain.jsonl",
    "data/attestation_key.bin",
    "data/anchor_state.json",
    "data/proofofpnl_publication.json",
    "data/runeclaw.db",
    "data/secrets_vault.enc",
    "data/shadow_book.json",
    "data/proactive_watch.json",
    "data/venue_override.json",
    "data/catalog_seen.json",
]
_CRITICAL_GLOBS = ["data/learning/*", "data/portfolio_*", "data/risk_state_*"]


def _backup_dir() -> Path:
    return Path(os.environ.get("BACKUP_DIR", "data/backups"))


def _keep() -> int:
    try:
        return max(1, int(os.environ.get("BACKUP_KEEP", "14")))
    except ValueError:
        return 14


_ENV_OVERRIDES = {
    "data/anchor_state.json": "ANCHOR_STATE_PATH",
    "data/proofofpnl_publication.json": "PROOFOFPNL_PUBLICATION_PATH",
}


def critical_paths(root: str = ".") -> list[Path]:
    rootp = Path(root)
    found: list[Path] = []
    for rel in _CRITICAL:
        env_key = _ENV_OVERRIDES.get(rel)
        actual = os.environ.get(env_key, rel) if env_key else rel
        p = Path(actual) if os.path.isabs(actual) else rootp / actual
        if p.is_file():
            found.append(p)
    for pat in _CRITICAL_GLOBS:
        for m in sorted(glob.glob(str(rootp / pat))):
            if Path(m).is_file():
                found.append(Path(m))
    return found


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(root: str = ".", now: Optional[float] = None) -> tuple[Path, dict]:
    """Snapshot the critical set. Returns (archive_path, manifest)."""
    ts = int(now if now is not None else time.time())
    dest = _backup_dir()
    dest.mkdir(parents=True, exist_ok=True)
    name = f"runeclaw-backup-{ts}"
    archive = dest / f"{name}.tar.gz"
    files = critical_paths(root)
    manifest = {
        "created_at": ts,
        "files": {},
        "note": "verify with bot.utils.backup.verify_backup — hashes are "
                "re-derived from the archive, never trusted from this file",
    }
    with tarfile.open(archive, "w:gz") as tar:
        for p in files:
            # tarfile strips a leading "/" from member names on write — use
            # the stripped form everywhere so verify's member.name matches.
            rel = str(p).lstrip("/")
            manifest["files"][rel] = _sha256(p)
            tar.add(p, arcname=rel)
    (dest / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    _rotate(dest)
    return archive, manifest


def _rotate(dest: Path) -> None:
    archives = sorted(dest.glob("runeclaw-backup-*.tar.gz"))
    excess = len(archives) - _keep()
    for old in archives[:max(0, excess)]:
        try:
            old.unlink()
            side = old.with_name(old.name.replace(".tar.gz", ".manifest.json"))
            if side.exists():
                side.unlink()
        except OSError:
            pass


def list_backups() -> list[dict]:
    dest = _backup_dir()
    out = []
    for a in sorted(dest.glob("runeclaw-backup-*.tar.gz"), reverse=True):
        side = a.with_name(a.name.replace(".tar.gz", ".manifest.json"))
        n_files = None
        try:
            n_files = len(json.loads(side.read_text()).get("files", {}))
        except Exception:
            pass
        out.append({"name": a.name, "size_bytes": a.stat().st_size, "files": n_files})
    return out


def verify_backup(archive: Path | str) -> tuple[bool, list[str]]:
    """Re-hash every archive member against the manifest. (ok, problems)."""
    archive = Path(archive)
    problems: list[str] = []
    side = archive.with_name(archive.name.replace(".tar.gz", ".manifest.json"))
    try:
        manifest = json.loads(side.read_text())
    except Exception:
        return False, ["manifest missing or unreadable — cannot verify"]
    want: dict = manifest.get("files", {})
    seen = set()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                seen.add(member.name)
                f = tar.extractfile(member)
                h = hashlib.sha256()
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
                if member.name not in want:
                    problems.append(f"unexpected member not in manifest: {member.name}")
                elif h.hexdigest() != want[member.name]:
                    problems.append(f"HASH MISMATCH: {member.name}")
    except Exception as exc:
        return False, [f"archive unreadable: {exc}"]
    for missing in sorted(set(want) - seen):
        problems.append(f"missing from archive: {missing}")
    return (len(problems) == 0), problems


def maybe_daily_backup(root: str = ".", now: Optional[float] = None) -> Optional[Path]:
    """Opportunistic throttled backup (called from the publish scheduler).
    Fail-soft by contract: callers wrap in try/except."""
    try:
        interval_h = float(os.environ.get("BACKUP_INTERVAL_H", "24"))
    except ValueError:
        interval_h = 24.0
    if interval_h <= 0:
        return None
    ts = now if now is not None else time.time()
    stamp = _backup_dir() / ".last_backup"
    try:
        last = float(stamp.read_text().strip())
    except Exception:
        last = 0.0
    if ts - last < interval_h * 3600.0:
        return None
    archive, _ = create_backup(root, now=ts)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(str(ts))
    return archive

"""MH4 — durable, verifiable backups of irreplaceable state.

Contract: archives carry a sidecar manifest of per-file SHA-256 hashes;
verify re-derives every hash from the archive bytes (tampering fails);
rotation keeps BACKUP_KEEP; the daily hook throttles; restore is manual
(the module contains no code that overwrites live state from an archive).
"""

import json
import tarfile

import pytest

from bot.utils import backup


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "logs").mkdir(parents=True)
    (root / "data" / "learning").mkdir(parents=True)
    (root / "logs" / "audit_chain.jsonl").write_text('{"h":"x"}\n')
    (root / "data" / "attestation_key.bin").write_bytes(b"\x01\x02")
    (root / "data" / "learning" / "weights.json").write_text("{}")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("ANCHOR_STATE_PATH", raising=False)
    monkeypatch.delenv("PROOFOFPNL_PUBLICATION_PATH", raising=False)
    return root


def test_create_then_verify_roundtrip(world):
    archive, manifest = backup.create_backup(str(world), now=1_000_000)
    assert archive.exists()
    assert len(manifest["files"]) == 3
    ok, problems = backup.verify_backup(archive)
    assert ok, problems


def test_tampered_archive_fails_verification(world, tmp_path):
    archive, _ = backup.create_backup(str(world), now=1_000_000)
    # Rebuild the archive with one member's bytes changed; keep the manifest.
    extract = tmp_path / "x"
    with tarfile.open(archive) as tar:
        tar.extractall(extract, filter="data")
    victim = next(extract.rglob("audit_chain.jsonl"))
    victim.write_text('{"h":"FORGED"}\n')
    with tarfile.open(archive, "w:gz") as tar:
        for p in sorted(extract.rglob("*")):
            if p.is_file():
                tar.add(p, arcname=str(p.relative_to(extract)))
    ok, problems = backup.verify_backup(archive)
    assert not ok and any("HASH MISMATCH" in p for p in problems)


def test_missing_manifest_is_honest(world):
    archive, _ = backup.create_backup(str(world), now=1_000_000)
    archive.with_name(archive.name.replace(".tar.gz", ".manifest.json")).unlink()
    ok, problems = backup.verify_backup(archive)
    assert not ok and "manifest missing" in problems[0]


def test_rotation_keeps_newest(world, monkeypatch):
    monkeypatch.setenv("BACKUP_KEEP", "3")
    for i in range(5):
        backup.create_backup(str(world), now=1_000_000 + i)
    names = [b["name"] for b in backup.list_backups()]
    assert len(names) == 3
    assert names[0] == "runeclaw-backup-1000004.tar.gz", "newest kept, oldest rotated"


def test_daily_hook_throttles(world, monkeypatch):
    monkeypatch.setenv("BACKUP_INTERVAL_H", "24")
    assert backup.maybe_daily_backup(str(world), now=1_000_000) is not None
    assert backup.maybe_daily_backup(str(world), now=1_000_000 + 3600) is None
    assert backup.maybe_daily_backup(str(world), now=1_000_000 + 25 * 3600) is not None
    monkeypatch.setenv("BACKUP_INTERVAL_H", "0")
    assert backup.maybe_daily_backup(str(world), now=2_000_000_000) is None, "0 disables"


def test_restore_stays_manual_and_wiring_exists():
    import inspect
    src = inspect.getsource(backup)
    assert "extractall" not in src.replace("tar.extractfile", ""), \
        "the bot never overwrites live state from an archive — restore is manual"
    from bot.proofofpnl import scheduler
    assert "maybe_daily_backup" in inspect.getsource(scheduler)
    from bot.skills import telegram_handler
    assert '("backup", self._cmd_backup)' in inspect.getsource(telegram_handler)
    import pathlib
    assert (pathlib.Path(__file__).parent.parent / "docs" / "DURABILITY.md").exists()

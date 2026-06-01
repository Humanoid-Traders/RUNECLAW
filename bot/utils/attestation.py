"""
Ed25519 cryptographic attestation for RUNECLAW audit chain.

Provides non-repudiation: each audit batch is signed with an Ed25519 key,
proving the entries were created by this bot instance and haven't been
forged or replayed. Key material is generated on first run and stored
locally (data/attestation_key.bin).

Uses the standard library's hashlib for hashing and the cryptography
library for Ed25519. Falls back gracefully if the library is not
available (signatures disabled, logged warning).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Key storage location
_KEY_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
_KEY_PATH = os.path.join(_KEY_DIR, "attestation_key.bin")


@dataclass
class AttestationResult:
    """Result of signing or verifying an audit batch."""

    valid: bool
    signature_hex: str = ""
    public_key_hex: str = ""
    entries_hash: str = ""
    batch_size: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    error: Optional[str] = None


class AttestationEngine:
    """Ed25519 signing engine for audit chain batches.

    Signs batches of audit entries (Merkle root of entry hashes) with
    Ed25519, providing cryptographic non-repudiation.
    """

    def __init__(self, key_path: str = _KEY_PATH):
        self._key_path = Path(key_path)
        self._lock = threading.Lock()
        self._signing_key = None
        self._verify_key = None
        self._available = False
        self._init_keys()

    # -- key management -------------------------------------------------------

    def _init_keys(self) -> None:
        """Load or generate Ed25519 keypair."""
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
            from cryptography.hazmat.primitives import serialization

            if self._key_path.exists():
                key_bytes = self._key_path.read_bytes()
                self._signing_key = Ed25519PrivateKey.from_private_bytes(
                    key_bytes[:32]
                )
            else:
                self._key_path.parent.mkdir(parents=True, exist_ok=True)
                self._signing_key = Ed25519PrivateKey.generate()
                # Store raw 32-byte seed
                raw = self._signing_key.private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                )
                self._key_path.write_bytes(raw)
                os.chmod(str(self._key_path), 0o600)

            self._verify_key = self._signing_key.public_key()
            self._available = True
            logger.info(
                "Ed25519 attestation engine initialized (cryptography backend)"
            )

        except ImportError:
            logger.warning(
                "Ed25519 attestation unavailable: install 'cryptography' package. "
                "Audit chain integrity still protected by SHA-256 hash chain."
            )
            self._available = False

    # -- properties -----------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._available

    @property
    def public_key_hex(self) -> str:
        if not self._available or self._verify_key is None:
            return ""
        from cryptography.hazmat.primitives import serialization

        raw = self._verify_key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return raw.hex()

    # -- Merkle tree ----------------------------------------------------------

    def compute_merkle_root(self, entry_hashes: list[str]) -> str:
        """Compute Merkle root of a list of entry hashes."""
        if not entry_hashes:
            return "0" * 64

        # Leaf nodes
        nodes = [bytes.fromhex(h) for h in entry_hashes]

        # Build tree bottom-up
        while len(nodes) > 1:
            next_level = []
            for i in range(0, len(nodes), 2):
                left = nodes[i]
                # Duplicate odd node to pair with itself
                right = nodes[i + 1] if i + 1 < len(nodes) else left
                combined = hashlib.sha256(left + right).digest()
                next_level.append(combined)
            nodes = next_level

        return nodes[0].hex()

    # -- signing / verification -----------------------------------------------

    def sign_batch(self, entry_hashes: list[str]) -> AttestationResult:
        """Sign a batch of audit entries by their hashes.

        Computes Merkle root of all entry hashes, then signs with Ed25519.
        """
        if not self._available:
            return AttestationResult(
                valid=False,
                error="Ed25519 not available (missing cryptography package)",
                batch_size=len(entry_hashes),
            )

        if not entry_hashes:
            return AttestationResult(
                valid=False,
                error="Empty batch",
                batch_size=0,
            )

        with self._lock:
            try:
                merkle_root = self.compute_merkle_root(entry_hashes)

                # Sign the merkle root bytes
                signature = self._signing_key.sign(bytes.fromhex(merkle_root))

                return AttestationResult(
                    valid=True,
                    signature_hex=signature.hex(),
                    public_key_hex=self.public_key_hex,
                    entries_hash=merkle_root,
                    batch_size=len(entry_hashes),
                )
            except Exception as exc:
                return AttestationResult(
                    valid=False,
                    error=str(exc),
                    batch_size=len(entry_hashes),
                )

    def verify_batch(
        self,
        entry_hashes: list[str],
        signature_hex: str,
        public_key_hex: str,
    ) -> AttestationResult:
        """Verify a previously signed batch."""
        if not self._available:
            return AttestationResult(
                valid=False,
                error="Ed25519 not available",
            )

        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )

            merkle_root = self.compute_merkle_root(entry_hashes)
            pub_key = Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(public_key_hex)
            )

            pub_key.verify(
                bytes.fromhex(signature_hex),
                bytes.fromhex(merkle_root),
            )

            return AttestationResult(
                valid=True,
                signature_hex=signature_hex,
                public_key_hex=public_key_hex,
                entries_hash=merkle_root,
                batch_size=len(entry_hashes),
            )
        except Exception as exc:
            return AttestationResult(
                valid=False,
                error=f"Verification failed: {exc}",
                entries_hash=(
                    self.compute_merkle_root(entry_hashes) if entry_hashes else ""
                ),
                batch_size=len(entry_hashes),
            )

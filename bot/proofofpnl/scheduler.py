"""Continuous Proof-of-PnL publisher — the loop that fills the empty feed.

The sealer (``bot/proofofpnl/publish.publish_now``) is the unit called each
epoch; this is the scheduler that calls it. Until now nothing did, so the public
``/proof`` feed and the MCP ``get_proof_of_pnl`` tool served an empty store in
production — the thesis centerpiece, built end-to-end, with no producer.

This module is the producer. Design discipline mirrors the rest of the
Proof-of-PnL stack:

* **DEFAULT-OFF** — publishes nothing unless ``PROOFOFPNL_PUBLISH_ENABLED`` is
  set. No production behaviour changes until the operator turns it on.
* **FAIL-SAFE** — ``publish()`` never raises into its caller and never blocks;
  a bad epoch logs and returns ``None``. The engine's scan loop must never be
  put at risk by the act of publishing a track record.
* **DETERMINISTIC** — ``published_at`` is passed in (the sealer never reads the
  wall clock), so the same fills + stamp seal to the same ``publish_hash``.
* **I/O-FREE** — this module gathers no fills and touches no exchange; the
  caller passes already-fetched CCXT trades in. That keeps it a pure, testable
  unit and keeps all network/credential concerns on the engine side.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from bot.proofofpnl.assemble import assemble_track_record
from bot.proofofpnl.publish import PublicationStore, get_publication_store, publish_now

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 3600          # publish at most hourly
MIN_INTERVAL_S = 60               # floor — never hammer the sealer/exchange


def feature_enabled(env: Optional[dict] = None) -> bool:
    """Master switch for continuous publishing — default OFF (fail-closed)."""
    e = env if env is not None else os.environ
    return str(e.get("PROOFOFPNL_PUBLISH_ENABLED", "")).strip().lower() in (
        "1", "true", "yes", "on")


def publish_interval_s(env: Optional[dict] = None) -> int:
    """Seconds between publications (default hourly, floored at 60)."""
    e = env if env is not None else os.environ
    try:
        v = int(str(e.get("PROOFOFPNL_PUBLISH_INTERVAL_S", "") or DEFAULT_INTERVAL_S))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_S
    return v if v >= MIN_INTERVAL_S else DEFAULT_INTERVAL_S


class ProofOfPnLPublisher:
    """Cadenced, fail-safe publisher: assemble → seal → persist one epoch.

    The caller owns fetching fills (an async, credentialed operation) and hands
    them to :meth:`publish`. This object owns the cadence gate, the assembly +
    sealing, and recording the last-published time. Nothing here raises.
    """

    def __init__(self, *, account_ids: list[str],
                 agent_address: Optional[str] = None,
                 venue: str = "bitget",
                 interval_s: Optional[int] = None,
                 store: Optional[PublicationStore] = None,
                 env: Optional[dict] = None) -> None:
        self._account_ids = list(account_ids or [])
        self._agent_address = agent_address or None
        self._venue = venue
        self._interval_s = int(interval_s) if interval_s else publish_interval_s(env)
        self._store = store or get_publication_store()
        self._env = env
        self._last_publish_ts = 0

    @property
    def interval_s(self) -> int:
        return self._interval_s

    @property
    def last_publish_ts(self) -> int:
        return self._last_publish_ts

    def enabled(self) -> bool:
        return feature_enabled(self._env)

    def due(self, now_ts: int) -> bool:
        return (int(now_ts) - int(self._last_publish_ts)) >= self._interval_s

    def should_publish(self, now_ts: int, *, force: bool = False) -> bool:
        """True when publishing should run now: enabled AND (forced OR due)."""
        return self.enabled() and (force or self.due(now_ts))

    def publish(self, now_ts: int, ccxt_trades: Optional[list[dict]], *,
                range_start: int = 0, range_end: int = 0,
                open_balance: Any = None, close_balance: Any = None,
                balance_ccy: str = "USDT",
                envelope: Optional[dict] = None) -> Optional[dict]:
        """Assemble the fills into a public-safe bundle, seal it with ``now_ts``,
        and persist it as the latest publication. Records the publish time on
        success. NEVER raises — returns the publication, or ``None`` on any
        error (which is logged, not propagated)."""
        try:
            bundle = assemble_track_record(
                list(ccxt_trades or []),
                account_ids=self._account_ids,
                open_balance=open_balance, close_balance=close_balance,
                balance_ccy=balance_ccy,
                range_start=int(range_start or 0), range_end=int(range_end or 0),
                venue=self._venue, agent_address=self._agent_address,
                envelope=envelope)
            pub = publish_now(bundle, published_at_ts=int(now_ts), store=self._store)
            self._last_publish_ts = int(now_ts)
            logger.info(
                "Proof-of-PnL published: hash=%s tier=%s recon=%s trades=%d",
                str(pub.get("publish_hash"))[:12], pub.get("trust_tier"),
                pub.get("reconciliation"), len(list(ccxt_trades or [])))
            return pub
        except Exception as exc:   # fail-safe — publishing must never break the caller
            logger.warning("Proof-of-PnL publish skipped (error): %s", exc)
            return None


_OPERATOR_PUBLISHER: Optional[ProofOfPnLPublisher] = None
_OPERATOR_PUBLISHER_BUILT = False


def get_operator_publisher(env: Optional[dict] = None) -> Optional[ProofOfPnLPublisher]:
    """The single operator-scoped publisher, built once from env config.

    Returns ``None`` only if construction fails; the returned publisher is still
    inert until ``PROOFOFPNL_PUBLISH_ENABLED`` is set (checked per-tick via
    ``should_publish``), so it is always safe to hold a reference."""
    global _OPERATOR_PUBLISHER, _OPERATOR_PUBLISHER_BUILT
    if _OPERATOR_PUBLISHER_BUILT:
        return _OPERATOR_PUBLISHER
    e = env if env is not None else os.environ
    try:
        account_id = str(e.get("PROOFOFPNL_ACCOUNT_ID", "") or "operator").strip()
        _OPERATOR_PUBLISHER = ProofOfPnLPublisher(
            account_ids=[account_id],
            agent_address=(str(e.get("PROOFOFPNL_AGENT_ADDRESS", "")).strip() or None),
            venue=(str(e.get("PROOFOFPNL_VENUE", "")).strip() or "bitget"),
            env=env)
    except Exception as exc:
        logger.warning("Proof-of-PnL operator publisher not built: %s", exc)
        _OPERATOR_PUBLISHER = None
    _OPERATOR_PUBLISHER_BUILT = True
    return _OPERATOR_PUBLISHER


def reset_operator_publisher() -> None:
    """Test hook — drop the cached operator publisher."""
    global _OPERATOR_PUBLISHER, _OPERATOR_PUBLISHER_BUILT
    _OPERATOR_PUBLISHER = None
    _OPERATOR_PUBLISHER_BUILT = False

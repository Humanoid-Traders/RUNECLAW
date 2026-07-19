"""Least-privilege preflight — reconcile an Authority Envelope against the key's
*observable* posture, honestly.

The envelope (``authority.py``) is what a human GRANTED. This module checks what a
linked exchange key can ACTUALLY do and reports each dimension with an explicit
verification status — never inventing proof:

* ``CONFIRMED``  — observed and consistent with the envelope.
* ``VIOLATION``  — observed and INCONSISTENT (e.g. a key that can withdraw under an
                   envelope that forbids it — an over-privileged key).
* ``UNVERIFIED`` — could not be observed safely (e.g. withdraw permission without a
                   privileged key-info endpoint). We NEVER attempt a withdrawal to
                   test it — an honest UNVERIFIED beats invented proof, exactly as
                   in Proof-of-PnL.
* ``INFO``       — observed, no envelope constraint to check against.

Split so the decision logic is pure and unit-testable:

* ``reconcile_posture(envelope, observed)`` — PURE. Envelope + an observed-posture
  dict → a per-dimension report.
* ``probe_posture(venue, fields, sandbox)`` — async, network. Fills ``observed``
  from what can be observed SAFELY (a read-only balance fetch proves ``read`` and
  the live/demo environment; ``withdraw`` stays ``"unknown"`` unless a caller
  supplies a value from a privileged endpoint). Reuses the existing read-only
  validators — it never places an order or moves funds.
"""

from __future__ import annotations

from typing import Any, Optional

CONFIRMED = "CONFIRMED"
VIOLATION = "VIOLATION"
UNVERIFIED = "UNVERIFIED"
INFO = "INFO"


def _dim(name: str, status: str, detail: str) -> dict:
    return {"dimension": name, "status": status, "detail": detail}


def reconcile_posture(envelope: Optional[dict], observed: Optional[dict]) -> dict:
    """Reconcile a compiled ``envelope`` against an ``observed`` posture dict.

    ``observed`` (all optional)::

        {"read": bool,                       # key can read the account
         "environment": "live"|"demo"|None,  # which Bitget environment the key hit
         "expected_environment": "live"|"demo"|None,
         "withdraw": "on"|"off"|"unknown",   # granted withdraw scope, if known
         "ip_allowlist": [str, ...]}         # IPs the key is pinned to, if known

    Returns ``{ok, blocking, dimensions:[{dimension,status,detail}], summary}``.
    ``ok`` is False if ANY dimension is a VIOLATION (a hard mismatch). UNVERIFIED
    dimensions do NOT fail the preflight — they are surfaced honestly for a human
    to resolve, never silently passed as CONFIRMED.
    """
    env = envelope or {}
    obs = observed or {}
    dims: list[dict] = []

    # 1) READ — the envelope always needs at least read to be usable.
    read = obs.get("read")
    if read is True:
        dims.append(_dim("read", CONFIRMED, "key authenticates and can read the account"))
    elif read is False:
        dims.append(_dim("read", VIOLATION, "key cannot even read the account — unusable"))
    else:
        dims.append(_dim("read", UNVERIFIED, "read permission not probed"))

    # 2) ENVIRONMENT — a live key under a demo bot (or vice-versa) is a real hazard.
    environ = obs.get("environment")
    expected = obs.get("expected_environment")
    if environ and expected:
        if str(environ).lower() == str(expected).lower():
            dims.append(_dim("environment", CONFIRMED, f"key is a {environ} key, matching the bot"))
        else:
            dims.append(_dim("environment", VIOLATION,
                             f"key is {environ} but the bot runs {expected}"))
    elif environ:
        dims.append(_dim("environment", INFO, f"key environment: {environ}"))
    else:
        dims.append(_dim("environment", UNVERIFIED, "environment not probed"))

    # 3) WITHDRAW — the crux of non-custody. Compare the key's granted withdraw
    #    scope against the envelope's declared withdraw_allowed.
    want_withdraw = bool(env.get("withdraw_allowed"))
    wd = str(obs.get("withdraw") or "unknown").lower()
    if wd == "off":
        if want_withdraw:
            dims.append(_dim("withdraw", VIOLATION,
                             "envelope allows withdrawal but the key has NO withdraw "
                             "permission — the grant cannot be honored"))
        else:
            dims.append(_dim("withdraw", CONFIRMED,
                             "key has no withdraw permission — non-custodial, as intended"))
    elif wd == "on":
        if want_withdraw:
            dims.append(_dim("withdraw", CONFIRMED,
                             "key can withdraw and the envelope grants it (allowlisted)"))
        else:
            dims.append(_dim("withdraw", VIOLATION,
                             "OVER-PRIVILEGED KEY: it can withdraw, but this envelope "
                             "forbids withdrawal — mint a trade-only key"))
    else:  # unknown
        dims.append(_dim("withdraw", UNVERIFIED,
                         "withdraw permission not observable without a privileged "
                         "key-info endpoint — not verified (no withdrawal was attempted)"))

    # 4) IP ALLOWLIST — informational; the envelope does not (yet) bind IPs.
    ips = obs.get("ip_allowlist")
    if isinstance(ips, list) and ips:
        dims.append(_dim("ip_allowlist", INFO, f"key pinned to {len(ips)} IP(s)"))
    elif ips == []:
        dims.append(_dim("ip_allowlist", INFO, "key is NOT IP-restricted (consider pinning)"))

    violations = [d for d in dims if d["status"] == VIOLATION]
    unverified = [d for d in dims if d["status"] == UNVERIFIED]
    ok = not violations
    if violations:
        summary = f"PREFLIGHT FAIL: {len(violations)} violation(s) — " + \
                  "; ".join(d["detail"] for d in violations)
    elif unverified:
        summary = (f"PREFLIGHT OK with {len(unverified)} unverified dimension(s) — "
                   "resolve for a full non-custodial proof")
    else:
        summary = "PREFLIGHT OK: key posture matches the authority envelope"
    return {"ok": ok, "blocking": [d["dimension"] for d in violations],
            "dimensions": dims, "summary": summary}


async def probe_posture(venue: str, fields: dict, *,
                        sandbox: bool = False,
                        withdraw: str = "unknown") -> dict:
    """Observe a key's posture SAFELY (read-only). Returns an ``observed`` dict for
    ``reconcile_posture``. Never places an order or moves funds.

    ``read``/``environment`` come from the existing read-only balance validators.
    ``withdraw`` is passed through (default ``"unknown"``) — this probe does NOT
    attempt a withdrawal, so it cannot prove withdraw scope on its own; a caller
    that has queried a privileged key-info endpoint may supply ``"on"``/``"off"``.
    """
    venue = str(venue).lower().strip()
    observed: dict[str, Any] = {
        "environment": "demo" if sandbox else "live",
        "expected_environment": "demo" if sandbox else "live",
        "withdraw": withdraw,
    }
    try:
        from bot.core.exchange_credentials import validate_venue_credentials
        ok, detail = await validate_venue_credentials(venue, fields, sandbox=sandbox)
        observed["read"] = bool(ok)
        observed["read_detail"] = detail
    except Exception as exc:   # import or probe failure → read unverified, honest
        observed["read"] = None
        observed["read_detail"] = f"probe unavailable: {exc}"
    return observed


def human_readable(report: Optional[dict]) -> str:
    """Plain-text render of a preflight report (no markup)."""
    if not report or not isinstance(report, dict):
        return "No preflight report."
    lines = [report.get("summary", "")]
    _icon = {CONFIRMED: "✓", VIOLATION: "✗", UNVERIFIED: "?", INFO: "·"}
    for d in report.get("dimensions", []):
        lines.append(f"  {_icon.get(d['status'], '·')} {d['dimension']}: "
                     f"{d['status']} — {d['detail']}")
    return "\n".join(lines)

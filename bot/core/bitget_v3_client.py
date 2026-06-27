"""Bitget v3 REST transport.

A thin, signed-HTTP client for Bitget's ``/api/v3`` endpoints, extracted from
``live_executor.py`` where the same HMAC-SHA256 signing block was copy-pasted at
five call sites (the riskiest possible duplication: a one-byte drift in the
signature breaks *every* live order). Centralising it here gives a single,
independently-testable signing path and lets the executor be tested against a
fake client.

The signature scheme is Bitget's standard:

    pre_sign = timestamp + METHOD + requestPath + body
    ACCESS-SIGN = base64( HMAC_SHA256(api_secret, pre_sign) )

``request()`` is intentionally synchronous and lets network/HTTP errors
propagate unchanged — every existing caller already wraps it in
``asyncio.to_thread`` and its own ``try/except`` (which inspects ``e.read()`` on
an ``HTTPError``), so keeping that contract preserves their behaviour exactly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.request
from typing import Any, Optional

from bot.config import CONFIG

BITGET_BASE_URL = "https://api.bitget.com"


class BitgetV3Client:
    """Signed HTTP transport for Bitget v3 REST endpoints."""

    def __init__(self, api_key: str, api_secret: str, passphrase: str,
                 base_url: str = BITGET_BASE_URL) -> None:
        self._api_key = api_key or ""
        self._api_secret = api_secret or ""
        self._passphrase = passphrase or ""
        self._base_url = base_url

    @classmethod
    def from_config(cls) -> "BitgetV3Client":
        """Build a client from the live exchange credentials in CONFIG.

        Credentials are read fresh on each construction (matching the previous
        inline behaviour, which read ``CONFIG.exchange`` per call), so a runtime
        credential change is picked up by the next request.
        """
        cfg = CONFIG.exchange
        return cls(cfg.api_key, cfg.api_secret, cfg.passphrase)

    @property
    def has_credentials(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Return the base64 ACCESS-SIGN for the given request components.

        ``pre_sign = timestamp + method + path + body`` — body is "" for GET.
        """
        pre_sign = timestamp + method + path + body
        return base64.b64encode(
            hmac.new(self._api_secret.encode(), pre_sign.encode(), hashlib.sha256).digest()
        ).decode()

    def _headers(self, timestamp: str, signature: str) -> dict[str, str]:
        return {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self._passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    def build_request(self, method: str, path: str,
                      body_dict: Optional[dict] = None) -> urllib.request.Request:
        """Construct a signed urllib Request (no network I/O).

        Exposed for testing — asserts can inspect the exact URL, method, body
        and headers that would go on the wire.
        """
        body = json.dumps(body_dict) if body_dict is not None else ""
        timestamp = str(int(time.time() * 1000))
        signature = self.sign(timestamp, method, path, body)
        data = body.encode() if body_dict is not None else None
        req = urllib.request.Request(self._base_url + path, data=data, method=method)
        for key, value in self._headers(timestamp, signature).items():
            req.add_header(key, value)
        return req

    def request(self, method: str, path: str,
                body_dict: Optional[dict] = None, timeout: float = 10) -> Any:
        """Sign and send the request; return the parsed JSON response.

        Raises on network/HTTP errors (urllib.error.*), exactly as the inline
        ``urlopen`` calls did, so existing callers' ``try/except`` blocks — which
        read ``e.read()`` off an ``HTTPError`` to recover the JSON error body —
        keep working unchanged.
        """
        req = self.build_request(method, path, body_dict)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())

    def get(self, path: str, timeout: float = 10) -> Any:
        return self.request("GET", path, None, timeout)

    def post(self, path: str, body_dict: dict, timeout: float = 10) -> Any:
        return self.request("POST", path, body_dict, timeout)

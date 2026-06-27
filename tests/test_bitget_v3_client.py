"""
BitgetV3Client — the signed-HTTP transport extracted from live_executor.py.

Because the v3 REST path only executes against the live exchange, these tests
are the safety net for the extraction: they prove the client produces a
BYTE-IDENTICAL signature to the old inline formula, builds the exact request
(url/method/body/headers) the inline code did, and lets errors propagate so the
callers' existing try/except keeps working.
"""

import base64
import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

from bot.core.bitget_v3_client import BITGET_BASE_URL, BitgetV3Client

KEY, SECRET, PASS = "test-key", "test-secret", "test-pass"


def _client():
    return BitgetV3Client(KEY, SECRET, PASS)


def _expected_sig(secret, ts, method, path, body=""):
    """The exact pre-extraction formula, recomputed independently."""
    pre_sign = ts + method + path + body
    return base64.b64encode(
        hmac.new(secret.encode(), pre_sign.encode(), hashlib.sha256).digest()
    ).decode()


class TestSigningIsByteIdentical:
    def test_get_signature_matches_inline_formula(self):
        ts, path = "1700000000000", "/api/v3/position/current-position?category=USDT-FUTURES"
        assert _client().sign(ts, "GET", path) == _expected_sig(SECRET, ts, "GET", path)

    def test_post_signature_matches_inline_formula(self):
        ts, path = "1700000000001", "/api/v3/trade/place-strategy-order"
        body = json.dumps({"symbol": "BTCUSDT", "posSide": "long"})
        assert _client().sign(ts, "POST", path, body) == _expected_sig(SECRET, ts, "POST", path, body)

    def test_pre_sign_is_ts_method_path_body(self):
        # GET has empty body; POST appends the JSON body.
        ts = "123"
        assert _client().sign(ts, "GET", "/p") == _expected_sig(SECRET, ts, "GET", "/p", "")
        assert _client().sign(ts, "POST", "/p", '{"a":1}') == _expected_sig(SECRET, ts, "POST", "/p", '{"a":1}')


class TestBuildRequest:
    def test_get_request_shape_and_headers(self):
        path = "/api/v3/account/settings"
        with patch("bot.core.bitget_v3_client.time.time", return_value=1700000000.0):
            req = _client().build_request("GET", path)
        ts = "1700000000000"
        assert req.full_url == BITGET_BASE_URL + path
        assert req.get_method() == "GET"
        assert req.data is None  # no body on GET
        assert req.headers["Access-key"] == KEY
        assert req.headers["Access-passphrase"] == PASS
        assert req.headers["Access-timestamp"] == ts
        assert req.headers["Access-sign"] == _expected_sig(SECRET, ts, "GET", path)
        assert req.headers["Content-type"] == "application/json"
        assert req.headers["Locale"] == "en-US"

    def test_post_request_carries_signed_body(self):
        path = "/api/v3/trade/close-positions"
        body_dict = {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "posSide": "long"}
        with patch("bot.core.bitget_v3_client.time.time", return_value=1700000000.0):
            req = _client().build_request("POST", path, body_dict)
        ts = "1700000000000"
        expected_body = json.dumps(body_dict)
        assert req.get_method() == "POST"
        assert req.data == expected_body.encode()
        # The signature must be over the EXACT serialized body that is sent.
        assert req.headers["Access-sign"] == _expected_sig(SECRET, ts, "POST", path, expected_body)


class TestRequestIO:
    def test_request_parses_json_response(self):
        class _Resp:
            def read(self):
                return b'{"code":"00000","data":[]}'

        with patch("bot.core.bitget_v3_client.urllib.request.urlopen", return_value=_Resp()) as uo:
            out = _client().request("GET", "/api/v3/x")
        assert out == {"code": "00000", "data": []}
        # urlopen received our signed Request and the timeout.
        sent_req = uo.call_args.args[0]
        assert sent_req.headers["Access-sign"]

    def test_request_propagates_errors(self):
        # Callers rely on exceptions propagating (they read e.read() on HTTPError).
        def _boom(*a, **k):
            raise OSError("network down")

        with patch("bot.core.bitget_v3_client.urllib.request.urlopen", side_effect=_boom):
            with pytest.raises(OSError):
                _client().request("POST", "/api/v3/x", {"a": 1})

    def test_get_and_post_helpers_route_correctly(self):
        captured = {}

        class _Resp:
            def read(self):
                return b'{"ok":1}'

        def _fake(req, timeout=10):
            captured["method"] = req.get_method()
            captured["data"] = req.data
            return _Resp()

        with patch("bot.core.bitget_v3_client.urllib.request.urlopen", side_effect=_fake):
            assert _client().get("/g") == {"ok": 1}
            assert captured["method"] == "GET" and captured["data"] is None
            assert _client().post("/p", {"x": 2}) == {"ok": 1}
            assert captured["method"] == "POST" and captured["data"] == b'{"x": 2}'


class TestFromConfig:
    def test_from_config_reads_exchange_credentials(self):
        from types import SimpleNamespace
        with patch("bot.core.bitget_v3_client.CONFIG") as cfg:
            cfg.exchange = SimpleNamespace(api_key="K", api_secret="S", passphrase="P")
            client = BitgetV3Client.from_config()
        assert client.has_credentials
        assert client.sign("1", "GET", "/p") == _expected_sig("S", "1", "GET", "/p")

# SPDX-License-Identifier: MIT
"""Regression tests for openclaw_x402.mcp_server TLS verification."""
import os

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("OPENCLAW_INSECURE_SKIP_TLS_VERIFY", raising=False)
    yield


class TestVerifyDefault:
    def test_default_is_secure(self, monkeypatch):
        import importlib
        import openclaw_x402.mcp_server as mcp_server
        importlib.reload(mcp_server)
        assert mcp_server._VERIFY is True
        assert mcp_server._INSECURE_TLS is False

    def test_env_one_overrides(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_INSECURE_SKIP_TLS_VERIFY", "1")
        import importlib
        import openclaw_x402.mcp_server as mcp_server
        importlib.reload(mcp_server)
        assert mcp_server._VERIFY is False
        assert mcp_server._INSECURE_TLS is True

    def test_env_true_overrides(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_INSECURE_SKIP_TLS_VERIFY", "true")
        import importlib
        import openclaw_x402.mcp_server as mcp_server
        importlib.reload(mcp_server)
        assert mcp_server._VERIFY is False

    def test_env_yes_overrides(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_INSECURE_SKIP_TLS_VERIFY", "yes")
        import importlib
        import openclaw_x402.mcp_server as mcp_server
        importlib.reload(mcp_server)
        assert mcp_server._VERIFY is False

    def test_env_zero_does_not_override(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_INSECURE_SKIP_TLS_VERIFY", "0")
        import importlib
        import openclaw_x402.mcp_server as mcp_server
        importlib.reload(mcp_server)
        assert mcp_server._VERIFY is True


class TestVerifyIsPassedThrough:
    def test_tx_lookup_uses_resolved_verify(self, monkeypatch):
        import openclaw_x402.mcp_server as mcp_server
        seen = {}

        def fake_get(url, verify, timeout):
            seen["url"] = url
            seen["verify"] = verify
            class R:
                status_code = 200
                def json(self_inner):
                    return {"to": mcp_server.TREASURY_WALLET, "from": "chain-sender", "amount": "0.25"}
            return R()

        monkeypatch.setattr(mcp_server, "TREASURY_WALLET", "openclaw-treasury")
        monkeypatch.setattr(mcp_server.httpx, "get", fake_get)

        mcp_server._verify_payment(
            '{"tx_id": "tx-x", "from": "client", "amount": 0.25}',
            0.25,
            "bcos_report",
        )

        assert seen["verify"] is True

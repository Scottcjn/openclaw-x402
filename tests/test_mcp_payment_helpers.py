import json

import openclaw_x402.mcp_server as mcp_server


class DummyResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_verify_payment_rejects_malformed_json_token():
    result = mcp_server._verify_payment("not-json", 0.1, "premium_search")

    assert result == {
        "valid": False,
        "error": "Malformed payment token. Expected JSON.",
    }


def test_verify_payment_rejects_insufficient_amount_before_network_call(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("network verification should not run for insufficient payment")

    monkeypatch.setattr(mcp_server.httpx, "get", fail_if_called)

    result = mcp_server._verify_payment(
        json.dumps({"tx_id": "tx-low", "from": "payer", "amount": 0.04}),
        0.05,
        "miner_profile",
    )

    assert result["valid"] is False
    assert "Insufficient payment" in result["error"]


def test_verify_payment_accepts_matching_confirmed_ledger_transaction(monkeypatch):
    monkeypatch.setattr(mcp_server, "TREASURY_WALLET", "openclaw-treasury")

    def fake_get(url, verify, timeout):
        assert url.endswith("/api/tx/tx-ok")
        assert verify is False
        assert timeout == 10
        return DummyResponse(200, {"to": "openclaw-treasury", "amount": "0.25"})

    monkeypatch.setattr(mcp_server.httpx, "get", fake_get)

    result = mcp_server._verify_payment(
        json.dumps({"tx_id": "tx-ok", "from": "payer-wallet", "amount": 0.25}),
        0.25,
        "bcos_report",
    )

    assert result == {
        "valid": True,
        "tx_id": "tx-ok",
        "from": "payer-wallet",
        "amount": 0.25,
    }


def test_verify_payment_rejects_confirmed_transaction_to_wrong_treasury(monkeypatch):
    monkeypatch.setattr(mcp_server, "TREASURY_WALLET", "expected-treasury")
    monkeypatch.setattr(
        mcp_server.httpx,
        "get",
        lambda *args, **kwargs: DummyResponse(200, {"to": "other-wallet", "amount": "1.0"}),
    )

    result = mcp_server._verify_payment(
        json.dumps({"tx_id": "tx-wrong", "from": "payer", "amount": 1.0}),
        0.25,
        "bcos_report",
    )

    assert result == {
        "valid": False,
        "error": "Transaction destination or amount mismatch.",
    }


def test_verify_payment_reports_unverified_tx_when_testnet_fallback_disabled(monkeypatch):
    monkeypatch.setenv("X402_TESTNET", "0")
    monkeypatch.setattr(
        mcp_server.httpx,
        "get",
        lambda *args, **kwargs: DummyResponse(404, {"error": "missing"}),
    )

    result = mcp_server._verify_payment(
        json.dumps({"tx_id": "missing-tx", "from": "payer", "amount": 1.0}),
        0.25,
        "bcos_report",
    )

    assert result == {"valid": False, "error": "Cannot verify tx: HTTP 404"}


def test_payment_required_instructions_include_price_treasury_and_retry_token(monkeypatch):
    monkeypatch.setattr(mcp_server, "RUSTCHAIN_NODE", "https://node.example")
    monkeypatch.setattr(mcp_server, "TREASURY_WALLET", "treasury-wallet")

    payload = json.loads(
        mcp_server._payment_required(0.1, "premium_search", "Search ledger")
    )

    assert payload["status"] == "payment_required"
    assert payload["x402"]["price_rtc"] == 0.1
    assert payload["x402"]["treasury"] == "treasury-wallet"
    assert payload["x402"]["node"] == "https://node.example"
    assert payload["x402"]["tool"] == "premium_search"
    assert '"memo": "x402:premium_search"' in payload["x402"]["how_to_pay"]
    assert "payment_token=" in payload["x402"]["how_to_pay"]


def test_gate_returns_none_only_after_valid_payment(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "_verify_payment",
        lambda token, price, tool_name: (
            {"valid": True}
            if token == "valid-token"
            else {"valid": False, "error": "bad token"}
        ),
    )

    required = json.loads(mcp_server._gate("", 0.1, "premium_search", "Search ledger"))
    failed = json.loads(
        mcp_server._gate("invalid-token", 0.1, "premium_search", "Search ledger")
    )

    assert required["status"] == "payment_required"
    assert failed["status"] == "payment_failed"
    assert mcp_server._gate("valid-token", 0.1, "premium_search", "Search ledger") is None

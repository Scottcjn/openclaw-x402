import json

import openclaw_x402.mcp_server as mcp_server


class DummyResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _confirmed_tx(monkeypatch, amount="0.25"):
    """Make the ledger report one confirmed payment to the treasury."""
    monkeypatch.setattr(mcp_server, "TREASURY_WALLET", "openclaw-treasury")
    monkeypatch.setattr(
        mcp_server.httpx,
        "get",
        lambda *a, **k: DummyResponse(
            200, {"to": "openclaw-treasury", "amount": amount, "from": "chain-sender"}
        ),
    )


def _token(tx_id="tx-paid", amount=0.25):
    return json.dumps({"tx_id": tx_id, "from": "payer", "amount": amount})


def test_payment_token_is_spent_after_first_use(monkeypatch):
    """A confirmed tx stays confirmed forever; it must still buy only one call."""
    _confirmed_tx(monkeypatch)

    first = mcp_server._verify_payment(_token(), 0.25, "bcos_report")
    assert first["valid"] is True

    second = mcp_server._verify_payment(_token(), 0.25, "bcos_report")
    assert second["valid"] is False
    assert "already used" in second["error"]


def test_spent_token_cannot_be_carried_to_another_tool(monkeypatch):
    _confirmed_tx(monkeypatch)

    assert mcp_server._verify_payment(_token(), 0.25, "bcos_report")["valid"] is True

    reused = mcp_server._verify_payment(_token(), 0.1, "premium_search")
    assert reused["valid"] is False
    assert "already used" in reused["error"]


def test_gate_refuses_the_second_call_with_the_same_token(monkeypatch):
    _confirmed_tx(monkeypatch)

    assert mcp_server._gate(_token(), 0.25, "bcos_report", "BCOS trust report") is None

    replay = mcp_server._gate(_token(), 0.25, "bcos_report", "BCOS trust report")
    assert replay is not None
    assert json.loads(replay)["status"] == "payment_failed"


def test_a_different_transaction_still_pays(monkeypatch):
    _confirmed_tx(monkeypatch)

    assert mcp_server._verify_payment(_token("tx-a"), 0.25, "bcos_report")["valid"] is True
    assert mcp_server._verify_payment(_token("tx-b"), 0.25, "bcos_report")["valid"] is True


def test_unwritable_ledger_fails_closed(monkeypatch, tmp_path):
    """If the spend ledger cannot be written, refuse rather than allow replays."""
    _confirmed_tx(monkeypatch)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setattr(mcp_server, "SPENT_TX_DB", str(blocker / "spent_tx.db"))

    result = mcp_server._verify_payment(_token(), 0.25, "bcos_report")
    assert result["valid"] is False
    assert "ledger unavailable" in result["error"]

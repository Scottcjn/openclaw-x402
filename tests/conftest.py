import pytest

import openclaw_x402.mcp_server as mcp_server


@pytest.fixture(autouse=True)
def isolated_spent_tx_ledger(tmp_path, monkeypatch):
    """Give every test its own spent-transaction ledger.

    Without this the ledger would live in the developer's home directory and
    leak between tests and between runs (a tx_id spent by one test would be
    rejected as a replay by the next one).
    """
    monkeypatch.setattr(mcp_server, "SPENT_TX_DB", str(tmp_path / "spent_tx.db"))

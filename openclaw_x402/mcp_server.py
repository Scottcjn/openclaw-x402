"""
OpenClaw x402 MCP Server -- Paid tools for AI agents.

Claude calls a paid tool. It just works.

Flow:
  1. Agent calls tool (e.g. premium_search)
  2. Tool checks for payment token in arguments
  3. No token? Returns price + payment instructions
  4. Agent signs RTC payment via RustChain
  5. Agent retries with payment token
  6. Server verifies payment on-chain, executes tool

Usage:
    python -m openclaw_x402.mcp_server

Or in Claude Desktop config:
    {
      "mcpServers": {
        "openclaw-x402": {
          "command": "python",
          "args": ["-m", "openclaw_x402.mcp_server"],
          "env": {
            "RUSTCHAIN_NODE": "https://50.28.86.131",
            "TREASURY_WALLET": "your-wallet-id"
          }
        }
      }
    }
"""

import hashlib
import json
import logging
import math
import os
import sqlite3
import time

import httpx
from fastmcp import FastMCP

log = logging.getLogger("openclaw_x402.mcp")

# --- Config ---
RUSTCHAIN_NODE = os.environ.get("RUSTCHAIN_NODE", "https://50.28.86.131")
TREASURY_WALLET = os.environ.get("TREASURY_WALLET", "openclaw-x402-treasury")
ADMIN_KEY = os.environ.get("RC_ADMIN_KEY", "")

mcp = FastMCP(
    "openclaw-x402",
    instructions=(
        "Paid MCP tools. AI agents pay RTC per call via HTTP 402 protocol. "
        "Call list_prices to see available tools and costs. "
        "Paid tools require a payment_token argument -- call without it first to get payment instructions."
    ),
)

# --- Payment verification ---

# Spent-transaction ledger. A payment_token is a bearer credential: anyone
# holding the tx_id (including any third party reading it off the public
# ledger) can present it. One transaction must therefore buy exactly one call.
SPENT_TX_DB = os.environ.get(
    "X402_SPENT_TX_DB",
    os.path.join(os.path.expanduser("~"), ".openclaw-x402", "spent_tx.db"),
)


def _consume_tx(tx_id: str, tool_name: str) -> str:
    """
    Atomically mark a verified tx_id as spent.

    Returns "claimed" the first time a tx_id is seen, "replay" if it was
    already spent, and "unavailable" if the ledger cannot be written --
    which is refused rather than accepted, in line with the rest of the
    verification path failing closed.
    """
    if not tx_id:
        return "replay"
    try:
        parent = os.path.dirname(SPENT_TX_DB)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(SPENT_TX_DB, timeout=10)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS spent_tx ("
                "  tx_id TEXT PRIMARY KEY,"
                "  tool TEXT,"
                "  spent_at REAL"
                ")"
            )
            conn.execute(
                "INSERT INTO spent_tx (tx_id, tool, spent_at) VALUES (?, ?, ?)",
                (tx_id, tool_name, time.time()),
            )
            conn.commit()
            return "claimed"
        except sqlite3.IntegrityError:
            return "replay"
        finally:
            conn.close()
    except Exception as e:
        log.error("Spent-tx ledger unavailable (%s): refusing payment %s", e, tx_id)
        return "unavailable"


def _parse_payment_amount(value) -> float | None:
    """Parse a JSON payment amount and reject values the ledger cannot represent."""
    if isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(amount):
        return None
    return amount


def _verify_payment(payment_token: str, expected_price: float, tool_name: str) -> dict:
    """
    Verify an RTC payment token against RustChain.

    A payment_token is a JSON string: {"tx_id": "...", "from": "...", "amount": ...}
    """
    try:
        token = json.loads(payment_token)
    except (json.JSONDecodeError, TypeError):
        return {"valid": False, "error": "Malformed payment token. Expected JSON."}
    if not isinstance(token, dict):
        return {"valid": False, "error": "Malformed payment token. Expected JSON object."}

    tx_id = token.get("tx_id", "")
    if not isinstance(tx_id, str) or not tx_id.strip():
        return {"valid": False, "error": "Malformed payment token. Expected non-empty tx_id."}
    tx_id = tx_id.strip()

    amount = _parse_payment_amount(token.get("amount", 0))
    if amount is None:
        return {
            "valid": False,
            "error": "Malformed payment token amount. Expected a finite number.",
        }
    # NOTE: the client-supplied "from" is intentionally ignored; the verified
    # sender is read from the on-chain tx below.

    if amount < expected_price:
        return {
            "valid": False,
            "error": f"Insufficient payment. Sent {amount} RTC, need {expected_price} RTC.",
        }

    # Verify tx exists on RustChain ledger
    try:
        resp = httpx.get(
            f"{RUSTCHAIN_NODE}/api/tx/{tx_id}",
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            tx_data = resp.json()
            chain_to = tx_data.get("to")
            chain_from = tx_data.get("from")
            chain_amount = _parse_payment_amount(tx_data.get("amount", 0))
            if (
                chain_to == TREASURY_WALLET
                and chain_amount is not None
                and chain_amount >= expected_price
                and chain_from
            ):
                # A confirmed transaction stays confirmed forever, so it would
                # otherwise pay for unlimited calls. Spend it exactly once.
                claim = _consume_tx(tx_id, tool_name)
                if claim == "replay":
                    return {
                        "valid": False,
                        "error": (
                            "Payment token already used. Each transaction pays "
                            "for exactly one call -- send a new payment."
                        ),
                    }
                if claim != "claimed":
                    return {
                        "valid": False,
                        "error": "Cannot verify payment: spent-transaction ledger unavailable.",
                    }
                # Bind the result to the verified on-chain sender AND amount.
                # Never echo the client-supplied `from`/`amount` (those are
                # attacker-controlled and would corrupt downstream accounting).
                return {
                    "valid": True,
                    "tx_id": tx_id,
                    "from": chain_from,
                    "amount": chain_amount,
                }
            return {
                "valid": False,
                "error": "Transaction destination, amount, or sender could not be verified.",
            }
        # SECURITY (fail closed): never accept a payment "on trust" when the
        # ledger lookup does not return a verified 200. The previous testnet
        # fallback (default ON) accepted any self-asserted token whenever the
        # tx lookup 404'd/errored — i.e. for every fabricated tx_id.
        return {"valid": False, "error": f"Cannot verify tx: HTTP {resp.status_code}"}
    except Exception as e:
        return {"valid": False, "error": f"Verification failed: {e}"}


def _payment_required(price: float, tool_name: str, description: str) -> str:
    """Return 402-style payment instructions as a tool response."""
    return json.dumps(
        {
            "status": "payment_required",
            "x402": {
                "version": "1",
                "price_rtc": price,
                "treasury": TREASURY_WALLET,
                "node": RUSTCHAIN_NODE,
                "tool": tool_name,
                "description": description,
                "how_to_pay": (
                    f"POST {RUSTCHAIN_NODE}/wallet/transfer/signed with "
                    f'{{"to_address": "{TREASURY_WALLET}", "amount_rtc": {price}, '
                    f'"memo": "x402:{tool_name}"}}. '
                    "Then retry this tool with payment_token="
                    '\'{"tx_id": "<tx_hash>", "from": "<your_wallet>", "amount": <amount>}\''
                ),
            },
        },
        indent=2,
    )


def _gate(payment_token: str, price: float, tool_name: str, description: str):
    """
    Payment gate. Returns None if payment is valid, or an error string if not.

    Usage inside a tool:
        err = _gate(payment_token, 0.1, "my_tool", "My tool")
        if err:
            return err
        # ... do work ...
    """
    if not payment_token:
        return _payment_required(price, tool_name, description)

    result = _verify_payment(payment_token, price, tool_name)
    if not result["valid"]:
        return json.dumps(
            {
                "status": "payment_failed",
                "error": result["error"],
                "retry": json.loads(_payment_required(price, tool_name, description)),
            },
            indent=2,
        )

    log.info("Paid tool %s executed: %s RTC from %s", tool_name, price, result.get("from"))
    return None  # Payment OK


def _paid_result(data: dict, price: float, tx_token: str) -> str:
    """Wrap a tool result with payment receipt."""
    try:
        token = json.loads(tx_token)
    except Exception:
        token = {}
    if not isinstance(token, dict):
        token = {}
    return json.dumps(
        {
            "status": "ok",
            "payment": {
                "tx_id": token.get("tx_id", ""),
                "amount": price,
                "currency": "RTC",
            },
            "result": data,
        },
        indent=2,
    )


# ============================================================
# Paid tools -- each has explicit parameters + payment_token
# ============================================================


@mcp.tool
def premium_search(query: str = "", payment_token: str = "") -> str:
    """[0.1 RTC] Search the RustChain ledger for transactions.

    Args:
        query: Search term (wallet ID, memo text, etc.)
        payment_token: RTC payment JSON. Omit to get payment instructions.
    """
    err = _gate(payment_token, 0.1, "premium_search", "Search RustChain ledger")
    if err:
        return err

    try:
        resp = httpx.get(
            f"{RUSTCHAIN_NODE}/api/ledger",
            params={"q": query, "limit": 20},
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            return _paid_result({"matches": resp.json(), "query": query}, 0.1, payment_token)
    except Exception:
        pass
    return _paid_result(
        {"matches": [], "query": query, "note": "Ledger search unavailable, returning stub."},
        0.1,
        payment_token,
    )


@mcp.tool
def miner_profile(miner_id: str = "", payment_token: str = "") -> str:
    """[0.05 RTC] Get detailed miner hardware fingerprint profile.

    Args:
        miner_id: The miner wallet ID to look up.
        payment_token: RTC payment JSON. Omit to get payment instructions.
    """
    err = _gate(payment_token, 0.05, "miner_profile", "Miner hardware profile")
    if err:
        return err

    try:
        resp = httpx.get(
            f"{RUSTCHAIN_NODE}/api/miner/{miner_id}",
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            return _paid_result(resp.json(), 0.05, payment_token)
    except Exception:
        pass
    return _paid_result(
        {"miner_id": miner_id, "note": "Profile lookup unavailable."},
        0.05,
        payment_token,
    )


@mcp.tool
def bcos_report(repo: str = "", payment_token: str = "") -> str:
    """[0.25 RTC] Generate a BCOS trust report for a GitHub repository.

    Args:
        repo: GitHub repo in owner/name format (e.g. "Scottcjn/Rustchain").
        payment_token: RTC payment JSON. Omit to get payment instructions.
    """
    err = _gate(payment_token, 0.25, "bcos_report", "BCOS trust report for a GitHub repo")
    if err:
        return err

    report_id = hashlib.sha256(f"{repo}:{time.time()}".encode()).hexdigest()[:16]
    return _paid_result(
        {
            "repo": repo,
            "report_id": report_id,
            "trust_score": "pending",
            "note": "BCOS engine integration coming. This is a demo response.",
        },
        0.25,
        payment_token,
    )


# ============================================================
# Free tools
# ============================================================


@mcp.tool
def network_status() -> str:
    """[FREE] Check RustChain network health. No payment required."""
    try:
        resp = httpx.get(f"{RUSTCHAIN_NODE}/health", verify=False, timeout=10)
        if resp.status_code == 200:
            return json.dumps(resp.json(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"status": "unreachable"})


@mcp.tool
def list_prices() -> str:
    """[FREE] List all paid tools and their RTC prices."""
    return json.dumps(
        {
            "tools": [
                {"name": "premium_search", "price_rtc": 0.1, "description": "Search RustChain ledger"},
                {"name": "miner_profile", "price_rtc": 0.05, "description": "Miner hardware profile"},
                {"name": "bcos_report", "price_rtc": 0.25, "description": "BCOS trust report"},
            ],
            "free_tools": ["network_status", "list_prices"],
            "payment_info": {
                "currency": "RTC (RustChain Token)",
                "reference_rate": "1 RTC = $0.10 USD",
                "treasury": TREASURY_WALLET,
                "node": RUSTCHAIN_NODE,
            },
        },
        indent=2,
    )


# ============================================================
# Entry point
# ============================================================


def main():
    """Run the MCP server."""
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    log.info("Starting OpenClaw x402 MCP Server")
    log.info("  Node: %s", RUSTCHAIN_NODE)
    log.info("  Treasury: %s", TREASURY_WALLET)
    # Payment verification always requires a real on-chain 200 lookup now;
    # X402_TESTNET no longer bypasses verification. Default OFF.
    log.info("  Testnet node hint: %s", "ON" if os.environ.get("X402_TESTNET", "0") == "1" else "OFF")
    mcp.run()


if __name__ == "__main__":
    main()

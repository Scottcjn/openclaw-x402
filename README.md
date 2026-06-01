# openclaw-x402

**The shortest path from agent demo to agent commerce.**

An MCP server where tools cost RTC to use. Claude calls a paid tool and it just works -- payment happens automatically via the [x402 protocol](https://www.x402.org/) (HTTP 402 Payment Required).

Also includes drop-in Flask middleware for adding x402 payments to any REST API.

## 5-Second Demo

```python
from openclaw_x402.mcp_server import mcp, _gate, _paid_result

@mcp.tool
def premium_export(format: str = "json", payment_token: str = "") -> str:
    """[0.1 RTC] Premium data export."""
    err = _gate(payment_token, 0.1, "premium_export", "Premium data export")
    if err:
        return err
    return _paid_result({"data": "your premium content", "format": format}, 0.1, payment_token)
```

That is a paid MCP tool. Agents that call it without paying get a 402 response with payment instructions. Agents that pay 0.1 RTC get the data.

## Install

```bash
pip install openclaw-x402
```

## How It Works

```
Agent calls tool
    |
    v
No payment token? -----> Return 402 + price + payment instructions
    |                          |
    |                     Agent signs RTC transfer to treasury
    |                          |
    |                     Agent retries with payment_token
    |
Has payment token
    |
    v
Verify on RustChain -----> Invalid? Return error + retry instructions
    |
    v
Execute tool, return result
```

## Claude Desktop / Claude Code Setup

Add to your MCP config (`~/.claude/claude_desktop_config.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "openclaw-x402": {
      "command": "python",
      "args": ["-m", "openclaw_x402"],
      "env": {
        "RUSTCHAIN_NODE": "https://50.28.86.131",
        "TREASURY_WALLET": "your-wallet-id",
        "X402_TESTNET": "0"
      }
    }
  }
}
```

Claude now has access to paid tools. It can call `list_prices` (free) to see what is available, then pay for `premium_search`, `miner_profile`, or `bcos_report`.

## Built-in Tools

| Tool | Price | Description |
|------|-------|-------------|
| `list_prices` | FREE | List all tools and prices |
| `network_status` | FREE | RustChain node health |
| `premium_search` | 0.1 RTC | Search the RustChain ledger |
| `miner_profile` | 0.05 RTC | Miner hardware fingerprint profile |
| `bcos_report` | 0.25 RTC | BCOS trust report for a GitHub repo |

## Add Your Own Paid Tools

```python
from openclaw_x402.mcp_server import mcp, _gate, _paid_result

@mcp.tool
def gpu_inference(prompt: str = "", model: str = "llama-7b", payment_token: str = "") -> str:
    """[0.5 RTC] Run GPU inference job.

    Args:
        prompt: The input prompt.
        model: Model name to use.
        payment_token: RTC payment JSON. Omit to get payment instructions.
    """
    err = _gate(payment_token, 0.5, "gpu_inference", "Run GPU inference job")
    if err:
        return err
    result = run_model(prompt, model)
    return _paid_result({"output": result}, 0.5, payment_token)

# Free tools use the normal FastMCP decorator
@mcp.tool
def check_queue() -> str:
    """[FREE] Check GPU job queue length."""
    return '{"queue_length": 3}'
```

## Payment Token Format

When an agent pays for a tool, it includes a `payment_token` argument:

```json
{
  "tx_id": "abc123...",
  "from": "agent-wallet-id",
  "amount": 0.1
}
```

The server verifies this transaction exists on RustChain (a successful on-chain lookup with matching treasury destination, sufficient amount, and a confirmed sender) before executing the tool. Verification is **always required** — `X402_TESTNET` only hints which node to use and never bypasses verification or accepts a payment on trust.

## Flask Middleware (REST APIs)

For traditional HTTP APIs (not MCP), use the Flask middleware:

```python
from flask import Flask, jsonify
from openclaw_x402 import X402Middleware

app = Flask(__name__)
x402 = X402Middleware(app, treasury="0xYourBaseAddress")

@app.route("/api/premium/data")
@x402.premium(price="10000", description="Premium data export")  # $0.01 USDC
def premium_data():
    return jsonify({"data": "your premium content"})
```

The Flask middleware uses USDC on Base chain via Coinbase facilitator. The MCP server uses RTC on RustChain. Same pattern, two payment rails.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `RUSTCHAIN_NODE` | `https://50.28.86.131` | RustChain node URL |
| `TREASURY_WALLET` | `openclaw-x402-treasury` | Wallet receiving payments |
| `X402_TESTNET` | `0` | Node hint only — does **not** bypass payment verification |
| `RC_ADMIN_KEY` | | Admin key for verified transfers |

## Why x402 + MCP

HTTP 402 has been a reserved status code since 1999. x402 gives it a real protocol. MCP gives AI agents a tool interface. Together: agents discover tools, see prices, pay, and use them -- no human in the loop.

This is the infrastructure layer for agent commerce. Every GPU cluster, every API, every dataset can become a paid tool that any agent can use.

## Links

- [x402 Protocol](https://www.x402.org/) -- HTTP 402 Payment Required standard
- [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) -- Agent tool interface
- [RustChain](https://rustchain.org) -- Proof-of-Antiquity blockchain
- [FastMCP](https://gofastmcp.com) -- Python MCP framework
- [Coinbase x402](https://docs.cdp.coinbase.com/x402/docs/welcome) -- x402 on Base chain

## License

MIT

"""
Minimal Flask app metered in wCSPR on Casper via x402.

Run:
    pip install -e ".[flask]"
    export CASPER_TREASURY=00<your 64-hex account hash>
    export CASPER_WCSPR_PACKAGE_HASH=<64-hex CEP-18 package hash>
    python examples/casper_flask_app.py

Then:
    curl -i http://localhost:5000/api/premium/weather
    # -> 402 with an accepts[] block describing the Casper payment
"""

import os

from flask import Flask, jsonify

from openclaw_x402 import CasperRailConfig, X402Middleware, cspr_to_motes

app = Flask(__name__)

x402 = X402Middleware(
    app,
    rail="casper",
    casper_config=CasperRailConfig(
        treasury=os.environ.get("CASPER_TREASURY", "00" + "ab" * 32),
        network=os.environ.get("CASPER_NETWORK", "casper:casper-test"),
        asset=os.environ.get("CASPER_WCSPR_PACKAGE_HASH", "ef" * 32),
    ),
)

# Prices are motes (9 decimals). 7.5 CSPR == "7500000000".
PRICE = cspr_to_motes("7.5")


@app.route("/api/premium/weather")
@x402.premium(price=PRICE, description="Premium weather export")
def premium_weather():
    """Paid endpoint -- unlocked once the facilitator settles the payment."""
    return jsonify({"city": "San Francisco", "weather": "foggy", "temperature": 60})


@app.route("/api/free/health")
@x402.premium(price="0", description="Health check")
def health():
    """Free endpoint -- $0 pricing passes straight through."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(port=5000)

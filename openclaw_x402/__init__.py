"""
OpenClaw x402 — Drop-in x402 payment middleware for Flask APIs.

Usage:
    from openclaw_x402 import X402Middleware

    app = Flask(__name__)
    x402 = X402Middleware(app, treasury="0xYourAddress")

    @app.route("/api/premium/data")
    @x402.premium(price="10000", description="Premium data export")
    def premium_data():
        return jsonify({"data": "..."})

Casper rail (wCSPR CEP-18, amounts in motes at 9 decimals):
    x402 = X402Middleware(app, rail="casper", treasury="00" + "ab" * 32)
"""

__version__ = "0.2.0"

from .middleware import X402Middleware
from .casper import (
    CasperPaymentError, CasperRail, CasperRailConfig,
    cspr_to_motes, motes_to_cspr,
    is_valid_account_hash, is_valid_package_hash,
)
from .config import (
    X402_NETWORK, USDC_BASE, WRTC_BASE, AERODROME_POOL,
    FACILITATOR_URL, SWAP_INFO, is_free, has_cdp_credentials,
    CASPER_MAINNET, CASPER_TESTNET, CASPER_NETWORKS, CASPER_NETWORK,
    CASPER_FACILITATOR_URL, CASPER_ASSET_DECIMALS, MOTES_PER_CSPR,
    is_casper_network, casper_chain_name,
)

__all__ = [
    "X402Middleware",
    "X402_NETWORK", "USDC_BASE", "WRTC_BASE", "AERODROME_POOL",
    "FACILITATOR_URL", "SWAP_INFO", "is_free", "has_cdp_credentials",
    "CasperPaymentError", "CasperRail", "CasperRailConfig",
    "cspr_to_motes", "motes_to_cspr",
    "is_valid_account_hash", "is_valid_package_hash",
    "CASPER_MAINNET", "CASPER_TESTNET", "CASPER_NETWORKS", "CASPER_NETWORK",
    "CASPER_FACILITATOR_URL", "CASPER_ASSET_DECIMALS", "MOTES_PER_CSPR",
    "is_casper_network", "casper_chain_name",
]

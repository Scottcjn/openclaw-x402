"""
OpenClaw x402 shared configuration.

All contract addresses are for Base mainnet (eip155:8453).
Prices are in USDC atomic units (6 decimals): 1 USDC = 1,000,000.
"""

import os
from decimal import Decimal

# --- x402 Constants ---
X402_VERSION = 2  # x402 protocol version used by the Casper rail
X402_NETWORK = "eip155:8453"  # Base mainnet (CAIP-2)
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # Native USDC on Base
WRTC_BASE = "0x5683C10596AaA09AD7F4eF13CAB94b9b74A669c6"  # wRTC on Base
AERODROME_POOL = "0x4C2A0b915279f0C22EA766D58F9B815Ded2d2A3F"  # wRTC/WETH

# --- Facilitator ---
FACILITATOR_URL = "https://x402-facilitator.cdp.coinbase.com"

# --- CDP Credentials ---
CDP_API_KEY_NAME = os.environ.get("CDP_API_KEY_NAME", "")
CDP_API_KEY_PRIVATE_KEY = os.environ.get("CDP_API_KEY_PRIVATE_KEY", "")

# --- Swap Info ---
SWAP_INFO = {
    "wrtc_contract": WRTC_BASE,
    "usdc_contract": USDC_BASE,
    "aerodrome_pool": AERODROME_POOL,
    "swap_url": f"https://aerodrome.finance/swap?from={USDC_BASE}&to={WRTC_BASE}",
    "network": "Base (eip155:8453)",
    "reference_price_usd": 0.10,
}


def is_free(price_str):
    """Check if a price is $0 (free mode)."""
    return price_str in ("0", "")


def has_cdp_credentials():
    """Check if CDP API credentials are configured."""
    return bool(CDP_API_KEY_NAME and CDP_API_KEY_PRIVATE_KEY)


# --- Casper rail (third payment rail) ---
#
# Settlement asset is wCSPR exposed as a CEP-18 token. CSPR amounts are always
# expressed in motes, the atomic unit: 1 CSPR = 1_000_000_000 motes (9 decimals).

CASPER_MAINNET = "casper:casper"
CASPER_TESTNET = "casper:casper-test"
CASPER_NETWORKS = (CASPER_MAINNET, CASPER_TESTNET)

CASPER_SCHEME = "exact"
CASPER_ASSET_DECIMALS = 9
MOTES_PER_CSPR = Decimal(10) ** CASPER_ASSET_DECIMALS  # 1_000_000_000

CASPER_NETWORK = os.environ.get("CASPER_NETWORK", CASPER_MAINNET)
CASPER_FACILITATOR_URL = os.environ.get(
    "CASPER_FACILITATOR_URL", "https://x402-facilitator.cspr.cloud"
)
CASPER_WCSPR_PACKAGE_HASH = os.environ.get("CASPER_WCSPR_PACKAGE_HASH", "")
CASPER_TREASURY = os.environ.get("CASPER_TREASURY", "")
CASPER_TOKEN_NAME = os.environ.get("CASPER_TOKEN_NAME", "wCSPR")
CASPER_TOKEN_VERSION = os.environ.get("CASPER_TOKEN_VERSION", "1")
CASPER_MAX_TIMEOUT_SECONDS = int(os.environ.get("CASPER_MAX_TIMEOUT_SECONDS", "900"))


def is_casper_network(network):
    """Check if a CAIP-2 network id is a supported Casper network."""
    return network in CASPER_NETWORKS


def casper_chain_name(network):
    """Return the Casper chain name (``casper``/``casper-test``) for a CAIP-2 id."""
    if not is_casper_network(network):
        raise ValueError(f"Unsupported Casper network: {network!r}")
    return network.split(":", 1)[1]


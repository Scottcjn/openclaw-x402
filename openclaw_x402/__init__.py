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
"""

from typing import TYPE_CHECKING

__version__ = "0.2.0"

from .config import (
    X402_NETWORK, USDC_BASE, WRTC_BASE, AERODROME_POOL,
    FACILITATOR_URL, SWAP_INFO, is_free, has_cdp_credentials,
)

if TYPE_CHECKING:  # pragma: no cover - type-checker only
    from .middleware import X402Middleware


def __getattr__(name):
    """Lazily expose Flask-only middleware without breaking MCP-only installs."""
    if name != "X402Middleware":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    try:
        from .middleware import X402Middleware
    except ModuleNotFoundError as exc:
        if exc.name == "flask":
            raise ImportError(
                "X402Middleware requires Flask. Install the optional extra with "
                "`pip install openclaw-x402[flask]` or install Flask directly."
            ) from exc
        raise
    return X402Middleware


__all__ = [
    "X402Middleware",
    "X402_NETWORK", "USDC_BASE", "WRTC_BASE", "AERODROME_POOL",
    "FACILITATOR_URL", "SWAP_INFO", "is_free", "has_cdp_credentials",
]

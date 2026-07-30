"""
OpenClaw x402 Flask Middleware.

Drop-in x402 payment enforcement for any Flask API.
Supports free mode ($0 pricing), real USDC payments via Coinbase facilitator,
real wCSPR payments on Casper via an x402 v2 facilitator, and graceful
degradation when x402 libraries are not installed.

Usage:
    from openclaw_x402 import X402Middleware

    x402 = X402Middleware(app, treasury="0xYourAddress")

    @app.route("/api/premium/data")
    @x402.premium(price="10000", description="Premium data export")
    def premium_data():
        return jsonify({"data": "..."})

    # Casper rail (amounts in motes, 9 decimals):
    x402 = X402Middleware(app, rail="casper", treasury="00" + "ab" * 32)

    @app.route("/api/premium/casper")
    @x402.premium(price="7500000000", description="Premium data export")
    def premium_casper():
        return jsonify({"data": "..."})
"""

import functools
import logging
import time

from flask import jsonify, request

from .casper import CasperPaymentError, CasperRail, CasperRailConfig
from .config import (
    X402_NETWORK, USDC_BASE, FACILITATOR_URL, SWAP_INFO,
    is_free, has_cdp_credentials,
)

log = logging.getLogger("openclaw_x402")

# Try importing x402 Flask helpers (optional dependency)
try:
    from x402.flask import x402_middleware as _x402_mw
    X402_LIB_AVAILABLE = True
except ImportError:
    X402_LIB_AVAILABLE = False
    log.info("x402 Flask library not installed — running in manual mode")


class X402Middleware:
    """
    x402 payment middleware for Flask.

    Args:
        app: Flask application (or None, call init_app later)
        treasury: Address receiving payments. A Base chain address for the
            default ``base`` rail, or a Casper account hash for ``casper``.
        db_func: Optional callable returning a DB connection (for payment logging)
        rail: Payment rail, ``"base"`` (USDC on Base) or ``"casper"``
            (wCSPR CEP-18 on Casper).
        casper_config: Optional :class:`~openclaw_x402.casper.CasperRailConfig`
            overriding network, facilitator URL, asset and token metadata.
    """

    RAILS = ("base", "casper")

    def __init__(self, app=None, treasury="", db_func=None, rail="base",
                 casper_config=None):
        if rail not in self.RAILS:
            raise ValueError(f"Unsupported rail {rail!r}. Expected one of {self.RAILS}.")
        self.treasury = treasury
        self.db_func = db_func
        self.rail = rail
        self._payment_table_created = False
        self.casper = None
        if rail == "casper":
            config = casper_config or CasperRailConfig(treasury=treasury)
            if treasury and not config.treasury:
                config.treasury = treasury
            self.casper = CasperRail(config)
            self.treasury = self.casper.config.treasury or treasury
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """Register x402 routes and middleware on the Flask app."""
        self.app = app
        self._ensure_payment_table()
        self._register_routes(app)
        log.info(
            "OpenClaw x402 initialized: treasury=%s, x402_lib=%s",
            self.treasury[:10] + "..." if self.treasury else "NOT SET",
            X402_LIB_AVAILABLE,
        )

    def _ensure_payment_table(self):
        """Create x402_payments table if DB function is provided."""
        if not self.db_func or self._payment_table_created:
            return
        try:
            db = self.db_func()
            db.execute("""
                CREATE TABLE IF NOT EXISTS x402_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payer_address TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    amount_usdc TEXT NOT NULL,
                    tx_hash TEXT,
                    network TEXT DEFAULT 'eip155:8453',
                    description TEXT,
                    created_at REAL NOT NULL
                )
            """)
            db.commit()
            self._payment_table_created = True
        except Exception as e:
            log.warning("Failed to create x402_payments table: %s", e)

    def _register_routes(self, app):
        """Register x402 status endpoint."""

        @app.route("/api/x402/status")
        def x402_status():
            payload = {
                "x402_enabled": True,
                "x402_lib": X402_LIB_AVAILABLE,
                "cdp_configured": has_cdp_credentials(),
                "network": X402_NETWORK,
                "facilitator": FACILITATOR_URL,
                "treasury": self.treasury,
                "swap_info": SWAP_INFO,
                "rail": self.rail,
            }
            if self.casper is not None:
                casper_config = self.casper.config.to_dict()
                payload["casper"] = casper_config
                payload["network"] = casper_config["network"]
                payload["facilitator"] = casper_config["facilitator"]
            return jsonify(payload)

    def premium(self, price="0", description="Premium endpoint"):
        """
        Decorator to enforce x402 payment on a route.

        If price is "0", requests pass through freely (proving the flow).
        If price is non-zero:
          - With x402 lib: uses Coinbase facilitator for verification
          - Without x402 lib: returns 402 with manual payment instructions

        Args:
            price: USDC atomic units (6 decimals). "10000" = $0.01
            description: Human-readable endpoint description
        """
        def decorator(f):
            @functools.wraps(f)
            def wrapper(*args, **kwargs):
                # Free mode — pass through
                if is_free(price):
                    return f(*args, **kwargs)

                # Check for x402 payment header
                payment_header = request.headers.get("X-PAYMENT", "").strip()

                # Casper rail: real facilitator verify + settle. Any failure
                # falls through to a fresh 402 (fail closed, same as Base).
                if self.casper is not None:
                    if not payment_header:
                        return self._payment_required(price, description)
                    return self._casper_paid_request(
                        f, args, kwargs, payment_header, price, description
                    )

                # SECURITY (fail closed): the facilitator verification path is
                # not actually wired here — the imported x402 middleware is never
                # invoked — so an X-PAYMENT header must NEVER be trusted on its
                # own. Previously, with the x402 lib installed, ANY non-empty
                # X-PAYMENT header was logged as "x402-verified" and granted free
                # access. Until real on-chain/facilitator settlement verification
                # is implemented, every unverified request gets a 402.
                if payment_header:
                    log.warning(
                        "Rejected unverified X-PAYMENT header for %s "
                        "(facilitator verification not implemented; failing closed)",
                        request.path,
                    )
                return self._payment_required(price, description)

            return wrapper
        return decorator

    def _casper_paid_request(self, view, args, kwargs, payment_header, price,
                             description):
        """Verify + settle a Casper payment, then run the protected view."""
        requirements = self.casper.payment_requirements(price, description, request.url)
        try:
            payload, settlement = self.casper.verify_and_settle(
                payment_header, requirements
            )
        except CasperPaymentError as e:
            log.warning(
                "Casper payment rejected for %s: %s (%s)",
                request.path, e.reason, e.message,
            )
            return self._payment_required(
                price, description, reason=e.reason, message=e.message
            )

        authorization = payload.get("payload", {}).get("authorization", {})
        payer = settlement.get("payer") or authorization.get("from", "")
        self._log_payment(
            payer=payer,
            endpoint=request.path,
            amount=str(price),
            tx_hash=settlement.get("transaction", ""),
            description=description,
        )

        response = self.app.make_response(view(*args, **kwargs))
        response.headers["X-PAYMENT-RESPONSE"] = self.casper.settlement_receipt(
            settlement
        )
        return response

    def _payment_required(self, price, description, reason="", message=""):
        """Return HTTP 402 with x402 payment instructions."""
        if self.casper is not None:
            body = self.casper.challenge(price, description, request.url)
            if reason:
                body["reason"] = reason
                body["message"] = message
            return jsonify(body), 402
        return jsonify({
            "error": "Payment Required",
            "x402": {
                "version": "1",
                "network": X402_NETWORK,
                "asset": USDC_BASE,
                "payTo": self.treasury,
                "maxAmountRequired": price,
                "facilitator": FACILITATOR_URL,
                "resource": request.url,
                "description": description,
            },
        }), 402

    def _log_payment(self, payer, endpoint, amount, tx_hash, description):
        """Log a payment to the database."""
        if not self.db_func:
            return
        try:
            db = self.db_func()
            db.execute(
                "INSERT INTO x402_payments (payer_address, endpoint, amount_usdc, tx_hash, description, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (payer, endpoint, amount, tx_hash, description, time.time()),
            )
            db.commit()
        except Exception as e:
            log.warning("Failed to log x402 payment: %s", e)

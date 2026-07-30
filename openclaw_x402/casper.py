"""
Casper payment rail for OpenClaw x402.

Third payment rail alongside USDC-on-Base (Flask middleware) and RTC-on-RustChain
(MCP server). Settlement happens in a CEP-18 token (wCSPR by default) on the
Casper network, brokered by an x402 v2 facilitator that exposes ``POST /verify``
and ``POST /settle``.

Amounts are always handled as **motes** -- the atomic unit of CSPR, 9 decimals::

    1 CSPR = 1_000_000_000 motes

Everything network-facing is configurable through :class:`CasperRailConfig`
(or the matching environment variables in ``openclaw_x402.config``), with the
public cspr.cloud facilitator as the default.

Usage::

    from openclaw_x402 import X402Middleware

    app = Flask(__name__)
    x402 = X402Middleware(
        app,
        rail="casper",
        treasury="00" + "ab" * 32,           # Casper account hash
    )

    @app.route("/api/premium/data")
    @x402.premium(price="7500000000", description="Premium data export")  # 7.5 CSPR
    def premium_data():
        return jsonify({"data": "..."})
"""

import base64
import binascii
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

import httpx

from .config import (
    CASPER_ASSET_DECIMALS,
    CASPER_FACILITATOR_URL,
    CASPER_MAX_TIMEOUT_SECONDS,
    CASPER_NETWORK,
    CASPER_NETWORKS,
    CASPER_SCHEME,
    CASPER_TOKEN_NAME,
    CASPER_TOKEN_VERSION,
    CASPER_TREASURY,
    CASPER_WCSPR_PACKAGE_HASH,
    MOTES_PER_CSPR,
    X402_VERSION,
)

log = logging.getLogger("openclaw_x402.casper")

_ACCOUNT_HASH_RE = re.compile(r"^(00|01)[0-9a-fA-F]{64}$")
_PACKAGE_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class CasperPaymentError(Exception):
    """Raised when a Casper payment cannot be verified or settled."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


def is_valid_account_hash(value: str) -> bool:
    """Return True if ``value`` is a Casper account hash (``00``/``01`` + 64 hex)."""
    return bool(value) and bool(_ACCOUNT_HASH_RE.match(value))


def is_valid_package_hash(value: str) -> bool:
    """Return True if ``value`` is a 64-char hex CEP-18 contract package hash."""
    return bool(value) and bool(_PACKAGE_HASH_RE.match(value))


def cspr_to_motes(amount: Any) -> str:
    """
    Convert a CSPR amount to a motes string (9 decimals, no exponent).

    ``cspr_to_motes("7.5") == "7500000000"``

    Raises:
        ValueError: if the amount is not a finite number or has sub-mote precision.
    """
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid CSPR amount: {amount!r}") from exc
    if not value.is_finite() or value < 0:
        raise ValueError(f"Invalid CSPR amount: {amount!r}")
    motes = value * MOTES_PER_CSPR
    if motes != motes.to_integral_value():
        raise ValueError(f"CSPR amount {amount!r} is finer than one mote")
    return str(int(motes))


def motes_to_cspr(motes: Any) -> str:
    """
    Convert a motes amount to a human-readable CSPR string.

    ``motes_to_cspr("7500000000") == "7.5"``
    """
    try:
        value = Decimal(str(motes))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid mote amount: {motes!r}") from exc
    if value != value.to_integral_value():
        raise ValueError(f"Mote amounts must be whole numbers, got {motes!r}")
    cspr = (value / MOTES_PER_CSPR).normalize()
    return format(cspr, "f")


class CasperRailConfig:
    """
    Configuration for the Casper payment rail.

    Args:
        treasury: Casper account hash receiving payments (``00`` + 64 hex).
        network: CAIP-2 network id, ``casper:casper`` or ``casper:casper-test``.
        facilitator_url: Base URL of an x402 v2 facilitator for Casper.
        asset: CEP-18 contract package hash of the settlement token (wCSPR).
        asset_decimals: Decimals of the settlement token (wCSPR uses 9).
        token_name: CEP-18 token name seeding the EIP-712 domain.
        token_version: CEP-18 token version seeding the EIP-712 domain.
        max_timeout_seconds: Validity window advertised in the 402 challenge.
        timeout: HTTP timeout (seconds) for facilitator calls.
    """

    def __init__(
        self,
        treasury: str = "",
        network: str = CASPER_NETWORK,
        facilitator_url: str = CASPER_FACILITATOR_URL,
        asset: str = CASPER_WCSPR_PACKAGE_HASH,
        asset_decimals: int = CASPER_ASSET_DECIMALS,
        token_name: str = CASPER_TOKEN_NAME,
        token_version: str = CASPER_TOKEN_VERSION,
        max_timeout_seconds: int = CASPER_MAX_TIMEOUT_SECONDS,
        timeout: float = 10.0,
    ) -> None:
        if network not in CASPER_NETWORKS:
            raise ValueError(
                f"Unsupported Casper network {network!r}. "
                f"Expected one of {sorted(CASPER_NETWORKS)}."
            )
        self.treasury = treasury or CASPER_TREASURY
        self.network = network
        self.facilitator_url = facilitator_url.rstrip("/")
        self.asset = asset
        self.asset_decimals = int(asset_decimals)
        self.token_name = token_name
        self.token_version = token_version
        self.max_timeout_seconds = int(max_timeout_seconds)
        self.timeout = timeout

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the rail config for the ``/api/x402/status`` endpoint."""
        return {
            "rail": "casper",
            "network": self.network,
            "facilitator": self.facilitator_url,
            "asset": self.asset,
            "asset_decimals": self.asset_decimals,
            "treasury": self.treasury,
            "scheme": CASPER_SCHEME,
            "motes_per_cspr": int(MOTES_PER_CSPR),
        }


class CasperRail:
    """
    Casper rail: builds 402 challenges and talks to the x402 facilitator.

    The rail is deliberately stateless -- it owns no keys and never signs. The
    payer signs the ``TransferWithAuthorization`` payload client-side and the
    facilitator pays gas to submit the CEP-18 ``transfer_with_authorization``
    deploy on settlement.
    """

    def __init__(self, config: Optional[CasperRailConfig] = None) -> None:
        self.config = config or CasperRailConfig()

    # --- 402 challenge ---------------------------------------------------

    def payment_requirements(
        self,
        price: str,
        description: str,
        resource: str,
    ) -> Dict[str, Any]:
        """
        Build an x402 v2 ``PaymentRequirements`` object for this rail.

        Args:
            price: Amount in motes (9 decimals) as a decimal string.
            description: Human-readable endpoint description.
            resource: Absolute URL of the protected resource.
        """
        return {
            "scheme": CASPER_SCHEME,
            "network": self.config.network,
            "payTo": self.config.treasury,
            "amount": str(price),
            "maxAmountRequired": str(price),
            "asset": self.config.asset,
            "resource": resource,
            "description": description,
            "mimeType": "application/json",
            "maxTimeoutSeconds": self.config.max_timeout_seconds,
            "extra": {
                "name": self.config.token_name,
                "version": self.config.token_version,
                "decimals": str(self.config.asset_decimals),
            },
        }

    def challenge(self, price: str, description: str, resource: str) -> Dict[str, Any]:
        """Build the full 402 response body (``accepts`` list, x402 v2 shape)."""
        return {
            "x402Version": X402_VERSION,
            "error": "Payment Required",
            "accepts": [self.payment_requirements(price, description, resource)],
        }

    # --- payload handling ------------------------------------------------

    @staticmethod
    def decode_payment_header(header: str) -> Dict[str, Any]:
        """
        Decode an ``X-PAYMENT`` header into an x402 ``PaymentPayload`` dict.

        The header is base64-encoded JSON per the x402 spec; raw JSON is also
        accepted for easier local testing.

        Raises:
            CasperPaymentError: with reason ``malformed_payload``.
        """
        raw = (header or "").strip()
        if not raw:
            raise CasperPaymentError("malformed_payload", "Empty X-PAYMENT header")
        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            decoded = raw
        try:
            payload = json.loads(decoded)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CasperPaymentError(
                "malformed_payload", "X-PAYMENT is not valid base64 JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise CasperPaymentError(
                "malformed_payload", "X-PAYMENT must decode to a JSON object"
            )
        return payload

    def _check_payload_shape(
        self, payload: Dict[str, Any], requirements: Dict[str, Any]
    ) -> None:
        """Cheap local pre-checks so obviously bad payloads never reach the wire."""
        if payload.get("scheme", CASPER_SCHEME) != CASPER_SCHEME:
            raise CasperPaymentError(
                "unsupported_scheme", f"Expected scheme {CASPER_SCHEME!r}"
            )
        if payload.get("network") and payload["network"] != requirements["network"]:
            raise CasperPaymentError(
                "network_mismatch",
                f"Payload network {payload['network']!r} != {requirements['network']!r}",
            )
        inner = payload.get("payload")
        if not isinstance(inner, dict):
            raise CasperPaymentError("malformed_payload", "Missing payload object")
        authorization = inner.get("authorization")
        if not isinstance(authorization, dict):
            raise CasperPaymentError("malformed_payload", "Missing authorization object")
        for field in ("from", "to", "value", "validAfter", "validBefore", "nonce"):
            if not authorization.get(field):
                raise CasperPaymentError(
                    "malformed_payload", f"Missing authorization field {field!r}"
                )
        if not is_valid_account_hash(authorization["to"]):
            raise CasperPaymentError(
                "invalid_pay_to", "authorization.to is not a Casper account hash"
            )
        if authorization["to"] != requirements["payTo"]:
            raise CasperPaymentError(
                "pay_to_mismatch", "authorization.to does not match payTo"
            )
        if str(authorization["value"]) != str(requirements["amount"]):
            raise CasperPaymentError(
                "amount_mismatch",
                f"Expected {requirements['amount']} motes, got {authorization['value']}",
            )

    # --- facilitator calls -----------------------------------------------

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.config.facilitator_url}{path}"
        try:
            response = httpx.post(url, json=body, timeout=self.config.timeout)
        except Exception as exc:  # network error -> fail closed
            raise CasperPaymentError(
                "facilitator_unreachable", f"Facilitator {url} unreachable: {exc}"
            ) from exc
        if response.status_code != 200:
            raise CasperPaymentError(
                "facilitator_error",
                f"Facilitator {url} returned HTTP {response.status_code}",
            )
        try:
            data = response.json()
        except Exception as exc:
            raise CasperPaymentError(
                "facilitator_error", f"Facilitator {url} returned non-JSON body"
            ) from exc
        if not isinstance(data, dict):
            raise CasperPaymentError(
                "facilitator_error", f"Facilitator {url} returned non-object body"
            )
        return data

    def verify(
        self, payload: Dict[str, Any], requirements: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Verify a payment payload with the facilitator (``POST /verify``).

        Returns the facilitator response dict on success.

        Raises:
            CasperPaymentError: if the payload is malformed or rejected.
        """
        self._check_payload_shape(payload, requirements)
        body = {
            "x402Version": X402_VERSION,
            "paymentPayload": self._normalized_payload(payload),
            "paymentRequirements": requirements,
        }
        data = self._post("/verify", body)
        if not data.get("isValid"):
            raise CasperPaymentError(
                data.get("invalidReason", "invalid_payment"),
                data.get("invalidMessage", "Facilitator rejected the payment"),
            )
        return data

    def settle(
        self, payload: Dict[str, Any], requirements: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Settle a verified payment on-chain (``POST /settle``).

        Returns the facilitator response dict on success (contains
        ``transaction`` -- the Casper deploy hash -- and ``payer``).

        Raises:
            CasperPaymentError: if settlement fails for any reason.
        """
        body = {
            "x402Version": X402_VERSION,
            "paymentPayload": self._normalized_payload(payload),
            "paymentRequirements": requirements,
        }
        data = self._post("/settle", body)
        if not data.get("success"):
            raise CasperPaymentError(
                data.get("errorReason", "settlement_failed"),
                data.get("errorMessage", "Facilitator could not settle the payment"),
            )
        return data

    def verify_and_settle(
        self, payment_header: str, requirements: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Full happy path: decode header, verify, then settle.

        Returns:
            ``(payload, settle_response)``

        Raises:
            CasperPaymentError: on any failure -- callers must fail closed.
        """
        payload = self.decode_payment_header(payment_header)
        self.verify(payload, requirements)
        return payload, self.settle(payload, requirements)

    def _normalized_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Fill in x402 envelope defaults the client may have omitted."""
        normalized = dict(payload)
        normalized.setdefault("x402Version", X402_VERSION)
        normalized.setdefault("scheme", CASPER_SCHEME)
        normalized.setdefault("network", self.config.network)
        return normalized

    @staticmethod
    def settlement_receipt(settle_response: Dict[str, Any]) -> str:
        """Base64-encode a settle response for the ``X-PAYMENT-RESPONSE`` header."""
        return base64.b64encode(
            json.dumps(settle_response, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

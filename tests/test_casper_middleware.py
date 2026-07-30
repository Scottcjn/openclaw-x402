import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask, jsonify

from openclaw_x402 import config
from openclaw_x402.casper import CasperRailConfig
from openclaw_x402.middleware import X402Middleware
import openclaw_x402.casper as casper_module

TREASURY = "00" + "ab" * 32
PAYER = "00" + "cd" * 32
ASSET = "ef" * 32
PRICE_MOTES = "7500000000"  # 7.5 CSPR
DEPLOY_HASH = "dd" * 32


def casper_config(**overrides):
    kwargs = {
        "treasury": TREASURY,
        "asset": ASSET,
        "facilitator_url": "https://facilitator.example",
    }
    kwargs.update(overrides)
    return CasperRailConfig(**kwargs)


def payment_header():
    payload = {
        "x402Version": 2,
        "scheme": "exact",
        "network": config.CASPER_MAINNET,
        "payload": {
            "signature": "aa" * 65,
            "publicKey": "01" + "bb" * 32,
            "authorization": {
                "from": PAYER,
                "to": TREASURY,
                "value": PRICE_MOTES,
                "validAfter": "1710000000",
                "validBefore": "1710000900",
                "nonce": "cc" * 32,
            },
        },
    }
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


VERIFY_OK = FakeResponse({"isValid": True, "payer": PAYER})
SETTLE_OK = FakeResponse(
    {
        "success": True,
        "transaction": DEPLOY_HASH,
        "network": config.CASPER_MAINNET,
        "payer": PAYER,
    }
)


def create_casper_app(db_func=None, **config_overrides):
    app = Flask(__name__)
    x402 = X402Middleware(
        app,
        rail="casper",
        treasury=TREASURY,
        db_func=db_func,
        casper_config=casper_config(**config_overrides),
    )

    @app.route("/premium")
    @x402.premium(price=PRICE_MOTES, description="Premium export")
    def premium_endpoint():
        return jsonify({"ok": True})

    @app.route("/free")
    @x402.premium(price="0", description="Free endpoint")
    def free_endpoint():
        return jsonify({"ok": True})

    return app


class CasperRailSelectionTests(unittest.TestCase):
    def test_default_rail_is_base_and_has_no_casper_rail(self):
        middleware = X402Middleware(treasury="0xabc")
        self.assertEqual(middleware.rail, "base")
        self.assertIsNone(middleware.casper)

    def test_unknown_rail_is_rejected(self):
        with self.assertRaises(ValueError):
            X402Middleware(treasury="0xabc", rail="dogecoin")

    def test_casper_rail_defaults_to_cspr_cloud_facilitator(self):
        middleware = X402Middleware(rail="casper", treasury=TREASURY)
        self.assertEqual(
            middleware.casper.config.facilitator_url,
            "https://x402-facilitator.cspr.cloud",
        )
        self.assertEqual(middleware.casper.config.network, "casper:casper")

    def test_status_endpoint_reports_casper_rail(self):
        app = create_casper_app()
        payload = app.test_client().get("/api/x402/status").get_json()

        self.assertEqual(payload["rail"], "casper")
        self.assertEqual(payload["network"], "casper:casper")
        self.assertEqual(payload["facilitator"], "https://facilitator.example")
        self.assertEqual(payload["casper"]["asset"], ASSET)
        self.assertEqual(payload["casper"]["asset_decimals"], 9)
        self.assertEqual(payload["casper"]["motes_per_cspr"], 1_000_000_000)
        self.assertEqual(payload["casper"]["treasury"], TREASURY)

    def test_base_rail_status_still_reports_base_network(self):
        app = Flask(__name__)
        X402Middleware(app, treasury="0xabc")
        payload = app.test_client().get("/api/x402/status").get_json()

        self.assertEqual(payload["rail"], "base")
        self.assertEqual(payload["network"], config.X402_NETWORK)
        self.assertNotIn("casper", payload)


class CasperChallengeTests(unittest.TestCase):
    def setUp(self):
        self.client = create_casper_app().test_client()

    def test_unpaid_request_returns_402_with_casper_accepts(self):
        response = self.client.get("/premium", base_url="https://api.example.test")

        self.assertEqual(response.status_code, 402)
        body = response.get_json()
        self.assertEqual(body["x402Version"], 2)
        accepts = body["accepts"][0]
        self.assertEqual(accepts["scheme"], "exact")
        self.assertEqual(accepts["network"], "casper:casper")
        self.assertEqual(accepts["payTo"], TREASURY)
        self.assertEqual(accepts["amount"], PRICE_MOTES)
        self.assertEqual(accepts["asset"], ASSET)
        self.assertEqual(accepts["extra"]["decimals"], "9")
        self.assertEqual(accepts["resource"], "https://api.example.test/premium")

    def test_free_route_is_unaffected_by_the_casper_rail(self):
        response = self.client.get("/free")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})

    def test_testnet_challenge_advertises_testnet_network(self):
        client = create_casper_app(network=config.CASPER_TESTNET).test_client()
        body = client.get("/premium").get_json()
        self.assertEqual(body["accepts"][0]["network"], "casper:casper-test")


class CasperPaidRequestTests(unittest.TestCase):
    def test_verified_and_settled_payment_unlocks_the_route(self):
        client = create_casper_app().test_client()
        with mock.patch.object(
            casper_module.httpx, "post", side_effect=[VERIFY_OK, SETTLE_OK]
        ) as post:
            response = client.get("/premium", headers={"X-PAYMENT": payment_header()})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        self.assertEqual(post.call_count, 2)

        receipt = json.loads(
            base64.b64decode(response.headers["X-PAYMENT-RESPONSE"]).decode("utf-8")
        )
        self.assertTrue(receipt["success"])
        self.assertEqual(receipt["transaction"], DEPLOY_HASH)

    def test_fake_payment_header_still_returns_402(self):
        client = create_casper_app().test_client()
        with mock.patch.object(casper_module.httpx, "post") as post:
            response = client.get("/premium", headers={"X-PAYMENT": "totally-fake"})

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.get_json()["reason"], "malformed_payload")
        post.assert_not_called()

    def test_facilitator_rejection_returns_402_with_reason(self):
        client = create_casper_app().test_client()
        rejection = FakeResponse(
            {
                "isValid": False,
                "invalidReason": "invalid_signature",
                "invalidMessage": "signature does not verify",
            }
        )
        with mock.patch.object(casper_module.httpx, "post", return_value=rejection):
            response = client.get("/premium", headers={"X-PAYMENT": payment_header()})

        self.assertEqual(response.status_code, 402)
        body = response.get_json()
        self.assertEqual(body["reason"], "invalid_signature")
        self.assertEqual(body["message"], "signature does not verify")

    def test_settlement_failure_fails_closed_with_402(self):
        client = create_casper_app().test_client()
        settle_failure = FakeResponse(
            {
                "success": False,
                "errorReason": "put_deploy_failed",
                "errorMessage": "node rejected deploy",
            }
        )
        with mock.patch.object(
            casper_module.httpx, "post", side_effect=[VERIFY_OK, settle_failure]
        ):
            response = client.get("/premium", headers={"X-PAYMENT": payment_header()})

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.get_json()["reason"], "put_deploy_failed")

    def test_unreachable_facilitator_fails_closed_with_402(self):
        client = create_casper_app().test_client()
        with mock.patch.object(
            casper_module.httpx, "post", side_effect=OSError("connection refused")
        ):
            response = client.get("/premium", headers={"X-PAYMENT": payment_header()})

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.get_json()["reason"], "facilitator_unreachable")


class CasperPaymentLoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "payments.db"

    def tearDown(self):
        self.tmp.cleanup()

    def db_func(self):
        return sqlite3.connect(self.db_path)

    def test_settled_payment_is_logged_with_deploy_hash_and_motes(self):
        client = create_casper_app(db_func=self.db_func).test_client()
        with mock.patch.object(
            casper_module.httpx, "post", side_effect=[VERIFY_OK, SETTLE_OK]
        ):
            response = client.get("/premium", headers={"X-PAYMENT": payment_header()})

        self.assertEqual(response.status_code, 200)
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT payer_address, endpoint, amount_usdc, tx_hash, description "
                "FROM x402_payments"
            ).fetchone()

        self.assertEqual(
            row, (PAYER, "/premium", PRICE_MOTES, DEPLOY_HASH, "Premium export")
        )

    def test_rejected_payment_is_not_logged(self):
        client = create_casper_app(db_func=self.db_func).test_client()
        with mock.patch.object(
            casper_module.httpx,
            "post",
            return_value=FakeResponse({"isValid": False, "invalidReason": "expired"}),
        ):
            client.get("/premium", headers={"X-PAYMENT": payment_header()})

        with sqlite3.connect(self.db_path) as db:
            count = db.execute("SELECT COUNT(*) FROM x402_payments").fetchone()[0]

        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

from openclaw_x402 import config
from openclaw_x402.middleware import X402Middleware


class ConfigHelperTests(unittest.TestCase):
    def test_is_free_only_accepts_zero_or_empty_price(self):
        self.assertTrue(config.is_free("0"))
        self.assertTrue(config.is_free(""))
        self.assertFalse(config.is_free("0.00"))
        self.assertFalse(config.is_free("1000"))

    def test_has_cdp_credentials_requires_both_values(self):
        with mock.patch.object(config, "CDP_API_KEY_NAME", ""), mock.patch.object(
            config, "CDP_API_KEY_PRIVATE_KEY", "secret"
        ):
            self.assertFalse(config.has_cdp_credentials())

        with mock.patch.object(
            config, "CDP_API_KEY_NAME", "key-name"
        ), mock.patch.object(config, "CDP_API_KEY_PRIVATE_KEY", "secret"):
            self.assertTrue(config.has_cdp_credentials())


class MiddlewareHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "payments.db"

    def tearDown(self):
        self.tmp.cleanup()

    def db_func(self):
        return sqlite3.connect(self.db_path)

    def test_init_app_creates_payment_table_when_db_func_is_configured(self):
        app = Flask(__name__)
        middleware = X402Middleware(app, treasury="0xabc", db_func=self.db_func)

        self.assertTrue(middleware._payment_table_created)
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("x402_payments",),
            ).fetchone()

        self.assertEqual(row, ("x402_payments",))

    def test_log_payment_persists_expected_fields(self):
        app = Flask(__name__)
        middleware = X402Middleware(app, treasury="0xabc", db_func=self.db_func)

        middleware._log_payment(
            payer="0xpayer",
            endpoint="/premium",
            amount="1000",
            tx_hash="0x" + ("11" * 32),
            description="Premium export",
        )

        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                """
                SELECT payer_address, endpoint, amount_usdc, tx_hash, network, description
                FROM x402_payments
                """
            ).fetchone()

        self.assertEqual(
            row,
            (
                "0xpayer",
                "/premium",
                "1000",
                "0x" + ("11" * 32),
                "eip155:8453",
                "Premium export",
            ),
        )

    def test_payment_required_response_includes_current_resource_and_treasury(self):
        app = Flask(__name__)
        middleware = X402Middleware(treasury="0xabc")

        with app.test_request_context(
            "/premium?format=json",
            base_url="https://api.example.test",
        ):
            response, status = middleware._payment_required("1000", "Premium export")

        payload = response.get_json()
        self.assertEqual(status, 402)
        self.assertEqual(payload["error"], "Payment Required")
        self.assertEqual(payload["x402"]["payTo"], "0xabc")
        self.assertEqual(payload["x402"]["maxAmountRequired"], "1000")
        self.assertEqual(payload["x402"]["resource"], "https://api.example.test/premium?format=json")
        self.assertEqual(payload["x402"]["description"], "Premium export")

    def test_status_endpoint_reports_runtime_configuration(self):
        app = Flask(__name__)
        X402Middleware(app, treasury="0xabc")

        response = app.test_client().get("/api/x402/status")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["x402_enabled"])
        self.assertEqual(payload["network"], config.X402_NETWORK)
        self.assertEqual(payload["facilitator"], config.FACILITATOR_URL)
        self.assertEqual(payload["treasury"], "0xabc")
        self.assertEqual(payload["swap_info"], config.SWAP_INFO)

    def test_payment_table_setup_failure_leaves_table_marked_uncreated(self):
        class BrokenDB:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("cannot create table")

            def commit(self):
                raise AssertionError("commit should not run after execute failure")

        middleware = X402Middleware(db_func=lambda: BrokenDB())

        middleware._ensure_payment_table()

        self.assertFalse(middleware._payment_table_created)


if __name__ == "__main__":
    unittest.main()

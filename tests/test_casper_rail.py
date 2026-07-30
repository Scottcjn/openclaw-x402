import base64
import json
import unittest
from unittest import mock

from openclaw_x402 import config
from openclaw_x402.casper import (
    CasperPaymentError,
    CasperRail,
    CasperRailConfig,
    cspr_to_motes,
    is_valid_account_hash,
    is_valid_package_hash,
    motes_to_cspr,
)
import openclaw_x402.casper as casper_module

TREASURY = "00" + "ab" * 32
PAYER = "00" + "cd" * 32
ASSET = "ef" * 32
PRICE_MOTES = "7500000000"  # 7.5 CSPR


def build_payload(value=PRICE_MOTES, to=TREASURY, network=config.CASPER_MAINNET):
    return {
        "x402Version": 2,
        "scheme": "exact",
        "network": network,
        "payload": {
            "signature": "aa" * 65,
            "publicKey": "01" + "bb" * 32,
            "authorization": {
                "from": PAYER,
                "to": to,
                "value": value,
                "validAfter": "1710000000",
                "validBefore": "1710000900",
                "nonce": "cc" * 32,
            },
        },
    }


def encode_header(payload):
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


class FakeResponse:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def make_rail(**overrides):
    kwargs = {
        "treasury": TREASURY,
        "asset": ASSET,
        "facilitator_url": "https://x402-facilitator.cspr.cloud",
    }
    kwargs.update(overrides)
    return CasperRail(CasperRailConfig(**kwargs))


class MoteConversionTests(unittest.TestCase):
    def test_one_cspr_is_one_billion_motes(self):
        self.assertEqual(cspr_to_motes(1), "1000000000")
        self.assertEqual(int(config.MOTES_PER_CSPR), 1_000_000_000)

    def test_fractional_cspr_converts_without_float_error(self):
        self.assertEqual(cspr_to_motes("7.5"), "7500000000")
        self.assertEqual(cspr_to_motes("0.000000001"), "1")

    def test_round_trip_preserves_value(self):
        for cspr in ("0.1", "1", "7.5", "12345.678901234"):
            with self.subTest(cspr=cspr):
                self.assertEqual(motes_to_cspr(cspr_to_motes(cspr)), cspr)

    def test_sub_mote_precision_is_rejected(self):
        with self.assertRaises(ValueError):
            cspr_to_motes("0.0000000001")

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            cspr_to_motes("-1")

    def test_fractional_motes_are_rejected(self):
        with self.assertRaises(ValueError):
            motes_to_cspr("1.5")


class AddressValidationTests(unittest.TestCase):
    def test_account_hash_must_be_prefixed_and_64_hex(self):
        self.assertTrue(is_valid_account_hash(TREASURY))
        self.assertTrue(is_valid_account_hash("01" + "ff" * 32))
        self.assertFalse(is_valid_account_hash(""))
        self.assertFalse(is_valid_account_hash("02" + "ab" * 32))
        self.assertFalse(is_valid_account_hash("00" + "ab" * 31))
        self.assertFalse(is_valid_account_hash("0x" + "ab" * 32))

    def test_package_hash_is_bare_64_hex(self):
        self.assertTrue(is_valid_package_hash(ASSET))
        self.assertFalse(is_valid_package_hash("00" + ASSET))
        self.assertFalse(is_valid_package_hash("zz" * 32))


class RailConfigTests(unittest.TestCase):
    def test_defaults_point_at_cspr_cloud_facilitator_and_mainnet(self):
        rail_config = CasperRailConfig(treasury=TREASURY)
        self.assertEqual(
            rail_config.facilitator_url, "https://x402-facilitator.cspr.cloud"
        )
        self.assertEqual(rail_config.network, config.CASPER_MAINNET)
        self.assertEqual(rail_config.asset_decimals, 9)

    def test_testnet_is_supported(self):
        rail_config = CasperRailConfig(
            treasury=TREASURY, network=config.CASPER_TESTNET
        )
        self.assertEqual(rail_config.network, "casper:casper-test")
        self.assertEqual(config.casper_chain_name(rail_config.network), "casper-test")

    def test_unknown_network_is_rejected(self):
        with self.assertRaises(ValueError):
            CasperRailConfig(treasury=TREASURY, network="eip155:8453")

    def test_trailing_slash_is_stripped_from_facilitator_url(self):
        rail_config = CasperRailConfig(
            treasury=TREASURY, facilitator_url="https://facilitator.example/"
        )
        self.assertEqual(rail_config.facilitator_url, "https://facilitator.example")


class PaymentRequirementsTests(unittest.TestCase):
    def test_requirements_match_x402_v2_casper_shape(self):
        rail = make_rail()
        requirements = rail.payment_requirements(
            PRICE_MOTES, "Premium export", "https://api.example.test/premium"
        )

        self.assertEqual(requirements["scheme"], "exact")
        self.assertEqual(requirements["network"], "casper:casper")
        self.assertEqual(requirements["payTo"], TREASURY)
        self.assertEqual(requirements["amount"], PRICE_MOTES)
        self.assertEqual(requirements["maxAmountRequired"], PRICE_MOTES)
        self.assertEqual(requirements["asset"], ASSET)
        self.assertEqual(requirements["extra"]["decimals"], "9")
        self.assertEqual(requirements["extra"]["name"], "wCSPR")
        self.assertEqual(requirements["extra"]["version"], "1")
        self.assertEqual(requirements["maxTimeoutSeconds"], 900)

    def test_challenge_wraps_requirements_in_accepts_list(self):
        rail = make_rail()
        challenge = rail.challenge(
            PRICE_MOTES, "Premium export", "https://api.example.test/premium"
        )

        self.assertEqual(challenge["x402Version"], 2)
        self.assertEqual(challenge["error"], "Payment Required")
        self.assertEqual(len(challenge["accepts"]), 1)
        self.assertEqual(challenge["accepts"][0]["payTo"], TREASURY)


class PaymentHeaderDecodingTests(unittest.TestCase):
    def test_base64_json_header_is_decoded(self):
        payload = build_payload()
        self.assertEqual(
            CasperRail.decode_payment_header(encode_header(payload)), payload
        )

    def test_raw_json_header_is_accepted(self):
        payload = build_payload()
        self.assertEqual(
            CasperRail.decode_payment_header(json.dumps(payload)), payload
        )

    def test_empty_header_is_malformed(self):
        with self.assertRaises(CasperPaymentError) as ctx:
            CasperRail.decode_payment_header("   ")
        self.assertEqual(ctx.exception.reason, "malformed_payload")

    def test_garbage_header_is_malformed(self):
        with self.assertRaises(CasperPaymentError) as ctx:
            CasperRail.decode_payment_header("not-a-payment")
        self.assertEqual(ctx.exception.reason, "malformed_payload")

    def test_non_object_json_is_malformed(self):
        with self.assertRaises(CasperPaymentError) as ctx:
            CasperRail.decode_payment_header(
                base64.b64encode(b"[1, 2, 3]").decode("ascii")
            )
        self.assertEqual(ctx.exception.reason, "malformed_payload")


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.rail = make_rail()
        self.requirements = self.rail.payment_requirements(
            PRICE_MOTES, "Premium export", "https://api.example.test/premium"
        )

    def test_verify_posts_x402_v2_body_to_facilitator(self):
        with mock.patch.object(
            casper_module.httpx,
            "post",
            return_value=FakeResponse({"isValid": True, "payer": PAYER}),
        ) as post:
            result = self.rail.verify(build_payload(), self.requirements)

        self.assertTrue(result["isValid"])
        url, = post.call_args[0]
        body = post.call_args[1]["json"]
        self.assertEqual(url, "https://x402-facilitator.cspr.cloud/verify")
        self.assertEqual(body["x402Version"], 2)
        self.assertEqual(body["paymentRequirements"], self.requirements)
        self.assertEqual(body["paymentPayload"]["network"], "casper:casper")

    def test_facilitator_rejection_raises_with_reason(self):
        with mock.patch.object(
            casper_module.httpx,
            "post",
            return_value=FakeResponse(
                {
                    "isValid": False,
                    "invalidReason": "invalid_signature",
                    "invalidMessage": "bad sig",
                }
            ),
        ):
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.verify(build_payload(), self.requirements)

        self.assertEqual(ctx.exception.reason, "invalid_signature")
        self.assertEqual(ctx.exception.message, "bad sig")

    def test_amount_mismatch_is_caught_locally_without_network_call(self):
        with mock.patch.object(casper_module.httpx, "post") as post:
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.verify(build_payload(value="1"), self.requirements)

        self.assertEqual(ctx.exception.reason, "amount_mismatch")
        post.assert_not_called()

    def test_pay_to_mismatch_is_caught_locally(self):
        with mock.patch.object(casper_module.httpx, "post") as post:
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.verify(
                    build_payload(to="00" + "11" * 32), self.requirements
                )

        self.assertEqual(ctx.exception.reason, "pay_to_mismatch")
        post.assert_not_called()

    def test_network_mismatch_is_caught_locally(self):
        with mock.patch.object(casper_module.httpx, "post") as post:
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.verify(
                    build_payload(network=config.CASPER_TESTNET), self.requirements
                )

        self.assertEqual(ctx.exception.reason, "network_mismatch")
        post.assert_not_called()

    def test_missing_authorization_field_is_malformed(self):
        payload = build_payload()
        del payload["payload"]["authorization"]["nonce"]
        with mock.patch.object(casper_module.httpx, "post") as post:
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.verify(payload, self.requirements)

        self.assertEqual(ctx.exception.reason, "malformed_payload")
        post.assert_not_called()

    def test_unreachable_facilitator_fails_closed(self):
        with mock.patch.object(
            casper_module.httpx, "post", side_effect=OSError("connection refused")
        ):
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.verify(build_payload(), self.requirements)

        self.assertEqual(ctx.exception.reason, "facilitator_unreachable")

    def test_non_200_facilitator_response_fails_closed(self):
        with mock.patch.object(
            casper_module.httpx, "post", return_value=FakeResponse({}, status_code=502)
        ):
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.verify(build_payload(), self.requirements)

        self.assertEqual(ctx.exception.reason, "facilitator_error")

    def test_non_json_facilitator_response_fails_closed(self):
        with mock.patch.object(
            casper_module.httpx,
            "post",
            return_value=FakeResponse(ValueError("not json")),
        ):
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.verify(build_payload(), self.requirements)

        self.assertEqual(ctx.exception.reason, "facilitator_error")


class SettleTests(unittest.TestCase):
    def setUp(self):
        self.rail = make_rail()
        self.requirements = self.rail.payment_requirements(
            PRICE_MOTES, "Premium export", "https://api.example.test/premium"
        )

    def test_settle_returns_deploy_hash(self):
        settle_response = {
            "success": True,
            "transaction": "dd" * 32,
            "network": "casper:casper",
            "payer": PAYER,
        }
        with mock.patch.object(
            casper_module.httpx, "post", return_value=FakeResponse(settle_response)
        ) as post:
            result = self.rail.settle(build_payload(), self.requirements)

        self.assertEqual(result["transaction"], "dd" * 32)
        self.assertEqual(
            post.call_args[0][0], "https://x402-facilitator.cspr.cloud/settle"
        )

    def test_failed_settlement_raises_with_error_reason(self):
        with mock.patch.object(
            casper_module.httpx,
            "post",
            return_value=FakeResponse(
                {
                    "success": False,
                    "errorReason": "put_deploy_failed",
                    "errorMessage": "node rejected deploy",
                }
            ),
        ):
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.settle(build_payload(), self.requirements)

        self.assertEqual(ctx.exception.reason, "put_deploy_failed")

    def test_verify_and_settle_calls_both_endpoints_in_order(self):
        responses = [
            FakeResponse({"isValid": True, "payer": PAYER}),
            FakeResponse({"success": True, "transaction": "dd" * 32, "payer": PAYER}),
        ]
        with mock.patch.object(
            casper_module.httpx, "post", side_effect=responses
        ) as post:
            payload, settlement = self.rail.verify_and_settle(
                encode_header(build_payload()), self.requirements
            )

        called = [call[0][0] for call in post.call_args_list]
        self.assertEqual(
            called,
            [
                "https://x402-facilitator.cspr.cloud/verify",
                "https://x402-facilitator.cspr.cloud/settle",
            ],
        )
        self.assertEqual(settlement["transaction"], "dd" * 32)
        self.assertEqual(payload["payload"]["authorization"]["from"], PAYER)

    def test_verify_failure_short_circuits_settlement(self):
        with mock.patch.object(
            casper_module.httpx,
            "post",
            return_value=FakeResponse(
                {"isValid": False, "invalidReason": "payload_expired"}
            ),
        ) as post:
            with self.assertRaises(CasperPaymentError) as ctx:
                self.rail.verify_and_settle(
                    encode_header(build_payload()), self.requirements
                )

        self.assertEqual(ctx.exception.reason, "payload_expired")
        self.assertEqual(post.call_count, 1)

    def test_settlement_receipt_is_base64_json(self):
        settle_response = {"success": True, "transaction": "dd" * 32}
        receipt = CasperRail.settlement_receipt(settle_response)
        self.assertEqual(
            json.loads(base64.b64decode(receipt).decode("utf-8")), settle_response
        )


if __name__ == "__main__":
    unittest.main()

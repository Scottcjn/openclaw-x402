import unittest
import importlib.util
import pathlib
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "openclaw_x402" / "config.py"
spec = importlib.util.spec_from_file_location("openclaw_x402_config", CONFIG_PATH)
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class ConfigConstantTests(unittest.TestCase):
    def test_base_network_constants_are_populated(self):
        self.assertEqual(config.X402_NETWORK, "eip155:8453")
        self.assertTrue(config.USDC_BASE.startswith("0x"))
        self.assertEqual(len(config.USDC_BASE), 42)
        self.assertTrue(config.WRTC_BASE.startswith("0x"))
        self.assertEqual(len(config.WRTC_BASE), 42)

    def test_swap_info_uses_configured_contracts_and_network(self):
        self.assertEqual(config.SWAP_INFO["wrtc_contract"], config.WRTC_BASE)
        self.assertEqual(config.SWAP_INFO["usdc_contract"], config.USDC_BASE)
        self.assertEqual(config.SWAP_INFO["network"], "Base (eip155:8453)")
        self.assertIn(config.USDC_BASE, config.SWAP_INFO["swap_url"])
        self.assertIn(config.WRTC_BASE, config.SWAP_INFO["swap_url"])


class IsFreeTests(unittest.TestCase):
    def test_zero_string_is_free(self):
        self.assertTrue(config.is_free("0"))

    def test_empty_string_is_free(self):
        self.assertTrue(config.is_free(""))

    def test_non_zero_prices_are_not_free(self):
        for price in ("1", "10000", "0.0", " 0"):
            with self.subTest(price=price):
                self.assertFalse(config.is_free(price))


class CdpCredentialTests(unittest.TestCase):
    def test_has_cdp_credentials_false_when_both_empty(self):
        with mock.patch.object(config, "CDP_API_KEY_NAME", ""), mock.patch.object(
            config, "CDP_API_KEY_PRIVATE_KEY", ""
        ):
            self.assertFalse(config.has_cdp_credentials())

    def test_has_cdp_credentials_false_when_only_name_present(self):
        with mock.patch.object(config, "CDP_API_KEY_NAME", "key-name"), mock.patch.object(
            config, "CDP_API_KEY_PRIVATE_KEY", ""
        ):
            self.assertFalse(config.has_cdp_credentials())

    def test_has_cdp_credentials_false_when_only_private_key_present(self):
        with mock.patch.object(config, "CDP_API_KEY_NAME", ""), mock.patch.object(
            config, "CDP_API_KEY_PRIVATE_KEY", "private-key"
        ):
            self.assertFalse(config.has_cdp_credentials())

    def test_has_cdp_credentials_true_when_both_present(self):
        with mock.patch.object(config, "CDP_API_KEY_NAME", "key-name"), mock.patch.object(
            config, "CDP_API_KEY_PRIVATE_KEY", "private-key"
        ):
            self.assertTrue(config.has_cdp_credentials())


if __name__ == "__main__":
    unittest.main()

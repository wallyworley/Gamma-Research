"""Contract tests for the pinned engine config.

Pure stdlib (unittest); runs before the data stack is installed:

    python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import ConfigError, EngineConfig  # noqa: E402

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_TOML = os.path.join(_REPO, "config", "engine.toml")


class TestEngineConfig(unittest.TestCase):
    def test_dict_roundtrip(self):
        cfg = EngineConfig.default()
        self.assertEqual(EngineConfig.from_dict(cfg.to_dict()), cfg)

    def test_toml_matches_code_defaults(self):
        # Drift guard: config/engine.toml must equal the code defaults.
        self.assertEqual(EngineConfig.from_toml(_TOML), EngineConfig.default())

    def test_partial_override_merges_onto_defaults(self):
        cfg = EngineConfig.from_dict({"costs": {"slippage_bps": 5.0}})
        self.assertEqual(cfg.costs.slippage_bps, 5.0)
        # untouched fields keep defaults
        self.assertEqual(cfg.pricer.model, "black_scholes")
        self.assertEqual(cfg.backtest.base_currency, "USD")

    def test_config_hash_is_stable_and_sensitive(self):
        base = EngineConfig.default()
        self.assertEqual(base.config_hash(), EngineConfig.default().config_hash())
        changed = EngineConfig.from_dict({"pricer": {"risk_free_rate": 0.05}})
        self.assertNotEqual(base.config_hash(), changed.config_hash())

    def test_unknown_top_level_key_raises(self):
        with self.assertRaises(ConfigError):
            EngineConfig.from_dict({"nope": {}})

    def test_unknown_section_key_raises(self):
        with self.assertRaises(ConfigError):
            EngineConfig.from_dict({"pricer": {"riskfree": 0.04}})

    def test_no_same_bar_fill_by_default(self):
        # The no-lookahead fill guard must default off.
        self.assertFalse(EngineConfig.default().backtest.allow_same_bar_fill)


if __name__ == "__main__":
    unittest.main()

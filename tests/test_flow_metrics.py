"""Golden tests for the Batch B flow metrics (flow.py) and the otm_customer sign.

Volume-weighted GEX (item 7), normalized GEX (item 4), the dealer-sign convention
sweep and the skew-adjusted "otm_customer" convention (item 5). Hand-computed exact
values on mini-chains. Needs the data stack.

    .venv/bin/python -m unittest discover -s tests -v
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pandas as pd
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

from src.ingest import schema  # noqa: E402

_QD = dt.date(2024, 6, 3)
_EXP = dt.date(2024, 7, 19)


def mini_chain(contracts, *, spot, quote_date=_QD):
    """Build a valid canonical frame from partial contract dicts (with volume)."""
    qts = pd.Timestamp(quote_date.year, quote_date.month, quote_date.day, 20, 0, tz="UTC")
    rows = []
    for c in contracts:
        row = {name: None for name in schema.field_names()}
        row.update({
            "symbol": "TEST", "root": c.get("root", "TEST"), "quote_ts": qts,
            "expiration": pd.Timestamp(c.get("expiration", _EXP)),
            "strike": float(c["strike"]), "type": c["type"],
            "underlying_price": float(spot),
            "gamma": c.get("gamma"), "delta": c.get("delta"),
            "open_interest": c.get("open_interest"), "volume": c.get("volume"),
            "iv": c.get("iv"), "_adapter": "test",
        })
        rows.append(row)
    df = pd.DataFrame(rows, columns=schema.field_names())
    df["quote_ts"] = pd.to_datetime(df["quote_ts"], utc=True)
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["oi_asof_date"] = pd.to_datetime(df["oi_asof_date"])
    scalar = {k: v for k, v in schema.pandas_dtypes().items()
              if k not in ("quote_ts", "expiration", "oi_asof_date")}
    return df.astype(scalar)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestVolumeGex(unittest.TestCase):
    def setUp(self):
        # spot=100 => dollar factor 100, size 100 => weight * gamma * 10000.
        self.df = mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.05, "volume": 1000},  # +500,000
            {"type": "put",  "strike": 100, "gamma": 0.04, "volume": 2000},  # -800,000
            {"type": "call", "strike": 110, "gamma": 0.03, "volume": 500},   # +150,000
        ], spot=100.0)

    def test_frame_is_valid(self):
        self.assertEqual(schema.validate_frame(self.df), [])

    def test_contract_values(self):
        from src.metrics import contract_gex_volume_proxy
        vals = contract_gex_volume_proxy(self.df).tolist()
        self.assertAlmostEqual(vals[0], 500_000.0)
        self.assertAlmostEqual(vals[1], -800_000.0)
        self.assertAlmostEqual(vals[2], 150_000.0)

    def test_net_golden(self):
        from src.metrics import net_gex_volume_proxy
        self.assertAlmostEqual(net_gex_volume_proxy(self.df), -150_000.0)

    def test_by_strike(self):
        from src.metrics import gex_volume_by_strike_proxy
        by = gex_volume_by_strike_proxy(self.df)
        self.assertAlmostEqual(by.loc[100.0], -300_000.0)
        self.assertAlmostEqual(by.loc[110.0], 150_000.0)

    def test_null_volume_is_zero_weight(self):
        # A contract with null volume must contribute 0 (mirrors gex.py null-OI).
        from src.metrics import net_gex_volume_proxy
        df = mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.05, "volume": 1000},   # +500,000
            {"type": "put",  "strike": 100, "gamma": 0.90, "volume": None},    # 0 weight
        ], spot=100.0)
        self.assertAlmostEqual(net_gex_volume_proxy(df), 500_000.0)

    def test_volume_differs_from_oi(self):
        # Same chain, OI != volume: the two GEX variants must differ.
        from src.metrics import net_gex, net_gex_volume_proxy
        df = mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.05, "open_interest": 100, "volume": 5000},
        ], spot=100.0)
        self.assertAlmostEqual(net_gex(df), 0.05 * 100 * 10000)          # OI weight
        self.assertAlmostEqual(net_gex_volume_proxy(df), 0.05 * 5000 * 10000)  # volume weight


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestNormalizedGex(unittest.TestCase):
    def setUp(self):
        self.df = mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.05, "open_interest": 1000},  # +500,000
            {"type": "put",  "strike": 100, "gamma": 0.04, "open_interest": 2000},  # -800,000
            {"type": "call", "strike": 110, "gamma": 0.03, "open_interest": 500},   # +150,000
        ], spot=100.0)

    def test_option_notional_golden(self):
        from src.metrics import option_notional
        # (1000+2000+500) * 100 * 100 = 35,000,000
        self.assertAlmostEqual(option_notional(self.df), 35_000_000.0)

    def test_normalized_by_notional(self):
        from src.metrics import gex_normalized
        # net_gex = -150,000; / 35,000,000 = -0.00428571...
        self.assertAlmostEqual(gex_normalized(self.df), -150_000.0 / 35_000_000.0)

    def test_explicit_denominator(self):
        from src.metrics import gex_normalized
        self.assertAlmostEqual(gex_normalized(self.df, denominator=1_000_000.0), -0.15)

    def test_invalid_denominator_is_none(self):
        from src.metrics import gex_normalized
        self.assertIsNone(gex_normalized(self.df, denominator=0.0))
        self.assertIsNone(gex_normalized(self.df, denominator=-5.0))
        self.assertIsNone(gex_normalized(self.df, denominator=float("nan")))

    def test_unknown_string_denominator_raises(self):
        from src.metrics import gex_normalized
        with self.assertRaises(ValueError):
            gex_normalized(self.df, denominator="market_cap")

    def test_empty_notional_is_none(self):
        from src.metrics import gex_normalized
        self.assertIsNone(gex_normalized(mini_chain([], spot=100.0)))


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestConventionSweep(unittest.TestCase):
    def _disagreeing_chain(self):
        # OTM call (both conventions agree +) plus a big ITM put: naive signs the put
        # -1 (=> net negative), otm_customer excludes ITM (=> net positive). They flip.
        return mini_chain([
            {"type": "call", "strike": 110, "gamma": 0.03, "open_interest": 1000},  # +300,000 both
            {"type": "put",  "strike": 130, "gamma": 0.05, "open_interest": 2000},  # naive -1M; otm 0
        ], spot=100.0)

    def test_conventions_disagree(self):
        from src.metrics import net_gex_by_convention
        out = net_gex_by_convention(self._disagreeing_chain())
        self.assertEqual(set(out), {"long_call_short_put", "otm_customer"})
        self.assertAlmostEqual(out["long_call_short_put"], -700_000.0)  # 300k - 1,000k
        self.assertAlmostEqual(out["otm_customer"], 300_000.0)          # ITM put excluded

    def test_custom_conventions_tuple(self):
        from src.metrics import net_gex_by_convention
        out = net_gex_by_convention(self._disagreeing_chain(),
                                    conventions=("long_call_short_put", "short_call_long_put"))
        self.assertAlmostEqual(out["long_call_short_put"], -700_000.0)
        self.assertAlmostEqual(out["short_call_long_put"], 700_000.0)  # exact mirror


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestOtmCustomerSigns(unittest.TestCase):
    def test_signs_per_row(self):
        from src.metrics._common import dealer_signs
        df = mini_chain([
            {"type": "call", "strike": 110, "gamma": 0.01, "open_interest": 1},  # OTM call -> +1
            {"type": "put",  "strike": 90,  "gamma": 0.01, "open_interest": 1},  # OTM put  -> -1
            {"type": "call", "strike": 90,  "gamma": 0.01, "open_interest": 1},  # ITM call -> 0
            {"type": "put",  "strike": 110, "gamma": 0.01, "open_interest": 1},  # ITM put  -> 0
            {"type": "call", "strike": 100, "gamma": 0.01, "open_interest": 1},  # ATM     -> 0
            {"type": "put",  "strike": 100, "gamma": 0.01, "open_interest": 1},  # ATM     -> 0
        ], spot=100.0)
        signs = dealer_signs(df, "otm_customer").tolist()
        self.assertEqual(signs, [1.0, -1.0, 0.0, 0.0, 0.0, 0.0])

    def test_net_gex_accepts_convention(self):
        from src.config import EngineConfig
        from src.metrics import net_gex
        cfg = EngineConfig.from_dict({"metrics": {"dealer_sign_convention": "otm_customer"}})
        df = mini_chain([
            {"type": "call", "strike": 110, "gamma": 0.03, "open_interest": 1000},  # +300,000
            {"type": "put",  "strike": 90,  "gamma": 0.04, "open_interest": 1000},  # -400,000
        ], spot=100.0)
        self.assertAlmostEqual(net_gex(df, config=cfg), -100_000.0)

    def test_unknown_convention_lists_otm_customer(self):
        from src.metrics._common import dealer_signs
        df = mini_chain([{"type": "call", "strike": 100, "gamma": 0.01, "open_interest": 1}], spot=100.0)
        with self.assertRaises(ValueError) as ctx:
            dealer_signs(df, "bogus")
        self.assertIn("otm_customer", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

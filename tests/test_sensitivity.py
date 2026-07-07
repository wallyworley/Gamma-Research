"""Tests for the sign-convention sensitivity sweep (src/eval/sensitivity.py, item 5/F11).

Chains built so the two dealer-sign conventions produce OPPOSITE regime signals ->
the sweep reports flips=True; a symmetric case where both conventions agree ->
flips=False. Plus direct coverage of the verdict logic's two flip conditions.

    .venv/bin/python -m unittest discover -s tests -v
"""

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

_DATES = ([pd.Timestamp(d) for d in ("2024-06-03", "2024-06-04", "2024-06-05", "2024-06-06")]
          if _HAVE_STACK else [])


def _uts(d):
    return pd.Timestamp(d.year, d.month, d.day, 20, 0, tz="UTC")


def mini_chain(contracts, *, spot, ts):
    rows = []
    for c in contracts:
        row = {n: None for n in schema.field_names()}
        row.update({
            "symbol": "T", "quote_ts": ts, "expiration": pd.Timestamp("2024-07-19"),
            "strike": float(c["strike"]), "type": c["type"], "underlying_price": spot,
            "gamma": 0.03, "open_interest": c.get("oi", 1000), "iv": 0.2, "_adapter": "t",
        })
        rows.append(row)
    df = pd.DataFrame(rows, columns=schema.field_names())
    df["quote_ts"] = pd.to_datetime(df["quote_ts"], utc=True)
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["oi_asof_date"] = pd.to_datetime(df["oi_asof_date"])
    scalar = {k: v for k, v in schema.pandas_dtypes().items()
              if k not in ("quote_ts", "expiration", "oi_asof_date")}
    return df.astype(scalar)


def _bars():
    # A steadily rising market so a long book profits and a short book loses.
    return pd.DataFrame({"open": [100.0, 102.0, 104.0, 106.0],
                         "close": [102.0, 104.0, 106.0, 108.0]}, index=_DATES)


def _builder(chains, cfg):
    from src.signals import regime_signal
    return regime_signal(chains, config=cfg, long=1.0, short=-1.0, flat=0.0)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestConventionSweep(unittest.TestCase):
    def _flip_chains(self):
        # ITM call@90 (OI 2000) + OTM put@90 (OI 1000), spot 100.
        #   long_call_short_put: call +1, put -1 -> +2000-1000 -> +GEX -> long.
        #   otm_customer:        ITM call excluded, OTM put -1 -> -GEX -> short.
        contracts = [{"type": "call", "strike": 90, "oi": 2000},
                     {"type": "put", "strike": 90, "oi": 1000}]
        return {d: mini_chain(contracts, spot=100.0, ts=_uts(d)) for d in _DATES}

    def _symmetric_chains(self):
        # OTM call@110 (OI 2000) + OTM put@90 (OI 1000), spot 100.
        # Both conventions count the same signed contributions -> +GEX under both.
        contracts = [{"type": "call", "strike": 110, "oi": 2000},
                     {"type": "put", "strike": 90, "oi": 1000}]
        return {d: mini_chain(contracts, spot=100.0, ts=_uts(d)) for d in _DATES}

    def test_opposite_conventions_report_flip(self):
        from src.eval import convention_sweep
        res = convention_sweep(self._flip_chains(), _bars(), signal_builder=_builder,
                               n_permutations=20, n_controls=0, bootstrap_n=0, random_seed=0)
        self.assertIn("long_call_short_put", res)
        self.assertIn("otm_customer", res)
        self.assertTrue(res["verdict"]["flips"])
        self.assertTrue(res["verdict"]["total_return_sign_change"])
        # The two scorecards must carry different config hashes (different convention).
        self.assertNotEqual(res["long_call_short_put"]["config_hash"],
                            res["otm_customer"]["config_hash"])
        # Long on an up market makes money; short loses -> opposite total-return signs.
        self.assertGreater(res["long_call_short_put"]["strategy"]["total_return"], 0.0)
        self.assertLess(res["otm_customer"]["strategy"]["total_return"], 0.0)

    def test_symmetric_conventions_report_no_flip(self):
        from src.eval import convention_sweep
        res = convention_sweep(self._symmetric_chains(), _bars(), signal_builder=_builder,
                               n_permutations=20, n_controls=0, bootstrap_n=0, random_seed=0)
        self.assertFalse(res["verdict"]["flips"])
        self.assertFalse(res["verdict"]["total_return_sign_change"])
        # Identical signals -> identical strategy total returns across conventions.
        self.assertAlmostEqual(res["long_call_short_put"]["strategy"]["total_return"],
                               res["otm_customer"]["strategy"]["total_return"])


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestVerdictLogic(unittest.TestCase):
    def test_permutation_percentile_crossing_half_flips(self):
        # Same total-return sign, but the permutation verdict swaps sides across 0.5.
        from src.eval.sensitivity import _verdict
        per = {"a": {"strategy_percentile": 0.9, "total_return": 0.1},
               "b": {"strategy_percentile": 0.2, "total_return": 0.1}}
        v = _verdict(per)
        self.assertTrue(v["flips"])
        self.assertTrue(v["crosses_permutation_half"])
        self.assertFalse(v["total_return_sign_change"])

    def test_agreeing_conventions_do_not_flip(self):
        from src.eval.sensitivity import _verdict
        per = {"a": {"strategy_percentile": 0.8, "total_return": 0.1},
               "b": {"strategy_percentile": 0.7, "total_return": 0.2}}
        v = _verdict(per)
        self.assertFalse(v["flips"])

    def test_nan_percentiles_ignored(self):
        # With permutation testing disabled (percentile NaN) only the sign rule applies.
        from src.eval.sensitivity import _verdict
        per = {"a": {"strategy_percentile": float("nan"), "total_return": 0.1},
               "b": {"strategy_percentile": float("nan"), "total_return": -0.1}}
        v = _verdict(per)
        self.assertTrue(v["flips"])
        self.assertFalse(v["crosses_permutation_half"])
        self.assertTrue(v["total_return_sign_change"])


if __name__ == "__main__":
    unittest.main()

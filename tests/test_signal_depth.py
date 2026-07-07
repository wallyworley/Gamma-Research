"""Tests for the deeper signal rules (src/signals/rules.py, Batch C item 2).

Hand-built 3-4 date chains + bars with exact expected weight sequences: the
distance-to-flip signal (including a NaN-flip date), the percentile gate, and the
trend-interaction conditional (including a lookahead-alignment check). Follows the
mini_chain pattern of tests/test_signals_eval.py.

    .venv/bin/python -m unittest discover -s tests -v
"""

import math
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
            "gamma": c.get("gamma", 0.03), "open_interest": c.get("oi", 1000),
            "iv": 0.2, "_adapter": "t",
        })
        rows.append(row)
    df = pd.DataFrame(rows, columns=schema.field_names())
    df["quote_ts"] = pd.to_datetime(df["quote_ts"], utc=True)
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["oi_asof_date"] = pd.to_datetime(df["oi_asof_date"])
    scalar = {k: v for k, v in schema.pandas_dtypes().items()
              if k not in ("quote_ts", "expiration", "oi_asof_date")}
    return df.astype(scalar)


def _calls():
    return [{"type": "call", "strike": 100}, {"type": "call", "strike": 110}]


def _puts():
    return [{"type": "put", "strike": 100}, {"type": "put", "strike": 90}]


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestFlipDistance(unittest.TestCase):
    def _chains(self):
        d0, d1, d2, d3 = _DATES
        return {
            # put@85 + call@95: Net GEX flips BELOW spot -> spot above flip -> +distance.
            d0: mini_chain([{"type": "put", "strike": 85}, {"type": "call", "strike": 95}],
                           spot=100.0, ts=_uts(d0)),
            # put@105 + call@115: flip ABOVE spot -> spot below flip -> -distance.
            d1: mini_chain([{"type": "put", "strike": 105}, {"type": "call", "strike": 115}],
                           spot=100.0, ts=_uts(d1)),
            # identical-strike straddle: Net GEX is identically 0 -> flip AT spot -> distance 0.
            d2: mini_chain([{"type": "call", "strike": 100}, {"type": "put", "strike": 100}],
                           spot=100.0, ts=_uts(d2)),
            # all calls: Net GEX never crosses 0 in the grid -> no flip -> NaN distance.
            d3: mini_chain(_calls(), spot=100.0, ts=_uts(d3)),
        }

    def test_distance_series_signs_and_nan(self):
        from src.signals import flip_distance_series
        dist = flip_distance_series(self._chains())
        d0, d1, d2, d3 = _DATES
        self.assertGreater(dist[d0], 0.0)          # spot above flip
        self.assertTrue(0.05 < dist[d0] < 0.15)    # ~+0.108, bounded golden
        self.assertLess(dist[d1], 0.0)             # spot below flip
        self.assertAlmostEqual(dist[d2], 0.0)      # flip exactly at spot
        self.assertTrue(math.isnan(dist[d3]))      # no flip in the grid

    def test_distance_signal_mapping(self):
        from src.signals import flip_distance_signal
        sig = flip_distance_signal(self._chains(), threshold=0.005,
                                   long=1.0, short=-1.0, flat=0.0)
        self.assertEqual([sig[d] for d in _DATES], [1.0, -1.0, 0.0, 0.0])

    def test_threshold_widens_to_flat(self):
        # A threshold wider than the actual distances collapses everything to flat.
        from src.signals import flip_distance_signal
        sig = flip_distance_signal(self._chains(), threshold=0.5,
                                   long=1.0, short=-1.0, flat=0.0)
        self.assertEqual([sig[d] for d in _DATES], [0.0, 0.0, 0.0, 0.0])


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestPercentileGate(unittest.TestCase):
    def test_gate_middle_of_range(self):
        from src.signals import percentile_gate
        dates = _DATES + [pd.Timestamp("2024-06-07")]
        series = pd.Series([100.0, 0.0, 50.0, 90.0, 10.0], index=dates)
        signal = pd.Series([1.0, -1.0, 1.0, -1.0, 1.0], index=dates)
        gated = percentile_gate(signal, series, window=5, low=0.3, high=0.7)
        # trailing percentiles: 1.0, 0.5, 0.667, 0.75, 0.4 -> gate rows in [0.3,0.7].
        self.assertEqual(list(gated.values), [1.0, 0.0, 0.0, -1.0, 0.0])

    def test_gate_passes_extremes_through(self):
        from src.signals import percentile_gate
        # A strictly increasing series is always at its trailing max (percentile 1.0),
        # which is outside any middle band -> nothing is gated.
        dates = _DATES
        series = pd.Series([1.0, 2.0, 3.0, 4.0], index=dates)
        signal = pd.Series([1.0, 1.0, 1.0, 1.0], index=dates)
        gated = percentile_gate(signal, series, window=4, low=0.3, high=0.7)
        self.assertEqual(list(gated.values), [1.0, 1.0, 1.0, 1.0])


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestTrendInteraction(unittest.TestCase):
    def _chains(self):
        d0, d1, d2, d3 = _DATES
        return {
            d0: mini_chain(_calls(), spot=100.0, ts=_uts(d0)),   # +GEX
            d1: mini_chain(_puts(), spot=100.0, ts=_uts(d1)),    # -GEX
            d2: mini_chain(_calls(), spot=100.0, ts=_uts(d2)),   # +GEX
            d3: mini_chain(_puts(), spot=100.0, ts=_uts(d3)),    # -GEX
        }

    def _bars(self):
        return pd.DataFrame({"open": [100, 100, 110, 105], "close": [100.0, 110.0, 105.0, 105.0]},
                            index=_DATES)

    def test_follow_in_short_gamma_fade_in_long_gamma(self):
        from src.signals import trend_interaction_signal
        sig = trend_interaction_signal(self._chains(), self._bars(), lookback=1)
        # d0: no prior bar -> flat. d1 (-GEX, up trend) -> follow -> +1.
        # d2 (+GEX, down trend) -> fade -> +1. d3 (-GEX, zero trend) -> flat.
        self.assertEqual([sig[d] for d in _DATES], [0.0, 1.0, 1.0, 0.0])

    def test_no_lookahead_future_close_does_not_change_earlier_weight(self):
        from src.signals import trend_interaction_signal
        chains, bars = self._chains(), self._bars()
        base = trend_interaction_signal(chains, bars, lookback=1)
        mutated = bars.copy()
        mutated.loc[_DATES[2], "close"] = 999.0     # a FUTURE close relative to d1
        mutated.loc[_DATES[3], "close"] = 999.0
        after = trend_interaction_signal(chains, mutated, lookback=1)
        self.assertEqual(base[_DATES[1]], after[_DATES[1]])   # d1 weight is decided at d1


if __name__ == "__main__":
    unittest.main()

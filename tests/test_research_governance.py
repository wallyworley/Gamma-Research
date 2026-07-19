import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.research.audit import audit_series_and_bars
from src.research.registry import canonical_hash, load_and_verify_manifest, validate_manifest


class TestManifestLock(unittest.TestCase):
    def _manifest(self):
        payload = {k: {} for k in (
            "question", "hypothesis", "universe", "signal", "target", "controls",
            "validation", "pass_fail", "placebos", "exclusions"
        )}
        payload.update({"experiment_id": "EXP-X", "status": "locked",
                        "registered_at": "2026-07-13"})
        payload["manifest_hash"] = canonical_hash(payload)
        return payload

    def test_locked_manifest_round_trip(self):
        payload = self._manifest()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td, "m.json")
            path.write_text(json.dumps(payload))
            self.assertEqual(load_and_verify_manifest(path)["experiment_id"], "EXP-X")

    def test_edit_invalidates_lock(self):
        payload = self._manifest()
        payload["question"] = "changed after lock"
        self.assertTrue(any("hash mismatch" in x for x in validate_manifest(payload)))


class TestPreOutcomeAudit(unittest.TestCase):
    def test_freezes_coverage_without_outcomes(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="B")
        series = pd.DataFrame({
            "spot": [100, 101, 102], "net_gex": [1, 2, 1000],
            "net_gex_otm": [-1, -2, -3], "option_notional": [1000, 1000, 1000],
            "n_contracts": [10, 11, 12], "spot_source": ["vendor_close"] * 3,
        }, index=idx)
        bars = pd.DataFrame({"open": [100, 101, 102], "high": [101, 102, 103],
                             "low": [99, 100, 101], "close": [100.5, 101.5, 102.5]}, index=idx)
        result = audit_series_and_bars(series, bars, symbol="spy",
                                       prospective_start="2026-07-13", minimum_history=2)
        self.assertEqual(result["overlap_sessions"], 3)
        self.assertTrue(result["eligibility"]["historical_development_eligible"])
        self.assertNotIn("target", json.dumps(result).lower())

    def test_invalid_ohlc_is_reported(self):
        idx = pd.to_datetime(["2026-01-02"])
        series = pd.DataFrame({"spot": [100], "net_gex": [1], "net_gex_otm": [-1],
                               "option_notional": [1000], "n_contracts": [10]}, index=idx)
        bars = pd.DataFrame({"open": [100], "high": [99], "low": [98], "close": [100]}, index=idx)
        result = audit_series_and_bars(series, bars, symbol="SPY",
                                       prospective_start="2026-07-13", minimum_history=1)
        self.assertEqual(result["invalid_ohlc_dates"], ["2026-01-02"])


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.audit_gex_contributions import audit_partition


class TestContributionAudit(unittest.TestCase):
    def test_missing_partition_is_explicit(self):
        result = audit_partition("/definitely/missing.parquet", symbol="SPY",
                                 session="2026-01-02")
        self.assertEqual(result["status"], "missing")

    def test_recomputes_and_ranks_contracts(self):
        frame = pd.DataFrame({
            "symbol": ["SPY", "SPY"], "root": ["SPY", "SPY"],
            "expiration": [pd.Timestamp("2026-02-01")] * 2,
            "strike": [100.0, 90.0], "type": ["call", "put"],
            "underlying_price": [100.0, 100.0], "gamma": [0.02, 0.01],
            "open_interest": [100, 50], "iv": [0.2, 0.3], "volume": [10, 5],
            "_greek_source": ["fixture"] * 2, "_spot_source": ["fixture"] * 2,
            "oi_asof_date": [pd.Timestamp("2026-01-01")] * 2,
        })
        with tempfile.TemporaryDirectory() as td:
            path = Path(td, "chain.parquet")
            pq.write_table(pa.Table.from_pandas(frame), path)
            result = audit_partition(path, symbol="SPY", session="2026-01-02", top_n=2)
        self.assertEqual(result["status"], "audited")
        self.assertEqual(result["duplicate_key_rows"], 0)
        self.assertAlmostEqual(result["recomputed"]["net_gex"], 15000.0)
        self.assertEqual(result["top_contracts"][0]["type"], "call")


if __name__ == "__main__":
    unittest.main()

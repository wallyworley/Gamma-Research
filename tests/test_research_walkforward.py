import unittest

import numpy as np
import pandas as pd

from src.research.holdout import HoldoutPolicy, assert_frozen_development_bars
from src.research.walkforward import (
    DEFAULT_CONTROLS,
    next_day_absolute_log_return,
    walk_forward_score,
)


class TestHoldoutBoundary(unittest.TestCase):
    def _policy(self):
        return HoldoutPolicy(
            experiment_id="EXP-X-HOLDOUT", parent_experiment="EXP-X",
            start=pd.Timestamp("2026-07-13"), minimum_sessions=126,
            earliest_evaluation_date=pd.Timestamp("2027-01-15"),
            maximum_terminal_looks=1,
        )

    def test_frozen_development_bars_pass(self):
        bars = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime(["2026-07-10"]))
        assert_frozen_development_bars(bars, self._policy(), source="dev.csv")

    def test_holdout_bar_fails_closed(self):
        bars = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime(["2026-07-13"]))
        with self.assertRaisesRegex(ValueError, "sealed holdout violation"):
            assert_frozen_development_bars(bars, self._policy(), source="current.csv")

    def test_target_does_not_cross_last_loaded_bar(self):
        bars = pd.DataFrame({"close": [100.0, 101.0]},
                            index=pd.to_datetime(["2026-07-09", "2026-07-10"]))
        target = next_day_absolute_log_return(bars)
        self.assertTrue(pd.notna(target.iloc[0]))
        self.assertTrue(pd.isna(target.iloc[-1]))


class TestWalkForwardScorer(unittest.TestCase):
    def test_detects_strong_incremental_negative_signal(self):
        rng = np.random.default_rng(7)
        idx = pd.bdate_range("2017-01-02", "2022-12-30")
        signal = rng.normal(size=len(idx))
        frame = pd.DataFrame(index=idx)
        for col in DEFAULT_CONTROLS:
            frame[col] = rng.normal(size=len(idx))
        frame["day_of_week"] = frame.index.dayofweek
        frame["signal"] = signal
        frame["target_abs_log_return"] = 1.0 - 0.35 * signal + rng.normal(0, 0.1, len(idx))
        score = walk_forward_score(
            frame, signal="signal", first_test_year=2019,
            bootstrap_samples=200, placebo_permutations=20,
        )
        self.assertGreater(score["oos_squared_error_improvement"], 0.01)
        self.assertEqual(score["annual_fold_sign_consistency"], 1.0)
        self.assertLessEqual(
            score["moving_block_bootstrap"]["one_sided_p_mean_loss_gain_le_zero"], 0.05
        )
        self.assertGreaterEqual(score["block_permutation_placebo"]["observed_percentile"], 0.95)


if __name__ == "__main__":
    unittest.main()

# Legacy vendor-gamma result quarantine

The July 8 result files under `data/analysis/vol_forecast_results_*.json` and
`data/calibration/scorecard_SPY.json` are preserved as negative-evidence and
research-history artifacts, but they are **invalid for current inference**.

The contribution audit found that retained vendor gamma could remain extreme
when the vendor IV solver had failed and IV was nulled. The corrected research
series recomputes Black-Scholes gamma only from valid stored IV across every
session. No current claim may quote the July 8 positive SPY cells as evidence
unless it is explicitly labeled as a contaminated-input historical result.

Current inference must come from the locked `EXP-2026-001` development scorer
using the `gex_series_bsgamma_*`/chain-feature lineage and, later, the sealed
prospective holdout.

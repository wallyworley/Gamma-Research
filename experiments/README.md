# Experiment registry

Each experiment has a locked JSON manifest under `registry/`. The stored
`manifest_hash` is SHA-256 over canonical JSON with the hash field excluded.
Changing any hypothesis, sample, feature, exclusion, validation rule, or gate
invalidates the lock; substantive changes require a new experiment ID.

`eligibility/` contains pre-outcome data freezes. These artifacts may record
coverage, missingness, input file hashes, and feature outliers, but must not
contain forward targets or model results.

`EXP-2026-001-HOLDOUT.json` is the fail-closed prospective access policy. The
development scorer rejects a bar file containing even one session on or after
2026-07-13. The holdout cannot be evaluated before both 126 sessions and
2027-01-15, and it permits one terminal look only.

Verify and generate the first freeze:

```bash
python scripts/week1_research_freeze.py \
  --manifest experiments/registry/EXP-2026-001.json \
  --input SPY data/analysis/gex_series_SPY.json data/analysis/yf_spy_daily.csv \
  --input SPX data/analysis/gex_series_SPX.json data/analysis/yf_spx_daily.csv \
  --out experiments/eligibility/EXP-2026-001-week1-freeze.json
```

Run the corrected-gamma development scorecard on the VPS after building the
chain-only feature files. Use physically frozen development bars; do not point
this command at an updated/current bar file:

```bash
python scripts/score_exp_2026_001.py \
  --manifest experiments/registry/EXP-2026-001.json \
  --holdout-policy experiments/registry/EXP-2026-001-HOLDOUT.json \
  --input SPY analysis/EXP-2026-001-chain-features-SPY.json analysis/yf_spy_daily.DEVELOPMENT.csv analysis/yf_spy_daily.DEVELOPMENT.csv \
  --input SPX analysis/EXP-2026-001-chain-features-SPX.json analysis/yf_spx_daily.DEVELOPMENT.csv analysis/yf_spy_daily.DEVELOPMENT.csv \
  --out analysis/EXP-2026-001-development-scorecard.json
```

The SPY empirical-sign arm is the only primary inference. SPX has no independent
empirical sign calibration and is reported as a naive-sign replication sensitivity.

The completed compact result is tracked at
`results/EXP-2026-001-development-summary.json`; the full result remains on the
VPS under `analysis/`. The registered Day-30 decision is to stop EOD OI-GEX level
as a standalone alpha signal. Do not open the prospective holdout for this failed
specification.

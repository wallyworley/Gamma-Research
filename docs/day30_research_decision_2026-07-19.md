# Day-30 research decision — EXP-2026-001

Date: 2026-07-19
Decision: **STOP EOD OI-GEX level as a standalone alpha signal.**

## What was tested

The locked EXP-2026-001 model was run on the corrected Black-Scholes-gamma
feature panels with the predeclared HAR, IV, skew, option-volume, liquidity,
trend, market-return, and calendar controls. The evaluation used expanding
annual walk-forward predictions beginning in 2019, training-only 0.5/99.5%
winsorization, a 10-session moving-block bootstrap, and 100 block-permutation
placebos.

The prospective holdout beginning 2026-07-13 was not scored. The scorer used
physically frozen bar files ending 2026-07-07 and recorded zero holdout outcome
accesses. A separate locked policy forbids holdout evaluation before 126 sessions
and 2027-01-15 and permits one terminal look.

## Result

| Arm | OOS predictions | OOS squared-error improvement | Bootstrap p | Negative-sign folds | Placebo percentile | Gates passed |
|---|---:|---:|---:|---:|---:|---:|
| SPY corrected empirical sign (primary) | 1,884 | -0.0045% | 0.5385 | 62.5% | 79% | 0/4 |
| SPX corrected naive sign (sensitivity) | 1,613 | -0.3313% | 0.9970 | 12.5% | 14% | 0/4 |

The SPY signal did not improve OOS forecast loss and missed every registered
gate. SPX was worse and is only a sensitivity because no independent empirical
SPX sign calibration exists. The development evidence therefore does not
justify waiting to score this specification on the prospective holdout.

## Disposition

1. Preserve the null and the sealed holdout; do not open the holdout for this
   failed specification.
2. Retain corrected EOD OI-GEX level as a descriptive control or risk feature,
   not as a standalone predictive claim.
3. Keep the July 8 vendor-gamma scorecards quarantined as invalid for inference.
4. Do not buy open-close data, build option execution, or build broker plumbing
   on the basis of this result.
5. Any future ΔGEX, volume-GEX, or DTE-bucket work requires a new locked
   experiment and must be justified as a distinct observable signal family, not
   a threshold/subperiod rescue of EXP-2026-001.

The full VPS artifact is
`/opt/gamma-research/analysis/EXP-2026-001-development-scorecard.json`, SHA-256
`c5b758e59e2d55d16de59c2728429d630e507d9c8eb442ed3f3782f09defd3a3`.
The compact tracked result is
`experiments/results/EXP-2026-001-development-summary.json`.

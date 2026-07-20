# Week 1 Research Freeze — EXP-2026-001

Date: 2026-07-13

## What is locked

The primary hypothesis, controls, exclusions, target, validation protocol,
placebos, and pass/fail thresholds are locked in
`experiments/registry/EXP-2026-001.json` under manifest hash:

`83a4b36ed5d1abd7272197072b822dfce636b69937cfa8608db221b6d96ba683`

Historical outcomes through the current files have already been inspected in
prior research. They are development-only. The untouched holdout begins
2026-07-13 and currently contains zero sessions; it must not be scored until a
separate evaluation-date/minimum-sample manifest is registered.

## Frozen input coverage

| Symbol | Series/bar overlap | Span | Missing sessions | Invalid rows |
|---|---:|---|---|---:|
| SPY | 2,389 | 2017-01-03–2026-07-07 | 0 | 0 |
| SPX | 2,388 | 2017-01-03–2026-07-07 | 2026-07-02 | 0 |

Exact input SHA-256 values and the complete pre-outcome audit are stored in
`experiments/eligibility/EXP-2026-001-week1-freeze.json`. No forward-return or
model-outcome fields were calculated by the freeze.

## Reconciliation gates before feature modeling

The MAD-10 diagnostic flags five SPY and 22 SPX normalized-GEX observations.
Flags are diagnostics, not automatic exclusions.

1. **SPY 2026-06-26:** `gex_norm=-0.8493736`, robust z `-91.53`. The underlying
   net-GEX value is also present in the separately rebuilt empirical-sign series,
   so this is not merely a precomputed-series serialization problem. Reconcile
   contract contributions, gamma source, expiry/root mix, spot, OI, and duplicate
   keys against the raw partition.
2. **SPX 2020-10 through 2021-12 cluster:** numerous large observations, including
   `gex_norm` near 2.0. Determine whether this is a vendor-era/schema/root change,
   a small denominator, a small set of contracts, or a genuine exposure regime.
   The frozen audit shows no corresponding MAD-10 option-notional or contract-count
   outliers, making a contribution-level audit necessary.
3. **SPX 2024-07-03:** `gex_norm=0.727685`, robust z `117.15`; perform the same
   contract-contribution reconciliation.
4. **SPX 2026-07-02 missing:** establish whether the raw chain is absent, rejected,
   or omitted during series construction. Do not impute it.
5. **SPY provenance:** one historical row has no `spot_source`; identify its date
   and vendor provenance before building quote-derived controls.

For every flagged date, produce a reconciliation row containing raw partition
hash, top 20 absolute contract-GEX contributions, root/expiry/DTE totals, OI and
gamma coverage, duplicate-key count, spot source, and a disposition of
`valid`, `vendor-corrected`, or `ineligible-data-error`. A correction changes an
input hash and therefore requires a new eligibility freeze; it does not permit
editing the experiment hypothesis or gates.

## VPS contribution-level audit result

The read-only VPS audit was completed for all 27 MAD-10 dates plus the missing
SPX date. The derived artifact is
`experiments/eligibility/EXP-2026-001-contribution-audit.json`; each audited row
contains its raw Parquet SHA-256, recomputed GEX, duplicate count, coverage,
top-20 contracts, and expiry/root/type concentration.

- All 27 available partitions reproduce the frozen series exactly; this rules
  out series serialization and aggregation drift.
- No audited partition has duplicate canonical keys. Spot is constant within
  each partition, gamma coverage is 100%, and option notional/contract count are
  not the source of the extremes.
- **26 of 27 dominant contracts have null IV.** The adapter nulls IV when the
  vendor's IV fit is unreliable but explicitly retains all vendor greeks
  (`src/ingest/adapters/thetadata.py:278-301`). GEX then consumes that retained
  gamma as valid.
- The remaining dominant contract (SPY 2019-11-08) reports IV `0.0029` and gamma
  `20.7558`, also an implausible solver output for research use.
- In 21/27 sessions, one contract supplies at least 50% of gross absolute GEX;
  in 19/27 it supplies at least 70%. Thus the flags are isolated solver-print
  contamination, not broad-chain positioning regimes.
- The worst SPY row is the 2026-06-26 729 put: gamma `198.1665`, null IV, and
  97.1% of gross absolute session GEX. It creates `-1.1205T` contract GEX.
- SPX examples are equally mechanical: the 2021-04-07 4080 call has 44 DTE,
  null IV, gamma `141.0597`, and 95.3% of gross absolute session GEX; the
  2024-07-03 5550 call has 16 DTE, null IV, gamma `6.4993`, and 94.0%.
- SPX 2026-07-02 is absent from the VPS store and remains missing; it must not be
  imputed.

### Disposition

All 27 MAD-10 observations are classified `ineligible-data-error` **under the
current vendor-gamma construction**. This does not authorize deleting entire
sessions. The defensible repair is to create a new signal input that recomputes
Black-Scholes gamma from stored valid IV under the pinned pricer, assigns no GEX
to contracts whose IV is null/invalid, reports excluded gamma-OI coverage, and
then regenerates the whole series—not only the flagged dates. This avoids a
result-conditioned date filter and makes gamma/IV internally consistent.

Because regenerated inputs will have new hashes, the eligibility freeze must be
rerun. The experiment hypothesis and gates remain locked; only the documented
data-quality correction changes.

## Week 1 exit condition

Week 1 governance and root-cause reconciliation are complete. The Week 2 feature
panel must use the internally recomputed-gamma series described above, not the
contaminated vendor-gamma series. The prospective holdout remains unscored.

## Corrected-series completion

The unattended VPS rebuild completed with zero failures:

| Symbol | Partitions | Corrected span | Output SHA-256 | MAD-10 GEX outliers |
|---|---:|---|---|---:|
| SPY | 2,392 | 2017-01-03–2026-07-10 | `cb8bafc7b0dbec996d1155d2ef1b8dc604a2f1e1bee1861e0fad4c9762c7c899` | 0 |
| SPX | 2,391 | 2017-01-03–2026-07-10 | `7a08b37797c424f2b7791dc5f9c23868c3d9eadf92ab11243d8287028b675a99` | 0 |

The corrected pre-outcome freeze is
`experiments/eligibility/EXP-2026-001-bsgamma-freeze.json`. It examined no
forward outcome fields. On dates overlapping the current bar files, SPY has
2,389 sessions and SPX 2,388; SPX 2026-07-02 remains missing. Median eligible-IV
OI coverage is 90.2% for SPY and 93.2% for SPX. The corrected normalized-GEX
ranges are `[-0.01846, 0.04292]` for SPY and `[-0.01222, 0.02554]` for SPX,
eliminating the solver-error spikes without deleting sessions.

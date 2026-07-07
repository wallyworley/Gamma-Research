# Quant Review: Gap Analysis Toward a Money-Making System

Date: 2026-07-04. Reviewer stance: skeptical quant analyst auditing the project end-to-end,
asking one question: *what is missing between "what exists today" and "a system that could
plausibly make money," ranked by expected value.* Research-grounded where the literature or
practitioner consensus is relevant.

## Verdict up front

The project has built the two hardest **boring** parts correctly: a point-in-time-honest,
full-universe options data pipeline (rare even at funds), and an evaluation harness that
cannot be fooled by beta (the permutation scorecard). What it does NOT yet have: history to
test on, the right first hypothesis wired up (volatility, not direction), a normalized
cross-sectional signal (the actual differentiator), calibration of its single biggest model
assumption (dealer sign), and second-order flow metrics (vanna/charm) where practitioner
mindshare now lives. There is also one existential ops gap: **the irreplaceable data store
has no backup.**

Likely end-state, done well: (a) a genuinely useful regime/risk overlay for discretionary
trading, (b) possibly a modest cross-sectional volatility/reversal edge in less-crowded
names, (c) index-timing alpha as the least likely outcome (most crowded trade). The
system's honesty guarantees a *cheap null result* if the edge isn't there - that is worth
more than it sounds.

---

## Tier 1 - Existential (without these, no edge can ever be proven)

### 1. The data store has no backup
`/opt/gamma-research/data` is the project's only asset that cannot be re-bought or
re-derived (snapshot-only source; OI/greeks are not backfillable). One VPS disk failure
resets the project to zero. **Fix now:** nightly compressed rsync/rclone of the store (and
`.env`) to Google Drive or local disk. ~150MB/session today; trivial cost. Highest
value-per-hour item in this document.

### 2. Cold-start: buy 2-5 years of EOD option history with OI
Waiting for the nightly collector alone means ~6-12 months before the first statistically
meaningful index-timing conclusion. Purchasable EOD chains with OI (and optionally 15:45
greeks) exist: [Cboe DataShop Option EOD Summary](https://datashop.cboe.com/option-eod-summary),
[end-of-day OI summary](https://datashop.cboe.com/end-of-day-options-summary), or budget
vendors (historicaloptiondata.com class, hundreds of dollars). The adapter architecture
makes ingest a one-day job (new `ChainAdapter` for the purchased format). **This is the
single highest-EV dollar spent on the project.** Backtests this quarter instead of next year.

*Evaluated and rejected (2026-07-04):* the free DoltHub `post-no-preference/options`
database (years of EOD chains). Schema verified live via the DoltHub SQL API: it carries
bid/ask, IV, and full greeks but **no open interest and no volume** - the exact weight GEX
runs on - so it cannot backfill GEX history. Secondary uses only: independent
cross-validation of our greeks/implied spot, and free IV-history features. DoltHub is also
not a fit as the store/backup itself: versioning is redundant for an immutable PIT store, a
row-store loses badly to parquet+DuckDB for analytical scans, and public repos would
republish licensed vendor data.

### 3. Test volatility prediction before direction (open finding F4)
The academically supported GEX effect is on **realized volatility and its distribution**,
not drift: dealer long-gamma hedging suppresses vol, short-gamma amplifies it (JFE / JEDC
results; see [FlashAlpha's summary of the mechanism](https://flashalpha.com/articles/what-is-gamma-exposure-gex-explained),
[SpotGamma](https://spotgamma.com/gamma-exposure-gex/)). Volatility is persistent and
forecastable (high signal-to-noise), so far less history is needed than for directional
tests. **Missing harness pieces:** realized-vol/range calculators from bars; a
vol-forecast scorecard (regression of next-day realized vol/range on normalized GEX,
*controlling for trailing vol* - does GEX add anything beyond vol clustering? benchmark
against a HAR-style baseline). This is the right first experiment, runnable on ~60
accumulated sessions.

---

## Tier 2 - The actual edge candidates

### 4. Cross-sectional breadth is the differentiator - but GEX is not normalized
Everyone watches SPX GEX (crowded, likely arbed). Almost nobody runs GEX across ~4,000
names daily, because they lack the data - this project has it. Fundamental law of active
management: IR ≈ IC·√breadth; thousands of small independent bets beat one index-timing
bet at equal skill. **Missing:** a comparability normalization (raw $GEX just ranks by
size) - divide by market cap, by ADV x spot, or by total option notional; then a
cross-sectional harness (daily rank IC, decile portfolios: do most-negative-GEX names show
higher next-day vol / distinct reversal behavior?). Small/mid-cap effects are uninvestable
for funds - precisely where personal-scale edges persist.

### 5. The dealer-sign assumption is the biggest model risk - calibrate, don't assume
Naive long-call/short-put is exactly what the field has abandoned:
[TradingVolatility retired naive GEX as "of limited use"](https://stocks.tradingvolatility.net/gexDashboard)
and moved to skew-adjusted MM-exposure models; [SqueezeMetrics' own guide](https://squeezemetrics.com/monitor/static/guide.pdf)
motivates flow-based refinement. Platforms disagree on GEX *sign* purely from positioning
assumptions. We cannot do trade classification on this tier (no aggressor side). Pragmatic
mitigations we CAN do: (a) **empirical sign calibration** - per bucket (index/large/small
cap), test whether +GEX actually precedes vol suppression; where it doesn't, the convention
is wrong there; (b) a **skew-adjusted convention** as a config alternative (OTM puts
customer-bought, OTM calls customer-sold, ATM ambiguous); (c) wire the **sign-convention
sensitivity sweep (F11)** into every scorecard: any conclusion that flips under the
alternate convention is dead on arrival.

### 6. Vanna and charm exposures - cheap to add, heavily used in practice
Second-order flows (delta sensitivity to IV and to time) drive the documented
OpEx/expiration effects ([MenthorQ on OpEx vanna/charm](https://menthorq.com/guide/why-markets-can-go-wild-after-options-expiration-vanna-and-charm-and-the-volatility-effect/),
[VannaCharm](https://vannacharm.com/blog/introducing-vannacharm)). We already store IV,
delta, vega, theta per contract and have a BS module: aggregate vanna/charm exposure is a
small metric addition. Pair with an **expiration calendar** feature (days-to-monthly-OpEx,
%-of-OI expiring) to enable the OpEx event study - the most documented calendar flow, and
testable with ~1 year of history.

### 7. The 0DTE blind spot - patch with volume-weighted gamma
0DTE is now the majority of SPX option volume and is structurally invisible to ANY
EOD-OI-based GEX (0DTE positions open and expire intraday; OI never captures them). We
already store per-contract same-day **volume**: add a volume-weighted "gamma flow" variant
alongside OI-GEX. It is a ~20-line metric on existing data and addresses the biggest
known-staleness critique of EOD GEX.

### 8. Intraday snapshots (pilot)
The plan has unlimited calls; the gamma flip moves with spot intraday and the documented
intraday reversal/momentum effects are, well, intraday. An hourly RTH capture of a pilot
set (SPX, NDX, RUT, SPY, QQQ, top-20 names) multiplies observations per calendar day
(partially offsetting the cold-start) and enables intraday-flip studies. OI is static
intraday but greeks/IV/spot reprice - which is the point. Infrastructure already exists
(second systemd timer + hour-tagged quote_ts).

---

## Tier 3 - Correctness and robustness gaps

9. **External cross-validation.** One-time: compare our SPX NetGEX/flip against published
   values (SpotGamma / TradingVolatility / MenthorQ free tiers) for a few sessions. Catches
   sign/scale errors no unit test can.
10. **Corporate actions policy.** Bars are split-adjusted; stored chains are frozen as
    captured. A multi-month backtest that joins chains to adjusted bars will mismatch
    across split boundaries (adjusted OCC roots like AVGO1 are the tell). Decide: adjust at
    read, or flag-and-exclude adjusted-root names near events.
11. **OI as-of stamping (F1, still open).** `prior_weekday` is not holiday-aware: Monday
    7/6's capture stamps `oi_asof=` Friday 7/3 (a market holiday). The holiday set already
    exists in `capture.py` - reuse it. Cosmetic today; wrong metadata in an event study.
12. **Store scalability.** ~4,000 small parquet files/day -> ~1M/year; time-series scans
    (the whole point of the store) will crawl. Add monthly per-symbol compaction or a
    DuckDB view. Disk: ~40GB/yr vs 60GB free - capacity planning by mid-2027.
13. **Holiday calendar hardcoded through 2027** - maintenance note.
14. **Signal layer is thin.** Only regime sign (long/flat/short). Add: distance-to-flip as
    a continuous signal (likely more informative than sign), GEX-ratio percentile
    conditioning (helper exists, unused by any signal), and trend-interaction ("in -GEX
    follow trend, in +GEX fade" is a *conditional* rule - the practitioner playbook - not a
    standalone).
15. **Backtester is single-asset, underlying-only.** The cleanest GEX expressions are vol
    trades (straddles/condors). Historical option *price* bars ARE available (no OI needed
    to price a chosen structure), so a multi-leg option backtest is feasible later - keep it
    on the engine roadmap.

---

## What is already right (do not break)

Point-in-time integrity end-to-end; OCC-root-correct keys (full index book, no silent OI
loss); fail-safe capture (skip, never mislabel); provenance columns (`_spot_source`,
`_greek_source`); the permutation/exposure-matched evaluation harness; adversarial review
culture; vendor-swappable adapters; full-universe breadth. These are exactly the parts most
retail quant projects get wrong first.

## Sequenced plan

**This week:** store backup job; decide the history purchase; realized-vol + vol-forecast
harness; volume-GEX + normalized-GEX metrics; one-day external SPX cross-check.
**30 days:** vanna/charm + OpEx calendar; cross-sectional rank harness (IC, deciles);
intraday pilot on the indices; sign-convention sweep wired into every scorecard.
**60-90 days:** first honest verdicts - does GEX add vol-forecast value (index and
cross-section)? OpEx event study. Only then decide whether direction-timing is worth
pursuing at all.

## Sources

- [FlashAlpha - What Is Gamma Exposure (GEX)? Dealer Hedging Explained](https://flashalpha.com/articles/what-is-gamma-exposure-gex-explained)
- [SpotGamma - Gamma Exposure (GEX)](https://spotgamma.com/gamma-exposure-gex/)
- [SqueezeMetrics - GEX guide (PDF)](https://squeezemetrics.com/monitor/static/guide.pdf)
- [TradingVolatility - GEX dashboard / model notes](https://stocks.tradingvolatility.net/gexDashboard)
- [MenthorQ - OpEx, Vanna and Charm](https://menthorq.com/guide/why-markets-can-go-wild-after-options-expiration-vanna-and-charm-and-the-volatility-effect/)
- [VannaCharm - Dealer Gamma, Vanna, Charm exposure](https://vannacharm.com/blog/introducing-vannacharm)
- [Cboe DataShop - Option EOD Summary](https://datashop.cboe.com/option-eod-summary)
- [Cboe DataShop - EOD Open Interest Summary](https://datashop.cboe.com/end-of-day-options-summary)

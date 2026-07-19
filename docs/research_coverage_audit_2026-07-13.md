# Options Research Coverage and Strategy-Gap Audit

Date: 2026-07-13
Scope: supplied repository and saved artifacts only; outside facts are explicitly labeled.
Artifact notation: `path#key` identifies a JSON key; code citations use `path:line`.

## 1. Executive verdict

The program is broad enough to support a narrow conclusion—EOD OI-based normalized GEX has not yet shown robust incremental next-day volatility-forecast value in SPX or SPY—but not broad enough to judge GEX generally, directional strategies, or option profitability. The repository implements many metrics and honest evaluation components, yet the saved research is concentrated in one forecast family, one horizon, two underlyings, three realized-volatility targets, and uncertain dealer-sign constructions. The strongest supplied result is negative: SPX is null under two conventions, while SPY's small naive-sign effect shrinks to one in-sample target and fails temporal cross-fit under empirically estimated signs. The largest validity gap is an untouched, walk-forward test of observable signal variants against richer price/IV/calendar controls; the largest adjacent strategy gap is change/term-bucket/volume-GEX across the already-backfilled 18-symbol panel. The option-chain store has bids/asks, so simple EOD multi-leg mark-to-next-EOD research may be feasible, but no option lifecycle, executable-price, exercise/settlement, or portfolio engine has been demonstrated; therefore no option structure has been tested as a tradable strategy. Public EOD chains cannot identify dealer inventory, opening versus closing, complex-order intent, or the net same-day 0DTE book, so those must remain latent variables rather than labels. Do **not** conclude that “GEX works,” that “GEX does not work,” that positive gamma causes lower volatility, that any underlying rule is profitable, or that a straddle/condor/skew trade follows from the regressions.

## 2. Tested-coverage matrix

| Lane | Hypothesis/strategy | Status | Evidence | Data coverage | Verdict supported |
|---|---|---|---|---|---|
| Scope | Offline research, no live execution | implemented | `README.md:3-8`; `docs/phase_1_plan.md:25-31`; simulator timing in `src/backtest/engine.py:1-20` | Local research stack | Yes: research-only |
| Direction | MA momentum underlying timing | tested, but only harness demo | `HANDOFF.md:53-60`; scorecard machinery `src/eval/harness.py:121-188` | SPY, 50-day demo | No timing skill beyond exposure; not a GEX test |
| Direction | GEX regime long/flat or long/short | implemented, untested | `src/signals/rules.py:42-55` | Chain history exists | No empirical verdict |
| Direction | Trend-follow in -GEX / fade in +GEX | implemented, untested | `src/signals/rules.py:139-171` | Chain + OHLC available | No empirical verdict |
| Volatility | EOD normalized GEX predicts next-day range, absolute return, Parkinson variance beyond HAR | tested inadequately | `scripts/vol_forecast_experiment.py:1-28`; `data/analysis/vol_forecast_results_SPX.json#targets`; `...SPY.json#targets` | SPX/SPY, 2017-01–2026-07, 2,388 usable rows each | SPX null for this specification; naive SPY positive is convention-sensitive |
| Volatility | Empirically signed SPY GEX adds next-day forecast value | tested inadequately | `data/calibration/scorecard_SPY.json#targets.*.arms.empirical`; temporal cross-fits at `#targets.*.oos` | SPY only; sign map from 245 sampled sessions | Whole-sample abs-return survives weakly; all temporal cross-fits fail, so no robust effect |
| Horizon | Overnight, open-close, 2–5 day, weekly, monthly/OpEx | untested | Forecast target is strictly t+1 (`src/eval/volatility.py:318-360`); EOD signal fills next open for underlying backtests (`src/backtest/engine.py:80-86`) | OHLC permits several decompositions; chains permit EOD horizons | None |
| Signal form | Level/sign of normalized net GEX | tested | Experiment source `scripts/vol_forecast_experiment.py:1-28` | SPX/SPY | Narrow null/fragile result only |
| Signal form | Distance to gamma flip | implemented, untested | `src/signals/rules.py:67-109`; grid limitation `src/metrics/gex.py:135-141` | Historical chains | None |
| Signal form | Percentile conditioning | implemented, untested | `src/signals/rules.py:112-136` | Historical series | None |
| Signal form | Change, acceleration, DTE/moneyness concentration, call/put asymmetry | mostly untested | Chain metrics exist, but no saved scorecards | Backfilled chains support them | None |
| Flow | Same-day volume-GEX companion | implemented, untested | `src/metrics/flow.py:1-20`; named next step `HANDOFF.md:195-197` | Volume exists in chains | None; volume is not inventory |
| Greeks | DEX, vanna, charm | implemented, untested | `HANDOFF.md:71-75`; source modules `src/metrics/dex.py`, `src/metrics/vanna_charm.py` | Historical chain fields | None |
| Expiry | OpEx/calendar features | implemented, untested | `HANDOFF.md:71-75`; `src/metrics/expiry.py` | Calendar derivable | None |
| Levels | ZeroGEX, OI walls, transition proxies, ratios, grade proxy | implemented, untested | `src/metrics/gex.py:69-175`; terms/proxy boundary `docs/reddit_gamma_strategy_terms.md:257-268` | Historical chains | None |
| Regimes | Pre-COVID, 2020–21, 2022–26; positive/negative sign subsets | tested post hoc/inadequately | `HANDOFF.md:138-148`; artifact `data/analysis/vol_forecast_results_SPY.json#targets.*.subperiods` | SPY/SPX | Descriptive instability, not independent confirmation |
| Cross-asset | SPX versus SPY | tested inadequately | Both result JSONs | Two correlated S&P exposures | Construction difference exists; cause is unidentified |
| Cross-asset | QQQ/IWM/RUT/XSP and single names | data prepared, untested | Backfill inventory `HANDOFF.md:99-110`; local series for six symbols | 18 symbols backfilled; six precomputed series local | None |
| Cross-sectional | Daily ranks/long-short portfolios | untested | Normalization exists (`src/metrics/flow.py:15-20`) but no artifact | 18-symbol historical panel; larger universe only accumulates nightly | None; current 18-name universe is selected, not survivor-free breadth |
| Option expression | Straddles, strangles, calendars, flies/condors, skew, verticals | untested | No option P&L engine or result artifact | ThetaData chains have EOD NBBO per `HANDOFF.md:93-95` | No profitability conclusion |
| Dealer sign | Two assumed conventions | tested | Sweep design `src/eval/sensitivity.py:1-17`; vol artifacts | SPX/SPY | SPY conclusion flips; SPX remains null |
| Dealer sign | Quote-rule empirical bucket map | tested inadequately | `data/calibration/sign_map_SPY.json#stable_counts,#convention_agreement_gamma_oi_weighted`; `HANDOFF.md:151-191` | SPY; 245 sampled sessions, <=60 DTE | Neither convention approximates measured flow well; flow is not inventory |
| Controls | HAR volatility baseline | tested | `src/eval/volatility.py:175-191,318-360` | SPX/SPY | GEX adds little/no in-sample explanatory power |
| Controls | IV level/skew, liquidity, option volume, trend, calendar/events, market return | untested | Absent from saved model specification | Some need derived IV/skew/calendar; event data partly missing | Current incremental claim is only “beyond HAR,” not beyond options/market controls |
| Validation | Moving-block bootstrap | tested | `src/eval/volatility.py:246-266` | Full sample | Addresses local dependence imperfectly; does not repair model selection |
| Validation | Untouched holdout, walk-forward, purged CV, FDR/selection adjustment | untested | No supplied artifact | Feasible now | No discovery-adjusted evidence |
| Portfolio risk | Multi-symbol aggregation, beta/vega/gamma, ES, capacity, crash behavior | untested | Underlying backtest reports simple stats only (`src/backtest/engine.py:53-60`) | Extension required | None |

The local result files are present, so the central numeric claims are reproducible from supplied artifacts. The underlying 8.5GB VPS chain store is not local; only selected series and calibration trades are supplied, so chain-to-series reconstruction for all reported dates cannot be independently rerun here.

## 3. Trial and conclusion ledger

### Material trial ledger

1. **Harness demo:** one SPY MA-momentum underlying rule, 50-day lookback, versus buy-and-hold, sign-safe permutations, exposure-matched random controls, and bootstrap (`HANDOFF.md:53-60`). This validates plumbing, not GEX.
2. **Primary vol experiment:** 2 symbols × 3 targets × 2 assumed dealer-sign conventions = **12 primary cells**, all on the same 2017–2026 sample. The targets are range, absolute return, and Parkinson; the model is HAR plus one contemporaneous EOD normalized-GEX regressor.
3. **Diagnostic expansion:** sign subsamples (positive/negative) and three time partitions appear in the saved output/documentation. Conservatively, 12 cells × 2 sign subsets plus 12 × 3 periods adds **60 descriptive looks**, before any informal symbol/data-source comparisons. These are correlated, but still researcher degrees of freedom.
4. **Empirical-sign calibration:** 245 SPY sessions selected at two/month plus top-20 absolute-GEX days; 2 option types × 5 moneyness bins × 3 DTE bins = **30 bucket signs**, estimated full/early/late, plus regular-trade exclusion and gamma-weighted alternatives. Selection includes extreme signal days and is not a random calendar sample (`HANDOFF.md:155-180`).
5. **Three-arm SPY rerun:** 3 targets × 3 sign arms = **9 whole-sample cells**, plus 3 targets × 2 temporal map cross-fits = **6 OOS cells** (`data/calibration/scorecard_SPY.json#targets`). Only the cross-fits deserve an OOS label; the 2022 split was introduced after earlier full-sample work and is not an untouched terminal holdout.

Undocumented or weakly documented degrees of freedom include target choice, normalization denominator, linear functional form, target transforms, bootstrap block length 10, 5% gate, period boundaries, calibration session sampling, moneyness/DTE cut points, fallback convention for unstable/>60DTE cells, and focus on SPY after seeing SPX/SPY divergence. Configuration hashes aid reproducibility but do not make these choices pre-registered. There is no family-wise/FDR control, reality-check/deflated-Sharpe equivalent, or immutable trial registry.

### Conclusion ledger

| Claim | Rating | Strongest valid conclusion |
|---|---|---|
| Repo is research-only and does not execute live trades | supported | Code and docs consistently implement only collection, analytics, and simulation. |
| Underlying scorecard controls beta/exposure better than naked return comparisons | supported | Sign-preserving gross permutation is primary (`src/eval/harness.py:124-159`). |
| SPX normalized OI-GEX is null for next-day realized-vol proxies | supported, narrow | Under the exact two conventions, three targets, HAR baseline, and supplied period, incremental adjusted R² is approximately zero/negative. It does not reject other signals/horizons. |
| Naive-sign SPY GEX predicts lower next-day volatility | premature | In-sample effect exists, but it is one of many correlated looks, convention-sensitive, and not a tradable test. |
| SPY effect is not a COVID artifact and reflects the 0DTE blind spot | overstated | Subperiod decay is descriptive; attributing it to 0DTE is an untested mechanism and could reflect regime, vendor, liquidity, IV, or selection changes. |
| Empirical dealer signs invalidate the SPY finding | supported with caveat | They materially weaken it and temporal cross-fits fail; quote-rule flow is still an error-prone proxy for inventory, so “weak/absent under measured signs” is appropriate. |
| Neither assumed convention matches measured customer flow | supported for sampled SPY flow buckets | Weighted agreement is 50.86% and 33.41% (`sign_map_SPY.json#convention_agreement_gamma_oi_weighted`); this is not direct dealer-position truth. |
| Gamma metrics/levels/DEX/vanna/charm/expiry signals have been evaluated | not reproducible / false if implied | They are implemented and unit-tested, not backed by saved empirical scorecards. |
| A GEX strategy is profitable | not reproducible | No supplied GEX underlying strategy scorecard and no option-structure P&L. |
| GEX has causal market impact | overstated | The design is predictive association; gamma, spot, IV, OI, and volatility are endogenous. |

## 4. Ranked gap register

Scores are ordinal (1 low, 5 high); cost 5 means expensive. They guide ordering rather than form a synthetic total.

| Rank | Gap | Class | Why it matters | Current-data feasibility | Info | Plaus. | Ready | Cost | Risk if ignored |
|---:|---|:---:|---|---|:---:|:---:|:---:|:---:|---|
| 1 | Locked walk-forward model with IV/skew, volume, trend, liquidity and calendar controls | A | Tests whether GEX adds information rather than repackaging known state | High after feature derivation | 5 | 5 | 4 | 3 | False positive attribution |
| 2 | OI-GEX level vs Δ/acceleration vs volume-GEX, DTE buckets | B | Directly discriminates stale inventory from same-day activity and persistent level from innovation | High on stored chains | 5 | 4 | 5 | 3 | Abandoning/accepting wrong signal form |
| 3 | Multi-horizon/timing decomposition | B | EOD construction may affect overnight differently from open-close or 2–5 day variance | High with OHLC | 5 | 4 | 5 | 2 | Horizon mismatch creates false null |
| 4 | Breadth and cross-sectional ranks with fixed historical universe rules | D | Two correlated S&P instruments cannot establish generality | Moderate: 18 names now; broad store too short | 5 | 4 | 3 | Symbol cherry-picking |
| 5 | Untouched test set and multiplicity governance | A | Current positives arose amid many implicit trials | High | 5 | 5 | 5 | Research overfit |
| 6 | Simple executable option-structure engine and delta-hedged straddle test | C | Separates forecast utility from net option P&L/VRP | Moderate; NBBO exists, lifecycle logic absent | 5 | 4 | 3 | Mistaking R² for trade economics |
| 7 | Pre/post daily-expiry structural regime and OpEx/event interactions | D | Positioning mechanism plausibly changes with expiry mix | High for expiry; macro/earnings calendar extension needed | 4 | 4 | 4 | Pooling unstable regimes |
| 8 | Portfolio aggregation, factor exposure, ES/drawdown/capacity | A | Required before any capital claim | Moderate; portfolio layer absent | 4 | 5 | 2 | Hidden beta/vega/crash concentration |
| 9 | Index-versus-ETF paired construction | B | SPX/SPY divergence may identify settlement, user base, normalization or data differences | High for SPX/SPY, later QQQ/NDX constrained | 4 | 4 | 4 | Storytelling around unexplained divergence |
| 10 | IV-change, skew-change, VRP and tail-probability targets | B | GEX may affect option repricing rather than next-day spot range | Moderate; derive constant-maturity IV/skew and forward realized variance | 4 | 4 | 3 | Forecast-object false null |
| 11 | Placebo leads/lags and sign/random-bucket synthetic nulls | A | Detects timing leakage and flexible sign-map fitting | High | 4 | 5 | 5 | Spurious association survives |
| 12 | AM/PM settlement, adjusted contracts, dividends/rates/early exercise | D | Can distort index/equity comparison and option P&L | Partial; root exists, full lifecycle metadata missing | 4 | 4 | 2 | Biased structure returns |
| 13 | Open/close and participant-type inventory proxy | X | Directly addresses dealer-sign uncertainty | Not with current public chains; purchasable exchange slice is partial-market flow | 5 | 4 | 1 | Latent sign remains unresolved |
| 14 | Intraday pin/level-crossing mechanics | X | Claimed mechanisms operate intraday near strikes | EOD snapshots/OHLC cannot identify path or hedge response | 4 | 3 | 1 | Invalid EOD inference |
| 15 | Dispersion/correlation | E | Legitimate family but requires index/constituent IV and synchronized executable legs | Material data/engine extension | 3 | 3 | 1 | Opportunity cost if pursued early |

## 5. Top five pre-registered experiments

All transformations, exclusions and thresholds below should be written to a machine-readable manifest and hashed before the untouched test is opened. Use only observations whose chain fields are as-of date *t*; forecast starts after the t close. Do not tune on the test segment.

### 1. Incremental observable-feature horse race

- **Hypothesis/sign:** conditional on lagged realized volatility, ATM IV, 25-delta skew, option volume/notional, liquidity, trend and calendar, higher signed normalized dealer gamma predicts lower next-day realized volatility (coefficient < 0). The primary arm uses the frozen empirical SPY sign map; assumed conventions are sensitivity only.
- **Unit/universe:** symbol-session for SPY and SPX; primary inference is separate by symbol, not pooled.
- **Signal:** `G_t = net_gex_empirical,t / sum(OI_t * 100 * spot_t)`, winsorized using training 0.5/99.5 percentiles. OI must satisfy `oi_asof_date <= t`; no backfill from t+1. Freeze fallback signs before scoring.
- **Target/horizon:** primary `abs(log(C_{t+1}/C_t))`; secondary Parkinson range. One primary target prevents another three-target fishing expedition.
- **Controls:** HAR daily/weekly/monthly; training-standardized ATM 30D IV, 25D put-call skew, log option volume/notional, quoted-spread summary, 5/20-day return, day-of-week, OpEx distance, month-end, lagged market return.
- **Validation:** expanding annual walk-forward beginning 2019; reserve 2025-01-01 onward untouched. If this period has already been inspected, reserve newly accumulated 2026-07-13 onward and accept the wait.
- **Metric/gate:** primary mean OOS squared-error reduction versus controls-only must be >1% and Diebold-Mariano/block-bootstrap one-sided p <= .05; coefficient negative in >=70% of annual folds. Also require calibration-slope 0.8–1.2 for predicted target.
- **Costs:** forecast-only; explicitly no profitability claim.
- **Sensitivity:** two assumed conventions, no-fallback cells, DTE<=60, vendor-overlap sessions, exclude top 0.5% signal, SPX roots split AM/PM.
- **Placebo:** G_{t+1} predicting t (must not pass); 100 block-permuted G series with observed improvement above the 95th percentile.
- **Data:** chains/OHLC exist; IV/skew/liquidity derivation required.
- **Kill/defer:** kill the EOD OI-GEX level lane if test loss does not improve by 1% or sign stability fails in both symbols.

### 2. Level versus innovation versus volume/DTE decomposition

- **Hypothesis/sign:** if stale OI captures persistent inventory, level GEX dominates; if same-day activity matters, volume-GEX or ΔGEX dominates after the daily-expiry expansion. Expected signs: level/Δ/volume signed gamma coefficients <0 for next-day volatility; no directional sign is pre-claimed for acceleration.
- **Unit/universe:** sessions for SPY, SPX, QQQ, IWM, XSP, RUT where >=500 observations; symbol fixed effects only in secondary pooled model.
- **Signals:** training-z-scored `G_t`; `ΔG_t=G_t-G_{t-1}`; `Δ²G_t`; volume-GEX normalized by total option volume notional; each split into 0DTE, 1–7, 8–30, 31–60, >60 DTE. Missing buckets are zero plus a missing flag, never forward-filled.
- **Target:** next-day absolute return; secondary 2–5-day sum of squared close returns with non-overlapping weekly origins.
- **Controls/validation:** controls and walk-forward protocol from Experiment 1. Group-lasso/ridge penalty selected only inside training; untouched 2025+ or future-only holdout.
- **Metric/gate:** nested-model OOS loss improves >=2%; selected family stable in >=4 of 6 symbols with same signed direction; Benjamini-Hochberg q<=.10 across predeclared family tests.
- **Costs:** forecast-only.
- **Sensitivity:** convention, normalization by option notional versus dollar ADV, exclude 0DTE, pre/post 2022 interaction, vendor overlap.
- **Placebo:** shift volume one session backward/forward and randomize DTE labels within session.
- **Data:** historical chains exist for named symbols; ADV and feature builder needed.
- **Kill/defer:** defer all fine-grained GEX engineering if no family clears OOS loss/FDR gates.

### 3. Timing and horizon discrimination

- **Hypothesis/sign:** EOD signed GEX has a stronger negative association with next session open-to-close range than with close-to-open gaps; any effect decays over 2–5 days.
- **Unit/universe:** SPY/SPX session, then replication symbols.
- **Signal:** the single winning frozen signal from Experiment 1 or, if none, empirical normalized level only—no new tuning.
- **Targets:** `|log(O_{t+1}/C_t)|`, `log(H_{t+1}/L_{t+1})`, `|log(C_{t+1}/O_{t+1})|`, and non-overlapping 5-day realized variance. Primary contrast is open-close minus overnight standardized coefficient.
- **Controls:** component-matched HAR measures, IV, skew, trend, calendar, gap history.
- **Validation:** same untouched dates; overlapping multi-day labels use purged folds with five-session embargo.
- **Metric/gate:** primary coefficient contrast <0 with block-bootstrap p<=.05 and correct ordering in both SPY and SPX; otherwise no timing claim.
- **Costs:** forecast-only; an underlying trade is a later experiment.
- **Sensitivity:** close timestamp/as-of cut, holidays, OpEx, large-gap exclusions.
- **Placebo:** contemporaneous t target and lead signal checks.
- **Data:** OHLC and signals exist.
- **Kill/defer:** if components are indistinguishable or signs conflict, stop mechanism-specific timing narratives.

### 4. Fixed-universe cross-sectional replication

- **Hypothesis/sign:** unusually high gamma intensity relative to a symbol’s own history ranks into lower next-day residual volatility cross-sectionally.
- **Unit/universe:** daily ranks among the 18 Phase-1 names, eligible only after 252 prior valid sessions; freeze this list and report index/ETF/single-name strata. Do not call it market-wide.
- **Signal:** within-symbol trailing-252 percentile of the Experiment-1 winning observable signal; rank daily after lag/as-of validation.
- **Target:** next-day absolute residual return after market/sector beta and own HAR forecast.
- **Controls:** liquidity, IV/skew, size/ADV, earnings flag for single names, symbol and date effects.
- **Validation:** expanding walk-forward; years are blocks; final two years untouched. No survivorship claim because universe was selected ex post—replication on nightly frozen membership must follow.
- **Metric/gate:** daily Spearman IC mean <= -0.03, block-bootstrap p<=.05, negative in each broad stratum and >=60% of years.
- **Costs:** forecast-only. A rank portfolio must later include borrow/turnover.
- **Sensitivity:** leave-one-symbol-out, winsorization, index exclusion, equal versus liquidity weights.
- **Placebo:** permute ranks within date and lead target one day.
- **Data:** chains exist; sector/earnings/history metadata extension needed.
- **Kill/defer:** fail if effect is driven by one symbol/stratum or disappears with IV/liquidity controls.

### 5. Forecast-to-trade bridge: one delta-hedged straddle

- **Hypothesis/sign:** only if Experiment 1 passes, the frozen forecast identifies mispricing in next-session delta-hedged ATM straddle returns: high predicted realized-versus-implied spread yields higher long-straddle net P&L.
- **Unit/universe:** SPY first; one ATM expiry with 20–40 calendar DTE, minimum bid >0, relative spread <=15%, both legs present at t and t+1.
- **Signal/trade:** at t EOD select strike closest to spot and expiry closest to 30D. Buy one call + one put at t ask when predicted next-day variance minus IV-implied daily variance exceeds a training-fixed threshold; sell at t+1 bid. Hedge entry delta with shares at next open and close hedge at next close. No same-close fills.
- **Target/horizon:** net dollar P&L and return on maximum deployed premium over one session.
- **Baseline:** identical straddle selected without GEX using controls-only forecast; unconditional same-frequency straddle; no-trade.
- **Validation:** walk-forward threshold fixed by training utility; final 2025+ or future period untouched.
- **Metric/gate:** GEX arm improves mean net P&L over controls-only by >=10% of median entry spread cost, block-bootstrap p<=.05, positive expected shortfall-adjusted utility, and no single year supplies >50% of P&L.
- **Execution:** entry at displayed ask, exit bid, stock half-spread/slippage, commissions per contract/share, no quote interpolation. Reject missing/stale/crossed quotes. This is deliberately punitive.
- **Sensitivity:** midpoint-plus-half-spread (not midpoint alone), 15–45 DTE, ±1 strike, early-exercise/dividend exclusions, adjusted contracts, AM/PM irrelevant for SPY but must be encoded before index use.
- **Placebo:** reverse GEX ranking and random eligible entry days matched on IV.
- **Data:** EOD NBBO/chains exist; option lifecycle, corporate actions and hedging engine do not yet exist.
- **Kill/defer:** do not expand to condors/calendars if the simple straddle cannot beat controls-only after spread and hedge costs.

## 6. Strategies that remain unchecked

### Testable now

- **Underlying volatility timing:** scale next-open SPY/SPX exposure inversely with a frozen GEX-augmented volatility forecast; exit next close. Requires OHLC and the validated forecast; honest expression is volatility targeting, not directional alpha.
- **Direction interactions:** next-open to next-close underlying long/short based on frozen trend × gamma regime. Requires bars and chain signals; borrow must be charged for shorts.
- **Level/change/term-bucket forecasts:** next-day absolute return/range from OI-GEX, ΔGEX, volume-GEX and DTE buckets. Forecast test only.
- **Index/ETF relative forecast:** compare SPX versus SPY residual-vol forecast errors under matched constructions; no direct arbitrage claim.
- **Cross-sectional residual-vol ranks:** feasible on the fixed 18-name panel, with the selection limitation stated.

### Testable after a small data/engine extension

- **One-session delta-hedged ATM straddle:** needs contract matching across dates, bid/ask execution, stock hedge, commissions and corporate-action filters.
- **Skew change:** 25-delta put-call constant-maturity skew t to t+1; needs robust interpolation and quote-quality filters. A tradable expression is a delta-hedged risk reversal only after leg-level execution exists.
- **VRP:** constant-maturity implied variance minus forward realized variance; needs variance interpolation. Expression is delta-hedged straddle/variance proxy, not naked option return.
- **Vertical/defined-risk direction:** frozen direction forecast, 30–45D vertical entered ask/ask and exited bid/bid next EOD; needs lifecycle and assignment rules.
- **Calendar term-structure relative value:** same strike/delta, near versus far expiries; requires synchronized legs and vega/delta hedging.

### Requires materially new data

- **Intraday gamma-flip crossings/pinning:** intraday chains, NBBO/trades, underlying bars, quote timestamps and executable simulation.
- **Participant/open-close dealer-flow research:** exchange participant type plus buy/sell/open/close. Cboe’s Open-Close product explicitly supplies these fields on its four exchanges, but it is not whole-market inventory (outside source: https://datashop.cboe.com/cboe-options-open-close-volume-summary).
- **Complex-order intent:** complex order book/linkage or reliable multi-leg identifiers; OPRA leg prints alone are inadequate.
- **Dispersion/correlation:** synchronized index and constituent option surfaces, corporate actions, weights, executable multi-leg prices and portfolio margin.
- **Event-conditioned single names:** clean point-in-time earnings/macro/rebalance calendars.

### Not identifiable enough to pursue

- “True dealer inventory” reconstructed from public OI plus quote rule.
- Same-day 0DTE dealer book inferred from unsigned aggregate volume.
- Causal dealer-hedging impact from EOD associations without an identification strategy.
- Exact proprietary GammaEdge levels/grade without published definitions.
- Intraday support/resistance or pin fills from EOD high/low alone.

Cboe reports that 0DTE is 59% of SPX volume and defines it as expiring the same day; that makes an EOD-OI mechanism mismatch plausible, not proven (outside source: https://www.cboe.com/tradable-products/0dte). Cboe’s own participant-aware research also finds balanced customer activity and small net market-maker gamma, underscoring why raw volume cannot be equated with net inventory (outside source: https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options).

## 7. Stop-doing list

- Stop rerunning the same SPY next-day regression with more thresholds/subperiods. Revisit only after a locked holdout and multiplicity plan.
- Stop treating helper unit tests as research evidence. Require one immutable result manifest per empirical claim.
- Stop global sign-convention sweeps as a substitute for identification. Use them as sensitivity; buy participant/open-close data only after observable-signal OOS value survives.
- Stop adding exotic structures (“try condors/calendars”) before the one-straddle engine passes quote/lifecycle/cost validation.
- Stop midpoint-only option backtests. Revisit after bid/ask, stale-quote, leg synchrony and assignment/settlement logic.
- Stop interpreting SPX-versus-SPY divergence as institutional versus retail behavior. First equalize data eras, root/settlement treatment, normalization, IV/skew and liquidity controls.
- Stop broad nightly-universe cross-sectional claims until membership snapshots, delistings, corporate actions and a minimum history rule exist.
- Stop intraday gamma-wall/pinning studies with EOD data. Revisit with timestamped intraday chains and underlying paths.
- Stop fitting ever finer dealer-sign buckets on 245 selected sessions. Revisit with denser random sampling, frozen bins, cross-symbol replication or exchange open-close data.
- Stop causal language. Revisit only with a credible instrument/natural experiment and explicit causal estimand.

## 8. 30/60/90-day research sequence

### Days 0–30: validity before breadth

1. Create an append-only trial registry: manifest hash, hypothesis, primary target, universe, dates, features, exclusions, metric and gate.
2. Inventory every chain partition and produce symbol/date/root/quote-quality/field-coverage tables; freeze the eligible historical universe.
3. Build constant-maturity ATM IV, 25D skew, spread/liquidity, option-volume, DTE-bucket, OpEx and trend features with explicit as-of rules.
4. Reproduce existing SPX/SPY artifacts from chains where accessible; reconcile the unexplained extreme empirical-series observations before use.
5. Lock Experiment 1 and its untouched segment, then run once.

**Day-30 gate:** proceed only if chain-to-feature audits pass, no leakage/placebo failure exists, and the GEX model meets the predeclared OOS loss/sign gate. If it fails both symbols, stop EOD OI-GEX level work and retain it only as a control.

### Days 31–60: discriminate the mechanism

1. Run Experiment 2 (level/change/volume/DTE), with FDR control.
2. Run Experiment 3 (overnight/open-close/weekly) using only the surviving frozen signal.
3. Run paired SPX/SPY construction checks: roots, AM/PM, expiry distribution, notional denominator, IV/skew/liquidity, common dates.
4. Begin the fixed 18-name feature panel and document selection/survivorship limitations.
5. In parallel, specify—but do not yet generalize—the minimum option P&L engine.

**Day-60 gate:** at least one observable signal family must improve OOS loss, pass placebo/FDR checks, and show stable sign in more than one instrument. Otherwise defer GEX prediction and do not purchase open-close data.

### Days 61–90: economic translation

1. Run Experiment 4 cross-sectional replication.
2. Implement and validate contract lifecycle, NBBO execution, stock hedge, costs, corporate actions, settlement and assignment rules.
3. Run Experiment 5 once on the untouched segment.
4. Add portfolio aggregation: beta/delta/vega/gamma, turnover, concentration, max drawdown, expected shortfall, liquidity/capacity and missing-data attribution.
5. Only if sign sensitivity remains the dominant uncertainty *and* OOS economic value survives, price a small participant/open-close slice with a written replication decision rule.

**Day-90 go/no-go:** go only if the forecast beats controls OOS and the simple structure adds net P&L after punitive execution with acceptable tail concentration. No-go if improvement is in-sample only, convention-dependent, placebo-sensitive, single-symbol, or consumed by spread/hedge costs.

## 9. Honest bottom line

With my own budget, I would next test the **observable GEX innovation family—OI level versus ΔGEX versus volume-GEX split by DTE—inside a locked walk-forward model containing IV/skew, liquidity, trend and calendar controls**. It offers the most information because it can distinguish stale inventory, same-day activity and generic volatility-state explanations using data already owned. I would defer dealer-flow purchases, proprietary-level mimicry, dispersion, and multi-leg strategy proliferation until this family survives an untouched test. I would abandon GEX as a useful standalone EOD predictor if no predeclared observable construction improves OOS forecast loss after controls in at least two instruments, if placebo/lead tests perform similarly, or if any surviving forecast cannot improve a simple delta-hedged straddle over the controls-only model after executable costs. GEX could still remain a descriptive risk feature, but not a research alpha lane.

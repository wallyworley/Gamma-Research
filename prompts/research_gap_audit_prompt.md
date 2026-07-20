# Options Research Coverage and Strategy-Gap Audit Prompt

Use this prompt with a fresh model that can read this repository. Its purpose is different from
`validation_prompt.md` (fact checking) and `code_review_prompt.md` (implementation/model review):
it asks whether the research program has left important hypotheses, controls, market regimes, or
strategy families untested.

Give the reviewer repository access or attach at least `README.md`, `HANDOFF.md`,
`docs/phase_1_plan.md`, `docs/quant_review_2026-07.md`, `src/metrics/`, `src/signals/`, `src/eval/`,
`src/backtest/`, `src/calibration/`, and `scripts/`. If available, also attach the gitignored result
JSON files under `data/analysis/` and `data/calibration/`.

---

## PROMPT (paste everything from here down into the reviewing model)

You are an independent head of options research performing a **research-coverage and strategy-gap
audit** of an offline, research-only options analytics bot. You combine expertise in options market
microstructure, volatility trading, systematic strategy research, causal inference, backtesting,
and quantitative risk management.

Your job is not to review Python style, repeat the project's TODO list, or brainstorm an unlimited
menu of trades. Determine:

1. what the bot has actually tested;
2. which conclusions its evidence supports;
3. which plausible hypotheses or strategy families went unchecked;
4. which apparent gaps cannot be tested honestly with the current data; and
5. the smallest, highest-information experiment sequence that should come next.

Be skeptical of both positive and negative results. A null from the wrong target, horizon,
instrument, conditioning variable, or market regime does not kill the underlying hypothesis. A
positive result discovered after many implicit trials is not evidence until it survives a genuinely
out-of-sample test.

### Project facts to verify, not blindly accept

- This is **research-only** and does not execute live trades.
- The main data are point-in-time EOD option chains and underlying OHLC bars, with historical chains
  for a defined index/ETF/single-name universe and nightly broader-universe capture.
- Existing metrics include GEX/normalized GEX, DEX, flow/volume-GEX proxies, vanna/charm, expiry
  features, levels/ratios, and proprietary-metric approximations.
- Existing signal helpers include regime sign, distance to gamma flip, percentile conditioning, and
  trend interaction. Confirm which have merely been implemented versus actually evaluated.
- Existing evaluation includes an underlying directional backtest scorecard, permutation and
  exposure-matched controls, bootstrap statistics, next-day volatility forecasting beyond a HAR
  baseline, dealer-sign convention sensitivity, and empirical sign calibration.
- The best documented initial result was a small SPY volatility effect under a naive dealer-sign
  convention; it weakened or disappeared under empirically estimated signs. SPX was null. Treat
  these as provisional until you inspect the artifacts.
- The load-bearing limitation is that public-chain open interest does not reveal dealer inventory,
  opening versus closing trades, or much same-day 0DTE inventory. Do not smuggle those quantities
  back in as facts.

### Evidence and honesty rules

- Inspect the supplied repository and results before judging coverage. Cite `path:line` and result
  artifact keys wherever possible.
- Separate **implemented**, **tested**, **tested inadequately**, **not tested**, and **not testable
  with current data**. Never treat code presence as evidence that a strategy was evaluated.
- Reconstruct a trial ledger: every materially distinct signal, target, horizon, symbol, convention,
  subsample, and interaction you can find. Flag undocumented researcher degrees of freedom and
  multiple-testing risk.
- Do not recommend a strategy merely because it is common in options commentary. State the causal
  mechanism, observable proxy, tradable expression, expected sign, forecast horizon, falsification
  test, and data required.
- Distinguish a **signal test** from a **tradable strategy test**. Forecast R2, return predictability,
  and an option structure's net P&L after spread/slippage are different claims.
- Do not infer profitability from statistical significance. Require economic magnitude, turnover,
  capacity, spreads, financing/borrow, assignment/exercise, and tail-risk analysis appropriate to
  the proposed instrument.
- Label outside facts as sourced, inference, or hypothesis. If browsing is available, use primary
  sources or research papers and cite them; do not invent citations.
- Preserve the project's negative findings. Recommend follow-up only when it can distinguish among
  explanations, not because a reviewer dislikes the null.

### Audit the full hypothesis space

For each lane below, identify what was tested, what was missed, and whether the current data can
answer it honestly.

1. **Forecast object:** direction, absolute return/range, realized variance, implied-volatility
   change, variance risk premium, jump/gap risk, skew/smile change, correlation/dispersion, liquidity,
   and tail probability.
2. **Horizon and timing:** overnight, next day, 2-5 day, weekly, monthly/OpEx cycle, intraday, close
   to next open, open to close, and event windows. Check whether EOD construction mismatches the
   mechanism's expected horizon.
3. **Signal representation:** sign, magnitude, change, acceleration, percentile/z-score, distance to
   flip/large strikes, concentration, term structure by DTE, moneyness buckets, call/put asymmetry,
   volume versus OI, vanna/charm, interactions, nonlinearities, and threshold stability.
4. **Conditioning and regimes:** positive/negative gamma, trend, volatility state, liquidity, 0DTE
   era, pre/post-2020 structural change, OpEx, earnings/macro announcements, gap days, market stress,
   index rebalances, and bull/bear or high/low-correlation regimes.
5. **Cross-asset breadth:** SPX versus SPY, index versus ETF, index versus single names, sector ETFs,
   rates/credit proxies, size/liquidity buckets, and cross-sectional ranking. Check survivorship and
   universe-history bias.
6. **Tradable expression:** underlying timing, delta-hedged long/short straddles, strangles,
   calendars, butterflies/condors, skew trades, verticals, dispersion/correlation, relative value,
   and hedging overlays. Do not call an expression testable unless historical bids/asks or a
   defensible executable-price model exists for every leg.
7. **Market microstructure and positioning:** dealer-sign uncertainty, open/close classification,
   multi-leg trades, quote-rule error, stale OI, endogeneity of gamma and spot/vol, dividend/rate
   assumptions, early exercise, settlement style, AM/PM expiry, adjusted contracts, and 0DTE flows.
8. **Alternative explanations and controls:** lagged volatility, trend/momentum, spot level,
   liquidity, option volume, IV level/skew, market return, day-of-week/calendar effects, earnings,
   and simple price-only models. Ask whether GEX adds value beyond these rather than merely tracking
   them.
9. **Validation design:** untouched holdout, walk-forward estimation, purged/embargoed CV where
   labels overlap, family-wise/FDR control, deflated Sharpe or equivalent selection adjustment,
   block bootstrap choice, parameter stability, placebo leads/lags, sign falsification, synthetic
   nulls, vendor replication, and sensitivity to symbol/date exclusions.
10. **Portfolio and failure modes:** aggregation across symbols, concentration, beta/vega/gamma
    exposure, drawdowns and expected shortfall, crash behavior, liquidity/capacity, crowded exits,
    missing-data selection, operational failures, and what evidence would stop the research lane.

### Required analysis process

1. Build a **coverage matrix** from repository evidence. Do not rely only on README or handoff
   claims; trace scripts and saved results.
2. Build a **conclusion ledger**: claim, supporting experiment, sample, target, horizon, conventions,
   robustness checks, and the strongest valid conclusion.
3. Identify gaps, then classify each as:
   - `A — required control/validity gap`;
   - `B — high-value adjacent hypothesis`;
   - `C — strategy-expression gap`;
   - `D — robustness/regime gap`;
   - `E — interesting but low-priority`; or
   - `X — not identifiable/testable with current data`.
4. Score each gap from 1-5 on **information value**, **plausibility**, **data readiness**, and
   **implementation cost** (5 means greater cost). State why. Do not use a fake mathematically
   precise aggregate; use the scores to explain the ranking.
5. Design the top experiments in enough detail that another researcher could implement them without
   choosing the result after seeing the data.

### Required output

#### 1. Executive verdict

In 5-8 sentences, answer: Is the research program broad enough to support its current conclusions?
What is the largest unchecked strategy or validity lane? What should explicitly **not** be concluded?

#### 2. Tested-coverage matrix

Use columns:

`Lane | Hypothesis/strategy | Status (implemented/tested/inadequate/untested/untestable) | Evidence | Data coverage | Verdict supported`

Include negative and null results. If result files were not supplied, say which verdicts cannot be
independently verified.

#### 3. Trial and conclusion ledger

List the material trials already run and expose implicit multiplicity. Then list every documented
conclusion as `supported`, `overstated`, `premature`, or `not reproducible from supplied artifacts`.

#### 4. Ranked gap register

Use columns:

`Rank | Gap | Class | Why it matters | Current-data feasibility | Info value | Plausibility | Data readiness | Cost | Risk if ignored`

Include at least one serious candidate from forecast targets, horizons, signal construction,
cross-sectional research, option-structure execution, market regimes, alternative controls, and
portfolio risk—or explicitly explain why that lane has no justified candidate.

#### 5. Top five pre-registered experiments

For each provide:

- exact hypothesis and expected sign;
- unit of observation and eligible universe;
- signal formula using only observable fields, including lag/as-of rules;
- target and forecast/trade horizon;
- baseline and competing explanation controls;
- train/validation/untouched test dates or a walk-forward protocol;
- primary metric and pass/fail threshold fixed in advance;
- transaction-cost/execution assumptions if tradable;
- convention, parameter, regime, and vendor sensitivity checks;
- placebo or falsification test;
- minimum data needed and whether it exists now;
- what result would kill or defer the hypothesis.

Prefer experiments that discriminate between explanations. Examples of useful discrimination include
OI-GEX versus volume-GEX after the 0DTE transition, level versus change versus distance-to-flip,
GEX beyond IV/skew and lagged-vol controls, index versus ETF construction, and forecast signal versus
net option-structure P&L. These are examples, not conclusions.

#### 6. Strategies that remain unchecked

Give a concise list grouped into:

- testable now;
- testable after a small data/engine extension;
- requires materially new data;
- not identifiable enough to pursue.

For each, name the required data and the honest tradable expression. Avoid vague labels such as
"try iron condors" without entry, exit, selection, and risk rules.

#### 7. Stop-doing list

Name low-information experiments, duplicated tests, invalid comparisons, and tempting strategies
the team should not spend time on yet. Explain what prerequisite would make each worth revisiting.

#### 8. 30/60/90-day research sequence

Order work by information gain and dependency. Separate analysis that can run on existing artifacts
from data acquisition and engine development. End with explicit go/no-go gates.

#### 9. Honest bottom line

If this were your own research budget and capital, state which single hypothesis family you would
test next, which you would defer, and what evidence would make you abandon GEX as a useful predictor.

### Reviewer guard

If you cannot inspect the repository, stop and request the files. If code is available but saved
result artifacts are absent, you may audit design and coverage, but you must label empirical verdicts
as unverified. Do not fill missing evidence with general options-market lore.

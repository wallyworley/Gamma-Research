# Handoff / Resume Notes

Quick-start context for picking this repo back up in a new chat. Open `~/dev/gamma-research`
and read this file first.

Last updated: 2026-07-02 (Massive/Polygon paid adapter + nightly VPS universe capture LIVE; two-tier spot recovery).

## LIVE: paid data + nightly VPS universe capture (start here)

The engine now collects real option chains for the **full optionable US equity universe**
every weekday, unattended, on the always-on OVH VPS.

- **Provider:** Massive (rebranded Polygon.io; API host still `api.polygon.io`), **Options
  Starter ~$29/mo**. Key in `.env` (`MASSIVE_API_KEY`; also on the VPS at
  `/opt/gamma-research/.env`, perms 600). The free-tier products can't drive GEX: flat
  files lack greeks/OI; the free stock snapshot is 403.
- **Adapter** `src/ingest/adapters/massive.py` (registered `"massive"`). The tier gives
  **no underlying spot** (underlying_asset carries only a ticker, stock snapshot 403, the
  underlying daily bar lags the options snapshot). So, unlike a normal EOD source:
  - **Session** = latest ET date among per-contract `day.last_updated` (not `/prev`, which
    lags a full session and caused a ~4.6% spot error fable caught + blocked).
  - **Spot** = Black-Scholes **delta-inversion** of the snapshot's own near-ATM greeks
    (`_util.bs_implied_spot`), median, self-consistent with the gammas we integrate.
    **Two tiers** (`_SPOT_TIERS`): tight 2-60d/0.30-0.70 delta/floor-5 for liquid names,
    a wider 2-120d/0.25-0.75/floor-3 fallback for thin chains; a dispersion gate refuses
    an inconsistent cluster (no bad spot written). Known limit: ignores dividends, so
    high-yield names run ~q*tau low (bounded, documented; `underlying_close` overrides on
    an entitled tier). See memory `massive-spot-from-delta-inversion`.
- **Universe** `src/ingest/universe.py`: the ~5,290 optionable equities from the Cboe
  symbol directory (download + cache, poison-proof floor). Cash indices captured via the
  Polygon `I:` prefix (stored under the plain root): only the single-OCC-root ones
  (`INDEX_CAPTURE_ROOTS` = XSP, DJX, OEX). SPX/NDX/RUT are **deferred**: `I:SPX` bundles
  AM-settled SPX and PM-settled SPXW at identical (exp, strike, type), which the canonical
  key can't distinguish, so storing them would silently drop ~40% of SPX OI - the adapter
  now **fails loud** (B2) on such dual-root chains. Correct capture needs settlement/OCC-root
  in the schema key (follow-up). VIX excluded (settles to futures).
- **Capture** `src/ingest/capture.py`: `capture_many(max_workers=8)` concurrent, per-symbol
  failures isolated; a **session staleness guard** drops any frame whose session != the run
  day (no wrong-partition writes); `is_after_close` gate + trading-day/holiday guard. Atomic
  parquet writes (`io.py`: tmp + `os.replace`). Runner `scripts/snapshot_universe.py` writes
  a `data/.last_run.json` heartbeat.
- **Deploy** `.github/workflows/deploy.yml`: push to main -> **test gate (py3.12 + lock)** ->
  rsync to `/opt/gamma-research` (excludes `.env`/`.venv`/**`data/`**, no `--delete`) -> venv +
  `requirements.lock.txt` -> install + enable `deploy/systemd/gamma-snapshot.timer`
  (**17:30 ET Mon-Fri**, DST-correct on the UTC host; holidays no-op via the guard). Secrets
  `VPS_HOST` / `VPS_SSH_KEY` set. Store: `/opt/gamma-research/data`.
- **First full production run:** 3161/5290 captured in ~6 min; the two-tier recovery lifts
  thin-chain coverage from ~60% toward ~78% (validated spots within ~1-2% of reference).
  Remaining skips are genuinely illiquid/binary names (fail-safe, nothing bad written).
- **Reviewed:** MassiveAdapter + deploy hardened across multiple fable passes (PRs #11, #12,
  #13). VPS: Ubuntu 24.04, systemd 255, python3.12, ubuntu-owned `/opt/gamma-research`.
- **Next:** SPX/NDX/RUT index capture (needs settlement/OCC-root in the canonical key so
  AM vs PM don't collide), a real failure alert (`OnFailure=` mailer/webhook vs today's
  `.failures.log` marker), and running the metric/proxy suite + a backtest over the
  accumulating store. (Done: `_spot_source` provenance, XSP/DJX/OEX index capture,
  `read_canonical` returns canonical dtypes so the metric engine runs on stored chains.)

## What this repo is

Research-only effort to reverse-engineer and document a **GammaEdge-style** options
gamma-exposure trading framework using public / API-accessible data. **No live trader** (Phase 1
is offline backtesting research only). GammaEdge is an unaffiliated third-party product; its terms
are reconstructed from public sources and tagged known / inferred / unknown-proprietary.

## Key decisions already made

- **Location:** deliberately a standalone repo (`~/dev/gamma-research`), kept separate from the
  Finxact client work.
- **No invented formulas.** Only publicly documented formulas are stated as fact; anything
  unconfirmed is labeled proprietary and paired with an owned `_proxy` approximation.
- **Dealer positioning is unobservable** from public chains, so every GEX/DEX metric is a modeled
  proxy under a stated dealer-sign convention. This is the foundational caveat throughout.

## Current state (all three deliverables done + validated twice)

- `docs/reddit_gamma_strategy_terms.md` - term glossary, each tagged known / inferred /
  unknown-proprietary, with transparent approximations where the real formula is not public.
- `docs/data_provider_assessment.md` - 9-capability matrix + notes for Alpha Vantage, Polygon,
  ORATS, Databento, CBOE, EODHD, plus cross-cutting caveats.
- `docs/phase_1_plan.md` - non-live backtesting engine design (architecture, data model, metric
  engine, backtester, guardrails, milestones M0-M6).
- `prompts/validation_prompt.md` - template for cross-model validation.
- `scripts/build_validation_prompt.py` - generates `prompts/validation_prompt.FILLED.md`
  (git-ignored) with the three docs embedded inline, ready to paste into another model.
- `README.md`, `.gitignore`.

### Code: M1 canonical contract locked (schema + adapter interface, before any vendor code)
- `src/ingest/schema.py` - **single source of truth** for the canonical option-chain schema
  (`CANONICAL_FIELDS`, version `1.0.0`). Pure stdlib: pandas/pyarrow types are *derived* lazily, so
  it imports and validates with no data stack. Enforces point-in-time integrity centrally
  (tz-aware `quote_ts`, `expiration >= quote date`, `oi_asof_date <= quote date`, positive
  strike/spot). Added `oi_asof_date`, `_iv_source`, `_adapter` vs the original doc table; the plan's
  data-model table (section 5) was updated to match.
- `src/ingest/adapter.py` - `ChainAdapter` ABC (`fetch_raw` + `normalize` -> validated `load`
  template method) + a name-keyed registry so config selects EODHD/ORATS/Polygon. No adapter can
  leak a non-conforming frame to the metric engine.
- `src/ingest/io.py` - partitioned parquet store (`symbol=<SYM>/date=<YYYY-MM-DD>/chain.parquet`),
  validates on write and read. First *exercised* in M1 once the stack is installed.
- `tests/test_schema_contract.py` - 20 stdlib-`unittest` tests, all passing now
  (`python3 -m unittest discover -s tests -v`): schema shape, every value/lookahead rule, adapter
  registry, abstract-instantiation guard.
- `requirements.txt` - compatible-release pins (pandas/pyarrow/numpy/scipy).

### Code: M0 complete (env frozen + pinned config + CI)
- Committed on branch `phase1-m0-m1-scaffold` (off `master`; no remote yet).
- `src/config/engine.py` + `config/engine.toml` - pinned pricer / cost / backtest / metric config.
  Code holds the defaults; the TOML mirrors them and a drift test asserts they stay equal. Every
  config exposes `config_hash()` to stamp reproducible runs. Encodes the no-lookahead fill guard
  (`allow_same_bar_fill=false`) and the foundational `dealer_sign_convention`.
- `requirements.lock.txt` - exact frozen versions (numpy 2.5.0, pandas 2.3.3, pyarrow 18.1.0,
  scipy 1.18.0, + transitives) from a local `.venv` (gitignored). Install byte-identical envs with
  `pip install -r requirements.lock.txt`.
- `.github/workflows/ci.yml` - runs contract tests stdlib-only first (fail fast), then installs the
  stack and reruns the full suite, on Python 3.11 + 3.13.
- `tests/test_config.py` (7) + `tests/test_io_roundtrip.py` (4, skip without the stack).
  **31 tests total, all green.** The io round-trip caught and fixed a real bug: `pq.read_table` on a
  Hive path invented a dictionary `symbol` partition column that collided with the file's string
  `symbol`; `read_canonical` now reads the single file directly.

### Code: M1 first vendor adapter (EODHD) built + live-verified
- `src/ingest/adapters/eodhd.py` - `EodhdAdapter` (registered as `"eodhd"`) mapping the EODHD
  UnicornBay options **EOD** API (`/api/mp/unicornbay/options/eod`, JSON:API `data[].attributes`)
  onto the canonical schema. Verified field names against the live API.
- **Key finding:** the options payload has **no underlying spot** (only a 2-decimal `moneyness`), so
  the adapter makes a second call to the EOD stock endpoint (`/api/eod/{SYM}.US`) and attaches that
  day's `close` as `underlying_price`. `oi_asof_date` is stamped (default prior weekday,
  `oi_lag_days=1`) as an explicit **unverified** OI-timing assumption (see F1 in review hardening
  below). `quote_ts` = equity close (16:00 America/New_York, DST-aware) in UTC.
- Split cleanly: `fetch_raw` = live HTTP (paginated chain + underlying close), `normalize` /
  `_extract_records` = pure mapping, unit-tested against a recorded fixture
  (`tests/fixtures/eodhd_options_eod_sample.json`).
- `tests/test_eodhd_adapter.py` - 14 tests incl. an end-to-end normalize -> write_canonical ->
  read_canonical -> validate pipeline. **45 tests total, all green** (17 skip without the data stack).
- **Live-smoke-verified** against the EODHD `demo` token: fetched 1000 real AAPL contracts + real
  underlying close, normalized to a valid canonical frame with zero validation issues.
- Requires an API token: pass `EodhdAdapter(api_token=...)` or set `EODHD_API_TOKEN`.

### Code: M2 GEX metric engine (Net GEX / ZeroGEX / regime) - on branch `phase1-m2-metrics`
- Branch `phase1-m2-metrics` off `phase1-m0-m1-scaffold` (stacked PR #2, base = the scaffold branch).
- `src/metrics/gex.py` - Net GEX / per-strike GEX (dollar-per-1% form `sign*gamma*OI*100*Spot^2*0.01`),
  `gex_by_strike`, `regime` (+GEX/-GEX/flat), `zero_gex`, and a `gamma_snapshot` summary. All read the
  canonical schema; dealer sign + GEX form come from `EngineConfig` (never hard-coded), grounded in
  `docs/reddit_gamma_strategy_terms.md`.
- `src/metrics/blackscholes.py` - `bs_gamma` (BS gamma). ZeroGEX **recomputes** gamma at candidate
  spots (holding vendor gamma fixed can't flip sign - the `Spot^2*0.01` weight is a positive scalar),
  then interpolates the Net GEX sign change nearest spot. Uses `PricerConfig` r/q/day-count.
- **Config rename:** `metrics.dealer_sign_convention` default `mm_short_gamma` -> `long_call_short_put`
  (unambiguous: calls +1 / puts -1, per the terms doc). Changed in both `src/config/engine.py` and
  `config/engine.toml` on this branch; drift test still green.
- `tests/test_gex_metrics.py` - golden tests: exact hand-computed Net GEX (`-150,000` on the mini-chain),
  shares vs dollar form, dealer-convention sign flip, exact BS gamma (`0.01984763` ATM), and ZeroGEX
  validated by its sign-change property + no-crossing/empty cases. **59 tests total, all green** (32
  skip without the data stack; suite also passes under `-W error::DeprecationWarning`).

### Code: M3 proxy metric suite - on branch `phase1-m3-proxy-metrics` (off main)
- `src/metrics/_common.py` - shared dealer-sign / contract-size / dollar-factor helpers; `gex.py`
  refactored to use them (so GEX and DEX can't drift on the dealer convention).
- `src/metrics/dex.py` - DEX / dealer delta balance (`DealerSign*Delta*OI*100*Spot`) split above/
  below/at spot (`DexBalance`, with a normalized `skew_proxy`); `db_change` (series diff).
- `src/metrics/ratios.py` - `gex_ratio` (`|Call GEX|/|Put GEX|`; +inf if no puts) + `trailing_percentile`.
- `src/metrics/levels.py` - `oi_levels` (COI/POI argmax-OI strikes + totals), `moneyness_levels`
  (COTMP/COTMC/CITMP/CITMC grid), `gamma_transitions` (PTrans/NTrans per-strike dominance flips).
- `src/metrics/grade.py` - `grade_proxy`: owned 0-10 composite of regime, GEX-ratio percentile,
  delta-balance skew, distance-to-ZeroGEX, OI-level proximity; published weights (normalized so the
  score is always in [0,10]). Labeled a proxy, never "Grade 11".
- All proprietary-derived outputs carry a `_proxy` suffix; every metric reads the canonical schema and
  takes the dealer sign / GEX form from `EngineConfig`. Grounded in `reddit_gamma_strategy_terms.md`.
- `tests/test_proxy_metrics.py` - golden tests: exact DEX buckets (above 81M / below 70.5M / net
  151.5M), convention flip, db_change, GEX-ratio cases, COI/POI + the full moneyness grid, PTrans/
  NTrans dominance, and grade range/monotonicity. **75 tests total, all green** (48 skip without the
  data stack; passes under `-W error::DeprecationWarning`).

### Code: M4 event-driven backtester - on branch `phase1-m4-backtester` (off main)
- `src/backtest/engine.py` - `run_backtest(bars, target_position, config)`: non-live, simulated fills
  only. Signal-agnostic - consumes a bar timeline (`open`/`close`, datetime-indexed; `validate_bars`)
  plus a per-bar **target weight** series in [-1,1] and returns net/gross equity, a trade log, and
  stats. **Point-in-time rule:** `target_position[t]` is decided at bar t's close and executed at
  bar t+1's **open** (never same-bar-close). `backtest.allow_same_bar_fill` (pinned False) flips to
  same-close only for measuring the look-ahead a naive fill would steal.
- Costs from `CostConfig`: flat commission + slippage bps on traded notional. Every run reports **net
  and gross** so cost drag is explicit.
- `src/backtest/stats.py` - `total_return`, `max_drawdown`, `summarize`, and a `buy_and_hold` baseline.
- `tests/test_backtest.py` - golden hand-computed equity path (`[100000, 110000, 110000]`), exact cost
  drag (net final `109,979`, total cost `21`), the no-lookahead proof (d0 signal fills at d1 open, not
  d0 close), drawdown, baseline, and bars validation. **85 tests total, all green** (58 skip without
  the data stack; passes under `-W error::DeprecationWarning`).
- **Not yet wired:** the signal layer (gamma-structure rule -> target weights) and building `bars`
  from a vendor. The EODHD stock EOD call already returns OHLC, so `bars` is a thin adapter step.

### Code: M5 signal layer + evaluation harness - on branch `phase1-m5-signals-eval` (off main)
- `src/signals/rules.py` - `regime_signal` (+GEX -> long / -GEX -> short / flat), `regime_series`, and
  the generic `chain_metric_series(chains, fn)`. Input `chains` is an ordered mapping
  `{bar_timestamp: chain_df}` keyed to match `bars.index`; the weight decided from bar t's chain fills
  at t+1's open (engine enforces it). Rules are transparent/config-driven (owned baselines).
- `src/eval/baselines.py` - `random_entry_control` (seeded, reproducible random long/flat control).
- `src/eval/harness.py` - `regime_attribution` (per-bar returns bucketed by the regime at t-1 that
  drove the position), `cost_sweep` (net return over a commission x slippage grid), and `scorecard`
  (rule stats vs buy-and-hold + random control, `beats_*` flags, attribution, stamped with
  `config_hash()`).
- `tests/test_signals_eval.py` - signal weights, attribution golden (+GEX bar +0.10 / -GEX bar -0.10),
  cost-sweep monotonicity, reproducible random baseline, and an end-to-end scorecard that beats both
  baselines on a favorable synthetic scenario. **93 tests total, all green** (66 skip without the data
  stack; passes under `-W error::DeprecationWarning`).

### Review hardening - phase 1 (fable code+model review, must-fix set) - branch `phase1-review-hardening`
A comparable model (fable) reviewed the whole codebase (prompt: `prompts/code_review_prompt.md`,
builder: `scripts/build_code_review_prompt.py`). Full findings F1-F21. This branch fixes the must-fix
set; the rest are queued for later phases (see below).
- **F5** (silent double-count): `validate_records`/`validate_frame` now reject duplicate `PRIMARY_KEY`
  rows; `EodhdAdapter.normalize` de-dups with a loud `logging.warning`.
- **F6** (dead config): removed `fill_timing` (was ignored; `allow_same_bar_fill` is the real knob);
  `half_spread_cost: bool` -> `half_spread_bps: float`, now **wired** into the backtester cost. NB this
  fixed only the two knobs F6 named; several `pricer.*` knobs (`model`, `iv_method`, `iv_tol`,
  `iv_max_iter`, `iv_vol_lo`, `iv_vol_hi`) and `backtest.base_currency` are still hashed but have no
  consumer yet (reserved for self-computed greeks / multi-currency; no IV solver exists today), so
  `config_hash()` is not yet a clean function of only effective knobs. Tracked for a later phase.
- **F1** (OI timing lie): deleted the false "the backtester/metric engine aligns OI T-1" docstrings.
  `EodhdAdapter` now **stamps `oi_asof_date`** (default prior weekday, `oi_lag_days=1`; weekend-aware
  only, NOT holiday-aware), a documented UNVERIFIED assumption. Nothing shifts OI across time; the
  metric docstrings now say so.
- **F8** (incoherent composite): `grade_proxy` `score_proxy` is **quarantined** - `None` unless
  `enable_composite=True`; the 5 descriptive components are always returned; `oi_proximity` relabeled a
  non-directional pin-strength feature.
- **F3** (scorecard launders beta): replaced the naked `beats_*` booleans. The **primary** timing test
  is now `permutation_test` - the strategy vs shuffles of its OWN weights on **gross** returns, which
  matches exposure and sign (long AND short) and isolates timing (a fable re-review caught that an
  exposure-only, long-only control still laundered *short* beta, and that a net-basis permutation test
  would be biased by cost/turnover asymmetry - both fixed). Plus a demoted exposure-matched long-only
  control, a bootstrap mean-return CI, and a Sharpe. Verified: always-short on a falling market scores
  permutation percentile 0.0 (old control gave 1.0).
- **F2** (chain completeness): can't be verified without a live token (demo returns AAPL sample only);
  added a prominent UNVERIFIED caveat in the adapter docstring with the exact A/B check to run.
- **103 tests, all green** (`.venv/bin/python -m unittest discover -s tests`; passes under
  `-W error::DeprecationWarning`). Fable re-reviewed the branch twice; verdict merge-ready.

### Phase 2: real market data via Cboe (free) - branch `phase2-cboe-adapter`
The EODHD token turned out to be free-tier with **no options entitlement** (403; options is a paid
add-on). A fable provider search found a zero-cost path we can use TODAY:
- `src/ingest/adapters/cboe.py` - **`CboeAdapter`** (registered `"cboe"`), reads Cboe's free, no-key,
  ~15-min-delayed options JSON (`cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json`). Returns
  the **full chain** with greeks + IV + OI and the underlying spot in one call. Parses OSI symbols.
  **Session handling (fable B1):** the payload timestamp is a ticking UTC generation clock, so the
  trading session is taken from its *Eastern* date and `quote_ts` is anchored to that session's close
  (16:00 ET in UTC) - avoids the US-evening UTC-date rollover; `oi_asof_date` = T-1 weekday of the
  session. **Equities only (fable B2):** AM/PM-settled index chains (SPX vs SPXW share exp/strike/type)
  would collide on the canonical key; the adapter **raises** on those rather than silently dropping OI
  (index support needs a settlement field in the schema - future work). Snapshot-only; build history
  going forward. `tests/fixtures/cboe_options_sample.json` is a trimmed real pull.
- **Live-verified**: `CboeAdapter().load("AAPL")` fetched 3,508 real contracts, validated with zero
  issues, and the metric engine computed real GEX (Net GEX ~+1.5B, +GEX regime, ZeroGEX ~263 vs spot
  ~308). First time the whole pipeline ran on real market data.
- `tests/test_cboe_adapter.py` - OSI/timestamp parsers, normalize mapping, validation, dedup,
  end-to-end parquet, registration, index-URL, plus adversarial B1 (evening rollover) and B2 (dual-settlement) cases. **121 tests total, all green.**
- Cboe caveats: unofficial CDN (no SLA; be gentle/cache), 15-min delayed, snapshot-only, some deep
  contracts report iv/greeks 0; do not redistribute. **Availability risk (fable nit):** after a
  corporate action an equity carries adjusted roots (AAPL vs AAPL1) at shared expiration/strike/type,
  which trips the same multi-root guard as index AM/PM settlement and **hard-fails that symbol's load**
  until the adjusted series expires. Deliberate (corrupt merged data is worse than a gap); the eventual
  schema `settlement`/deliverable field should cover adjusted series too, not just index AM/PM.

**Provider decision (fable-researched, July 2026, verified live):** for a *historical* backtest the
cheap self-serve options-with-greeks vendors are **Alpha Vantage Premium** ($49.99/mo, 2008+ history)
or upgrading the existing **EODHD** account ($39.99/mo, ~Q4 2023+). Cheapest broad **multi-asset**
future platform = **Polygon.io** (~$29/mo: stocks/options/forex/crypto). **IBKR** (account on hand)
is the future *live/forward + execution* layer, not a historical-backtest source. `.env` holds the
(valid, free-tier) EODHD token; it works for stock EOD and will unlock options if upgraded.

### Phase 2 code-rigor batch (more fable findings) - branch `phase2-code-rigor`
- **F9** `regime_attribution` now splits each bar into intraday (regime[t-1]) and overnight
  (regime[t-2]) so the overnight gap is booked to the regime that actually held the position; returns
  `pnl_contribution`/`n_periods` per bucket. Signature is now `(bars, target, regimes)`; scorecard updated.
- **F10** ZeroGEX search grid moved into hashed `MetricsConfig` (`zerogex_grid_lo_frac/hi_frac/n`);
  `zero_gex` returns None = "no crossing in the searched grid" (not "no flip exists"); `gamma_snapshot`
  exposes `zero_gex_in_grid`.
- **F13** `gamma_snapshot.gamma_source_agrees` flags when the vendor-gamma regime and the BS-gamma net
  at spot disagree in sign (internally inconsistent snapshot).
- **F12** `greek_coverage(df)` reports the share of OI backed by usable gamma/IV (live AAPL: 95.8%
  gamma, 486 iv=0 rows) - so a metric's trustworthiness is visible.
- **F17** `require_single_snapshot` guards every snapshot metric (net_gex/gamma_snapshot, DEX, ratios,
  levels, grade) against a silently-concatenated multi-day/multi-symbol frame.
- **F7/F15** backtester rejects a target whose index doesn't intersect bars (silent zero-trade) and a
  weight outside [-1, 1] (leverage/typo).
- **F18** deleted `moneyness_levels` dead locals.
- **128 tests, all green** (`-W error::DeprecationWarning`). Deferred: F14 (rebalance band), F11
  (dealer-sign sweep), F19/F20/F21 (minor), plus F12 hard plausibility bounds (calibrate vs real data).

### Two validation passes already incorporated
1. Round 1 (claims-only; reviewer couldn't see the docs) - fixed GEX formula framing (share vs
   dollar-per-1%-move), Polygon history (~2014 trades/aggs), ORATS 1-min (Aug 2020), Alpha Vantage
   real-time nuance, added cross-cutting caveats. Also fixed the validation prompt so future passes
   can see the docs.
2. Round 2 (true doc review) - **verified** and applied Databento schema-specific history (Apr 2013
   aggregates / Mar 2023 fine schemas), reframed CBOE "2004" as product-specific, relabeled COTMP/
   COTMC as "thinly corroborated" with specific citations, split COI/POI tag (known acronym /
   inferred level), added a corporate-actions backtest risk. CBOE's specific "Jan 2010/2012" dates
   were NOT verifiable, so they were hedged rather than asserted.

## Open threads / next steps

- **On GitHub (`origin`, default `main`):** M0-M5 and review-hardening phase 1 are all **merged to
  `main`** (PRs #1,#3,#4,#5,#6,#7). The Cboe adapter is on branch `phase2-cboe-adapter` (PR pending).
  Recreate the env with `python3 -m venv .venv && .venv/bin/pip install -r requirements.lock.txt`;
  run `.venv/bin/python -m unittest discover -s tests -v`.
- **M0 - M5 done + review-hardening phase 1 + Cboe adapter.** The engine is complete end-to-end
  (adapter -> canonical parquet -> metrics/proxies -> signal -> point-in-time backtest -> significance
  scorecard) and now runs on **real** options data via Cboe. **121 passing tests.**
- **Review loop:** the workflow is - implement a batch of fable findings -> have fable re-review the
  branch -> next batch. Re-review by spawning a general-purpose agent on model `fable`, read-only,
  pointed at `prompts/code_review_prompt.md` (see that file's `## PROMPT` section).
- **Remaining fable findings (next phases), roughly by priority:** F4 (test the volatility channel,
  not close-to-close direction) and F2-live (verify chain completeness with a token) are gating for
  real conclusions; then F9 (attribution overnight-gap misattribution), F10 (ZeroGEX grid -> hashed
  config, distinguish "no flip on grid" from "no flip"), F11 (dealer-sign sensitivity sweep + note net
  DEX is structurally signed), F12 (schema plausibility bounds: gamma>=0, |delta|<=1, iv in (0,5];
  greek-coverage stat), F13 (regime vs ZeroGEX gamma-source disagreement flag), F7/F17 (loud errors on
  empty target-intersection and multi-snapshot frames), F14/F15 (rebalance band; short/borrow model +
  weight-range guard), plus F18-F21 cleanups. Full list + evidence in the review (and reproducible via
  the fable re-review).
- **Running it for real now that Cboe works (free, no token):**
  1. `CboeAdapter().load(symbol)` gives a real, validated chain today; the metric/proxy suite runs on
     it directly (verified on AAPL).
  2. Cboe is snapshot-only, so a *historical* backtest needs history. **Built (free path):**
     `src/ingest/capture.py` (`capture_snapshot`/`capture_many`, vendor-agnostic) + CLI
     `scripts/snapshot_cboe.py` persist a session's chain via `io.write_canonical`, partitioned by
     symbol/session. Run daily after the close to accumulate history; idempotent per session; data
     goes to `$GAMMA_DATA_DIR` or `data/normalized/` (git-ignored). Live-verified (AAPL, 3508
     contracts). **Not yet scheduled** - needs a launchd/cron entry (offer pending user OK). Instant
     backfill alternative: buy history (Alpha Vantage Premium / EODHD upgrade).
  3. For a first backtest, build `bars` (open/close) from a stock OHLC source (EODHD stock EOD works
     even on the free token, or Cboe/other), then `regime_signal` -> `scorecard`. Expect it to be
     underpowered (F4); keep the honest negative/inconclusive result.
- **M6 (optional, deeper history / greek quality):** add an ORATS or Polygon `ChainAdapter` behind the
  same interface and run the cross-vendor comparison. Greek source is already recorded per row
  (`_greek_source`), so results are comparable.
- **Signal ideas beyond regime:** threshold rules on `gex_ratio` / `grade_proxy` /
  distance-to-ZeroGEX, built with `chain_metric_series`. History-dependent inputs
  (`db_change`, `trailing_percentile`, grade's `gex_ratio_percentile`) take a series accumulated
  across bars.
- **Run validation again** with the filled prompt: `python3 scripts/build_validation_prompt.py`,
  then paste `prompts/validation_prompt.FILLED.md` into a fresh model.
- **Vendor matrix asterisk:** re-check current plan entitlements before buying any provider; history
  depth and features shift by plan.

## Suggested first vendor stack

Prototype on EODHD or Alpha Vantage (cheap EOD greeks/IV/OI), then graduate gamma computations to
ORATS or Polygon for greek quality and history depth, behind a swappable adapter.

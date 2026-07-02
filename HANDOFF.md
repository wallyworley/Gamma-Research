# Handoff / Resume Notes

Quick-start context for picking this repo back up in a new chat. Open `~/dev/gamma-research`
and read this file first.

Last updated: 2026-07-01 (M5 signal layer + evaluation harness; M0-M4 done).

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
  day's `close` as `underlying_price`. `oi_asof_date` left null (undisclosed OI timing => schema's
  T-1 convention). `quote_ts` = equity close (16:00 America/New_York, DST-aware) in UTC.
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

- **On GitHub (`origin`, default `main`):** M0/M1 (#1), M2 (#3), M3 (#4), M4 (#5) are all **merged to
  `main`**. M5 is on branch `phase1-m5-signals-eval` (PR #6, base `main`). Recreate the env with
  `python3 -m venv .venv && .venv/bin/pip install -r requirements.lock.txt`; run `.venv/bin/python -m
  unittest discover -s tests -v`.
- **M0 - M5 done. The offline research engine is functionally complete end-to-end**
  (vendor adapter -> canonical parquet -> metrics/proxies -> signal -> point-in-time backtest ->
  scorecard vs baselines), all on synthetic/fixture data with 93 passing tests.
- **What's left to run it for real (needs a live `EODHD_API_TOKEN`):**
  1. Build `bars` (open/close) from the EODHD stock EOD call (already returns OHLC) - a thin adapter
     step; then pull a range of daily chains + underlying closes for one symbol.
  2. Persist chains via `io.write_canonical`, compute per-date snapshots, run `regime_signal` ->
     `scorecard`. Keep the negative result if the rule doesn't beat baselines net of costs.
  Demo token only returns AAPL sample data; EODHD options history reaches only ~Q4 2023.
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

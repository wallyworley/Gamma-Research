# Handoff / Resume Notes

Quick-start context for picking this repo back up in a new chat. Open `~/dev/gamma-research`
and read this file first.

Last updated: 2026-07-01 (M1 EODHD adapter built + live-verified; M0 complete).

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

- **Branch `phase1-m0-m1-scaffold`** on GitHub (`origin`, default branch `main`); PR #1 open. The M1
  adapter commit lands on this same branch unless split out. Recreate the env with `python3 -m venv
  .venv && .venv/bin/pip install -r requirements.lock.txt`; run `.venv/bin/python -m unittest
  discover -s tests -v`.
- **M0 + M1 done.** **M2 is the next build step.**
- **M2:** metric engine - Net GEX / per-strike GEX (dollar-per-1%-move: `Gamma x OI x 100 x Spot^2
  x 0.01`, sign by dealer convention), ZeroGEX (solve Net GEX(S)=0 over a spot grid), and the +/-GEX
  regime flag. Read the chain via the canonical schema, read conventions via `EngineConfig`
  (`metrics.gex_convention`, `metrics.dealer_sign_convention`). **Golden tests on a hand-built
  mini-chain** are the M2 deliverable. Apply the OI T-1 alignment here (rows carry `oi_asof_date`;
  null => T-1 of `quote_ts`).
- **Before real backtests:** need a live `EODHD_API_TOKEN` (demo token only returns AAPL sample
  data). EODHD options history reaches only ~Q4 2023, so early backtests are shallow until a
  deeper-history vendor is graduated in (M6).
- **Run validation again** with the filled prompt: `python3 scripts/build_validation_prompt.py`,
  then paste `prompts/validation_prompt.FILLED.md` into a fresh model.
- **Vendor matrix asterisk:** re-check current plan entitlements before buying any provider; history
  depth and features shift by plan.

## Suggested first vendor stack

Prototype on EODHD or Alpha Vantage (cheap EOD greeks/IV/OI), then graduate gamma computations to
ORATS or Polygon for greek quality and history depth, behind a swappable adapter.

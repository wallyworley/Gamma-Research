# Phase 1 Plan: Non-Live GammaEdge-Style Backtesting Engine

> **Scope of Phase 1.** Build a **research-only, offline backtesting engine** that reconstructs
> GammaEdge-style gamma-structure metrics from historical option chains and evaluates simple,
> transparent mechanical rules against history. **This phase does not trade.**
>
> Companion docs: [reddit_gamma_strategy_terms.md](reddit_gamma_strategy_terms.md) (what the metrics
> mean and which are proxies) and [data_provider_assessment.md](data_provider_assessment.md)
> (where the data comes from).

Last updated: 2026-07-01.

---

## 1. Objectives

1. Ingest historical option chains (OI, IV, greeks, strikes, expirations) plus underlying prices.
2. Compute a reproducible set of gamma-structure metrics: Net GEX, ZeroGEX, +GEX/-GEX regime,
   DEX (dealer delta balance), GEX Ratio, and OI-concentration levels (COI/POI, COTMP/COTMC proxies).
3. Reconstruct GammaEdge-style **levels** (PTrans/NTrans, C_TM_ levels) as clearly labeled proxies.
4. Backtest simple mechanical rules built on those metrics with **point-in-time correctness**
   (no lookahead), realistic costs, and honest performance reporting.
5. Produce an evaluation harness so any rule or metric definition can be swapped and re-scored.

## 2. Non-goals (explicit)

- **No live trading, no broker/order routing, no paper-trading connection.** Simulated fills only.
- No real-time data feed. Batch/offline historical data only in Phase 1.
- No claim that proxy metrics equal GammaEdge's proprietary outputs. Proxies are labeled `_proxy`.
- No reproduction of GammaEdge's unpublished formulas or its "structural grade" rubric. We define
  our own transparent composite where a proprietary one is missing.

## 3. Guiding principles

- **Transparency over mimicry.** Every metric has a documented formula in code, cross-linked to the
  terms doc. Proprietary methods get an owned approximation with a `_proxy` suffix, never a fake
  "exact" value.
- **Point-in-time everything.** At bar `t`, only data knowable at or before `t` is used. This is the
  single most important correctness rule for options backtests.
- **Vendor-swappable data.** All ingestion sits behind one adapter interface so EODHD, ORATS,
  Polygon, etc. are interchangeable (see provider doc).
- **Reproducibility.** Pin the pricer (model, risk-free rate, dividend, IV-solve) and record vendor
  greek assumptions so runs are comparable across data sources.

---

## 4. Architecture

```
                 +-------------------+
 raw vendor  --> |  Data Ingestion   |  adapters: EODHD / ORATS / Polygon / Databento / CBOE / AV
 files/API       |  + Normalization  |  -> canonical chain schema (parquet)
                 +---------+---------+
                           |
                           v
                 +-------------------+
                 |  Metric Engine    |  Net GEX, ZeroGEX, +/-GEX, DEX, GEX Ratio,
                 |  (point-in-time)  |  OI levels, PTrans/NTrans proxies, grade_proxy
                 +---------+---------+
                           |
                           v
                 +-------------------+
                 |  Signal Layer     |  mechanical rules -> target position (per bar)
                 +---------+---------+
                           |
                           v
                 +-------------------+
                 |  Backtest /       |  event-driven loop, fills, costs, slippage,
                 |  Fill Simulator   |  position + PnL accounting (NO live orders)
                 +---------+---------+
                           |
                           v
                 +-------------------+
                 |  Analytics /      |  metrics, equity curve, drawdown, trade log,
                 |  Reporting        |  regime attribution, plots
                 +-------------------+
```

### 4.1 Suggested repo layout

```
gamma-research/
  docs/                     # this folder
  data/
    raw/                    # vendor payloads (gitignored)
    normalized/             # canonical parquet chains
  src/
    ingest/                 # vendor adapters -> canonical schema
    metrics/                # GEX, ZeroGEX, DEX, ratios, levels, grade_proxy
    signals/                # mechanical rule definitions
    backtest/               # event loop, fills, costs, portfolio, PnL
    reporting/              # stats + charts
    config/                 # pricer + backtest config (rates, divs, costs)
  tests/                    # unit tests incl. no-lookahead + golden GEX cases
  notebooks/                # exploration only, not the source of truth
```

Language suggestion: Python (pandas/pyarrow/numpy/scipy). Store normalized chains as **parquet**
partitioned by `symbol` and `date`. Keep raw vendor payloads out of git.

---

## 5. Canonical data model

One normalized row per contract per timestamp. **The authoritative, machine-checked definition is
[`src/ingest/schema.py`](../src/ingest/schema.py)** (`CANONICAL_FIELDS`, schema version `1.0.0`);
this table mirrors it.

| Field | Notes |
|---|---|
| `symbol` | underlying ticker (key) |
| `quote_ts` | point-in-time timestamp, tz-aware UTC (EOD snapshot or intraday bar) (key) |
| `expiration` | contract expiry; must be >= quote date (key) |
| `strike` | strike price, > 0 (key) |
| `type` | `call` / `put` (key) |
| `underlying_price` | spot at `quote_ts`, > 0; adapter must attach it (GEX/DEX undefined without it) |
| `bid`, `ask`, `last` | contract prices (nullable) |
| `open_interest` | contracts outstanding (usually T-1 for EOD sources) |
| `oi_asof_date` | session the OI is as-of (adapter-stamped); null = unknown, no layer infers it; must be <= quote date |
| `volume` | contract volume |
| `iv` | vendor IV, or self-computed (record source) |
| `delta`,`gamma`,`theta`,`vega`,`rho` | vendor greeks, or self-computed (record source + model) |
| `_iv_source` | vendor name or pricer id that produced `iv` |
| `_greek_source` | vendor name or pricer id that produced the greeks |
| `_adapter` | adapter that produced the row (`ChainAdapter.name`), for cross-vendor comparison (M6) |

The key columns `(symbol, quote_ts, expiration, strike, type)` form the natural key. Non-nullable:
`symbol`, `quote_ts`, `expiration`, `strike`, `type`, `underlying_price`, `_adapter`. On disk, chains
are parquet partitioned as `symbol=<SYM>/date=<YYYY-MM-DD>/chain.parquet`.

**OI timing caveat:** exchange OI is typically published for the **prior** session. The adapter should
stamp `oi_asof_date` with the session the OI is actually as-of, and ingestion enforces
`oi_asof_date <= quote date` so a *future*-dated stamp cannot pass. **This guarantee is only as strong
as the adapter's stamp** - the EODHD adapter's default (prior weekday) is an *unverified* assumption
(review finding F1); no layer realigns OI across a time series. `expiration >= quote date` and tz-aware
`quote_ts` are also enforced centrally (see [`validate_records`](../src/ingest/schema.py) and
[`tests/test_schema_contract.py`](../tests/test_schema_contract.py)).

---

## 6. Metric engine (Phase 1 deliverable)

Implement, each with a unit test and a docstring linking to the terms doc:

- **Net GEX / per-strike GEX** (Known dollar-per-1%-move convention): `Gamma x OI x 100 x Spot^2 x 0.01`,
  the dollar-GEX restatement of the SqueezeMetrics share form; sign by dealer convention. Record which
  convention is used (see terms doc). Golden-value unit test on a hand-built mini-chain.
- **ZeroGEX** (Known): solve `Net GEX(S) = 0` over a spot grid; interpolate the flip.
- **+GEX / -GEX regime flag** (Known): sign of Net GEX.
- **DEX / dealer delta balance** (proxy): `DealerSign x Delta x OI x 100 x Spot`, split above/below spot.
- **db_change** (proxy): first difference of DEX over the chosen interval.
- **GEX Ratio** (proxy): `|Call GEX| / |Put GEX|` + trailing percentile.
- **COI / POI levels** (Known acronym, inferred GammaEdge level use): argmax-OI strikes for calls / puts.
- **COTMP / COTMC / CITMP / CITMC** (proxy): OI-concentration strike in each moneyness/type bucket.
- **PTrans / NTrans** (proxy): first strike above/below spot where rolling call/put gamma dominance
  flips (acceleration trigger).
- **grade_proxy** (owned composite): documented 0-10 (or 1-N) score from regime, GEX-ratio
  percentile, delta-balance skew, distance-to-ZeroGEX, and proximity to key OI levels.

Every proxy is emitted with a `_proxy` suffix and a config-driven definition so alternatives can be
A/B tested.

---

## 7. Backtest / fill simulator

- **Event-driven loop** over the point-in-time timeline (EOD bars first; intraday later if the data
  source supports it).
- **Instruments:** start with trading the **underlying (or its ETF)** on gamma-structure signals to
  isolate signal value before adding options execution complexity. Options-leg simulation is a
  Phase 1.5 stretch goal.
- **Fills:** next-bar-open or modeled mid +/- slippage; never same-bar-close on the signal bar
  (avoids lookahead).
- **Costs:** commission per trade + configurable slippage (bps) + optional spread cost. Make costs a
  first-class config so results are shown gross and net.
- **Accounting:** positions, cash, mark-to-market equity curve, per-trade log with entry/exit
  reasons (which metric fired).

---

## 8. Validation and guardrails

- **No-lookahead tests:** unit tests that fail if any metric or signal at `t` reads data with
  timestamp > `t`. (Status: the backtester's next-open fill rule is tested; a cross-time OI T-1
  realignment is **not implemented** - OI is used as-of the adapter-stamped `oi_asof_date`, which is
  an unverified assumption for EOD vendors. See review finding F1.)
- **Golden GEX cases:** hand-computed mini-chains asserting exact GEX/ZeroGEX values.
- **Survivorship / universe integrity:** track delistings and symbol changes; document any gaps.
- **Cost sensitivity:** report performance across a slippage/commission grid, not a single rosy number.
- **Regime attribution:** break results out by +GEX vs -GEX regime to see where any edge lives.
- **Baseline comparison:** always compare against buy-and-hold of the underlying and a random-entry
  control, with the same cost model.
- **Honest reporting:** if a rule does not beat baseline net of costs, say so; keep the negative result.

---

## 9. Milestones

| # | Milestone | Output |
|---|---|---|
| M0 | Repo + config scaffolding, pricer/backtest config pinned | runnable skeleton, CI with tests |
| M1 | One vendor adapter (recommend EODHD for cheap EOD) -> canonical parquet | normalized chains for a few symbols |
| M2 | Metric engine: Net GEX, ZeroGEX, regime, with golden tests | validated metrics + charts |
| M3 | DEX, GEX Ratio, OI levels, PTrans/NTrans proxies, grade_proxy | full metric suite, all `_proxy` labeled |
| M4 | Event-driven backtester on the underlying + cost model | equity curve, trade log, drawdown |
| M5 | Evaluation harness + regime attribution + baselines | reproducible scorecard per rule |
| M6 | Swap to ORATS/Polygon for greek quality + longer history | cross-vendor comparison report |

## 10. Risks and open questions

- **Proxy fidelity:** our PTrans/NTrans/grade proxies may diverge from GammaEdge's true levels. Track
  divergence qualitatively where GammaEdge publishes example levels; never claim equivalence.
- **Greek model dependence:** vendor greeks differ by model; self-computed greeks need a pinned
  pricer. Cross-vendor results must note the greek source.
- **Dealer-sign assumption:** the whole framework rests on an unobservable dealer position. Test
  sensitivity to the sign/classification convention.
- **OI staleness / timing:** EOD OI is T-1; misalignment silently creates lookahead. Guard with tests.
- **Data history limits (verify by entitlement):** EODHD options from Q4 2023; Polygon trades/aggregates
  ~2014 and quotes ~2022; ORATS near-EOD 2007; Databento OPRA aggregates (trades/OHLCV/statistics/
  definitions/CBBO-1m) from Apr 2013, finer schemas (CMBP-1/TCBBO/CBBO-1s) from Mar 2023; CBOE 2004
  overall but later per product. Backtest length is bounded by the chosen vendor (see provider doc).
- **Corporate actions:** splits, special dividends, and symbol changes rewrite strikes, contract
  multipliers (adjusted / non-standard OCC options), and the underlying price series. Use
  split/dividend-adjusted underlying data and handle adjusted-option contracts explicitly, or they
  silently corrupt strike-relative metrics (moneyness, OI levels, GEX-by-strike).
- **Overfitting:** with many tunable proxy definitions, use out-of-sample/walk-forward splits and
  keep the parameter surface small and documented.

## 11. Definition of done for Phase 1

- Canonical chains reproducible from at least one vendor via a documented adapter.
- Full metric suite computed point-in-time with passing no-lookahead and golden tests.
- Backtester produces gross+net equity curves, drawdowns, and trade logs for at least one mechanical
  rule, compared against buy-and-hold and a random-entry baseline.
- A written scorecard, including negative results, with every proprietary-derived metric clearly
  labeled as a proxy and cross-linked to the terms doc.

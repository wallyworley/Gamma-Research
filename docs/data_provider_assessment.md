# Data Provider Assessment (Options + Equities)

> **Purpose.** Compare six market-data providers for a **research-only, non-live** GammaEdge-style
> backtesting effort. The workload needs historical option chains with open interest, implied
> volatility, and greeks (especially gamma and delta), across strikes and expirations, plus the
> underlying's price history. No live trading, no order routing.

Last researched: 2026-07-01. Capabilities and history windows change often; **re-verify against each
vendor's current docs and pricing before committing.** Entries below cite the vendor pages and
third-party summaries reviewed on the date above.

Legend: **Yes** = supported. **Partial** = supported with a meaningful caveat (see notes).
**No** = not offered (or must be derived yourself).

---

## Capability matrix

| Capability | Alpha Vantage | Polygon.io | ORATS | Databento | CBOE DataShop / LiveVol | EODHD |
|---|---|---|---|---|---|---|
| Historical stock prices | Yes | Yes | Partial | Yes | Yes | Yes |
| Intraday stock prices | Yes | Yes | Partial | Yes | Yes | Partial |
| Option chains (current) | Yes | Yes | Yes | Partial | Yes | Yes |
| Historical option chains | Yes | Yes | Yes | Yes | Yes | Partial |
| Open interest | Yes | Yes | Yes | Yes | Yes | Yes |
| Implied volatility | Yes | Yes | Yes | No | Yes | Yes |
| Greeks | Yes | Yes | Yes | No | Yes | Yes |
| Real-time options | Partial | Yes | Yes | Yes | Partial | No |
| OPRA-level data | No | Yes | Partial | Yes | Yes | No |

### How to read "Partial" and "No"

- **Greeks / IV = No (Databento):** by design. Databento does not ship vendor-calculated IV or
  greeks; it gives you raw prints and tutorials so you compute them yourself.
- **OPRA-level = No (Alpha Vantage, EODHD):** these serve aggregated/EOD snapshots, not the raw
  OPRA tick/quote feed.
- **OPRA-level = Partial (ORATS):** ORATS is a value-added vendor (smoothed greeks, near-close
  snapshots) rather than a raw OPRA tick tape.
- **Option chains current = Partial (Databento):** you reconstruct the chain from OPRA instrument
  definitions and quotes; there is no single "chain snapshot with greeks" endpoint.
- **Real-time options = Partial (Alpha Vantage):** premium REALTIME_OPTIONS snapshots are genuinely
  real-time (not merely delayed), but it is a snapshot endpoint, not a streaming OPRA feed. **= Partial (CBOE):** live or 15-min-delayed via APIs / LiveVol Pro
  platform; DataShop itself is bulk historical. **= No (EODHD):** end-of-day focus, updated after close.
- **Historical stock / intraday = Partial (ORATS):** underlying prices come bundled with the options
  data; ORATS is options-first, not a general equities-history vendor.
- **Historical option chains = Partial (EODHD):** supported but **shallow** (EOD only, from Q4 2023).
- **Intraday stock = Partial (EODHD):** intraday equities exist but with limited interval/history depth.

---

## Provider notes

### Alpha Vantage
- **History depth:** 20+ years of daily/weekly/monthly equities; intraday at 1/5/15/30/60-min.
  Options endpoints (REALTIME_OPTIONS, HISTORICAL_OPTIONS) provide chains with greeks and IV on
  premium tiers; **HISTORICAL_OPTIONS covers daily EOD options back to 2008** (a deeper start date
  than Polygon/EODHD/Databento, though EOD-only, not intraday or tick).
- **Strengths:** cheapest and simplest to integrate; JSON REST; greeks + IV included; long EOD
  options history (2008); good for a quick prototype and for equities history.
- **Weaknesses:** options are snapshot/EOD granularity, not tick. Real-time options **are** offered
  (premium REALTIME_OPTIONS snapshot, with optional greeks/IV); the real limits are that it is a
  snapshot endpoint rather than a streaming feed, and it is **not raw OPRA**. Rate-limited on lower tiers.
- **Fit:** fast prototyping and underlying-price history. Premium ~$50-$250/mo.

### Polygon.io
- **History depth:** in current Polygon/Massive flat-file docs, options **quotes** start ~2022, while
  **trades and minute/day aggregates go back to ~2014** (older Polygon docs cited 2016, so verify by
  entitlement). Sources real-time and historical from **OPRA across all 17 US options exchanges**.
  Full option-chain snapshot endpoint returns greeks, IV, and open interest; equities well covered.
- **Strengths:** strong all-rounder; genuine OPRA-level tick data plus convenience snapshots with
  Polygon-computed greeks/IV; WebSocket streaming; developer-friendly REST.
- **Weaknesses:** greeks/IV are Polygon-computed (may be missing for deep-ITM contracts); options
  history starts more recently than ORATS/CBOE. OPRA real-time needs an entitled plan.
- **Fit:** best single-vendor balance of OPRA fidelity + ready greeks for both prototype and scale-up.

### ORATS
- **History depth:** near-EOD full chains back to **2007** (snapshotted ~14 min before close);
  1-minute intraday since ~**Aug 2020** (per ORATS' 1-minute data page; some ORATS materials say
  Oct 2020, so confirm); 2-minute archive Jan 2015-Sep 2020 (described as raw / no greeks).
- **Strengths:** purpose-built options analytics; high-quality greeks (delta, gamma, theta, vega,
  rho) plus smoothed/theoretical values and proprietary IV forecasts; OI and volume included; live
  API for prices/greeks/theos/IVs; deepest ready-made greeks history of this set.
- **Weaknesses:** values are opinionated/smoothed (a feature for research, a caveat for microstructure
  purists); not a raw OPRA tape; equities are secondary. Priced as a premium analytics vendor.
- **Fit:** strongest choice when you want clean, pre-computed greeks/IV and long historical chains
  without building a pricer.

### Databento
- **History depth (schema-specific):** the OPRA (OPRA.PILLAR) live capture began **March 28, 2023**,
  and as of a May 2025 backfill the dataset now reaches **April 1, 2013** for the consolidated
  aggregate schemas (trades, OHLCV, statistics, instrument definitions, CBBO-1m). The finer schemas
  (CMBP-1, TCBBO, CBBO-1s) remain from **March 28, 2023**. So "history" depends on the schema: ~2013
  for 1-minute / last-sale aggregates, ~2023 for tick / 1-second consolidated book. ~2M option tickers
  across US equity options, plus CME/ICE; equities via separate datasets. Confirm depth and
  entitlement per schema.
- **Strengths:** rawest microstructure (MBP-1/top-of-book, last sale, tick), true OPRA fidelity,
  transparent and cost-efficient per-GB pricing, OI via statistics schema, expirations via
  instrument definitions; live + historical.
- **Weaknesses:** **no vendor greeks or IV** (compute yourself); no turnkey chain-with-greeks
  snapshot; tick / 1-second history only from 2023 (aggregates reach 2013); steeper engineering lift.
- **Fit:** best when you want full control and OPRA accuracy and are willing to build your own
  greek/IV pipeline. Pairs well as the raw-data layer under your own pricer.

### CBOE DataShop / LiveVol
- **History depth (product-specific):** DataShop markets data from **2004** overall, but individual
  listed-options products start later depending on the product (e.g., LiveVol Pro time & sales from
  ~2011). Treat "2004" as the deepest available for some EOD products, not a uniform floor; check the
  specific product's start date. Products: EOD summary and 3:45pm snapshots, 1-min/n-min interval
  summaries, and trade-by-trade (TBT), all over OPRA.
- **Strengths:** authoritative (exchange operator/SIP participant); deepest history; optional
  "Calcs" add IV + greeks to EOD and intraday products; dedicated trade-by-trade greeks product; OI
  available on interval summaries and EOD summary.
- **Weaknesses:** primarily a **bulk-download** model (DataShop) rather than a streaming developer
  API, though Trade Review / All Access APIs exist (live or 15-min delayed); can get expensive;
  LiveVol Pro platform is a separate ~$380/mo product.
- **Fit:** best for deep, authoritative historical research datasets and when you want the source of
  record. Good for bulk backtest corpora; less convenient for low-latency app development.

### EODHD (EOD Historical Data)
- **History depth:** EOD options for **6,600+ US stocks from Q4 2023** (~2.5 yrs); broad global
  equities EOD history; some intraday equities.
- **Strengths:** cheapest full-featured options **history with all five greeks + IV + OI**
  (open_interest, change, pct-change) and 42+ fields per contract; simple REST/SDKs/MCP.
- **Weaknesses:** **EOD only** (no real-time, no OPRA tick); shallow options history (from 2023);
  intraday equities limited.
- **Fit:** best budget starting point for EOD backtests where intraday microstructure is not required.

---

## Recommendation for this project (research-only backtest)

Priorities: (1) historical chains with OI + IV + **gamma/delta greeks**, (2) enough history to
backtest, (3) reproducibility, (4) cost.

- **Prototype / lowest cost:** **EODHD** (EOD greeks+IV+OI from 2023) or **Alpha Vantage**. Enough
  to build and validate the metric-computation layer against real chains without big spend.
- **Primary research vendor (recommended):** **ORATS** for the cleanest ready-made greeks/IV and the
  longest turnkey chain history (2007), or **Polygon.io** if you want OPRA-level fidelity plus
  computed greeks in one place. ORATS minimizes pricer-building; Polygon maximizes raw-data control
  while still giving greeks.
- **Deepest / authoritative history:** **CBOE DataShop** (back to 2004, Calcs greeks) for a
  gold-standard backtest corpus.
- **Rawest control layer:** **Databento** (OPRA tick) *if* you build your own greek/IV pipeline;
  otherwise its lack of vendor greeks is a real cost for a gamma-centric project.

**Suggested phase-1 stack:** start on **EODHD** (cheap EOD, to build the pipeline), then graduate the
gamma computations to **ORATS or Polygon** for greek quality and history depth. Keep the data layer
behind an adapter interface so vendors are swappable (see `phase_1_plan.md`).

> **Data-quality reminder for gamma work:** greeks and IV are model-dependent. When a vendor supplies
> them (AV, Polygon, ORATS, CBOE, EODHD), record the vendor's model assumptions. When it does not
> (Databento), pin your own pricer (model, rate, dividend, and IV-solve conventions) so results are
> reproducible and comparable across vendors.

### Cross-cutting caveats (apply to every vendor)

- **Entitlements gate features.** Real-time, OPRA tick, greeks/IV add-ons, and deep history are
  frequently on higher plans or paid add-ons, not the base tier. The matrix marks a capability as
  supported by the vendor; confirm your specific plan actually includes it before relying on it.
- **Three different kinds of "data."** Keep separate: (1) raw market data (trades/quotes/OI straight
  from the exchange or OPRA), (2) vendor-calculated analytics (greeks/IV a vendor computes with its
  own model), and (3) user-computed analytics (what you calculate yourself). Do not mix them
  silently; label the source of every greek and IV value.
- **Open interest is T-1 / start-of-day.** OCC/exchange OI reflects the prior session and is applied
  at the start of the day, not live intraday (per the Cboe DataShop FAQ). Align OI point-in-time as
  T-1 in any backtest to avoid lookahead.

---

## Sources

- [Alpha Vantage - API documentation](https://www.alphavantage.co/documentation/)
- [Polygon.io - Options API](https://polygon.io/options) and [Options API for Business](https://polygon.io/business-options)
- [ORATS - Options Data API](https://orats.com/data-api), [Near-EOD data since 2007](https://orats.com/near-eod-data), [1-minute intraday since Aug 2020](https://orats.com/one-minute-data)
- [Databento - Options market data](https://databento.com/options), [OPRA.PILLAR dataset](https://databento.com/datasets/OPRA.PILLAR), [OPRA improvements / 2013 backfill](https://databento.com/blog/opra-improvements-coming-soon), [Computing option greeks blog](https://databento.com/blog/option-greeks)
- [Cboe DataShop](https://datashop.cboe.com/), [Option EOD Summary](https://datashop.cboe.com/option-eod-summary), [US Options Trade-by-Trade Greeks](https://datashop.cboe.com/us-options-trade-by-trade-greeks), [LiveVol Pro](https://datashop.cboe.com/livevol-pro), [DataShop FAQ (OI timing)](https://datashop.cboe.com/faqs)
- [EODHD - US Stock Options API](https://eodhd.com/lp/us-stock-options-api), [Options marketplace](https://eodhd.com/marketplace/unicornbay/options)
- Third-party comparisons: [QuantVPS - Best APIs for Historical Options Data](https://www.quantvps.com/blog/best-apis-for-historical-options-market-data-volatility), [QuantVPS - Options Data API](https://www.quantvps.com/blog/options-data-api)

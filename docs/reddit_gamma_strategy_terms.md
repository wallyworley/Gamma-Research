# GammaEdge-Style Gamma Strategy Terms

> **Purpose.** Reverse-engineer and document the vocabulary used by a "GammaEdge-style"
> options gamma-exposure trading framework, using only public or API-accessible references.
> This is a **research document**, not trading advice and not an endorsement. GammaEdge is a
> third-party product (gammaedge.com / gammaedge.us); the terms below are reconstructed from
> its public blog posts, newsletter, and social posts, cross-checked against standard options
> market-structure literature (SqueezeMetrics, SpotGamma, and general options theory).
>
> **No formulas are invented.** Where a metric's exact formula is not public, it is labeled
> **Proprietary** and paired with a clearly marked *transparent approximation* we can compute
> ourselves from raw option-chain data. Approximations are our own reconstructions, not the
> vendor's method.

Last researched: 2026-07-01.

---

## Status legend

| Tag | Meaning |
|-----|---------|
| **Known** | Definition corroborated by public documentation (GammaEdge public content and/or standard options literature). |
| **Inferred** | Not published verbatim. Decoded from naming conventions, GammaEdge usage examples, and standard options mechanics. Reasonable but unconfirmed. |
| **Unknown / Proprietary** | GammaEdge-internal. No reliable public definition or exact formula. A transparent approximation is proposed instead. |

### Foundational caveat (applies to every term)

Dealer-vs-customer positioning is **not directly observable** from public option-chain data.
Every GEX-style metric depends on a *dealer-sign assumption* (the most common convention:
dealers are net long calls and net short puts, so `Net GEX = Call gamma - Put gamma`). Different
vendors use different sign and classification rules, so the same term can produce different
numbers across tools. Treat all dealer-positioning metrics as **modeled proxies**, not ground truth.

---

## Quick reference table

| Term | Status | One-line meaning |
|------|--------|------------------|
| PTrans / Pos_Trans | Known (concept), Proprietary (formula) | Upside strike where gamma turns call-dominated; an acceleration trigger, not resistance. |
| NTrans / Neg_Trans | Known (concept), Proprietary (formula) | Downside strike where gamma turns put-dominated; an acceleration trigger, not support. |
| ZeroGEX | Known | The gamma-flip price where net dealer GEX crosses zero (positive to negative). |
| +GEX / plus_GEX | Known | Positive-gamma regime: dealers long gamma, hedging dampens volatility (mean-reverting). |
| -GEX / minus_GEX | Known | Negative-gamma regime: dealers short gamma, hedging amplifies volatility (trending). |
| COTMP | Thinly corroborated (behavior), Inferred (acronym) | Downside support level from OTM-put OI concentration; supportive in a bullish regime. |
| COTMC | Thinly corroborated (behavior), Inferred (acronym) | Upside resistance from OTM-call OI concentration; where call holders monetize. |
| CITMP | Inferred | Upside level from ITM-put OI concentration (put strikes above spot). |
| CITMC | Inferred | Downside level from ITM-call OI concentration (call strikes below spot). |
| COI / POI | Known (acronym); Inferred (as a level) | Call Open Interest / Put Open Interest concentration levels (call-wall / put-wall analogs). |
| GEX Ratio | Known (concept), Proprietary (exact formula) | Balance-of-power ratio of call vs put gamma; individual names typically read > 1. |
| Dealer delta balance | Known (concept), Proprietary (exact formula) | Net modeled dealer delta (DEX), tracked above vs below spot. |
| db_change | Inferred | Change in delta balance over an interval (intraday or day-over-day). |
| Grade 11 / structural grade | Unknown / Proprietary | A GammaEdge market-structure score; scale and formula not public. |

---

## The base concept: GEX (Gamma Exposure)

**Status: Known (two standard conventions; mind the units).**

GEX estimates how much stock dealers must trade to stay delta-neutral as the underlying moves. It is
the shared root of every term in this document. There is **more than one "standard" formula**, and
they differ in units, so pin one and label it:

1. **Share-exposure form (SqueezeMetrics base).** The original SqueezeMetrics definition is a
   share-count exposure, with puts signed negative:

   ```
   GEX_shares = Gamma x OpenInterest x ContractSize(100)      ; puts negative
   ```

2. **Dollar-per-1%-move form (common "dollar GEX").** Many chart vendors (e.g. Quant Data) report
   the dollar value of the re-hedge per 1% move:

   ```
   GEX_$per1% = Gamma x OpenInterest x ContractSize(100) x Spot^2 x 0.01
   ```

   This equals the share form scaled by Spot and by a 1% (= 0.01 x Spot) move. It is a derived
   restatement, not a different phenomenon.

```
Net GEX = sum over strikes/expirations of ( Call GEX - Put GEX )   ; sign per dealer convention
```

- **Positive Net GEX** implies dealers are net long gamma. Hedging is counter-cyclical
  (sell rallies, buy dips), which tends to *dampen* volatility.
- **Negative Net GEX** implies dealers are net short gamma. Hedging is pro-cyclical
  (buy rallies, sell dips), which tends to *amplify* volatility.

> Notes: (a) The share form is the SqueezeMetrics base; the `Spot^2 x 0.01` form is a dollar-notional
> restatement, not "the one true formula." Record which one you use. (b) The sign depends entirely on
> the unobservable dealer-position assumption (see Foundational caveat). (c) GammaEdge's internal GEX
> may use its own conventions, so its published levels can differ. We treat the forms above as our
> transparent, reproducible baselines.

---

## Term-by-term

### PTrans / Pos_Trans (Positive Transition)
- **Status:** Known concept, Proprietary exact formula.
- **Public definition (GammaEdge):** "The strike where gamma becomes call-dominated above
  current price." GammaEdge stresses these are **acceleration triggers, not support/resistance**:
  price is *expected to break through and continue*, because once price crosses the level,
  dealer delta grows quickly and hedging (buying) accelerates the move higher.
- **Transparent approximation:** Walk strikes above spot; find the first strike (or zone) where
  cumulative call gamma exposure begins to dominate put gamma exposure and net dealer gamma turns
  supportive of trend continuation. Practically: the lowest strike above spot where
  `rolling(Call GEX - Put GEX)` crosses from stabilizing to accelerating under the chosen dealer-sign
  convention. Label output as a proxy; GammaEdge's exact window and weighting are unpublished.

### NTrans / Neg_Trans (Negative Transition)
- **Status:** Known concept, Proprietary exact formula.
- **Public definition (GammaEdge):** "The strike where gamma becomes put-dominated below current
  price." The mirror of PTrans: below NTrans, aggressive dealer hedging (selling) *accelerates*
  moves lower. Again an acceleration trigger, not a bounce level.
- **Transparent approximation:** Mirror of PTrans on the downside: the highest strike below spot
  where put gamma exposure begins to dominate and hedging flips pro-cyclical.

### ZeroGEX (Zero Gamma / Gamma Flip)
- **Status:** Known.
- **Definition:** The underlying price at which **net dealer GEX crosses zero**, flipping the
  regime from positive gamma (above) to negative gamma (below). Widely documented (SpotGamma,
  Perfiliev, gexboard, etc.) as the "gamma flip" or "zero gamma level."
- **Interpretation:** Above ZeroGEX, hedging tends to stabilize price (low-vol regime). Below it,
  hedging tends to destabilize (high-vol regime). Crossing down through ZeroGEX often precedes
  volatility expansion.
- **Transparent approximation:** Solve for the spot level S where `Net GEX(S) = 0` by
  recomputing the chain's aggregate gamma at candidate prices (interpolate across a spot grid).

### +GEX / plus_GEX
- **Status:** Known.
- **Definition:** The **positive-gamma regime / zone** (net dealer GEX > 0, i.e. price above
  ZeroGEX). Dealers long gamma, counter-cyclical hedging, volatility-dampening, mean-reverting
  behavior around pins. In GammaEdge shorthand, "PGEX" also appears as the positive-gamma side used
  in strategies like "PTrans2PGEX."
- **Transparent approximation:** Regime flag = `Net GEX > 0`. Magnitude = summed positive GEX.

### -GEX / minus_GEX
- **Status:** Known.
- **Definition:** The **negative-gamma regime / zone** (net dealer GEX < 0, price below ZeroGEX).
  Dealers short gamma, pro-cyclical hedging, volatility-amplifying, trend-extending behavior.
- **Transparent approximation:** Regime flag = `Net GEX < 0`. Magnitude = summed negative GEX.

### COTMP
- **Status:** Thinly corroborated behavior (vendor social posts), **Inferred acronym**.
- **Documented behavior (GammaEdge social posts, thin evidence):** "COTMP levels ... the key downside level that
  we expect to be supportive in a bullish environment." So COTMP is a **downside support level**.
- **Inferred expansion:** `C-OTM-P` = **Concentration of OTM Puts**. Out-of-the-money puts have
  strikes *below* spot, which is consistent with COTMP being a downside level. Acts like a put-wall
  style floor.
- **Transparent approximation:** Strike below spot with the greatest OTM-put open-interest (or
  put-gamma) concentration. Report as "OTM-put OI concentration level (COTMP proxy)."

### COTMC
- **Status:** Thinly corroborated behavior (vendor social posts), **Inferred acronym**.
- **Documented behavior (GammaEdge social posts, thin evidence):** "profit taking into our COTMC level. This is a
  point where historically we observe monetization of calls." So COTMC is an **upside resistance /
  call-monetization level**.
- **Inferred expansion:** `C-OTM-C` = **Concentration of OTM Calls**. OTM calls have strikes
  *above* spot, consistent with COTMC being an upside level. Call-wall analog where long calls are
  sold/monetized.
- **Transparent approximation:** Strike above spot with the greatest OTM-call OI (or call-gamma)
  concentration. Report as "OTM-call OI concentration level (COTMC proxy)."

### CITMP
- **Status:** **Inferred** (no public text definition found; explained only in GammaEdge video content).
- **Inferred expansion:** `C-ITM-P` = **Concentration of ITM Puts**. In-the-money puts have strikes
  *above* spot, so CITMP would be an **upside** structural level. Completes the moneyness grid with
  COTMC on the upside.
- **Transparent approximation:** Strike above spot with the greatest ITM-put OI concentration.

### CITMC
- **Status:** **Inferred** (no public text definition found).
- **Inferred expansion:** `C-ITM-C` = **Concentration of ITM Calls**. In-the-money calls have
  strikes *below* spot, so CITMC would be a **downside** structural level, pairing with COTMP below.
- **Transparent approximation:** Strike below spot with the greatest ITM-call OI concentration.

> **Moneyness grid (inferred, ties the four C_TM_ terms together).**
> The four levels appear to bucket open-interest concentration by moneyness and option type
> relative to spot:
>
> | | Below spot (downside) | Above spot (upside) |
> |---|---|---|
> | **Puts** | COTMP (OTM puts) | CITMP (ITM puts) |
> | **Calls** | CITMC (ITM calls) | COTMC (OTM calls) |
>
> COTMP and COTMC are corroborated by GammaEdge posts, though much of that evidence is social/video
> snippets, so treat the corroboration as thin. CITMP and CITMC are inferred by symmetry. A consistent
> alternate reading is "monetization" zones: COTMC = where call holders take profit (resistance),
> COTMP = where put holders take profit (support). This does not change the tags above.

### COI / POI
- **Status:** Known acronym (Call / Put Open Interest); **Inferred** as a GammaEdge level implementation.
- **Definition:** **C**all **O**pen **I**nterest and **P**ut **O**pen **I**nterest. GammaEdge uses
  them as **OI concentration levels** ("levels where calls/puts have significant positioning"),
  functioning as call-wall / put-wall analogs and price magnets/barriers.
- **Ambiguity to resolve in code:** "COI/POI" can mean either (a) the *aggregate* call/put OI for
  the name, or (b) the *specific strike* holding the largest call/put OI. GammaEdge usage leans
  toward (b) as a level. Implement both; default to the strike-level interpretation for signals.
- **Transparent approximation:** `COI_level = argmax_strike(Call OI)`, `POI_level = argmax_strike(Put OI)`.
- **Timing caveat:** Exchange/OCC open interest is published for the *prior* session and applied at
  start-of-day, not live intraday. Align OI as T-1 to avoid lookahead (see phase plan).

### GEX Ratio
- **Status:** Known concept, **Proprietary exact formula**.
- **Public description (GammaEdge):** Gauges "the balance of power in the options market";
  "individual stocks typically maintain a ratio greater than 1"; the edge is in "the trend and where
  the current ratio sits within its historical range." Used alongside delta balance to hold winners
  and exit deteriorating setups.
- **Transparent approximation:** `GEX Ratio ~= |aggregate Call GEX| / |aggregate Put GEX|`
  across the chain, tracked as a time series and compared to its own historical percentile.
  (Numerator/denominator convention is unconfirmed; alternatives include positive-GEX/negative-GEX.
  Pick one, document it, keep it fixed.)

### Dealer delta balance ("Delta Balance")
- **Status:** Known concept (DEX), **Proprietary exact formula**.
- **Public description (GammaEdge):** "your structural GPS ... tracks how call and put positioning
  (expressed through Delta) evolves above and below current price levels." This is the delta-exposure
  (DEX) analog of GEX: the net modeled dealer delta the Street must hedge.
- **Transparent approximation:**
  `DEX_strike = DealerSign x Delta x OpenInterest x 100 x Spot`, summed across strikes, and
  reported **split into above-spot vs below-spot** buckets to mirror GammaEdge's "above/below current
  price" framing. Dealer sign follows the stated convention (long calls, short puts) unless a
  trade-classification rule is available.

### db_change
- **Status:** **Inferred** (naming; "db" = delta balance).
- **Inferred definition:** The **change in dealer delta balance** over an interval (intraday tick,
  bar-over-bar, or day-over-day). Rising db_change = accumulating positive dealer delta (hedging
  demand shifting bullish); falling = the reverse.
- **Transparent approximation:** `db_change_t = DeltaBalance_t - DeltaBalance_{t-1}` for the chosen
  interval. Also useful: sign flips and rate-of-change of db as a momentum/deterioration signal.

### Grade 11 / structural grade
- **Status:** **Unknown / Proprietary.**
- **What is public:** GammaEdge markets a "structural grade" / market-structure scoring output.
  No public source defines the scale, the inputs, or their weights. "Grade 11" implies an ordinal
  scale (a numbered grade, plausibly 1..N), but the range and rubric are **not confirmed**. Do not
  assume 11 is a maximum or that the scale is 1-11.
- **Do not reverse-engineer a fake formula.** Instead, propose a **transparent composite score** we
  define and own, so backtests are reproducible:
  1. Regime term: sign and magnitude of Net GEX (are we in +GEX or -GEX).
  2. GEX-ratio percentile: where today's GEX Ratio sits in its trailing historical range.
  3. Delta-balance skew: above-spot vs below-spot DEX imbalance and its `db_change` trend.
  4. Distance to ZeroGEX: how close/far spot is from the flip.
  5. Proximity to key OI levels: distance to nearest COTMP/COTMC/COI/POI.

  Combine into an explicit ordinal (e.g., a documented 0-10 or 1-N scale) with published weights.
  **Label every output "GammaEdge-inspired proxy grade," never "Grade 11."** If GammaEdge later
  publishes its rubric, swap it in behind the same interface.

---

## What we can compute transparently vs what stays a black box

| Reproducible from raw chains (Known/derivable) | Proxy only (Proprietary exact method) |
|---|---|
| GEX per strike and Net GEX | GammaEdge's exact PTrans/NTrans window + weighting |
| ZeroGEX (gamma flip) via spot grid | GammaEdge's exact GEX Ratio numerator/denominator |
| +GEX / -GEX regime flags | GammaEdge's exact delta-balance definition |
| Call/Put OI concentration levels (COI/POI, COTMP/COTMC proxies) | Grade / structural grade rubric and scale |
| DEX (dealer delta balance) under a stated sign convention | Any level that depends on unpublished trade classification |

**Rule for the codebase:** anything in the right column is emitted with a `_proxy` suffix and a
docstring pointing back to this file. No proxy is presented as the vendor's true value.

---

## Sources

Core GEX / dealer-hedging concepts:
- [SqueezeMetrics Gamma Exposure white paper (PDF)](https://squeezemetrics.com/monitor/download/pdf/white_paper.pdf)
- [SpotGamma - Gamma Exposure (GEX)](https://spotgamma.com/gamma-exposure-gex/)
- [InsiderFinance - The Ultimate Guide to Gamma Exposure (GEX)](https://www.insiderfinance.io/resources/the-ultimate-guide-to-gamma-exposure-gex)
- [Quant Data - What is Gamma Exposure (GEX)?](https://help.quantdata.us/en/articles/7852449-what-is-gamma-exposure-gex)
- [Perfiliev - How to Calculate Gamma Exposure and Zero Gamma Level](https://perfiliev.com/blog/how-to-calculate-gamma-exposure-and-zero-gamma-level/)
- [gexboard - Gamma Flip and Zero Gamma Level Explained](https://gexboard.com/learn/zero-gamma-gamma-flip)

GammaEdge-specific terms:
- [GammaEdge - Ultimate Guide to Trading Transition Zones (PTrans/NTrans)](https://www.gammaedge.com/blog/the-ultimate-guide-to-trading-transition-zones-how-to-read-market-structure-for-consistent-profits)
- [GammaEdge - PTrans2PGEX Mechanical Trading Strategy](https://www.gammaedge.us/ptrans-2-pgex-mechanical-trading-strategy/)
- [GammaEdge - Spot Trends Using GEX Ratio and Delta Balance](https://www.gammaedge.us/gex-ratio-and-delta-balance-spot-market-trends/)
- [GammaEdge - Delta Balance in Trading Guide](https://www.gammaedge.us/delta-balance-in-trading-guide/)
- [GammaEdge - How to Choose the Right Options Strike: ITM vs OTM vs ATM](https://www.gammaedge.com/blog/how-to-choose-the-right-options-strike-itm-vs-otm-vs-atm)
- GammaEdge social posts (primary but thin): COTMP as a supportive downside level
  ([x.com/GammaEdges/status/1954935608683495782](https://x.com/GammaEdges/status/1954935608683495782))
  and COTMC as a call-monetization resistance level
  ([x.com/GammaEdges/status/2001700459321704797](https://x.com/GammaEdges/status/2001700459321704797)),
  plus the "Importance of COTMP/COTMC" newsletter
  ([gammaedge.substack.com](https://gammaedge.substack.com/p/daily-digest-september-7-2023)).

> Several GammaEdge definitions (notably COTMP/COTMC/CITMP/CITMC exact expansions and the structural
> grade rubric) are delivered only in gated video/app content and are **not** available as public
> text. Those are marked Inferred or Proprietary above and must be validated before any live use.

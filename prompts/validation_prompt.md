# Cross-Model Validation Prompt

Use this to have an **independent model** (GPT, Gemini, a fresh Claude, etc.) fact-check the research
in this repo. The three documents live at:

- `/Users/walterworley/dev/gamma-research/docs/reddit_gamma_strategy_terms.md`
- `/Users/walterworley/dev/gamma-research/docs/data_provider_assessment.md`
- `/Users/walterworley/dev/gamma-research/docs/phase_1_plan.md`

**You must give the reviewer the actual document contents.** Either paste each document into the
matching slot at the bottom, or (if the model has file access) point it at the paths above. A reviewer
that cannot see the documents can only check the high-risk claim list, not do a real document review.
(This exact gap happened on the first pass: the placeholders were left unfilled, so the review was
claims-only.)

Goal: catch factual errors, misclassified confidence tags, invented formulas, and stale vendor
capabilities **before** any of this informs real work.

---

## PROMPT (paste everything from here down into the other model)

You are an independent, skeptical reviewer with two areas of expertise:
1. **Options market structure** (dealer gamma/delta hedging, GEX, open-interest analytics, and the
   GammaEdge product's public terminology).
2. **Market-data vendors** (Alpha Vantage, Polygon.io, ORATS, Databento, CBOE DataShop/LiveVol, EODHD).

You are validating three research documents produced by another AI model. Your job is to **verify or
refute their claims**, not to agree. Do not defer to the documents' stated confidence. Where you can,
use your own knowledge and (if available) live web search, and **cite sources**. If you cannot verify
a claim, say "Unverifiable" rather than guessing.

### Non-negotiable constraints the documents were supposed to follow
Check that they actually did:
- **No invented formulas.** Any formula presented as fact must be a genuinely public/standard one
  (e.g., the SqueezeMetrics GEX formula). Flag any formula that looks made up or is stated with
  false confidence.
- **Every term tagged** as one of: `known`, `inferred`, or `unknown/proprietary`. Challenge any tag
  you think is wrong (e.g., something marked "known" that is actually the author's inference).
- **Proprietary metrics get a labeled approximation**, never a fake "exact" value. Flag violations.

### What to check, document by document

**Document 1 - terms glossary (`reddit_gamma_strategy_terms.md`).** For each term
(PTrans/Pos_Trans, NTrans/Neg_Trans, ZeroGEX, +GEX, -GEX, COTMP, COTMC, CITMP, CITMC, COI/POI,
GEX Ratio, dealer delta balance, db_change, Grade 11 / structural grade):
- Is the definition consistent with public GammaEdge content and standard options theory?
- Is the confidence tag (known / inferred / unknown-proprietary) defensible?
- Is any formula invented or overstated?

**Document 2 - provider assessment (`data_provider_assessment.md`).**
- Verify each cell of the 9-capability matrix (historical stock, intraday stock, option chains,
  historical option chains, open interest, implied volatility, greeks, real-time options, OPRA-level)
  for all six vendors. Vendor capabilities and history windows change; prefer current vendor docs.

**Document 3 - phase 1 plan (`phase_1_plan.md`).**
- Sanity-check the backtest methodology, especially the no-lookahead / point-in-time claims, the
  open-interest T-1 timing caveat, and whether any step implies live trading (it should not).

### Targeted claims to stress-test (these are the highest-risk assertions)

Options terms:
1. GEX per-strike formula stated as `Gamma x OpenInterest x 100 x Spot^2 x 0.01`. Is this the
   standard public form, or is a factor wrong?
2. Claim: PTrans/NTrans are "acceleration triggers, not support/resistance" and price is expected to
   break through them. Correct per GammaEdge?
3. Claim: COTMP is a downside support level (supportive in a bullish regime); COTMC is an
   upside/call-monetization resistance level. Correct?
4. **Author's inference** that the acronyms expand as Concentration of OTM Puts (COTMP), OTM Calls
   (COTMC), ITM Puts (CITMP), ITM Calls (CITMC). Is this expansion supported anywhere public, or is
   it unconfirmed? (The docs mark it inferred - confirm that is the right call.)
5. The moneyness geometry used: put OTM when strike < spot and ITM when strike > spot; call ITM when
   strike < spot and OTM when strike > spot. Verify the grid.
6. Claim: GEX Ratio for individual stocks "typically > 1." Accurate characterization of GammaEdge's
   statement?
7. Claim: "Grade 11 / structural grade" scale and rubric are NOT public (so it is proprietary).
   Is there any public definition the author missed?

Vendor facts:
8. Databento provides **no** vendor greeks/IV (compute-your-own); OPRA history from ~March 28, 2023.
9. Polygon options quotes from ~2022, trades from ~2016; sources OPRA across all 17 US options
   exchanges; provides computed greeks/IV/OI.
10. ORATS near-EOD chains back to ~2007; 1-min intraday since ~Oct 2020; 2-min archive from ~2015.
11. CBOE DataShop history back to ~2004; greeks/IV via optional "Calcs."
12. EODHD options are EOD-only, ~6,600+ US stocks, history from ~Q4 2023.
13. Alpha Vantage offers options chains with greeks + IV, but no raw OPRA and only delayed/limited
    real-time.

### Also look for
- **Missing terms or capabilities** the documents should have covered.
- **Internal contradictions** across the three documents.
- **Overconfidence**: anything asserted as fact that is actually uncertain, model-dependent, or
  vendor-specific (especially anything resting on the unobservable dealer-vs-customer positioning
  assumption).

### Output format (required)
1. **Verdict table** - one row per checked claim:
   `Claim | Verdict (Confirmed / Partially correct / Incorrect / Unverifiable) | Evidence or source | Severity (High / Med / Low)`
2. **Must-fix before use** - bulleted list of High-severity errors, each with the correction.
3. **Tag corrections** - any term whose known/inferred/proprietary tag should change, and to what.
4. **Missing items** - terms, capabilities, or caveats that should be added.
5. **Overall confidence** - one paragraph: how trustworthy is this research as a foundation, and what
   are the top three things to fix or independently re-verify.

Be specific and cite sources. If the documents got something right, say so briefly; spend your effort
on what is wrong, stale, or unproven.

---

> **Reviewer guard:** if the three documents are not actually present below (you still see the
> `<<...>>` placeholders), STOP and request them. Do not attempt a full review from this prompt alone;
> at most, validate the high-risk claim list and state plainly that the document review was not possible.

### [ PASTE DOCUMENT 1 HERE: reddit_gamma_strategy_terms.md ]
<<replace this line with the full contents of reddit_gamma_strategy_terms.md>>

### [ PASTE DOCUMENT 2 HERE: data_provider_assessment.md ]
<<replace this line with the full contents of data_provider_assessment.md>>

### [ PASTE DOCUMENT 3 HERE: phase_1_plan.md ]
<<replace this line with the full contents of phase_1_plan.md>>

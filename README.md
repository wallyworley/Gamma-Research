# gamma-research

Research-only repo to reverse-engineer and document a **GammaEdge-style** options gamma-exposure
trading framework using public or API-accessible data.

**This repo does not run a live trader.** Phase 1 is offline backtesting research only.
Nothing here is trading advice, and GammaEdge is an unaffiliated third-party product; its terms are
reconstructed from public sources and clearly marked where inferred or proprietary.

## Docs

- [docs/reddit_gamma_strategy_terms.md](docs/reddit_gamma_strategy_terms.md) - glossary of the
  framework's terms, each tagged **known / inferred / unknown-proprietary**, with transparent
  approximations where the real formula is not public.
- [docs/data_provider_assessment.md](docs/data_provider_assessment.md) - capability comparison of
  Alpha Vantage, Polygon, ORATS, Databento, CBOE, and EODHD for options + equities data.
- [docs/phase_1_plan.md](docs/phase_1_plan.md) - design for a non-live backtesting engine.
- [docs/day30_research_decision_2026-07-19.md](docs/day30_research_decision_2026-07-19.md) -
  locked corrected-gamma walk-forward result: EOD OI-GEX level failed all four gates and is
  stopped as a standalone alpha lane.

## Ground rules

1. Research first. Do not build a live trader in Phase 1.
2. Do not invent formulas. Confirmed metrics get documented formulas; unconfirmed ones are labeled
   proprietary and paired with an owned, transparent approximation (suffixed `_proxy` in code).
3. Point-in-time correctness (no lookahead) is the top backtest requirement.

Last updated: 2026-07-19.

#!/usr/bin/env python3
"""Validate the backtest harness end-to-end on real price history.

Fetches daily bars for a ticker, builds a transparent price signal (time-series
momentum: long when the close is above its N-day moving average, else flat), and runs
the full scorecard: the sign-safe permutation timing test (primary), an exposure-matched
random control, a bootstrap CI on the mean bar return, and a buy-and-hold comparison.

The point is to exercise the whole pipeline on real data and confirm the machinery is
sound (an informationless signal should NOT clear the permutation test). Swap in the GEX
`signals.rules.regime_signal` once the nightly store has accumulated enough sessions - the
harness is identical; only the target-position series changes.

Usage:
    python3 scripts/backtest_demo.py [TICKER] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                                     [--lookback 50] [--long-short] [--permutations 1000]
Env: MASSIVE_API_KEY.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402

from src.backtest.bars import fetch_daily_bars  # noqa: E402
from src.eval.harness import scorecard  # noqa: E402


def momentum_target(bars: pd.DataFrame, lookback: int, *, long_short: bool) -> pd.Series:
    """Target weight decided at each close: long above the N-day MA, else flat (or short).

    Uses only information available at the decision bar (close and its trailing MA); the
    backtester fills at the NEXT open, so there is no look-ahead. NaN during the MA warmup.
    """
    ma = bars["close"].rolling(lookback).mean()
    above = bars["close"] > ma
    weight = above.astype(float) if not long_short else (above.astype(float) * 2.0 - 1.0)
    return weight.where(ma.notna())


def _fmt_pct(x) -> str:
    try:
        return f"{100 * float(x):+.1f}%"
    except (TypeError, ValueError):
        return str(x)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Backtest-harness validation on price history")
    ap.add_argument("ticker", nargs="?", default="SPY")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-07-02")
    ap.add_argument("--lookback", type=int, default=50)
    ap.add_argument("--long-short", action="store_true", help="short below the MA (else flat)")
    ap.add_argument("--permutations", type=int, default=1000)
    ap.add_argument("--controls", type=int, default=500)
    args = ap.parse_args(argv)

    bars = fetch_daily_bars(args.ticker, args.start, args.end)
    target = momentum_target(bars, args.lookback, long_short=args.long_short)
    card = scorecard(bars, target, n_permutations=args.permutations, n_controls=args.controls)

    kind = "long/short" if args.long_short else "long/flat"
    print(f"\n=== {args.ticker.upper()}  {args.lookback}d-MA momentum ({kind})  "
          f"{args.start}..{args.end}  {len(bars)} bars ===")
    s = card["strategy"]
    print(f"strategy   total return {_fmt_pct(s['total_return'])}  "
          f"(gross {_fmt_pct(s.get('gross_total_return'))}, cost drag {_fmt_pct(s.get('cost_drag'))}), "
          f"max DD {_fmt_pct(s['max_drawdown'])}, {s['n_trades']} trades")
    print(f"buy & hold total return {_fmt_pct(card['buy_and_hold_return'])}  "
          f"(strategy excess {_fmt_pct(card['excess_vs_buy_and_hold'])})")
    print(f"Sharpe {card['strategy_sharpe']:.2f}   "
          f"mean-bar-return 95% CI {tuple(round(100 * v, 3) for v in card['bootstrap_mean_ci_95'])}%")
    p = card["permutation_test"]
    print(f"\nPRIMARY timing test (sign-safe permutations, gross basis, n={p['n']}):")
    print(f"  strategy beats {_fmt_pct(p['strategy_percentile'])} of shuffles of its own weights")
    print(f"  (permutation mean {_fmt_pct(p['mean_return'])}; strategy gross {_fmt_pct(p['strategy_gross_return'])})")
    verdict = ("timing skill is plausible" if (p["strategy_percentile"] or 0) >= 0.95
               else "NO evidence of timing skill beyond exposure")
    print(f"  -> {verdict}")
    c = card["random_control"]
    print(f"exposure-matched control (long-only, prob={c['exposure_matched_prob']:.2f}): "
          f"strategy beats {_fmt_pct(c['strategy_percentile'])} of {c['n']} controls")
    print(f"\nconfig_hash {card['config_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

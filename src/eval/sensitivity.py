"""Sign-convention sensitivity sweep (F11, quant review item 5).

The dealer-sign convention is the framework's single biggest model assumption and
it is UNOBSERVABLE from public chains: the field has retired naive long-call/
short-put, and platforms disagree on GEX sign purely from positioning assumptions.
Review item 5 is blunt about the consequence: any conclusion that flips under the
alternate convention is dead on arrival.

`convention_sweep` wires that check into the real scorecard. It rebuilds the signal
under each dealer-sign convention (varying ONLY that convention, via
dataclasses.replace on the pinned config - the same pattern as
metrics.flow.net_gex_by_convention), scores each with harness.scorecard, and emits
a verdict: does the conclusion FLIP across conventions? A flip is declared when
either the strategy's permutation-test percentile crosses 0.5 (skill vs no-skill
swaps sides) OR the strategy's total return changes sign (long turns into short).
Both are conclusion-level reversals that ride entirely on the unobservable
assumption, so the sweep surfaces them explicitly.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..config import EngineConfig
from ..metrics._common import resolve_config
from .harness import scorecard

_DEFAULT_CONVENTIONS = ("long_call_short_put", "otm_customer")


def _finite(x) -> bool:
    try:
        return bool(np.isfinite(x))
    except (TypeError, ValueError):
        return False


def _verdict(per_convention: dict) -> dict:
    """Decide whether a conclusion flips across conventions.

    * crosses_permutation_half: some convention scores below 0.5 and another above -
      the timing-skill verdict swaps sides (skillful vs not) with the assumption.
    * total_return_sign_change: some convention is net long-like (return > 0) and
      another net short-like (return < 0) - the position itself reverses.
    Either reversal => flips True (the result cannot be trusted; item 5).
    """
    pcts = [v["strategy_percentile"] for v in per_convention.values() if _finite(v["strategy_percentile"])]
    trs = [v["total_return"] for v in per_convention.values() if _finite(v["total_return"])]

    crosses_half = bool(pcts) and (min(pcts) < 0.5) and (max(pcts) > 0.5)
    sign_change = any(t > 0 for t in trs) and any(t < 0 for t in trs)
    return {
        "flips": bool(crosses_half or sign_change),
        "crosses_permutation_half": crosses_half,
        "total_return_sign_change": sign_change,
        "per_convention": per_convention,
    }


def convention_sweep(chains, bars, *, signal_builder,
                     conventions=_DEFAULT_CONVENTIONS,
                     config: EngineConfig | None = None,
                     **scorecard_kwargs) -> dict:
    """Score ``signal_builder``'s rule under each dealer-sign convention; flag flips.

    ``signal_builder`` is a callable ``(chains, config) -> target-weight series``: it
    is re-invoked once per convention with a config whose dealer_sign_convention has
    been swapped (everything else held fixed), so the sign assumption is the ONLY
    thing that varies across the sweep. Each rebuilt signal is scored by
    harness.scorecard under the matching config (so each scorecard's config_hash
    records its convention). Extra keyword args (e.g. n_permutations, n_controls,
    bootstrap_n, random_seed) pass straight through to scorecard, so a caller can run
    it fast.

    Returns ``{convention: scorecard, ..., "verdict": {...}}`` where the verdict's
    ``flips`` is True when the permutation percentile crosses 0.5 OR the total return
    changes sign across conventions - a conclusion riding on the unobservable
    assumption (item 5), dead on arrival.
    """
    cfg = resolve_config(config)
    out: dict = {}
    per_convention: dict = {}
    for conv in conventions:
        conv_cfg = replace(cfg, metrics=replace(cfg.metrics, dealer_sign_convention=conv))
        signal = signal_builder(chains, conv_cfg)
        card = scorecard(bars, signal, config=conv_cfg, **scorecard_kwargs)
        out[conv] = card
        per_convention[conv] = {
            "strategy_percentile": card["permutation_test"]["strategy_percentile"],
            "total_return": card["strategy"]["total_return"],
        }
    out["verdict"] = _verdict(per_convention)
    return out


__all__ = ["convention_sweep"]

"""Point-in-time gamma-structure metrics computed from canonical chains.

M2: Net GEX, per-strike GEX, +/-GEX regime, ZeroGEX (gex.py, blackscholes.py).
M3: the proxy suite - DEX / dealer delta balance, GEX Ratio, OI-concentration
levels (COI/POI, COTMP/COTMC/CITMP/CITMC), PTrans/NTrans, and grade_proxy. All
read the canonical schema and take conventions from EngineConfig; proprietary-
derived metrics are labeled proxies. Needs the data stack.
"""

from ._common import greek_coverage
from .blackscholes import bs_gamma
from .dex import DexBalance, contract_dex, db_change, dealer_delta_balance
from .gex import (
    GexSnapshot,
    contract_gex,
    gamma_snapshot,
    gex_by_strike,
    net_gex,
    regime,
    zero_gex,
)
from .grade import DEFAULT_WEIGHTS, GradeProxy, grade_proxy
from .levels import (
    MoneynessLevels,
    OiLevels,
    Transitions,
    gamma_transitions,
    moneyness_levels,
    oi_levels,
)
from .ratios import gex_ratio, trailing_percentile

__all__ = [
    # M2
    "bs_gamma",
    "greek_coverage",
    "contract_gex",
    "net_gex",
    "gex_by_strike",
    "regime",
    "zero_gex",
    "GexSnapshot",
    "gamma_snapshot",
    # M3: DEX
    "contract_dex",
    "DexBalance",
    "dealer_delta_balance",
    "db_change",
    # M3: ratios
    "gex_ratio",
    "trailing_percentile",
    # M3: levels
    "OiLevels",
    "oi_levels",
    "MoneynessLevels",
    "moneyness_levels",
    "Transitions",
    "gamma_transitions",
    # M3: grade
    "GradeProxy",
    "grade_proxy",
    "DEFAULT_WEIGHTS",
]

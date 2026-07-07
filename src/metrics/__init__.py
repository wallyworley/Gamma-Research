"""Point-in-time gamma-structure metrics computed from canonical chains.

M2: Net GEX, per-strike GEX, +/-GEX regime, ZeroGEX (gex.py, blackscholes.py).
M3: the proxy suite - DEX / dealer delta balance, GEX Ratio, OI-concentration
levels (COI/POI, COTMP/COTMC/CITMP/CITMC), PTrans/NTrans, and grade_proxy.
Batch B (flow metrics, 2026-07 quant review): volume-weighted GEX, normalized GEX,
and a dealer-sign convention sweep (flow.py); vanna/charm dealer exposures
(vanna_charm.py, blackscholes.py); and expiration-calendar features (expiry.py).
All read the canonical schema and take conventions from EngineConfig; proprietary-
derived metrics are labeled proxies. Needs the data stack.
"""

from ._common import greek_coverage
from .blackscholes import bs_charm, bs_gamma, bs_vanna
from .dex import DexBalance, contract_dex, db_change, dealer_delta_balance
from .expiry import days_to_monthly_opex, oi_expiring_within, third_friday
from .flow import (
    contract_gex_volume_proxy,
    gex_normalized,
    gex_volume_by_strike_proxy,
    net_gex_by_convention,
    net_gex_volume_proxy,
    option_notional,
)
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
from .vanna_charm import (
    CharmExposure,
    VannaExposure,
    net_charm_exposure,
    net_vanna_exposure,
)

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
    # Batch B: flow metrics
    "contract_gex_volume_proxy",
    "net_gex_volume_proxy",
    "gex_volume_by_strike_proxy",
    "option_notional",
    "gex_normalized",
    "net_gex_by_convention",
    # Batch B: vanna / charm exposures
    "bs_vanna",
    "bs_charm",
    "VannaExposure",
    "CharmExposure",
    "net_vanna_exposure",
    "net_charm_exposure",
    # Batch B: expiration calendar
    "third_friday",
    "days_to_monthly_opex",
    "oi_expiring_within",
]

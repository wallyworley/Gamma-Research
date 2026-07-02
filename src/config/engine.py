"""Pinned engine configuration: pricer, costs, backtest, and metric conventions.

Reproducibility is a Phase 1 guiding principle (docs/phase_1_plan.md section 3):
runs are only comparable if the pricer model, rates/dividends, IV solver, cost
model, and the dealer-sign convention are all pinned and recorded. This module
is that pin.

Design mirrors src/ingest/schema.py: stdlib-only, dataclasses are the single
source of truth for defaults, and `config/engine.toml` is a human-readable
mirror kept honest by a drift test (tests/test_config.py). Every config carries
a `config_hash()` so a run can stamp exactly which assumptions produced it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping

CONFIG_VERSION = "1.0.0"


class ConfigError(ValueError):
    """Raised on unknown keys or invalid values in a config payload."""


@dataclass(frozen=True)
class PricerConfig:
    """Option pricer + IV solver. Pinned so greeks are reproducible across runs.

    When greeks come from a vendor, `_greek_source` in the chain records that;
    when self-computed, these settings define the model (docs section 6, risk
    "Greek model dependence").
    """

    model: str = "black_scholes"
    risk_free_rate: float = 0.04       # annualized; flat until a curve is wired
    dividend_yield: float = 0.0        # continuous annualized
    day_count: str = "act/365"
    iv_method: str = "brent"
    iv_tol: float = 1e-6
    iv_max_iter: int = 100
    iv_vol_lo: float = 1e-4            # IV solve bracket, lower bound
    iv_vol_hi: float = 5.0             # IV solve bracket, upper bound


@dataclass(frozen=True)
class CostConfig:
    """Transaction costs. First-class so results are shown gross and net
    (docs section 7 "Costs", section 8 "Cost sensitivity")."""

    commission_per_trade: float = 0.0
    slippage_bps: float = 1.0
    half_spread_bps: float = 0.0       # half the quoted spread, in bps on traded notional


@dataclass(frozen=True)
class BacktestConfig:
    """Event-loop + fill assumptions. Defaults encode the no-lookahead fill rule:
    never fill same-bar-close on the signal bar (docs section 7 "Fills")."""

    initial_capital: float = 100_000.0
    # allow_same_bar_fill=False => decide at bar t's close, fill at t+1's open (the
    # no-lookahead default). True fills at t's own close (look-ahead; comparison only).
    allow_same_bar_fill: bool = False
    base_currency: str = "USD"


@dataclass(frozen=True)
class MetricsConfig:
    """Foundational metric conventions. The dealer-sign convention is the
    framework's central unobservable assumption (docs section 3, risk
    "Dealer-sign assumption"); pin it here and test sensitivity to it."""

    gex_convention: str = "dollar_per_1pct"          # "dollar_per_1pct" | "shares"
    # Standard convention (terms doc "Foundational caveat"): dealers net long calls,
    # net short puts, so Net GEX = Call gamma - Put gamma (call +1, put -1).
    dealer_sign_convention: str = "long_call_short_put"
    # ZeroGEX search grid (hashed, so a run records the range it searched). A None
    # flip means "no crossing in this grid", not "no flip exists" (F10).
    zerogex_grid_lo_frac: float = 0.7
    zerogex_grid_hi_frac: float = 1.3
    zerogex_grid_n: int = 121


_SECTIONS = {
    "pricer": PricerConfig,
    "costs": CostConfig,
    "backtest": BacktestConfig,
    "metrics": MetricsConfig,
}


@dataclass(frozen=True)
class EngineConfig:
    """Top-level pinned configuration bundle."""

    pricer: PricerConfig = PricerConfig()
    costs: CostConfig = CostConfig()
    backtest: BacktestConfig = BacktestConfig()
    metrics: MetricsConfig = MetricsConfig()
    version: str = CONFIG_VERSION

    @classmethod
    def default(cls) -> "EngineConfig":
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pricer": asdict(self.pricer),
            "costs": asdict(self.costs),
            "backtest": asdict(self.backtest),
            "metrics": asdict(self.metrics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EngineConfig":
        """Build from a (possibly partial) mapping, merged onto defaults.

        Unknown top-level or section keys raise ConfigError, so a typo in a
        config file fails loudly instead of being silently ignored.
        """
        known_top = set(_SECTIONS) | {"version"}
        for key in data:
            if key not in known_top:
                raise ConfigError(f"unknown config key {key!r}; expected {sorted(known_top)}")

        kwargs: dict[str, Any] = {}
        for name, section_cls in _SECTIONS.items():
            section = data.get(name, {})
            if not isinstance(section, Mapping):
                raise ConfigError(f"config section [{name}] must be a table, got {type(section).__name__}")
            valid = {f.name for f in fields(section_cls)}
            unknown = set(section) - valid
            if unknown:
                raise ConfigError(f"unknown keys in [{name}]: {sorted(unknown)}; expected {sorted(valid)}")
            defaults = section_cls()
            merged = {f.name: section.get(f.name, getattr(defaults, f.name)) for f in fields(section_cls)}
            kwargs[name] = section_cls(**merged)

        kwargs["version"] = data.get("version", CONFIG_VERSION)
        return cls(**kwargs)

    @classmethod
    def from_toml(cls, path: str) -> "EngineConfig":
        """Load config from a TOML file (stdlib tomllib, Python 3.11+)."""
        import tomllib

        with open(path, "rb") as fh:
            return cls.from_dict(tomllib.load(fh))

    def config_hash(self) -> str:
        """Stable short hash of the full config, for stamping reproducible runs."""
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


__all__ = [
    "CONFIG_VERSION",
    "ConfigError",
    "PricerConfig",
    "CostConfig",
    "BacktestConfig",
    "MetricsConfig",
    "EngineConfig",
]

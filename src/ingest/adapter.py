"""Vendor-agnostic option-chain adapter interface.

One interface sits in front of every data source (docs/phase_1_plan.md
guiding principle "Vendor-swappable data" and architecture section 4). A concrete
adapter's only job is to turn a vendor payload into a canonical DataFrame; the
base class enforces the schema so a broken adapter fails loudly at ingestion
instead of silently corrupting downstream metrics.

M1 wires the first concrete adapter (recommend EODHD for cheap EOD greeks/IV/OI);
M6 swaps in ORATS/Polygon for greek quality and history depth. Because every
adapter emits the same validated schema, that swap is a config change, not a
rewrite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING, Any, Callable

from .schema import validate_frame

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime pandas dependency
    import pandas as pd


class ChainAdapter(ABC):
    """Base class for a point-in-time option-chain source.

    Contract:
      * ``fetch_raw(symbol, quote_date)`` pulls one snapshot for one symbol as of
        one date, in whatever shape the vendor returns (JSON, CSV rows, arrow, ...).
      * ``normalize(raw, symbol, quote_date)`` maps that payload onto
        ``schema.CANONICAL_FIELDS`` and returns a pandas DataFrame. It is
        responsible for: mapping option-type casing to 'call'/'put', attaching
        ``underlying_price`` (spot at ``quote_ts``), setting ``oi_asof_date`` when
        the vendor discloses open-interest timing, and stamping ``_adapter`` /
        ``_greek_source`` / ``_iv_source`` provenance.
      * ``load(...)`` is the template method callers use. It fetches, normalizes,
        and then *validates* against the canonical schema before returning, so no
        adapter can leak a non-conforming frame into the metric engine.

    Subclasses override ``fetch_raw`` and ``normalize`` only. Set ``name`` to the
    short vendor id; it is also what belongs in each row's ``_adapter`` column.
    """

    #: short vendor id, e.g. "eodhd"; also written into the _adapter column.
    name: str = ""

    @abstractmethod
    def fetch_raw(self, symbol: str, quote_date: date, **kwargs: Any) -> Any:
        """Pull one point-in-time snapshot for ``symbol`` as of ``quote_date``."""

    @abstractmethod
    def normalize(self, raw: Any, *, symbol: str, quote_date: date) -> "pd.DataFrame":
        """Map a raw vendor payload onto a canonical DataFrame (unvalidated)."""

    def load(self, symbol: str, quote_date: date, **kwargs: Any) -> "pd.DataFrame":
        """Fetch + normalize + enforce the canonical contract. Raises SchemaError."""
        raw = self.fetch_raw(symbol, quote_date, **kwargs)
        frame = self.normalize(raw, symbol=symbol, quote_date=quote_date)
        validate_frame(frame)
        return frame


# --------------------------------------------------------------------------- #
# Tiny registry so config can select an adapter by name (EODHD -> ORATS -> ...).
# --------------------------------------------------------------------------- #

_REGISTRY: dict[str, type[ChainAdapter]] = {}


def register_adapter(cls: type[ChainAdapter]) -> type[ChainAdapter]:
    """Class decorator: register a ChainAdapter subclass under its ``name``."""
    key = getattr(cls, "name", "")
    if not key:
        raise ValueError(f"{cls.__name__} must set a non-empty class attribute 'name'")
    if key in _REGISTRY and _REGISTRY[key] is not cls:
        raise ValueError(f"adapter name {key!r} already registered to {_REGISTRY[key].__name__}")
    _REGISTRY[key] = cls
    return cls


def get_adapter(name: str) -> type[ChainAdapter]:
    """Look up a registered adapter class by vendor id."""
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise KeyError(f"no adapter named {name!r}; registered: {known}") from None


def registered_adapters() -> list[str]:
    """Names of all registered adapters."""
    return sorted(_REGISTRY)


__all__: list[str] = [
    "ChainAdapter",
    "register_adapter",
    "get_adapter",
    "registered_adapters",
]

# Referenced for typing clarity; keeps linters from flagging the Callable import
# as unused in environments that strip TYPE_CHECKING blocks.
_AdapterFactory = Callable[..., ChainAdapter]

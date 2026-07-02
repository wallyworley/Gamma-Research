"""Cboe delayed-quotes options adapter: free, no key, real full chains.

Cboe publishes a public delayed (~15 min) options snapshot as JSON - the whole
chain with greeks + IV + OI and the underlying spot in one response, no API key.
This makes it the zero-cost way to run the engine on real data today.

Endpoint:  https://cdn.cboe.com/api/global/delayed_quotes/options/{SYM}.json
           (indices use an underscore prefix, e.g. _SPX.json)
Payload:   {"timestamp": "YYYY-MM-DD HH:MM:SS" (UTC),
            "data": {"current_price": <spot>, "options": [
                {"option": <OSI symbol>, "bid","ask","last_trade_price",
                 "open_interest","volume","iv","delta","gamma","theta","vega","rho", ...}]}}
The per-contract `option` is an OSI symbol: root + YYMMDD + C/P + strike*1000.

Design notes:
  * `quote_ts` is taken from the payload's own `timestamp` (the snapshot as-of
    time, UTC), NOT from wall-clock, so a recorded fixture validates identically
    whenever the tests run.
  * Exchange open interest is a prior-session figure, so `oi_asof_date` is stamped
    T-1 weekday (`oi_lag_days`, default 1). This is the exchange convention, a
    firmer basis than the EODHD adapter's guess - but still weekday-only, NOT
    holiday-aware (F1).
  * Snapshot-only: there is no historical date parameter. `quote_date` is
    informational; the returned data is always the current snapshot. Build history
    by capturing daily going forward.

Caveats: unofficial CDN (no SLA - be gentle, cache); ~15-min delayed; some deep
contracts report iv/greeks as 0; do not redistribute.

`fetch_raw` does live HTTP; all parsing lives in `normalize` / the pure helpers,
unit-tested against a recorded fixture.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from ..adapter import ChainAdapter, register_adapter
from ..schema import PRIMARY_KEY, field_names, pandas_dtypes, validate_frame

_log = logging.getLogger(__name__)

_BASE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options"
_HTTP_TIMEOUT = 30
# OSI: root (letters/digits) + YYMMDD + C|P + 8-digit strike (price * 1000).
_OSI = re.compile(r"^([A-Z0-9]+?)(\d{6})([CP])(\d{8})$")


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    f = _num(value)
    return None if f is None else int(f)


def _parse_osi(symbol: str) -> tuple[date, float, str] | None:
    """OSI option symbol -> (expiration, strike, 'call'|'put'); None if unparseable."""
    m = _OSI.match(symbol or "")
    if not m:
        return None
    _root, ymd, cp, strike8 = m.groups()
    try:
        exp = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    return exp, int(strike8) / 1000.0, ("call" if cp == "C" else "put")


def _parse_ts(value: str) -> datetime:
    """Parse the payload timestamp into a tz-aware UTC datetime."""
    if not value:
        raise ValueError("Cboe payload has no timestamp")
    parsed = datetime.fromisoformat(value)  # accepts 'YYYY-MM-DD HH:MM:SS'
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


@register_adapter
class CboeAdapter(ChainAdapter):
    """Canonical-chain adapter for Cboe's free delayed options quotes."""

    name = "cboe"

    def __init__(self, *, index: bool = False, oi_lag_days: int = 1, base_url: str = _BASE_URL):
        self.index = index          # True for index options (URL gets a '_' prefix)
        self.oi_lag_days = oi_lag_days
        self.base_url = base_url

    def _url(self, symbol: str) -> str:
        return f"{self.base_url}/{'_' if self.index else ''}{symbol.upper()}.json"

    def _oi_asof(self, quote_date: date):
        """Assumed OI-as-of session: quote_date - oi_lag_days weekdays (not holiday-aware)."""
        if self.oi_lag_days <= 0:
            return quote_date
        return (pd.Timestamp(quote_date) - pd.tseries.offsets.BusinessDay(self.oi_lag_days)).date()

    def fetch_raw(self, symbol: str, quote_date: date | None = None, **kwargs: Any) -> dict[str, Any]:
        """Fetch the current delayed snapshot for ``symbol`` (quote_date is informational)."""
        req = urllib.request.Request(self._url(symbol), headers={"User-Agent": "gamma-research/1.0"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode())

    def normalize(self, raw: dict[str, Any], *, symbol: str,
                  quote_date: date | None = None) -> pd.DataFrame:
        data = raw.get("data") or {}
        spot = _num(data.get("current_price"))
        if spot is None or spot <= 0:
            raise ValueError(f"Cboe: missing/invalid current_price for {symbol}")

        quote_ts = _parse_ts(raw.get("timestamp"))
        oi_asof = self._oi_asof(quote_ts.date())

        rows = []
        for o in data.get("options", []) or []:
            parsed = _parse_osi(o.get("option", ""))
            if parsed is None:
                continue
            exp, strike, opt_type = parsed
            rows.append({
                "symbol": symbol.upper(),
                "quote_ts": quote_ts,
                "expiration": exp,
                "strike": strike,
                "type": opt_type,
                "underlying_price": spot,
                "bid": _num(o.get("bid")),
                "ask": _num(o.get("ask")),
                "last": _num(o.get("last_trade_price")),
                "open_interest": _int(o.get("open_interest")),
                "oi_asof_date": oi_asof,
                "volume": _int(o.get("volume")),
                "iv": _num(o.get("iv")),
                "delta": _num(o.get("delta")),
                "gamma": _num(o.get("gamma")),
                "theta": _num(o.get("theta")),
                "vega": _num(o.get("vega")),
                "rho": _num(o.get("rho")),
                "_iv_source": self.name,
                "_greek_source": self.name,
                "_adapter": self.name,
            })

        df = pd.DataFrame(rows, columns=field_names())
        df["quote_ts"] = pd.to_datetime(df["quote_ts"], utc=True)
        df["expiration"] = pd.to_datetime(df["expiration"])
        df["oi_asof_date"] = pd.to_datetime(df["oi_asof_date"])
        scalar = {k: v for k, v in pandas_dtypes().items()
                  if k not in ("quote_ts", "expiration", "oi_asof_date")}
        df = df.astype(scalar)

        before = len(df)
        df = df.drop_duplicates(subset=list(PRIMARY_KEY), keep="last").reset_index(drop=True)
        if len(df) < before:
            _log.warning("Cboe %s: dropped %d duplicate contract row(s)", symbol.upper(), before - len(df))
        return df

    def load(self, symbol: str, quote_date: date | None = None, **kwargs: Any) -> pd.DataFrame:
        """Fetch + normalize + validate the current snapshot for ``symbol``."""
        raw = self.fetch_raw(symbol, quote_date, **kwargs)
        frame = self.normalize(raw, symbol=symbol, quote_date=quote_date)
        validate_frame(frame)
        return frame


__all__ = ["CboeAdapter"]

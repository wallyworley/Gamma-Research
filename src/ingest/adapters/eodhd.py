"""EODHD (UnicornBay) end-of-day options adapter - first M1 vendor.

Maps the EODHD US Stock Options EOD API onto the canonical chain schema
(src/ingest/schema.py). Chosen first for cheap EOD greeks/IV/OI
(docs/data_provider_assessment.md); options history reaches only ~Q4 2023, so
early backtests are shallow until a deeper-history vendor is graduated in (M6).

Endpoint (verified against the live API):
    GET https://eodhd.com/api/mp/unicornbay/options/eod
        ?filter[underlying_symbol]=AAPL
        &filter[tradetime_from]=YYYY-MM-DD&filter[tradetime_to]=YYYY-MM-DD
        &api_token=...&limit=1000&offset=0
Response is JSON:API: {"meta":{...}, "data":[{"id","type","attributes":{...}}]}.
Each contract's fields live under "attributes".

Two facts drive the design:
  * There is NO underlying spot field in the options payload (only a coarse,
    2-decimal `moneyness`). The canonical schema requires `underlying_price`, so
    the adapter makes a second call to the EOD stock endpoint and attaches that
    day's close to every row.
  * **Open-interest timing is an assumption, not a verified fact.** EODHD does not
    document what session its `open_interest` is as-of. Following the standard
    convention (OCC publishes a session's OI the next morning, so the freshest OI
    knowable at date T's close is session T-1's), the adapter stamps
    `oi_asof_date = T - oi_lag_days business days` (default 1). Nothing downstream
    shifts OI across time; a single snapshot just uses OI as-of that stamped date.
    **VERIFY before trusting live results (review finding F1):** fetch date T and
    T+1 for one symbol and compare `open_interest`; if EODHD already reports the
    T-1 figure under date T, keep `oi_lag_days=1`; if it reports same-session OI,
    set `oi_lag_days=0` and beware that same-session OI is not knowable at T's close.

**Chain-completeness caveat (review finding F2, UNVERIFIED):** `fetch_raw` filters
the EOD options endpoint by `tradetime`. If `tradetime` is a last-trade field,
that filter may drop contracts that did not trade on the date - exactly the deep
OTM wings whose open interest drives the COI/POI/COTMP levels. Verify against the
live API (tradetime-filtered contract count vs a date/expiry-filtered count)
before relying on any OI-concentration metric.

`fetch_raw` does live HTTP (integration-tested with a real token); all mapping
lives in `normalize`/`_extract_records`, unit-tested against a recorded fixture.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ..adapter import ChainAdapter, register_adapter
from ..schema import PRIMARY_KEY, field_names, pandas_dtypes

_log = logging.getLogger(__name__)

# US options mark at the equity close; EOD snapshot timestamp = that close in UTC.
# Using a real tz (not a fixed offset) keeps DST correct across the year.
_MARKET_CLOSE = (16, 0)
_MARKET_TZ = ZoneInfo("America/New_York")

_OPTIONS_URL = "https://eodhd.com/api/mp/unicornbay/options/eod"
_EOD_URL = "https://eodhd.com/api/eod/{symbol}.{exchange}"
_PAGE_LIMIT = 1000
_HTTP_TIMEOUT = 30


def _num(value: Any) -> float | None:
    """Coerce to float; None/""/unparseable -> None. Zeros are kept as 0.0."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    f = _num(value)
    return None if f is None else int(f)


def _extract_records(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the list of `attributes` dicts out of one JSON:API page."""
    return [row.get("attributes", {}) for row in page.get("data", [])]


def _session_close_utc(quote_date: date) -> datetime:
    """The equity-close timestamp for `quote_date`, in UTC (DST-aware)."""
    local = datetime(quote_date.year, quote_date.month, quote_date.day,
                     _MARKET_CLOSE[0], _MARKET_CLOSE[1], tzinfo=_MARKET_TZ)
    return local.astimezone(timezone.utc)


@register_adapter
class EodhdAdapter(ChainAdapter):
    """Canonical-chain adapter for the EODHD UnicornBay options EOD API."""

    name = "eodhd"

    def __init__(self, api_token: str | None = None, *, exchange: str = "US",
                 oi_lag_days: int = 1,
                 options_url: str = _OPTIONS_URL, eod_url: str = _EOD_URL):
        import os

        self.api_token = api_token or os.environ.get("EODHD_API_TOKEN")
        self.exchange = exchange
        # Business-day lag stamped into oi_asof_date (see module docstring, F1).
        # 1 = standard "OI is prior session"; 0 = same-session (only if verified).
        self.oi_lag_days = oi_lag_days
        self.options_url = options_url
        self.eod_url = eod_url

    def _oi_asof_date(self, quote_date: date):
        """The session the open interest is assumed as-of (quote_date - lag BDays)."""
        if self.oi_lag_days <= 0:
            return quote_date
        stamped = pd.Timestamp(quote_date) - pd.tseries.offsets.BusinessDay(self.oi_lag_days)
        return stamped.date()

    # ---- live I/O (integration-tested with a real token) ------------------ #

    def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode({**params, "api_token": self.api_token})
        req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "gamma-research/1.0"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode())

    def _fetch_underlying_close(self, symbol: str, quote_date: date) -> float | None:
        url = self.eod_url.format(symbol=symbol.upper(), exchange=self.exchange)
        iso = quote_date.isoformat()
        rows = self._get_json(url, {"from": iso, "to": iso, "fmt": "json"})
        if isinstance(rows, list) and rows:
            return _num(rows[-1].get("close"))
        return None

    def fetch_raw(self, symbol: str, quote_date: date, **kwargs: Any) -> dict[str, Any]:
        """Fetch the EOD chain (paginated) plus the underlying's EOD close."""
        if not self.api_token:
            raise ValueError("EODHD api_token not set (pass api_token= or set EODHD_API_TOKEN)")

        iso = quote_date.isoformat()
        records: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._get_json(self.options_url, {
                "filter[underlying_symbol]": symbol.upper(),
                "filter[tradetime_from]": iso,
                "filter[tradetime_to]": iso,
                "limit": _PAGE_LIMIT,
                "offset": offset,
            })
            batch = _extract_records(page)
            records.extend(batch)
            if len(batch) < _PAGE_LIMIT:
                break
            offset += _PAGE_LIMIT

        return {
            "records": records,
            "underlying_close": self._fetch_underlying_close(symbol, quote_date),
        }

    # ---- pure mapping (unit-tested against a fixture) --------------------- #

    def normalize(self, raw: dict[str, Any], *, symbol: str, quote_date: date) -> pd.DataFrame:
        records = raw.get("records", [])
        spot = _num(raw.get("underlying_close"))
        if spot is None:
            raise ValueError(
                f"EODHD: no underlying EOD close for {symbol} on {quote_date.isoformat()}; "
                "cannot set required underlying_price")

        quote_ts = _session_close_utc(quote_date)
        oi_asof = self._oi_asof_date(quote_date)  # assumed OI session (F1); see docstring
        rows = []
        for rec in records:
            opt_type = str(rec.get("type", "")).lower()
            rows.append({
                "symbol": symbol.upper(),
                "quote_ts": quote_ts,
                "expiration": rec.get("exp_date"),
                "strike": _num(rec.get("strike")),
                "type": opt_type,
                "underlying_price": spot,
                "bid": _num(rec.get("bid")),
                "ask": _num(rec.get("ask")),
                "last": _num(rec.get("last")),
                "open_interest": _int(rec.get("open_interest")),
                "oi_asof_date": oi_asof,
                "volume": _int(rec.get("volume")),
                "iv": _num(rec.get("volatility")),
                "delta": _num(rec.get("delta")),
                "gamma": _num(rec.get("gamma")),
                "theta": _num(rec.get("theta")),
                "vega": _num(rec.get("vega")),
                "rho": _num(rec.get("rho")),
                "_iv_source": self.name,
                "_greek_source": self.name,
                "_adapter": self.name,
            })

        df = pd.DataFrame(rows, columns=field_names())
        # Parse datetimes explicitly (None -> NaT) before applying scalar dtypes.
        df["quote_ts"] = pd.to_datetime(df["quote_ts"], utc=True)
        df["expiration"] = pd.to_datetime(df["expiration"])
        df["oi_asof_date"] = pd.to_datetime(df["oi_asof_date"])
        scalar = {k: v for k, v in pandas_dtypes().items()
                  if k not in ("quote_ts", "expiration", "oi_asof_date")}
        df = df.astype(scalar)

        # Drop duplicate contracts (e.g. from overlapping pagination) rather than
        # silently double-counting OI/GEX downstream. Loud, because it should not
        # happen (F5).
        before = len(df)
        df = df.drop_duplicates(subset=list(PRIMARY_KEY), keep="last").reset_index(drop=True)
        if len(df) < before:
            _log.warning("EODHD %s %s: dropped %d duplicate contract row(s)",
                         symbol.upper(), quote_date.isoformat(), before - len(df))
        return df


__all__ = ["EodhdAdapter"]

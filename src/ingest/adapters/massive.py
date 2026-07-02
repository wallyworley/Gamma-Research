"""Massive (formerly Polygon.io) options adapter - REST chain snapshot.

The website rebranded to massive.com but the API host is still api.polygon.io.
Uses the per-underlying Option Chain Snapshot, which carries greeks + IV + open
interest - the flat-file products (trades/quotes/aggregates) do NOT include greeks
or OI, so they cannot drive GEX.

Two tier realities on Options Starter (verified live): the options snapshot omits
the underlying price, and the real-time stock snapshot is 403 (not entitled). So
spot comes from the **previous completed daily bar** (`/v2/aggs/ticker/{sym}/prev`,
allowed on the tier), which also gives the session date. Run after the US close so
that bar is the just-closed session (this is an EOD adapter, like eodhd/cboe).

Endpoints (auth via `Authorization: Bearer <MASSIVE_API_KEY>`, kept out of URLs):
  GET /v2/aggs/ticker/{SYM}/prev              -> underlying close + session date
  GET /v3/snapshot/options/{SYM}?limit=250    -> chain, paginated via `next_url`

Per contract: `details.{contract_type,expiration_date,strike_price}`,
`greeks.{delta,gamma,theta,vega}` (NO rho -> null), `implied_volatility`,
`open_interest`, `day.{close,volume}`. Bid/ask are not entitled on this tier (null).
`oi_asof_date` = T-1 weekday (see _util / F1).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import date
from typing import Any

import pandas as pd

from ..adapter import ChainAdapter, register_adapter
from ..schema import PRIMARY_KEY, field_names, pandas_dtypes, validate_frame
from ._util import num, prior_weekday, session_close_utc, to_int

_log = logging.getLogger(__name__)

_BASE = "https://api.polygon.io"      # massive.com rebrand; API host unchanged
_HTTP_TIMEOUT = 30
_PAGE_LIMIT = 250
_MAX_PAGES = 400                      # safety cap (a huge chain is ~tens of pages)


@register_adapter
class MassiveAdapter(ChainAdapter):
    """Canonical-chain adapter for the Massive/Polygon options snapshot API."""

    name = "massive"

    def __init__(self, api_key: str | None = None, *, oi_lag_days: int = 1, base_url: str = _BASE):
        self.api_key = api_key or os.environ.get("MASSIVE_API_KEY")
        self.oi_lag_days = oi_lag_days
        self.base_url = base_url

    def _get(self, url: str) -> dict[str, Any]:
        """GET a path or a full next_url with the bearer header (key never in the URL)."""
        full = url if url.startswith("http") else self.base_url + url
        req = urllib.request.Request(full, headers={
            "Authorization": f"Bearer {self.api_key}", "User-Agent": "gamma-research/1.0"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode())

    def fetch_raw(self, symbol: str, quote_date: date | None = None, **kwargs: Any) -> dict[str, Any]:
        """Fetch the underlying's prior close (spot + session) plus the full chain."""
        if not self.api_key:
            raise ValueError("MASSIVE_API_KEY not set (pass api_key= or set MASSIVE_API_KEY)")
        sym = symbol.upper()

        prev = self._get(f"/v2/aggs/ticker/{sym}/prev").get("results") or []
        if not prev:
            raise ValueError(f"Massive: no previous-close bar for {sym}")
        bar = prev[0]
        session_date = pd.Timestamp(bar["t"], unit="ms", tz="UTC").date().isoformat()

        results: list[dict] = []
        url = f"/v3/snapshot/options/{sym}?limit={_PAGE_LIMIT}"
        for _ in range(_MAX_PAGES):
            page = self._get(url)
            results.extend(page.get("results") or [])
            nxt = page.get("next_url")
            if not nxt:
                break
            url = nxt
        return {"results": results, "underlying_close": num(bar.get("c")), "session_date": session_date}

    def normalize(self, raw: dict[str, Any], *, symbol: str,
                  quote_date: date | None = None) -> pd.DataFrame:
        spot = num(raw.get("underlying_close"))
        if spot is None or spot <= 0:
            raise ValueError(f"Massive: missing/invalid underlying close for {symbol}")
        session_date = date.fromisoformat(raw["session_date"])
        quote_ts = session_close_utc(session_date)
        oi_asof = prior_weekday(session_date, self.oi_lag_days)

        rows = []
        skipped_expired = skipped_bad = 0
        for r in raw.get("results") or []:
            d = r.get("details") or {}
            strike = num(d.get("strike_price"))
            ctype = str(d.get("contract_type", "")).lower()
            exp_s = d.get("expiration_date")
            if not exp_s or strike is None or ctype not in ("call", "put"):
                skipped_bad += 1
                continue
            exp = date.fromisoformat(exp_s)
            if exp < session_date:
                skipped_expired += 1
                continue
            g = r.get("greeks") or {}
            day = r.get("day") or {}
            rows.append({
                "symbol": symbol.upper(), "quote_ts": quote_ts, "expiration": exp,
                "strike": strike, "type": ctype, "underlying_price": spot,
                "bid": None, "ask": None, "last": num(day.get("close")),
                "open_interest": to_int(r.get("open_interest")), "oi_asof_date": oi_asof,
                "volume": to_int(day.get("volume")), "iv": num(r.get("implied_volatility")),
                "delta": num(g.get("delta")), "gamma": num(g.get("gamma")),
                "theta": num(g.get("theta")), "vega": num(g.get("vega")), "rho": None,
                "_iv_source": self.name, "_greek_source": self.name, "_adapter": self.name,
            })

        if not rows:
            raise ValueError(f"Massive {symbol.upper()}: no valid contracts "
                             f"(skipped {skipped_bad} malformed, {skipped_expired} expired)")
        if skipped_expired or skipped_bad:
            _log.warning("Massive %s: skipped %d expired + %d malformed contract(s)",
                         symbol.upper(), skipped_expired, skipped_bad)

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
            _log.warning("Massive %s: dropped %d duplicate contract row(s)", symbol.upper(), before - len(df))
        return df

    def load(self, symbol: str, quote_date: date | None = None, **kwargs: Any) -> pd.DataFrame:
        raw = self.fetch_raw(symbol, quote_date, **kwargs)
        frame = self.normalize(raw, symbol=symbol, quote_date=quote_date)
        validate_frame(frame)
        return frame


__all__ = ["MassiveAdapter"]

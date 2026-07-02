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
  * The payload's top-level `timestamp` is a **UTC generation clock** that keeps
    ticking after the close, so its UTC calendar date rolls over in the US evening
    (verified). Using it directly would mis-date any evening snapshot. So the
    **trading session** is derived from that timestamp's *Eastern* date, and
    `quote_ts` is anchored to that session's close (16:00 ET, in UTC) - the same
    model as the EODHD adapter. (Assumes the fetch happens during or after the
    session it reports, not in the pre-dawn ET window; and each pull is treated as
    a snapshot of that session, stamped at its close. An evening capture's
    `current_price` may include after-hours ticks yet is stamped as-of the close;
    option quotes are frozen at the close, so the spot/label mismatch is small.)
  * Exchange open interest is a prior-session figure, so `oi_asof_date` is stamped
    T-1 weekday from the session date (`oi_lag_days`, default 1). Exchange
    convention, but weekday-only, NOT holiday-aware (F1).
  * Snapshot-only: there is no historical date parameter. `quote_date` is
    informational (a mismatch warns); the data is always the current snapshot.
  * **Equities only for now.** Index chains with AM/PM settlement (e.g. SPX vs
    SPXW share expiration/strike/type but are distinct contracts) would collide on
    the canonical key; the adapter fails loudly on such chains rather than silently
    drop open interest. Index support needs a settlement field in the schema.

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
from zoneinfo import ZoneInfo

import pandas as pd

from ..adapter import ChainAdapter, register_adapter
from ..schema import PRIMARY_KEY, field_names, pandas_dtypes, validate_frame

_log = logging.getLogger(__name__)

_BASE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options"
_HTTP_TIMEOUT = 30
_ET = ZoneInfo("America/New_York")
_MARKET_CLOSE = (16, 0)
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


def _parse_osi(symbol: str) -> tuple[date, float, str, str] | None:
    """OSI option symbol -> (expiration, strike, 'call'|'put', root); None if bad."""
    m = _OSI.match(symbol or "")
    if not m:
        return None
    root, ymd, cp, strike8 = m.groups()
    try:
        exp = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    return exp, int(strike8) / 1000.0, ("call" if cp == "C" else "put"), root


def _parse_ts(value: str) -> datetime:
    """Parse the payload timestamp into a tz-aware UTC datetime."""
    if not value:
        raise ValueError("Cboe payload has no timestamp")
    parsed = datetime.fromisoformat(value)  # accepts 'YYYY-MM-DD HH:MM:SS'
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _session_close_utc(session_date: date) -> datetime:
    """The ET session close (16:00 America/New_York) for session_date, in UTC."""
    local = datetime(session_date.year, session_date.month, session_date.day,
                     _MARKET_CLOSE[0], _MARKET_CLOSE[1], tzinfo=_ET)
    return local.astimezone(timezone.utc)


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

        # Session = the ET date of the (UTC) generation timestamp; anchor quote_ts
        # to that session's close so oi_asof and expiry validation use the real
        # trading day, not the UTC date that rolls over in the US evening (B1).
        session_date = _parse_ts(raw.get("timestamp")).astimezone(_ET).date()
        quote_ts = _session_close_utc(session_date)
        oi_asof = self._oi_asof(session_date)
        if quote_date is not None and quote_date != session_date:
            _log.warning("Cboe %s: requested quote_date %s != live snapshot session %s "
                         "(Cboe is snapshot-only)", symbol.upper(), quote_date, session_date)

        roots_by_key: dict[tuple, set] = {}
        rows = []
        skipped_osi = skipped_expired = 0
        for o in data.get("options", []) or []:
            parsed = _parse_osi(o.get("option", ""))
            if parsed is None:
                skipped_osi += 1
                continue
            exp, strike, opt_type, root = parsed
            if exp < session_date:            # already-expired stray: drop, don't reject the chain
                skipped_expired += 1
                continue
            roots_by_key.setdefault((exp, strike, opt_type), set()).add(root)
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

        # Distinct OCC roots under one (expiration, strike, type) are AM/PM-settled
        # index variants (SPX vs SPXW), genuinely different contracts the canonical
        # key cannot tell apart. Merging would silently drop OI, so fail loudly (B2).
        collisions = {k: v for k, v in roots_by_key.items() if len(v) > 1}
        if collisions:
            key, roots = next(iter(collisions.items()))
            raise NotImplementedError(
                f"Cboe {symbol.upper()}: {len(collisions)} (expiration,strike,type) key(s) carry "
                f"multiple OCC roots (e.g. {sorted(roots)} at {key}) - AM/PM-settled index variants. "
                "The canonical schema cannot yet distinguish settlement, so they would be silently "
                "merged. Index / dual-settled chains are unsupported until settlement is in the schema.")
        if skipped_osi or skipped_expired:
            _log.warning("Cboe %s: skipped %d unparseable + %d expired contract(s)",
                         symbol.upper(), skipped_osi, skipped_expired)
        if not rows:
            n = len(data.get("options", []) or [])
            raise ValueError(
                f"Cboe {symbol.upper()}: no valid contracts (skipped {skipped_osi} unparseable, "
                f"{skipped_expired} expired of {n}); refusing to emit an empty chain")

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
            _log.warning("Cboe %s: dropped %d exact-duplicate contract row(s)", symbol.upper(), before - len(df))
        return df

    def load(self, symbol: str, quote_date: date | None = None, **kwargs: Any) -> pd.DataFrame:
        """Fetch + normalize + validate the current snapshot for ``symbol``."""
        raw = self.fetch_raw(symbol, quote_date, **kwargs)
        frame = self.normalize(raw, symbol=symbol, quote_date=quote_date)
        validate_frame(frame)
        return frame


__all__ = ["CboeAdapter"]

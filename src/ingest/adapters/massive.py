"""Massive (formerly Polygon.io) options adapter - REST chain snapshot.

The website rebranded to massive.com but the API host is still api.polygon.io.
Uses the per-underlying Option Chain Snapshot, which carries greeks + IV + open
interest - the flat-file products (trades/quotes/aggregates) do NOT include greeks
or OI, so they cannot drive GEX.

**Session + spot, the hard part (Options Starter tier).** The tier gives no
contemporaneous underlying price: the snapshot's `underlying_asset` carries only a
ticker (no price/quote/trade), the stock snapshot is 403, and the underlying's daily
aggregate bar *lags the options snapshot* - verified live, at 3.5h after the close
`/v2/aggs/.../prev` and `/range/1/day` still returned the prior session while the
options snapshot was already the just-closed session. Trusting `/prev` mislabeled the
whole frame by a session and mispriced spot by ~4.6% (review blocker). So instead:

  * **Session** = the most recent ET date among per-contract `day.last_updated`
    timestamps (the freshest day bar marks the current session).
  * **Spot** = Black-Scholes *delta-inversion* of the snapshot's own near-ATM greeks
    (`_util.bs_implied_spot`), median across liquid contracts. This spot is
    self-consistent with the gammas we integrate, and lands within a fraction of a
    percent (live AAPL: 16-98 contracts, p10-p90 band ~0.03-0.2%). A dispersion gate
    rejects unreliable clusters rather than emit a bad spot. A future entitled tier can
    still pass an authoritative `underlying_close` in the raw dict to override.

**Run after the US close (EOD adapter, like eodhd/cboe).** The snapshot is always
"current": derived session/spot/quote_ts describe whatever the chain reflects *now*.
After the close that is the just-closed session with 0DTE still present; an intraday run
would stamp an in-progress session with a *future* 16:00 close time. normalize stays
vendor-pure and clock-free; the wall-clock guards live outside it: the capture layer
drops a *dormant* chain (its derived session != the run day) and no-ops on non-trading
days, and the nightly runner (scripts/snapshot_universe.py) refuses to run before the
post-close window (capture.is_after_close), so a reboot-triggered catch-up can't write an
intraday chain as EOD. **High-yield names:** the inversion ignores dividends, so on the tight
tier the recovered spot runs ~q*tau low (AAPL -0.25%, AGNC ~13% yield -1.2%), and the wider
fallback tier (tau to 120d) roughly doubles that on high-yielders (up to ~3.5% low, CHMI).
The bias is bounded, one-directional, and still self-consistent with the vendor gammas for
GEX. Prefer the `underlying_close` override on an entitled tier if penny-accurate spot on
high-yielders matters.

Endpoints (auth via `Authorization: Bearer <MASSIVE_API_KEY>`, kept out of URLs):
  GET /v3/snapshot/options/{SYM}?limit=250    -> chain, paginated via `next_url`

Per contract: `details.{contract_type,expiration_date,strike_price}`,
`greeks.{delta,gamma,theta,vega}` (NO rho -> null), `implied_volatility`,
`open_interest`, `day.{close,volume,last_updated}`. Bid/ask are not entitled on this
tier (null). `oi_asof_date` = T-1 weekday (see _util / F1). A per-contract `day` bar
from an earlier session is nulled out rather than mislabeled as this session's
last/volume (review finding R1).
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any

import pandas as pd

from ..adapter import ChainAdapter, register_adapter
from ..schema import PRIMARY_KEY, field_names, pandas_dtypes, validate_frame
from ._util import (
    bs_implied_spot,
    et_date_from_epoch_ns,
    num,
    prior_weekday,
    session_close_utc,
    to_int,
)

_log = logging.getLogger(__name__)

_BASE = "https://api.polygon.io"      # massive.com rebrand; API host unchanged
_HTTP_TIMEOUT = 30
_HTTP_RETRIES = 4                     # transient network/429/5xx; batch of 5,300 needs it
_PAGE_LIMIT = 250
_MAX_PAGES = 400                      # safety cap (a huge chain is ~tens of pages)
_RETRY_CODES = frozenset({429, 500, 502, 503, 504})

# Greek delta-inversion spot recovery (see module docstring). Two tiers, tried in order:
# a tight, high-precision near-ATM band first (liquid names), then a wider fallback that
# recovers thin chains (few strikes/expiries) which would otherwise be dropped. The
# dispersion gate still guards every tier, so a genuinely inconsistent cluster is refused
# rather than emit a bad spot. Validated live: the fallback recovers ~half of the thin-chain
# failures; spots are ~1-2% of the reference close typically, but up to ~3.5% low on
# high-yield names (the dividend bias grows with the 120d horizon, q*tau; one-directional).
_SPOT_R = 0.045                       # flat risk-free; small effect at short tau
_TAU_MIN = 2 / 365                    # skip 0DTE blowups
# (tau_max, delta_lo, delta_hi, min_contracts, max_dispersion)
_SPOT_TIERS = (
    (60 / 365,  0.30, 0.70, 5, 0.02),   # primary: tight near-ATM, most reliable
    (120 / 365, 0.25, 0.75, 3, 0.03),   # fallback: wider band/horizon for thin chains
)


def _session_from_snapshot(results: list[dict]) -> date | None:
    """Session = the latest ET date among per-contract ``day.last_updated`` stamps."""
    latest: date | None = None
    for r in results:
        d = et_date_from_epoch_ns((r.get("day") or {}).get("last_updated"))
        if d is not None and (latest is None or d > latest):
            latest = d
    return latest


def _tier_spots(results: list[dict], session_date: date,
                tau_max: float, delta_lo: float, delta_hi: float) -> list[float]:
    """Delta-inverted spots for every contract inside one tier's near-ATM/horizon band."""
    xs: list[float] = []
    for r in results:
        d = r.get("details") or {}
        ctype = str(d.get("contract_type", "")).lower()
        exp_s = d.get("expiration_date")
        if ctype not in ("call", "put") or not exp_s:
            continue
        try:
            tau = (date.fromisoformat(exp_s) - session_date).days / 365.0
        except (TypeError, ValueError):
            continue
        if not (_TAU_MIN < tau < tau_max):
            continue
        delta = num((r.get("greeks") or {}).get("delta"))
        if delta is None:
            continue
        ncdf = delta if ctype == "call" else delta + 1.0
        if not (delta_lo < ncdf < delta_hi):
            continue
        s = bs_implied_spot(num(d.get("strike_price")), num(r.get("implied_volatility")),
                            delta, tau, ctype == "call", _SPOT_R)
        if s is not None and s > 0:
            xs.append(s)
    return xs


def _implied_spot(results: list[dict],
                  session_date: date) -> tuple[float | None, int, float | None, int | None]:
    """Median near-ATM delta-inverted spot. Returns (spot|None, n_used, dispersion, tier).

    Tries the tight primary tier first, then the wider fallback; each is gated on a
    minimum contract count and a maximum p10..p90 dispersion. Spot is None (caller refuses
    the symbol rather than write a bad underlying price) only if no tier qualifies.
    """
    n_last, disp_last = 0, None
    for tier, (tau_max, delta_lo, delta_hi, floor, max_disp) in enumerate(_SPOT_TIERS):
        xs = _tier_spots(results, session_date, tau_max, delta_lo, delta_hi)
        n_last = len(xs)
        if len(xs) < floor:
            continue
        xs.sort()
        med = statistics.median(xs)
        dispersion = (xs[(9 * len(xs)) // 10] - xs[len(xs) // 10]) / med if med else None
        disp_last = dispersion
        if dispersion is None or dispersion <= max_disp:
            return med, len(xs), dispersion, tier
    return None, n_last, disp_last, None


@register_adapter
class MassiveAdapter(ChainAdapter):
    """Canonical-chain adapter for the Massive/Polygon options snapshot API."""

    name = "massive"

    def __init__(self, api_key: str | None = None, *, oi_lag_days: int = 1, base_url: str = _BASE,
                 index_roots: frozenset[str] = frozenset()):
        self.api_key = api_key or os.environ.get("MASSIVE_API_KEY")
        self.oi_lag_days = oi_lag_days
        self.base_url = base_url
        # Canonical roots that are cash-settled indices on Polygon: the snapshot URL needs
        # the `I:` prefix (`I:SPX`), but the canonical/partition symbol stays plain (`SPX`).
        self.index_roots = frozenset(s.upper() for s in index_roots)

    def _polygon_ticker(self, sym: str) -> str:
        return f"I:{sym}" if sym in self.index_roots else sym

    def _get(self, url: str) -> dict[str, Any]:
        """GET a path or a full next_url with the bearer header (key never in the URL).

        Retries transient network errors and 429/5xx with linear backoff so a
        5,300-symbol batch survives blips; 4xx (auth/not-found) fail fast.
        """
        full = url if url.startswith("http") else self.base_url + url
        req = urllib.request.Request(full, headers={
            "Authorization": f"Bearer {self.api_key}", "User-Agent": "gamma-research/1.0"})
        last: Exception | None = None
        for attempt in range(_HTTP_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code not in _RETRY_CODES or attempt == _HTTP_RETRIES - 1:
                    raise
                last = e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == _HTTP_RETRIES - 1:
                    raise
                last = e
            time.sleep(1.5 * (attempt + 1))
        raise last  # unreachable, but keeps the type checker honest

    def fetch_raw(self, symbol: str, quote_date: date | None = None, **kwargs: Any) -> dict[str, Any]:
        """Fetch the full option-chain snapshot (paginated). Spot/session are derived in
        normalize from the snapshot itself, so this returns only the raw contracts."""
        if not self.api_key:
            raise ValueError("MASSIVE_API_KEY not set (pass api_key= or set MASSIVE_API_KEY)")
        if quote_date is not None:                                        # R2
            _log.warning("Massive %s: quote_date=%s ignored (the snapshot is always current)",
                         symbol.upper(), quote_date)
        sym = self._polygon_ticker(symbol.upper())

        results: list[dict] = []
        url = f"/v3/snapshot/options/{sym}?limit={_PAGE_LIMIT}"
        for _ in range(_MAX_PAGES):
            page = self._get(url)
            results.extend(page.get("results") or [])
            nxt = page.get("next_url")
            if not nxt:
                break
            url = nxt
        else:                                                            # R3: no break
            _log.warning("Massive %s: hit page cap _MAX_PAGES=%d; chain may be truncated",
                         sym, _MAX_PAGES)
        return {"results": results}

    def normalize(self, raw: dict[str, Any], *, symbol: str,
                  quote_date: date | None = None) -> pd.DataFrame:
        sym = symbol.upper()
        results = raw.get("results") or []

        # Session: explicit override (tests / future tiers) else derive from the snapshot.
        if raw.get("session_date"):
            session_date = date.fromisoformat(raw["session_date"])
        else:
            session_date = _session_from_snapshot(results)
        if session_date is None:
            raise ValueError(f"Massive {sym}: cannot determine session (no day-bar timestamps)")

        # Spot: authoritative vendor close if supplied, else greek delta-inversion.
        spot = num(raw.get("underlying_close"))
        if spot is not None and spot > 0:
            spot_source = "vendor_close"
        else:
            spot, n_used, dispersion, tier = _implied_spot(results, session_date)
            if spot is None:
                raise ValueError(
                    f"Massive {sym}: could not recover a reliable spot from greeks "
                    f"(n_used={n_used}, dispersion={dispersion})")
            spot_source = f"implied_delta_t{tier}"   # t0 = tight tier, t1 = wider fallback
            _log.info("Massive %s: greek-implied spot %.4f from %d near-ATM contracts "
                      "(tier %d, dispersion %.4f)", sym, spot, n_used, tier, dispersion or 0.0)

        quote_ts = session_close_utc(session_date)
        oi_asof = prior_weekday(session_date, self.oi_lag_days)

        rows = []
        skipped_expired = skipped_bad = stale_day = 0
        for r in results:
            d = r.get("details") or {}
            strike = num(d.get("strike_price"))
            ctype = str(d.get("contract_type", "")).lower()
            exp_s = d.get("expiration_date")
            if not exp_s or strike is None or ctype not in ("call", "put"):
                skipped_bad += 1
                continue
            try:
                exp = date.fromisoformat(exp_s)                          # R4: bad date -> skip
            except (TypeError, ValueError):
                skipped_bad += 1
                continue
            if exp < session_date:
                skipped_expired += 1
                continue

            g = r.get("greeks") or {}
            day = r.get("day") or {}
            # R1: only trust the day bar's last/volume if it belongs to THIS session.
            last_px = vol = None
            day_date = et_date_from_epoch_ns(day.get("last_updated"))
            if day_date == session_date:
                last_px = num(day.get("close"))
                vol = to_int(day.get("volume"))
            elif day_date is not None:
                stale_day += 1

            rows.append({
                "symbol": sym, "quote_ts": quote_ts, "expiration": exp,
                "strike": strike, "type": ctype, "underlying_price": spot,
                "bid": None, "ask": None, "last": last_px,
                "open_interest": to_int(r.get("open_interest")), "oi_asof_date": oi_asof,
                "volume": vol, "iv": num(r.get("implied_volatility")),
                "delta": num(g.get("delta")), "gamma": num(g.get("gamma")),
                "theta": num(g.get("theta")), "vega": num(g.get("vega")), "rho": None,
                "_iv_source": self.name, "_greek_source": self.name, "_adapter": self.name,
                "_spot_source": spot_source,
            })

        if not rows:
            raise ValueError(f"Massive {sym}: no valid contracts "
                             f"(skipped {skipped_bad} malformed, {skipped_expired} expired)")
        if skipped_expired or skipped_bad or stale_day:
            _log.warning("Massive %s: %d expired, %d malformed, %d stale-day-bar contract(s); "
                         "spot=%.4f via %s", sym, skipped_expired, skipped_bad, stale_day,
                         spot, spot_source)

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
            _log.warning("Massive %s: dropped %d duplicate contract row(s)", sym, before - len(df))
        return df

    def load(self, symbol: str, quote_date: date | None = None, **kwargs: Any) -> pd.DataFrame:
        raw = self.fetch_raw(symbol, quote_date, **kwargs)
        frame = self.normalize(raw, symbol=symbol, quote_date=quote_date)
        validate_frame(frame)
        return frame


__all__ = ["MassiveAdapter"]

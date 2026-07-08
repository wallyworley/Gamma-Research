"""ThetaData historical option-chain adapter - the validated backfill source.

Unlike the nightly Massive/Polygon snapshot (which only ever returns the *current*
session and gives no underlying spot), ThetaData serves point-in-time EOD history:
you ask for one past ``session`` and it returns that day's greeks/IV/NBBO plus a
morning open-interest print. We field-diffed it against our own store on two overlap
sessions: open interest matched 100.00% contract-for-contract, and their
``underlying_price`` matched our delta-inversion spot within 0.14%. So this adapter is
how the canonical store gets *history* it could never backfill from the snapshot source
(OI/greeks are not reconstructable after the fact).

Two endpoints are joined per session (both return one row per contract):
  * ``option_history_greeks_eod(symbol=ROOT, expiration="*", start_date=S, end_date=S)``
    -> open/high/low/close, volume, bid, ask (NBBO at close), delta/gamma/theta/vega/rho,
       implied_vol, iv_error, underlying_price (a real vendor close, not an inversion).
       Also carries vanna/charm and higher-order greeks that the canonical schema does
       not model; those are dropped.
  * ``option_history_open_interest(symbol=ROOT, expiration="*", date=S)``
    -> open_interest, timestamped ~06:30 ET (the morning OCC print = positions as of the
       PRIOR session's close, the same T-1 semantic we validated).

Point-in-time discipline (schema-enforced downstream, mirrored here):
  * ``quote_ts`` = the ET session close (16:00) in UTC, via ``_util.session_close_utc`` -
    NOT the vendor's raw feed clock, so its UTC date equals the trading session.
  * ``oi_asof_date`` = ``prior_trading_day(session, 1)`` (holiday-aware NYSE calendar):
    the 06:30 print reflects the prior close, so open interest is dated one session back,
    never as-of the quote date (anti-lookahead).
  * ``underlying_price`` = the vendor's ``underlying_price`` column (``_spot_source =
    "vendor_close"``). If it is missing/non-positive on every greeks row we raise rather
    than write a chain with no honest spot (GEX/DEX are undefined without it).

IV hygiene: ThetaData publishes an ``iv_error`` (the solver's fit residual) alongside
``implied_vol``. Where that residual is large (> ``_IV_ERROR_MAX``) or the reported vol is
non-positive, the fit is unreliable, so we null ``iv`` (a null is honest; a bad fit that
looks like data is not). The greeks are left as the vendor supplied them.

Index support (mirrors MassiveAdapter's ``index_roots``): on ThetaData each OCC root is a
distinct *symbol*, so an index like SPX is captured by querying EACH of its roots (SPX and
SPXW) separately and concatenating the results under ``symbol=SPX`` with a per-row ``root``.
The canonical key carries ``root``, so AM-settled SPX and PM-settled SPXW coexist at the
same (expiration, strike, type) without collision and GEX sums the whole index book. The
per-index root list is ``ROOT_MAP`` (documented, verifiable against
``option_list_symbols``); a root that returns no data for a session is skipped with a
warning, not treated as fatal.

Optional dependency: the ``thetadata`` client (and its ``python-dotenv`` dep) are imported
LAZILY inside ``fetch_raw`` so ``import src.ingest.adapters`` (and the nightly capture path)
works without them. They live in requirements-backfill.txt, never requirements.txt.
"""

from __future__ import annotations

import logging
import os
import statistics
import threading
from datetime import date
from typing import Any

import pandas as pd

from ..adapter import ChainAdapter, register_adapter
from ..schema import PRIMARY_KEY, field_names, pandas_dtypes, validate_frame
from ._util import num, prior_trading_day, session_close_utc, to_int

_log = logging.getLogger(__name__)

# iv_error above this (the vendor's solver residual) marks an unreliable fit -> null iv.
_IV_ERROR_MAX = 0.05

# OCC roots to query per captured index underlying. On ThetaData each root is its own
# symbol, so an index's whole book is the concat of these separate queries (stored under
# the plain index symbol, distinguished by the per-row `root`). Verifiable later against
# ``ThetaClient.option_list_symbols``; a listed root that returns no data on a given
# session is skipped, so an over-broad tuple is safe.
ROOT_MAP: dict[str, tuple[str, ...]] = {
    "SPX": ("SPX", "SPXW"),
    "NDX": ("NDX", "NDXP"),
    "RUT": ("RUT", "RUTW"),
    "XSP": ("XSP",),
    "DJX": ("DJX",),
    "OEX": ("OEX",),
}

_RIGHT_TO_TYPE = {"CALL": "call", "PUT": "put", "C": "call", "P": "put"}


class NoDataForSession(Exception):
    """Raised by ``fetch_raw`` when a session has no data for any queried root.

    A session older than the subscription's history floor, or before a symbol listed,
    returns empty/permission errors from every endpoint. That is a clean *skip* for a
    resumable backfill (the runner counts it, does not fail), distinct from a genuine
    error (auth failure, network) which must surface and stop nothing silently.
    """


def _coerce_session(value: "date | str") -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"cannot interpret {value!r} as a session date")


def _nonneg(value: Any, counters: dict) -> "float | None":
    """A price field (bid/ask/last) coerced to float, with NEGATIVE prints nulled.

    The vendor occasionally emits a negative option close (observed live: RUT
    2026-02-10, close=-2.67), which is meaningless for a price and would fail the
    canonical schema's non-negativity rule, killing the whole session for one bad
    cell. Same philosophy as the iv_error hygiene: a null is honest, a bad print
    that looks like data is not. Counted in ``neg_price_nulled``."""
    x = num(value)
    if x is not None and x < 0:
        counters["neg_price_nulled"] = counters.get("neg_price_nulled", 0) + 1
        return None
    return x


def _to_pandas(frame: Any) -> "pd.DataFrame":
    """Return a pandas DataFrame from a client result (polars or already pandas)."""
    to_p = getattr(frame, "to_pandas", None)
    return to_p() if callable(to_p) else frame


def _records(frame: Any) -> list[dict]:
    """Client frame -> JSON-friendly records: drop feed-clock columns, NaN/NaT -> None.

    The timestamp columns are dropped (quote_ts/oi_asof are derived from the session, not
    the raw feed clock), which also keeps a recorded fixture pure JSON. NaN/NaT become
    None so the numeric coercers (`num`/`to_int`) see missing as missing, not float('nan').
    """
    pdf = _to_pandas(frame)
    if pdf is None or len(pdf) == 0:
        return []
    drop = [c for c in ("timestamp", "underlying_timestamp") if c in pdf.columns]
    if drop:
        pdf = pdf.drop(columns=drop)
    pdf = pdf.astype(object).where(pd.notna(pdf), None)
    return pdf.to_dict(orient="records")


def _is_no_data_error(exc: BaseException, no_data_cls: type) -> bool:
    """True when ``exc`` means 'this session/root simply has no data for this tier'.

    Covers the library's ``NoDataFoundError`` and the gRPC PERMISSION_DENIED / NOT_FOUND /
    OUT_OF_RANGE a Standard-tier request gets for a session outside its history window. An
    ``AuthenticationError`` (bad key) is NOT here: it is a real failure and must propagate.
    """
    if isinstance(exc, no_data_cls):
        return True
    code = getattr(exc, "code", None)
    try:
        name = code().name if callable(code) else ""
    except Exception:  # noqa: BLE001 - some rendezvous objects raise from code()
        name = ""
    if name in ("PERMISSION_DENIED", "NOT_FOUND", "OUT_OF_RANGE"):
        return True
    msg = str(exc)
    return "PERMISSION_DENIED" in msg or "No data for the specified" in msg


@register_adapter
class ThetadataAdapter(ChainAdapter):
    """Canonical-chain adapter for ThetaData EOD history (backfill source)."""

    name = "thetadata"

    def __init__(self, api_key: str | None = None, *, oi_lag_days: int = 1,
                 index_roots: frozenset[str] = frozenset(), dotenv_path: str | None = None):
        self.api_key = api_key or os.environ.get("THETADATA_API_KEY")
        self.oi_lag_days = oi_lag_days
        self.index_roots = frozenset(s.upper() for s in index_roots)
        self.dotenv_path = dotenv_path
        self._client_obj: Any = None
        self._client_lock = threading.Lock()

    # ----- vendor client (lazy, so `import src.ingest.adapters` needs no thetadata) --- #

    def _client(self) -> Any:
        """Construct (once, thread-safely) the ThetaData client. Imported here so the
        optional ``thetadata`` dependency is never touched by the nightly capture path."""
        if self._client_obj is None:
            with self._client_lock:
                if self._client_obj is None:
                    from thetadata import ThetaClient  # optional dep, imported lazily
                    kw: dict[str, Any] = {"dataframe_type": "pandas"}
                    if self.api_key:
                        kw["api_key"] = self.api_key
                    if self.dotenv_path:
                        kw["dotenv_path"] = self.dotenv_path
                    self._client_obj = ThetaClient(**kw)
        return self._client_obj

    def _roots_for(self, symbol: str) -> tuple[str, ...]:
        """The OCC roots to query for ``symbol``: an index expands via ROOT_MAP, an
        equity is just itself."""
        s = symbol.upper()
        if s in self.index_roots:
            return ROOT_MAP.get(s, (s,))
        return (s,)

    def _fetch_endpoint(self, call, sym: str, session: date, root: str, label: str) -> list[dict]:
        """Run one endpoint call, converting a genuine no-data/permission result to an
        empty list (a clean per-root skip) while letting real errors (auth) propagate."""
        from thetadata.errors import NoDataFoundError  # optional dep, lazy
        try:
            return _records(call())
        except NoDataFoundError as exc:
            _log.debug("Thetadata %s: %s no data for root %s on %s (%s)",
                       sym, label, root, session, exc)
            return []
        except Exception as exc:  # noqa: BLE001 - classify, then re-raise real failures
            if _is_no_data_error(exc, NoDataFoundError):
                _log.debug("Thetadata %s: %s no data for root %s on %s (%s)",
                           sym, label, root, session, type(exc).__name__)
                return []
            raise

    def fetch_raw(self, symbol: str, quote_date: "date | str | None" = None,
                  **kwargs: Any) -> dict[str, Any]:
        """Pull greeks_eod + open_interest for every root of ``symbol`` on one session.

        Returns a raw dict {symbol, session_date, roots: {root: {greeks, open_interest}}}.
        A root with no data on the session is skipped with a warning. If NO root has data
        (session before the history floor / before the symbol listed), raises
        ``NoDataForSession`` so the backfill runner records a clean skip.
        """
        if not self.api_key:
            raise ValueError("THETADATA_API_KEY not set (pass api_key= or set THETADATA_API_KEY)")
        if quote_date is None:
            raise ValueError("Thetadata: quote_date (the session to backfill) is required")
        sym = symbol.upper()
        session = _coerce_session(quote_date)
        client = self._client()

        roots: dict[str, dict] = {}
        for root in self._roots_for(sym):
            greeks = self._fetch_endpoint(
                lambda root=root: client.option_history_greeks_eod(
                    symbol=root, expiration="*", start_date=session, end_date=session),
                sym, session, root, "greeks_eod")
            oi = self._fetch_endpoint(
                lambda root=root: client.option_history_open_interest(
                    symbol=root, expiration="*", date=session),
                sym, session, root, "open_interest")
            if not greeks and not oi:
                _log.warning("Thetadata %s: root %s returned no data for %s; skipping root",
                             sym, root, session.isoformat())
                continue
            roots[root] = {"greeks": greeks, "open_interest": oi}

        if not roots:
            raise NoDataForSession(
                f"Thetadata {sym}: no data for session {session.isoformat()} "
                "(before history floor / symbol not listed)")
        return {"symbol": sym, "session_date": session.isoformat(), "roots": roots}

    # ----- normalization -------------------------------------------------------------- #

    def _key(self, rec: dict, counters: dict) -> "tuple[date, float, str] | None":
        """(expiration, strike, type) join key for one endpoint record, or None if
        malformed (counted, not fatal)."""
        strike = num(rec.get("strike"))
        ctype = _RIGHT_TO_TYPE.get(str(rec.get("right", "")).upper())
        exp_s = rec.get("expiration")
        if strike is None or strike <= 0 or ctype is None or not exp_s:
            counters["malformed"] += 1
            return None
        try:
            exp = date.fromisoformat(str(exp_s)[:10])
        except (TypeError, ValueError):
            counters["malformed"] += 1
            return None
        return (exp, strike, ctype)

    def _build_row(self, *, sym: str, root: str, quote_ts, exp: date, strike: float,
                   ctype: str, spot: float, oi_asof, g: dict, o: dict, counters: dict) -> dict:
        """One canonical row from a greeks record (g, may be empty) and an OI record (o,
        may be empty). Applies the iv-error hygiene (unreliable fit -> honest null iv)."""
        iv = num(g.get("implied_vol"))
        iv_err = num(g.get("iv_error"))
        if iv is not None and (iv <= 0 or (iv_err is not None and iv_err > _IV_ERROR_MAX)):
            iv = None                                       # unreliable fit -> honest null
            counters["iv_nulled"] += 1
        return {
            "symbol": sym, "root": root, "quote_ts": quote_ts,
            "expiration": exp, "strike": strike, "type": ctype,
            "underlying_price": spot,
            "bid": _nonneg(g.get("bid"), counters), "ask": _nonneg(g.get("ask"), counters),
            "last": _nonneg(g.get("close"), counters),
            "open_interest": to_int(o.get("open_interest")) if o else None,
            "oi_asof_date": oi_asof, "volume": to_int(g.get("volume")),
            "iv": iv,
            "delta": num(g.get("delta")), "gamma": num(g.get("gamma")),
            "theta": num(g.get("theta")), "vega": num(g.get("vega")),
            "rho": num(g.get("rho")),
            # vanna/charm/vomma/... exist on greeks_eod but are not canonical -> dropped.
            "_iv_source": self.name, "_greek_source": self.name, "_adapter": self.name,
            "_spot_source": "vendor_close",
        }

    def _rows_for_root(self, root: str, section: dict, *, sym: str, session: date,
                       spot: float, quote_ts, oi_asof, counters: dict) -> list[dict]:
        """Join one root's greeks_eod and open_interest on (exp, strike, type).

        Rows present in either endpoint are kept: a greeks-only row gets null OI, an OI-only
        row gets null greeks. Expired rows (expiration < session) are dropped. One row is
        emitted per source record (rather than pre-collapsing by key), so a vendor that
        somehow repeats a contract survives to the exact-duplicate collapse / B2 collision
        guard in normalize() instead of silently losing a side's open interest here.
        """
        greeks = section.get("greeks") or []
        oi_recs = section.get("open_interest") or []

        # OI keyed for attaching to matching greeks; keep every OI record too (so an
        # unmatched OI print - even a repeated one - becomes its own OI-only row).
        oi_by_key: dict[tuple, dict] = {}
        oi_all: list[tuple[tuple, dict]] = []
        for o in oi_recs:
            key = self._key(o, counters)
            if key is not None:
                oi_by_key[key] = o
                oi_all.append((key, o))

        rows: list[dict] = []
        greeks_keys: set[tuple] = set()
        for g in greeks:
            key = self._key(g, counters)
            if key is None:
                continue
            greeks_keys.add(key)
            exp, strike, ctype = key
            if exp < session:
                counters["expired"] += 1
                continue
            rows.append(self._build_row(
                sym=sym, root=root, quote_ts=quote_ts, exp=exp, strike=strike, ctype=ctype,
                spot=spot, oi_asof=oi_asof, g=g, o=oi_by_key.get(key) or {}, counters=counters))

        for key, o in oi_all:                               # OI-only: no greeks for this key
            if key in greeks_keys:
                continue
            exp, strike, ctype = key
            if exp < session:
                counters["expired"] += 1
                continue
            rows.append(self._build_row(
                sym=sym, root=root, quote_ts=quote_ts, exp=exp, strike=strike, ctype=ctype,
                spot=spot, oi_asof=oi_asof, g={}, o=o, counters=counters))
        return rows

    def normalize(self, raw: dict[str, Any], *, symbol: str,
                  quote_date: "date | str | None" = None) -> pd.DataFrame:
        sym = symbol.upper()
        roots = raw.get("roots") or {}

        # Session: from the raw payload (stamped by fetch_raw), else the passed quote_date.
        session_s = raw.get("session_date")
        if session_s:
            session = _coerce_session(session_s)
        elif quote_date is not None:
            session = _coerce_session(quote_date)
        else:
            raise ValueError(f"Thetadata {sym}: cannot determine session (no session_date)")

        # Spot: the vendor's own underlying_price (a real close), constant across the
        # chain. Median over every greeks row (robust to a stray cell).
        greeks_rows = [g for section in roots.values() for g in (section.get("greeks") or [])]
        if not greeks_rows:
            # OI-only session: the vendor's greeks history has a per-symbol floor (e.g.
            # SPX greeks begin 2017-01 while its OI extends further back). Without greeks
            # there is no spot and no gamma, so the session cannot drive GEX: a clean
            # backfill skip, not a failure (the runner counts it and moves on).
            raise NoDataForSession(
                f"Thetadata {sym}: no greeks rows for session {session.isoformat()} "
                "(OI-only or empty; before the vendor's greeks history floor)")
        spots = [s for g in greeks_rows
                 if (s := num(g.get("underlying_price"))) is not None and s > 0]
        if not spots:
            raise ValueError(
                f"Thetadata {sym}: greeks rows exist but none carries a positive "
                f"underlying_price for session {session.isoformat()}; refusing to write "
                "a chain with no honest spot")
        spot = statistics.median(spots)

        quote_ts = session_close_utc(session)
        oi_asof = prior_trading_day(session, self.oi_lag_days)

        counters = {"malformed": 0, "expired": 0, "iv_nulled": 0}
        rows: list[dict] = []
        for root, section in roots.items():
            rows.extend(self._rows_for_root(
                root, section, sym=sym, session=session, spot=spot,
                quote_ts=quote_ts, oi_asof=oi_asof, counters=counters))

        if not rows:
            raise ValueError(
                f"Thetadata {sym}: no valid contracts for session {session.isoformat()} "
                f"(skipped {counters['malformed']} malformed, {counters['expired']} expired)")
        if any(counters.values()):
            _log.warning("Thetadata %s %s: %d malformed, %d expired, %d iv nulled "
                         "(iv_error>%.2f); spot=%.4f via vendor_close",
                         sym, session.isoformat(), counters["malformed"], counters["expired"],
                         counters["iv_nulled"], _IV_ERROR_MAX, spot)

        df = pd.DataFrame(rows, columns=field_names())
        df["quote_ts"] = pd.to_datetime(df["quote_ts"], utc=True)
        df["expiration"] = pd.to_datetime(df["expiration"])
        df["oi_asof_date"] = pd.to_datetime(df["oi_asof_date"])
        scalar = {k: v for k, v in pandas_dtypes().items()
                  if k not in ("quote_ts", "expiration", "oi_asof_date")}
        df = df.astype(scalar)

        # De-dup vendor artifacts, but NOT genuine collisions (copy MassiveAdapter's B2
        # guard). Two rows sharing the full canonical key (which includes `root`) that are
        # byte-identical are a vendor repeat -> safe to collapse. Distinct contracts that
        # still share the whole key (a repeated contract with conflicting greeks/OI, or an
        # unexpected settlement overlap `root` did not separate) would silently drop open
        # interest if collapsed, so fail loud instead. Reachable because _rows_for_root
        # emits one row per source record rather than pre-collapsing by contract key.
        pk = list(PRIMARY_KEY)
        shares_key = df.duplicated(subset=pk, keep=False)
        collides = shares_key & ~df.duplicated(keep=False)
        if collides.any():
            n = int(df[shares_key].drop_duplicates(subset=pk).shape[0])
            raise NotImplementedError(
                f"Thetadata {sym}: {n} canonical key(s) shared by distinct contracts even "
                "with root in the key; collapsing would silently drop open interest (B2).")
        before = len(df)
        df = df.drop_duplicates(subset=pk, keep="last").reset_index(drop=True)
        if len(df) < before:
            _log.warning("Thetadata %s: collapsed %d exact-duplicate contract row(s)",
                         sym, before - len(df))
        return df

    def load(self, symbol: str, quote_date: "date | str | None" = None,
             **kwargs: Any) -> pd.DataFrame:
        raw = self.fetch_raw(symbol, quote_date, **kwargs)
        frame = self.normalize(raw, symbol=symbol, quote_date=quote_date)
        validate_frame(frame)
        return frame


__all__ = ["ThetadataAdapter", "NoDataForSession", "ROOT_MAP"]

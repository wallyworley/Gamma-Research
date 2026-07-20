"""Resumable ThetaData trade+quote puller for the dealer-sign calibration.

Pulls tick-level option TRADES with their prevailing NBBO for one session via
``ThetaClient.option_history_trade_quote(symbol, expiration='*', date=session,
max_dte=...)`` - one call returns every trade across every expiration inside the DTE
window, each row already carrying the bid/ask that prevailed at the trade (the join
the quote rule needs). Verified semantics (probed before bulk pulling):

  * ``expiration='*'`` + ``max_dte=N`` returns all expirations with DTE <= N in one
    request (SPY 2018-03-15 max_dte=7 -> 56k rows/1.8s; a recent 0DTE-era session ->
    ~1.2M rows/~30s, since short-dated volume dominates and max_dte 7 ~ 60).
  * Columns include symbol, expiration, strike, right (CALL/PUT), price, size, bid,
    ask, condition, ext_condition1-4 (plus feed-clock/exchange columns we drop).
  * The trade file carries NO underlying price; spot for moneyness comes from the
    stored chain (aggregate.py), never from here.

Cached per (symbol, session) to ``<root>/trades/symbol=<SYM>/date=<YYYY-MM-DD>/
trades.parquet`` with only the columns the classifier and the condition-robustness
pass need (downcast to keep the cache small). RESUMABLE by construction: a session
whose parquet exists is skipped, so an interrupted bulk pull just resumes. Transient
errors retry with exponential backoff; a rate-limit / entitlement wall raises
``RateLimited`` so the caller stops rather than hammering the API.

The ``thetadata`` client is imported lazily, so importing this module (and the pure
logic beside it) needs no optional dependency.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date

_log = logging.getLogger(__name__)

# Columns kept from the vendor trade_quote frame (drop feed-clock/exchange/quote-size
# metadata we do not use). condition + ext_condition* drive the robustness pass.
KEEP_COLUMNS = ("expiration", "strike", "right", "price", "size", "bid", "ask",
                "condition", "ext_condition1", "ext_condition2", "ext_condition3",
                "ext_condition4")

_CACHE_FILE = "trades.parquet"
_MAX_RETRIES = 4
_BACKOFF_BASE = 2.0     # seconds; doubled each retry


class RateLimited(Exception):
    """Raised when the API signals a rate-limit / entitlement wall (stop, do not hammer)."""


def _coerce_session(value: "date | str") -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def cache_path(root: str, symbol: str, session: "date | str") -> str:
    """Parquet path for one (symbol, session) trade cache."""
    sess = _coerce_session(session).isoformat()
    return os.path.join(root, "trades", f"symbol={symbol.upper()}",
                        f"date={sess}", _CACHE_FILE)


def is_cached(root: str, symbol: str, session: "date | str") -> bool:
    """True if this session's trades are already cached (the resume gate)."""
    return os.path.exists(cache_path(root, symbol, session))


def make_client(api_key: str | None = None, *, dotenv_path: str | None = None):
    """Construct a pandas-mode ThetaData client (lazy import of the optional dep)."""
    from thetadata import ThetaClient

    kw = {"dataframe_type": "pandas"}
    if api_key or os.environ.get("THETADATA_API_KEY"):
        kw["api_key"] = api_key or os.environ.get("THETADATA_API_KEY")
    if dotenv_path:
        kw["dotenv_path"] = dotenv_path
    return ThetaClient(**kw)


def _is_rate_limit(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    try:
        name = code().name if callable(code) else ""
    except Exception:  # noqa: BLE001
        name = ""
    if name in ("RESOURCE_EXHAUSTED", "UNAUTHENTICATED", "PERMISSION_DENIED"):
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "resource_exhausted" in msg


def _is_transient(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    try:
        name = code().name if callable(code) else ""
    except Exception:  # noqa: BLE001
        name = ""
    if name in ("UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL", "ABORTED"):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in ("unavailable", "deadline", "timeout", "connection", "reset"))


def _to_pandas(frame):
    to_p = getattr(frame, "to_pandas", None)
    return to_p() if callable(to_p) else frame


def _reduce(df):
    """Keep only KEEP_COLUMNS present, downcast numerics to shrink the cache."""
    import numpy as np
    import pandas as pd  # noqa: F401

    cols = [c for c in KEEP_COLUMNS if c in df.columns]
    out = df[cols].copy()
    if "expiration" in out:
        out["expiration"] = pd.to_datetime(out["expiration"]).dt.tz_localize(None)
    for c in ("strike", "price", "bid", "ask"):
        if c in out:
            out[c] = out[c].astype("float32")
    if "size" in out:
        out["size"] = out["size"].astype("float64").fillna(0).astype("int32")
    for c in ("condition", "ext_condition1", "ext_condition2", "ext_condition3", "ext_condition4"):
        if c in out:
            out[c] = out[c].astype("float64").fillna(-1).astype("int32")
    if "right" in out:
        out["right"] = out["right"].astype("string")
    return out


def pull_session_trades(client, symbol: str, session: "date | str", *,
                        max_dte: int = 60, right: str = "both"):
    """Fetch + reduce one session's trades. Retries transient errors, raises
    ``RateLimited`` on a rate/entitlement wall. Returns a reduced pandas DataFrame."""
    sess = _coerce_session(session)
    attempt = 0
    while True:
        try:
            raw = client.option_history_trade_quote(
                symbol.upper(), expiration="*", date=sess, max_dte=max_dte, right=right)
            return _reduce(_to_pandas(raw))
        except Exception as exc:  # noqa: BLE001 - classify: rate-limit vs transient vs fatal
            if _is_rate_limit(exc):
                raise RateLimited(f"{symbol} {sess}: {type(exc).__name__}: {exc}") from exc
            attempt += 1
            if attempt > _MAX_RETRIES or not _is_transient(exc):
                raise
            wait = _BACKOFF_BASE * (2 ** (attempt - 1))
            _log.warning("transient error %s on %s %s; retry %d/%d in %.0fs",
                         type(exc).__name__, symbol, sess, attempt, _MAX_RETRIES, wait)
            time.sleep(wait)


def cache_session(client, symbol: str, session: "date | str", root: str, *,
                  max_dte: int = 60, overwrite: bool = False) -> tuple[str, int]:
    """Pull one session and cache it to parquet. Returns (status, n_rows).

    status: ``"cached"`` (already present, skipped), ``"written"`` (fetched + stored),
    or ``"empty"`` (no trades in the window -> a zero-row marker file is still written
    so the session is not re-attempted). Atomic write (tmp + os.replace).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = cache_path(root, symbol, session)
    if not overwrite and os.path.exists(path):
        return ("cached", -1)
    df = pull_session_trades(client, symbol, session, max_dte=max_dte)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".{os.getpid()}.tmp"
    table = pa.Table.from_pandas(df, preserve_index=False)
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return (("written" if len(df) else "empty"), int(len(df)))


def read_cached(root: str, symbol: str, session: "date | str"):
    """Read one cached session's trades back as a pandas DataFrame."""
    import pyarrow.parquet as pq

    return pq.ParquetFile(cache_path(root, symbol, session)).read().to_pandas()


__all__ = [
    "KEEP_COLUMNS", "RateLimited", "cache_path", "is_cached", "make_client",
    "pull_session_trades", "cache_session", "read_cached",
]

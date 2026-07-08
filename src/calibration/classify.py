"""Trade-side classification (the quote rule) - PURE logic, stdlib-only import.

The empirical dealer-sign calibration replaces the unobservable dealer-position
assumption with a measurement: it classifies each option trade as customer-BUY or
customer-SELL from the trade price relative to the prevailing NBBO, then reads the
dealer as the OTHER side of that flow (a dealer who passively sells when a customer
lifts the offer is short that contract). This module is the single, testable
definition of that quote rule.

Quote rule (Lee-Ready style, adapted to options with an already-joined NBBO):

  * Invalid NBBO (missing bid/ask, bid <= 0, or ask <= bid) -> ``invalid``:
    dropped, no side. A crossed/locked/one-sided quote cannot anchor a side.
  * price >= ask  -> customer BUY  (+1): the customer lifted the offer; the dealer
    passively sold, so the dealer is SHORT this contract.
  * price <= bid  -> customer SELL (-1): the customer hit the bid; the dealer bought,
    so the dealer is LONG this contract.
  * strictly inside the spread: classify by proximity to the touch -
        price > midpoint -> BUY  (closer to the ask)
        price < midpoint -> SELL (closer to the bid)
        price == midpoint -> ``mid``: ambiguous, sign 0, DROPPED from the primary
        map and counted separately (a large midpoint share is a data-quality flag).

The customer side is the observed flow; the dealer sign is its negation
(``dealer_sign_from_customer``). Aggregation lives in aggregate.py; this module only
decides one trade's side, so it stays free of pandas/numpy at import time and the
stdlib-only CI leg can unit-test it directly. A numpy-vectorized companion
(``classify_vectorized``) imports numpy lazily for the bulk path.
"""

from __future__ import annotations

import math

# Customer-side labels (the observed flow) and their signed value.
BUY = "buy"
SELL = "sell"
MID = "mid"
INVALID = "invalid"

_CATEGORY_SIGN = {BUY: 1, SELL: -1, MID: 0, INVALID: 0}


def _is_missing(x) -> bool:
    """True for None or NaN (a trade/quote field that carries no value)."""
    if x is None:
        return True
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return True


def valid_nbbo(bid, ask) -> bool:
    """A quote can anchor a side only if bid > 0 and ask > bid (both present)."""
    if _is_missing(bid) or _is_missing(ask):
        return False
    return float(bid) > 0.0 and float(ask) > float(bid)


def classify_one(price, bid, ask) -> str:
    """Customer-side category for ONE trade against its prevailing NBBO.

    Returns one of ``BUY`` / ``SELL`` / ``MID`` / ``INVALID`` (see module docstring).
    ``MID`` (exact-midpoint) and ``INVALID`` carry no directional information and are
    excluded from the primary flow map by the caller; both are counted so the
    ambiguous / unusable share is auditable.
    """
    if not valid_nbbo(bid, ask) or _is_missing(price):
        return INVALID
    price, bid, ask = float(price), float(bid), float(ask)
    if price <= 0.0:
        return INVALID
    if price >= ask:
        return BUY
    if price <= bid:
        return SELL
    mid = 0.5 * (bid + ask)
    if price > mid:
        return BUY
    if price < mid:
        return SELL
    return MID


def category_sign(category: str) -> int:
    """Signed customer flow for a category: BUY +1, SELL -1, MID/INVALID 0."""
    return _CATEGORY_SIGN[category]


def customer_sign_one(price, bid, ask) -> int:
    """Signed customer flow for one trade (+1 buy / -1 sell / 0 ambiguous)."""
    return category_sign(classify_one(price, bid, ask))


def dealer_sign_from_customer(customer_net_sign: float) -> int:
    """Dealer sign = MINUS the sign of net customer flow.

    Persistent customer opening flow into a bucket leaves the dealer holding the
    opposite inventory: net customer buying (+) => dealer short (-1), net customer
    selling (-) => dealer long (+1). Zero / undefined net flow => 0 (no call).
    """
    if customer_net_sign > 0:
        return -1
    if customer_net_sign < 0:
        return 1
    return 0


def classify_vectorized(price, bid, ask):
    """Vectorized quote rule: arrays -> (categories, signs). Lazy numpy import.

    ``categories`` is an object array of BUY/SELL/MID/INVALID; ``signs`` is an int8
    array of +1/-1/0. Identical rule to ``classify_one`` (kept in lock-step by the
    unit tests), for the bulk per-session path in aggregate.py.
    """
    import numpy as np

    price = np.asarray(price, dtype="float64")
    bid = np.asarray(bid, dtype="float64")
    ask = np.asarray(ask, dtype="float64")

    valid = np.isfinite(bid) & np.isfinite(ask) & (bid > 0.0) & (ask > bid) \
        & np.isfinite(price) & (price > 0.0)
    mid = 0.5 * (bid + ask)

    signs = np.zeros(price.shape, dtype="int8")
    is_buy = valid & ((price >= ask) | ((price < ask) & (price > bid) & (price > mid)))
    is_sell = valid & ((price <= bid) | ((price < ask) & (price > bid) & (price < mid)))
    signs[is_buy] = 1
    signs[is_sell] = -1

    categories = np.full(price.shape, INVALID, dtype=object)
    categories[valid] = MID                     # inside-and-exact-mid remainder
    categories[is_buy] = BUY
    categories[is_sell] = SELL
    return categories, signs


__all__ = [
    "BUY", "SELL", "MID", "INVALID",
    "valid_nbbo", "classify_one", "category_sign", "customer_sign_one",
    "dealer_sign_from_customer", "classify_vectorized",
]

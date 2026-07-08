"""Empirical dealer-sign calibration (Route 2).

Replaces the unobservable dealer-position ASSUMPTION with a MEASUREMENT: classify
option trades against their prevailing NBBO (classify), aggregate signed customer
flow into type x moneyness x DTE buckets (bucket, aggregate), read a standing
dealer-sign per stable bucket (signmap), then rebuild Net GEX under that empirical
map and rerun the vol-forecast scorecard as a third arm beside the two conventions
(gex_rebuild).

Only the PURE logic (classify / bucket / signmap) is re-exported here so
``import src.calibration`` needs no data stack (the stdlib CI leg imports it). The
data-stack modules (pull, aggregate, gex_rebuild) are imported explicitly by callers.
"""

from __future__ import annotations

from . import bucket, classify, signmap

__all__ = ["classify", "bucket", "signmap"]

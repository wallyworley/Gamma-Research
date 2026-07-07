# Vendor API specs (reference only)

## thetadata-openapi.yaml

Fetched 2026-07-07 from https://http-docs.thetadata.us/openapi.yaml (public).
OpenAPI 3.1 spec for the Theta Data REST API, 73 paths. Reference asset only:
nothing imports it; the live integration goes through the official `thetadata`
Python library (see requirements-backfill.txt).

Why it is kept:
- Machine-readable tier gating: every path carries `x-min-subscription`
  (free/value/standard/pro), the authoritative answer to "which tier unlocks X".
- Exact parameter enums and response schemas for endpoints the Python library
  wraps thinly - useful when debugging or extending the adapter offline.
- Documents the `/bulk_hist/option/*` family (eod_greeks, open_interest, ...):
  one request per ROOT per DATE RANGE across all contracts - the efficient shape
  for the Phase 2 full-universe backfill, vs our current per-(symbol, session)
  calls.
- Diffing a re-fetched copy against this one detects vendor API changes before
  they break the backfill.

CAVEAT: this spec documents the **v2 Terminal API** (`servers: http://127.0.0.1:25510/v2`,
the old local-Java-terminal surface). Our integration uses the newer key-authenticated
v3 service via the Python library. Endpoint names, parameters, and response fields
largely carry over, but paths and transport differ - treat it as a field/semantics
reference, not a URL reference.

"""Execution layer — broker-adapter pattern.

See Project-HERON.md Section 4.4 for full spec. Key constraints:
- Idempotent orders: client_order_id = {strategy}_{utc_ms}_{ticker}_{side}
- Virtual stops: HERON polls, not bracket orders (fractional shares can't use brackets)
- Stale-quote kill switch: never submit when quote > 10s old
- HTTP 422 "client_order_id must be unique" = success, not error
"""

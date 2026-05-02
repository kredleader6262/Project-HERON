---
description: "Use when: writing or modifying strategy layer, execution layer, order submission, position sizing, risk checks, wash-sale logic, PDT tracking, reconciliation, stop-loss/take-profit code, or anything that touches real money."
---
# Trading Safety Rules

Full specs in `Project-HERON.md` Sections 4.3–4.4, 5.

## Order Submission

- **Idempotent.** Use `make_entry_order_id(strategy, candidate_id, ticker, side)` for entries and `make_close_order_id(strategy, trade_id, ticker, side)` for exits. Always query by this ID before resubmitting on error.
- **HTTP 422 "client_order_id must be unique"** means the order already went through. Handle as success, not error.
- **Stale-quote kill switch.** Never submit when last quote > 10 seconds old. Alpaca trading-API incident gating is a pre-live hardening item unless already implemented in the code path you're touching.
- **Fractional shares** don't support bracket/OCO, can't be replaced (cancel+resubmit only), TIF must be DAY.
- **Virtual stops.** Stop-loss and take-profit live in HERON's polling code, not Alpaca order types. Current supervisor cadence is the `executor_cycle` schedule; update code, tests, and docs together before changing it.

## Wash-Sale (IRC §1091)

- 30-day lookback on ticker *families* (SPY/VOO/IVV are one family). See `Project-HERON.md` Section 5.4.1 for the map.
- Pre-trade: in live mode, query journal for any live closed losing lot in the same family within 30 days. If found, reject entry. Paper mode skips wash-sale checks.
- Post-trade: on every sale at a loss, record loss amount and 30-day window end date.
- A missed wash-sale violation is an **automatic halt on all strategies**.

## PDT / GFV

- Margin account, $500 seed. Three day-trades per rolling 5 business days (of FINRA's 4 limit).
- Strategy layer maintains rolling count from journal. Rejects entries requiring same-day exit at cap.
- T+1 settlement: avoid good faith violations (unsettled proceeds funding a buy that's sold before settlement).
- Prefer swing strategies (2–10 day holds) over intraday.

## Risk Limits

| Limit | Value | Enforced by |
|---|---|---|
| Max total exposure | 80% of equity | Execution layer |
| Max concurrent positions | 3 (6 above $1,500) | Execution layer |
| Max single-trade loss | 5% of equity | Hard stop |
| Max daily loss | 8% of equity | Halt new entries |
| Max daily new entries | 3 | Execution layer |

## Reconciliation

- Runs at market open and close.
- Compares SQLite state vs Alpaca's `/v2/orders`, `/v2/positions`, `/v2/account`.
- Any drift must be operator-visible and block new live entries. Current implementation logs drift/startup-audit events and live preflight blocks; the operator resolves the broker/journal mismatch before resuming.

## Cap-and-Fallback

Every hard cap names its fallback. No orphan limits. See `Project-HERON.md` Section 5.3 for full table.

## The Rule

**If you're writing code that could submit an order, move money, or affect risk calculations — slow down.** Check the spec. Check the tests. If tests don't exist, say so.

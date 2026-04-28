"""Executor — orchestrates order flow, virtual stops, and risk checks.

This is the entry point for all trade execution. Never bypass this to talk
to the broker adapter directly.

Flow:
1. Pre-trade risk checks (all must pass)
2. Quote freshness check (stale = abort)
3. Submit order via broker adapter (idempotent)
4. Record trade in journal
5. Virtual stop/target polling (30s during market hours)
"""

import logging
import time
from datetime import datetime, timezone

from heron.strategy.risk import pre_trade_checks
from heron.journal.trades import create_trade, fill_trade, close_trade, list_trades
from heron.journal.ops import log_event
from heron.execution.broker import make_entry_order_id, make_close_order_id

log = logging.getLogger(__name__)

# How long to poll a freshly-submitted order for a fill before giving up and
# letting reconcile() pick it up later. Market orders normally fill instantly;
# this is just a safety net for accepted-but-unfilled responses.
_FILL_POLL_TIMEOUT_S = 3.0
_FILL_POLL_INTERVAL_S = 0.5


class Executor:
    """Orchestrates order submission with full risk checking."""

    def __init__(self, broker, conn):
        """
        broker: BrokerAdapter instance
        conn: journal database connection
        """
        self.broker = broker
        self.conn = conn

    def get_equity(self):
        """Current account equity from broker."""
        account = self.broker.get_account()
        return account["equity"]

    def enter_position(self, strategy_id, ticker, qty, side="buy",
                       stop_price=None, target_price=None,
                       candidate_id=None, thesis=None,
                       strategy_config=None, requires_same_day_exit=False,
                       mode="paper", client_order_id=None):
        """Full entry flow: risk checks → quote check → submit → journal.

        Returns (trade_id, order_dict) on success.
        Raises ValueError if any risk check fails.
        mode: "paper" or "live" — should match the strategy's current state.
        client_order_id: optional explicit ID for caller-driven retries. If
            omitted, derived from candidate_id (deterministic) or from a
            millisecond nonce (unsafe to retry without recording the ID).
        """
        equity = self.get_equity()

        # Get fresh quote
        quote = self.broker.get_quote(ticker)
        if quote["is_stale"]:
            reason = f"Stale quote: {quote['age_seconds']}s old"
            log_event(self.conn, "stale_quote_abort", reason,
                      severity="warn", source="executor")
            raise ValueError(reason)

        entry_price = quote["ask"] if side == "buy" else quote["bid"]

        # B2: apply DERISK qty scaling before risk checks (so the scaled cost
        # is what's evaluated against budgets).
        from heron.strategy.policy import current_system_mode, derisk_qty
        sys_mode = current_system_mode(self.conn)
        if sys_mode == "DERISK":
            scaled = derisk_qty(qty, mode_state=sys_mode)
            if scaled != qty:
                log_event(self.conn, "derisk_size_scaled",
                          f"{ticker} qty {qty} -> {scaled} (DERISK)",
                          severity="info", source="executor")
                qty = scaled
            if qty <= 0:
                raise ValueError("DERISK scaled qty to 0")

        # Run all pre-trade risk checks (mode-aware)
        checks = pre_trade_checks(
            self.conn, ticker, entry_price, stop_price, qty, equity,
            quote["age_seconds"], strategy_config=strategy_config,
            requires_same_day_exit=requires_same_day_exit,
            mode=mode, strategy_id=strategy_id,
        )
        failures = [(name, c) for name, c in checks if not c.ok]
        if failures:
            reasons = "; ".join(f"{name}: {c.reason}" for name, c in failures)
            raise ValueError(f"Risk check(s) failed: {reasons}")

        # Deterministic order ID. With a candidate_id this is retry-safe across
        # transient errors; without one, retries must reuse the returned id.
        if client_order_id is None:
            client_order_id = make_entry_order_id(strategy_id, candidate_id, ticker, side)

        order = self._submit_with_retry(ticker, side, qty, client_order_id, attempt_label="entry")

        # Record in journal
        trade_id = create_trade(
            self.conn, strategy_id, ticker, side, mode, qty,
            client_order_id=client_order_id,
            stop_price=stop_price, target_price=target_price,
            candidate_id=candidate_id, thesis=thesis,
        )

        # Record fill (poll briefly if not immediately filled)
        order = self._await_fill(client_order_id, order)
        if order.get("filled_avg_price"):
            slippage_bps = None
            if entry_price:
                slippage_bps = round(
                    abs(order["filled_avg_price"] - entry_price) / entry_price * 10000, 1
                )
            fill_trade(self.conn, trade_id,
                       order["filled_avg_price"],
                       order.get("filled_qty"),
                       slippage_bps)

        log.info(f"Entered: {ticker} {side} {qty} @ {order.get('filled_avg_price', 'pending')} "
                 f"[{client_order_id}]")
        return trade_id, order

    def _submit_with_retry(self, ticker, side, qty, client_order_id, attempt_label="order"):
        """Submit an order; on any non-APIError failure, query the broker to see
        if it actually got through before raising. The Alpaca adapter already
        deduplicates explicit 422 'client_order_id must be unique' errors via
        get_order; this guards against ConnectionError / Timeout / 5xx where
        the SDK may raise without us knowing whether the order was accepted.
        """
        try:
            return self.broker.submit_order(
                ticker, side, qty,
                order_type="market",
                client_order_id=client_order_id,
            )
        except Exception as e:
            log_event(self.conn, "submit_error",
                      f"{attempt_label} {client_order_id}: {e}",
                      severity="error", source="executor")
            existing = None
            try:
                existing = self.broker.get_order(client_order_id)
            except Exception as ge:
                log.warning(f"get_order check failed for {client_order_id}: {ge}")
            if existing:
                log.warning(f"{attempt_label} appears to have submitted despite error: {client_order_id}")
                return existing
            raise

    def _await_fill(self, client_order_id, order):
        """If the submit response has no fill yet, briefly poll get_order. The
        reconciler will catch anything still unfilled afterwards.
        """
        if order.get("filled_avg_price"):
            return order
        deadline = time.monotonic() + _FILL_POLL_TIMEOUT_S
        last = order
        while time.monotonic() < deadline:
            time.sleep(_FILL_POLL_INTERVAL_S)
            try:
                latest = self.broker.get_order(client_order_id)
            except Exception as e:
                log.debug(f"fill poll get_order failed for {client_order_id}: {e}")
                continue
            if latest:
                last = latest
                if latest.get("filled_avg_price"):
                    return latest
        return last

    def check_exits(self, strategy):
        """Poll all open trades for a strategy and check exit conditions.

        Called on a 30-second interval during market hours.
        strategy: BaseStrategy instance with should_exit() method.
        """
        open_trades = list_trades(self.conn, strategy_id=strategy.strategy_id, open_only=True)
        exits = []

        for trade in open_trades:
            if not trade["fill_price"]:
                continue  # not filled yet

            ticker = trade["ticker"]
            try:
                quote = self.broker.get_quote(ticker)
                if quote["is_stale"]:
                    log.warning(f"Stale quote for {ticker}, skipping exit check")
                    continue

                current_price = (quote["bid"] + quote["ask"]) / 2
                # Approximate days held
                filled_at = datetime.fromisoformat(trade["filled_at"])
                days_held = (datetime.now(timezone.utc) - filled_at).days

                market_data = {
                    "current_price": current_price,
                    "days_held": days_held,
                }

                should, reason, price = strategy.should_exit(trade, market_data)

                if should and strategy.check_min_hold(days_held):
                    self._close_position(trade, price, reason)
                    exits.append((trade["id"], reason))
                elif should and not strategy.check_min_hold(days_held):
                    log.info(f"Exit signal for {ticker} ({reason}) but min hold not met "
                             f"({days_held}d < {strategy.min_hold_days}d)")

            except Exception as e:
                log.error(f"Exit check failed for {ticker}: {e}")

        return exits

    def _close_position(self, trade, close_price, reason):
        """Submit close order and update journal.

        The client_order_id is derived deterministically from trade['id'], so
        repeated close attempts for the same trade always hit the same broker
        order — no risk of submitting two sells if the journal write fails
        between polls.
        """
        ticker = trade["ticker"]
        side = "sell" if trade["side"] == "buy" else "buy"
        qty = trade["fill_qty"]
        if not qty:
            log.error(f"Cannot close {ticker}: no fill_qty on trade {trade['id']}")
            return

        client_order_id = make_close_order_id(
            trade["strategy_id"], trade["id"], ticker, side
        )

        try:
            order = self._submit_with_retry(ticker, side, qty, client_order_id, attempt_label="close")
            order = self._await_fill(client_order_id, order)
            actual_close = order.get("filled_avg_price") or close_price
            close_trade(self.conn, trade["id"], actual_close, reason)
            log.info(f"Closed: {ticker} {side} {qty} @ {actual_close} ({reason}) [{client_order_id}]")
        except Exception as e:
            log.error(f"Failed to close {ticker}: {e}")
            log_event(self.conn, "close_failed", str(e),
                      severity="error", source="executor")

    def reconcile(self):
        """Compare journal state vs broker state. Returns list of discrepancies."""
        discrepancies = []

        # Get broker positions
        broker_positions = {p["ticker"]: p for p in self.broker.get_positions()}

        # Get journal open trades
        journal_open = list_trades(self.conn, open_only=True)
        journal_tickers = {}
        for t in journal_open:
            if t["fill_price"]:
                journal_tickers.setdefault(t["ticker"], []).append(t)

        # Check for positions in broker not in journal
        for ticker, pos in broker_positions.items():
            if ticker not in journal_tickers:
                discrepancies.append({
                    "type": "broker_only",
                    "ticker": ticker,
                    "broker_qty": pos["qty"],
                    "journal_qty": 0,
                    "message": f"{ticker}: position in broker but not journal",
                })

        # Check for trades in journal not in broker
        for ticker, trades in journal_tickers.items():
            journal_qty = sum(t["fill_qty"] or 0 for t in trades)
            broker_qty = broker_positions.get(ticker, {}).get("qty", 0)
            if abs(journal_qty - broker_qty) > 0.001:
                discrepancies.append({
                    "type": "qty_mismatch",
                    "ticker": ticker,
                    "broker_qty": broker_qty,
                    "journal_qty": journal_qty,
                    "message": f"{ticker}: journal={journal_qty}, broker={broker_qty}",
                })

        if discrepancies:
            for d in discrepancies:
                log_event(self.conn, "reconciliation_drift", d["message"],
                          severity="error", source="executor")
            log.error(f"Reconciliation: {len(discrepancies)} discrepancy(s) found")
        else:
            log.info("Reconciliation: clean")

        return discrepancies

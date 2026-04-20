"""Broker adapter interface.

Abstract interface so the Strategy layer never talks to Alpaca directly.
Adapters: AlpacaPaperAdapter (M5), AlpacaLiveAdapter (future).
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone


def make_client_order_id(strategy_id, ticker, side, nonce=None):
    """Generate an idempotent broker order ID.

    Format: {strategy_id}_{nonce}_{ticker}_{side} (underscore-delimited; Alpaca
    only allows letters, digits, `.`, `-`, `_`).

    `nonce` MUST be deterministic with respect to the trade you intend to
    submit. If you let it default, two calls within the same millisecond can
    produce the same ID and two calls across milliseconds produce different
    IDs — either way, retries are unsafe. Prefer the helpers:
        make_entry_order_id(strategy_id, candidate_id, ticker, side)
        make_close_order_id(strategy_id, trade_id, ticker, side)
    or pass an explicit nonce derived from a stable identifier.

    Note: the resulting ID is not safely splittable on `_` because strategy_id
    can itself contain underscores (`pead_v1`). Don't parse it; treat it as opaque.
    """
    if nonce is None:
        nonce = int(datetime.now(timezone.utc).timestamp() * 1000)
    return f"{strategy_id}_{nonce}_{ticker}_{side}"


def make_entry_order_id(strategy_id, candidate_id, ticker, side):
    """Deterministic entry order ID. Same (candidate, side) → same ID across retries.

    If `candidate_id` is None (manual entry), falls back to a millisecond nonce
    — caller is responsible for not retrying without the same ID.
    """
    if candidate_id is None:
        return make_client_order_id(strategy_id, ticker, side)
    return make_client_order_id(strategy_id, ticker, side, nonce=f"e{candidate_id}")


def make_close_order_id(strategy_id, trade_id, ticker, side):
    """Deterministic close order ID. Same trade → same ID across every poll."""
    return make_client_order_id(strategy_id, ticker, side, nonce=f"c{trade_id}")


class BrokerAdapter(ABC):
    """Abstract broker interface. All methods must be idempotent-safe."""

    @abstractmethod
    def submit_order(self, ticker, side, qty, order_type="market",
                     limit_price=None, client_order_id=None):
        """Submit an order. Returns order dict or raises.

        On network error, caller should retry with same client_order_id.
        HTTP 422 "client_order_id must be unique" means order already succeeded.
        """

    @abstractmethod
    def get_order(self, client_order_id):
        """Query order by client_order_id. For idempotency checks."""

    @abstractmethod
    def cancel_order(self, order_id):
        """Cancel an order by Alpaca order ID."""

    @abstractmethod
    def list_orders(self, status="open"):
        """List orders by status."""

    @abstractmethod
    def get_positions(self):
        """Get all open positions."""

    @abstractmethod
    def get_position(self, ticker):
        """Get position for a specific ticker."""

    @abstractmethod
    def get_account(self):
        """Get account info (equity, cash, buying power)."""

    @abstractmethod
    def get_quote(self, ticker):
        """Get latest quote with age check."""

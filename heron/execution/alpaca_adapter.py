"""Alpaca paper trading adapter.

Wraps alpaca-py SDK with idempotent order handling, stale-quote kill switch,
and fractional-share constraints. See Project-HERON.md Section 4.4.
"""

import logging
from datetime import datetime, timezone

from alpaca.common.exceptions import APIError

from heron.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, QUOTE_STALE_SECONDS
from heron.execution.broker import BrokerAdapter

log = logging.getLogger(__name__)


class AlpacaPaperAdapter(BrokerAdapter):
    """Alpaca paper trading adapter using alpaca-py SDK."""

    def __init__(self):
        from alpaca.trading.client import TradingClient
        from alpaca.data.live import StockDataStream

        self._trading = TradingClient(
            ALPACA_API_KEY, ALPACA_SECRET_KEY,
            paper=True,
        )

    def submit_order(self, ticker, side, qty, order_type="market",
                     limit_price=None, client_order_id=None):
        """Submit order with idempotency handling.

        Fractional shares: TIF must be DAY, no bracket/OCO.
        HTTP 422 with "client_order_id must be unique" = already succeeded.
        """
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.common.exceptions import APIError

        side_enum = OrderSide.BUY if side == "buy" else OrderSide.SELL

        try:
            if order_type == "market":
                req = MarketOrderRequest(
                    symbol=ticker,
                    qty=qty,
                    side=side_enum,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )
            else:
                req = LimitOrderRequest(
                    symbol=ticker,
                    qty=qty,
                    side=side_enum,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price,
                    client_order_id=client_order_id,
                )
            order = self._trading.submit_order(req)
            log.info(f"Order submitted: {client_order_id} → {order.id}")
            return self._order_to_dict(order)

        except APIError as e:
            # HTTP 422 "client_order_id must be unique" = already succeeded
            if "client_order_id must be unique" in str(e) or "already been taken" in str(e):
                log.info(f"Order already exists: {client_order_id}")
                existing = self.get_order(client_order_id)
                if existing:
                    return existing
            raise

    def get_order(self, client_order_id):
        """Look up order by client_order_id for idempotency."""
        from alpaca.trading.requests import GetOrdersRequest  # noqa: F401
        try:
            order = self._trading.get_order_by_client_id(client_order_id)
            return self._order_to_dict(order)
        except APIError:
            return None

    def cancel_order(self, order_id):
        self._trading.cancel_order_by_id(order_id)
        log.info(f"Order cancelled: {order_id}")

    def list_orders(self, status="open"):
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }
        req = GetOrdersRequest(status=status_map.get(status, QueryOrderStatus.OPEN))
        orders = self._trading.get_orders(req)
        return [self._order_to_dict(o) for o in orders]

    def get_positions(self):
        positions = self._trading.get_all_positions()
        return [self._position_to_dict(p) for p in positions]

    def get_position(self, ticker):
        try:
            p = self._trading.get_open_position(ticker)
            return self._position_to_dict(p)
        except APIError:
            return None

    def get_account(self):
        a = self._trading.get_account()
        return {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "portfolio_value": float(a.portfolio_value),
            "daytrade_count": a.daytrade_count,
            "pattern_day_trader": a.pattern_day_trader,
        }

    def get_quote(self, ticker):
        """Get latest quote with staleness check."""
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest

        data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quotes = data_client.get_stock_latest_quote(req)
        q = quotes[ticker]

        age = (datetime.now(timezone.utc) - q.timestamp).total_seconds() if q.timestamp else 999
        return {
            "ticker": ticker,
            "bid": float(q.bid_price) if q.bid_price else 0.0,
            "ask": float(q.ask_price) if q.ask_price else 0.0,
            "bid_size": q.bid_size,
            "ask_size": q.ask_size,
            "age_seconds": round(age, 1),
            "is_stale": age > QUOTE_STALE_SECONDS,
            "timestamp": q.timestamp.isoformat() if q.timestamp else None,
        }

    @staticmethod
    def _order_to_dict(order):
        return {
            "id": str(order.id),
            "client_order_id": order.client_order_id,
            "ticker": order.symbol,
            "side": str(order.side),
            "qty": float(order.qty) if order.qty else None,
            "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
            "type": str(order.type),
            "status": str(order.status),
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "filled_at": order.filled_at.isoformat() if order.filled_at else None,
        }

    @staticmethod
    def _position_to_dict(pos):
        return {
            "ticker": pos.symbol,
            "qty": float(pos.qty),
            "side": str(pos.side),
            "avg_entry": float(pos.avg_entry_price),
            "current_price": float(pos.current_price),
            "market_value": float(pos.market_value),
            "unrealized_pnl": float(pos.unrealized_pl),
            "unrealized_pnl_pct": float(pos.unrealized_plpc),
        }

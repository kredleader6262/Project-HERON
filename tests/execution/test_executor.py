"""Tests for executor using a mock broker adapter."""

import pytest
from unittest.mock import MagicMock, patch
from heron.journal import get_journal_conn, init_journal
from heron.journal.strategies import create_strategy
from heron.journal.trades import create_trade, fill_trade, list_trades
from heron.execution.executor import Executor
from heron.execution.broker import BrokerAdapter


class MockBroker(BrokerAdapter):
    """In-memory mock broker for testing."""

    def __init__(self, equity=500.0):
        self._equity = equity
        self._orders = {}
        self._positions = {}

    def submit_order(self, ticker, side, qty, order_type="market",
                     limit_price=None, client_order_id=None):
        # Idempotent: same client_order_id returns the same order (mirrors
        # Alpaca's behavior post-422-dedup).
        if client_order_id and client_order_id in self._orders:
            return self._orders[client_order_id]
        order = {
            "id": f"mock_{client_order_id}",
            "client_order_id": client_order_id,
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "filled_qty": qty,
            "type": order_type,
            "status": "filled",
            "filled_avg_price": 150.0,  # mock fill
            "created_at": "2025-01-15T10:00:00+00:00",
            "filled_at": "2025-01-15T10:00:01+00:00",
        }
        self._orders[client_order_id] = order
        self.submit_calls = getattr(self, "submit_calls", 0) + 1
        return order

    def get_order(self, client_order_id):
        return self._orders.get(client_order_id)

    def cancel_order(self, order_id):
        pass

    def list_orders(self, status="open"):
        return list(self._orders.values())

    def get_positions(self):
        return list(self._positions.values())

    def get_position(self, ticker):
        return self._positions.get(ticker)

    def get_account(self):
        return {
            "equity": self._equity,
            "cash": self._equity,
            "buying_power": self._equity,
            "portfolio_value": self._equity,
            "daytrade_count": 0,
            "pattern_day_trader": False,
        }

    def get_quote(self, ticker):
        return {
            "ticker": ticker,
            "bid": 149.50,
            "ask": 150.50,
            "bid_size": 100,
            "ask_size": 100,
            "age_seconds": 2.0,
            "is_stale": False,
            "timestamp": "2025-01-15T10:00:00+00:00",
        }


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "test_exec.db"
    conn = get_journal_conn(str(db))
    init_journal(conn)
    create_strategy(conn, "pead", "PEAD")
    broker = MockBroker(equity=500.0)
    executor = Executor(broker, conn)
    return executor, conn, broker


# ── Entry ──────────────────────────────────────

def test_enter_position(setup):
    executor, conn, broker = setup
    trade_id, order = executor.enter_position(
        "pead", "AAPL", 2, side="buy",
        stop_price=145.0, target_price=160.0,
        thesis="PEAD test entry")
    assert trade_id is not None
    assert order["status"] == "filled"
    # Check journal
    trades = list_trades(conn, strategy_id="pead")
    assert len(trades) == 1
    assert trades[0]["ticker"] == "AAPL"


def test_enter_records_fill(setup):
    executor, conn, broker = setup
    trade_id, order = executor.enter_position(
        "pead", "AAPL", 2, stop_price=145.0, target_price=160.0)
    from heron.journal.trades import get_trade
    t = get_trade(conn, trade_id)
    assert t["fill_price"] == 150.0
    assert t["fill_qty"] is not None


def test_stale_quote_rejects(setup):
    executor, conn, broker = setup
    # Make quote stale
    original = broker.get_quote

    def stale_quote(ticker):
        q = original(ticker)
        q["is_stale"] = True
        q["age_seconds"] = 15.0
        return q

    broker.get_quote = stale_quote
    with pytest.raises(ValueError, match="Stale quote"):
        executor.enter_position("pead", "AAPL", 2, stop_price=145.0, target_price=160.0)


def test_wash_sale_rejects(setup):
    executor, conn, broker = setup
    # Create a losing trade to trigger wash-sale
    tid = create_trade(conn, "pead", "AAPL", "buy", "live", 10)
    fill_trade(conn, tid, 150.00)
    from heron.journal.trades import close_trade
    close_trade(conn, tid, 140.00, "stop")  # loss → wash-sale lot

    # Live-mode entry must see the wash-sale lot. (In paper mode the check
    # is intentionally a no-op since paper losses aren't real tax events.)
    with pytest.raises(ValueError, match="wash_sale"):
        executor.enter_position("pead", "AAPL", 2, stop_price=145.0, target_price=160.0,
                                mode="live")


# ── Exit Checks ──────────────────────────────────

def test_check_exits_stop_hit(setup):
    executor, conn, broker = setup

    # Create an open filled trade
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10,
                       client_order_id="pead_1_AAPL_buy")
    fill_trade(conn, tid, 150.00)
    # Set stop/target in the trade
    conn.execute("UPDATE trades SET stop_price=145.0, target_price=165.0 WHERE id=?", (tid,))
    conn.commit()

    # Mock quote at stop level
    broker.get_quote = lambda t: {
        "ticker": t, "bid": 143.0, "ask": 144.0,
        "age_seconds": 1.0, "is_stale": False,
        "bid_size": 100, "ask_size": 100,
        "timestamp": "2025-01-15T10:00:00+00:00",
    }

    # Create a mock strategy
    class MockStrategy:
        strategy_id = "pead"
        min_hold_days = 0  # allow immediate exit for test

        def should_exit(self, trade, market_data):
            price = market_data["current_price"]
            if trade["stop_price"] and price <= trade["stop_price"]:
                return True, "stop", price
            return False, "hold", price

        def check_min_hold(self, days):
            return True  # always OK for test

    exits = executor.check_exits(MockStrategy())
    assert len(exits) == 1
    assert exits[0][1] == "stop"


# ── Reconciliation ──────────────────────────────

def test_reconcile_clean(setup):
    executor, conn, broker = setup
    discrepancies = executor.reconcile()
    assert discrepancies == []


def test_reconcile_broker_only_position(setup):
    executor, conn, broker = setup
    broker._positions["AAPL"] = {
        "ticker": "AAPL", "qty": 5, "side": "long",
        "avg_entry": 150.0, "current_price": 155.0,
        "market_value": 775.0, "unrealized_pnl": 25.0,
        "unrealized_pnl_pct": 0.033,
    }
    discrepancies = executor.reconcile()
    assert len(discrepancies) == 1
    assert discrepancies[0]["type"] == "broker_only"


def test_reconcile_qty_mismatch(setup):
    executor, conn, broker = setup
    # Journal has open trade
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    fill_trade(conn, tid, 150.00)
    # Broker has different qty
    broker._positions["AAPL"] = {
        "ticker": "AAPL", "qty": 5, "side": "long",
        "avg_entry": 150.0, "current_price": 155.0,
        "market_value": 775.0, "unrealized_pnl": 25.0,
        "unrealized_pnl_pct": 0.033,
    }
    discrepancies = executor.reconcile()
    assert len(discrepancies) == 1
    assert discrepancies[0]["type"] == "qty_mismatch"


# ── Idempotency ───────────────────────────────────

def test_close_uses_deterministic_id_per_trade(setup):
    """Two consecutive close attempts on the same trade hit the same broker ID."""
    executor, conn, broker = setup
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10,
                       client_order_id="pead_demo_AAPL_buy")
    fill_trade(conn, tid, 150.00)
    trade = list_trades(conn, strategy_id="pead")[0]

    # First close
    broker.submit_calls = 0
    executor._close_position(trade, 152.0, "target")
    first_call_count = broker.submit_calls
    assert first_call_count == 1
    first_close_id = next(k for k in broker._orders if k.startswith("pead_c"))

    # Simulate a second exit-check tick attempting to close the same trade.
    # We re-fetch trade to see the (now-closed) row, then call _close_position again.
    # Even though close_trade already ran, the deterministic id means the broker
    # sees the same client_order_id and dedupes (no second sell).
    trade_after = list_trades(conn, strategy_id="pead")[0]
    executor._close_position(trade_after, 152.0, "target_retry")
    # Broker should NOT have received a second submit (idempotent dedup), or if
    # it did, it must have been the same client_order_id.
    second_close_id = next(k for k in broker._orders if k.startswith("pead_c"))
    assert first_close_id == second_close_id
    # Only one unique close order ever existed:
    assert sum(1 for k in broker._orders if k.startswith("pead_c")) == 1


def test_entry_uses_deterministic_client_order_id(setup):
    """Entry client_order_id must be derived from candidate + ticker + side so
    retries dedupe at the broker. See make_entry_order_id."""
    executor, conn, broker = setup
    from heron.journal.candidates import create_candidate
    cid = create_candidate(conn, "pead", "AAPL", side="buy", source="test",
                           local_score=0.7, thesis="test")
    tid1, order1 = executor.enter_position(
        "pead", "AAPL", 1, side="buy",
        stop_price=145.0, target_price=160.0,
        candidate_id=cid)
    from heron.execution.broker import make_entry_order_id
    expected = make_entry_order_id("pead", cid, "AAPL", "buy")
    assert order1["client_order_id"] == expected


def test_submit_retry_queries_broker_on_failure(setup):
    """If submit_order raises a transient error but the order actually went
    through, the executor should detect it via get_order and not blow up."""
    executor, conn, broker = setup
    from heron.journal.candidates import create_candidate
    cid = create_candidate(conn, "pead", "AAPL", side="buy", source="test",
                           local_score=0.7, thesis="test")

    # Pre-seed an order in the broker as if it had been accepted
    from heron.execution.broker import make_entry_order_id
    pre_id = make_entry_order_id("pead", cid, "AAPL", "buy")
    broker._orders[pre_id] = {
        "id": f"mock_{pre_id}",
        "client_order_id": pre_id,
        "ticker": "AAPL", "side": "buy", "qty": 1,
        "filled_qty": 1, "type": "market", "status": "filled",
        "filled_avg_price": 150.0,
        "created_at": "2025-01-15T10:00:00+00:00",
        "filled_at": "2025-01-15T10:00:01+00:00",
    }
    # Force submit_order to raise (simulates network blip after broker accepted)
    def boom(*a, **kw):
        raise ConnectionError("simulated network blip")
    broker.submit_order = boom

    tid, order = executor.enter_position(
        "pead", "AAPL", 1, side="buy",
        stop_price=145.0, target_price=160.0,
        candidate_id=cid)
    # Recovered the pre-existing order:
    assert order["client_order_id"] == pre_id
    assert order["filled_avg_price"] == 150.0


def test_submit_failure_with_no_existing_raises(setup):
    """If submit fails AND broker has no record, the error propagates."""
    executor, conn, broker = setup
    from heron.journal.candidates import create_candidate
    cid = create_candidate(conn, "pead", "AAPL", side="buy", source="test",
                           local_score=0.7, thesis="test")
    broker.submit_order = lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("hard fail"))
    with pytest.raises(ConnectionError):
        executor.enter_position(
            "pead", "AAPL", 1, side="buy",
            stop_price=145.0, target_price=160.0,
            candidate_id=cid)


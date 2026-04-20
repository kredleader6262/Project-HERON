"""Tests for trades, wash-sale lots, PDT tracking."""

import pytest
from datetime import datetime, timezone, timedelta
from heron.journal import get_journal_conn, init_journal
from heron.journal.strategies import create_strategy
from heron.journal.trades import (
    create_trade, fill_trade, close_trade, get_trade, list_trades,
    check_wash_sale, get_wash_sale_exposure, get_pdt_count, can_daytrade,
    _ticker_family,
)


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test_trades.db"
    c = get_journal_conn(str(db))
    init_journal(c)
    create_strategy(c, "pead", "PEAD")
    yield c
    c.close()


# ── Trade lifecycle ──────────────────────────────

def test_create_trade(conn):
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10,
                       client_order_id="pead_123_AAPL_buy")
    t = get_trade(conn, tid)
    assert t["ticker"] == "AAPL"
    assert t["side"] == "buy"
    assert t["mode"] == "paper"
    assert t["qty"] == 10
    assert t["close_price"] is None


def test_fill_trade(conn):
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    fill_trade(conn, tid, 150.00)
    t = get_trade(conn, tid)
    assert t["fill_price"] == 150.00
    assert t["fill_qty"] == 10
    assert t["filled_at"] is not None


def test_close_trade_profit(conn):
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    fill_trade(conn, tid, 150.00)
    t = close_trade(conn, tid, 160.00, "target")
    assert t["close_price"] == 160.00
    assert t["pnl"] == 100.00  # (160-150)*10
    assert t["pnl_pct"] == pytest.approx(100.0 / 1500.0)
    assert t["close_reason"] == "target"


def test_close_trade_loss_creates_wash_sale_lot(conn):
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    fill_trade(conn, tid, 150.00)
    close_trade(conn, tid, 140.00, "stop")

    lots = check_wash_sale(conn, "AAPL")
    assert len(lots) == 1
    assert lots[0]["loss_amount"] == -100.00  # (140-150)*10
    assert lots[0]["ticker_family"] == "AAPL"


def test_close_without_fill_raises(conn):
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    with pytest.raises(ValueError, match="no fill"):
        close_trade(conn, tid, 140.00, "stop")


def test_close_missing_trade_raises(conn):
    with pytest.raises(ValueError, match="not found"):
        close_trade(conn, 9999, 140.00, "stop")


# ── Wash-Sale ──────────────────────────────────────

def test_wash_sale_family_grouping(conn):
    """SPY loss should block VOO entry (same family)."""
    tid = create_trade(conn, "pead", "SPY", "buy", "paper", 5)
    fill_trade(conn, tid, 400.00)
    close_trade(conn, tid, 390.00, "stop")

    # SPY and VOO are in the same family
    spy_lots = check_wash_sale(conn, "SPY")
    voo_lots = check_wash_sale(conn, "VOO")
    assert len(spy_lots) >= 1
    assert len(voo_lots) >= 1  # same family


def test_wash_sale_exposure(conn):
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    fill_trade(conn, tid, 150.00)
    close_trade(conn, tid, 140.00, "stop")
    exposure = get_wash_sale_exposure(conn)
    assert len(exposure) >= 1


def test_ticker_family():
    assert _ticker_family("SPY") == _ticker_family("VOO")
    assert _ticker_family("AAPL") == "AAPL"
    assert _ticker_family("UNKNOWN_TICKER") == "UNKNOWN_TICKER"


# ── PDT ──────────────────────────────────────────

def test_pdt_same_day_close(conn):
    """Same-day entry+close should record a day trade."""
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    fill_trade(conn, tid, 150.00)
    # filled_at is set to now; closing also at now = same calendar day
    close_trade(conn, tid, 155.00, "target")
    assert get_pdt_count(conn) >= 1


def test_can_daytrade_limit(conn):
    assert can_daytrade(conn, limit=3) is True

    # Create 3 day trades
    for _ in range(3):
        tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 1)
        fill_trade(conn, tid, 100.00)
        close_trade(conn, tid, 101.00, "target")

    assert can_daytrade(conn, limit=3) is False


# ── List Trades ──────────────────────────────────

def test_list_trades(conn):
    create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    create_trade(conn, "pead", "MSFT", "buy", "live", 5)
    assert len(list_trades(conn)) == 2
    assert len(list_trades(conn, mode="paper")) == 1
    assert len(list_trades(conn, ticker="MSFT")) == 1


def test_list_trades_open_only(conn):
    tid1 = create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    fill_trade(conn, tid1, 150.00)
    close_trade(conn, tid1, 160.00, "target")
    create_trade(conn, "pead", "MSFT", "buy", "paper", 5)
    assert len(list_trades(conn, open_only=True)) == 1


def test_client_order_id_unique(conn):
    create_trade(conn, "pead", "AAPL", "buy", "paper", 10,
                 client_order_id="pead_123_AAPL_buy")
    with pytest.raises(Exception):  # UNIQUE constraint
        create_trade(conn, "pead", "AAPL", "buy", "paper", 10,
                     client_order_id="pead_123_AAPL_buy")

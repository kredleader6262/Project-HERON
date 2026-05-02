"""Tests for pre-trade risk checks."""

import pytest
from heron.journal.trades import create_trade, fill_trade, close_trade
from heron.strategy.risk import (
    CheckResult,
    check_wash_sale_risk, check_pdt_risk, check_exposure,
    check_position_count, check_daily_entries, check_daily_loss,
    check_single_trade_risk, check_quote_freshness,
    pre_trade_checks,
)


@pytest.fixture
def conn(pead_conn):
    return pead_conn


# ── CheckResult ──────────────────────────────────

def test_check_result_truthy():
    assert CheckResult(True)
    assert not CheckResult(False)


def test_check_result_reason():
    r = CheckResult(False, "bad thing")
    assert r.reason == "bad thing"


# ── Wash-Sale ──────────────────────────────────

def test_wash_sale_no_lots(conn):
    assert check_wash_sale_risk(conn, "AAPL").ok


def test_wash_sale_with_lot(conn):
    tid = create_trade(conn, "pead", "AAPL", "buy", "live", 10)
    fill_trade(conn, tid, 150.00)
    close_trade(conn, tid, 140.00, "stop")  # creates wash-sale lot
    result = check_wash_sale_risk(conn, "AAPL")
    assert not result.ok
    assert "Wash-sale" in result.reason


def test_wash_sale_family(conn):
    """SPY loss should block VOO entry."""
    tid = create_trade(conn, "pead", "SPY", "buy", "live", 5)
    fill_trade(conn, tid, 400.00)
    close_trade(conn, tid, 390.00, "stop")
    result = check_wash_sale_risk(conn, "VOO")
    assert not result.ok


# ── PDT ──────────────────────────────────────────

def test_pdt_swing_trade(conn):
    """Non-same-day exits always pass PDT."""
    assert check_pdt_risk(conn, requires_same_day_exit=False).ok


def test_pdt_under_limit(conn):
    assert check_pdt_risk(conn, requires_same_day_exit=True, limit=3).ok


def test_pdt_at_limit(conn):
    for _ in range(3):
        tid = create_trade(conn, "pead", "AAPL", "buy", "live", 1)
        fill_trade(conn, tid, 100.00)
        close_trade(conn, tid, 101.00, "target")  # same-day close = day trade
    result = check_pdt_risk(conn, requires_same_day_exit=True, limit=3)
    assert not result.ok
    assert "PDT" in result.reason


# ── Exposure ──────────────────────────────────────

def test_exposure_under_limit(conn):
    assert check_exposure(conn, entry_cost=100, equity=500, max_pct=0.80).ok


def test_exposure_over_limit(conn):
    # Create open trade worth $350
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 2)
    fill_trade(conn, tid, 175.00)
    # Trying to add $100 more → total $450 > 80% of $500 = $400
    result = check_exposure(conn, entry_cost=100, equity=500, max_pct=0.80)
    assert not result.ok
    assert "Exposure" in result.reason


# ── Position Count ──────────────────────────────

def test_positions_under_limit(conn):
    assert check_position_count(conn, max_positions=3).ok


def test_positions_at_limit(conn):
    for ticker in ["AAPL", "MSFT", "GOOGL"]:
        tid = create_trade(conn, "pead", ticker, "buy", "paper", 1)
        fill_trade(conn, tid, 100.00)
    result = check_position_count(conn, max_positions=3)
    assert not result.ok


# ── Daily Entries ──────────────────────────────

def test_daily_entries_under_limit(conn):
    assert check_daily_entries(conn, max_daily=3).ok


# ── Daily Loss ──────────────────────────────────

def test_daily_loss_ok(conn):
    assert check_daily_loss(conn, equity=500).ok


# ── Single Trade Risk ──────────────────────────

def test_single_trade_within_budget():
    result = check_single_trade_risk(
        entry_price=100, stop_price=95, qty=2, equity=500, max_pct=0.05)
    # loss = $10, limit = $25
    assert result.ok


def test_single_trade_over_budget():
    result = check_single_trade_risk(
        entry_price=100, stop_price=80, qty=2, equity=500, max_pct=0.05)
    # loss = $40, limit = $25
    assert not result.ok


def test_single_trade_missing_prices():
    assert not check_single_trade_risk(None, 95, 2, 500).ok


# ── Quote Freshness ──────────────────────────────

def test_fresh_quote():
    assert check_quote_freshness(5).ok


def test_stale_quote():
    result = check_quote_freshness(15)
    assert not result.ok
    assert "Stale" in result.reason


# ── Composite ──────────────────────────────────

def test_pre_trade_all_pass(conn):
    checks = pre_trade_checks(
        conn, ticker="AAPL", entry_price=150, stop_price=145,
        qty=2, equity=500, quote_age_seconds=3)
    failures = [name for name, c in checks if not c.ok]
    assert failures == [], f"Unexpected failures: {failures}"


def test_pre_trade_wash_sale_fails(conn):
    tid = create_trade(conn, "pead", "AAPL", "buy", "live", 10)
    fill_trade(conn, tid, 150.00)
    close_trade(conn, tid, 140.00, "stop")

    checks = pre_trade_checks(
        conn, ticker="AAPL", entry_price=145, stop_price=140,
        qty=2, equity=500, quote_age_seconds=3)
    results = {name: c for name, c in checks}
    assert not results["wash_sale"].ok
    # Other checks should still have run
    assert results["quote_fresh"].ok


# ── Mode isolation ────────────────────────────────

def test_paper_loss_doesnt_block_live_entry(conn):
    """A paper-mode wash-sale lot must not block a live-mode entry."""
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 10)
    fill_trade(conn, tid, 150.00)
    close_trade(conn, tid, 140.00, "stop")  # paper loss → wash-sale lot

    live_check = check_wash_sale_risk(conn, "AAPL", mode="live")
    paper_check = check_wash_sale_risk(conn, "AAPL", mode="paper")
    assert live_check.ok
    assert paper_check.ok


def test_paper_daytrades_do_not_block_live_pdt(conn):
    for _ in range(3):
        tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 1)
        fill_trade(conn, tid, 100.00)
        close_trade(conn, tid, 101.00, "target")

    assert check_pdt_risk(conn, requires_same_day_exit=True, limit=3, mode="live").ok


def test_paper_open_position_doesnt_consume_live_exposure(conn):
    """Open paper position must not eat into live exposure budget."""
    tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 2)
    fill_trade(conn, tid, 175.00)  # $350 paper exposure

    # Live check sees nothing → full budget available
    live_ok = check_exposure(conn, entry_cost=100, equity=500, max_pct=0.80, mode="live")
    assert live_ok.ok

    # Paper check sees the open paper trade → over budget
    paper = check_exposure(conn, entry_cost=100, equity=500, max_pct=0.80, mode="paper")
    assert not paper.ok


def test_paper_position_count_isolated(conn):
    for ticker in ["AAPL", "MSFT", "GOOGL"]:
        tid = create_trade(conn, "pead", ticker, "buy", "paper", 1)
        fill_trade(conn, tid, 100.00)

    # Live mode: no live positions → fine
    assert check_position_count(conn, max_positions=3, mode="live").ok
    # Paper mode: at the cap
    assert not check_position_count(conn, max_positions=3, mode="paper").ok


def test_pdt_skipped_in_paper_mode(conn):
    """PDT only matters for live accounts."""
    for _ in range(5):
        tid = create_trade(conn, "pead", "AAPL", "buy", "paper", 1)
        fill_trade(conn, tid, 100.00)
        close_trade(conn, tid, 101.00, "target")
    # Even with 5 same-day closes, paper mode passes:
    assert check_pdt_risk(conn, requires_same_day_exit=True, limit=3, mode="paper").ok

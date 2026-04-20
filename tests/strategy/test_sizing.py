"""Tests for position sizing and level computation."""

import pytest
from heron.strategy.sizing import size_position, compute_stop_target, minimum_edge_check


# ── size_position ──────────────────────────────────

def test_basic_sizing():
    qty, risk, capital = size_position(
        equity=500, entry_price=100, stop_price=95, risk_pct=0.05, max_capital_pct=0.15)
    # risk_budget = 25, risk_per_share = 5, qty_by_risk = 5
    # max_capital = 75, qty_by_capital = 0.75
    # qty = min(5, 0.75) = 0.75
    assert qty == 0.75
    assert risk == pytest.approx(3.75)
    assert capital == pytest.approx(75.0)


def test_sizing_risk_limited():
    """When risk limit is more binding than capital limit."""
    qty, risk, capital = size_position(
        equity=10000, entry_price=100, stop_price=95, risk_pct=0.02, max_capital_pct=0.50)
    # risk_budget = 200, risk_per_share = 5, qty_by_risk = 40
    # max_capital = 5000, qty_by_capital = 50
    # qty = min(40, 50) = 40
    assert qty == 40


def test_sizing_zero_risk():
    """Stop == entry → no position."""
    qty, _, _ = size_position(equity=500, entry_price=100, stop_price=100)
    assert qty == 0


def test_sizing_zero_price():
    qty, _, _ = size_position(equity=500, entry_price=0, stop_price=0)
    assert qty == 0


def test_sizing_none_prices():
    qty, _, _ = size_position(equity=500, entry_price=None, stop_price=None)
    assert qty == 0


# ── compute_stop_target ──────────────────────────

def test_stop_target_basic():
    stop, target = compute_stop_target(entry_price=100, atr=5)
    assert stop == 90.0   # 100 - 2*5
    assert target == 115.0  # 100 + 3*5


def test_stop_target_custom_mult():
    stop, target = compute_stop_target(entry_price=200, atr=10, stop_mult=1.5, target_mult=2.0)
    assert stop == 185.0
    assert target == 220.0


def test_stop_floor():
    """Stop can't go below $0.01."""
    stop, target = compute_stop_target(entry_price=1.0, atr=2.0)
    assert stop == 0.01


def test_stop_target_no_atr():
    assert compute_stop_target(entry_price=100, atr=0) == (None, None)
    assert compute_stop_target(entry_price=100, atr=None) == (None, None)


# ── minimum_edge_check ──────────────────────────

def test_edge_passes():
    # entry=100, target=101 → 100 bps gross, -25 cost = 75 bps net > 30
    passes, net_bps = minimum_edge_check(100, 101, cost_bps=25, min_edge_bps=30)
    assert passes
    assert net_bps == pytest.approx(75)


def test_edge_fails():
    # entry=100, target=100.03 → 3 bps gross, -25 cost = -22 bps net < 30
    passes, net_bps = minimum_edge_check(100, 100.03, cost_bps=25, min_edge_bps=30)
    assert not passes


def test_edge_none_prices():
    passes, _ = minimum_edge_check(None, 100)
    assert not passes

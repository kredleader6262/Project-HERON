"""Tests for portfolio allocator (B1)."""

import json

import pytest

from heron.journal.strategies import (
    create_strategy, transition_strategy, set_strategy_tags,
)
from heron.strategy.portfolio import compute_allocations, get_strategy_budget

def _mk(c, sid, *, max_cap=0.15, dd_budget=0.05, tags=None, paper=True):
    create_strategy(c, sid, sid, max_capital_pct=max_cap,
                    drawdown_budget_pct=dd_budget)
    transition_strategy(c, sid, "PAPER", reason="test")
    if tags:
        set_strategy_tags(c, sid, tags)


def _save_parity(c, sid, *, passes):
    """Persist a backtest report with a parity verdict."""
    c.execute(
        """INSERT INTO backtest_reports
           (strategy_id, start_date, end_date, params_json, seed,
            metrics_json, trades_json, created_at)
           VALUES (?, '2024-01-01', '2024-12-31', ?, 42, ?, ?, datetime('now'))""",
        (sid, "{}",
         json.dumps({"parity": {"available": True, "passes": passes,
                                "ci_lower": 0.001, "mean_diff": 0.005}}),
         "[]"),
    )
    c.commit()


# ── Basic allocation ──

def test_no_active_strategies(conn):
    assert compute_allocations(conn, 10000.0, mode="paper") == {}


def test_default_alloc_with_no_parity(conn):
    """Strategy without backtest history → parity_factor=0.7."""
    _mk(conn, "s1", max_cap=0.20)
    a = compute_allocations(conn, 10000.0, mode="paper")
    assert "s1" in a
    # 0.20 base * 0.7 parity * 1.0 drawdown = 0.14
    assert a["s1"] == pytest.approx(0.14, abs=1e-4)


def test_parity_pass_boosts_alloc(conn):
    _mk(conn, "winner")
    _save_parity(conn, "winner", passes=True)
    a = compute_allocations(conn, 10000.0, mode="paper")
    assert a["winner"] == pytest.approx(0.15, abs=1e-4)  # 0.15 * 1.0 * 1.0


def test_parity_fail_throttles_alloc(conn):
    _mk(conn, "loser")
    _save_parity(conn, "loser", passes=False)
    a = compute_allocations(conn, 10000.0, mode="paper")
    assert a["loser"] == pytest.approx(0.075, abs=1e-4)  # 0.15 * 0.5


# ── Crowding cap ──

def test_crowding_cap_scales_overlapping_strategies(conn):
    """Three strategies all tagged 'tech' totaling > tag_budget should scale."""
    for sid in ("s1", "s2", "s3"):
        _mk(conn, sid, max_cap=0.15, tags=["tech"])
        _save_parity(conn, sid, passes=True)
    a = compute_allocations(conn, 10000.0, mode="paper")
    tech_total = sum(v for k, v in a.items())
    # tag_budget default 0.30; three at 0.15 each → 0.45, must scale to 0.30
    assert tech_total == pytest.approx(0.30, abs=1e-3)


def test_untagged_strategy_uncapped_by_crowding(conn):
    _mk(conn, "tagged", tags=["tech"])
    _mk(conn, "untagged")
    _save_parity(conn, "tagged", passes=True)
    _save_parity(conn, "untagged", passes=True)
    a = compute_allocations(conn, 10000.0, mode="paper")
    assert a["tagged"] == pytest.approx(0.15, abs=1e-3)
    assert a["untagged"] == pytest.approx(0.15, abs=1e-3)


# ── Global cap ──

def test_global_cap_scales_total(conn):
    """Many strategies summing > max_total should be scaled to fit."""
    for i in range(8):
        sid = f"s{i}"
        _mk(conn, sid, max_cap=0.15)
        _save_parity(conn, sid, passes=True)
    a = compute_allocations(conn, 10000.0, mode="paper")
    total = sum(a.values())
    # Default max_total_exposure is 0.80
    assert total == pytest.approx(0.80, abs=1e-3)
    # All scaled equally
    vals = sorted(a.values())
    assert vals[0] == pytest.approx(vals[-1], abs=1e-4)


# ── get_strategy_budget ──

def test_get_strategy_budget(conn):
    _mk(conn, "s1")
    _save_parity(conn, "s1", passes=True)
    b = get_strategy_budget(conn, "s1", 10000.0, mode="paper")
    assert b == pytest.approx(0.15, abs=1e-4)
    assert get_strategy_budget(conn, "missing", 10000.0, mode="paper") == 0.0


def test_paper_mode_excludes_live_strategies(conn):
    """`mode='paper'` should not list LIVE-only strategies."""
    create_strategy(conn, "live_only", "L")
    transition_strategy(conn, "live_only", "PAPER")
    transition_strategy(conn, "live_only", "LIVE")
    a_paper = compute_allocations(conn, 10000.0, mode="paper")
    a_live = compute_allocations(conn, 10000.0, mode="live")
    assert "live_only" not in a_paper
    assert "live_only" in a_live


def test_max_per_strategy_cap(conn):
    """Strategy with max_cap above per-strategy cap is clipped."""
    _mk(conn, "greedy", max_cap=0.50)  # well above default 0.30 cap
    _save_parity(conn, "greedy", passes=True)
    a = compute_allocations(conn, 10000.0, mode="paper")
    assert a["greedy"] <= 0.30 + 1e-6

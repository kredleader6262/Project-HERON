"""Tests for walk-forward backtest runner."""

import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from heron.backtest.walkforward import _add_months, plan_windows, run_walkforward
from heron.data.cache import init_db, upsert_bars
from heron.journal import init_journal
from heron.journal.strategies import create_strategy
from heron.strategy.pead import PEAD_UNIVERSE


def test_add_months_basic():
    assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)  # leap-year clip
    assert _add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)
    assert _add_months(date(2024, 1, 15), 12) == date(2025, 1, 15)
    assert _add_months(date(2024, 12, 31), 1) == date(2025, 1, 31)


def test_plan_windows_three_year_window():
    wins = plan_windows("2022-01-01", "2024-12-31",
                        train_months=6, test_months=3, step_months=3)
    # First train: 2022-01-01..2022-06-30, test: 2022-07-01..2022-09-30
    assert wins[0] == ("2022-01-01", "2022-06-30", "2022-07-01", "2022-09-30")
    # Step is 3 months -> next train starts 2022-04-01.
    assert wins[1][0] == "2022-04-01"
    # All test ends within range.
    for _, _, _, te in wins:
        assert te <= "2024-12-31"
    # Should produce at least 5 windows.
    assert len(wins) >= 5


def test_plan_windows_zero_train_allowed():
    wins = plan_windows("2024-01-01", "2024-12-31",
                        train_months=0, test_months=3, step_months=3)
    # First test starts on the start date.
    assert wins[0][2] == "2024-01-01"
    assert wins[0][3] == "2024-03-31"


def test_plan_windows_invalid_args():
    with pytest.raises(ValueError):
        plan_windows("2024-01-01", "2024-06-30", train_months=6, test_months=0, step_months=3)
    with pytest.raises(ValueError):
        plan_windows("2024-01-01", "2024-06-30", train_months=6, test_months=3, step_months=0)


def test_plan_windows_window_too_short_returns_empty():
    wins = plan_windows("2024-01-01", "2024-03-01",
                        train_months=12, test_months=3, step_months=3)
    assert wins == []


# ── Integration: walk-forward with parameter fitting ─────────────────

def _bars(tickers, n_days=400, start_date=datetime(2024, 1, 2)):
    rows = []
    for ticker in tickers:
        price = 100.0
        offset = sum(ord(c) for c in ticker) % 17
        for i in range(n_days):
            d = start_date + timedelta(days=i)
            wiggle = ((i + offset) % 11 - 5) / 100
            price = max(1.0, price * (1 + 0.001 + wiggle * 0.01))
            rows.append({
                "ticker": ticker,
                "ts": d.strftime("%Y-%m-%dT09:30:00+00:00"),
                "timeframe": "1Day",
                "open": price * 0.995,
                "high": price * 1.015,
                "low": price * 0.985,
                "close": price,
                "volume": 1_000_000,
                "source": "synthetic",
            })
    return rows


@pytest.fixture
def seeded_conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "j.db"))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_journal(c)
    init_db(c)
    tickers = list(PEAD_UNIVERSE[:2])
    create_strategy(
        c, "wf_test", "WF Test",
        config={"universe": tickers, "stop_mult": 2.0, "target_mult": 3.0,
                "surprise_threshold_pct": 5.0, "max_positions": 3},
    )
    upsert_bars(c, _bars(tickers))
    yield c
    c.close()


def test_walkforward_without_axes_freezes_config(seeded_conn):
    result = run_walkforward(
        seeded_conn, "wf_test",
        start="2024-01-02", end="2024-12-31",
        train_months=3, test_months=3, step_months=3,
        seeder="synthetic",
    )
    # No axes → no per-window locked overrides recorded.
    for child in result["children"]:
        assert child["locked_overrides"] == {}
        assert child["fit"] is None


def test_walkforward_with_axes_locks_per_window(seeded_conn):
    result = run_walkforward(
        seeded_conn, "wf_test",
        start="2024-01-02", end="2024-12-31",
        train_months=3, test_months=3, step_months=3,
        seeder="synthetic",
        axes={"stop_mult": [1.5, 2.0, 2.5]},
        objective="total_return",
    )
    assert result["children"], "expected at least one walk-forward window"
    for child in result["children"]:
        assert "stop_mult" in child["locked_overrides"]
        assert child["fit"] is not None
        assert child["fit"]["objective"] == "total_return"

        # Confirm the locked overrides got persisted to the child report.
        row = seeded_conn.execute(
            "SELECT params_json FROM backtest_reports WHERE id=?",
            (child["report_id"],),
        ).fetchone()
        params = json.loads(row["params_json"])
        assert params["walkforward_locked_overrides"] == child["locked_overrides"]
        assert params["walkforward_fit"]["objective"] == "total_return"

    # Parent report records the per-window trajectory.
    parent_row = seeded_conn.execute(
        "SELECT params_json FROM backtest_reports WHERE id=?",
        (result["parent_report_id"],),
    ).fetchone()
    parent_params = json.loads(parent_row["params_json"])
    assert parent_params["axes"] == {"stop_mult": [1.5, 2.0, 2.5]}
    assert parent_params["objective"] == "total_return"
    assert len(parent_params["per_window_locked"]) == len(result["children"])

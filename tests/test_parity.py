"""Tests for parity report, significance helper, and persisted metrics."""

from __future__ import annotations

import json
import random
import sqlite3

import pytest

from heron.backtest.parity import (
    compute_parity_report,
    get_latest_backtest_parity,
    is_beat_test_passing,
)
from heron.backtest.significance import bootstrap_beat_test
from heron.journal import init_journal
from heron.data.cache import init_db


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "j.sqlite"
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    init_journal(c)
    init_db(c)
    yield c
    c.close()


# ---------- bootstrap_beat_test ----------

class TestBootstrap:
    def test_too_few_days(self):
        r = bootstrap_beat_test([0.01, 0.02])
        assert r["passes"] is False
        assert "Insufficient data" in r["reason"]

    def test_clear_positive(self):
        rng = random.Random(0)
        diffs = [0.01] * 30
        r = bootstrap_beat_test(diffs, n_bootstrap=500, rng=rng)
        assert r["passes"] is True
        assert r["ci_lower"] > 0

    def test_clear_negative(self):
        rng = random.Random(0)
        diffs = [-0.01] * 30
        r = bootstrap_beat_test(diffs, n_bootstrap=500, rng=rng)
        assert r["passes"] is False

    def test_zero_mean_does_not_pass(self):
        rng = random.Random(0)
        diffs = [0.0] * 30
        r = bootstrap_beat_test(diffs, n_bootstrap=500, rng=rng)
        assert r["passes"] is False


# ---------- compute_parity_report ----------

def _curve(rets, start_eq=100.0):
    out, eq = [], start_eq
    for i, r in enumerate(rets):
        eq *= (1 + r)
        out.append({"date": f"2024-01-{i+1:02d}", "equity": round(eq, 4)})
    return out


class TestParity:
    def test_missing_curves(self):
        r = compute_parity_report([], _curve([0.01] * 5))
        assert r["available"] is False

    def test_strategy_beats_baseline(self):
        rng = random.Random(0)
        strat = _curve([0.02] * 20)
        base = _curve([0.005] * 20)
        r = compute_parity_report(strat, base, baseline_report_id=42, n_bootstrap=300, rng=rng)
        assert r["available"] is True
        assert r["passes"] is True
        assert r["baseline_report_id"] == 42
        assert r["n_days"] == 19  # first day skipped

    def test_strategy_loses(self):
        rng = random.Random(0)
        strat = _curve([0.001] * 20)
        base = _curve([0.02] * 20)
        r = compute_parity_report(strat, base, n_bootstrap=300, rng=rng)
        assert r["available"] is True
        assert r["passes"] is False


# ---------- save_report persists parity + regime_breakdown ----------

def _insert_report(conn, strategy_id, start, end, equity_curve, *, parity=None):
    """Helper for tests that need a pre-existing report."""
    metrics = {
        "n_trades": 0, "total_return": 0.0, "win_rate": 0.0,
        "sharpe": 0.0, "max_drawdown": 0.0, "avg_trade_pnl": 0.0,
        "equity_curve": equity_curve,
    }
    if parity is not None:
        metrics["parity"] = parity
    cur = conn.execute(
        """INSERT INTO backtest_reports
           (strategy_id, start_date, end_date, params_json, seed,
            n_trades, total_return, win_rate, sharpe, max_drawdown, avg_trade_pnl,
            metrics_json, trades_json, contaminated, contamination_notes, created_at)
           VALUES (?, ?, ?, '{}', 0, 0,0,0,0,0,0, ?, '[]', 0, NULL, '2024-01-01T00:00:00Z')""",
        (strategy_id, start, end, json.dumps(metrics)),
    )
    conn.commit()
    return cur.lastrowid


class TestPersistedParity:
    def test_save_report_attaches_parity_when_baseline_exists(self, conn):
        from heron.backtest.report import save_report

        # Pre-seed the baseline report covering the same window.
        base_id = _insert_report(conn, "alpha_baseline", "2024-01-01", "2024-01-30",
                                 _curve([0.005] * 25))
        # Now save a "real" strategy report that should pick up parity.
        result = {
            "strategy_id": "alpha",
            "start_date": "2024-01-01",
            "end_date": "2024-01-30",
            "params": {},
            "seed": 0,
            "metrics": {
                "n_trades": 0, "total_return": 0.0, "win_rate": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0, "avg_trade_pnl": 0.0,
            },
            "equity_curve": _curve([0.02] * 25),
            "trades": [],
        }
        rid = save_report(conn, result)
        m = json.loads(conn.execute(
            "SELECT metrics_json FROM backtest_reports WHERE id=?", (rid,),
        ).fetchone()["metrics_json"])
        assert m["parity"]["available"] is True
        assert m["parity"]["baseline_report_id"] == base_id

    def test_save_report_no_baseline_no_parity_field(self, conn):
        from heron.backtest.report import save_report

        result = {
            "strategy_id": "alpha",
            "start_date": "2024-01-01", "end_date": "2024-01-10",
            "params": {}, "seed": 0,
            "metrics": {
                "n_trades": 0, "total_return": 0.0, "win_rate": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0, "avg_trade_pnl": 0.0,
            },
            "equity_curve": _curve([0.01] * 5),
            "trades": [],
        }
        rid = save_report(conn, result)
        m = json.loads(conn.execute(
            "SELECT metrics_json FROM backtest_reports WHERE id=?", (rid,),
        ).fetchone()["metrics_json"])
        # No baseline → either absent or available=False; both are acceptable.
        assert m.get("parity") is None or m["parity"].get("available") is False


class TestReparity:
    def test_reparity_backfills_parity(self, conn):
        from heron.backtest.report import reparity_report

        _insert_report(conn, "alpha_baseline", "2024-01-01", "2024-01-30",
                       _curve([0.005] * 25))
        rid = _insert_report(conn, "alpha", "2024-01-01", "2024-01-30",
                             _curve([0.02] * 25))
        m = reparity_report(conn, rid)
        assert m["parity"]["available"] is True
        assert m["parity"]["passes"] is True


class TestLatestParityHelpers:
    def test_get_latest_returns_none_when_missing(self, conn):
        _insert_report(conn, "alpha", "2024-01-01", "2024-01-10",
                       _curve([0.01] * 5))
        assert get_latest_backtest_parity(conn, "alpha") is None

    def test_is_beat_test_passing_true(self, conn):
        _insert_report(conn, "alpha", "2024-01-01", "2024-01-10",
                       _curve([0.01] * 5),
                       parity={"available": True, "passes": True,
                               "ci_lower": 0.001, "ci_upper": 0.01,
                               "mean_diff": 0.005, "n_days": 5,
                               "n_bootstrap": 1000})
        assert is_beat_test_passing(conn, "alpha") is True

    def test_is_beat_test_passing_false_when_fails(self, conn):
        _insert_report(conn, "alpha", "2024-01-01", "2024-01-10",
                       _curve([0.01] * 5),
                       parity={"available": True, "passes": False,
                               "ci_lower": -0.01, "ci_upper": 0.01,
                               "mean_diff": 0.0, "n_days": 5,
                               "n_bootstrap": 1000})
        assert is_beat_test_passing(conn, "alpha") is False

"""Tests for the walk-forward parameter fitter."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from heron.backtest.fitter import _score, fit_params
from heron.data.cache import init_db, upsert_bars
from heron.journal import init_journal
from heron.journal.strategies import create_strategy
from heron.strategy.pead import PEAD_UNIVERSE


def _bars(tickers, n_days=200, start_date=datetime(2024, 1, 2)):
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
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "j.db"))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_journal(c)
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    """Strategy + 200 days of bars for two tickers."""
    tickers = list(PEAD_UNIVERSE[:2])
    create_strategy(
        conn, "fit_test", "Fit Test",
        config={"universe": tickers, "stop_mult": 2.0, "target_mult": 3.0,
                "surprise_threshold_pct": 5.0, "max_positions": 3},
    )
    upsert_bars(conn, _bars(tickers))
    return "fit_test", tickers


class TestScore:
    def test_low_trades_penalized(self):
        a = {"n_trades": 1, "sharpe": 5.0, "total_return": 0.5}
        b = {"n_trades": 10, "sharpe": 0.5, "total_return": 0.05}
        assert _score(b, "sharpe") > _score(a, "sharpe")

    def test_sharpe_objective(self):
        a = {"n_trades": 10, "sharpe": 1.0, "total_return": 0.1}
        b = {"n_trades": 10, "sharpe": 2.0, "total_return": 0.05}
        assert _score(b, "sharpe") > _score(a, "sharpe")

    def test_sharpe_none_falls_back_to_return(self):
        a = {"n_trades": 10, "sharpe": None, "total_return": 0.20}
        b = {"n_trades": 10, "sharpe": None, "total_return": 0.05}
        assert _score(a, "sharpe") > _score(b, "sharpe")

    def test_total_return_objective(self):
        a = {"n_trades": 10, "sharpe": 0.5, "total_return": 0.30}
        b = {"n_trades": 10, "sharpe": 2.0, "total_return": 0.05}
        assert _score(a, "total_return") > _score(b, "total_return")

    def test_unknown_objective_raises(self):
        with pytest.raises(ValueError):
            _score({"n_trades": 10}, "nonsense")


class TestFitParams:
    def test_empty_axes_runs_single_combo(self, conn, seeded):
        sid, _ = seeded
        result = fit_params(conn, sid, {}, start="2024-01-02", end="2024-04-30")
        assert result["overrides"] == {}
        assert len(result["candidates"]) == 1

    def test_picks_winner_by_objective(self, conn, seeded):
        sid, _ = seeded
        result = fit_params(
            conn, sid, {"stop_mult": [1.0, 2.0, 3.0]},
            start="2024-01-02", end="2024-06-30",
            objective="total_return",
        )
        assert "stop_mult" in result["overrides"]
        assert result["objective"] == "total_return"
        # Winner score >= every other valid candidate's score.
        valid = [c for c in result["candidates"] if c.get("metrics") is not None]
        winner_score = result["score"]
        for c in valid:
            assert c["score"] <= winner_score

    def test_all_failing_raises(self, conn, seeded):
        sid, _ = seeded
        # Window with no bars → run_strategy_backtest raises ValueError on every combo.
        with pytest.raises(ValueError, match="cannot lock"):
            fit_params(
                conn, sid, {"stop_mult": [1.0, 2.0]},
                start="2030-01-01", end="2030-06-30",
            )

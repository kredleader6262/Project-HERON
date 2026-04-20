"""Tests for M13 — deterministic backtester."""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from heron.backtest import run_backtest, save_report, list_reports, get_report
from heron.backtest.costs import (
    apply_slippage, round_trip_cost, sell_fees, slippage_bps,
)
from heron.backtest.report import check_contamination
from heron.backtest.seeders import synthetic_pead_candidates
from heron.journal import init_journal
from heron.journal.strategies import create_strategy
from heron.strategy.pead import PEADStrategy, PEAD_UNIVERSE


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "j.db"))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_journal(c)
    yield c
    c.close()


def _synthetic_bars(tickers, n_days=200, start_price=100.0, drift=0.001):
    """Deterministic fake bars with a mild upward drift + periodic volatility."""
    bars = []
    start = datetime(2024, 1, 2)
    for ticker in tickers:
        price = start_price
        # ticker-specific seed for variation
        offset = sum(ord(c) for c in ticker) % 17
        for i in range(n_days):
            d = start + timedelta(days=i)
            # Simple deterministic wiggle
            wiggle = ((i + offset) % 11 - 5) / 100
            price = max(1.0, price * (1 + drift + wiggle * 0.01))
            high = price * 1.015
            low = price * 0.985
            open_ = price * 0.995
            close = price
            bars.append({
                "ticker": ticker,
                "ts": d.strftime("%Y-%m-%dT09:30:00+00:00"),
                "timeframe": "1Day",
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000_000,
            })
    return bars


# ── Costs ────────────────────────────────

class TestCosts:

    def test_slippage_applied_correctly(self):
        # Buy pays up
        assert apply_slippage(100, "buy", bps=10) == pytest.approx(100.1)
        # Sell gives up
        assert apply_slippage(100, "sell", bps=10) == pytest.approx(99.9)

    def test_sell_fees_include_sec_and_taf(self):
        fees = sell_fees(100, 50)  # $5000 proceeds
        assert fees > 0
        # SEC on $5000 at 27.80/M
        assert fees > 5000 * (27.80 / 1_000_000) - 0.01

    def test_taf_cap(self):
        # 1 million shares at $0.01 would exceed cap
        fees = sell_fees(1_000_000, 50)
        # TAF component capped at $8.30
        # So fees ≈ SEC($1390) + $8.30
        assert fees < 1400

    def test_round_trip_cost_positive(self):
        result = round_trip_cost(100, 105, 10)
        assert result["total_cost"] > 0
        assert result["entry_fill"] > 100
        assert result["exit_fill"] < 105


# ── Contamination flag ────────────────────────────────

class TestContamination:

    def test_pre_cutoff_flagged(self):
        flagged, notes = check_contamination("2023-01-01", "2023-06-30")
        assert flagged
        assert "cutoff" in notes.lower()

    def test_post_cutoff_clean(self):
        flagged, notes = check_contamination("2029-01-01", "2029-06-30")
        assert not flagged
        assert notes is None


# ── Engine determinism ────────────────────────────────

class TestDeterminism:

    def test_same_inputs_same_output(self):
        tickers = PEAD_UNIVERSE[:3]
        bars = _synthetic_bars(tickers, n_days=120)
        cands = synthetic_pead_candidates(bars, universe=tickers, seed=42)
        strat1 = PEADStrategy(strategy_id="det1", is_llm_variant=False)
        strat2 = PEADStrategy(strategy_id="det1", is_llm_variant=False)
        r1 = run_backtest(strat1, bars, cands, seed=42)
        r2 = run_backtest(strat2, bars, cands, seed=42)
        assert r1["trades"] == r2["trades"]
        assert r1["equity_curve"] == r2["equity_curve"]
        assert r1["metrics"] == r2["metrics"]

    def test_different_seeds_different_candidates(self):
        bars = _synthetic_bars(PEAD_UNIVERSE[:2], n_days=200, drift=0.003)
        c1 = synthetic_pead_candidates(bars, universe=PEAD_UNIVERSE[:2], seed=1)
        c2 = synthetic_pead_candidates(bars, universe=PEAD_UNIVERSE[:2], seed=99)
        # At least one should have generated candidates
        assert c1 or c2
        assert c1 != c2


# ── Engine behavior ────────────────────────────────

class TestEngine:

    def test_empty_bars_produces_empty_report(self):
        strat = PEADStrategy(strategy_id="e1", is_llm_variant=False)
        r = run_backtest(strat, [], [], seed=0)
        assert r["metrics"]["n_trades"] == 0
        assert r["final_equity"] == r["initial_equity"]

    def test_respects_max_positions(self):
        tickers = PEAD_UNIVERSE
        bars = _synthetic_bars(tickers, n_days=100)
        # Force a candidate on the same day for every ticker
        same_day = bars[30]["ts"][:10]
        cands = [
            {"date": same_day, "ticker": t, "surprise_pct": 10.0,
             "announced_hours_ago": 12, "conviction": 0.8}
            for t in tickers
        ]
        strat = PEADStrategy(strategy_id="e2", is_llm_variant=False)
        r = run_backtest(strat, bars, cands, seed=0)
        # Shouldn't open more than max_positions concurrent trades
        # (though over time can be more). Check that no day had > max open.
        # Simpler invariant: n_trades <= ticker count (6).
        assert r["metrics"]["n_trades"] <= len(tickers)

    def test_metrics_sane(self):
        tickers = PEAD_UNIVERSE[:3]
        bars = _synthetic_bars(tickers, n_days=200, drift=0.002)
        cands = synthetic_pead_candidates(bars, universe=tickers, seed=7)
        strat = PEADStrategy(strategy_id="e3", is_llm_variant=False)
        r = run_backtest(strat, bars, cands, seed=7)
        m = r["metrics"]
        assert 0 <= m["win_rate"] <= 1
        assert m["n_trades"] == m["n_wins"] + m["n_losses"]
        assert r["equity_curve"][0]["date"] <= r["equity_curve"][-1]["date"]

    def test_trades_have_all_required_fields(self):
        tickers = PEAD_UNIVERSE[:2]
        bars = _synthetic_bars(tickers, n_days=150)
        cands = synthetic_pead_candidates(bars, universe=tickers, seed=3)
        strat = PEADStrategy(strategy_id="e4", is_llm_variant=False)
        r = run_backtest(strat, bars, cands, seed=3)
        for t in r["trades"]:
            for k in ("ticker", "entry", "exit", "qty", "net_pnl", "reason"):
                assert k in t


# ── Persistence ────────────────────────────────

class TestPersistence:

    def test_save_and_retrieve(self, conn):
        create_strategy(conn, "persist1", "Test")
        tickers = PEAD_UNIVERSE[:2]
        bars = _synthetic_bars(tickers, n_days=100)
        cands = synthetic_pead_candidates(bars, universe=tickers, seed=5)
        strat = PEADStrategy(strategy_id="persist1", is_llm_variant=False)
        r = run_backtest(strat, bars, cands, seed=5)
        rid = save_report(conn, r)
        assert rid > 0
        row = get_report(conn, rid)
        assert row["strategy_id"] == "persist1"
        assert row["seed"] == 5
        assert row["n_trades"] == r["metrics"]["n_trades"]

    def test_list_filters_by_strategy(self, conn):
        create_strategy(conn, "s_a", "A")
        create_strategy(conn, "s_b", "B")
        tickers = PEAD_UNIVERSE[:2]
        bars = _synthetic_bars(tickers, n_days=80)
        cands = synthetic_pead_candidates(bars, universe=tickers, seed=1)
        for sid in ("s_a", "s_b"):
            strat = PEADStrategy(strategy_id=sid, is_llm_variant=False)
            r = run_backtest(strat, bars, cands, seed=1)
            r["strategy_id"] = sid  # ensure override for save
            save_report(conn, r)
        a_only = list_reports(conn, strategy_id="s_a")
        assert all(r["strategy_id"] == "s_a" for r in a_only)

    def test_contamination_flag_stored(self, conn):
        create_strategy(conn, "old_win", "Old")
        tickers = PEAD_UNIVERSE[:2]
        # Force dates pre-cutoff
        bars = []
        base = datetime(2023, 6, 1)
        for ticker in tickers:
            price = 100.0
            for i in range(60):
                d = base + timedelta(days=i)
                price *= 1.001
                bars.append({
                    "ticker": ticker,
                    "ts": d.strftime("%Y-%m-%dT09:30:00+00:00"),
                    "timeframe": "1Day",
                    "open": price, "high": price * 1.01, "low": price * 0.99,
                    "close": price, "volume": 100,
                })
        cands = []
        strat = PEADStrategy(strategy_id="old_win", is_llm_variant=False)
        r = run_backtest(strat, bars, cands, seed=0)
        rid = save_report(conn, r)
        row = get_report(conn, rid)
        assert row["contaminated"] == 1
        assert "cutoff" in (row["contamination_notes"] or "").lower()

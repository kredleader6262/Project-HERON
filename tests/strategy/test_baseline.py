"""Tests for M9 — Baseline-variant runner, equity curves, bootstrap beat test."""

import json
import random

import pytest

from heron.journal.trades import create_trade, fill_trade, close_trade
from heron.strategy.baseline import (
    ensure_baseline, mirror_candidate_to_baseline,
    get_daily_returns, get_paired_daily_returns,
    bootstrap_beat_test, run_beat_test, get_equity_curve,
)


@pytest.fixture
def journal_conn(baseline_pead_v1_conn):
    return baseline_pead_v1_conn


def _make_closed_trade(conn, strategy_id, ticker, fill, close, date, side="buy"):
    """Helper: create a filled+closed trade on a specific date."""
    tid = create_trade(conn, strategy_id, ticker, side, "paper", 100)
    fill_trade(conn, tid, fill)
    # Override filled_at and close_filled_at to specific date
    conn.execute(
        "UPDATE trades SET filled_at=?, close_filled_at=?, close_price=?, close_reason='target', "
        "pnl=?, pnl_pct=?, updated_at=? WHERE id=?",
        (f"{date}T10:00:00+00:00", f"{date}T15:00:00+00:00",
         close, (close - fill) * 100, (close - fill) / fill,
         f"{date}T15:00:00+00:00", tid),
    )
    conn.commit()
    return tid


class TestEnsureBaseline:

    def test_creates_baseline(self, journal_conn):
        bid = ensure_baseline(journal_conn, "pead_v1")
        assert bid == "pead_v1_baseline"

        row = journal_conn.execute(
            "SELECT * FROM strategies WHERE id=?", (bid,)
        ).fetchone()
        assert row is not None
        assert row["is_baseline"] == 1
        assert row["parent_id"] == "pead_v1"
        assert row["state"] == "PAPER"

    def test_idempotent(self, journal_conn):
        bid1 = ensure_baseline(journal_conn, "pead_v1")
        bid2 = ensure_baseline(journal_conn, "pead_v1")
        assert bid1 == bid2

        count = journal_conn.execute(
            "SELECT COUNT(*) as n FROM strategies WHERE id=?", (bid1,)
        ).fetchone()["n"]
        assert count == 1

    def test_missing_parent(self, journal_conn):
        with pytest.raises(ValueError, match="not found"):
            ensure_baseline(journal_conn, "nonexistent")


class TestMirrorCandidate:

    def test_mirror_basic(self, journal_conn):
        from heron.journal.candidates import create_candidate, get_candidate

        ensure_baseline(journal_conn, "pead_v1")
        cid = create_candidate(journal_conn, "pead_v1", "AAPL", side="buy",
                               local_score=0.75, thesis="Strong beat",
                               context_json='{"sentiment": "positive"}')

        mid = mirror_candidate_to_baseline(journal_conn, cid, "pead_v1_baseline")
        assert mid is not None

        mirrored = get_candidate(journal_conn, mid)
        assert mirrored["strategy_id"] == "pead_v1_baseline"
        assert mirrored["ticker"] == "AAPL"
        assert mirrored["source"] == "baseline_mirror"
        assert "[BASELINE]" in mirrored["thesis"]

    def test_mirror_dedup(self, journal_conn):
        from heron.journal.candidates import create_candidate

        ensure_baseline(journal_conn, "pead_v1")
        cid = create_candidate(journal_conn, "pead_v1", "AAPL", side="buy",
                               local_score=0.75, context_json='{}')

        mid1 = mirror_candidate_to_baseline(journal_conn, cid, "pead_v1_baseline")
        mid2 = mirror_candidate_to_baseline(journal_conn, cid, "pead_v1_baseline")
        assert mid1 == mid2  # Deduped

    def test_mirror_preserves_signal_link(self, journal_conn):
        from heron.journal.campaigns import create_campaign
        from heron.journal.candidates import create_candidate
        from heron.journal.signals import create_signal, get_signal_for_candidate, link_signal_candidate

        create_campaign(journal_conn, "baseline_desk", "Baseline Desk", state="ACTIVE")
        journal_conn.execute("UPDATE strategies SET campaign_id='baseline_desk' WHERE id='pead_v1'")
        journal_conn.commit()
        ensure_baseline(journal_conn, "pead_v1")
        cid = create_candidate(journal_conn, "pead_v1", "AAPL", side="buy", context_json='{}')
        sid = create_signal(
            journal_conn, "baseline_desk", "research_local", "earnings", "long_bias",
            "AAPL beat", ticker="AAPL",
        )
        link_signal_candidate(journal_conn, sid, cid, "pead_v1")

        mid1 = mirror_candidate_to_baseline(journal_conn, cid, "pead_v1_baseline")
        mid2 = mirror_candidate_to_baseline(journal_conn, cid, "pead_v1_baseline")
        trace = get_signal_for_candidate(journal_conn, mid1)
        assert mid1 == mid2
        assert trace["signal_id"] == sid
        assert trace["bridge_source"] == "baseline_mirror"
        count = journal_conn.execute(
            "SELECT COUNT(*) AS n FROM signal_candidates WHERE candidate_id=?", (mid1,)
        ).fetchone()["n"]
        assert count == 1

    def test_mirror_without_signal_has_no_signal_link(self, journal_conn):
        from heron.journal.candidates import create_candidate
        from heron.journal.signals import get_signal_for_candidate

        ensure_baseline(journal_conn, "pead_v1")
        cid = create_candidate(journal_conn, "pead_v1", "MSFT", side="buy", context_json='{}')

        mid = mirror_candidate_to_baseline(journal_conn, cid, "pead_v1_baseline")
        assert get_signal_for_candidate(journal_conn, mid) is None

    def test_mirror_nonexistent(self, journal_conn):
        result = mirror_candidate_to_baseline(journal_conn, 9999, "pead_v1_baseline")
        assert result is None


class TestDailyReturns:

    def test_basic(self, journal_conn):
        _make_closed_trade(journal_conn, "pead_v1", "AAPL", 150.0, 155.0, "2026-01-05")
        _make_closed_trade(journal_conn, "pead_v1", "MSFT", 300.0, 295.0, "2026-01-06")

        returns = get_daily_returns(journal_conn, "pead_v1")
        assert len(returns) == 2
        assert returns[0]["date"] == "2026-01-05"
        assert returns[0]["return_pct"] > 0
        assert returns[1]["return_pct"] < 0

    def test_aggregation(self, journal_conn):
        # Two trades same day
        _make_closed_trade(journal_conn, "pead_v1", "AAPL", 150.0, 155.0, "2026-01-05")
        _make_closed_trade(journal_conn, "pead_v1", "MSFT", 300.0, 310.0, "2026-01-05")

        returns = get_daily_returns(journal_conn, "pead_v1")
        assert len(returns) == 1
        assert returns[0]["return_pct"] > 0

    def test_date_filter(self, journal_conn):
        _make_closed_trade(journal_conn, "pead_v1", "AAPL", 150.0, 155.0, "2026-01-05")
        _make_closed_trade(journal_conn, "pead_v1", "MSFT", 300.0, 310.0, "2026-02-10")

        returns = get_daily_returns(journal_conn, "pead_v1",
                                    start_date="2026-02-01", end_date="2026-02-28")
        assert len(returns) == 1
        assert returns[0]["date"] == "2026-02-10"

    def test_empty(self, journal_conn):
        returns = get_daily_returns(journal_conn, "pead_v1")
        assert returns == []


class TestPairedReturns:

    def test_paired(self, journal_conn):
        ensure_baseline(journal_conn, "pead_v1")

        _make_closed_trade(journal_conn, "pead_v1", "AAPL", 150.0, 155.0, "2026-01-05")
        _make_closed_trade(journal_conn, "pead_v1_baseline", "AAPL", 150.0, 153.0, "2026-01-05")

        paired = get_paired_daily_returns(journal_conn, "pead_v1")
        assert len(paired) == 1
        assert paired[0]["llm_return"] > paired[0]["baseline_return"]
        assert paired[0]["diff"] > 0

    def test_missing_days(self, journal_conn):
        ensure_baseline(journal_conn, "pead_v1")

        _make_closed_trade(journal_conn, "pead_v1", "AAPL", 150.0, 155.0, "2026-01-05")
        _make_closed_trade(journal_conn, "pead_v1_baseline", "MSFT", 300.0, 310.0, "2026-01-06")

        paired = get_paired_daily_returns(journal_conn, "pead_v1")
        assert len(paired) == 2
        # Day 1: LLM has return, baseline=0
        assert paired[0]["baseline_return"] == 0.0
        # Day 2: baseline has return, LLM=0
        assert paired[1]["llm_return"] == 0.0


class TestBootstrapBeatTest:

    def test_clear_win(self):
        """LLM consistently beats baseline → passes."""
        diffs = [0.01, 0.02, 0.015, 0.008, 0.012, 0.018, 0.009, 0.011, 0.014, 0.016]
        rng = random.Random(42)
        result = bootstrap_beat_test(diffs, n_bootstrap=5000, rng=rng)
        assert result["passes"] is True
        assert result["ci_lower"] > 0

    def test_clear_loss(self):
        """LLM consistently loses → fails."""
        diffs = [-0.01, -0.02, -0.015, -0.008, -0.012, -0.018, -0.009, -0.011]
        rng = random.Random(42)
        result = bootstrap_beat_test(diffs, n_bootstrap=5000, rng=rng)
        assert result["passes"] is False
        assert result["ci_upper"] < 0

    def test_ambiguous(self):
        """Mixed results → CI spans zero → fails."""
        diffs = [0.01, -0.01, 0.02, -0.02, 0.005, -0.005, 0.01, -0.01]
        rng = random.Random(42)
        result = bootstrap_beat_test(diffs, n_bootstrap=5000, rng=rng)
        assert result["passes"] is False

    def test_insufficient_data(self):
        """Fewer than 5 days → fails with reason."""
        diffs = [0.01, 0.02]
        result = bootstrap_beat_test(diffs)
        assert result["passes"] is False
        assert "Insufficient" in result.get("reason", "")

    def test_empty(self):
        result = bootstrap_beat_test([])
        assert result["passes"] is False
        assert result["n_days"] == 0

    def test_deterministic_with_seed(self):
        """Same seed → same result."""
        diffs = [0.01, 0.005, 0.012, 0.008, 0.015, 0.003, 0.007]
        r1 = bootstrap_beat_test(diffs, rng=random.Random(123))
        r2 = bootstrap_beat_test(diffs, rng=random.Random(123))
        assert r1["ci_lower"] == r2["ci_lower"]
        assert r1["ci_upper"] == r2["ci_upper"]


class TestRunBeatTest:

    def test_integration(self, journal_conn):
        ensure_baseline(journal_conn, "pead_v1")

        # LLM variant: consistently positive
        for i, d in enumerate(range(5, 15)):
            _make_closed_trade(journal_conn, "pead_v1", "AAPL",
                               150.0, 155.0 + i * 0.5, f"2026-01-{d:02d}")
        # Baseline: consistently less positive
        for i, d in enumerate(range(5, 15)):
            _make_closed_trade(journal_conn, "pead_v1_baseline", "AAPL",
                               150.0, 152.0, f"2026-01-{d:02d}")

        result = run_beat_test(journal_conn, "pead_v1")
        assert result["strategy_id"] == "pead_v1"
        assert result["baseline_id"] == "pead_v1_baseline"
        assert result["n_days"] == 10


class TestEquityCurve:

    def test_basic(self, journal_conn):
        _make_closed_trade(journal_conn, "pead_v1", "AAPL", 100.0, 105.0, "2026-01-05")
        _make_closed_trade(journal_conn, "pead_v1", "MSFT", 200.0, 210.0, "2026-01-06")

        curve = get_equity_curve(journal_conn, "pead_v1", initial=100000.0)
        assert len(curve) == 2
        assert curve[0]["equity"] > 100000
        assert curve[1]["equity"] > curve[0]["equity"]

    def test_empty(self, journal_conn):
        curve = get_equity_curve(journal_conn, "pead_v1")
        assert curve == []


class TestPEADVariants:
    """Test that PEADStrategy works as both LLM and baseline variant."""

    def test_llm_variant_vetoes(self):
        from heron.strategy.pead import PEADStrategy

        llm = PEADStrategy(is_llm_variant=True, config={**_pead_config(), "min_conviction": 0.5})
        ok, reason = llm.screen_candidate({
            "ticker": "AAPL", "surprise_pct": 7.0,
            "announced_hours_ago": 12, "conviction": 0.3,
        })
        assert not ok
        assert "Conviction" in reason

    def test_baseline_ignores_conviction(self):
        from heron.strategy.pead import PEADStrategy

        baseline = PEADStrategy(is_llm_variant=False, config=_pead_config())
        ok, reason = baseline.screen_candidate({
            "ticker": "AAPL", "surprise_pct": 7.0,
            "announced_hours_ago": 12, "conviction": 0.1,
        })
        assert ok

    def test_baseline_ignores_veto(self):
        from heron.strategy.pead import PEADStrategy

        baseline = PEADStrategy(is_llm_variant=False, config=_pead_config())
        ok, reason = baseline.screen_candidate({
            "ticker": "AAPL", "surprise_pct": 7.0,
            "announced_hours_ago": 12, "llm_veto": True,
        })
        assert ok


def _pead_config():
    return {
        "universe": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META"],
        "surprise_threshold_pct": 5.0,
        "surprise_window_hours": 24,
        "min_conviction": 0.0,
    }

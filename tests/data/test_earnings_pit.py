"""Tests for point-in-time semantics on the earnings cache."""

from __future__ import annotations

import sqlite3
import pytest

from heron.data.cache import init_db
from heron.data.earnings import cache_earnings_events, get_earnings_events
from heron.backtest.seeders import real_pead_candidates


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _evt(eps=1.40, surprise=7.69, **over):
    base = {
        "ticker": "AAPL", "event_date": "2024-08-01", "event_time": "amc",
        "eps_actual": eps, "eps_estimate": 1.30, "surprise_pct": surprise,
        "revenue_actual": None, "revenue_estimate": None, "source": "finnhub",
    }
    base.update(over)
    return base


class TestPITWrite:
    def test_first_insert_creates_current_row(self, conn):
        n = cache_earnings_events(conn, [_evt()], as_of="2024-08-01T20:00:00Z")
        assert n == 1
        rows = conn.execute("SELECT as_of_ts, superseded_at FROM earnings_events").fetchall()
        assert len(rows) == 1
        assert rows[0]["as_of_ts"] == "2024-08-01T20:00:00Z"
        assert rows[0]["superseded_at"] is None

    def test_no_op_when_values_unchanged(self, conn):
        cache_earnings_events(conn, [_evt()], as_of="2024-08-01T20:00:00Z")
        n = cache_earnings_events(conn, [_evt()], as_of="2024-08-15T20:00:00Z")
        assert n == 0  # no new row inserted
        rows = conn.execute("SELECT as_of_ts FROM earnings_events").fetchall()
        assert len(rows) == 1
        # Original timestamp preserved — the value never changed.
        assert rows[0]["as_of_ts"] == "2024-08-01T20:00:00Z"

    def test_restatement_supersedes_old_row(self, conn):
        cache_earnings_events(conn, [_evt(eps=1.40, surprise=7.69)],
                              as_of="2024-08-01T20:00:00Z")
        n = cache_earnings_events(conn, [_evt(eps=1.45, surprise=11.54)],
                                  as_of="2024-09-15T20:00:00Z")
        assert n == 1
        rows = conn.execute(
            "SELECT eps_actual, as_of_ts, superseded_at FROM earnings_events ORDER BY as_of_ts"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["superseded_at"] == "2024-09-15T20:00:00Z"
        assert rows[1]["superseded_at"] is None
        assert rows[1]["eps_actual"] == 1.45


class TestPITRead:
    def setup_event(self, conn):
        cache_earnings_events(conn, [_evt(eps=1.40, surprise=7.69)],
                              as_of="2024-08-01T20:00:00Z")
        cache_earnings_events(conn, [_evt(eps=1.45, surprise=11.54)],
                              as_of="2024-09-15T20:00:00Z")

    def test_default_returns_current(self, conn):
        self.setup_event(conn)
        rows = get_earnings_events(conn)
        assert len(rows) == 1
        assert rows[0]["surprise_pct"] == 11.54

    def test_as_of_before_restatement(self, conn):
        self.setup_event(conn)
        rows = get_earnings_events(conn, as_of="2024-08-15T00:00:00Z")
        assert len(rows) == 1
        assert rows[0]["surprise_pct"] == 7.69

    def test_as_of_after_restatement(self, conn):
        self.setup_event(conn)
        rows = get_earnings_events(conn, as_of="2024-10-01T00:00:00Z")
        assert len(rows) == 1
        assert rows[0]["surprise_pct"] == 11.54

    def test_as_of_before_first_insert(self, conn):
        self.setup_event(conn)
        rows = get_earnings_events(conn, as_of="2024-07-01T00:00:00Z")
        assert rows == []


class TestSeederPIT:
    def test_real_pead_candidates_respects_as_of(self, conn):
        # Initial: small surprise (below default 5% threshold).
        cache_earnings_events(conn, [_evt(eps=1.31, surprise=0.77)],
                              as_of="2024-08-01T20:00:00Z")
        # Restated: large surprise (above threshold).
        cache_earnings_events(conn, [_evt(eps=1.45, surprise=11.54)],
                              as_of="2024-09-15T20:00:00Z")

        # Replaying as of just after the original announcement: too small,
        # should not be a candidate.
        before = real_pead_candidates(conn, universe=["AAPL"],
                                      as_of="2024-08-15T00:00:00Z")
        assert before == []

        # Current view: above threshold, should be a candidate.
        now = real_pead_candidates(conn, universe=["AAPL"])
        assert len(now) == 1
        assert now[0]["surprise_pct"] == 11.54


class TestUniverseSnapshots:
    def test_resolve_universe_uses_snapshot_when_present(self, conn):
        from heron.backtest.runner import _resolve_universe
        from heron.util import utc_now_iso
        now = utc_now_iso()
        conn.executemany(
            "INSERT INTO universe_snapshots (snapshot_date, ticker, source, note, created_at)"
            " VALUES (?, ?, 'manual', NULL, ?)",
            [("2024-01-01", "AAA", now), ("2024-01-01", "BBB", now)],
        )
        conn.commit()
        # Strategy row with no config; without `conn`+`as_of` falls back to PEAD.
        strategy_row = {"config": None}
        # Simulate Row-like access via dict — mimic conn.row_factory output.
        class _Row(dict):
            def keys(self):
                return super().keys()
        row = _Row(strategy_row)
        resolved = _resolve_universe(row, conn=conn, as_of="2024-06-01T00:00:00Z")
        assert resolved == ["AAA", "BBB"]

    def test_resolve_universe_falls_back_when_no_snapshot_covers_as_of(self, conn):
        from heron.backtest.runner import _resolve_universe
        from heron.strategy.templates import PEAD_UNIVERSE
        from heron.util import utc_now_iso
        now = utc_now_iso()
        conn.execute(
            "INSERT INTO universe_snapshots (snapshot_date, ticker, source, note, created_at)"
            " VALUES (?, ?, 'manual', NULL, ?)",
            ("2024-06-01", "AAA", now),
        )
        conn.commit()
        class _Row(dict):
            def keys(self):
                return super().keys()
        # as_of before the only snapshot → fall back.
        resolved = _resolve_universe(_Row({"config": None}), conn=conn,
                                     as_of="2024-01-01T00:00:00Z")
        assert resolved == list(PEAD_UNIVERSE)

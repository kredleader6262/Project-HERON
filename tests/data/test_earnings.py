"""Tests for the earnings cache + real PEAD seeder."""

import sqlite3

import pytest

from heron.backtest.seeders import real_pead_candidates
from heron.data.cache import init_db
from heron.data.earnings import (
    cache_earnings_events,
    fetch_finnhub_earnings,
    get_earnings_events,
    _surprise_pct,
)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def test_surprise_pct_basic():
    assert _surprise_pct(1.10, 1.00) == 10.0
    assert _surprise_pct(0.90, 1.00) == -10.0
    # Negative estimate uses absolute value as denominator (so a beat is positive).
    assert _surprise_pct(-0.50, -1.00) == 50.0


def test_surprise_pct_handles_none_and_zero():
    assert _surprise_pct(None, 1.0) is None
    assert _surprise_pct(1.0, None) is None
    assert _surprise_pct(1.0, 0) is None
    assert _surprise_pct("bad", 1.0) is None


def test_cache_and_get_earnings_events(conn):
    events = [
        {"ticker": "AAPL", "event_date": "2024-08-01", "event_time": "amc",
         "eps_actual": 1.40, "eps_estimate": 1.30, "surprise_pct": 7.69,
         "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"},
        {"ticker": "MSFT", "event_date": "2024-07-30", "event_time": "amc",
         "eps_actual": 2.95, "eps_estimate": 2.93, "surprise_pct": 0.68,
         "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"},
    ]
    n = cache_earnings_events(conn, events)
    assert n == 2

    rows = get_earnings_events(conn)
    assert [r["ticker"] for r in rows] == ["MSFT", "AAPL"]  # ordered by date

    # Filter by ticker + min surprise
    high = get_earnings_events(conn, min_abs_surprise=5.0)
    assert [r["ticker"] for r in high] == ["AAPL"]

    just_aapl = get_earnings_events(conn, tickers=["AAPL"])
    assert len(just_aapl) == 1


def test_cache_upserts_on_conflict(conn):
    """Restating a value supersedes the old row but keeps both for PIT lookup.

    The default `get_earnings_events()` (no `as_of`) returns only the current
    row, so end-users see exactly one row per (ticker, event_date, source) —
    same as the pre-PIT behavior.
    """
    base = {"ticker": "AAPL", "event_date": "2024-08-01", "event_time": "amc",
            "eps_actual": 1.40, "eps_estimate": 1.30, "surprise_pct": 7.69,
            "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"}
    cache_earnings_events(conn, [base])
    updated = dict(base)
    updated["eps_actual"] = 1.45
    updated["surprise_pct"] = 11.54
    cache_earnings_events(conn, [updated])

    rows = get_earnings_events(conn)
    assert len(rows) == 1
    assert rows[0]["surprise_pct"] == 11.54


def test_real_pead_candidates_filters_universe_and_threshold(conn):
    cache_earnings_events(conn, [
        {"ticker": "AAPL", "event_date": "2024-08-01", "event_time": "amc",
         "eps_actual": 1.40, "eps_estimate": 1.30, "surprise_pct": 7.69,
         "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"},
        {"ticker": "MSFT", "event_date": "2024-07-30", "event_time": "bmo",
         "eps_actual": 2.95, "eps_estimate": 2.93, "surprise_pct": 0.68,
         "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"},
        {"ticker": "TSLA", "event_date": "2024-07-23", "event_time": None,
         "eps_actual": 0.52, "eps_estimate": 0.62, "surprise_pct": -16.13,
         "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"},
    ])

    cands = real_pead_candidates(conn, universe=["AAPL", "MSFT"],
                                  surprise_threshold=5.0)
    # MSFT below threshold; TSLA outside universe.
    assert [c["ticker"] for c in cands] == ["AAPL"]
    aapl = cands[0]
    assert aapl["announced_hours_ago"] == 17  # amc
    assert 0.5 <= aapl["conviction"] <= 0.95
    assert aapl["surprise_pct"] == 7.69


def test_real_pead_candidates_announce_timing(conn):
    cache_earnings_events(conn, [
        {"ticker": "AAA", "event_date": "2024-01-02", "event_time": "bmo",
         "eps_actual": 1.0, "eps_estimate": 0.5, "surprise_pct": 100.0,
         "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"},
        {"ticker": "BBB", "event_date": "2024-01-03", "event_time": "amc",
         "eps_actual": 1.0, "eps_estimate": 0.5, "surprise_pct": 100.0,
         "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"},
        {"ticker": "CCC", "event_date": "2024-01-04", "event_time": None,
         "eps_actual": 1.0, "eps_estimate": 0.5, "surprise_pct": 100.0,
         "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"},
    ])
    cands = real_pead_candidates(conn, universe=["AAA", "BBB", "CCC"])
    by_ticker = {c["ticker"]: c for c in cands}
    assert by_ticker["AAA"]["announced_hours_ago"] == 6
    assert by_ticker["BBB"]["announced_hours_ago"] == 17
    assert by_ticker["CCC"]["announced_hours_ago"] == 12
    # Conviction is capped at 0.95 even for huge surprises.
    assert by_ticker["AAA"]["conviction"] == 0.95


def test_real_pead_candidates_skips_null_surprise(conn):
    cache_earnings_events(conn, [
        {"ticker": "AAPL", "event_date": "2024-08-01", "event_time": "amc",
         "eps_actual": None, "eps_estimate": None, "surprise_pct": None,
         "revenue_actual": None, "revenue_estimate": None, "source": "finnhub"},
    ])
    assert real_pead_candidates(conn, universe=["AAPL"]) == []


def test_fetch_finnhub_earnings_no_key_raises(monkeypatch):
    monkeypatch.setattr("heron.data.earnings.FINNHUB_API_KEY", "")
    with pytest.raises(RuntimeError, match="FINNHUB_API_KEY"):
        fetch_finnhub_earnings("2024-01-01", "2024-01-31")


def test_fetch_finnhub_earnings_normalizes(monkeypatch):
    payload = {
        "earningsCalendar": [
            {"symbol": "AAPL", "date": "2024-08-01", "hour": "AMC",
             "epsActual": 1.4, "epsEstimate": 1.3,
             "revenueActual": 100, "revenueEstimate": 95},
            {"symbol": "", "date": "2024-08-01"},  # filtered: empty ticker
            {"symbol": "GOOG", "date": "2024-07-23", "hour": "amc",
             "epsActual": None, "epsEstimate": None},
        ]
    }
    monkeypatch.setattr("heron.data.earnings.FINNHUB_API_KEY", "fake-key")
    monkeypatch.setattr("heron.data.earnings._http_get_json", lambda url, timeout=15: payload)

    events = fetch_finnhub_earnings("2024-07-01", "2024-08-31",
                                    universe=["AAPL", "GOOG"], api_key="fake-key")
    assert len(events) == 2
    aapl = next(e for e in events if e["ticker"] == "AAPL")
    assert aapl["event_time"] == "amc"  # lowercased
    assert aapl["surprise_pct"] == 7.69
    goog = next(e for e in events if e["ticker"] == "GOOG")
    assert goog["surprise_pct"] is None

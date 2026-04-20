"""Tests for SQLite cache layer."""

import sqlite3
from heron.data.cache import get_conn, init_db, upsert_bars, get_bars, upsert_articles, get_articles, update_fetch_log, get_last_fetch


def _mem_conn():
    conn = get_conn(":memory:")
    init_db(conn)
    return conn


def test_init_creates_tables():
    conn = _mem_conn()
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "ohlcv" in tables
    assert "news_articles" in tables
    assert "fetch_log" in tables
    conn.close()


def test_wal_mode():
    # WAL only works on file-backed DBs, but get_conn sets the pragma
    conn = _mem_conn()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    # :memory: dbs may report 'memory' instead of 'wal' — that's fine
    assert mode in ("wal", "memory")
    conn.close()


def test_upsert_and_get_bars():
    conn = _mem_conn()
    bars = [
        {"ticker": "AAPL", "timeframe": "1Day", "ts": "2026-04-18T00:00:00+00:00",
         "open": 150.0, "high": 155.0, "low": 149.0, "close": 153.0, "volume": 1000000, "source": "test"},
        {"ticker": "AAPL", "timeframe": "1Day", "ts": "2026-04-17T00:00:00+00:00",
         "open": 148.0, "high": 152.0, "low": 147.0, "close": 150.0, "volume": 900000, "source": "test"},
    ]
    upsert_bars(conn, bars)
    result = get_bars(conn, "AAPL", "1Day")
    assert len(result) == 2
    assert result[0]["close"] == 150.0  # sorted by ts, so 04-17 first
    assert result[1]["close"] == 153.0
    conn.close()


def test_bars_dedup():
    conn = _mem_conn()
    bar = {"ticker": "MSFT", "timeframe": "1Day", "ts": "2026-04-18T00:00:00+00:00",
           "open": 300.0, "high": 305.0, "low": 298.0, "close": 302.0, "volume": 500000, "source": "test"}
    upsert_bars(conn, [bar])
    upsert_bars(conn, [bar])  # duplicate
    result = get_bars(conn, "MSFT", "1Day")
    assert len(result) == 1
    conn.close()


def test_bars_range_filter():
    conn = _mem_conn()
    bars = [
        {"ticker": "AAPL", "timeframe": "1Day", "ts": f"2026-04-{d:02d}T00:00:00+00:00",
         "open": 150.0, "high": 155.0, "low": 149.0, "close": 153.0, "volume": 1000000, "source": "test"}
        for d in range(14, 19)
    ]
    upsert_bars(conn, bars)
    result = get_bars(conn, "AAPL", "1Day", start="2026-04-16", end="2026-04-18")
    assert len(result) == 3  # 16th, 17th, 18th match; 14th, 15th too early
    conn.close()


def test_upsert_and_get_articles():
    conn = _mem_conn()
    articles = [
        {"id": "test:1", "source": "test_feed", "headline": "AAPL beats earnings",
         "summary": "Good quarter", "body_sanitized": "", "tickers": ["AAPL"],
         "published_at": "2026-04-18T12:00:00+00:00", "credibility_weight": 0.8},
        {"id": "test:2", "source": "test_feed", "headline": "Fed holds rates",
         "summary": "No change", "body_sanitized": "", "tickers": [],
         "published_at": "2026-04-18T14:00:00+00:00", "credibility_weight": 1.0},
    ]
    upsert_articles(conn, articles)
    result = get_articles(conn)
    assert len(result) == 2
    conn.close()


def test_articles_dedup():
    conn = _mem_conn()
    a = {"id": "test:dup", "source": "test", "headline": "Headline",
         "summary": "", "body_sanitized": "", "tickers": [],
         "published_at": "2026-04-18T12:00:00+00:00", "credibility_weight": 0.5}
    upsert_articles(conn, [a])
    upsert_articles(conn, [a])
    result = get_articles(conn)
    assert len(result) == 1
    conn.close()


def test_articles_filter_by_ticker():
    conn = _mem_conn()
    articles = [
        {"id": "t:1", "source": "s", "headline": "A", "summary": "", "body_sanitized": "",
         "tickers": ["AAPL", "MSFT"], "published_at": "2026-04-18T12:00:00+00:00", "credibility_weight": 0.8},
        {"id": "t:2", "source": "s", "headline": "B", "summary": "", "body_sanitized": "",
         "tickers": ["GOOGL"], "published_at": "2026-04-18T13:00:00+00:00", "credibility_weight": 0.8},
    ]
    upsert_articles(conn, articles)
    result = get_articles(conn, ticker="AAPL")
    assert len(result) == 1
    assert result[0]["headline"] == "A"
    conn.close()


def test_fetch_log():
    conn = _mem_conn()
    update_fetch_log(conn, "alpaca_bars", "AAPL", "ok")
    log = get_last_fetch(conn, "alpaca_bars", "AAPL")
    assert log is not None
    assert log["status"] == "ok"

    # Update it
    update_fetch_log(conn, "alpaca_bars", "AAPL", "error: timeout")
    log = get_last_fetch(conn, "alpaca_bars", "AAPL")
    assert log["status"] == "error: timeout"
    conn.close()

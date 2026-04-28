"""SQLite cache for market data and news. WAL mode, immutable-after-write."""

import json
import sqlite3
from pathlib import Path

from heron.config import CACHE_DB, CACHE_DIR

_DDL = """
CREATE TABLE IF NOT EXISTS ohlcv (
    ticker     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    ts         TEXT    NOT NULL,   -- ISO-8601 UTC
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'alpaca',
    fetched_at TEXT    NOT NULL,
    PRIMARY KEY (ticker, timeframe, ts)
);

CREATE TABLE IF NOT EXISTS news_articles (
    id               TEXT PRIMARY KEY,   -- source:article_id
    source           TEXT NOT NULL,
    headline         TEXT NOT NULL,
    summary          TEXT,
    body_sanitized   TEXT,
    tickers          TEXT,               -- JSON array
    published_at     TEXT NOT NULL,       -- ISO-8601 UTC
    credibility_weight REAL NOT NULL DEFAULT 0.5,
    fetched_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    source         TEXT NOT NULL,
    ticker         TEXT,
    last_fetched   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'ok',
    PRIMARY KEY (source, ticker)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_tf ON ohlcv(ticker, timeframe);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles(published_at);
CREATE INDEX IF NOT EXISTS idx_news_source ON news_articles(source);

CREATE TABLE IF NOT EXISTS earnings_events (
    ticker         TEXT NOT NULL,
    event_date     TEXT NOT NULL,          -- YYYY-MM-DD (announce date)
    event_time     TEXT,                   -- 'bmo' | 'amc' | 'dmh' | NULL
    eps_actual     REAL,
    eps_estimate   REAL,
    surprise_pct   REAL,                   -- (actual - estimate) / |estimate| * 100
    revenue_actual REAL,
    revenue_estimate REAL,
    source         TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (ticker, event_date, source)
);

CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings_events(event_date);
CREATE INDEX IF NOT EXISTS idx_earnings_ticker ON earnings_events(ticker);

-- Operator-curated point-in-time universe snapshots. Used by the backtest
-- runner when `as_of` is set; falls back to PEAD_UNIVERSE if no snapshot.
CREATE TABLE IF NOT EXISTS universe_snapshots (
    snapshot_date  TEXT NOT NULL,          -- YYYY-MM-DD; universe is current at this date
    ticker         TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT 'manual',
    note           TEXT,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_universe_date ON universe_snapshots(snapshot_date);
"""

from heron.util import utc_now_iso as _now  # noqa: E402


def get_conn(db_path=None):
    p = db_path or CACHE_DB
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn=None):
    c = conn or get_conn()
    c.executescript(_DDL)
    _migrate_earnings_pit(c)
    c.commit()
    if conn is None:
        c.close()


def _migrate_earnings_pit(conn):
    """Idempotently add point-in-time columns to earnings_events.

    `as_of_ts`     — when this record's values became known (default = fetched_at).
    `superseded_at`— NULL = current; non-NULL = restated and replaced by a newer row.
    Backfills both columns on existing rows so older databases keep working.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(earnings_events)").fetchall()}
    if "as_of_ts" not in cols:
        conn.execute("ALTER TABLE earnings_events ADD COLUMN as_of_ts TEXT")
        conn.execute("UPDATE earnings_events SET as_of_ts = fetched_at WHERE as_of_ts IS NULL")
    if "superseded_at" not in cols:
        conn.execute("ALTER TABLE earnings_events ADD COLUMN superseded_at TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_earnings_pit "
                 "ON earnings_events(ticker, event_date, as_of_ts)")
    # The original PRIMARY KEY (ticker, event_date, source) prevents storing
    # multiple PIT versions of the same announcement. Drop it via the standard
    # SQLite recreate-and-copy pattern, but only once.
    pk_cols = [r for r in conn.execute("PRAGMA table_info(earnings_events)").fetchall() if r[5]]
    if len(pk_cols) == 3:  # original PK still in place
        conn.executescript("""
            CREATE TABLE earnings_events_new (
                ticker         TEXT NOT NULL,
                event_date     TEXT NOT NULL,
                event_time     TEXT,
                eps_actual     REAL,
                eps_estimate   REAL,
                surprise_pct   REAL,
                revenue_actual REAL,
                revenue_estimate REAL,
                source         TEXT NOT NULL,
                fetched_at     TEXT NOT NULL,
                as_of_ts       TEXT NOT NULL,
                superseded_at  TEXT,
                PRIMARY KEY (ticker, event_date, source, as_of_ts)
            );
            INSERT INTO earnings_events_new
              SELECT ticker, event_date, event_time, eps_actual, eps_estimate,
                     surprise_pct, revenue_actual, revenue_estimate, source,
                     fetched_at, COALESCE(as_of_ts, fetched_at), superseded_at
                FROM earnings_events;
            DROP TABLE earnings_events;
            ALTER TABLE earnings_events_new RENAME TO earnings_events;
            CREATE INDEX idx_earnings_date ON earnings_events(event_date);
            CREATE INDEX idx_earnings_ticker ON earnings_events(ticker);
            CREATE INDEX idx_earnings_pit ON earnings_events(ticker, event_date, as_of_ts);
        """)


# --- OHLCV ---

def upsert_bars(conn, bars):
    """Insert OHLCV bars. bars: list of dicts with keys matching the table columns."""
    now = _now()
    conn.executemany(
        """INSERT OR IGNORE INTO ohlcv
           (ticker, timeframe, ts, open, high, low, close, volume, source, fetched_at)
           VALUES (:ticker, :timeframe, :ts, :open, :high, :low, :close, :volume, :source, :fetched_at)""",
        [{**b, "fetched_at": b.get("fetched_at", now)} for b in bars],
    )
    conn.commit()


def get_bars(conn, ticker, timeframe, start=None, end=None):
    q = "SELECT * FROM ohlcv WHERE ticker=? AND timeframe=?"
    params = [ticker, timeframe]
    if start:
        q += " AND ts>=?"
        params.append(start)
    if end:
        # Bare date strings ("2026-04-18") sort before ISO timestamps ("2026-04-18T...")
        # so append end-of-day to include bars on the end date
        if len(end) == 10:
            end = end + "T23:59:59+99:99"
        q += " AND ts<=?"
        params.append(end)
    q += " ORDER BY ts"
    return conn.execute(q, params).fetchall()


# --- News ---

def upsert_articles(conn, articles):
    """Insert news articles. Dedup on id (source:article_id)."""
    now = _now()
    conn.executemany(
        """INSERT OR IGNORE INTO news_articles
           (id, source, headline, summary, body_sanitized, tickers, published_at, credibility_weight, fetched_at)
           VALUES (:id, :source, :headline, :summary, :body_sanitized, :tickers, :published_at, :credibility_weight, :fetched_at)""",
        [{**a, "tickers": json.dumps(a.get("tickers", [])), "fetched_at": a.get("fetched_at", now)} for a in articles],
    )
    conn.commit()


def get_articles(conn, start=None, end=None, source=None, ticker=None):
    q = "SELECT * FROM news_articles WHERE 1=1"
    params = []
    if start:
        q += " AND published_at>=?"
        params.append(start)
    if end:
        q += " AND published_at<=?"
        params.append(end)
    if source:
        q += " AND source=?"
        params.append(source)
    if ticker:
        q += " AND tickers LIKE ?"
        params.append(f'%"{ticker}"%')
    q += " ORDER BY published_at DESC"
    return conn.execute(q, params).fetchall()


# --- Fetch log ---

def update_fetch_log(conn, source, ticker=None, status="ok"):
    conn.execute(
        """INSERT INTO fetch_log (source, ticker, last_fetched, status)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(source, ticker) DO UPDATE SET last_fetched=excluded.last_fetched, status=excluded.status""",
        (source, ticker or "", _now(), status),
    )
    conn.commit()


def get_last_fetch(conn, source, ticker=None):
    row = conn.execute(
        "SELECT last_fetched, status FROM fetch_log WHERE source=? AND ticker=?",
        (source, ticker or ""),
    ).fetchone()
    return dict(row) if row else None

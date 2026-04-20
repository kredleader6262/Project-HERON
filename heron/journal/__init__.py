"""Journal database schema and initialization.

The journal is the product. Every decision at every layer writes here.
WAL mode, all timestamps UTC ISO-8601.
See Project-HERON.md Sections 3, 4.5, 5.4, 5.5, 6, 7, 11.
"""

import sqlite3
from pathlib import Path

from heron.config import CACHE_DB, CACHE_DIR

_JOURNAL_DDL = """
-- ============================================================
-- STRATEGIES — first-class objects of HERON
-- ============================================================
CREATE TABLE IF NOT EXISTS strategies (
    id              TEXT PRIMARY KEY,           -- slug: e.g. 'pead_v1'
    name            TEXT NOT NULL,
    description     TEXT,                       -- human-readable, written by proposing agent
    rationale       TEXT,                       -- why this strategy was proposed
    state           TEXT NOT NULL DEFAULT 'PROPOSED',  -- PROPOSED|PAPER|LIVE|RETIRED
    is_baseline     INTEGER NOT NULL DEFAULT 0, -- 1 = deterministic baseline variant
    parent_id       TEXT,                       -- links baseline to its LLM variant
    config          TEXT,                       -- JSON: entry/exit rules, sizing, universe, etc.

    -- Per-strategy risk limits (Section 5.2)
    max_capital_pct     REAL DEFAULT 0.15,      -- max % of equity for this strategy
    max_positions       INTEGER DEFAULT 3,
    drawdown_budget_pct REAL DEFAULT 0.05,      -- auto-retire if drawdown exceeds this
    min_conviction      REAL DEFAULT 0.0,
    min_hold_days       INTEGER DEFAULT 2,      -- PDT safety

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    retired_at      TEXT,
    retired_reason  TEXT,

    FOREIGN KEY (parent_id) REFERENCES strategies(id)
);

-- ============================================================
-- STATE HISTORY — every state transition logged
-- ============================================================
CREATE TABLE IF NOT EXISTS strategy_state_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT NOT NULL,
    from_state      TEXT,
    to_state        TEXT NOT NULL,
    reason          TEXT,
    operator        TEXT DEFAULT 'system',       -- 'operator' or 'system'
    ts              TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

-- ============================================================
-- CANDIDATES — trade candidates surfaced by Research layer
-- ============================================================
CREATE TABLE IF NOT EXISTS candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL DEFAULT 'buy', -- buy|sell
    source          TEXT,                        -- 'research_local', 'research_api', 'manual'

    -- Scores
    local_score     REAL,                        -- Qwen classification score
    api_score       REAL,                        -- Claude conviction score (if escalated)
    final_score     REAL,

    -- Disposition
    disposition     TEXT DEFAULT 'pending',       -- pending|accepted|rejected|expired
    rejection_reason TEXT,

    -- Context
    thesis          TEXT,                        -- LLM-written thesis
    context_json    TEXT,                        -- JSON: news refs, price context, etc.

    created_at      TEXT NOT NULL,
    disposed_at     TEXT,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

-- ============================================================
-- TRADES — every executed trade
-- ============================================================
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT NOT NULL,
    candidate_id    INTEGER,
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,                -- buy|sell
    mode            TEXT NOT NULL DEFAULT 'paper', -- paper|live

    -- Order
    client_order_id TEXT UNIQUE,                 -- {strategy}_{utc_ms}_{ticker}_{side}
    order_type      TEXT DEFAULT 'market',
    qty             REAL NOT NULL,
    limit_price     REAL,

    -- Fill
    fill_price      REAL,
    fill_qty        REAL,
    filled_at       TEXT,
    slippage_bps    REAL,                        -- actual vs requested

    -- Stops (virtual — polled, not bracket orders)
    stop_price      REAL,
    target_price    REAL,

    -- Close
    close_price     REAL,
    close_filled_at TEXT,
    close_reason    TEXT,                        -- stop|target|time_exit|manual|retirement
    pnl             REAL,                        -- realized P&L in dollars
    pnl_pct         REAL,                        -- realized P&L as % of entry

    -- Context
    thesis          TEXT,
    outcome_notes   TEXT,                        -- EOD debrief prose

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id),
    FOREIGN KEY (candidate_id) REFERENCES candidates(id)
);

-- ============================================================
-- WASH-SALE LOTS — closed losing lots for 30-day lookback
-- ============================================================
CREATE TABLE IF NOT EXISTS wash_sale_lots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL,
    ticker          TEXT NOT NULL,
    ticker_family   TEXT NOT NULL,               -- family key from config
    loss_amount     REAL NOT NULL,               -- negative = loss
    closed_at       TEXT NOT NULL,               -- when the losing lot was closed
    window_end      TEXT NOT NULL,               -- closed_at + 30 calendar days
    disallowed      INTEGER NOT NULL DEFAULT 0,  -- 1 if this loss was disallowed by a repurchase
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);

-- ============================================================
-- PDT DAY-TRADES — rolling 5 business day tracker
-- ============================================================
CREATE TABLE IF NOT EXISTS pdt_daytrades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL,
    ticker          TEXT NOT NULL,
    entry_date      TEXT NOT NULL,               -- date of entry (business day)
    exit_date       TEXT NOT NULL,               -- date of exit (same day = day trade)
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);

-- ============================================================
-- AUDITS — LLM audit records (Section 6)
-- ============================================================
CREATE TABLE IF NOT EXISTS audits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_type      TEXT NOT NULL,               -- baseline_comparison|cost_triggered|sampling|memorization
    strategy_id     TEXT,
    trade_id        INTEGER,
    candidate_id    INTEGER,

    local_output    TEXT,                        -- what the local model produced
    api_output      TEXT,                        -- what Claude produced
    actual_outcome  TEXT,                        -- what actually happened
    divergence      INTEGER DEFAULT 0,           -- 1 if local != api
    notes           TEXT,

    created_at      TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id),
    FOREIGN KEY (trade_id) REFERENCES trades(id),
    FOREIGN KEY (candidate_id) REFERENCES candidates(id)
);

-- ============================================================
-- COST TRACKING — per-day token usage, per-strategy attribution
-- ============================================================
CREATE TABLE IF NOT EXISTS cost_tracking (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,               -- YYYY-MM-DD
    model           TEXT NOT NULL,               -- 'qwen_local', 'claude_sonnet', 'claude_haiku'
    strategy_id     TEXT,                        -- NULL = system-level cost
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0.0,
    task            TEXT,                        -- 'classification', 'thesis', 'debrief', etc.
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

-- ============================================================
-- REVIEWS — monthly go/no-go (Section 11)
-- ============================================================
CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    review_month    TEXT NOT NULL UNIQUE,         -- YYYY-MM
    status          TEXT NOT NULL DEFAULT 'pending', -- pending|filed
    body            TEXT,                        -- operator's written review
    decision        TEXT,                        -- go|no-go
    filed_at        TEXT,
    created_at      TEXT NOT NULL
);

-- ============================================================
-- EVENTS — generic event log for anything not covered above
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,               -- reconciliation_drift, halt, alert, etc.
    severity        TEXT NOT NULL DEFAULT 'info', -- info|warn|error|critical
    source          TEXT,                        -- which layer/module
    message         TEXT NOT NULL,
    details_json    TEXT,
    created_at      TEXT NOT NULL
);

-- ============================================================
-- BACKTEST_REPORTS — results of deterministic strategy replays (Section 12)
-- ============================================================
CREATE TABLE IF NOT EXISTS backtest_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    params_json     TEXT NOT NULL,               -- strategy config snapshot
    seed            INTEGER NOT NULL,            -- for determinism
    n_trades        INTEGER NOT NULL DEFAULT 0,
    total_return    REAL NOT NULL DEFAULT 0.0,
    win_rate        REAL NOT NULL DEFAULT 0.0,
    sharpe          REAL,
    max_drawdown    REAL NOT NULL DEFAULT 0.0,
    avg_trade_pnl   REAL NOT NULL DEFAULT 0.0,
    metrics_json    TEXT NOT NULL,               -- full metric dump
    trades_json     TEXT NOT NULL,               -- list of simulated trades
    contaminated    INTEGER NOT NULL DEFAULT 0,  -- 1 if window overlaps LLM cutoff
    contamination_notes TEXT,
    created_at      TEXT NOT NULL
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_strategies_state ON strategies(state);
CREATE INDEX IF NOT EXISTS idx_candidates_strategy ON candidates(strategy_id);
CREATE INDEX IF NOT EXISTS idx_candidates_disposition ON candidates(disposition);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_id);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_mode ON trades(mode);
CREATE INDEX IF NOT EXISTS idx_trades_client_order ON trades(client_order_id);
CREATE INDEX IF NOT EXISTS idx_wash_sale_family ON wash_sale_lots(ticker_family, window_end);
CREATE INDEX IF NOT EXISTS idx_wash_sale_window ON wash_sale_lots(window_end);
CREATE INDEX IF NOT EXISTS idx_pdt_exit_date ON pdt_daytrades(exit_date);
CREATE INDEX IF NOT EXISTS idx_cost_date ON cost_tracking(date);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_backtest_strategy ON backtest_reports(strategy_id, created_at);
"""


def get_journal_conn(db_path=None):
    """Get a connection to the journal database. Shared with data cache."""
    p = db_path or CACHE_DB
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_journal(conn=None):
    """Create all journal tables. Safe to call repeatedly."""
    c = conn or get_journal_conn()
    c.executescript(_JOURNAL_DDL)
    c.commit()
    if conn is None:
        c.close()

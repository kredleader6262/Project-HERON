"""Journal write/read API for trades, wash-sale lots, PDT tracking."""

import json
from datetime import datetime, timezone, timedelta

from heron.config import TICKER_FAMILIES
from heron.util import utc_now_iso as _now, utc_today as _today


def _ticker_family(ticker):
    """Return the family key for a ticker (from config). Defaults to ticker itself."""
    for family_name, members in TICKER_FAMILIES.items():
        if ticker in members:
            return family_name
    return ticker


# ── Trades ──────────────────────────────────────────────

def create_trade(conn, strategy_id, ticker, side, mode, qty,
                 client_order_id=None, order_type="market", limit_price=None,
                 stop_price=None, target_price=None, candidate_id=None, thesis=None):
    """Record a new trade entry."""
    now = _now()
    cur = conn.execute(
        """INSERT INTO trades
           (strategy_id, candidate_id, ticker, side, mode,
            client_order_id, order_type, qty, limit_price,
            stop_price, target_price, thesis, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?, ?, ?)""",
        (strategy_id, candidate_id, ticker, side, mode,
         client_order_id, order_type, qty, limit_price,
         stop_price, target_price, thesis, now, now),
    )
    conn.commit()
    return cur.lastrowid


def fill_trade(conn, trade_id, fill_price, fill_qty=None, slippage_bps=None):
    """Record a fill on a trade."""
    now = _now()
    conn.execute(
        "UPDATE trades SET fill_price=?, fill_qty=COALESCE(?, qty), filled_at=?, slippage_bps=?, updated_at=? WHERE id=?",
        (fill_price, fill_qty, now, slippage_bps, now, trade_id),
    )
    conn.commit()


def close_trade(conn, trade_id, close_price, close_reason, outcome_notes=None):
    """Close a trade. Auto-creates wash-sale lot if loss. Auto-records PDT day-trade if same-day."""
    trade = get_trade(conn, trade_id)
    if not trade:
        raise ValueError(f"Trade {trade_id} not found")
    if not trade["fill_price"]:
        raise ValueError(f"Trade {trade_id} has no fill — can't close")

    pnl = (close_price - trade["fill_price"]) * trade["fill_qty"]
    if trade["side"] == "sell":
        pnl = -pnl
    pnl_pct = pnl / (trade["fill_price"] * trade["fill_qty"]) if trade["fill_price"] else 0

    now = _now()
    conn.execute(
        """UPDATE trades SET close_price=?, close_filled_at=?, close_reason=?,
           pnl=?, pnl_pct=?, outcome_notes=?, updated_at=?
           WHERE id=?""",
        (close_price, now, close_reason, pnl, pnl_pct, outcome_notes, now, trade_id),
    )

    # Wash-sale lot on losses
    if pnl < 0:
        closed_at = now
        window_end = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        family = _ticker_family(trade["ticker"])
        conn.execute(
            """INSERT INTO wash_sale_lots
               (trade_id, ticker, ticker_family, loss_amount, closed_at, window_end)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (trade_id, trade["ticker"], family, pnl, closed_at, window_end),
        )

    # PDT day-trade check: entry and exit on same calendar day
    if trade["filled_at"]:
        entry_date = trade["filled_at"][:10]
        exit_date = now[:10]
        if entry_date == exit_date:
            conn.execute(
                "INSERT INTO pdt_daytrades (trade_id, ticker, entry_date, exit_date) VALUES (?, ?, ?, ?)",
                (trade_id, trade["ticker"], entry_date, exit_date),
            )

    conn.commit()
    return get_trade(conn, trade_id)


def get_trade(conn, trade_id):
    return conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()


def list_trades(conn, strategy_id=None, ticker=None, mode=None, open_only=False):
    """List trades with optional filters."""
    clauses, params = [], []
    if strategy_id:
        clauses.append("strategy_id=?"); params.append(strategy_id)
    if ticker:
        clauses.append("ticker=?"); params.append(ticker)
    if mode:
        clauses.append("mode=?"); params.append(mode)
    if open_only:
        clauses.append("close_price IS NULL")
    where = " AND ".join(clauses)
    sql = f"SELECT * FROM trades{' WHERE ' + where if where else ''} ORDER BY created_at DESC"
    return conn.execute(sql, params).fetchall()


# ── Wash-Sale Queries ──────────────────────────────────

def check_wash_sale(conn, ticker):
    """Check if buying this ticker (or family member) would trigger wash-sale.
    Returns list of active wash-sale lots in the 30-day window.
    """
    family = _ticker_family(ticker)
    now = _now()
    return conn.execute(
        "SELECT * FROM wash_sale_lots WHERE ticker_family=? AND window_end > ? ORDER BY closed_at DESC",
        (family, now),
    ).fetchall()


def get_wash_sale_exposure(conn, mode=None):
    """All active wash-sale lots (window still open).

    `mode` optionally filters to a trade mode ('paper' or 'live'). Wash-sale is
    a tax rule that only matters for real money, so callers typically pass 'live'
    (or nothing in an 'all' view).
    """
    now = _now()
    if mode:
        return conn.execute(
            "SELECT w.* FROM wash_sale_lots w JOIN trades t ON t.id = w.trade_id "
            "WHERE w.window_end > ? AND t.mode = ? ORDER BY w.closed_at DESC",
            (now, mode),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM wash_sale_lots WHERE window_end > ? ORDER BY closed_at DESC",
        (now,),
    ).fetchall()


# ── PDT Queries ──────────────────────────────────────

def get_pdt_count(conn, lookback_days=5, mode=None):
    """Count day-trades in the rolling lookback window.
    Default 5 business days ≈ 7 calendar days for safety.

    `mode` optionally filters to a trade mode. PDT is a broker regulation that
    only applies to live accounts; paper day-trades are not PDT-counted.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days + 2)).strftime("%Y-%m-%d")
    if mode:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM pdt_daytrades p JOIN trades t ON t.id = p.trade_id "
            "WHERE p.exit_date >= ? AND t.mode = ?",
            (cutoff, mode),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM pdt_daytrades WHERE exit_date >= ?",
            (cutoff,),
        ).fetchone()
    return row["cnt"]


def can_daytrade(conn, limit=3):
    """True if another day-trade is allowed under PDT limit."""
    return get_pdt_count(conn) < limit

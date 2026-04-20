"""Backtest report persistence + memorization-contamination flag."""

import json

from heron.config import LOCAL_MODEL_KNOWLEDGE_CUTOFF, CLAUDE_KNOWLEDGE_CUTOFF
from heron.util import utc_now_iso


def check_contamination(start_date, end_date):
    """Return (is_contaminated, notes). True if window overlaps an LLM cutoff."""
    cutoffs = {
        "local": LOCAL_MODEL_KNOWLEDGE_CUTOFF,
        "claude": CLAUDE_KNOWLEDGE_CUTOFF,
    }
    overlaps = [name for name, cutoff in cutoffs.items()
                if cutoff and start_date <= cutoff]
    if not overlaps:
        return False, None
    parts = [f"{n} cutoff {cutoffs[n]}" for n in overlaps]
    return True, (
        f"Backtest window [{start_date} → {end_date}] overlaps LLM training data "
        f"({', '.join(parts)}). Treat results as reference only, not out-of-sample evidence."
    )


def save_report(conn, result):
    """Persist a backtest result from engine.run_backtest()."""
    m = result["metrics"]
    contaminated, notes = check_contamination(result["start_date"], result["end_date"])
    cur = conn.execute(
        """INSERT INTO backtest_reports
           (strategy_id, start_date, end_date, params_json, seed,
            n_trades, total_return, win_rate, sharpe, max_drawdown, avg_trade_pnl,
            metrics_json, trades_json, contaminated, contamination_notes, created_at)
           VALUES (?, ?, ?, ?, ?,  ?, ?, ?, ?, ?, ?,  ?, ?, ?, ?, ?)""",
        (
            result["strategy_id"], result["start_date"], result["end_date"],
            json.dumps(result["params"], default=str), result["seed"],
            m["n_trades"], m["total_return"], m["win_rate"], m["sharpe"],
            m["max_drawdown"], m["avg_trade_pnl"],
            json.dumps(m), json.dumps(result["trades"]),
            1 if contaminated else 0, notes, utc_now_iso(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_reports(conn, strategy_id=None, limit=50):
    q = "SELECT * FROM backtest_reports"
    params = []
    if strategy_id:
        q += " WHERE strategy_id=?"
        params.append(strategy_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(q, params).fetchall()


def get_report(conn, report_id):
    return conn.execute(
        "SELECT * FROM backtest_reports WHERE id=?", (report_id,)
    ).fetchone()


def latest_for_strategy(conn, strategy_id):
    return conn.execute(
        "SELECT * FROM backtest_reports WHERE strategy_id=? "
        "ORDER BY created_at DESC LIMIT 1",
        (strategy_id,),
    ).fetchone()

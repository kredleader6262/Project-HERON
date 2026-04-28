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
    """Persist a backtest result from engine.run_backtest().

    Enriches `metrics_json` with two derived blocks (best-effort, swallowed on error):
      - parity: paired-bootstrap verdict vs the matching baseline report.
      - regime_breakdown: per-vol-bucket trade metrics (using cached SPY bars).
    Both fields are absent when source data isn't available; callers should
    treat them as optional.
    """
    m = dict(result["metrics"])
    # Fold equity_curve into metrics_json so detail views can plot without
    # an extra column. Cheap (one float per trading day).
    m["equity_curve"] = result.get("equity_curve", [])

    # Parity vs baseline (best-effort).
    try:
        from heron.backtest.runner import find_baseline_report
        from heron.backtest.parity import compute_parity_report
        baseline = find_baseline_report(
            conn, result["strategy_id"], result["start_date"], result["end_date"],
        )
        if baseline is not None:
            try:
                bm = json.loads(baseline["metrics_json"])
            except (TypeError, json.JSONDecodeError):
                bm = {}
            m["parity"] = compute_parity_report(
                m.get("equity_curve") or [],
                bm.get("equity_curve") or [],
                baseline_report_id=baseline["id"],
            )
    except Exception as e:  # noqa: BLE001  — best-effort; never block save.
        m["parity"] = {"available": False, "reason": f"compute failed: {e}"}

    # Regime breakdown (best-effort).
    try:
        trades = result.get("trades") or []
        if trades:
            from heron.data.cache import get_bars
            from heron.backtest.regimes import vol_buckets_from_spy, tag_trades, regime_metrics
            spy_bars = get_bars(conn, "SPY", "1Day",
                                start=result["start_date"], end=result["end_date"])
            buckets = vol_buckets_from_spy(spy_bars) if spy_bars else {}
            tagged = tag_trades(trades, buckets)
            m["regime_breakdown"] = regime_metrics(tagged)
    except Exception as e:  # noqa: BLE001
        m["regime_breakdown"] = {"available": False, "reason": f"compute failed: {e}"}

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


def reparity_report(conn, report_id):
    """Recompute parity + regime_breakdown for an existing report and save in place.

    Returns the updated metrics dict. Useful for backfilling reports created
    before parity was persisted, or after a baseline backtest is added.
    """
    row = conn.execute("SELECT * FROM backtest_reports WHERE id=?", (report_id,)).fetchone()
    if row is None:
        raise ValueError(f"report {report_id} not found")
    try:
        m = json.loads(row["metrics_json"])
    except (TypeError, json.JSONDecodeError):
        m = {}
    try:
        trades = json.loads(row["trades_json"])
    except (TypeError, json.JSONDecodeError):
        trades = []

    from heron.backtest.runner import find_baseline_report
    from heron.backtest.parity import compute_parity_report
    baseline = find_baseline_report(conn, row["strategy_id"], row["start_date"], row["end_date"])
    if baseline is not None:
        try:
            bm = json.loads(baseline["metrics_json"])
        except (TypeError, json.JSONDecodeError):
            bm = {}
        m["parity"] = compute_parity_report(
            m.get("equity_curve") or [],
            bm.get("equity_curve") or [],
            baseline_report_id=baseline["id"],
        )
    else:
        m["parity"] = {"available": False, "reason": "no matching baseline report"}

    if trades:
        try:
            from heron.data.cache import get_bars
            from heron.backtest.regimes import vol_buckets_from_spy, tag_trades, regime_metrics
            spy_bars = get_bars(conn, "SPY", "1Day",
                                start=row["start_date"], end=row["end_date"])
            buckets = vol_buckets_from_spy(spy_bars) if spy_bars else {}
            tagged = tag_trades(trades, buckets)
            m["regime_breakdown"] = regime_metrics(tagged)
        except Exception as e:  # noqa: BLE001
            m["regime_breakdown"] = {"available": False, "reason": f"compute failed: {e}"}

    conn.execute(
        "UPDATE backtest_reports SET metrics_json=? WHERE id=?",
        (json.dumps(m), report_id),
    )
    conn.commit()
    return m


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

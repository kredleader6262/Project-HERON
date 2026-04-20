"""Startup audit (M15) — run every launch before accepting work.

Reconciles broker vs journal, verifies protective stops, logs outcome.
Refuses to signal readiness if drift is found until operator resolves.
"""

import json
import logging
from datetime import datetime, timezone

from heron.journal.ops import log_event
from heron.journal.trades import list_trades

log = logging.getLogger(__name__)


def run_startup_audit(conn, broker=None):
    """Run startup audit. Returns {status, issues, checks, timestamp}.

    status: "clean" | "drift" | "error"
    issues: list of human-readable strings
    checks: dict of individual check results
    """
    started = datetime.now(timezone.utc).isoformat()
    issues = []
    checks = {}

    # 1. Broker <-> journal reconciliation
    try:
        if broker is None:
            checks["reconciliation"] = {"status": "skipped", "reason": "no broker"}
        else:
            drift = _reconcile(conn, broker)
            checks["reconciliation"] = {
                "status": "clean" if not drift else "drift",
                "discrepancies": drift,
            }
            for d in drift:
                issues.append(f"reconciliation: {d['message']}")
    except Exception as e:
        checks["reconciliation"] = {"status": "error", "error": str(e)}
        issues.append(f"reconciliation failed: {e}")

    # 2. Every open position has a protective stop
    try:
        unprotected = _check_stops(conn)
        checks["stops"] = {
            "status": "clean" if not unprotected else "unprotected",
            "unprotected": unprotected,
        }
        for t in unprotected:
            issues.append(f"unprotected open trade: {t['ticker']} trade#{t['id']}")
    except Exception as e:
        checks["stops"] = {"status": "error", "error": str(e)}
        issues.append(f"stop check failed: {e}")

    # 3. Resume hook — count pending work the journal knows about
    try:
        pending = _pending_work(conn)
        checks["pending_work"] = pending
    except Exception as e:
        checks["pending_work"] = {"error": str(e)}

    status = "clean" if not issues else "drift"
    finished = datetime.now(timezone.utc).isoformat()
    result = {
        "status": status,
        "issues": issues,
        "checks": checks,
        "started_at": started,
        "finished_at": finished,
    }

    severity = "info" if status == "clean" else "error"
    log_event(
        conn, "startup_audit",
        f"Startup audit {status}: {len(issues)} issue(s)",
        severity=severity, source="resilience",
        details_json=json.dumps(result),
    )
    log.info(f"Startup audit: {status} ({len(issues)} issues)")
    return result


def _reconcile(conn, broker):
    """Compare broker positions vs journal open trades. Returns list of drift entries."""
    broker_positions = {p["ticker"]: p for p in broker.get_positions()}
    journal_open = list_trades(conn, open_only=True)

    journal_tickers = {}
    for t in journal_open:
        if t["fill_price"]:
            journal_tickers.setdefault(t["ticker"], []).append(t)

    drift = []
    for ticker, pos in broker_positions.items():
        if ticker not in journal_tickers:
            drift.append({
                "type": "broker_only", "ticker": ticker,
                "broker_qty": pos["qty"], "journal_qty": 0,
                "message": f"{ticker}: in broker but not journal",
            })
    for ticker, trades in journal_tickers.items():
        jqty = sum(t["fill_qty"] or 0 for t in trades)
        bqty = broker_positions.get(ticker, {}).get("qty", 0)
        if abs(jqty - bqty) > 0.001:
            drift.append({
                "type": "qty_mismatch", "ticker": ticker,
                "broker_qty": bqty, "journal_qty": jqty,
                "message": f"{ticker}: journal={jqty} broker={bqty}",
            })
    return drift


def _check_stops(conn):
    """Every filled open trade must have a stop_price set."""
    open_trades = list_trades(conn, open_only=True)
    unprotected = []
    for t in open_trades:
        if t["fill_price"] is None:
            continue  # not yet filled, no stop required
        if t["stop_price"] is None:
            unprotected.append({"id": t["id"], "ticker": t["ticker"]})
    return unprotected


def _pending_work(conn):
    """Count in-flight work the journal knows about (for resume visibility)."""
    row = conn.execute("""
        SELECT
          (SELECT COUNT(*) FROM trades WHERE close_price IS NULL) AS open_trades,
          (SELECT COUNT(*) FROM candidates WHERE disposition='pending') AS pending_candidates,
          (SELECT COUNT(*) FROM strategies WHERE state='PROPOSED') AS proposed_strategies
    """).fetchone()
    return dict(row) if row else {}

"""Journal write/read API for cost tracking, audits, reviews, events."""

from datetime import datetime, timezone

from heron.util import utc_now_iso as _now


# ── Cost Tracking ──────────────────────────────────────

def log_cost(conn, model, tokens_in, tokens_out, cost_usd,
             strategy_id=None, task=None, date=None):
    """Record token usage and cost."""
    conn.execute(
        """INSERT INTO cost_tracking (date, model, strategy_id, tokens_in, tokens_out, cost_usd, task)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
         model, strategy_id, tokens_in, tokens_out, cost_usd, task),
    )
    conn.commit()


def get_monthly_cost(conn, year_month=None):
    """Total cost for a given YYYY-MM. Defaults to current month."""
    ym = year_month or datetime.now(timezone.utc).strftime("%Y-%m")
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) as total FROM cost_tracking WHERE date LIKE ?",
        (f"{ym}%",),
    ).fetchone()
    return row["total"]


def get_daily_costs(conn, date=None):
    """Cost breakdown by model for a given date."""
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return conn.execute(
        "SELECT model, SUM(tokens_in) as tokens_in, SUM(tokens_out) as tokens_out, SUM(cost_usd) as cost FROM cost_tracking WHERE date=? GROUP BY model",
        (d,),
    ).fetchall()


# ── Audits ──────────────────────────────────────────────

def log_audit(conn, audit_type, strategy_id=None, trade_id=None, candidate_id=None,
              local_output=None, api_output=None, actual_outcome=None, divergence=False, notes=None):
    """Record an LLM audit entry."""
    conn.execute(
        """INSERT INTO audits
           (audit_type, strategy_id, trade_id, candidate_id,
            local_output, api_output, actual_outcome, divergence, notes, created_at)
           VALUES (?, ?, ?, ?,  ?, ?, ?, ?, ?, ?)""",
        (audit_type, strategy_id, trade_id, candidate_id,
         local_output, api_output, actual_outcome, 1 if divergence else 0, notes, _now()),
    )
    conn.commit()


def get_audits(conn, audit_type=None, limit=50):
    """List recent audits."""
    if audit_type:
        return conn.execute(
            "SELECT * FROM audits WHERE audit_type=? ORDER BY created_at DESC LIMIT ?",
            (audit_type, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM audits ORDER BY created_at DESC LIMIT ?", (limit,),
    ).fetchall()


# ── Reviews ──────────────────────────────────────────────

def create_review(conn, review_month=None):
    """Create a pending monthly review."""
    ym = review_month or datetime.now(timezone.utc).strftime("%Y-%m")
    conn.execute(
        "INSERT OR IGNORE INTO reviews (review_month, created_at) VALUES (?, ?)",
        (ym, _now()),
    )
    conn.commit()


def file_review(conn, review_month, body, decision):
    """File a monthly review with go/no-go decision."""
    if decision not in ("go", "no-go"):
        raise ValueError(f"Decision must be 'go' or 'no-go', got {decision!r}")
    conn.execute(
        "UPDATE reviews SET status='filed', body=?, decision=?, filed_at=? WHERE review_month=?",
        (body, decision, _now(), review_month),
    )
    conn.commit()


def get_review(conn, review_month):
    return conn.execute("SELECT * FROM reviews WHERE review_month=?", (review_month,)).fetchone()


def is_review_current(conn):
    """True if this month's review is filed. Blocks new promotions if not."""
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    row = get_review(conn, ym)
    return bool(row and row["status"] == "filed")


# ── Events ──────────────────────────────────────────────

def log_event(conn, event_type, message, severity="info", source=None, details_json=None):
    """Log a generic event."""
    conn.execute(
        "INSERT INTO events (event_type, severity, source, message, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (event_type, severity, source, message, details_json, _now()),
    )
    conn.commit()


def get_events(conn, event_type=None, severity=None, limit=100):
    """List recent events with optional filters."""
    clauses, params = [], []
    if event_type:
        clauses.append("event_type=?"); params.append(event_type)
    if severity:
        clauses.append("severity=?"); params.append(severity)
    where = " AND ".join(clauses)
    sql = f"SELECT * FROM events{' WHERE ' + where if where else ''} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()

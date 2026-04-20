"""Journal write/read API for candidates."""

from heron.util import utc_now_iso as _now


def create_candidate(conn, strategy_id, ticker, side="buy", source=None,
                     local_score=None, api_score=None, final_score=None,
                     thesis=None, context_json=None):
    """Insert a new candidate in pending disposition."""
    now = _now()
    cur = conn.execute(
        """INSERT INTO candidates
           (strategy_id, ticker, side, source,
            local_score, api_score, final_score,
            thesis, context_json, created_at)
           VALUES (?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?)""",
        (strategy_id, ticker, side, source,
         local_score, api_score, final_score,
         thesis, context_json, now),
    )
    conn.commit()
    return cur.lastrowid


def dispose_candidate(conn, candidate_id, disposition, rejection_reason=None):
    """Accept, reject, or expire a candidate."""
    if disposition not in ("accepted", "rejected", "expired"):
        raise ValueError(f"Invalid disposition: {disposition!r}")
    conn.execute(
        "UPDATE candidates SET disposition=?, rejection_reason=?, disposed_at=? WHERE id=?",
        (disposition, rejection_reason, _now(), candidate_id),
    )
    conn.commit()


def get_candidate(conn, candidate_id):
    return conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()


def list_candidates(conn, strategy_id=None, disposition=None, ticker=None):
    """List candidates with optional filters."""
    clauses, params = [], []
    if strategy_id:
        clauses.append("strategy_id=?"); params.append(strategy_id)
    if disposition:
        clauses.append("disposition=?"); params.append(disposition)
    if ticker:
        clauses.append("ticker=?"); params.append(ticker)
    where = " AND ".join(clauses)
    sql = f"SELECT * FROM candidates{' WHERE ' + where if where else ''} ORDER BY created_at DESC"
    return conn.execute(sql, params).fetchall()

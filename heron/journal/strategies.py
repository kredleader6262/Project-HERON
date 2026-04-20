"""Journal write API for strategies.

Every state transition is logged. The journal is the single source of truth.
"""

import json

from heron.util import utc_now_iso as _now


VALID_STATES = ("PROPOSED", "PAPER", "LIVE", "RETIRED")
VALID_TRANSITIONS = {
    "PROPOSED": ("PAPER", "RETIRED"),
    "PAPER": ("LIVE", "RETIRED"),
    "LIVE": ("RETIRED",),
    "RETIRED": ("PROPOSED",),  # reversible with operator action
}


def create_strategy(conn, id, name, description="", rationale="", config=None,
                    is_baseline=False, parent_id=None, **limits):
    """Insert a new strategy in PROPOSED state."""
    now = _now()
    conn.execute(
        """INSERT INTO strategies
           (id, name, description, rationale, state, is_baseline, parent_id, config,
            max_capital_pct, max_positions, drawdown_budget_pct, min_conviction, min_hold_days,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, 'PROPOSED', ?, ?, ?,  ?, ?, ?, ?, ?,  ?, ?)""",
        (id, name, description, rationale,
         1 if is_baseline else 0, parent_id,
         json.dumps(config) if config else None,
         limits.get("max_capital_pct", 0.15),
         limits.get("max_positions", 3),
         limits.get("drawdown_budget_pct", 0.05),
         limits.get("min_conviction", 0.0),
         limits.get("min_hold_days", 2),
         now, now),
    )
    # Log the initial state
    conn.execute(
        "INSERT INTO strategy_state_log (strategy_id, from_state, to_state, reason, operator, ts) VALUES (?, NULL, 'PROPOSED', 'created', 'system', ?)",
        (id, now),
    )
    conn.commit()
    return get_strategy(conn, id)


def get_strategy(conn, id):
    """Get a strategy by ID. Returns Row or None."""
    return conn.execute("SELECT * FROM strategies WHERE id=?", (id,)).fetchone()


def list_strategies(conn, state=None):
    """List strategies, optionally filtered by state."""
    if state:
        return conn.execute("SELECT * FROM strategies WHERE state=? ORDER BY created_at", (state,)).fetchall()
    return conn.execute("SELECT * FROM strategies ORDER BY created_at").fetchall()


def transition_strategy(conn, id, to_state, reason="", operator="system"):
    """Move a strategy to a new state. Validates the transition."""
    row = get_strategy(conn, id)
    if not row:
        raise ValueError(f"Strategy {id!r} not found")

    from_state = row["state"]
    if to_state not in VALID_STATES:
        raise ValueError(f"Invalid state {to_state!r}. Valid: {VALID_STATES}")
    if to_state not in VALID_TRANSITIONS.get(from_state, ()):
        raise ValueError(f"Cannot transition {from_state} → {to_state}. "
                         f"Valid from {from_state}: {VALID_TRANSITIONS[from_state]}")

    now = _now()
    updates = {"state": to_state, "updated_at": now}
    if to_state == "RETIRED":
        updates["retired_at"] = now
        updates["retired_reason"] = reason

    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE strategies SET {set_clause} WHERE id=?",
                 [*updates.values(), id])
    conn.execute(
        "INSERT INTO strategy_state_log (strategy_id, from_state, to_state, reason, operator, ts) VALUES (?, ?, ?, ?, ?, ?)",
        (id, from_state, to_state, reason, operator, now),
    )
    conn.commit()
    return get_strategy(conn, id)


def get_state_history(conn, strategy_id):
    """Get full state transition history for a strategy."""
    return conn.execute(
        "SELECT * FROM strategy_state_log WHERE strategy_id=? ORDER BY ts",
        (strategy_id,),
    ).fetchall()

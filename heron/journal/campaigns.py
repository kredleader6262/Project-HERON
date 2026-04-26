"""Journal write API for campaigns.

A campaign groups one or more strategies that run together as one experiment.
It owns the paper-window clock, capital allocation, and graduation lineage.
See Project-HERON.md §3.
"""

from heron.util import utc_now_iso as _now


VALID_STATES = ("DRAFT", "ACTIVE", "PAUSED", "GRADUATED", "RETIRED")
VALID_TRANSITIONS = {
    "DRAFT":     ("ACTIVE", "RETIRED"),
    "ACTIVE":    ("PAUSED", "GRADUATED", "RETIRED"),
    "PAUSED":    ("ACTIVE", "RETIRED"),
    "GRADUATED": ("RETIRED",),
    "RETIRED":   ("DRAFT",),
}


def create_campaign(conn, id, name, *, description="", mode="paper",
                    capital_allocation_usd=500.0, paper_window_days=90,
                    parent_campaign_id=None, state="DRAFT"):
    """Insert a new campaign. Defaults to DRAFT; transition to ACTIVE to start the clock."""
    if mode not in ("paper", "live"):
        raise ValueError(f"Invalid mode {mode!r}")
    if state not in VALID_STATES:
        raise ValueError(f"Invalid state {state!r}")

    now = _now()
    started_at = now if state == "ACTIVE" else None
    conn.execute(
        """INSERT INTO campaigns
              (id, name, description, mode, state,
               capital_allocation_usd, paper_window_days, parent_campaign_id,
               started_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?)""",
        (id, name, description, mode, state,
         capital_allocation_usd, paper_window_days, parent_campaign_id,
         started_at, now, now),
    )
    conn.execute(
        """INSERT INTO campaign_state_log (campaign_id, from_state, to_state, reason, operator, ts)
           VALUES (?, NULL, ?, 'created', 'system', ?)""",
        (id, state, now),
    )
    conn.commit()
    return get_campaign(conn, id)


def get_campaign(conn, id):
    return conn.execute("SELECT * FROM campaigns WHERE id=?", (id,)).fetchone()


def list_campaigns(conn, *, mode=None, state=None):
    clauses, params = [], []
    if mode:
        clauses.append("mode=?")
        params.append(mode)
    if state:
        clauses.append("state=?")
        params.append(state)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute(
        f"SELECT * FROM campaigns{where} ORDER BY created_at DESC", params
    ).fetchall()


def transition_campaign(conn, id, to_state, *, reason="", operator="system"):
    row = get_campaign(conn, id)
    if not row:
        raise ValueError(f"Campaign {id!r} not found")

    from_state = row["state"]
    if to_state not in VALID_STATES:
        raise ValueError(f"Invalid state {to_state!r}")
    if to_state not in VALID_TRANSITIONS.get(from_state, ()):
        raise ValueError(f"Cannot transition {from_state} → {to_state}. "
                         f"Valid from {from_state}: {VALID_TRANSITIONS[from_state]}")

    now = _now()
    updates = {"state": to_state, "updated_at": now}
    if to_state == "ACTIVE" and not row["started_at"]:
        updates["started_at"] = now
    if to_state == "GRADUATED":
        updates["graduated_at"] = now
    if to_state == "RETIRED":
        updates["retired_at"] = now
        updates["retired_reason"] = reason

    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE campaigns SET {set_clause} WHERE id=?",
                 [*updates.values(), id])
    conn.execute(
        """INSERT INTO campaign_state_log (campaign_id, from_state, to_state, reason, operator, ts)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, from_state, to_state, reason, operator, now),
    )
    conn.commit()
    return get_campaign(conn, id)


def attach_strategy(conn, campaign_id, strategy_id):
    """Attach an existing strategy to a campaign."""
    if not get_campaign(conn, campaign_id):
        raise ValueError(f"Campaign {campaign_id!r} not found")
    cur = conn.execute(
        "UPDATE strategies SET campaign_id=?, updated_at=? WHERE id=?",
        (campaign_id, _now(), strategy_id),
    )
    if cur.rowcount == 0:
        raise ValueError(f"Strategy {strategy_id!r} not found")
    conn.commit()


def get_campaign_strategies(conn, campaign_id):
    return conn.execute(
        "SELECT * FROM strategies WHERE campaign_id=? ORDER BY is_baseline, created_at",
        (campaign_id,),
    ).fetchall()


def get_state_history(conn, campaign_id):
    return conn.execute(
        "SELECT * FROM campaign_state_log WHERE campaign_id=? ORDER BY ts",
        (campaign_id,),
    ).fetchall()


def days_active(conn, campaign_id):
    """Days since `started_at` (NY trading-calendar-agnostic — wall calendar).

    Returns None if not yet started. The 90-day window in §1.3 is market days,
    but for UI progress we use calendar days; the bootstrap beat-test reads
    actual paired returns from `trades` and ignores this field.
    """
    from datetime import datetime, timezone
    row = get_campaign(conn, campaign_id)
    if not row or not row["started_at"]:
        return None
    started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).days

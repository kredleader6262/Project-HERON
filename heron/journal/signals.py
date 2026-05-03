"""Journal API for first-class Signals and Candidate bridges."""

from datetime import datetime
import json

from heron.journal.campaigns import get_campaign
from heron.journal.candidates import get_candidate
from heron.journal.strategies import get_strategy
from heron.util import utc_now_iso as _now


VALID_BIASES = ("long_bias", "short_bias", "informational", "risk-off")


def create_signal(conn, campaign_id, source, signal_type, bias, thesis, *,
                  finding_ref_json=None, producing_agent=None, producing_model=None,
                  ticker=None, sector=None, asset=None, confidence=None,
                  classification=None, evidence_json=None, generated_at=None,
                  expires_at=None, resolution_status="open", outcome_json=None,
                  baseline_json=None):
    """Create a Signal row and return its id."""
    _require_campaign(conn, campaign_id)
    source = _require_text(source, "source")
    signal_type = _require_text(signal_type, "signal_type")
    thesis = _require_text(thesis, "thesis")
    _validate_bias(bias)
    generated_at = _validate_ts(generated_at or _now(), "generated_at")
    if expires_at:
        expires_at = _validate_ts(expires_at, "expires_at")
    resolution_status = _require_text(resolution_status, "resolution_status")

    now = _now()
    cur = conn.execute(
        """INSERT INTO signals
           (campaign_id, source, finding_ref_json, producing_agent, producing_model,
            ticker, sector, asset, signal_type, bias, thesis, confidence,
            classification, evidence_json, generated_at, expires_at,
            resolution_status, outcome_json, baseline_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?,  ?, ?, ?, ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?, ?, ?)""",
        (campaign_id, source, _json_payload(finding_ref_json, "finding_ref_json"),
         producing_agent, producing_model, ticker, sector, asset, signal_type, bias,
         thesis, confidence, classification, _json_payload(evidence_json, "evidence_json"),
         generated_at, expires_at, resolution_status,
         _json_payload(outcome_json, "outcome_json"),
         _json_payload(baseline_json, "baseline_json"), now, now),
    )
    conn.commit()
    return cur.lastrowid


def create_or_get_signal(conn, **kwargs):
    """Return an existing matching Signal id, or create one."""
    finding_ref_json = _json_payload(kwargs.get("finding_ref_json"), "finding_ref_json")
    row = find_signal(
        conn,
        campaign_id=kwargs.get("campaign_id"),
        source=kwargs.get("source"),
        ticker=kwargs.get("ticker"),
        signal_type=kwargs.get("signal_type"),
        bias=kwargs.get("bias"),
        finding_ref_json=finding_ref_json,
    )
    if row:
        return row["id"]
    kwargs = dict(kwargs)
    kwargs["finding_ref_json"] = finding_ref_json
    return create_signal(conn, **kwargs)


def find_signal(conn, *, campaign_id, source, ticker, signal_type, bias, finding_ref_json=None):
    """Find the newest Signal matching the deterministic research key."""
    _validate_bias(bias)
    clauses = [
        "campaign_id=?", "source=?", "signal_type=?", "bias=?",
        _nullable_clause("ticker", ticker),
        _nullable_clause("finding_ref_json", finding_ref_json),
    ]
    params = [campaign_id, source, signal_type, bias]
    if ticker is not None:
        params.append(ticker)
    if finding_ref_json is not None:
        params.append(finding_ref_json)
    return conn.execute(
        f"SELECT * FROM signals WHERE {' AND '.join(clauses)} ORDER BY generated_at DESC, id DESC LIMIT 1",
        params,
    ).fetchone()


def get_signal(conn, signal_id):
    return conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()


def list_signals(conn, *, campaign_id=None, ticker=None, signal_type=None,
                 status=None, expires_before=None, expires_after=None, limit=None):
    """List Signals with optional filters."""
    clauses, params = [], []
    if campaign_id:
        clauses.append("campaign_id=?"); params.append(campaign_id)
    if ticker:
        clauses.append("ticker=?"); params.append(ticker)
    if signal_type:
        clauses.append("signal_type=?"); params.append(signal_type)
    if status:
        clauses.append("resolution_status=?"); params.append(status)
    if expires_before:
        clauses.append("expires_at IS NOT NULL AND expires_at <= ?"); params.append(expires_before)
    if expires_after:
        clauses.append("expires_at IS NOT NULL AND expires_at >= ?"); params.append(expires_after)
    where = " AND ".join(clauses)
    sql = f"SELECT * FROM signals{' WHERE ' + where if where else ''} ORDER BY generated_at DESC, id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def update_signal(conn, signal_id, *, resolution_status=None, outcome_json=None, baseline_json=None):
    """Update Signal lifecycle fields and return the updated row."""
    if not get_signal(conn, signal_id):
        raise ValueError(f"Signal {signal_id!r} not found")
    updates = {}
    if resolution_status is not None:
        updates["resolution_status"] = _require_text(resolution_status, "resolution_status")
    if outcome_json is not None:
        updates["outcome_json"] = _json_payload(outcome_json, "outcome_json")
    if baseline_json is not None:
        updates["baseline_json"] = _json_payload(baseline_json, "baseline_json")
    if not updates:
        return get_signal(conn, signal_id)

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{key}=?" for key in updates)
    conn.execute(f"UPDATE signals SET {set_clause} WHERE id=?", [*updates.values(), signal_id])
    conn.commit()
    return get_signal(conn, signal_id)


def link_signal_candidate(conn, signal_id, candidate_id, strategy_id, *, bridge_source="research"):
    """Link one Candidate to one upstream Signal and return the bridge id."""
    _require_signal(conn, signal_id)
    candidate = _require_candidate(conn, candidate_id)
    _require_strategy(conn, strategy_id)
    if candidate["strategy_id"] != strategy_id:
        raise ValueError(
            f"Candidate {candidate_id!r} belongs to strategy {candidate['strategy_id']!r}, not {strategy_id!r}"
        )
    bridge_source = _require_text(bridge_source, "bridge_source")

    existing = conn.execute(
        "SELECT * FROM signal_candidates WHERE candidate_id=?", (candidate_id,),
    ).fetchone()
    if existing:
        if existing["signal_id"] == signal_id and existing["strategy_id"] == strategy_id:
            return existing["id"]
        raise ValueError(f"Candidate {candidate_id!r} is already linked to Signal {existing['signal_id']!r}")

    cur = conn.execute(
        """INSERT INTO signal_candidates
           (signal_id, candidate_id, strategy_id, bridge_source, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (signal_id, candidate_id, strategy_id, bridge_source, _now()),
    )
    conn.commit()
    return cur.lastrowid


def get_signal_for_candidate(conn, candidate_id):
    """Return joined Signal/bridge provenance for a Candidate, if present."""
    return conn.execute(
        """SELECT s.id AS signal_id, s.campaign_id, s.source, s.finding_ref_json,
                  s.producing_agent, s.producing_model, s.ticker, s.sector, s.asset,
                  s.signal_type, s.bias, s.thesis, s.confidence, s.classification,
                  s.evidence_json, s.generated_at, s.expires_at, s.resolution_status,
                  s.outcome_json, s.baseline_json, s.created_at AS signal_created_at,
                  s.updated_at AS signal_updated_at,
                  sc.id AS bridge_id, sc.candidate_id, sc.strategy_id,
                  sc.bridge_source, sc.created_at AS bridge_created_at
           FROM signal_candidates sc
           JOIN signals s ON s.id = sc.signal_id
           WHERE sc.candidate_id=?""",
        (candidate_id,),
    ).fetchone()


def list_signal_candidates(conn, *, signal_id=None, candidate_id=None, strategy_id=None):
    clauses, params = [], []
    if signal_id:
        clauses.append("signal_id=?"); params.append(signal_id)
    if candidate_id:
        clauses.append("candidate_id=?"); params.append(candidate_id)
    if strategy_id:
        clauses.append("strategy_id=?"); params.append(strategy_id)
    where = " AND ".join(clauses)
    sql = f"SELECT * FROM signal_candidates{' WHERE ' + where if where else ''} ORDER BY created_at DESC, id DESC"
    return conn.execute(sql, params).fetchall()


def _json_payload(value, field):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be valid JSON") from exc
        return value
    return json.dumps(value, sort_keys=True)


def _require_text(value, field):
    if value is None or not str(value).strip():
        raise ValueError(f"{field} is required")
    return str(value).strip()


def _validate_bias(bias):
    if bias not in VALID_BIASES:
        raise ValueError(f"Invalid bias {bias!r}. Valid: {VALID_BIASES}")


def _validate_ts(value, field):
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    return str(value)


def _nullable_clause(column, value):
    return f"{column} IS NULL" if value is None else f"{column}=?"


def _require_campaign(conn, campaign_id):
    if not get_campaign(conn, campaign_id):
        raise ValueError(f"Campaign {campaign_id!r} not found")


def _require_signal(conn, signal_id):
    if not get_signal(conn, signal_id):
        raise ValueError(f"Signal {signal_id!r} not found")


def _require_candidate(conn, candidate_id):
    row = get_candidate(conn, candidate_id)
    if not row:
        raise ValueError(f"Candidate {candidate_id!r} not found")
    return row


def _require_strategy(conn, strategy_id):
    if not get_strategy(conn, strategy_id):
        raise ValueError(f"Strategy {strategy_id!r} not found")
"""Policy engine + system modes (B2).

Three system modes (global, mode-orthogonal — paper/live still apply within):
  NORMAL  — no restrictions.
  DERISK  — sizing scaled (config: derisk_size_factor, default 0.5); no promotions.
  SAFE    — entries blocked entirely; exits + reconcile only.

Policies are declared in `config.yaml::policies:` as a list of rules:

    - id: drawdown_safe
      when: "portfolio_drawdown_pct < -0.05"
      then: "safe_mode"
      reason: "Portfolio drawdown breach"

`evaluate_policies(state)` is pure: state in, list of triggered actions out.
Action → mode mapping: safe_mode → SAFE, derisk → DERISK, halt_research → (sentinel).

System mode is journaled as an `events` row with event_type='system_mode'.
"""

import json

from heron.config import POLICIES, POLICY_CONFIG
from heron.journal.ops import log_event
from heron.util import utc_now_iso


VALID_MODES = ("NORMAL", "DERISK", "SAFE")
ACTIONS = ("safe_mode", "derisk", "halt_research", "block_promotions")
_ACTION_TO_MODE = {"safe_mode": "SAFE", "derisk": "DERISK"}
_MODE_RANK = {"NORMAL": 0, "DERISK": 1, "SAFE": 2}


# ── Pure rule evaluator ────────────────────────────────────

_ALLOWED_BUILTINS = {"abs": abs, "min": min, "max": max, "round": round, "len": len}


def _safe_eval(expr, state):
    """Evaluate `expr` against `state` with a restricted namespace.

    Operator-controlled YAML; this just guards against accidental imports etc.
    """
    return eval(expr, {"__builtins__": _ALLOWED_BUILTINS}, dict(state))  # noqa: S307


def evaluate_policies(state, policies=None):
    """Return list of `{id, action, reason, when}` for each rule whose `when` is true.

    Eval failures are reported as triggered actions of `error` severity so
    operators see broken rules rather than silently ignoring them.
    """
    rules = policies if policies is not None else POLICIES
    out = []
    for rule in rules or []:
        rid = rule.get("id") or rule.get("when", "")
        when = rule.get("when", "")
        action = rule.get("then", "")
        reason = rule.get("reason") or f"rule {rid} fired"
        if not when or not action:
            continue
        try:
            if _safe_eval(when, state):
                out.append({"id": rid, "action": action, "reason": reason, "when": when})
        except Exception as e:  # noqa: BLE001
            out.append({"id": rid, "action": "error", "reason": f"eval failed: {e}",
                        "when": when})
    return out


def resolve_mode(actions, *, prior_mode="NORMAL"):
    """Most restrictive mode wins. Errors don't change mode (operator review)."""
    candidate = "NORMAL"
    for a in actions:
        m = _ACTION_TO_MODE.get(a.get("action", ""))
        if m and _MODE_RANK[m] > _MODE_RANK[candidate]:
            candidate = m
    # If operator forced a stricter mode manually, keep it.
    if _MODE_RANK.get(prior_mode, 0) > _MODE_RANK[candidate]:
        return prior_mode
    return candidate


# ── State assembly ─────────────────────────────────────────

def assemble_state(conn, *, mode="paper", equity=None):
    """Build the state dict consumed by policy `when` expressions."""
    from heron.journal.trades import list_trades
    from heron.research.cost_guard import check_budget
    from heron.util import trading_day_start_utc_iso

    today = trading_day_start_utc_iso()

    # Portfolio drawdown — sum of P&L since first trade vs. running peak.
    closed = [t for t in list_trades(conn, mode=mode) if t["pnl"] is not None]
    closed.sort(key=lambda r: r["close_filled_at"] or r["created_at"])
    running, peak = 0.0, 0.0
    for r in closed:
        running += r["pnl"] or 0.0
        if running > peak:
            peak = running
    dd = running - peak  # ≤ 0
    eq = float(equity or 0.0) or 1.0
    portfolio_drawdown_pct = dd / eq

    # Today's realized P&L (mode-scoped).
    daily_pnl = sum(
        (t["pnl"] or 0.0) for t in closed
        if t["close_filled_at"] and t["close_filled_at"] >= today
    )

    # Open positions count.
    open_n = len([t for t in list_trades(conn, open_only=True, mode=mode)])

    # Research cost today.
    try:
        budget = check_budget(conn)
        research_cost_today = float(budget.get("month_to_date", 0.0))
    except Exception:  # noqa: BLE001
        research_cost_today = 0.0

    return {
        "mode": mode,
        "equity": eq,
        "portfolio_drawdown_pct": portfolio_drawdown_pct,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl / eq,
        "open_positions": open_n,
        "research_cost_today": research_cost_today,
    }


# ── Mode persistence (events table) ────────────────────────

def current_system_mode(conn):
    """Read latest system_mode event. Defaults to NORMAL."""
    row = conn.execute(
        "SELECT message FROM events WHERE event_type='system_mode' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return "NORMAL"
    msg = row["message"] if hasattr(row, "keys") else row[0]
    return msg if msg in VALID_MODES else "NORMAL"


def set_system_mode(conn, new_mode, *, reason="", operator="system",
                    triggered_by=None):
    """Transition to `new_mode`. No-op if unchanged. Logs `system_mode` event."""
    if new_mode not in VALID_MODES:
        raise ValueError(f"Invalid mode {new_mode!r}. Valid: {VALID_MODES}")
    prior = current_system_mode(conn)
    if prior == new_mode:
        return prior
    details = {
        "from": prior, "to": new_mode, "operator": operator,
        "triggered_by": triggered_by, "ts": utc_now_iso(),
    }
    log_event(
        conn, "system_mode", new_mode,
        severity="warn" if new_mode != "NORMAL" else "info",
        source=f"policy.{operator}",
        details_json=json.dumps(details),
    )
    return prior


# ── Sizing helper ──────────────────────────────────────────

def derisk_qty(qty, *, mode_state):
    """Apply DERISK scaling to `qty`. NORMAL/SAFE return qty unchanged."""
    if mode_state != "DERISK":
        return qty
    factor = float(POLICY_CONFIG.get("derisk_size_factor", 0.5))
    return round(max(qty * factor, 0.0), 6)

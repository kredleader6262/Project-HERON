"""Scheduled jobs — pure functions wrapping existing layer entrypoints.

Each job takes `(conn, mode)` and returns a result dict; the supervisor
catches exceptions and records them in `scheduler_runs`.
"""

import logging

from heron.config import ANTHROPIC_API_KEY
from heron.journal.ops import log_event
from heron.research.cost_guard import check_budget
from heron.resilience.secrets import check_secrets_hygiene
from heron.resilience.startup_audit import run_startup_audit
from heron.util import utc_now_iso

log = logging.getLogger(__name__)


def job_research_premarket(conn, mode):
    """Run the pre-market research pass (news → classify → candidates → escalate)."""
    budget = check_budget(conn)
    if not budget["research_allowed"]:
        return {"status": "cost_halted", "reason": budget["reason"]}

    from heron.research.orchestrator import ResearchPass
    # ResearchPass owns its own DataFeed + connection by default; reuse our conn.
    rp = ResearchPass(conn=conn)
    try:
        return rp.run(pass_type="premarket", escalate=bool(ANTHROPIC_API_KEY))
    finally:
        # Don't close the conn we passed in.
        rp._own_conn = False
        rp.close()


def job_executor_cycle(conn, mode):
    """One executor tick: process accepted candidates + poll exits."""
    from heron.execution.cycle import run_executor_cycle
    return run_executor_cycle(conn, mode=mode)


def job_eod_debrief(conn, mode):
    """End-of-day debrief: aggregate trades, write prose, post Discord."""
    from heron.alerts.debrief import run as run_debrief
    result = run_debrief(conn, deliver=True, dry_run=False)
    return {
        "status": "ok",
        "closed_count": result["data"]["closed_count"],
        "pnl": result["data"]["pnl"],
        "delivery": (result.get("delivery") or {}).get("status"),
    }


def job_daily_health(conn, mode):
    """Resilience audit + secrets + cost — alert if anything is hot."""
    audit = run_startup_audit(conn)
    secrets = check_secrets_hygiene()
    budget = check_budget(conn)

    issues = []
    if audit["status"] != "clean":
        issues.extend(audit["issues"])
    if secrets["status"] != "clean":
        issues.extend(secrets["issues"])
    if budget["status"] != "ok":
        issues.append(f"cost: {budget['reason']}")

    if issues:
        # Use 'drift' category for any operational alarm (already rate-limited)
        try:
            from heron.alerts.discord import send as discord_send, dashboard_link
            msg = (f"⚠ **Daily health** — {len(issues)} issue(s):\n"
                   + "\n".join(f"• {i}" for i in issues[:5])
                   + f"\n{dashboard_link('/resilience')}")
            discord_send("drift", msg)
        except Exception as e:
            log.warning(f"daily health alert failed: {e}")

    return {
        "status": "ok" if not issues else "issues",
        "issues": issues,
        "audit": audit["status"],
        "cost": budget["status"],
    }


def job_heartbeat(conn, mode):
    """Tiny liveness ping. Logged to events; Discord on first beat after a gap."""
    log_event(conn, "heartbeat", f"supervisor alive [{mode}]",
              severity="info", source="runtime")
    return {"status": "ok", "ts": utc_now_iso()}

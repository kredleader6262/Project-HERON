"""Pre-flight checks for the supervisor.

Refuses to start if any *critical* check fails. Warnings are surfaced but
non-blocking. Critical = anything that would put real money at risk if we
proceeded blind: missing secrets in live mode, broker reconciliation drift,
unprotected open positions.
"""

from heron.research.cost_guard import check_budget
from heron.resilience.secrets import check_secrets_hygiene
from heron.resilience.startup_audit import run_startup_audit


def preflight(conn, *, mode="paper", broker=None):
    """Return {ok: bool, blockers: [...], warnings: [...], details: {...}}.

    Any blocker prevents the supervisor from starting.
    """
    blockers = []
    warnings = []
    details = {}

    # 1. Secrets hygiene
    sec = check_secrets_hygiene()
    details["secrets"] = sec
    missing_required = sec["env_vars"]["missing_required"]
    if missing_required:
        msg = f"missing required env vars: {missing_required}"
        # In paper mode Alpaca keys are still required; ANTHROPIC is optional
        # for pure-deterministic running but we still warn.
        blockers.append(f"secrets: {msg}")
    if sec["env_file"]["status"] not in ("ok", "missing"):
        warnings.append(f"secrets: env file {sec['env_file']['status']}")

    # 2. Cost guard — research halts if tripped, but supervisor still runs
    budget = check_budget(conn)
    details["cost"] = budget
    if budget["status"] == "tripped":
        warnings.append(f"cost: {budget['reason']} (research will be skipped)")
    elif budget["status"] == "warning":
        warnings.append(f"cost: {budget['reason']}")

    # 3. Startup audit (broker/journal reconciliation, stop coverage)
    audit = run_startup_audit(conn, broker=broker)
    details["audit"] = audit
    if audit["status"] != "clean":
        # Drift is only a blocker in live mode; paper can self-heal next cycle
        if mode == "live":
            blockers.append(f"startup audit: {len(audit['issues'])} issue(s)")
        else:
            warnings.append(f"startup audit: {len(audit['issues'])} issue(s) (paper)")

    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "details": details,
    }

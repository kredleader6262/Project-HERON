"""Cost controls (M14) — centralized budget guardrails.

Replaces the scattered `if month_cost >= MONTHLY_COST_CEILING: halt` pattern
with a single policy module that:

  1. Projects month-end cost from MTD run-rate.
  2. Classifies budget state as ok / warning / tripped.
  3. Fires Discord alerts at state transitions (rate-limited by category).
  4. Exposes `check_budget` + `assert_research_allowed` for callers.

Per Project-HERON.md §7 and §12:
  - Hard cap: $45/month.
  - Warning at 80% of ceiling (configurable).
  - Fallback: Research layer halts, Strategy + Execution continue.
"""

import calendar
from datetime import datetime, timezone

from heron.config import MONTHLY_COST_CEILING
from heron.journal.ops import get_monthly_cost, log_event

# Alert thresholds. 0.80 = 80% of ceiling warns, >=1.0 trips.
WARNING_PCT = 0.80


class CostTripped(Exception):
    """Raised when a caller attempts to escalate past the hard cap."""


def project_month_end(conn, now=None):
    """Linear extrapolation of MTD cost to month-end.

    Returns dict: {mtd, projected, ceiling, pct_used, pct_projected,
                   days_elapsed, days_in_month, year_month}.
    """
    now = now or datetime.now(timezone.utc)
    year_month = now.strftime("%Y-%m")
    mtd = get_monthly_cost(conn, year_month)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_elapsed = now.day
    # Avoid divide-by-zero on day 1
    daily_rate = mtd / max(days_elapsed, 1)
    projected = daily_rate * days_in_month
    return {
        "year_month": year_month,
        "mtd": mtd,
        "projected": projected,
        "ceiling": MONTHLY_COST_CEILING,
        "pct_used": mtd / MONTHLY_COST_CEILING if MONTHLY_COST_CEILING else 0,
        "pct_projected": projected / MONTHLY_COST_CEILING if MONTHLY_COST_CEILING else 0,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
    }


def check_budget(conn, now=None):
    """Classify current budget state.

    Returns dict with everything `project_month_end` does, plus:
      status:  'ok' | 'warning' | 'tripped'
      reason:  short human explanation
      research_allowed:  bool (False when tripped)
    """
    p = project_month_end(conn, now)
    ceiling = p["ceiling"]
    mtd = p["mtd"]
    projected = p["projected"]

    if ceiling <= 0:
        status, reason = "ok", "no ceiling configured"
    elif mtd >= ceiling:
        status = "tripped"
        reason = f"MTD ${mtd:.2f} ≥ ceiling ${ceiling:.2f}"
    elif projected >= ceiling:
        status = "tripped"
        reason = (f"Projected ${projected:.2f} ≥ ceiling ${ceiling:.2f} "
                  f"(day {p['days_elapsed']}/{p['days_in_month']})")
    elif mtd >= ceiling * WARNING_PCT or projected >= ceiling * WARNING_PCT:
        status = "warning"
        reason = (f"MTD ${mtd:.2f}, projected ${projected:.2f} "
                  f"(>{int(WARNING_PCT * 100)}% of ${ceiling:.2f})")
    else:
        status = "ok"
        reason = f"MTD ${mtd:.2f}, projected ${projected:.2f}"

    p["status"] = status
    p["reason"] = reason
    p["research_allowed"] = status != "tripped"
    return p


def assert_research_allowed(conn, *, task_name=None):
    """Raise CostTripped if the research layer is forbidden from escalating.

    Call this from Research-layer entry points (thesis, proposer, orchestrator)
    before spending Claude tokens.
    """
    state = check_budget(conn)
    if not state["research_allowed"]:
        task = task_name or "research"
        log_event(conn, "cost_trip", f"{task} halted: {state['reason']}",
                  severity="error", source="cost_guard")
        raise CostTripped(state["reason"])
    return state


def notify_if_threshold(conn, *, force=False):
    """Send Discord alert on budget state changes.

    Called by the orchestrator / CLI after any Claude call. Fires:
      - cost_warning  when status becomes 'warning'
      - cost_trip     when status becomes 'tripped'

    The alerts module applies per-category rate-limiting so we don't spam.
    Returns the budget state dict.
    """
    state = check_budget(conn)
    # Import here to avoid pulling httpx at module import in contexts
    # where alerts aren't needed.
    from heron.alerts.discord import send as discord_send, dashboard_link

    if state["status"] == "warning":
        msg = (f"⚠ **Cost warning** — MTD ${state['mtd']:.2f}, "
               f"projected ${state['projected']:.2f} of ${state['ceiling']:.0f}. "
               f"{dashboard_link('/costs')}")
        discord_send("cost_warning", msg, force=force)
    elif state["status"] == "tripped":
        msg = (f"🛑 **Cost ceiling tripped** — MTD ${state['mtd']:.2f}, "
               f"projected ${state['projected']:.2f} of ${state['ceiling']:.0f}. "
               f"Research halted; execution continues. "
               f"{dashboard_link('/costs')}")
        # cost_trip bypasses rate limit (always notify)
        discord_send("cost_trip", msg, force=True)
    return state

"""First-run setup: shared logic for `heron init` (CLI) and `/setup` (web).

Both surfaces call `plan_initial_setup` to preview, then `apply_initial_setup`
to commit. The plan is deterministic given the same inputs; the apply step
refuses on a populated DB so a misclick can't re-seed over real history.
"""

from __future__ import annotations

from heron.journal import campaigns as jcampaigns
from heron.journal.ops import log_event
from heron.journal.strategies import (
    create_strategy, list_strategies, transition_strategy,
)
from heron.strategy.baseline import ensure_baseline


class SetupAlreadyDoneError(RuntimeError):
    """Raised when the journal already has strategies/campaigns from an earlier setup."""


# Cadence presets — what scheduled jobs the operator wants. The supervisor
# always registers DEFAULT_JOBS; this is a hint we record in the journal so
# the user knows what they asked for. Disabling jobs is a separate operator
# action (Actions tab → Pause).
CADENCES = {
    "premarket_only": ["premarket_research"],
    "premarket_eod": ["premarket_research", "eod_debrief"],
    "full": ["premarket_research", "executor_cycle", "eod_debrief", "daily_health"],
}


def _is_populated(conn):
    """Treat the DB as populated if any non-default-paper campaign or strategy exists."""
    n_strat = conn.execute("SELECT COUNT(*) AS n FROM strategies").fetchone()["n"]
    if n_strat:
        return True
    # Allow `default_paper` (created by migration); only block if there are user campaigns.
    n_camp = conn.execute(
        "SELECT COUNT(*) AS n FROM campaigns WHERE id != 'default_paper'"
    ).fetchone()["n"]
    return n_camp > 0


def plan_initial_setup(*, capital_usd, campaign_name="Default Paper Campaign",
                       cadence="premarket_eod",
                       max_capital_pct=0.15, max_positions=3,
                       drawdown_budget_pct=0.05,
                       paper_window_days=90):
    """Pure preview: what would be created. No DB writes.

    Returns a dict the caller can render before calling `apply_initial_setup`.
    """
    if cadence not in CADENCES:
        raise ValueError(f"unknown cadence {cadence!r}; one of {list(CADENCES)}")
    if capital_usd <= 0:
        raise ValueError("capital_usd must be > 0")
    if not 0 < max_capital_pct <= 1.0:
        raise ValueError("max_capital_pct must be in (0, 1]")
    if max_positions < 1:
        raise ValueError("max_positions must be >= 1")
    if not 0 < drawdown_budget_pct < 1.0:
        raise ValueError("drawdown_budget_pct must be in (0, 1)")

    return {
        "campaign": {
            "id": "first_paper",
            "name": campaign_name,
            "mode": "paper",
            "state": "ACTIVE",
            "capital_allocation_usd": float(capital_usd),
            "paper_window_days": int(paper_window_days),
        },
        "strategies": [
            {
                "id": "pead_v1",
                "name": "PEAD LLM Variant",
                "template": "pead",
                "is_baseline": False,
                "state_target": "PAPER",
                "max_capital_pct": float(max_capital_pct),
                "max_positions": int(max_positions),
                "drawdown_budget_pct": float(drawdown_budget_pct),
            },
            {
                "id": "pead_v1_baseline",
                "name": "PEAD Deterministic Baseline",
                "template": "pead",
                "is_baseline": True,
                "parent_id": "pead_v1",
                "state_target": "PAPER",
            },
        ],
        "cadence": {
            "preset": cadence,
            "jobs": CADENCES[cadence],
        },
        "guardrails": {
            "max_capital_pct": float(max_capital_pct),
            "max_positions": int(max_positions),
            "drawdown_budget_pct": float(drawdown_budget_pct),
        },
    }


def apply_initial_setup(conn, plan):
    """Execute the plan. Refuses if the DB is already populated.

    Idempotent in spirit (re-running on a fresh DB after a partial failure
    will pick up where it left off via INSERT-or-skip semantics). Logs an
    `initial_setup` event so the bootstrap is auditable.
    """
    if _is_populated(conn):
        raise SetupAlreadyDoneError(
            "Journal already has strategies/campaigns; refusing to re-seed. "
            "Drop or back up `data/heron.db` if you really want to start over."
        )

    cmp = plan["campaign"]
    jcampaigns.create_campaign(
        conn, cmp["id"], cmp["name"],
        mode=cmp["mode"],
        state=cmp["state"],
        capital_allocation_usd=cmp["capital_allocation_usd"],
        paper_window_days=cmp["paper_window_days"],
    )

    created_ids = []
    for s in plan["strategies"]:
        if s.get("is_baseline"):
            # Use the canonical helper so baseline lineage matches what the
            # rest of the system expects. ensure_baseline copies the parent's
            # state (already PAPER from the transition above) and config.
            ensure_baseline(conn, s["parent_id"])
            jcampaigns.attach_strategy(conn, cmp["id"], s["id"])
            created_ids.append(s["id"])
            continue

        create_strategy(
            conn, s["id"], s["name"],
            description=f"{s['name']} (created via initial setup)",
            rationale="Initial setup wizard",
            campaign_id=cmp["id"],
            template=s["template"],
            max_capital_pct=s["max_capital_pct"],
            max_positions=s["max_positions"],
            drawdown_budget_pct=s["drawdown_budget_pct"],
        )
        transition_strategy(conn, s["id"], s["state_target"],
                            reason="initial setup", operator="setup")
        created_ids.append(s["id"])

    log_event(
        conn, "initial_setup",
        f"Bootstrapped {cmp['id']} with {len(created_ids)} strategies "
        f"(cadence={plan['cadence']['preset']})",
        severity="info", source="runtime.setup",
    )
    return {
        "campaign_id": cmp["id"],
        "strategy_ids": created_ids,
        "cadence": plan["cadence"]["preset"],
    }


def is_already_setup(conn):
    """Public predicate so UI/CLI can short-circuit before plan/apply."""
    return _is_populated(conn)

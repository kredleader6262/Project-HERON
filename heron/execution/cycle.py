"""Executor cycle — one tick of the live trading loop.

Iterates ACTIVE campaigns → their PAPER/LIVE strategies → instantiates each
from its template → reconciles open trades and processes accepted candidates.

Wraps `Executor` rather than duplicating its logic. Pure orchestration.
"""

import json
import logging

from heron.journal.campaigns import list_campaigns, get_campaign_strategies
from heron.journal.candidates import list_candidates, dispose_candidate
from heron.journal.ops import log_event
from heron.journal.strategies import list_strategies
from heron.strategy.templates import get_template, instantiate_from_template

log = logging.getLogger(__name__)


def _instantiate_strategy(row):
    """Build a BaseStrategy instance from a strategies row.

    Falls back to the PEAD template if no template column is set (legacy rows).
    Returns (strategy, None) or (None, skip_reason).
    """
    template_name = row["template"] if "template" in row.keys() and row["template"] else None
    if template_name is None:
        # Legacy strategies created before templates: assume PEAD only if id hints it
        if row["id"].startswith("pead"):
            template_name = "pead"
        else:
            return None, "missing template"
    try:
        template = get_template(template_name)
    except KeyError:
        return None, f"unknown template {template_name!r}"

    overrides = {}
    if row["config"]:
        try:
            overrides = json.loads(row["config"])
        except json.JSONDecodeError:
            log.warning(f"strategy {row['id']}: config not valid JSON; using template defaults")

    kwargs = {}
    init_params = template.cls.__init__.__code__.co_varnames
    if "is_llm_variant" in init_params:
        kwargs["is_llm_variant"] = not row["is_baseline"]
    return instantiate_from_template(
        template_name, row["id"], config_overrides=overrides, **kwargs,
    ), None


def _record_strategy_skip(conn, row, reason, summary):
    message = f"{row['id']}: {reason}"
    log.warning(f"strategy {message}; skipping")
    summary["skipped"].append(message)
    existing = conn.execute(
        "SELECT id FROM events WHERE event_type='strategy_skipped' AND message=? LIMIT 1",
        (message,),
    ).fetchone()
    if not existing:
        log_event(conn, "strategy_skipped", message, severity="warn", source="executor_cycle")


def run_executor_cycle(conn, *, mode="paper", broker=None):
    """One tick: for every active strategy, check exits and try to enter accepted candidates.

    `broker` defaults to a paper Alpaca adapter if not supplied. The function
    is safe to call from a scheduler — it never raises on a single-strategy
    failure; per-strategy errors are logged and the loop continues.
    """
    if broker is None:
        from heron.execution.alpaca_adapter import AlpacaPaperAdapter
        broker = AlpacaPaperAdapter()

    from heron.execution.executor import Executor
    executor = Executor(broker, conn)

    summary = {
        "mode": mode,
        "campaigns": 0,
        "strategies": 0,
        "exits": 0,
        "entries": 0,
        "errors": [],
        "skipped": [],
        "system_mode": "NORMAL",
        "policy_actions": [],
    }

    # B2: evaluate policies first; transition global system mode if needed.
    try:
        from heron.strategy.policy import (
            assemble_state, evaluate_policies, resolve_mode,
            current_system_mode, set_system_mode,
        )
        try:
            equity = executor.get_equity()
        except Exception:  # noqa: BLE001
            equity = None
        state = assemble_state(conn, mode=mode, equity=equity)
        actions = evaluate_policies(state)
        prior = current_system_mode(conn)
        target = resolve_mode(actions, prior_mode="NORMAL")  # rules drive transitions
        if target != prior:
            set_system_mode(conn, target, reason="policy auto",
                            operator="cycle",
                            triggered_by=[a["id"] for a in actions])
        summary["system_mode"] = target
        summary["policy_actions"] = actions
        if target == "SAFE":
            log.warning("system mode SAFE — entries blocked, exits/reconcile only")
    except Exception as e:  # noqa: BLE001
        log.exception(f"policy evaluation failed: {e}")
        summary["errors"].append(f"policy: {e}")

    campaigns = [c for c in list_campaigns(conn, mode=mode, state="ACTIVE")]
    summary["campaigns"] = len(campaigns)

    # Build the set of strategy rows to process: any PAPER/LIVE strategy under
    # an active campaign, plus any orphan strategy in PAPER/LIVE state for
    # back-compat with pre-campaigns deployments (migration leaves no orphans
    # in practice, but better not to silently drop a position).
    seen_ids = set()
    rows = []
    for camp in campaigns:
        for s in get_campaign_strategies(conn, camp["id"]):
            if s["state"] in ("PAPER", "LIVE"):
                rows.append(s)
                seen_ids.add(s["id"])
    for s in list_strategies(conn, state="PAPER"):
        if s["id"] not in seen_ids:
            rows.append(s)
    for s in list_strategies(conn, state="LIVE"):
        if s["id"] not in seen_ids:
            rows.append(s)

    summary["strategies"] = len(rows)

    for row in rows:
        try:
            strat, skip_reason = _instantiate_strategy(row)
            if skip_reason:
                _record_strategy_skip(conn, row, skip_reason, summary)
                continue

            # 1. Exits first — never miss a stop
            exits = executor.check_exits(strat)
            summary["exits"] += len(exits)

            # 2. Process accepted candidates that haven't been traded yet
            accepted = list_candidates(conn, strategy_id=row["id"], disposition="accepted")
            for cand in accepted:
                # Skip if a trade already exists for this candidate
                existing = conn.execute(
                    "SELECT id FROM trades WHERE candidate_id=?", (cand["id"],)
                ).fetchone()
                if existing:
                    continue

                # Levels need market data — minimal: last close + ATR. Defer to
                # the strategy itself; if it needs more, it can fetch via the
                # broker. For now we ask the broker for a fresh quote and use
                # the executor's `enter_position`.
                try:
                    equity = executor.get_equity()
                except Exception as e:
                    summary["errors"].append(f"{row['id']}: get_equity: {e}")
                    break

                # We don't have the historical bars here — defer to the
                # strategy's compute_levels via cached market data. If the
                # caller didn't pre-populate bars, skip (research/data layer's
                # job to provide). Conservative: rely on candidate context.
                try:
                    ctx = json.loads(cand["context_json"]) if cand["context_json"] else {}
                except json.JSONDecodeError:
                    ctx = {}

                md = ctx.get("market_data") or {}
                if not md.get("last_close") or not md.get("atr_14"):
                    log.debug(f"candidate {cand['id']} ({cand['ticker']}): no market_data in context; skipping")
                    continue

                levels = strat.compute_levels(cand["ticker"], md, equity)
                if not levels:
                    dispose_candidate(conn, cand["id"], "rejected",
                                      rejection_reason="levels rejected (edge/sizing)")
                    continue

                try:
                    executor.enter_position(
                        strategy_id=row["id"],
                        ticker=cand["ticker"],
                        qty=levels["qty"],
                        side=cand["side"],
                        stop_price=levels["stop"],
                        target_price=levels["target"],
                        candidate_id=cand["id"],
                        thesis=cand["thesis"],
                        strategy_config=strat.config,
                        mode=mode,
                    )
                    summary["entries"] += 1
                except ValueError as e:
                    # Risk-check failure is expected and not an error we alarm on
                    log.info(f"entry refused for {cand['ticker']}: {e}")
                    dispose_candidate(conn, cand["id"], "rejected", rejection_reason=str(e))
                except Exception as e:
                    summary["errors"].append(f"{row['id']}/{cand['ticker']}: {e}")
                    log.exception(f"entry failed for {cand['ticker']}")
        except Exception as e:
            summary["errors"].append(f"{row['id']}: {e}")
            log.exception(f"cycle failed for strategy {row['id']}")

    return summary

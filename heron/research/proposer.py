"""Strategy proposer — Claude writes new strategy proposals.

Called during pre-market research pass (infrequently). Claude analyzes
market conditions and suggests new strategies with full config + rationale.
Operator must approve before anything trades.
"""

import json
import logging

from heron.config import MONTHLY_COST_CEILING
from heron.data.sanitize import sanitize
from heron.journal.strategies import create_strategy, list_strategies
from heron.journal.ops import log_cost, log_event, get_monthly_cost
from heron.research.claude import call
from heron.research.progress import Spinner

log = logging.getLogger(__name__)

_PROPOSE_PROMPT = """You are a systematic trading strategy designer for HERON, a small retail trading system.

Current active strategies:
{active_strategies}

Recent market context:
{market_context}

Propose ONE new strategy that complements the existing portfolio. Respond with JSON:
{{
  "id": "short_snake_case_id",
  "name": "Human-Readable Name",
  "description": "2-3 sentence description of the strategy",
  "rationale": "Why this strategy, why now, what edge it exploits",
  "universe": ["TICKER1", "TICKER2"],
  "entry_rules": "1-2 sentences",
  "exit_rules": "1-2 sentences",
  "stop_method": "e.g. 2x ATR",
  "target_method": "e.g. 3x ATR or time exit",
  "position_sizing": "e.g. 15% max capital",
  "max_capital_pct": 0.15,
  "max_positions": 3,
  "drawdown_budget_pct": 0.05,
  "min_hold_days": 2,
  "confidence": 0.0-1.0,
  "time_horizon": "days|weeks"
}}

Rules:
- Must be implementable with deterministic rules (no LLM in execution)
- Must be compatible with a 6-name mega-cap universe (AAPL, MSFT, GOOGL, AMZN, NVDA, META)
- confidence 0.7+ to be worth proposing
- Keep it simple — HERON is a small system
- Do NOT propose a strategy that duplicates an existing one"""

MAX_PROPOSALS_PER_DAY = 2


def propose_strategy(conn, market_context="", force=False):
    """Ask Claude to propose a new strategy. Returns proposal dict or None.

    Respects cost ceiling and daily proposal limit unless force=True.
    """
    from heron.research.cost_guard import check_budget
    budget = check_budget(conn)
    if not budget["research_allowed"]:
        log.warning(f"Cost ceiling tripped ({budget['reason']})")
        return {"status": "cost_halted", "reason": budget["reason"]}

    if not force:
        # Check daily proposal count
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = conn.execute(
            "SELECT COUNT(*) as n FROM strategies WHERE created_at LIKE ? AND is_baseline=0",
            (f"{today}%",),
        ).fetchone()["n"]
        if count >= MAX_PROPOSALS_PER_DAY:
            log.info(f"Already {count} proposals today, skipping")
            return {"status": "daily_limit", "count": count}

    # Build context
    active = list_strategies(conn)
    active_str = "\n".join(
        f"- {s['id']}: {s['name']} ({s['state']})" for s in active if not s["is_baseline"]
    ) or "None"

    context_str = sanitize(market_context)[:2000] if market_context else "No specific context provided"

    prompt = _PROPOSE_PROMPT.format(
        active_strategies=active_str,
        market_context=context_str,
    )

    try:
        with Spinner("Claude proposing strategy"):
            result = call(prompt, json_mode=True, temperature=0.5, max_tokens=1024)
    except Exception as e:
        log.error(f"Proposal Claude call failed: {e}")
        return {"status": "error", "error": str(e)}

    parsed = result.get("parsed")
    if not parsed or not parsed.get("id"):
        log.warning("Claude returned invalid proposal JSON")
        return {"status": "parse_error", "raw": result.get("text", "")[:500]}

    confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0))))
    if confidence < 0.5 and not force:
        log.info(f"Proposal confidence too low ({confidence:.2f}), skipping")
        return {"status": "low_confidence", "confidence": confidence, "parsed": parsed}

    # Check for duplicate ID
    existing = conn.execute("SELECT id FROM strategies WHERE id=?", (parsed["id"],)).fetchone()
    if existing:
        log.warning(f"Strategy {parsed['id']} already exists, skipping")
        return {"status": "duplicate", "id": parsed["id"]}

    # Create the strategy
    config = {
        "universe": parsed.get("universe", []),
        "entry_rules": parsed.get("entry_rules", ""),
        "exit_rules": parsed.get("exit_rules", ""),
        "stop_method": parsed.get("stop_method", ""),
        "target_method": parsed.get("target_method", ""),
        "position_sizing": parsed.get("position_sizing", ""),
        "time_horizon": parsed.get("time_horizon", "days"),
        "confidence": confidence,
    }

    strat = create_strategy(
        conn, id=parsed["id"], name=parsed["name"],
        description=parsed.get("description", ""),
        rationale=parsed.get("rationale", ""),
        config=config,
        max_capital_pct=float(parsed.get("max_capital_pct", 0.15)),
        max_positions=int(parsed.get("max_positions", 3)),
        drawdown_budget_pct=float(parsed.get("drawdown_budget_pct", 0.05)),
        min_hold_days=int(parsed.get("min_hold_days", 2)),
    )

    # Log cost
    log_cost(conn, "claude_sonnet", result["tokens_in"], result["tokens_out"],
             result["cost_usd"], task="strategy_proposal")

    log_event(conn, "strategy_proposed",
              f"New strategy proposed: {parsed['id']} ({parsed['name']})",
              severity="info", source="research")

    log.info(f"Strategy proposed: {parsed['id']} confidence={confidence:.2f}")

    return {
        "status": "ok",
        "strategy_id": parsed["id"],
        "name": parsed["name"],
        "confidence": confidence,
        "cost_usd": result["cost_usd"],
        "parsed": parsed,
    }

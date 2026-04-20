"""Thesis writer & conviction scorer — Claude API tier.

Takes shortlisted candidates (already classified locally) and:
1. Writes a structured thesis (bull/bear case, catalysts, risks)
2. Assigns conviction score (0–1)
3. Updates the candidate with api_score and enriched thesis
"""

import json
import logging

from heron.config import MONTHLY_COST_CEILING
from heron.data.sanitize import sanitize
from heron.journal.candidates import get_candidate
from heron.journal.ops import log_cost, get_monthly_cost
from heron.research.claude import call
from heron.research.progress import Spinner

log = logging.getLogger(__name__)

_THESIS_PROMPT = """You are a systematic equity analyst. Write a structured trade thesis for {ticker} ({side}).

CONTEXT:
{context}

LOCAL CLASSIFIER OUTPUT:
{local_summary}

Respond with JSON only:
{{
  "conviction": 0.0-1.0,
  "thesis": "2-3 sentence thesis",
  "bull_case": "1 sentence",
  "bear_case": "1 sentence",
  "catalysts": ["catalyst 1", "catalyst 2"],
  "risks": ["risk 1", "risk 2"],
  "time_horizon": "days|weeks",
  "reasoning": "1 sentence on conviction level"
}}

Rules:
- conviction 0.8+ = high confidence, clear catalyst, strong sentiment alignment
- conviction 0.5-0.8 = moderate, some uncertainty or mixed signals
- conviction <0.5 = low, unclear edge or conflicting factors
- Be specific to {ticker}, not generic market commentary
- Keep total response under 300 words"""


def write_thesis(conn, candidate_id, strategy_id=None):
    """Write a Claude thesis for an existing candidate. Returns result dict or None.

    Updates the candidate's api_score and thesis in the journal.
    """
    candidate = get_candidate(conn, candidate_id)
    if not candidate:
        log.warning(f"Candidate {candidate_id} not found")
        return None

    # Cost gate
    from heron.research.cost_guard import check_budget
    budget = check_budget(conn)
    if not budget["research_allowed"]:
        log.warning(f"Cost ceiling tripped ({budget['reason']}), skipping thesis")
        return {"status": "cost_halted", "candidate_id": candidate_id,
                "reason": budget["reason"]}

    ticker = candidate["ticker"]
    side = candidate["side"]
    strategy_id = strategy_id or candidate["strategy_id"]

    # Build context from candidate's stored data
    context_data = {}
    if candidate["context_json"]:
        try:
            context_data = json.loads(candidate["context_json"])
        except json.JSONDecodeError as e:
            log.debug(f"Bad context_json on candidate {candidate['id']}: {e}")

    local_summary = (
        f"Score: {candidate['local_score']:.2f}, "
        f"Sentiment: {context_data.get('sentiment', '?')} ({context_data.get('sentiment_score', 0):+.2f}), "
        f"Category: {context_data.get('category', '?')}"
    )

    context_str = sanitize(json.dumps(context_data, indent=2))[:1500]
    prompt = _THESIS_PROMPT.format(
        ticker=ticker, side=side,
        context=context_str, local_summary=local_summary,
    )

    try:
        with Spinner(f"Claude thesis for {ticker}"):
            result = call(prompt, json_mode=True, temperature=0.3, max_tokens=512)
    except Exception as e:
        log.error(f"Claude thesis call failed for {ticker}: {e}")
        return {"status": "error", "candidate_id": candidate_id, "error": str(e)}

    parsed = result.get("parsed")
    if not parsed:
        log.warning(f"Claude returned invalid JSON for thesis on {ticker}")
        return {"status": "parse_error", "candidate_id": candidate_id,
                "raw_text": result.get("text", "")[:500]}

    conviction = max(0.0, min(1.0, float(parsed.get("conviction", 0))))

    # Build enriched thesis string
    thesis_text = _format_thesis(parsed, ticker, side)

    # Update candidate in journal
    conn.execute(
        "UPDATE candidates SET api_score=?, thesis=? WHERE id=?",
        (conviction, thesis_text, candidate_id),
    )
    conn.commit()

    # Log cost
    log_cost(conn, "claude_sonnet", result["tokens_in"], result["tokens_out"],
             result["cost_usd"], strategy_id=strategy_id, task="thesis")

    log.info(f"Thesis written for {ticker}: conviction={conviction:.2f}, "
             f"cost=${result['cost_usd']:.4f}")

    return {
        "status": "ok",
        "candidate_id": candidate_id,
        "ticker": ticker,
        "conviction": conviction,
        "thesis": thesis_text,
        "cost_usd": result["cost_usd"],
        "tokens_in": result["tokens_in"],
        "tokens_out": result["tokens_out"],
        "parsed": parsed,
    }


def write_theses_batch(conn, candidate_ids, strategy_id=None):
    """Write theses for multiple candidates. Respects cost ceiling between calls."""
    results = []
    for cid in candidate_ids:
        r = write_thesis(conn, cid, strategy_id=strategy_id)
        if not r:
            continue
        results.append(r)
        if r.get("status") == "cost_halted":
            log.warning("Cost ceiling reached, stopping thesis batch")
            break
    return results


def _format_thesis(parsed, ticker, side):
    """Format parsed Claude response into a readable thesis string."""
    parts = [
        f"[{side.upper()}] {ticker}: {parsed.get('thesis', 'No thesis')}",
        f"Bull: {parsed.get('bull_case', '?')}",
        f"Bear: {parsed.get('bear_case', '?')}",
    ]
    catalysts = parsed.get("catalysts", [])
    if catalysts:
        parts.append(f"Catalysts: {', '.join(catalysts[:3])}")
    risks = parsed.get("risks", [])
    if risks:
        parts.append(f"Risks: {', '.join(risks[:3])}")
    parts.append(f"Conviction: {parsed.get('conviction', 0):.2f} — {parsed.get('reasoning', '')}")
    parts.append(f"Horizon: {parsed.get('time_horizon', '?')}")
    return " | ".join(parts)

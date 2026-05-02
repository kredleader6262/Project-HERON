"""Escalation logic — routes candidates from local→API tier.

Three escalation paths (spec Section 6):
1. Continuous sampling: ~15% of local decisions escalated to Claude for audit comparison
2. Score-based: high-scoring local candidates get Claude thesis for conviction
3. Cost-triggered: losing trades from local-classified candidates get post-mortem

All escalations respect the monthly cost ceiling.
"""

import json
import logging
import random

from heron.config import CLAUDE_HAIKU_MODEL
from heron.journal.candidates import get_candidate
from heron.journal.ops import log_audit, log_cost
from heron.research.claude import call
from heron.research.cost_guard import check_budget
from heron.research.thesis import write_thesis
from heron.data.sanitize import sanitize

log = logging.getLogger(__name__)

SAMPLING_RATE = 0.15          # ~15% of local decisions
ESCALATION_SCORE_THRESHOLD = 0.6   # local_score above this → escalate for thesis
AUDIT_MAX_TOKENS = 256


def escalate_candidates(conn, candidate_ids, strategy_id=None, rng=None):
    """Apply escalation rules to a batch of new candidates.

    - All above score threshold → Claude thesis
    - ~15% sample of remainder → audit comparison
    Returns dict with escalation stats.
    """
    if rng is None:
        rng = random.Random()

    budget = check_budget(conn)
    if not budget["research_allowed"]:
        return {"status": "cost_halted", "escalated": 0, "sampled": 0,
                "reason": budget["reason"], "month_cost": budget["mtd"]}

    thesis_ids = []
    sample_ids = []

    for cid in candidate_ids:
        c = get_candidate(conn, cid)
        if not c:
            continue
        score = c["local_score"] or 0

        if score >= ESCALATION_SCORE_THRESHOLD:
            thesis_ids.append(cid)
        elif rng.random() < SAMPLING_RATE:
            sample_ids.append(cid)

    # 1. Write theses for high-scoring candidates
    thesis_results = []
    for cid in thesis_ids:
        if not check_budget(conn)["research_allowed"]:
            break
        r = write_thesis(conn, cid, strategy_id=strategy_id)
        if r:
            thesis_results.append(r)

    # 2. Audit-sample the rest
    audit_results = []
    for cid in sample_ids:
        if not check_budget(conn)["research_allowed"]:
            break
        r = _audit_sample(conn, cid, strategy_id=strategy_id)
        if r:
            audit_results.append(r)

    return {
        "status": "ok",
        "escalated": len(thesis_results),
        "sampled": len(audit_results),
        "thesis_results": thesis_results,
        "audit_results": audit_results,
        "month_cost": check_budget(conn)["mtd"],
    }


def _audit_sample(conn, candidate_id, strategy_id=None):
    """Run Claude on a sampled candidate and log divergence audit."""
    candidate = get_candidate(conn, candidate_id)
    if not candidate:
        return None

    ticker = candidate["ticker"]
    side = candidate["side"]
    local_score = candidate["local_score"] or 0

    context_data = {}
    if candidate["context_json"]:
        try:
            context_data = json.loads(candidate["context_json"])
        except json.JSONDecodeError as e:
            log.debug(f"Bad context_json on candidate {candidate['id']}: {e}")

    prompt = (
        f"Quick assessment: should a systematic trader {side} {ticker}? "
        f"Context: {sanitize(json.dumps(context_data))[:800]}\n\n"
        f"Respond JSON: {{\"agree\": true/false, \"conviction\": 0.0-1.0, \"reason\": \"1 sentence\"}}"
    )

    try:
        result = call(prompt, model=CLAUDE_HAIKU_MODEL, json_mode=True,
                      max_tokens=AUDIT_MAX_TOKENS, temperature=0.2)
    except Exception as e:
        log.warning(f"Audit sample failed for {ticker}: {e}")
        return None

    parsed = result.get("parsed", {})
    # Always log the cost — we paid for the call regardless of parse success.
    log_cost(conn, "claude_haiku", result["tokens_in"], result["tokens_out"],
             result["cost_usd"], strategy_id=strategy_id, task="audit_sample")
    if not parsed:
        log.warning(f"Audit sample for {ticker}: empty parse, cost ${result['cost_usd']:.4f} logged")
        return None

    api_conviction = max(0.0, min(1.0, float(parsed.get("conviction", 0))))
    agrees = parsed.get("agree", True)
    divergence = not agrees or abs(api_conviction - local_score) > 0.3

    # Log to audits table
    log_audit(
        conn, audit_type="sampling",
        strategy_id=strategy_id or candidate["strategy_id"],
        candidate_id=candidate_id,
        local_output=json.dumps({"local_score": local_score, "side": side}),
        api_output=json.dumps(parsed),
        divergence=divergence,
        notes=f"Sampled: local={local_score:.2f} vs api={api_conviction:.2f}, agree={agrees}",
    )

    log.info(f"Audit sample {ticker}: local={local_score:.2f} api={api_conviction:.2f} "
             f"divergent={divergence}")

    return {
        "candidate_id": candidate_id,
        "ticker": ticker,
        "local_score": local_score,
        "api_conviction": api_conviction,
        "agrees": agrees,
        "divergent": divergence,
        "cost_usd": result["cost_usd"],
    }



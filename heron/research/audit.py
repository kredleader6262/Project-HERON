"""Audit module (M11) — cost-triggered post-mortems + trust score.

Cost-triggered: every losing trade gets a post-mortem where Claude re-assesses
the candidate (post-cutoff only, memorization-safe). Divergence between local
and Claude on losing trades is the drift signal.

Trust score: rolling window over sampling + cost-triggered audits.
  trust = 1 - (divergent_audits / total_audits)
Only computed when sample_size >= TRUST_SCORE_MIN_SAMPLES.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from heron.config import (
    CLAUDE_HAIKU_MODEL, CLAUDE_KNOWLEDGE_CUTOFF,
    TRUST_SCORE_WINDOW_DAYS, TRUST_SCORE_MIN_SAMPLES,
    POST_MORTEM_DAILY_LIMIT,
)
from heron.data.sanitize import sanitize
from heron.journal.candidates import get_candidate
from heron.journal.ops import log_audit, log_cost, get_audits
from heron.research.claude import call
from heron.research.progress import Spinner
from heron.research.cost_guard import check_budget

log = logging.getLogger(__name__)

_POSTMORTEM_MAX_TOKENS = 400


def _after_cutoff(dt_str, cutoff=CLAUDE_KNOWLEDGE_CUTOFF):
    """True iff dt_str is strictly after cutoff date."""
    if not dt_str:
        return False
    try:
        # accept YYYY-MM-DD or ISO timestamp
        return dt_str[:10] > cutoff
    except (TypeError, IndexError):
        return False


def find_losing_trades_needing_postmortem(conn, limit=20):
    """Closed losing trades that don't yet have a cost_triggered audit."""
    rows = conn.execute(
        """SELECT t.* FROM trades t
           WHERE t.pnl IS NOT NULL AND t.pnl < 0
             AND t.candidate_id IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM audits a
               WHERE a.trade_id = t.id AND a.audit_type = 'cost_triggered'
             )
           ORDER BY t.close_filled_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return rows


def post_mortem_trade(conn, trade):
    """Run a single post-mortem on a losing trade.

    Re-asks Claude what it would have done given the candidate's original context.
    Logs as audit_type='cost_triggered'. Returns dict or None on skip/failure.
    """
    trade_id = trade["id"]
    candidate_id = trade["candidate_id"]
    if not candidate_id:
        return None

    candidate = get_candidate(conn, candidate_id)
    if not candidate:
        log.warning(f"Post-mortem {trade_id}: candidate missing")
        return None

    # Memorization guard: skip if candidate predates Claude's cutoff
    if not _after_cutoff(candidate["created_at"]):
        log_audit(
            conn, audit_type="cost_triggered",
            strategy_id=trade["strategy_id"], trade_id=trade_id,
            candidate_id=candidate_id,
            local_output=None, api_output=None,
            actual_outcome=f"pnl={trade['pnl']:.2f}",
            divergence=False,
            notes="skipped: pre-cutoff (memorization guard)",
        )
        return {"status": "skipped_pre_cutoff", "trade_id": trade_id}

    # Build local-output summary from candidate
    local_summary = {
        "local_score": candidate["local_score"],
        "final_score": candidate["final_score"],
        "side": candidate["side"],
    }

    # Ask Claude retroactively
    context_str = sanitize(candidate["context_json"] or "{}")[:800]
    ticker = candidate["ticker"]
    side = candidate["side"]
    prompt = (
        f"Post-mortem: a trade on {ticker} ({side}) closed with loss "
        f"pnl={trade['pnl']:.2f}. Given only the original context below, would "
        f"you have traded? Context: {context_str}\n\n"
        f"Respond JSON: {{\"would_trade\": true/false, \"conviction\": 0.0-1.0, "
        f"\"reason\": \"1 sentence\"}}"
    )

    try:
        with Spinner(f"Post-mortem {ticker} trade#{trade_id}"):
            result = call(prompt, model=CLAUDE_HAIKU_MODEL, json_mode=True,
                          max_tokens=_POSTMORTEM_MAX_TOKENS, temperature=0.2)
    except Exception as e:
        log.warning(f"Post-mortem {trade_id} failed: {e}")
        return None

    parsed = result.get("parsed", {}) or {}
    would_trade = bool(parsed.get("would_trade", True))
    api_conviction = max(0.0, min(1.0, float(parsed.get("conviction", 0))))
    local_score = float(candidate["local_score"] or 0)

    # Divergence: local said go, Claude said no (or conviction drifted materially)
    divergence = (not would_trade) or abs(api_conviction - local_score) > 0.3

    log_audit(
        conn, audit_type="cost_triggered",
        strategy_id=trade["strategy_id"], trade_id=trade_id,
        candidate_id=candidate_id,
        local_output=json.dumps(local_summary),
        api_output=json.dumps(parsed),
        actual_outcome=f"pnl={trade['pnl']:.2f}",
        divergence=divergence,
        notes=f"local={local_score:.2f} api={api_conviction:.2f} "
              f"would_trade={would_trade}",
    )
    log_cost(conn, "claude_haiku", result["tokens_in"], result["tokens_out"],
             result["cost_usd"], strategy_id=trade["strategy_id"],
             task="post_mortem")

    return {
        "status": "completed",
        "trade_id": trade_id,
        "ticker": ticker,
        "divergence": divergence,
        "local_score": local_score,
        "api_conviction": api_conviction,
    }


def run_pending_post_mortems(conn, limit=None):
    """Batch-run post-mortems on losing trades with daily + cost gates."""
    limit = limit or POST_MORTEM_DAILY_LIMIT

    # Cost gate — halt if tripped
    budget = check_budget(conn)
    if not budget["research_allowed"]:
        log.warning(f"Post-mortem: budget tripped ({budget['reason']})")
        return {"status": "cost_halted", "spent": budget["mtd"],
                "reason": budget["reason"], "completed": 0}

    # Daily limit gate — count today's cost_triggered audits
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_count = conn.execute(
        """SELECT COUNT(*) AS n FROM audits
           WHERE audit_type='cost_triggered' AND substr(created_at,1,10)=?""",
        (today,),
    ).fetchone()["n"]
    remaining = max(0, limit - today_count)
    if remaining == 0:
        return {"status": "daily_limit_reached", "completed": 0}

    trades = find_losing_trades_needing_postmortem(conn, limit=remaining)
    if not trades:
        return {"status": "no_pending", "completed": 0}

    results = []
    for t in trades:
        r = post_mortem_trade(conn, t)
        if r:
            results.append(r)

    divergent = sum(1 for r in results if r.get("divergence"))
    return {
        "status": "ok",
        "completed": len(results),
        "divergent": divergent,
        "results": results,
    }


def compute_trust_score(conn, window_days=None):
    """Trust score from rolling window of audits (sampling + cost-triggered).

    Returns dict with trust_score, sample_size, breakdown, and warning if
    below threshold or under-sampled.
    """
    window_days = window_days or TRUST_SCORE_WINDOW_DAYS
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

    rows = conn.execute(
        """SELECT audit_type, divergence, COUNT(*) AS n FROM audits
           WHERE created_at >= ?
             AND audit_type IN ('sampling','cost_triggered')
             AND (notes IS NULL OR notes NOT LIKE 'skipped:%')
           GROUP BY audit_type, divergence""",
        (cutoff,),
    ).fetchall()

    breakdown = {"sampling": {"n": 0, "divergent": 0},
                 "cost_triggered": {"n": 0, "divergent": 0}}
    for r in rows:
        t = r["audit_type"]
        breakdown[t]["n"] += r["n"]
        if r["divergence"]:
            breakdown[t]["divergent"] += r["n"]

    total = sum(b["n"] for b in breakdown.values())
    divergent = sum(b["divergent"] for b in breakdown.values())

    if total < TRUST_SCORE_MIN_SAMPLES:
        return {
            "trust_score": None,
            "sample_size": total,
            "window_days": window_days,
            "breakdown": breakdown,
            "warning": f"under-sampled: {total} < {TRUST_SCORE_MIN_SAMPLES} required",
        }

    score = 1.0 - (divergent / total)
    return {
        "trust_score": round(score, 3),
        "sample_size": total,
        "window_days": window_days,
        "breakdown": breakdown,
        "divergent": divergent,
    }


# --- Contamination static audit (A4) ---

import ast
import os

# Functions that read time-sensitive data and MUST be called with as_of= in
# strategy / research code paths used by backtests. Keys are the unqualified
# callable names we expect to see at the callsite.
_PIT_GUARDED_CALLS = {
    "get_earnings_events": "Earnings reads must pass as_of= to avoid restatement leakage.",
    "get_articles":        "News reads must pass as_of= for point-in-time replay.",
    "fetch_news":          "News fetches must pass as_of= for point-in-time replay.",
    "fetch_articles":      "RSS reads must pass as_of= for point-in-time replay.",
    "real_pead_candidates": "Backtest seeders must pass as_of= for PIT replay.",
}


def _call_name(node):
    """Return the trailing attribute / name of a Call node, or None."""
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def contamination_audit(path):
    """Static AST scan for PIT-leak patterns.

    Returns a list of `{file, line, rule, severity, message}` findings.
    `path` may be a single .py file or a directory (scanned recursively).
    """
    targets = []
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in files:
                if f.endswith(".py"):
                    targets.append(os.path.join(root, f))
    else:
        targets.append(path)

    findings = []
    for fp in targets:
        try:
            src = open(fp, encoding="utf-8").read()
            tree = ast.parse(src, filename=fp)
        except (OSError, SyntaxError) as e:
            findings.append({"file": fp, "line": 0, "rule": "parse_error",
                             "severity": "error", "message": str(e)})
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name not in _PIT_GUARDED_CALLS:
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            if "as_of" not in kwargs:
                findings.append({
                    "file": fp,
                    "line": node.lineno,
                    "rule": f"missing_as_of:{name}",
                    "severity": "error",
                    "message": _PIT_GUARDED_CALLS[name],
                })
    return findings


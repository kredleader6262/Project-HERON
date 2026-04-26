"""Baseline-variant runner — deterministic twin for every LLM strategy.

Each LLM strategy gets a paired deterministic variant (is_baseline=1, parent_id=strategy_id).
Both receive the same candidates and market data. The LLM variant can veto/boost via
conviction; the baseline takes everything that passes screen_candidate with is_llm_variant=False.

Equity curves are computed from trades. The Section 10.2 bootstrap test compares them.
"""

import json
import logging
from datetime import datetime, timezone

from heron.journal.trades import create_trade, get_trade, list_trades

log = logging.getLogger(__name__)


def ensure_baseline(conn, parent_strategy_id):
    """Ensure a deterministic baseline variant exists for the given strategy.

    Returns the baseline strategy_id (e.g., "pead_v1_baseline").
    Creates it if it doesn't exist.
    """
    baseline_id = f"{parent_strategy_id}_baseline"

    existing = conn.execute(
        "SELECT id FROM strategies WHERE id=?", (baseline_id,)
    ).fetchone()
    if existing:
        return baseline_id

    # Copy parent config
    parent = conn.execute(
        "SELECT * FROM strategies WHERE id=?", (parent_strategy_id,)
    ).fetchone()
    if not parent:
        raise ValueError(f"Parent strategy {parent_strategy_id!r} not found")

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO strategies
           (id, name, description, state, is_baseline, parent_id,
            campaign_id, template,
            config, max_capital_pct, max_positions, drawdown_budget_pct,
            min_conviction, min_hold_days, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?,  ?, ?,  ?, ?, ?, ?,  ?, ?, ?, ?)""",
        (baseline_id, f"{parent['name']} (baseline)", f"Deterministic baseline for {parent_strategy_id}",
         parent["state"], parent_strategy_id,
         parent["campaign_id"] if "campaign_id" in parent.keys() else None,
         parent["template"] if "template" in parent.keys() else None,
         parent["config"], parent["max_capital_pct"], parent["max_positions"],
         parent["drawdown_budget_pct"], 0.0,  # min_conviction=0 for baseline
         parent["min_hold_days"], now, now),
    )
    conn.commit()
    log.info(f"Created baseline variant: {baseline_id}")
    return baseline_id


def mirror_candidate_to_baseline(conn, candidate_id, baseline_strategy_id):
    """Create a mirrored candidate under the baseline strategy.

    Returns the new baseline candidate_id, or None if already mirrored.
    """
    from heron.journal.candidates import get_candidate, create_candidate

    orig = get_candidate(conn, candidate_id)
    if not orig:
        return None

    # Check for existing mirror (dedup by ticker + baseline strategy in last 24h)
    existing = conn.execute(
        """SELECT id FROM candidates
           WHERE strategy_id=? AND ticker=? AND disposition='pending'
             AND created_at >= datetime('now', '-24 hours')""",
        (baseline_strategy_id, orig["ticker"]),
    ).fetchone()
    if existing:
        return existing["id"]

    ctx = {}
    if orig["context_json"]:
        try:
            ctx = json.loads(orig["context_json"])
        except json.JSONDecodeError as e:
            log.debug(f"Bad context_json on candidate {candidate_id}: {e}")
    ctx["mirrored_from"] = candidate_id

    cid = create_candidate(
        conn, baseline_strategy_id, orig["ticker"],
        side=orig["side"], source="baseline_mirror",
        local_score=orig["local_score"],
        thesis=f"[BASELINE] {orig['thesis'] or ''}",
        context_json=json.dumps(ctx),
    )
    return cid


def get_daily_returns(conn, strategy_id, start_date=None, end_date=None):
    """Compute daily returns for a strategy from closed trades.

    Returns list of {"date": "YYYY-MM-DD", "return_pct": float}.
    Uses close_filled_at date, aggregates by day.
    """
    clauses = ["strategy_id=?", "close_price IS NOT NULL"]
    params = [strategy_id]
    if start_date:
        clauses.append("close_filled_at >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("close_filled_at <= ?")
        params.append(end_date + "T23:59:59")

    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""SELECT DATE(close_filled_at) as date, SUM(pnl_pct) as daily_return
            FROM trades WHERE {where}
            GROUP BY DATE(close_filled_at)
            ORDER BY date""",
        params,
    ).fetchall()

    return [{"date": r["date"], "return_pct": r["daily_return"]} for r in rows]


def get_paired_daily_returns(conn, llm_strategy_id, start_date=None, end_date=None):
    """Get paired daily returns for LLM variant and its baseline.

    Returns list of {"date", "llm_return", "baseline_return", "diff"} for days
    where at least one variant has a trade.
    """
    baseline_id = f"{llm_strategy_id}_baseline"

    llm_returns = {r["date"]: r["return_pct"]
                   for r in get_daily_returns(conn, llm_strategy_id, start_date, end_date)}
    base_returns = {r["date"]: r["return_pct"]
                    for r in get_daily_returns(conn, baseline_id, start_date, end_date)}

    all_dates = sorted(set(llm_returns) | set(base_returns))
    paired = []
    for d in all_dates:
        lr = llm_returns.get(d, 0.0)
        br = base_returns.get(d, 0.0)
        paired.append({
            "date": d,
            "llm_return": lr,
            "baseline_return": br,
            "diff": lr - br,
        })
    return paired


def bootstrap_beat_test(diffs, n_bootstrap=10000, ci=0.95, rng=None):
    """Paired bootstrap test per Section 10.2.

    diffs: list of daily return differences (d_i = r_LLM,i - r_baseline,i)
    Returns {"passes": bool, "ci_lower": float, "ci_upper": float, "mean_diff": float,
             "n_days": int, "n_bootstrap": int}
    """
    import random as _random
    if rng is None:
        rng = _random.Random()

    n = len(diffs)
    if n < 5:
        return {
            "passes": False, "ci_lower": 0.0, "ci_upper": 0.0,
            "mean_diff": 0.0, "n_days": n, "n_bootstrap": n_bootstrap,
            "reason": f"Insufficient data: {n} days (need ≥ 5)",
        }

    # Bootstrap
    means = []
    for _ in range(n_bootstrap):
        sample = [diffs[rng.randint(0, n - 1)] for _ in range(n)]
        means.append(sum(sample) / n)

    means.sort()
    alpha = (1 - ci) / 2
    lo_idx = int(alpha * n_bootstrap)
    hi_idx = int((1 - alpha) * n_bootstrap) - 1

    ci_lower = means[lo_idx]
    ci_upper = means[hi_idx]
    mean_diff = sum(diffs) / n

    passes = ci_lower > 0  # Entire CI above zero

    return {
        "passes": passes,
        "ci_lower": round(ci_lower, 6),
        "ci_upper": round(ci_upper, 6),
        "mean_diff": round(mean_diff, 6),
        "n_days": n,
        "n_bootstrap": n_bootstrap,
    }


def run_beat_test(conn, llm_strategy_id, start_date=None, end_date=None, n_bootstrap=10000):
    """Run the full baseline-beat test for a strategy.

    Returns the bootstrap test result dict.
    """
    paired = get_paired_daily_returns(conn, llm_strategy_id, start_date, end_date)
    diffs = [p["diff"] for p in paired]
    result = bootstrap_beat_test(diffs, n_bootstrap=n_bootstrap)
    result["strategy_id"] = llm_strategy_id
    result["baseline_id"] = f"{llm_strategy_id}_baseline"
    return result


def get_equity_curve(conn, strategy_id, start_date=None, end_date=None, initial=100000.0):
    """Build cumulative equity curve from daily returns.

    Returns list of {"date": str, "equity": float, "return_pct": float}.
    """
    daily = get_daily_returns(conn, strategy_id, start_date, end_date)
    curve = []
    equity = initial
    for d in daily:
        ret = d["return_pct"]
        equity *= (1 + ret)
        curve.append({"date": d["date"], "equity": round(equity, 2), "return_pct": ret})
    return curve

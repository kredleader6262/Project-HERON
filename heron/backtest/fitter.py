"""Parameter fitting for walk-forward backtests.

Given a strategy + train window + axes, run the cartesian grid in-memory
(save=False) and pick the combo that maximizes the objective. Returns the
locked override dict to apply to the subsequent test window.

Pure orchestration over `run_strategy_backtest`. No DB writes.
"""

from __future__ import annotations

import logging

from heron.backtest.runner import run_strategy_backtest
from heron.backtest.sweep import expand_grid

log = logging.getLogger(__name__)

# Minimum trades required for an objective to be considered valid.
# Combos that produce fewer trades fall back to a low-priority tiebreak.
MIN_TRADES_FOR_OBJECTIVE = 3


def _score(metrics, objective):
    """Score a metrics dict by objective. Returns (primary, tiebreak) tuple.

    Higher is better. Combos with too few trades are ranked below all
    combos with enough trades, then broken by total_return as a fallback.
    """
    n = metrics.get("n_trades", 0) or 0
    total_return = metrics.get("total_return") or 0.0
    if n < MIN_TRADES_FOR_OBJECTIVE:
        # Penalty bucket: ranked by raw return, but always below the valid bucket.
        return (-1, total_return)

    if objective == "sharpe":
        primary = metrics.get("sharpe")
        if primary is None:
            primary = total_return  # fallback when std is zero
    elif objective == "total_return":
        primary = total_return
    elif objective == "win_rate":
        primary = metrics.get("win_rate", 0.0) or 0.0
    elif objective == "avg_trade_pnl":
        primary = metrics.get("avg_trade_pnl", 0.0) or 0.0
    else:
        raise ValueError(f"Unknown objective {objective!r}")
    return (0, primary)


def fit_params(conn, strategy_id, axes, *, start, end, seed=0,
               initial_equity=100_000.0, seeder="synthetic",
               objective="sharpe"):
    """Run `axes` grid on [start, end] and return the winning override dict.

    Returns dict {
        "overrides": {axis: value, ...}    # winning combo, may be {}
        "score": (bucket, primary),
        "objective": objective,
        "metrics": {...},                  # winner's metrics
        "candidates": [...],               # all combos tried, with metrics + scores
    }

    Empty axes ({} or None) is allowed and degenerates to a single no-override
    run — useful so callers don't need to special-case "no fitting".
    """
    combos = expand_grid(axes or {})

    candidates = []
    for combo in combos:
        try:
            res = run_strategy_backtest(
                conn, strategy_id,
                start=start, end=end, seed=seed,
                initial_equity=initial_equity, save=False, seeder=seeder,
                config_overrides=combo,
            )
        except ValueError as e:
            log.debug("fit combo %s failed on train window: %s", combo, e)
            candidates.append({
                "overrides": combo,
                "metrics": None,
                "score": (-2, 0.0),
                "error": str(e),
            })
            continue
        score = _score(res["metrics"], objective)
        candidates.append({
            "overrides": combo,
            "metrics": res["metrics"],
            "score": score,
        })

    valid = [c for c in candidates if c.get("metrics") is not None]
    if not valid:
        raise ValueError(
            f"All {len(combos)} fitter combos failed on train window {start}..{end}; "
            f"cannot lock parameters."
        )

    # Stable order: highest score wins; ties broken by original combo order.
    valid_with_idx = list(enumerate(valid))
    valid_with_idx.sort(key=lambda p: (p[1]["score"], -p[0]), reverse=True)
    winner = valid_with_idx[0][1]

    return {
        "overrides": winner["overrides"],
        "score": winner["score"],
        "objective": objective,
        "metrics": winner["metrics"],
        "candidates": candidates,
    }

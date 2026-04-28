"""Statistical significance helpers for backtests + live-trade analysis.

Pure functions, no DB access. Imported by both `strategy.baseline` (live
daily-return paired test) and `backtest.parity` (paired-curve test from
saved backtest reports).
"""

from __future__ import annotations

import random as _random


def bootstrap_beat_test(diffs, n_bootstrap=10000, ci=0.95, rng=None):
    """Paired bootstrap test on a series of return differences.

    `diffs`: list of paired diffs (e.g., r_LLM,i - r_baseline,i).
    Passes when the lower bound of the CI of the mean diff is > 0.

    Returns dict {passes, ci_lower, ci_upper, mean_diff, n_days, n_bootstrap}.
    `n_days` < 5 short-circuits to a not-enough-data fail.
    """
    if rng is None:
        rng = _random.Random()

    n = len(diffs)
    if n < 5:
        return {
            "passes": False,
            "ci_lower": 0.0, "ci_upper": 0.0,
            "mean_diff": 0.0, "n_days": n, "n_bootstrap": n_bootstrap,
            "reason": f"Insufficient data: {n} days (need ≥ 5)",
        }

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
    passes = ci_lower > 0

    return {
        "passes": passes,
        "ci_lower": round(ci_lower, 6),
        "ci_upper": round(ci_upper, 6),
        "mean_diff": round(mean_diff, 6),
        "n_days": n,
        "n_bootstrap": n_bootstrap,
    }

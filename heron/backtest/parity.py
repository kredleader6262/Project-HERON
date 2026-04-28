"""Backtest parity report — does a strategy beat its frozen baseline?

Pure helpers. `compute_parity_report` is given two equity curves and returns
the bootstrap verdict; `save_report` calls it at write time so the verdict
is persisted alongside metrics.
"""

from __future__ import annotations

from heron.backtest.significance import bootstrap_beat_test


def _curve_to_daily_returns(curve):
    """Curve = [{date, equity}, ...] → {date: pct_return} (skips first day)."""
    out = {}
    prev = None
    for pt in curve:
        eq = pt.get("equity")
        if eq is None:
            continue
        if prev is not None and prev > 0:
            out[pt["date"]] = (eq - prev) / prev
        prev = eq
    return out


def compute_parity_report(strategy_curve, baseline_curve, *,
                          baseline_report_id=None, n_bootstrap=10000, rng=None):
    """Compare two equity curves with a paired bootstrap on daily-return diffs.

    Returns a dict suitable for storing in `metrics_json["parity"]`:
        {available, passes, ci_lower, ci_upper, mean_diff, n_days,
         baseline_report_id, [reason]}
    `available=False` when either curve is empty.
    """
    if not strategy_curve or not baseline_curve:
        return {"available": False, "reason": "missing equity curve",
                "baseline_report_id": baseline_report_id}

    s = _curve_to_daily_returns(strategy_curve)
    b = _curve_to_daily_returns(baseline_curve)
    common = sorted(set(s) & set(b))
    diffs = [s[d] - b[d] for d in common]

    result = bootstrap_beat_test(diffs, n_bootstrap=n_bootstrap, rng=rng)
    return {
        "available": True,
        "baseline_report_id": baseline_report_id,
        **result,
    }


def get_latest_backtest_parity(conn, strategy_id):
    """Return the parity dict from the most recent backtest report, or None.

    Looks at `metrics_json["parity"]` of the latest non-baseline report for
    `strategy_id`. Used by promote gates and by `is_beat_test_passing`.
    """
    import json as _json
    row = conn.execute(
        """SELECT id, metrics_json FROM backtest_reports
           WHERE strategy_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (strategy_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        m = _json.loads(row["metrics_json"])
    except (TypeError, _json.JSONDecodeError):
        return None
    parity = m.get("parity")
    if not parity:
        return None
    out = dict(parity)
    out["report_id"] = row["id"]
    return out


def is_beat_test_passing(conn, strategy_id):
    """True iff the latest backtest report has a parity verdict that passes."""
    p = get_latest_backtest_parity(conn, strategy_id)
    return bool(p and p.get("available") and p.get("passes"))

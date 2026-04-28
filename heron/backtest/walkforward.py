"""Walk-forward backtest runner.

Slides a (train, test) window across the full data range, runs an isolated
backtest on each test window, and aggregates the per-window results into a
parent report. The "train" window is reserved here as a future hook (param
fitting). For now we just freeze the strategy config across all windows —
the seam is the same, so swapping in a fitter later doesn't change the API.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import date, timedelta

from heron.backtest.fitter import fit_params
from heron.backtest.runner import run_strategy_backtest, _resolve_universe
from heron.journal.strategies import get_strategy
from heron.util import utc_now_iso

log = logging.getLogger(__name__)


def _add_months(d, months):
    """Add `months` calendar months to a date. Clips day to last of month."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last_day = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last_day))


def plan_windows(start, end, *, train_months, test_months, step_months):
    """Generate (train_start, train_end, test_start, test_end) date tuples.

    Walks forward by `step_months` until the test window would extend past `end`.
    All dates are inclusive ISO strings.
    """
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if isinstance(end, str):
        end = date.fromisoformat(end)
    if train_months < 0 or test_months <= 0 or step_months <= 0:
        raise ValueError("train_months>=0, test_months>0, step_months>0")

    windows = []
    cur = start
    while True:
        train_end = _add_months(cur, train_months) - timedelta(days=1) if train_months > 0 else cur - timedelta(days=1)
        test_start = train_end + timedelta(days=1)
        test_end = _add_months(test_start, test_months) - timedelta(days=1)
        if test_end > end:
            break
        windows.append((cur.isoformat(), train_end.isoformat(),
                        test_start.isoformat(), test_end.isoformat()))
        cur = _add_months(cur, step_months)
    return windows


def run_walkforward(conn, strategy_id, *, start, end,
                    train_months=6, test_months=3, step_months=3,
                    seed=0, initial_equity=100_000.0, seeder="synthetic",
                    axes=None, objective="sharpe"):
    """Run a walk-forward backtest. Returns parent report dict + child report ids.

    Each test window is a full `run_strategy_backtest` saved with a shared
    `walkforward_id`. We aggregate child metrics into a parent summary; the
    parent is also saved as a backtest_report row so it appears in /backtests.

    `axes`: optional dict {axis: [values, ...]} (same shape as sweep). When
    provided, each train window runs the grid in-memory and the winning
    combo (by `objective`: 'sharpe' default, 'total_return', 'win_rate',
    'avg_trade_pnl') is locked into the test window. When None or empty,
    behaves as before (config frozen across all windows).
    """
    s = get_strategy(conn, strategy_id)
    if not s:
        raise ValueError(f"Strategy {strategy_id!r} not found")
    universe = _resolve_universe(s)

    windows = plan_windows(start, end,
                           train_months=train_months,
                           test_months=test_months,
                           step_months=step_months)
    if not windows:
        raise ValueError(
            f"No walk-forward windows fit in {start}..{end} with "
            f"train={train_months}m, test={test_months}m. Widen the window or shrink train/test."
        )

    wf_id = secrets.token_hex(6)
    log.info("walkforward %s id=%s windows=%d", strategy_id, wf_id, len(windows))

    children = []
    aggregate_trades = []
    aggregate_equity = []
    cumulative_equity = initial_equity
    fit_enabled = bool(axes)
    for i, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
        # Fit on the train window if axes were provided. Train windows with
        # train_months=0 are degenerate (tr_s > tr_e); skip fitting in that case.
        locked_overrides = {}
        fit_summary = None
        if fit_enabled and tr_s <= tr_e:
            try:
                fit = fit_params(
                    conn, strategy_id, axes,
                    start=tr_s, end=tr_e, seed=seed,
                    initial_equity=initial_equity, seeder=seeder,
                    objective=objective,
                )
                locked_overrides = fit["overrides"]
                fit_summary = {
                    "objective": fit["objective"],
                    "score": fit["score"],
                    "train_metrics": fit["metrics"],
                    "n_combos": len(fit["candidates"]),
                }
            except ValueError as e:
                log.warning("walkforward window %d fit failed (%s); using defaults", i, e)

        try:
            res = run_strategy_backtest(
                conn, strategy_id,
                start=te_s, end=te_e,
                seed=seed, initial_equity=cumulative_equity,
                save=True, seeder=seeder,
                config_overrides=locked_overrides or None,
                as_of=(te_e + "T23:59:59Z") if len(te_e) == 10 else te_e,
            )
        except ValueError as e:
            log.warning("walkforward window %d failed: %s", i, e)
            continue

        # Tag the saved report with the walkforward_id and the locked overrides.
        if locked_overrides or fit_summary:
            row = conn.execute(
                "SELECT params_json FROM backtest_reports WHERE id=?",
                (res["report_id"],),
            ).fetchone()
            try:
                cur_params = json.loads(row["params_json"]) if row and row["params_json"] else {}
            except (TypeError, json.JSONDecodeError):
                cur_params = {}
            cur_params["walkforward_locked_overrides"] = locked_overrides
            if fit_summary:
                cur_params["walkforward_fit"] = fit_summary
            conn.execute(
                "UPDATE backtest_reports SET walkforward_id=?, params_json=? WHERE id=?",
                (wf_id, json.dumps(cur_params, default=str), res["report_id"]),
            )
        else:
            conn.execute(
                "UPDATE backtest_reports SET walkforward_id=? WHERE id=?",
                (wf_id, res["report_id"]),
            )
        conn.commit()

        children.append({
            "window_index": i,
            "train_start": tr_s, "train_end": tr_e,
            "test_start": te_s, "test_end": te_e,
            "report_id": res["report_id"],
            "metrics": res["metrics"],
            "locked_overrides": locked_overrides,
            "fit": fit_summary,
        })
        for t in res.get("trades", []):
            aggregate_trades.append(t)
        # Stitch equity curves: shift each child curve to start where the prev one ended.
        child_curve = res.get("equity_curve", [])
        if child_curve:
            scale = cumulative_equity / child_curve[0]["equity"] if child_curve[0]["equity"] else 1.0
            for pt in child_curve:
                aggregate_equity.append({
                    "date": pt["date"],
                    "equity": round(pt["equity"] * scale, 2),
                })
            cumulative_equity = aggregate_equity[-1]["equity"]

    if not children:
        raise ValueError("All walkforward windows produced errors; nothing to aggregate.")

    # Aggregate metrics.
    n_trades = sum(c["metrics"]["n_trades"] for c in children)
    n_wins = sum(c["metrics"]["n_wins"] for c in children)
    n_losses = sum(c["metrics"]["n_losses"] for c in children)
    total_pnl = sum(t.get("net_pnl", 0.0) or 0.0 for t in aggregate_trades)
    total_fees = sum(c["metrics"]["total_fees"] for c in children)
    final_equity = cumulative_equity
    total_return = (final_equity - initial_equity) / initial_equity if initial_equity else 0.0
    win_rate = (n_wins / n_trades) if n_trades else 0.0
    avg_pnl = (total_pnl / n_trades) if n_trades else 0.0
    # Max DD across the stitched curve.
    peak = initial_equity
    max_dd = 0.0
    for pt in aggregate_equity:
        peak = max(peak, pt["equity"])
        dd = (pt["equity"] - peak) / peak if peak else 0.0
        max_dd = min(max_dd, dd)

    parent_metrics = {
        "n_trades": n_trades,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "total_return": round(total_return, 4),
        "avg_trade_pnl": round(avg_pnl, 2),
        "max_drawdown": round(max_dd, 4),
        "sharpe": None,
        "total_fees": round(total_fees, 2),
        "n_windows": len(children),
    }

    # Save the parent as a backtest_report. Distinguish via params_json + walkforward_id.
    parent_params = {
        "walkforward": True,
        "train_months": train_months,
        "test_months": test_months,
        "step_months": step_months,
        "n_windows": len(children),
        "universe": universe,
        "seeder": seeder,
        "child_report_ids": [c["report_id"] for c in children],
        "axes": axes or None,
        "objective": objective if fit_enabled else None,
        "per_window_locked": [
            {"window_index": c["window_index"],
             "test_start": c["test_start"], "test_end": c["test_end"],
             "overrides": c["locked_overrides"]}
            for c in children
        ] if fit_enabled else None,
    }
    now = utc_now_iso()
    cur = conn.execute(
        """INSERT INTO backtest_reports
           (strategy_id, start_date, end_date, params_json, seed,
            n_trades, total_return, win_rate, sharpe, max_drawdown, avg_trade_pnl,
            metrics_json, trades_json, contaminated, contamination_notes,
            walkforward_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)""",
        (strategy_id, start, end, json.dumps(parent_params), seed,
         n_trades, total_return, win_rate, None, max_dd, avg_pnl,
         json.dumps({**parent_metrics, "equity_curve": aggregate_equity}),
         json.dumps(aggregate_trades), wf_id, now),
    )
    parent_id = cur.lastrowid
    conn.commit()

    return {
        "walkforward_id": wf_id,
        "parent_report_id": parent_id,
        "children": children,
        "metrics": parent_metrics,
        "equity_curve": aggregate_equity,
        "windows": windows,
    }


def list_walkforward_children(conn, walkforward_id):
    """Return ordered child reports for a walkforward run (excludes parent).

    Each row dict gains a `locked_overrides` key (parsed from params_json)
    so templates can render the per-window lock without re-parsing JSON.
    """
    rows = conn.execute(
        """SELECT * FROM backtest_reports
           WHERE walkforward_id=?
             AND (json_extract(params_json, '$.walkforward') IS NULL
                  OR json_extract(params_json, '$.walkforward') != 1)
           ORDER BY start_date""",
        (walkforward_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            p = json.loads(d.get("params_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            p = {}
        d["locked_overrides"] = p.get("walkforward_locked_overrides") or {}
        out.append(d)
    return out

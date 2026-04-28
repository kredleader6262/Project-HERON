"""Parameter sweep across a strategy's tunable config axes.

A sweep takes a base config + a dict of {param_name: [values...]}, generates
the Cartesian product, runs a deterministic backtest for each combination, and
tags every saved report with a shared `sweep_id`. The dashboard renders the
sweep as a sortable matrix so the operator can pick a winner and fork.
"""

from __future__ import annotations

import itertools
import json
import logging
import secrets

from heron.backtest.runner import run_strategy_backtest, _resolve_universe
from heron.journal.strategies import get_strategy

log = logging.getLogger(__name__)

# Axes the operator can sweep. Adding a new axis just means it shows up in the form.
SWEEPABLE_AXES = (
    "surprise_threshold_pct",
    "stop_mult",
    "target_mult",
    "max_hold_days",
    "max_positions",
    "max_capital_pct",
    "min_conviction",
    "min_edge_bps",
    "min_hold_days",
)


def _coerce(value, ref):
    """Coerce a string like '1.5' to the type of `ref`."""
    if isinstance(ref, bool):
        return value.strip().lower() in ("1", "true", "yes", "y")
    if isinstance(ref, int) and not isinstance(ref, bool):
        return int(float(value))
    if isinstance(ref, float):
        return float(value)
    return value


def parse_axes(axis_specs, base_config):
    """Parse a list of strings like 'stop_mult=1.0,1.5,2.0' into {axis: [values]}.

    Coerces each value to the type of the corresponding key in `base_config`.
    Unknown axes raise ValueError.
    """
    out = {}
    for spec in axis_specs:
        if "=" not in spec:
            raise ValueError(f"Bad axis spec {spec!r}; expected 'key=v1,v2,...'.")
        key, _, vals = spec.partition("=")
        key = key.strip()
        if key not in base_config:
            raise ValueError(f"Axis {key!r} not present in strategy config.")
        ref = base_config[key]
        parts = [p.strip() for p in vals.split(",") if p.strip()]
        if not parts:
            raise ValueError(f"Axis {key!r} has no values.")
        out[key] = [_coerce(p, ref) for p in parts]
    return out


def expand_grid(axes):
    """Cartesian product of {axis: [values]} -> list of dict overrides."""
    if not axes:
        return [{}]
    keys = list(axes.keys())
    values = [axes[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def run_sweep(conn, strategy_id, axes, *, start, end, seed=0,
              initial_equity=100_000.0, seeder="synthetic"):
    """Run a Cartesian sweep. Returns sweep_id + per-combo summaries.

    `axes` is {axis: [values]}. Each combo is run as a separate backtest with
    the override applied on top of the strategy's stored config. We do NOT
    persist a fork strategy per combo — sweep reports are tied to the parent
    strategy_id and distinguished by params_json.
    """
    s = get_strategy(conn, strategy_id)
    if not s:
        raise ValueError(f"Strategy {strategy_id!r} not found")

    universe = _resolve_universe(s)
    base_cfg = {}
    if s["config"]:
        try:
            base_cfg = json.loads(s["config"])
        except (TypeError, json.JSONDecodeError):
            base_cfg = {}

    combos = expand_grid(axes)
    if len(combos) > 50:
        raise ValueError(
            f"Sweep would run {len(combos)} backtests; cap is 50. "
            f"Reduce axes or values."
        )

    sweep_id = secrets.token_hex(6)
    log.info("sweep %s id=%s combos=%d", strategy_id, sweep_id, len(combos))

    summaries = []
    for combo in combos:
        try:
            res = run_strategy_backtest(
                conn, strategy_id,
                start=start, end=end, seed=seed,
                initial_equity=initial_equity, save=True, seeder=seeder,
                config_overrides=combo,
            )
        except ValueError as e:
            log.warning("sweep combo %s failed: %s", combo, e)
            continue

        # Tag the report with sweep_id and the override-only params for easy reading.
        params_for_sweep = json.dumps({"sweep_overrides": combo,
                                       "base_strategy": strategy_id,
                                       "universe": universe,
                                       "seeder": seeder})
        conn.execute(
            "UPDATE backtest_reports SET sweep_id=?, params_json=? WHERE id=?",
            (sweep_id, params_for_sweep, res["report_id"]),
        )
        conn.commit()

        summaries.append({
            "report_id": res["report_id"],
            "overrides": combo,
            "metrics": res["metrics"],
        })

    if not summaries:
        raise ValueError("All sweep combos failed; nothing saved.")

    return {
        "sweep_id": sweep_id,
        "strategy_id": strategy_id,
        "axes": axes,
        "n_combos": len(combos),
        "n_saved": len(summaries),
        "summaries": summaries,
    }


def get_sweep_reports(conn, sweep_id):
    """Return all reports for a sweep_id, ordered by report id."""
    rows = conn.execute(
        "SELECT * FROM backtest_reports WHERE sweep_id=? ORDER BY id",
        (sweep_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_sweeps(conn, *, limit=50):
    """List distinct sweeps with summary counts."""
    rows = conn.execute(
        """SELECT sweep_id, strategy_id, COUNT(*) AS n_reports,
                  MIN(created_at) AS started_at,
                  MAX(total_return) AS best_return
           FROM backtest_reports
           WHERE sweep_id IS NOT NULL
           GROUP BY sweep_id, strategy_id
           ORDER BY started_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]

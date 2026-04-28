"""High-level backtest orchestration shared by CLI and dashboard.

Wraps engine.run_backtest with the boring glue: load the strategy, resolve its
universe, fetch bars, generate candidates, run, save. Both `heron backtest run`
and the `/strategy/<id>/backtest` web route call into here so behaviour stays
identical across surfaces.
"""

import json
import logging

from heron.backtest.engine import run_backtest
from heron.backtest.report import save_report
from heron.backtest.seeders import synthetic_pead_candidates, real_pead_candidates
from heron.data.cache import get_bars
from heron.journal.strategies import get_strategy
from heron.strategy.pead import PEADStrategy, PEAD_UNIVERSE

log = logging.getLogger(__name__)


def _resolve_universe(strategy_row, *, conn=None, as_of=None):
    """Pull the configured universe off a strategy row, falling back to PEAD's.

    Strategy.config is JSON in the journal; UI form posts a comma-separated
    string under "universe", and the engine wants a list. Handle both shapes.

    When `conn` and `as_of` are both provided, prefers the most recent
    `universe_snapshots` row with snapshot_date <= as_of (point-in-time
    universe membership). Falls back to the strategy's config or PEAD_UNIVERSE
    when no snapshot covers `as_of`.
    """
    if conn is not None and as_of is not None:
        as_of_date = as_of[:10] if len(as_of) >= 10 else as_of
        latest = conn.execute(
            "SELECT MAX(snapshot_date) FROM universe_snapshots WHERE snapshot_date <= ?",
            (as_of_date,),
        ).fetchone()
        snap_date = latest[0] if latest else None
        if snap_date:
            rows = conn.execute(
                "SELECT ticker FROM universe_snapshots WHERE snapshot_date=? ORDER BY ticker",
                (snap_date,),
            ).fetchall()
            tickers = [r[0] for r in rows]
            if tickers:
                return tickers
    cfg_raw = strategy_row["config"] if "config" in strategy_row.keys() else None
    if not cfg_raw:
        return list(PEAD_UNIVERSE)
    try:
        cfg = json.loads(cfg_raw)
    except (TypeError, json.JSONDecodeError):
        return list(PEAD_UNIVERSE)
    u = cfg.get("universe") or PEAD_UNIVERSE
    if isinstance(u, str):
        u = [t.strip().upper() for t in u.split(",") if t.strip()]
    return list(u) or list(PEAD_UNIVERSE)


def run_strategy_backtest(conn, strategy_id, *, start=None, end=None,
                          seed=0, initial_equity=100_000.0, save=True,
                          seeder="synthetic", surprise_threshold=5.0,
                          config_overrides=None, as_of=None):
    """Run a deterministic backtest for a journaled strategy.

    `seeder`:
      - "synthetic": generate fake earnings surprises from cached bars (always works).
      - "real": pull cached `earnings_events` rows (run `heron data earnings fetch` first).
    `config_overrides`: optional dict layered on top of the strategy's stored
      config for this run only. Used by param sweeps; nothing is persisted to
      the strategy row.
    `as_of`: ISO timestamp. When set with seeder="real", the seeder uses only
      earnings values that were known at `as_of` (PIT replay). When None,
      defaults to end-of-window so a backtest never sees post-window restatements.
      Ignored by seeder="synthetic" (synthetic data has no restatement history).

    Returns the engine result dict augmented with `report_id` (None if save=False),
    `universe` (list of tickers actually used), and `seeder` (name used).
    Raises ValueError if the strategy doesn't exist, no cached bars cover the
    requested window, or seeder="real" with zero cached events.
    """
    s = get_strategy(conn, strategy_id)
    if not s:
        raise ValueError(f"Strategy {strategy_id!r} not found")

    if as_of is None and end is not None:
        # Default PIT cutoff = end-of-window. Use end-of-day so an event_date
        # equal to `end` is still included (cf. `get_bars` end handling).
        as_of = (end + "T23:59:59Z") if len(end) == 10 else end

    universe = _resolve_universe(s, conn=conn, as_of=as_of)

    bars = []
    for ticker in universe:
        bars.extend(get_bars(conn, ticker, "1Day", start=start, end=end))
    if not bars:
        raise ValueError(
            f"No cached bars for {strategy_id} universe={universe}. "
            f"Run `heron data today --days 200` first."
        )

    if seeder == "real":
        cands = real_pead_candidates(
            conn, universe=universe, start=start, end=end,
            surprise_threshold=surprise_threshold, as_of=as_of,
        )
        if not cands:
            raise ValueError(
                "No cached earnings events for this universe/window. "
                "Run `heron data earnings fetch --start ... --end ...` first."
            )
    elif seeder == "synthetic":
        cands = synthetic_pead_candidates(bars, universe=universe, seed=seed,
                                          surprise_threshold=surprise_threshold)
    else:
        raise ValueError(f"Unknown seeder {seeder!r}; expected 'synthetic' or 'real'.")

    log.info("backtest %s: %d bars, %d candidates (seeder=%s)",
             strategy_id, len(bars), len(cands), seeder)

    cfg = {}
    if s["config"]:
        try:
            cfg = json.loads(s["config"])
        except (TypeError, json.JSONDecodeError):
            cfg = {}
    cfg["universe"] = universe
    if config_overrides:
        cfg.update(config_overrides)

    strat = PEADStrategy(strategy_id=strategy_id, config=cfg, is_llm_variant=False)
    result = run_backtest(strat, bars, cands,
                          start_date=start, end_date=end,
                          initial_equity=initial_equity, seed=seed)
    result["universe"] = universe
    result["seeder"] = seeder
    if as_of is not None:
        result.setdefault("params", {})["as_of"] = as_of

    report_id = None
    if save:
        report_id = save_report(conn, result)
    result["report_id"] = report_id
    return result


def spy_benchmark_curve(conn, start, end, *, initial=100_000.0):
    """Return a buy-and-hold equity curve for SPY scaled to `initial`.

    List of {"date": "YYYY-MM-DD", "equity": float}. Empty if SPY isn't cached.
    """
    bars = get_bars(conn, "SPY", "1Day", start=start, end=end)
    if not bars:
        return []
    first_close = bars[0]["close"]
    if not first_close:
        return []
    return [
        {"date": b["ts"][:10], "equity": round(initial * (b["close"] / first_close), 2)}
        for b in bars
    ]


def drawdown_curve(equity_curve):
    """Compute peak-to-trough drawdown series from an equity curve.

    Returns list of {"date", "dd_pct"} where dd_pct is non-positive (0 at new peaks).
    """
    out = []
    peak = None
    for pt in equity_curve:
        eq = pt["equity"]
        peak = eq if peak is None else max(peak, eq)
        dd = 0.0 if peak <= 0 else (eq - peak) / peak
        out.append({"date": pt["date"], "dd_pct": round(dd, 6)})
    return out


def find_baseline_report(conn, strategy_id, start, end):
    """Find a saved baseline backtest matching this strategy's window.

    Returns Row or None. Match is exact on (start_date, end_date) — the user
    is expected to use 'Run baseline backtest' to align if missing.
    """
    baseline_id = f"{strategy_id}_baseline"
    return conn.execute(
        """SELECT * FROM backtest_reports
           WHERE strategy_id=? AND start_date=? AND end_date=?
           ORDER BY created_at DESC LIMIT 1""",
        (baseline_id, start, end),
    ).fetchone()

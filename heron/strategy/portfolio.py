"""Portfolio allocator (B1) — per-strategy capital budgets at portfolio level.

Per-strategy `max_capital_pct` is necessary but not sufficient: blind to confidence
(parity verdict), drawdown state, and crowding (tag overlap). This module decides
how much of the total budget each active strategy gets *given the others*.

Pure functions. No DB writes. Deterministic.
"""

import json
import math

from heron.config import PORTFOLIO_CONFIG
from heron.journal.strategies import list_strategies
from heron.journal.trades import list_trades


# ── State helpers ──────────────────────────────────────────

def _strategy_drawdown(conn, strategy_id, mode):
    """Crude drawdown: (current_pnl - peak_pnl) / max(peak_capital, 1).

    Walks closed trades chronologically tracking running P&L peak.
    Returns negative or zero. Never positive (peak is the max running P&L).
    """
    rows = list_trades(conn, strategy_id=strategy_id, mode=mode)
    closed = [r for r in rows if r["pnl"] is not None]
    if not closed:
        return 0.0
    closed.sort(key=lambda r: r["close_filled_at"] or r["created_at"])
    running = 0.0
    peak = 0.0
    for r in closed:
        running += r["pnl"] or 0.0
        if running > peak:
            peak = running
    return running - peak  # ≤ 0


def _parity_factor(strategy_row, conn):
    """Map latest backtest parity verdict to a confidence multiplier.

    pass → 1.0, fail → 0.5, missing → 0.7 (neutral / unproven).
    """
    row = conn.execute(
        """SELECT metrics_json FROM backtest_reports
           WHERE strategy_id=? ORDER BY created_at DESC LIMIT 1""",
        (strategy_row["id"],),
    ).fetchone()
    if not row or not row["metrics_json"]:
        return 0.7
    try:
        m = json.loads(row["metrics_json"])
    except (ValueError, TypeError):
        return 0.7
    parity = m.get("parity") or {}
    if not parity.get("available"):
        return 0.7
    return 1.0 if parity.get("passes") else 0.5


def _strategy_tags(strategy_row):
    """Parse tags JSON from a strategy row. Returns list of strings."""
    raw = None
    if "tags" in strategy_row.keys():
        raw = strategy_row["tags"]
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(t) for t in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


# ── Allocator ──────────────────────────────────────────────

def compute_allocations(conn, equity, *, mode):
    """Return `{strategy_id: capital_pct}` for active strategies in `mode`.

    Pipeline per strategy:
      1. base = min(strategy.max_capital_pct, MAX_PER_STRATEGY)
      2. * parity_factor (1.0 pass / 0.5 fail / 0.7 missing)
      3. * drawdown_factor: linear taper from drawdown_budget_pct down to 0
      4. crowding cap: per-tag sum ≤ TAG_BUDGET; scale offending strategies down
      5. global cap: sum ≤ MAX_TOTAL_EXPOSURE; scale all proportionally if exceeded

    Returns a `{strategy_id: float}` dict (always present for every active
    strategy; 0.0 if fully throttled).
    """
    states = ("PAPER", "LIVE") if mode == "live" else ("PAPER",)
    rows = [s for s in list_strategies(conn) if s["state"] in states]
    if not rows:
        return {}

    cfg = PORTFOLIO_CONFIG
    max_total = float(cfg.get("max_total_exposure", 0.80))
    max_per_strategy = float(cfg.get("max_per_strategy", 0.30))
    tag_budget = float(cfg.get("tag_budget", 0.30))

    raw = {}
    tags_by_strat = {}
    for s in rows:
        base = min(float(s["max_capital_pct"] or 0.15), max_per_strategy)
        pf = _parity_factor(s, conn)
        dd = _strategy_drawdown(conn, s["id"], mode)
        budget = float(s["drawdown_budget_pct"] or 0.05) * float(equity or 1.0)
        if budget <= 0:
            df = 1.0
        else:
            # dd ≤ 0; df = 1 + dd/budget, clamped to [0, 1].
            df = max(0.0, min(1.0, 1.0 + dd / budget))
        raw[s["id"]] = base * pf * df
        tags_by_strat[s["id"]] = _strategy_tags(s)

    # Crowding cap: any single tag's combined allocation ≤ tag_budget.
    if tag_budget > 0:
        tag_sums = {}
        for sid, alloc in raw.items():
            for t in tags_by_strat[sid]:
                tag_sums[t] = tag_sums.get(t, 0.0) + alloc
        for tag, total in tag_sums.items():
            if total > tag_budget and total > 0:
                scale = tag_budget / total
                for sid in raw:
                    if tag in tags_by_strat[sid]:
                        raw[sid] *= scale

    # Global cap.
    total = sum(raw.values())
    if total > max_total and total > 0:
        scale = max_total / total
        raw = {k: v * scale for k, v in raw.items()}

    return {k: round(v, 6) for k, v in raw.items()}


def get_strategy_budget(conn, strategy_id, equity, *, mode):
    """Return the capital_pct allocated to one strategy, or 0.0 if not active."""
    return compute_allocations(conn, equity, mode=mode).get(strategy_id, 0.0)


# ── Optional B3 — pairwise correlation ────────────────────
# Kept lightweight; allocator does not consume yet.

def compute_correlations(conn, *, mode, lookback_days=60):
    """Pairwise Pearson correlation of daily PnL across strategies.

    Returns `{(a, b): rho}` for strategies with ≥ 5 overlapping days.
    """
    from collections import defaultdict
    from heron.util import trading_day_of_iso

    rows = [s for s in list_strategies(conn) if s["state"] in ("PAPER", "LIVE")]
    daily = defaultdict(dict)  # strategy_id -> {date: pnl}
    for s in rows:
        for t in list_trades(conn, strategy_id=s["id"], mode=mode):
            if t["pnl"] is None or not t["close_filled_at"]:
                continue
            day = trading_day_of_iso(t["close_filled_at"])
            daily[s["id"]][day] = daily[s["id"]].get(day, 0.0) + t["pnl"]

    out = {}
    ids = sorted(daily.keys())
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            common = sorted(set(daily[a]) & set(daily[b]))
            if len(common) < 5:
                continue
            xs = [daily[a][d] for d in common]
            ys = [daily[b][d] for d in common]
            n = len(xs)
            mx, my = sum(xs) / n, sum(ys) / n
            num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
            dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
            dy = math.sqrt(sum((y - my) ** 2 for y in ys))
            if dx == 0 or dy == 0:
                continue
            out[(a, b)] = round(num / (dx * dy), 4)
    return out

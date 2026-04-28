"""Pre-trade risk checks.

Every check returns (ok: bool, reason: str). All must pass before any entry.
These enforce: wash-sale, PDT, exposure limits, daily loss cap, position limits,
and stale-quote kill switch.

The monthly review gate (Project-HERON.md §11) blocks PROMOTIONS, not entries
— enforced in the dashboard's promote route via `is_review_current`, not here.

See Project-HERON.md Section 5 and trading-safety.instructions.md.
"""

from heron.config import QUOTE_STALE_SECONDS
from heron.journal.trades import (
    check_wash_sale, get_pdt_count, can_daytrade, list_trades,
)
from heron.journal.ops import log_event
from heron.util import trading_day_start_utc_iso


# ── Check Results ──────────────────────────────────

class CheckResult:
    __slots__ = ("ok", "reason")

    def __init__(self, ok, reason=""):
        self.ok = ok
        self.reason = reason

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return f"CheckResult(ok={self.ok}, reason={self.reason!r})"


def _pass(reason=""):
    return CheckResult(True, reason)


def _fail(reason):
    return CheckResult(False, reason)


# ── Individual Checks ──────────────────────────────

def check_wash_sale_risk(conn, ticker, mode="live"):
    """Reject entry if buying ticker (or family member) has active wash-sale lots.

    Wash-sale is a tax rule on real money; in `paper` mode the check is a no-op.
    """
    if mode == "paper":
        return _pass("Paper mode, wash-sale N/A")
    lots = check_wash_sale(conn, ticker)
    if lots:
        total_loss = sum(l["loss_amount"] for l in lots)
        return _fail(f"Wash-sale: {len(lots)} active lot(s) for {ticker} family, "
                     f"disallowed loss ${abs(total_loss):.2f}")
    return _pass()


def check_pdt_risk(conn, requires_same_day_exit=False, limit=3, mode="live"):
    """Reject entry if it would require a same-day exit and PDT limit is at cap.

    PDT is a broker regulation that only applies to live accounts.
    """
    if mode == "paper":
        return _pass("Paper mode, PDT N/A")
    if not requires_same_day_exit:
        return _pass("Swing trade, PDT N/A")
    if not can_daytrade(conn, limit=limit):
        count = get_pdt_count(conn, mode="live")
        return _fail(f"PDT: {count} day-trades in window, limit {limit}")
    return _pass()


def check_exposure(conn, entry_cost, equity, max_pct=0.80, mode=None):
    """Reject if total exposure within `mode` would exceed max_pct of equity.

    `mode` filters which open trades count toward exposure. Default None counts
    all modes (legacy behavior); pass an explicit "paper" or "live" so paper
    positions don't eat the live budget (or vice versa).
    """
    open_trades = list_trades(conn, open_only=True, mode=mode)
    current_exposure = sum(
        (t["fill_price"] or 0) * (t["fill_qty"] or 0) for t in open_trades
    )
    new_exposure = current_exposure + entry_cost
    limit = equity * max_pct
    if new_exposure > limit:
        return _fail(f"Exposure: ${new_exposure:.0f} would exceed {max_pct:.0%} of ${equity:.0f} "
                     f"(limit ${limit:.0f})")
    return _pass()


def check_position_count(conn, max_positions=3, mode=None):
    """Reject if max concurrent positions in `mode` would be exceeded."""
    open_trades = list_trades(conn, open_only=True, mode=mode)
    if len(open_trades) >= max_positions:
        return _fail(f"Position limit: {len(open_trades)} open, max {max_positions}")
    return _pass()


def check_daily_entries(conn, max_daily=3, mode=None):
    """Reject if max daily new entries (in `mode`) exceeded.

    "Today" = America/New_York trading day, not UTC. A trade entered at
    19:00 ET counts toward today, even though it's tomorrow in UTC.
    """
    today = trading_day_start_utc_iso()
    sql = "SELECT COUNT(*) FROM trades WHERE created_at >= ?"
    params = [today]
    if mode:
        sql += " AND mode = ?"
        params.append(mode)
    count = conn.execute(sql, params).fetchone()[0]
    if count >= max_daily:
        return _fail(f"Daily entry limit: {count} entries today, max {max_daily}")
    return _pass()


def check_daily_loss(conn, equity, max_pct=0.08, mode=None):
    """Reject if realized daily loss (in `mode`) exceeds max_pct of equity.

    "Today" = NY trading day. See `check_daily_entries`.
    """
    today = trading_day_start_utc_iso()
    sql = ("SELECT COALESCE(SUM(pnl), 0) FROM trades "
           "WHERE close_filled_at >= ? AND pnl IS NOT NULL")
    params = [today]
    if mode:
        sql += " AND mode = ?"
        params.append(mode)
    row = conn.execute(sql, params).fetchone()
    daily_pnl = row[0]
    limit = -abs(equity * max_pct)
    if daily_pnl < limit:
        return _fail(f"Daily loss: ${daily_pnl:.2f} exceeds {max_pct:.0%} of ${equity:.0f} "
                     f"(limit ${limit:.2f})")
    return _pass()


def check_single_trade_risk(entry_price, stop_price, qty, equity, max_pct=0.05):
    """Reject if potential loss on this trade exceeds max_pct of equity."""
    if not stop_price or not entry_price:
        return _fail("Missing entry or stop price")
    potential_loss = abs(entry_price - stop_price) * qty
    limit = equity * max_pct
    if potential_loss > limit:
        return _fail(f"Single-trade risk: ${potential_loss:.2f} loss exceeds "
                     f"{max_pct:.0%} of ${equity:.0f} (limit ${limit:.2f})")
    return _pass()


def check_quote_freshness(quote_age_seconds, max_age=None):
    """Reject if quote is stale. Defaults to QUOTE_STALE_SECONDS from config."""
    if max_age is None:
        max_age = QUOTE_STALE_SECONDS
    if quote_age_seconds > max_age:
        return _fail(f"Stale quote: {quote_age_seconds:.1f}s old, max {max_age}s")
    return _pass()


def check_system_mode(conn):
    """Reject all entries when global system mode is SAFE.

    DERISK / NORMAL pass here — DERISK affects sizing, not the gate.
    """
    from heron.strategy.policy import current_system_mode
    m = current_system_mode(conn)
    if m == "SAFE":
        return _fail("System mode SAFE: entries blocked")
    return _pass(f"System mode {m}")


def check_portfolio_exposure(conn, strategy_id, entry_cost, equity, mode=None):
    """Reject if entry would exceed this strategy's portfolio-level capital budget.

    Consults `compute_allocations` for the per-strategy slice. Falls back to
    pass-through when no `strategy_id` is provided, when the strategy isn't
    in the active allocation (PAPER/LIVE), or when there are no other active
    strategies — the legacy `check_exposure` is the backstop in those cases.
    """
    if not strategy_id or mode is None:
        return _pass("Portfolio cap: no strategy context")
    from heron.strategy.portfolio import compute_allocations
    allocs = compute_allocations(conn, equity, mode=mode)
    if strategy_id not in allocs:
        # Strategy isn't being managed at the portfolio level (probably not
        # in PAPER/LIVE yet). Defer to legacy check_exposure backstop.
        return _pass(f"Portfolio cap: {strategy_id} not in active allocation")
    budget_pct = allocs[strategy_id]
    if budget_pct <= 0:
        return _fail(f"Portfolio cap: strategy {strategy_id} fully throttled")
    open_trades = list_trades(conn, open_only=True, mode=mode)
    strat_exposure = sum(
        (t["fill_price"] or 0) * (t["fill_qty"] or 0)
        for t in open_trades if t["strategy_id"] == strategy_id
    )
    new_exposure = strat_exposure + entry_cost
    limit = equity * budget_pct
    if new_exposure > limit:
        return _fail(f"Portfolio cap: ${new_exposure:.0f} would exceed "
                     f"{budget_pct:.1%} budget for {strategy_id} (limit ${limit:.0f})")
    return _pass()


# ── Composite Pre-Trade Check ──────────────────────

def pre_trade_checks(conn, ticker, entry_price, stop_price, qty, equity,
                     quote_age_seconds, strategy_config=None,
                     requires_same_day_exit=False, mode="live",
                     strategy_id=None):
    """Run all pre-trade risk checks. Returns list of CheckResults.

    All checks run even if earlier ones fail — operator sees the full picture.
    `mode` ("paper" or "live") scopes per-mode counters so paper trades don't
    leak into live PDT/exposure/wash-sale state and vice versa.
    `strategy_id` enables the portfolio-level (B1) cap; omit for legacy callers.
    """
    cfg = strategy_config or {}
    max_positions = cfg.get("max_positions", 3)
    entry_cost = entry_price * qty

    checks = [
        ("system_mode", check_system_mode(conn)),
        ("wash_sale", check_wash_sale_risk(conn, ticker, mode=mode)),
        ("pdt", check_pdt_risk(conn, requires_same_day_exit, mode=mode)),
        ("exposure", check_exposure(conn, entry_cost, equity, mode=mode)),
        ("portfolio_cap", check_portfolio_exposure(conn, strategy_id, entry_cost, equity, mode=mode)),
        ("positions", check_position_count(conn, max_positions, mode=mode)),
        ("daily_entries", check_daily_entries(conn, mode=mode)),
        ("daily_loss", check_daily_loss(conn, equity, mode=mode)),
        ("single_trade", check_single_trade_risk(entry_price, stop_price, qty, equity)),
        ("quote_fresh", check_quote_freshness(quote_age_seconds)),
    ]

    failures = [(name, c) for name, c in checks if not c.ok]
    if failures:
        for name, c in failures:
            log_event(conn, f"risk_check_failed:{name}", c.reason,
                      severity="warn", source="strategy.risk")
    return checks

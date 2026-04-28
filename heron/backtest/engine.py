"""Deterministic backtest engine.

Replays cached bars day by day. For each day:
  1. Check open positions for exits (should_exit → close at simulated fill)
  2. Process candidates seeded for that date (screen → compute_levels → open)
  3. Advance to next day

All inputs sorted; no wall-clock calls beyond saving the report.
Running the same (strategy, bars, candidates, seed) twice yields identical trades.

Candidates must be pre-seeded by the caller as a list of dicts:
  [{"date": "2024-01-15", "ticker": "AAPL", "surprise_pct": 8.2,
    "announced_hours_ago": 12, "conviction": 0.7}, ...]

This keeps the engine free of Research-layer / LLM dependencies. The
caller (CLI, tests, or a future historical-news replayer) is responsible
for producing them deterministically.
"""

from collections import defaultdict

from heron.backtest.costs import apply_slippage, sell_fees, slippage_bps


def _bars_by_date(bars):
    """Group bars by ticker → list of (date, bar) sorted ascending."""
    by_ticker = defaultdict(list)
    for b in bars:
        # bar is a sqlite Row or dict
        ticker = b["ticker"]
        date = b["ts"][:10]
        by_ticker[ticker].append((date, b))
    for t in by_ticker:
        by_ticker[t].sort(key=lambda p: p[0])
    return by_ticker


def _atr(bars_window):
    """Simple N-day True Range average. bars_window: list of dicts with h/l/c."""
    if len(bars_window) < 2:
        return None
    trs = []
    for i in range(1, len(bars_window)):
        h = bars_window[i]["high"]
        l = bars_window[i]["low"]
        pc = bars_window[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def run_backtest(strategy, bars, candidates, *,
                 start_date=None, end_date=None,
                 initial_equity=100_000.0,
                 seed=0, atr_period=14):
    """Replay `strategy` over `bars`, entering on `candidates`.

    Returns dict with equity_curve, trades, params snapshot.
    Deterministic: same inputs → same output. The seed is recorded on the
    saved report; consumers (seeders, walk-forward) are responsible for using
    it to drive any stochastic input.
    """
    bars_by_ticker = _bars_by_date(bars)
    all_dates = sorted({d for series in bars_by_ticker.values() for d, _ in series})
    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
    if end_date:
        all_dates = [d for d in all_dates if d <= end_date]

    # Index candidates by date, deterministic order
    cands_by_date = defaultdict(list)
    for c in sorted(candidates, key=lambda x: (x["date"], x["ticker"])):
        cands_by_date[c["date"]].append(c)

    equity = initial_equity
    peak_equity = equity
    max_dd = 0.0
    open_positions = {}  # ticker → dict
    closed_trades = []
    equity_curve = []

    for date in all_dates:
        # ── Step 1: check exits for open positions ──
        to_close = []
        for ticker, pos in open_positions.items():
            series = bars_by_ticker.get(ticker, [])
            today = next((b for d, b in series if d == date), None)
            if not today:
                continue
            days_held = _count_trading_days(series, pos["entry_date"], date)
            current_price = today["close"]
            md = {"current_price": current_price, "days_held": days_held}
            # Simulate intraday: check stop/target against day's high/low
            trade_row = {
                "stop_price": pos["stop"],
                "target_price": pos["target"],
                "fill_price": pos["entry"],
                "filled_at": pos["entry_date"],
            }
            # Check intraday stop hit first (conservative)
            exit_price = None
            exit_reason = None
            if pos["stop"] is not None and today["low"] <= pos["stop"]:
                exit_price = pos["stop"]
                exit_reason = "stop"
            elif pos["target"] is not None and today["high"] >= pos["target"]:
                exit_price = pos["target"]
                exit_reason = "target"
            else:
                should_close, reason, close_price = strategy.should_exit(trade_row, md)
                if should_close and strategy.check_min_hold(days_held):
                    exit_price = close_price or current_price
                    exit_reason = reason

            if exit_price is not None:
                to_close.append((ticker, exit_price, exit_reason, days_held))

        for ticker, raw_exit_px, reason, days_held in to_close:
            pos = open_positions.pop(ticker)
            # Apply exit slippage + sell-side fees
            exit_px = apply_slippage(raw_exit_px, "sell")
            fees = sell_fees(pos["qty"], exit_px)
            gross_pnl = (exit_px - pos["entry"]) * pos["qty"]
            net_pnl = gross_pnl - fees
            equity += net_pnl
            closed_trades.append({
                "ticker": ticker,
                "entry_date": pos["entry_date"],
                "exit_date": date,
                "entry": pos["entry"],
                "exit": exit_px,
                "qty": pos["qty"],
                "gross_pnl": gross_pnl,
                "fees": fees,
                "net_pnl": net_pnl,
                "pnl_pct": net_pnl / (pos["entry"] * pos["qty"]) if pos["entry"] else 0,
                "reason": reason,
                "days_held": days_held,
            })

        # ── Step 2: consider new candidates ──
        todays_cands = cands_by_date.get(date, [])
        for cand in todays_cands:
            ticker = cand["ticker"]
            if ticker in open_positions:
                continue
            if len(open_positions) >= strategy.max_positions:
                break
            series = bars_by_ticker.get(ticker, [])
            # Need ATR window ending on `date`
            window = [b for d, b in series if d <= date][-(atr_period + 1):]
            if len(window) < atr_period + 1:
                continue
            atr = _atr(window)
            last_close = window[-1]["close"]
            md = {"last_close": last_close, "atr_14": atr}

            accept, _reason = strategy.screen_candidate(cand, md)
            if not accept:
                continue
            levels = strategy.compute_levels(ticker, md, equity)
            if not levels:
                continue
            entry_fill = apply_slippage(levels["entry"], "buy")
            open_positions[ticker] = {
                "entry": entry_fill,
                "stop": levels["stop"],
                "target": levels["target"],
                "qty": levels["qty"],
                "entry_date": date,
            }

        # ── Step 3: mark-to-market equity ──
        mtm = equity
        for ticker, pos in open_positions.items():
            series = bars_by_ticker.get(ticker, [])
            today = next((b for d, b in series if d == date), None)
            if today:
                mtm += (today["close"] - pos["entry"]) * pos["qty"]
        peak_equity = max(peak_equity, mtm)
        dd = (peak_equity - mtm) / peak_equity if peak_equity > 0 else 0
        max_dd = max(max_dd, dd)
        equity_curve.append({"date": date, "equity": mtm})

    # Force-close any remaining positions at final close
    if all_dates:
        final_date = all_dates[-1]
        for ticker, pos in list(open_positions.items()):
            series = bars_by_ticker.get(ticker, [])
            final_bar = next((b for d, b in series if d == final_date), None)
            if not final_bar:
                continue
            exit_px = apply_slippage(final_bar["close"], "sell")
            fees = sell_fees(pos["qty"], exit_px)
            gross_pnl = (exit_px - pos["entry"]) * pos["qty"]
            net_pnl = gross_pnl - fees
            equity += net_pnl
            days_held = _count_trading_days(series, pos["entry_date"], final_date)
            closed_trades.append({
                "ticker": ticker,
                "entry_date": pos["entry_date"],
                "exit_date": final_date,
                "entry": pos["entry"],
                "exit": exit_px,
                "qty": pos["qty"],
                "gross_pnl": gross_pnl,
                "fees": fees,
                "net_pnl": net_pnl,
                "pnl_pct": net_pnl / (pos["entry"] * pos["qty"]) if pos["entry"] else 0,
                "reason": "end_of_backtest",
                "days_held": days_held,
            })
        open_positions.clear()

    metrics = _compute_metrics(
        equity_curve, closed_trades, initial_equity, final_equity=equity,
        max_dd=max_dd,
    )

    return {
        "strategy_id": strategy.strategy_id,
        "start_date": all_dates[0] if all_dates else None,
        "end_date": all_dates[-1] if all_dates else None,
        "seed": seed,
        "initial_equity": initial_equity,
        "final_equity": equity,
        "equity_curve": equity_curve,
        "trades": closed_trades,
        "metrics": metrics,
        "params": dict(strategy.config),
        "slippage_bps": slippage_bps(),
    }


def _count_trading_days(series, start_date, end_date):
    return sum(1 for d, _ in series if start_date <= d <= end_date) - 1


def _compute_metrics(equity_curve, trades, initial, final_equity, max_dd):
    n = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    total_ret = (final_equity - initial) / initial if initial else 0

    # Daily returns from equity curve for Sharpe
    sharpe = None
    if len(equity_curve) > 2:
        rets = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]["equity"]
            cur = equity_curve[i]["equity"]
            if prev:
                rets.append((cur - prev) / prev)
        if rets:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / len(rets)
            std = var ** 0.5
            if std > 0:
                sharpe = (mean / std) * (252 ** 0.5)

    avg_pnl = sum(t["net_pnl"] for t in trades) / n if n else 0

    return {
        "n_trades": n,
        "total_return": total_ret,
        "win_rate": len(wins) / n if n else 0,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "avg_trade_pnl": avg_pnl,
        "avg_win": sum(t["net_pnl"] for t in wins) / len(wins) if wins else 0,
        "avg_loss": sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "total_fees": sum(t["fees"] for t in trades),
    }

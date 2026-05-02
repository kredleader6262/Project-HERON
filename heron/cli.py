"""HERON CLI — data, journal, and demo commands."""

import json
import os
import socket
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import click

# Force UTF-8 stdout on Windows (cp1252 can't print many unicode chars in headlines)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # Older Python or redirected stream — fall back to system encoding
        pass


@click.group()
def cli():
    """HERON — Hypothesis-driven Execution with Research, Observation, and Notation."""
    from heron.logging_setup import setup_logging
    setup_logging()


def _dashboard_lan_urls(port):
    ips = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ips.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        ips.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    seen = []
    for ip in ips:
        if ip and not ip.startswith("127.") and ip not in seen:
            seen.append(ip)
    return [f"http://{ip}:{port}" for ip in seen]


def _echo_dashboard_urls(host, port):
    if host == "0.0.0.0":
        urls = _dashboard_lan_urls(port)
        click.echo(f"Starting HERON dashboard on all interfaces, port {port}")
        click.echo(f"Accessible locally via: http://127.0.0.1:{port}")
        if urls:
            click.echo(f"Accessible via: {urls[0]}")
            for url in urls[1:]:
                click.echo(f"Other detected address: {url}")
        else:
            click.echo("Could not detect a LAN IP. Run `ipconfig` and use this computer's IPv4 address.")
        return
    click.echo(f"Starting HERON dashboard at http://{host}:{port}")
    if host in ("127.0.0.1", "localhost"):
        click.echo("Phone/LAN access needs `--lan` or `--host 0.0.0.0`.")


@cli.group()
def data():
    """Data layer commands."""
    pass


@data.command()
@click.option("--days", default=5, help="Number of days of bars to fetch.")
@click.option("--timeframe", default="1Day", help="Bar timeframe.")
@click.option("--news/--no-news", "fetch_news", default=True, help="Also fetch news.")
def today(days, timeframe, fetch_news):
    """Fetch and display today's bars and headlines for the watchlist.

    This is the Milestone 1 demo command.
    """
    from heron.config import WATCHLIST
    from heron.data import DataFeed

    feed = DataFeed()
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")

    click.echo(f"=== HERON Data — {now.strftime('%Y-%m-%d %H:%M UTC')} ===\n")

    # -- Bars --
    click.echo(f"--- OHLCV ({timeframe}, last {days} days) ---\n")
    try:
        all_bars = feed.fetch_watchlist_bars(timeframe=timeframe, start=start, end=end)
        for ticker in WATCHLIST:
            bars = all_bars.get(ticker, [])
            if not bars:
                click.echo(f"  {ticker}: no data")
                continue
            latest = bars[-1]
            click.echo(
                f"  {ticker:6s}  O:{latest['open']:>9.2f}  H:{latest['high']:>9.2f}  "
                f"L:{latest['low']:>9.2f}  C:{latest['close']:>9.2f}  "
                f"V:{latest['volume']:>12,.0f}  ({latest['ts'][:10]})"
            )
    except Exception as e:
        click.echo(f"  [bars error: {e}]")

    # -- News --
    if fetch_news:
        click.echo(f"\n--- Headlines (last {days} days) ---\n")
        try:
            articles = feed.fetch_watchlist_news(start=start, end=end)
            if not articles:
                click.echo("  No articles found.")
            for a in articles[:25]:  # Cap display
                tickers = json.loads(a["tickers"]) if a["tickers"] else []
                ticker_str = ",".join(tickers[:3]) if tickers else "—"
                click.echo(
                    f"  [{a['source']:15s}] [{ticker_str:12s}] "
                    f"w={a['credibility_weight']:.1f}  {a['headline'][:100]}"
                )
            if len(articles) > 25:
                click.echo(f"  ... and {len(articles) - 25} more")
        except Exception as e:
            click.echo(f"  [news error: {e}]")

    click.echo(f"\n  Cache: {feed.conn.execute('SELECT COUNT(*) FROM ohlcv').fetchone()[0]} bars, "
               f"{feed.conn.execute('SELECT COUNT(*) FROM news_articles').fetchone()[0]} articles")
    feed.close()


@data.command()
@click.argument("ticker")
def quote(ticker):
    """Get latest quote for a ticker with staleness check."""
    from heron.data import DataFeed

    feed = DataFeed()
    try:
        q = feed.get_quote(ticker.upper())
        stale_flag = " ⚠ STALE" if q["is_stale"] else ""
        click.echo(f"{q['ticker']}  bid:{q['bid']}  ask:{q['ask']}  "
                   f"age:{q['age_seconds']}s{stale_flag}")
    except Exception as e:
        click.echo(f"[quote error: {e}]")
    finally:
        feed.close()


@data.group("earnings")
def data_earnings():
    """Earnings calendar / surprise data (Finnhub)."""
    pass


@data_earnings.command("fetch")
@click.option("--start", required=True, help="YYYY-MM-DD inclusive.")
@click.option("--end", required=True, help="YYYY-MM-DD inclusive.")
@click.option("--universe", default=None,
              help="Comma-separated tickers; default = WATCHLIST.")
def data_earnings_fetch(start, end, universe):
    """Fetch and cache earnings events from Finnhub for [start, end]."""
    from heron.config import WATCHLIST
    from heron.data.cache import get_conn, init_db
    from heron.data.earnings import fetch_and_cache

    tickers = [t.strip().upper() for t in universe.split(",")] if universe else list(WATCHLIST)
    conn = get_conn()
    init_db(conn)
    try:
        n = fetch_and_cache(conn, start, end, universe=tickers)
        click.echo(f"Cached {n} earnings events for {len(tickers)} tickers, {start} → {end}.")
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    finally:
        conn.close()


@data_earnings.command("list")
@click.option("--start", default=None)
@click.option("--end", default=None)
@click.option("--ticker", default=None)
@click.option("--min-surprise", default=None, type=float,
              help="Filter |surprise_pct| >= this.")
@click.option("--limit", default=50)
def data_earnings_list(start, end, ticker, min_surprise, limit):
    """List cached earnings events."""
    from heron.data.cache import get_conn, init_db
    from heron.data.earnings import get_earnings_events

    conn = get_conn()
    init_db(conn)
    try:
        rows = get_earnings_events(
            conn,
            start=start, end=end,
            tickers=[ticker.upper()] if ticker else None,
            min_abs_surprise=min_surprise,
        )
        if not rows:
            click.echo("(no events)")
            return
        for r in rows[:limit]:
            s = r["surprise_pct"]
            surp = f"{s:+.2f}%" if s is not None else "NA"
            click.echo(f"  {r['event_date']}  {r['ticker']:<6}  "
                       f"{(r['event_time'] or '?'):>3}  "
                       f"actual={r['eps_actual']}  est={r['eps_estimate']}  "
                       f"surprise={surp}")
        if len(rows) > limit:
            click.echo(f"... {len(rows) - limit} more.")
    finally:
        conn.close()


@data.group("universe")
def data_universe():
    """Manage point-in-time universe snapshots."""
    pass


@data_universe.command("snapshot")
@click.option("--date", "snapshot_date", required=True,
              help="YYYY-MM-DD — the date this universe was current.")
@click.option("--tickers", required=True,
              help="Comma-separated list of tickers in the universe at that date.")
@click.option("--note", default=None, help="Optional human note.")
def data_universe_snapshot(snapshot_date, tickers, note):
    """Record a point-in-time universe membership.

    The backtest runner prefers the most recent snapshot ≤ as_of when
    resolving a strategy's universe, so this enables survivorship-aware
    replay without paid data.
    """
    from heron.data.cache import get_conn, init_db
    from heron.util import utc_now_iso

    syms = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not syms:
        click.echo("No tickers parsed from --tickers", err=True)
        raise SystemExit(1)
    now = utc_now_iso()
    conn = get_conn()
    init_db(conn)
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO universe_snapshots
               (snapshot_date, ticker, source, note, created_at)
               VALUES (?, ?, 'manual', ?, ?)""",
            [(snapshot_date, t, note, now) for t in syms],
        )
        conn.commit()
        click.echo(f"Stored {len(syms)} tickers for snapshot {snapshot_date}.")
    finally:
        conn.close()


@data_universe.command("list")
@click.option("--limit", default=20)
def data_universe_list(limit):
    """List recorded universe snapshots, newest first."""
    from heron.data.cache import get_conn, init_db
    conn = get_conn()
    init_db(conn)
    try:
        dates = conn.execute(
            "SELECT snapshot_date, COUNT(*) AS n FROM universe_snapshots "
            "GROUP BY snapshot_date ORDER BY snapshot_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        if not dates:
            click.echo("(no snapshots)")
            return
        for d in dates:
            tickers = [r[0] for r in conn.execute(
                "SELECT ticker FROM universe_snapshots WHERE snapshot_date=? ORDER BY ticker",
                (d["snapshot_date"],),
            ).fetchall()]
            click.echo(f"  {d['snapshot_date']}  ({d['n']:>3}) {', '.join(tickers[:8])}"
                       + (f" … +{len(tickers)-8} more" if len(tickers) > 8 else ""))
    finally:
        conn.close()


@cli.group()
def journal():
    """Journal layer commands."""
    pass


@journal.command()
def demo():
    """M2 demo: insert fake records, query them, render a table.

    Creates a strategy, candidates, trades (with wash-sale/PDT), and prints a summary.
    """
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.strategies import create_strategy, transition_strategy, list_strategies
    from heron.journal.candidates import create_candidate, dispose_candidate, list_candidates
    from heron.journal.trades import (
        create_trade, fill_trade, close_trade, list_trades,
        check_wash_sale, get_pdt_count,
    )
    from heron.journal.ops import log_cost, get_monthly_cost, log_event, get_events
    from heron.journal import campaigns as jcampaigns

    conn = get_journal_conn()
    init_journal(conn)

    click.echo("=== HERON Journal Demo ===\n")

    # Campaign first — strategies attach to it
    click.echo("--- Creating campaign ---")
    try:
        jcampaigns.create_campaign(
            conn, "demo_campaign", "Demo Paper Campaign",
            description="Demo campaign for the journal walk-through.",
            mode="paper", capital_allocation_usd=500.0, paper_window_days=90,
            state="ACTIVE",
        )
        click.echo("  Created demo_campaign (ACTIVE)")
    except sqlite3.IntegrityError:
        click.echo("  (campaign already exists, skipping)")

    # Strategies
    click.echo("\n--- Creating strategies ---")
    try:
        create_strategy(conn, "pead_v1", "PEAD LLM Variant",
                        description="Post-earnings drift with LLM filtering",
                        rationale="Academic alpha, PDT-safe holds",
                        campaign_id="demo_campaign", template="pead")
        create_strategy(conn, "pead_v1_baseline", "PEAD Deterministic Baseline",
                        is_baseline=True, parent_id="pead_v1",
                        campaign_id="demo_campaign", template="pead")
        click.echo("  Created pead_v1 + baseline (attached to demo_campaign)")
    except sqlite3.IntegrityError:
        click.echo("  (strategies already exist, skipping)")

    transition_strategy(conn, "pead_v1", "PAPER", reason="operator approved", operator="operator")
    transition_strategy(conn, "pead_v1_baseline", "PAPER", reason="operator approved", operator="operator")
    click.echo("  Transitioned both to PAPER")

    strats = list_strategies(conn)
    click.echo(f"\n{'ID':<16} {'Name':<30} {'State':<10}")
    click.echo("-" * 56)
    for s in strats:
        click.echo(f"{s['id']:<16} {s['name']:<30} {s['state']:<10}")

    # Candidates
    click.echo("\n--- Creating candidates ---")
    c1 = create_candidate(conn, "pead_v1", "AAPL", source="research_local",
                          local_score=0.85, thesis="AAPL beat EPS by 12%, raised guidance")
    c2 = create_candidate(conn, "pead_v1", "NVDA", source="research_api",
                          local_score=0.6, api_score=0.9, final_score=0.9,
                          thesis="NVDA massive data center beat")
    c3 = create_candidate(conn, "pead_v1", "MSFT", source="research_local",
                          local_score=0.4, thesis="MSFT met expectations, no surprise")
    dispose_candidate(conn, c1, "accepted")
    dispose_candidate(conn, c2, "accepted")
    dispose_candidate(conn, c3, "rejected", rejection_reason="conviction < 0.5")
    click.echo(f"  Created 3 candidates (2 accepted, 1 rejected)")

    cands = list_candidates(conn, strategy_id="pead_v1")
    click.echo(f"\n{'Ticker':<8} {'Score':<8} {'Disposition':<12} {'Thesis'}")
    click.echo("-" * 70)
    for c in cands:
        score = c['final_score'] or c['local_score'] or 0
        click.echo(f"{c['ticker']:<8} {score:<8.2f} {c['disposition']:<12} {(c['thesis'] or '')[:40]}")

    # Trades
    click.echo("\n--- Simulating trades ---")
    t1 = create_trade(conn, "pead_v1", "AAPL", "buy", "paper", 10,
                       client_order_id=f"pead_v1_demo1_AAPL_buy")
    fill_trade(conn, t1, 185.50)
    close_trade(conn, t1, 192.00, "target", outcome_notes="Clean drift, hit 3x ATR target")
    click.echo("  AAPL: bought 10 @ $185.50, sold @ $192.00 → profit")

    t2 = create_trade(conn, "pead_v1", "NVDA", "buy", "paper", 5,
                       client_order_id=f"pead_v1_demo2_NVDA_buy")
    fill_trade(conn, t2, 880.00)
    close_trade(conn, t2, 855.00, "stop", outcome_notes="Stopped out, post-earnings reversal")
    click.echo("  NVDA: bought 5 @ $880.00, sold @ $855.00 → loss (wash-sale lot created)")

    trades = list_trades(conn, strategy_id="pead_v1")
    click.echo(f"\n{'Ticker':<8} {'Side':<6} {'Qty':<6} {'Entry':<10} {'Exit':<10} {'P&L':>10} {'Reason'}")
    click.echo("-" * 66)
    for t in trades:
        pnl_str = f"${t['pnl']:>+8.2f}" if t['pnl'] is not None else "open"
        click.echo(
            f"{t['ticker']:<8} {t['side']:<6} {t['qty']:<6.0f} "
            f"${t['fill_price'] or 0:<9.2f} ${t['close_price'] or 0:<9.2f} "
            f"{pnl_str:>10} {t['close_reason'] or ''}"
        )

    # Wash-sale check
    click.echo("\n--- Wash-sale check ---")
    nvda_lots = check_wash_sale(conn, "NVDA")
    click.echo(f"  NVDA: {len(nvda_lots)} active wash-sale lot(s)")
    aapl_lots = check_wash_sale(conn, "AAPL")
    click.echo(f"  AAPL: {len(aapl_lots)} active wash-sale lot(s) (profit, no lot)")

    # PDT check
    click.echo(f"\n--- PDT status ---")
    click.echo(f"  Day-trades in window: {get_pdt_count(conn)}")

    # Costs
    log_cost(conn, "qwen_local", 5000, 2000, 0.00, strategy_id="pead_v1",
             task="classification", date="2025-01-15")
    log_cost(conn, "claude_sonnet", 1200, 600, 0.04, strategy_id="pead_v1",
             task="thesis", date="2025-01-15")
    click.echo(f"\n--- Cost tracking ---")
    click.echo(f"  January 2025 total: ${get_monthly_cost(conn, '2025-01'):.4f}")

    # Events
    log_event(conn, "demo", "Journal demo completed", severity="info", source="cli")
    events = get_events(conn, limit=5)
    click.echo(f"\n--- Recent events ({len(events)}) ---")
    for e in events:
        click.echo(f"  [{e['severity']:>8}] {e['event_type']}: {e['message']}")

    click.echo("\n✓ Journal demo complete. All tables populated.")
    conn.close()


@journal.command()
def status():
    """Show journal stats: strategies, trades, wash-sale, PDT, costs."""
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.strategies import list_strategies
    from heron.journal.trades import list_trades, get_wash_sale_exposure, get_pdt_count
    from heron.journal.ops import get_monthly_cost

    conn = get_journal_conn()
    init_journal(conn)

    strats = list_strategies(conn)
    trades = list_trades(conn)
    open_trades = list_trades(conn, open_only=True)
    wash = get_wash_sale_exposure(conn)
    pdt = get_pdt_count(conn)
    month_cost = get_monthly_cost(conn)

    click.echo("=== HERON Journal Status ===\n")
    click.echo(f"  Strategies:     {len(strats)}")
    for s in strats:
        click.echo(f"    {s['id']:<20} {s['state']}")
    click.echo(f"  Total trades:   {len(trades)}")
    click.echo(f"  Open trades:    {len(open_trades)}")
    click.echo(f"  Wash-sale lots: {len(wash)} active")
    click.echo(f"  PDT day-trades: {pdt} in window")
    click.echo(f"  Month cost:     ${month_cost:.4f}")
    conn.close()


@journal.command()
@click.argument("strategy_id")
@click.option("--reason", "-r", default="Operator approved via CLI")
def approve(strategy_id, reason):
    """Approve a PROPOSED strategy → PAPER and create baseline variant."""
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.strategies import transition_strategy, get_strategy
    from heron.strategy.baseline import ensure_baseline

    conn = get_journal_conn()
    init_journal(conn)

    s = get_strategy(conn, strategy_id)
    if not s:
        click.echo(f"Strategy {strategy_id} not found", err=True)
        conn.close()
        raise SystemExit(1)

    try:
        transition_strategy(conn, strategy_id, "PAPER", reason=reason, operator="operator")
        ensure_baseline(conn, strategy_id)
        click.echo(f"✓ {strategy_id} approved → PAPER (baseline created)")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    finally:
        conn.close()


@journal.command()
@click.argument("strategy_id")
@click.option("--reason", "-r", default="Operator rejected via CLI")
def reject(strategy_id, reason):
    """Reject a PROPOSED strategy → RETIRED."""
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.strategies import transition_strategy, get_strategy

    conn = get_journal_conn()
    init_journal(conn)

    s = get_strategy(conn, strategy_id)
    if not s:
        click.echo(f"Strategy {strategy_id} not found", err=True)
        conn.close()
        raise SystemExit(1)

    try:
        transition_strategy(conn, strategy_id, "RETIRED", reason=reason, operator="operator")
        click.echo(f"✓ {strategy_id} rejected → RETIRED")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    finally:
        conn.close()


@journal.command()
@click.option("--state", "-s", default=None, help="Filter by state (PROPOSED, PAPER, LIVE, RETIRED).")
def inbox(state):
    """Show strategies awaiting action, or filter by state."""
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.strategies import list_strategies

    conn = get_journal_conn()
    init_journal(conn)

    strats = list_strategies(conn, state=state or "PROPOSED")
    if not strats:
        click.echo(f"No strategies in state {state or 'PROPOSED'}")
        conn.close()
        return

    click.echo(f"\n{'ID':<20} {'Name':<30} {'State':<10} {'Created':<12}")
    click.echo("-" * 72)
    for s in strats:
        click.echo(f"{s['id']:<20} {s['name']:<30} {s['state']:<10} {s['created_at'][:10]}")
    conn.close()


@cli.command()
@click.option("--host", default="127.0.0.1", help="Bind address.")
@click.option("--lan", is_flag=True, help="Listen on all LAN interfaces (0.0.0.0).")
@click.option("--port", default=5001, help="Port number.")
@click.option("--debug", is_flag=True, help="Enable Flask debug mode.")
def dashboard(host, lan, port, debug):
    """Launch the HERON web dashboard."""
    from heron.dashboard import create_app
    if lan:
        if host not in ("127.0.0.1", "0.0.0.0"):
            raise click.UsageError("Use either --lan or --host, not both.")
        host = "0.0.0.0"
    app = create_app()
    _echo_dashboard_urls(host, port)
    app.run(host=host, port=port, debug=debug)


@cli.command()
@click.option("--capital", type=float, help="USD capital for first paper campaign.")
@click.option("--cadence", type=click.Choice(["premarket_only", "premarket_eod", "full"]),
              default="premarket_eod")
@click.option("--max-capital-pct", type=float, default=0.15)
@click.option("--max-positions", type=int, default=3)
@click.option("--drawdown-budget-pct", type=float, default=0.05)
@click.option("--plan", "plan_only", is_flag=True, help="Show plan; do not write.")
@click.option("--yes", is_flag=True, help="Skip confirmations (non-interactive).")
def init(capital, cadence, max_capital_pct, max_positions,
         drawdown_budget_pct, plan_only, yes):
    """First-run setup: create initial paper campaign + PEAD strategy + baseline."""
    from heron.journal import get_journal_conn, init_journal
    from heron.runtime.setup import (
        plan_initial_setup, apply_initial_setup, is_already_setup,
        SetupAlreadyDoneError,
    )

    init_journal()
    conn = get_journal_conn()

    if is_already_setup(conn):
        click.echo("Journal already populated. Setup is a no-op.")
        click.echo("Visit /strategies in the dashboard to see what's there.")
        conn.close()
        return

    if capital is None and not yes:
        capital = click.prompt("Starting capital (USD)", type=float, default=500.0)
    elif capital is None:
        capital = 500.0

    try:
        plan = plan_initial_setup(
            capital_usd=capital,
            cadence=cadence,
            max_capital_pct=max_capital_pct,
            max_positions=max_positions,
            drawdown_budget_pct=drawdown_budget_pct,
        )
    except ValueError as e:
        click.echo(f"Bad inputs: {e}", err=True)
        conn.close()
        raise SystemExit(1)

    click.echo("\n=== Initial setup plan ===")
    cmp = plan["campaign"]
    click.echo(f"Campaign:   {cmp['id']}  ({cmp['mode']}, ${cmp['capital_allocation_usd']:.2f}, "
               f"{cmp['paper_window_days']}d window)")
    click.echo("Strategies:")
    for s in plan["strategies"]:
        marker = " (baseline)" if s.get("is_baseline") else ""
        click.echo(f"  - {s['id']}  → {s['state_target']}{marker}")
    g = plan["guardrails"]
    click.echo(f"Guardrails: max_capital={g['max_capital_pct']:.0%}  "
               f"max_positions={g['max_positions']}  dd_budget={g['drawdown_budget_pct']:.0%}")
    click.echo(f"Cadence:    {plan['cadence']['preset']}  "
               f"(jobs hint: {', '.join(plan['cadence']['jobs'])})")

    if plan_only:
        click.echo("\n--plan: nothing written.")
        conn.close()
        return

    if not yes:
        if not click.confirm("\nApply this plan?", default=False):
            click.echo("aborted.")
            conn.close()
            return

    try:
        result = apply_initial_setup(conn, plan)
    except SetupAlreadyDoneError as e:
        click.echo(str(e), err=True)
        conn.close()
        raise SystemExit(1)

    click.echo(f"\nDone. Campaign {result['campaign_id']} created with "
               f"{len(result['strategy_ids'])} strategies.")
    click.echo("Next: `heron data today --tickers ...` to seed market bars, "
               "then visit /strategies in the dashboard.")
    conn.close()


@cli.group()
def ollama():
    """Local Ollama process manager (repo-scoped install + models)."""
    pass


@ollama.command("status")
def ollama_status():
    """Show local Ollama install + runtime status."""
    from heron.tools.ollama_local import status
    s = status()
    click.echo("=== Ollama (repo-local) ===")
    click.echo(f"  Binary:     {s['binary']}")
    click.echo(f"  Installed:  {'✓' if s['installed'] else '✗'}")
    click.echo(f"  Models dir: {s['models_dir']}")
    click.echo(f"  Running:    {'✓' if s['running'] else '✗'}")
    if s["pid_file"]:
        click.echo(f"  PID file:   {s['pid_file']}")


@ollama.command("start")
def ollama_start():
    """Start the repo-local Ollama server (serves on 127.0.0.1:11434)."""
    from heron.tools.ollama_local import start
    ok, msg = start()
    click.echo(("✓ " if ok else "✗ ") + msg)
    if not ok:
        raise SystemExit(1)


@ollama.command("stop")
def ollama_stop():
    """Stop the repo-local Ollama server."""
    from heron.tools.ollama_local import stop
    ok, msg = stop()
    click.echo(("✓ " if ok else "✗ ") + msg)
    if not ok:
        raise SystemExit(1)


@ollama.command("pull")
@click.argument("model", required=False)
def ollama_pull(model):
    """Pull a model into the repo-local models dir. Defaults to OLLAMA_MODEL."""
    from heron.tools.ollama_local import run_cmd, is_running, start
    from heron.config import OLLAMA_MODEL
    target = model or OLLAMA_MODEL
    if not is_running():
        click.echo("Starting server first...")
        ok, msg = start()
        if not ok:
            click.echo(msg, err=True)
            raise SystemExit(1)
    click.echo(f"Pulling {target} ... (this can take a while, multi-GB)")
    res = run_cmd(["pull", target], capture=False)
    if res.returncode != 0:
        click.echo("Pull failed", err=True)
        raise SystemExit(res.returncode)
    click.echo(f"✓ {target} ready")


@ollama.command("list")
def ollama_list():
    """List models in the repo-local models dir."""
    from heron.tools.ollama_local import run_cmd, is_running, start
    if not is_running():
        ok, msg = start()
        if not ok:
            click.echo(msg, err=True)
            raise SystemExit(1)
    res = run_cmd(["list"])
    click.echo(res.stdout)


@cli.group()
def research():
    """Research layer commands."""
    pass


@research.command()
@click.option("--strategy", default="pead_v1", help="Strategy ID.")
@click.option("--pass-type", "pass_type", default="premarket",
              type=click.Choice(["premarket", "midday"]),
              help="Which research pass to run.")
@click.option("--lookback", default=16, help="Hours of news to look back.")
def run(strategy, pass_type, lookback):
    """Run a research pass: fetch news → classify → generate candidates."""
    from heron.research.orchestrator import ResearchPass

    with ResearchPass() as rp:
        result = rp.run(strategy_id=strategy, pass_type=pass_type, lookback_hours=lookback)

    click.echo(f"\n=== Research Pass: {pass_type} ===")
    click.echo(f"  Status:     {result['status']}")
    if result.get("articles"):
        click.echo(f"  Articles:   {result['articles']}")
    if result.get("relevant"):
        click.echo(f"  Relevant:   {result['relevant']}")
    if result.get("candidates"):
        click.echo(f"  Candidates: {result['candidates']}")
    if result.get("month_cost") is not None:
        click.echo(f"  Month cost: ${result['month_cost']:.4f}")
    if result.get("error"):
        click.echo(f"  Error:      {result['error']}")
    if result.get("escalation"):
        esc = result["escalation"]
        click.echo(f"  Escalated:  {esc.get('escalated', 0)} theses, {esc.get('sampled', 0)} audits")


@research.command()
def status():
    """Check Ollama availability and model status."""
    from heron.research import is_available
    from heron.config import OLLAMA_MODEL, OLLAMA_BASE_URL

    click.echo(f"  Ollama URL: {OLLAMA_BASE_URL}")
    click.echo(f"  Model:      {OLLAMA_MODEL}")
    if is_available():
        click.echo("  Status:     ✓ Available")
    else:
        click.echo("  Status:     ✗ Not available (is Ollama running?)")


@research.command()
@click.argument("candidate_id", type=int)
@click.option("--strategy", default=None, help="Strategy ID override.")
def thesis(candidate_id, strategy):
    """Write a Claude thesis for a specific candidate."""
    from heron.journal import get_journal_conn, init_journal
    from heron.research.thesis import write_thesis

    conn = get_journal_conn()
    init_journal(conn)
    result = write_thesis(conn, candidate_id, strategy_id=strategy)
    conn.close()

    if not result:
        click.echo(f"Candidate {candidate_id} not found.")
        return
    click.echo(f"\n=== Thesis: candidate #{candidate_id} ===")
    click.echo(f"  Status:     {result['status']}")
    if result.get("conviction") is not None:
        click.echo(f"  Conviction: {result['conviction']:.2f}")
    if result.get("cost_usd") is not None:
        click.echo(f"  Cost:       ${result['cost_usd']:.4f}")
    if result.get("thesis"):
        click.echo(f"  Thesis:     {result['thesis'][:200]}")


@research.command()
@click.option("--context", "-c", default="", help="Market context to pass to Claude.")
@click.option("--force", is_flag=True, help="Bypass daily limit and confidence floor.")
def propose(context, force):
    """Ask Claude to propose a new trading strategy."""
    from heron.journal import get_journal_conn, init_journal
    from heron.research.proposer import propose_strategy

    conn = get_journal_conn()
    init_journal(conn)
    result = propose_strategy(conn, market_context=context, force=force)
    conn.close()

    click.echo(f"\n=== Strategy Proposal ===")
    click.echo(f"  Status: {result['status']}")
    if result.get("strategy_id"):
        click.echo(f"  ID:     {result['strategy_id']}")
        click.echo(f"  Name:   {result.get('name', '')}")
        click.echo(f"  Conf:   {result.get('confidence', 0):.2f}")
        click.echo(f"  Cost:   ${result.get('cost_usd', 0):.4f}")
    elif result.get("error"):
        click.echo(f"  Error:  {result['error']}")


@cli.group()
def baseline():
    """Baseline-variant runner commands."""
    pass


@baseline.command()
@click.argument("strategy_id")
def create(strategy_id):
    """Create a deterministic baseline variant for a strategy."""
    from heron.journal import get_journal_conn, init_journal
    from heron.strategy.baseline import ensure_baseline

    conn = get_journal_conn()
    init_journal(conn)
    bid = ensure_baseline(conn, strategy_id)
    conn.close()
    click.echo(f"Baseline variant: {bid}")


@baseline.command("beat-test")
@click.argument("strategy_id")
@click.option("--start", default=None, help="Start date (YYYY-MM-DD).")
@click.option("--end", default=None, help="End date (YYYY-MM-DD).")
@click.option("--bootstraps", default=10000, help="Number of bootstrap samples.")
def beat_test(strategy_id, start, end, bootstraps):
    """Run the Section 10.2 bootstrap baseline-beat test."""
    from heron.journal import get_journal_conn, init_journal
    from heron.strategy.baseline import run_beat_test

    conn = get_journal_conn()
    init_journal(conn)
    result = run_beat_test(conn, strategy_id, start_date=start, end_date=end,
                           n_bootstrap=bootstraps)
    conn.close()

    click.echo(f"\n=== Baseline Beat Test: {strategy_id} ===")
    click.echo(f"  vs Baseline:  {result['baseline_id']}")
    click.echo(f"  Days:         {result['n_days']}")
    click.echo(f"  Mean diff:    {result['mean_diff']:+.4%}")
    click.echo(f"  95% CI:       [{result['ci_lower']:+.4%}, {result['ci_upper']:+.4%}]")
    verdict = "PASS ✓ — LLM beats baseline" if result["passes"] else "FAIL ✗ — no significant edge"
    click.echo(f"  Verdict:      {verdict}")
    if result.get("reason"):
        click.echo(f"  Note:         {result['reason']}")


@baseline.command()
@click.argument("strategy_id")
@click.option("--start", default=None, help="Start date.")
@click.option("--end", default=None, help="End date.")
def curves(strategy_id, start, end):
    """Show side-by-side equity curves for LLM vs baseline."""
    from heron.journal import get_journal_conn, init_journal
    from heron.strategy.baseline import get_equity_curve

    conn = get_journal_conn()
    init_journal(conn)
    baseline_id = f"{strategy_id}_baseline"

    llm_curve = get_equity_curve(conn, strategy_id, start, end)
    base_curve = get_equity_curve(conn, baseline_id, start, end)
    conn.close()

    if not llm_curve and not base_curve:
        click.echo("No closed trades yet for either variant.")
        return

    click.echo(f"\n{'Date':<14} {'LLM Equity':>12} {'Base Equity':>12} {'Diff':>10}")
    click.echo("-" * 50)

    llm_map = {c["date"]: c["equity"] for c in llm_curve}
    base_map = {c["date"]: c["equity"] for c in base_curve}
    all_dates = sorted(set(llm_map) | set(base_map))

    llm_eq = 100000.0
    base_eq = 100000.0
    for d in all_dates:
        if d in llm_map:
            llm_eq = llm_map[d]
        if d in base_map:
            base_eq = base_map[d]
        click.echo(f"{d:<14} {llm_eq:>12,.2f} {base_eq:>12,.2f} {llm_eq - base_eq:>+10,.2f}")


@cli.group()
def audit():
    """LLM audit commands (post-mortems, trust score)."""
    pass


@audit.command("run")
@click.option("--limit", type=int, default=None, help="Max post-mortems (default: config)")
def audit_run(limit):
    """Run cost-triggered post-mortems on losing trades."""
    from heron.journal import get_journal_conn, init_journal
    from heron.research.audit import run_pending_post_mortems
    init_journal()
    conn = get_journal_conn()
    r = run_pending_post_mortems(conn, limit=limit)
    click.echo(f"Status:    {r['status']}")
    click.echo(f"Completed: {r.get('completed', 0)}")
    if r.get("divergent") is not None:
        click.echo(f"Divergent: {r['divergent']}")
    for item in r.get("results", []):
        mark = "⚠" if item.get("divergence") else "✓"
        click.echo(f"  {mark} {item.get('ticker','?')} trade#{item['trade_id']}: "
                   f"local={item.get('local_score',0):.2f} "
                   f"api={item.get('api_conviction',0):.2f}")


@audit.command("score")
@click.option("--window", type=int, default=None, help="Window days")
def audit_score(window):
    """Show local-model trust score."""
    from heron.journal import get_journal_conn, init_journal
    from heron.research.audit import compute_trust_score
    init_journal()
    conn = get_journal_conn()
    r = compute_trust_score(conn, window_days=window)
    click.echo(f"Window:      {r['window_days']} days")
    click.echo(f"Sample size: {r['sample_size']}")
    if r["trust_score"] is None:
        click.echo(f"Trust:       n/a ({r['warning']})")
    else:
        click.echo(f"Trust:       {r['trust_score']:.3f}  ({r.get('divergent',0)} divergent)")
    b = r["breakdown"]
    click.echo(f"  sampling:       {b['sampling']['n']} total, "
               f"{b['sampling']['divergent']} divergent")
    click.echo(f"  cost_triggered: {b['cost_triggered']['n']} total, "
               f"{b['cost_triggered']['divergent']} divergent")


@audit.command("list")
@click.option("--type", "audit_type", type=click.Choice(["sampling", "cost_triggered"]),
              default=None)
@click.option("--limit", type=int, default=20)
def audit_list(audit_type, limit):
    """List recent audits."""
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.ops import get_audits
    init_journal()
    conn = get_journal_conn()
    rows = get_audits(conn, audit_type=audit_type, limit=limit)
    if not rows:
        click.echo("(no audits)")
        return
    for r in rows:
        mark = "⚠" if r["divergence"] else "·"
        click.echo(f"{r['created_at'][:19]}  {mark} {r['audit_type']:<15} "
                   f"trade#{r['trade_id'] or '-'} cand#{r['candidate_id'] or '-'}  "
                   f"{(r['notes'] or '')[:80]}")


@audit.command("contamination")
@click.argument("path", required=False)
def audit_contamination(path):
    """Static AST scan for PIT-leak patterns (missing as_of= on data reads).

    PATH defaults to heron/strategy. Pass a single .py file or a directory.
    Exits 1 if any findings — suitable for CI.
    """
    import sys
    from heron.research.audit import contamination_audit
    target = path or os.path.join("heron", "strategy")
    findings = contamination_audit(target)
    if not findings:
        click.echo(f"✓ {target}: no contamination findings.")
        return
    click.echo(f"⚠ {target}: {len(findings)} finding(s):")
    for f in findings:
        click.echo(f"  {f['file']}:{f['line']}  [{f['rule']}]  {f['message']}")
    sys.exit(1)


@cli.group()
def policy():
    """Policy engine + system mode (B2)."""
    pass


@policy.command("status")
@click.option("--mode", default="paper", help="paper or live")
def policy_status(mode):
    """Show current system mode + which rules would fire."""
    from heron.journal import get_journal_conn, init_journal
    from heron.strategy.policy import (
        assemble_state, evaluate_policies, current_system_mode,
    )
    init_journal()
    conn = get_journal_conn()
    state = assemble_state(conn, mode=mode, equity=10000.0)
    actions = evaluate_policies(state)
    sys_mode = current_system_mode(conn)
    click.echo(f"System mode: {sys_mode}")
    click.echo("State:")
    for k, v in state.items():
        click.echo(f"  {k:28s} {v}")
    if not actions:
        click.echo("Triggered rules: (none)")
    else:
        click.echo(f"Triggered rules ({len(actions)}):")
        for a in actions:
            click.echo(f"  • {a['id']:20s} -> {a['action']:15s} {a['reason']}")
    conn.close()


@policy.command("override")
@click.argument("new_mode", type=click.Choice(["NORMAL", "DERISK", "SAFE"]))
@click.option("--reason", required=True, help="Required reason for the override.")
def policy_override(new_mode, reason):
    """Force a system mode transition (operator action)."""
    from heron.journal import get_journal_conn, init_journal
    from heron.strategy.policy import set_system_mode
    init_journal()
    conn = get_journal_conn()
    prior = set_system_mode(conn, new_mode, reason=reason,
                            operator="cli", triggered_by=["operator_override"])
    click.echo(f"System mode: {prior} -> {new_mode}")
    conn.close()


@policy.command("eval")
@click.option("--mode", default="paper")
def policy_eval(mode):
    """Run policy evaluation now and apply the resulting mode (idempotent)."""
    from heron.journal import get_journal_conn, init_journal
    from heron.strategy.policy import (
        assemble_state, evaluate_policies, resolve_mode,
        current_system_mode, set_system_mode,
    )
    init_journal()
    conn = get_journal_conn()
    state = assemble_state(conn, mode=mode, equity=10000.0)
    actions = evaluate_policies(state)
    prior = current_system_mode(conn)
    target = resolve_mode(actions, prior_mode="NORMAL")
    if target != prior:
        set_system_mode(conn, target, reason="cli eval",
                        operator="cli", triggered_by=[a["id"] for a in actions])
    click.echo(f"{prior} -> {target}  ({len(actions)} rule(s) fired)")
    conn.close()


@cli.group()
def alert():
    """Discord alert commands (M12)."""
    pass


@alert.command("test")
def alert_test():
    """Send a test alert (bypasses rate-limiter)."""
    from heron.alerts.discord import send, DISCORD_WEBHOOK_URL
    if not DISCORD_WEBHOOK_URL:
        click.echo("DISCORD_WEBHOOK_URL not set. Add it to .env to enable alerts.")
        return
    r = send("test", "🦅 HERON alert test — webhook is live.", force=True)
    click.echo(f"Status: {r['status']}")


@alert.command("send")
@click.argument("category")
@click.argument("message")
@click.option("--force", is_flag=True, help="Bypass rate limiter.")
def alert_send(category, message, force):
    """Send an arbitrary alert. Categories: debrief, proposal, promotion,
    cost_warning, cost_trip, drift, review_reminder, test."""
    from heron.alerts.discord import send
    r = send(category, message, force=force)
    click.echo(f"Status: {r['status']}")


@alert.command("reset")
@click.option("--category", default=None, help="Clear one category; omit to clear all.")
def alert_reset(category):
    """Clear rate-limit state for alert categories."""
    from heron.alerts.discord import reset
    reset(category)
    click.echo(f"Reset {category or 'all categories'}")


@cli.command()
@click.option("--no-send", is_flag=True, help="Preview only, skip Discord.")
@click.option("--dry-run", is_flag=True, help="Call Claude but don't post to Discord.")
def debrief(no_send, dry_run):
    """Generate and post the end-of-day debrief (M12)."""
    from heron.journal import get_journal_conn, init_journal
    from heron.alerts.debrief import run as run_debrief

    conn = get_journal_conn()
    init_journal(conn)
    result = run_debrief(conn, deliver=not no_send, dry_run=dry_run)
    conn.close()

    click.echo("\n=== EOD Debrief ===")
    d = result["data"]
    click.echo(f"  Date:         {d['date']}")
    click.echo(f"  Closed:       {d['closed_count']} ({d['winners']}W/{d['losers']}L)")
    click.echo(f"  P&L:          ${d['pnl']:+.2f}")
    click.echo(f"  Open:         {d['open_count']}")
    click.echo(f"  Cost MTD:     ${d['cost_mtd']:.2f}")
    if result["prose"].get("cost_usd"):
        click.echo(f"  Prose cost:   ${result['prose']['cost_usd']:.4f}")
    click.echo("\n--- Message ---")
    click.echo(result["message"])
    if result["delivery"]:
        click.echo(f"\nDelivery: {result['delivery']['status']}")


@cli.group()
def backtest():
    """Backtester (M13) — deterministic strategy replay."""
    pass


@backtest.command("run")
@click.argument("strategy_id")
@click.option("--start", default=None, help="Start date YYYY-MM-DD (default: earliest cached bar).")
@click.option("--end", default=None, help="End date YYYY-MM-DD (default: latest cached bar).")
@click.option("--seed", default=0, help="RNG seed for determinism.")
@click.option("--equity", default=100_000.0, help="Initial equity.")
@click.option("--seeder", type=click.Choice(["synthetic", "real"]), default="synthetic",
              help="synthetic = bar-derived fake surprises; real = cached earnings_events.")
@click.option("--save/--no-save", default=True, help="Persist report to journal.")
def backtest_run(strategy_id, start, end, seed, equity, seeder, save):
    """Run a deterministic backtest for a strategy."""
    from heron.journal import get_journal_conn, init_journal
    from heron.backtest import run_strategy_backtest

    init_journal()
    conn = get_journal_conn()
    try:
        result = run_strategy_backtest(
            conn, strategy_id,
            start=start, end=end, seed=seed, initial_equity=equity,
            save=save, seeder=seeder,
        )
    except ValueError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1)

    universe = result.get("universe", [])
    click.echo(f"Universe: {','.join(universe)}  (seeder={seeder})")

    m = result["metrics"]
    click.echo(f"\n=== Backtest: {strategy_id} ===")
    click.echo(f"  Window:      {result['start_date']} → {result['end_date']}")
    click.echo(f"  Trades:      {m['n_trades']} ({m['n_wins']}W / {m['n_losses']}L)")
    click.echo(f"  Win rate:    {m['win_rate']:.1%}")
    click.echo(f"  Total ret:   {m['total_return']:+.2%}")
    click.echo(f"  Avg P&L:     ${m['avg_trade_pnl']:+,.2f}")
    click.echo(f"  Max DD:      {m['max_drawdown']:.2%}")
    if m["sharpe"] is not None:
        click.echo(f"  Sharpe:      {m['sharpe']:.2f}")
    click.echo(f"  Fees paid:   ${m['total_fees']:.2f}")
    click.echo(f"  Final eq:    ${result['final_equity']:,.2f}")

    report_id = result.get("report_id")
    if report_id:
        row = conn.execute(
            "SELECT contaminated, contamination_notes FROM backtest_reports WHERE id=?",
            (report_id,),
        ).fetchone()
        click.echo(f"\n  Saved report #{report_id}")
        if row and row["contaminated"]:
            click.echo(f"  ⚠ {row['contamination_notes']}")
    conn.close()


@backtest.command("walkforward")
@click.argument("strategy_id")
@click.option("--start", required=True, help="Overall start YYYY-MM-DD.")
@click.option("--end", required=True, help="Overall end YYYY-MM-DD.")
@click.option("--train", "train_months", default=6, help="Train window months (used for fitting when --vary is set).")
@click.option("--test", "test_months", default=3, help="Test window months.")
@click.option("--step", "step_months", default=3, help="Step months between windows.")
@click.option("--seed", default=0)
@click.option("--equity", default=100_000.0)
@click.option("--seeder", type=click.Choice(["synthetic", "real"]), default="synthetic")
@click.option("--vary", "vary", multiple=True,
              help="Optional axis spec like 'stop_mult=1.0,1.5,2.0' for per-window fitting. Repeatable.")
@click.option("--objective", type=click.Choice(["sharpe", "total_return", "win_rate", "avg_trade_pnl"]),
              default="sharpe", help="Fit objective when --vary is provided.")
def backtest_walkforward(strategy_id, start, end, train_months, test_months,
                         step_months, seed, equity, seeder, vary, objective):
    """Run a walk-forward backtest (sliding test windows). Optionally fit params per train window."""
    import json as _json
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.strategies import get_strategy
    from heron.backtest.walkforward import run_walkforward
    from heron.backtest.sweep import parse_axes

    init_journal()
    conn = get_journal_conn()

    axes = None
    if vary:
        s = get_strategy(conn, strategy_id)
        if not s:
            click.echo(f"Strategy {strategy_id!r} not found", err=True)
            conn.close()
            raise SystemExit(1)
        try:
            base_cfg = _json.loads(s["config"]) if s["config"] else {}
        except (TypeError, _json.JSONDecodeError):
            base_cfg = {}
        try:
            axes = parse_axes(list(vary), base_cfg)
        except ValueError as e:
            click.echo(str(e), err=True)
            conn.close()
            raise SystemExit(1)

    try:
        res = run_walkforward(
            conn, strategy_id,
            start=start, end=end,
            train_months=train_months, test_months=test_months, step_months=step_months,
            seed=seed, initial_equity=equity, seeder=seeder,
            axes=axes, objective=objective,
        )
    except ValueError as e:
        click.echo(str(e), err=True)
        conn.close()
        raise SystemExit(1)

    m = res["metrics"]
    click.echo(f"\n=== Walk-forward {strategy_id} (id={res['walkforward_id']}) ===")
    click.echo(f"  Windows:      {m['n_windows']}")
    click.echo(f"  Trades:       {m['n_trades']} ({m['n_wins']}W / {m['n_losses']}L)")
    click.echo(f"  Win rate:     {m['win_rate']:.1%}")
    click.echo(f"  Total ret:    {m['total_return']:+.2%}")
    click.echo(f"  Max DD:       {m['max_drawdown']:.2%}")
    if axes:
        click.echo(f"  Fit axes:     {axes}  objective={objective}")
        for c in res["children"]:
            if c["locked_overrides"]:
                click.echo(f"    win {c['window_index']} ({c['test_start']}..{c['test_end']}): "
                           f"locked={c['locked_overrides']}")
    click.echo(f"  Parent #{res['parent_report_id']}  children: "
               f"{', '.join('#' + str(c['report_id']) for c in res['children'])}")
    conn.close()


@backtest.command("sweep")
@click.argument("strategy_id")
@click.option("--vary", "vary", multiple=True, required=True,
              help="Axis spec like 'stop_mult=1.0,1.5,2.0'. Repeatable.")
@click.option("--start", help="Start YYYY-MM-DD.")
@click.option("--end", help="End YYYY-MM-DD.")
@click.option("--seed", default=0)
@click.option("--equity", default=100_000.0)
@click.option("--seeder", type=click.Choice(["synthetic", "real"]), default="synthetic")
def backtest_sweep(strategy_id, vary, start, end, seed, equity, seeder):
    """Cartesian param sweep across the strategy's tunable axes."""
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.strategies import get_strategy
    from heron.backtest.sweep import parse_axes, run_sweep

    init_journal()
    conn = get_journal_conn()
    s = get_strategy(conn, strategy_id)
    if not s:
        click.echo(f"strategy {strategy_id!r} not found", err=True)
        conn.close()
        raise SystemExit(1)
    base_cfg = json.loads(s["config"]) if s["config"] else {}

    try:
        axes = parse_axes(vary, base_cfg)
        res = run_sweep(conn, strategy_id, axes,
                        start=start, end=end, seed=seed,
                        initial_equity=equity, seeder=seeder)
    except ValueError as e:
        click.echo(str(e), err=True)
        conn.close()
        raise SystemExit(1)

    click.echo(f"\n=== Sweep {strategy_id} (id={res['sweep_id']}) ===")
    click.echo(f"  Combos run:   {res['n_saved']}/{res['n_combos']}")
    # Sort summaries by total_return desc.
    rows = sorted(res["summaries"],
                  key=lambda r: r["metrics"].get("total_return", 0),
                  reverse=True)
    for r in rows[:10]:
        m = r["metrics"]
        ov = " ".join(f"{k}={v}" for k, v in r["overrides"].items())
        click.echo(f"  #{r['report_id']:>4}  ret={m['total_return']:+.2%}  "
                   f"sharpe={m.get('sharpe', 0):.2f}  dd={m['max_drawdown']:.2%}  "
                   f"trades={m['n_trades']:>3}  {ov}")
    if len(rows) > 10:
        click.echo(f"  ... +{len(rows)-10} more")
    conn.close()


@backtest.command("list")
@click.option("--strategy", default=None, help="Filter by strategy id.")
@click.option("--limit", default=20)
def backtest_list(strategy, limit):
    """List backtest reports."""
    from heron.journal import get_journal_conn, init_journal
    from heron.backtest import list_reports

    init_journal()
    conn = get_journal_conn()
    rows = list_reports(conn, strategy_id=strategy, limit=limit)
    if not rows:
        click.echo("(no reports)")
        return
    click.echo(f"\n{'ID':<5} {'Strategy':<20} {'Window':<25} {'Trades':<7} "
               f"{'Return':<10} {'Sharpe':<8} {'Memo'}")
    click.echo("-" * 95)
    for r in rows:
        sharpe = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "—"
        memo = "⚠" if r["contaminated"] else "·"
        click.echo(f"{r['id']:<5} {r['strategy_id']:<20} "
                   f"{r['start_date']} → {r['end_date']}  "
                   f"{r['n_trades']:<7} {r['total_return']:+.2%}   "
                   f"{sharpe:<8} {memo}")
    conn.close()


@backtest.command("reparity")
@click.option("--report-id", type=int, default=None,
              help="Recompute a single report. Omit to backfill ALL reports missing parity.")
@click.option("--strategy", default=None, help="Limit --all backfill to this strategy id.")
def backtest_reparity(report_id, strategy):
    """Recompute parity verdict + regime breakdown on existing reports.

    Useful after running a baseline backtest for an LLM strategy that
    already had reports saved without parity.
    """
    from heron.journal import get_journal_conn, init_journal
    from heron.backtest import reparity_report

    init_journal()
    conn = get_journal_conn()
    if report_id is not None:
        targets = [report_id]
    else:
        q = "SELECT id FROM backtest_reports"
        params = []
        if strategy:
            q += " WHERE strategy_id=?"
            params.append(strategy)
        q += " ORDER BY id"
        targets = [r[0] for r in conn.execute(q, params).fetchall()]
    if not targets:
        click.echo("(no reports to reparity)")
        return
    passed = blocked = 0
    for rid in targets:
        try:
            m = reparity_report(conn, rid)
        except Exception as e:  # noqa: BLE001
            click.echo(f"  {rid}: error — {e}")
            continue
        p = m.get("parity") or {}
        if p.get("available") and p.get("passes"):
            verdict, passed_inc = "PASS", 1
        elif p.get("available"):
            verdict, passed_inc = "FAIL", 0
        else:
            verdict, passed_inc = p.get("reason", "n/a"), 0
        passed += passed_inc
        blocked += 0 if passed_inc else 1
        click.echo(f"  {rid}: {verdict}")
    click.echo(f"\n{len(targets)} reports updated · {passed} pass · {blocked} non-pass")
    conn.close()


@cli.group()
def cost():
    """Cost controls (M14) — monthly budget + halt."""
    pass


@cost.command("status")
def cost_status():
    """Show monthly budget state + projection."""
    from heron.journal import get_journal_conn, init_journal
    from heron.research.cost_guard import check_budget
    init_journal()
    conn = get_journal_conn()
    b = check_budget(conn)
    conn.close()

    icon = {"ok": "✓", "warning": "⚠", "tripped": "🛑"}.get(b["status"], "·")
    click.echo(f"\n=== Cost Status: {b['year_month']} ===")
    click.echo(f"  Status:        {icon} {b['status'].upper()}")
    click.echo(f"  Reason:        {b['reason']}")
    click.echo(f"  MTD:           ${b['mtd']:.2f}")
    click.echo(f"  Projected:     ${b['projected']:.2f}")
    click.echo(f"  Ceiling:       ${b['ceiling']:.2f}")
    click.echo(f"  Day:           {b['days_elapsed']}/{b['days_in_month']}")
    click.echo(f"  Research:      {'allowed' if b['research_allowed'] else 'HALTED'}")


@cost.command("notify")
@click.option("--force", is_flag=True, help="Bypass rate-limit when sending Discord alert.")
def cost_notify(force):
    """Check budget and send Discord alert if warning/tripped."""
    from heron.journal import get_journal_conn, init_journal
    from heron.research.cost_guard import notify_if_threshold
    init_journal()
    conn = get_journal_conn()
    s = notify_if_threshold(conn, force=force)
    conn.close()
    click.echo(f"Status: {s['status']} — {s['reason']}")


@cli.group()
def resilience():
    """Resilience hardening (M15) — startup audit, secrets, shutdown."""
    pass


@resilience.command("audit")
@click.option("--broker", type=click.Choice(["none", "alpaca"]), default="none",
              help="Broker to reconcile against (default: none).")
def resilience_audit(broker):
    """Run startup audit: reconciliation, stop coverage, pending work."""
    from heron.journal import get_journal_conn, init_journal
    from heron.resilience import run_startup_audit
    init_journal()
    conn = get_journal_conn()

    b = None
    if broker == "alpaca":
        from heron.execution.alpaca_adapter import AlpacaAdapter
        b = AlpacaAdapter()

    r = run_startup_audit(conn, broker=b)
    conn.close()

    icon = {"clean": "✓", "drift": "⚠", "error": "🛑"}.get(r["status"], "·")
    click.echo(f"\n=== Startup Audit ===")
    click.echo(f"  Status:   {icon} {r['status'].upper()}")
    click.echo(f"  Issues:   {len(r['issues'])}")
    for issue in r["issues"]:
        click.echo(f"    - {issue}")
    click.echo(f"\n  Checks:")
    for name, data in r["checks"].items():
        click.echo(f"    {name}: {data.get('status', data)}")
    if r["status"] != "clean":
        raise SystemExit(1)


@resilience.command("secrets")
@click.option("--env", default=".env", help="Path to env file.")
@click.option("--log", "log_file", default=None, help="Optional log file to scan for leaks.")
def resilience_secrets(env, log_file):
    """Check secrets hygiene: env perms, required vars, log leaks."""
    from heron.resilience import check_secrets_hygiene
    r = check_secrets_hygiene(env_path=env, log_path=log_file)
    icon = {"clean": "✓", "issues": "⚠"}.get(r["status"], "·")
    click.echo(f"\n=== Secrets Hygiene ===")
    click.echo(f"  Status:   {icon} {r['status'].upper()}")
    click.echo(f"  Env file: {r['env_file']['status']}")
    if r['env_file'].get('note'):
        click.echo(f"            ({r['env_file']['note']})")
    click.echo(f"  Env vars: {r['env_vars']['status']}")
    if r['env_vars']['missing_required']:
        click.echo(f"    missing: {', '.join(r['env_vars']['missing_required'])}")
    if r['env_vars']['missing_optional']:
        click.echo(f"    missing (optional): {', '.join(r['env_vars']['missing_optional'])}")
    if "log_scan" in r:
        click.echo(f"  Log scan: {r['log_scan']['status']}")
        for f in r['log_scan'].get('findings', [])[:5]:
            click.echo(f"    line {f['line']}: {f['pattern']}")
    if r["status"] != "clean":
        raise SystemExit(1)


@cli.command(name="run")
@click.option("--mode", type=click.Choice(["paper", "live"]), default="paper",
              help="Trading mode.")
@click.option("--once", "once_job", default=None,
              help="Fire one job synchronously and exit (e.g. research_premarket).")
@click.option("--status", "show_status", is_flag=True,
              help="Show current job schedule + recent runs and exit.")
@click.option("--skip-preflight", is_flag=True, help="Skip preflight (testing only).")
def run_cmd(mode, once_job, show_status, skip_preflight):
    """Start the supervisor — schedules research/executor/debrief/health jobs."""
    from heron.runtime.preflight import preflight
    from heron.runtime.supervisor import Supervisor
    from heron.journal import get_journal_conn, init_journal

    if show_status:
        conn = get_journal_conn()
        init_journal(conn)
        sup = Supervisor(mode=mode, conn=conn)
        try:
            s = sup.status()
            click.echo(f"Mode: {s['mode']}")
            click.echo("\nJobs:")
            for j in s["jobs"]:
                click.echo(f"  {j['id']:<22} next={j['next_run'] or '—'}")
            click.echo("\nRecent runs:")
            for r in s["recent_runs"][:10]:
                click.echo(f"  {r['started_at']:<32} {r['job_id']:<22} {r['status']}")
        finally:
            sup.stop(wait=False)
        return

    if once_job:
        conn = get_journal_conn()
        init_journal(conn)
        sup = Supervisor(mode=mode, conn=conn)
        try:
            click.echo(f"Running {once_job} (mode={mode})…")
            result = sup.run_once(once_job)
            click.echo(json.dumps(result, indent=2, default=str))
        finally:
            sup.stop(wait=False)
        return

    # Foreground supervisor
    conn = get_journal_conn()
    init_journal(conn)

    if not skip_preflight:
        click.echo("Preflight…")
        pf = preflight(conn, mode=mode)
        for w in pf["warnings"]:
            click.echo(f"  warn: {w}")
        if not pf["ok"]:
            click.echo("Preflight blocked:", err=True)
            for b in pf["blockers"]:
                click.echo(f"  ✗ {b}", err=True)
            click.echo("Use --skip-preflight to override (testing only).", err=True)
            raise SystemExit(2)
        click.echo("  ok")

    sup = Supervisor(mode=mode, conn=conn)

    import signal
    def _signal_handler(signum, frame):
        click.echo(f"\nShutdown signal: {signal.Signals(signum).name}")
        sup.stop(wait=True)
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _signal_handler)
        except (ValueError, AttributeError, OSError):
            pass

    sup.start()
    click.echo(f"Supervisor running (mode={mode}). Ctrl+C to stop.")
    try:
        # Block until shutdown. Signal handler stops the supervisor; loop exits.
        import time as _time
        while sup.scheduler.running:
            _time.sleep(1)
    except KeyboardInterrupt:
        sup.stop(wait=True)


if __name__ == "__main__":
    cli()

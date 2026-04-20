"""HERON CLI — data, journal, and demo commands."""

import json
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

    conn = get_journal_conn()
    init_journal(conn)

    click.echo("=== HERON Journal Demo ===\n")

    # Strategies
    click.echo("--- Creating strategies ---")
    try:
        create_strategy(conn, "pead_v1", "PEAD LLM Variant",
                        description="Post-earnings drift with LLM filtering",
                        rationale="Academic alpha, PDT-safe holds")
        create_strategy(conn, "pead_v1_baseline", "PEAD Deterministic Baseline",
                        is_baseline=True, parent_id="pead_v1")
        click.echo("  Created pead_v1 + baseline")
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
@click.option("--port", default=5001, help="Port number.")
@click.option("--debug", is_flag=True, help="Enable Flask debug mode.")
def dashboard(host, port, debug):
    """Launch the HERON web dashboard."""
    from heron.dashboard import create_app
    app = create_app()
    click.echo(f"Starting HERON dashboard at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)


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
@click.option("--save/--no-save", default=True, help="Persist report to journal.")
def backtest_run(strategy_id, start, end, seed, equity, save):
    """Run a deterministic backtest for a strategy."""
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.strategies import get_strategy
    from heron.data.cache import get_bars
    from heron.strategy.pead import PEADStrategy, PEAD_UNIVERSE
    from heron.backtest import run_backtest, save_report
    from heron.backtest.seeders import synthetic_pead_candidates

    init_journal()
    conn = get_journal_conn()
    s = get_strategy(conn, strategy_id)
    if not s:
        click.echo(f"Strategy {strategy_id} not found", err=True)
        raise SystemExit(1)

    # Load bars for strategy's universe
    bars = []
    for ticker in PEAD_UNIVERSE:
        bars.extend(get_bars(conn, ticker, "1Day", start=start, end=end))
    if not bars:
        click.echo("No cached bars found. Run `heron data today --days 200` first.", err=True)
        raise SystemExit(1)

    click.echo(f"Loaded {len(bars)} bars across {len(PEAD_UNIVERSE)} tickers")

    cands = synthetic_pead_candidates(bars, universe=PEAD_UNIVERSE, seed=seed)
    click.echo(f"Generated {len(cands)} synthetic candidates (seed={seed})")

    strat = PEADStrategy(strategy_id=strategy_id, is_llm_variant=False)
    result = run_backtest(strat, bars, cands,
                          start_date=start, end_date=end,
                          initial_equity=equity, seed=seed)

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

    if save:
        report_id = save_report(conn, result)
        row = conn.execute(
            "SELECT contaminated, contamination_notes FROM backtest_reports WHERE id=?",
            (report_id,),
        ).fetchone()
        click.echo(f"\n  Saved report #{report_id}")
        if row["contaminated"]:
            click.echo(f"  ⚠ {row['contamination_notes']}")
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


if __name__ == "__main__":
    cli()

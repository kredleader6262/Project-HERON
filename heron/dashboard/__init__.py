"""HERON Dashboard — Flask + HTMX web application.

Local access only (Tailscale VPN boundary). No public exposure.
See Project-HERON.md Section 4.5 for dashboard views.
"""

import json

from flask import Flask, render_template, request, redirect, url_for, abort, flash, jsonify, make_response
from heron.journal import get_journal_conn, init_journal
from heron.journal.strategies import list_strategies, get_strategy, get_state_history, transition_strategy, create_strategy
from heron.journal.candidates import list_candidates, get_candidate, dispose_candidate
from heron.journal.signals import get_signal_for_candidate, list_signals
from heron.journal.trades import list_trades, get_wash_sale_exposure, get_pdt_count
from heron.journal.ops import get_monthly_cost, get_daily_costs, get_events, get_review, get_audits, is_review_current, log_event
from heron.journal import campaigns as jcampaigns
from heron.strategy import templates as stemplates
from heron.dashboard import mode as vmode

import os
import secrets
import threading


DEFAULT_DESK_ID = "default_paper"
DEFAULT_DESK_ROUTE_ID = "default"
DEFAULT_DESK_NAME = "PEAD Desk"
DEFAULT_DESK_DESCRIPTION = (
    "Default Post-Earnings Drift desk for the initial paper workflow and "
    "orphan-strategy backfill."
)


def _resolve_campaign_id(campaign_id):
    return DEFAULT_DESK_ID if campaign_id == DEFAULT_DESK_ROUTE_ID else campaign_id


def _desk_route_id(campaign_id):
    return DEFAULT_DESK_ROUTE_ID if campaign_id == DEFAULT_DESK_ID else campaign_id


def _present_campaign(row):
    desk = dict(row)
    is_default = desk.get("id") == DEFAULT_DESK_ID
    desk["is_default_desk"] = is_default
    desk["desk_route_id"] = _desk_route_id(desk.get("id"))
    desk["display_id"] = DEFAULT_DESK_ROUTE_ID if is_default else desk.get("id")
    desk["display_name"] = DEFAULT_DESK_NAME if is_default else (desk.get("name") or desk.get("id"))
    desk["display_description"] = (
        DEFAULT_DESK_DESCRIPTION if is_default else (desk.get("description") or "")
    )
    return desk


def _present_strategy(row):
    strategy = dict(row)
    campaign_id = strategy.get("campaign_id")
    strategy["desk_route_id"] = _desk_route_id(campaign_id) if campaign_id else None
    if campaign_id == DEFAULT_DESK_ID:
        strategy["desk_display_name"] = DEFAULT_DESK_NAME
    else:
        strategy["desk_display_name"] = strategy.get("desk_campaign_name") or campaign_id
    return strategy


def _desk_label(campaign_id):
    return DEFAULT_DESK_NAME if campaign_id == DEFAULT_DESK_ID else campaign_id


def _desk_error(exc):
    return str(exc).replace("Campaign ", "Desk ")


def _market_session():
    """Rough US equity session from current wallclock in America/New_York.

    Uses pytz to handle EST/EDT correctly year-round. This is a UI hint only;
    real trading decisions go through the broker's market-clock APIs.
    """
    from datetime import datetime, timezone
    import pytz
    ny = datetime.now(timezone.utc).astimezone(pytz.timezone("America/New_York"))
    if ny.weekday() >= 5:
        return "closed"
    minutes = ny.hour * 60 + ny.minute
    if 240 <= minutes < 570:      # 04:00–09:30
        return "pre"
    if 570 <= minutes < 960:      # 09:30–16:00
        return "open"
    if 960 <= minutes < 1200:     # 16:00–20:00
        return "after"
    return "closed"


def _status_bar(conn, mode="all"):
    """Compute header status bar once per request.

    Mode-aware: candidates/wash-sale counts reflect the active paper/live/all view.
    Budget + session + PROPOSED inbox are global (mode-independent) by design.
    """
    from heron.config import MONTHLY_COST_CEILING
    from heron.util import trading_day_start_utc_iso
    try:
        mtd = get_monthly_cost(conn) or 0.0
    except Exception:
        mtd = 0.0
    ceiling = MONTHLY_COST_CEILING or 45.0
    pct = (mtd / ceiling) if ceiling else 0.0
    if pct >= 1.0:
        cost_state = "halt"
    elif pct >= 0.8:
        cost_state = "warn"
    else:
        cost_state = "ok"

    # Candidates belong to a strategy; a candidate "counts" for the current mode
    # only if its strategy's state is in that mode's state set.
    try:
        states = vmode.strategy_states(mode)
        clause, params = vmode.in_clause(states)
        row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM candidates c "
            f"JOIN strategies s ON s.id = c.strategy_id "
            f"WHERE c.disposition='pending' AND s.state {clause}",
            params,
        ).fetchone()
        pending_candidates = row["cnt"] if row else 0
    except Exception:
        pending_candidates = 0

    # PROPOSED strategies live in the inbox regardless of mode (that's what an
    # inbox is for). Keep this global so the inbox badge never "hides" work.
    try:
        proposed_strategies = [s for s in list_strategies(conn) if s["state"] == "PROPOSED"]
        pending_strategies = len(proposed_strategies)
    except Exception:
        pending_strategies = 0

    # Wash-sale is a tax rule on real money. In paper mode it's meaningless (0);
    # in live/all mode, count only live lots.
    try:
        wsmode = "live" if mode in ("live", "all") else None
        if mode == "paper":
            wash_count = 0
        else:
            wash_count = len(get_wash_sale_exposure(conn, mode=wsmode))
    except Exception:
        wash_count = 0

    try:
        pdt_count = None if mode == "paper" else get_pdt_count(conn, mode="live")
    except Exception:
        pdt_count = None

    tmode = vmode.trade_mode(mode)
    try:
        today = trading_day_start_utc_iso()
        trades = list_trades(conn, mode=tmode)
        daily_pnl = sum(
            (t["pnl"] or 0.0) for t in trades
            if t["close_filled_at"] and t["close_filled_at"] >= today
        )
        daily_loss_used = abs(daily_pnl) if daily_pnl < 0 else 0.0
    except Exception:
        daily_pnl = 0.0
        daily_loss_used = 0.0

    try:
        gross_exposure = 0.0
        net_exposure = 0.0
        for t in list_trades(conn, open_only=True, mode=tmode):
            qty = float(t["fill_qty"] or t["qty"] or 0.0)
            price = float(t["fill_price"] or t["limit_price"] or 0.0)
            notional = qty * price
            gross_exposure += abs(notional)
            net_exposure += -notional if t["side"] == "sell" else notional
    except Exception:
        gross_exposure = 0.0
        net_exposure = 0.0

    try:
        from heron.strategy.policy import current_system_mode
        system_mode = current_system_mode(conn)
    except Exception:
        system_mode = "NORMAL"

    try:
        review_current = is_review_current(conn)
    except Exception:
        review_current = False

    try:
        from heron.research.audit import compute_trust_score
        trust = compute_trust_score(conn)
        trust_score = trust.get("trust_score") if isinstance(trust, dict) else trust
        trust_sample_size = trust.get("sample_size") if isinstance(trust, dict) else None
    except Exception:
        trust_score = None
        trust_sample_size = None

    try:
        row = conn.execute(
            """SELECT event_type, severity, message, created_at FROM events
               WHERE event_type IN ('reconciliation_drift', 'startup_audit')
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        if not row:
            reconciliation = {"state": "pending", "label": "data pending audit"}
        elif row["event_type"] == "reconciliation_drift" or row["severity"] in ("warn", "error"):
            reconciliation = {"state": "warn", "label": "drift logged"}
        else:
            reconciliation = {"state": "ok", "label": "latest audit logged"}
    except Exception:
        reconciliation = {"state": "pending", "label": "data pending audit"}

    if not review_current:
        promotion_gate = {"state": "warn", "label": "review due"}
    elif system_mode == "SAFE":
        promotion_gate = {"state": "halt", "label": "blocked in SAFE"}
    elif system_mode == "DERISK":
        promotion_gate = {"state": "warn", "label": "restricted in DERISK"}
    else:
        promotion_gate = {"state": "pending", "label": "per-strategy parity"}

    # Overall system state: HALT if cost tripped, WARN if warnings present, else OK.
    if cost_state == "halt" or system_mode == "SAFE":
        system = "halt"
    elif cost_state == "warn" or wash_count > 0 or system_mode == "DERISK" or not review_current:
        system = "warn"
    else:
        system = "ok"

    return {
        "session": _market_session(),
        "system": system,
        "cost_state": cost_state,
        "mtd": mtd,
        "ceiling": ceiling,
        "pct": pct,
        "pending_candidates": pending_candidates,
        "pending_strategies": pending_strategies,
        "wash_count": wash_count,
        "pdt_count": pdt_count,
        "daily_pnl": daily_pnl,
        "daily_loss_used": daily_loss_used,
        "gross_exposure": gross_exposure,
        "net_exposure": net_exposure,
        "buying_power": None,
        "reconciliation": reconciliation,
        "broker_api_health": {"state": "pending", "label": "data pending broker check"},
        "system_mode": system_mode,
        "trust_score": trust_score,
        "trust_sample_size": trust_sample_size,
        "review_current": review_current,
        "promotion_gate": promotion_gate,
    }


def create_app():
    app = Flask(__name__, template_folder="templates")
    # Flash messages only; dashboard is Tailscale-gated (no public exposure).
    app.secret_key = os.environ.get("DASHBOARD_SECRET") or secrets.token_hex(16)

    def get_conn():
        conn = get_journal_conn()
        init_journal(conn)
        return conn

    @app.context_processor
    def inject_status():
        """Make the status bar available on every page without every view passing it."""
        m = vmode.get_mode()
        try:
            conn = get_conn()
            status = _status_bar(conn, mode=m)
            conn.close()
        except Exception:
            status = {"session": "unknown", "system": "ok", "cost_state": "ok",
                      "mtd": 0.0, "ceiling": 45.0, "pct": 0.0,
                      "pending_candidates": 0, "pending_strategies": 0, "wash_count": 0,
                      "pdt_count": None, "daily_pnl": 0.0, "daily_loss_used": 0.0,
                      "gross_exposure": 0.0, "net_exposure": 0.0, "buying_power": None,
                      "reconciliation": {"state": "pending", "label": "data pending audit"},
                      "broker_api_health": {"state": "pending", "label": "data pending broker check"},
                      "system_mode": "NORMAL", "trust_score": None, "trust_sample_size": None,
                      "review_current": False,
                      "promotion_gate": {"state": "pending", "label": "per-strategy parity"}}
        return {
            "status_bar": status,
            "active_path": request.path if request else "/",
            "mode": m,
            "mode_label": vmode.label(m),
            "mode_accent": vmode.accent(m),
            "modes": vmode.MODES,
        }

    @app.route("/mode/<m>", methods=["POST", "GET"])
    def set_mode(m):
        """Toggle the global paper/live/all filter. Persisted via cookie."""
        m = (m or "").lower()
        if m not in vmode.MODES:
            flash(f"Unknown mode: {m}", "error")
            m = vmode.DEFAULT
        dest = request.args.get("next") or request.referrer or url_for("index")
        resp = make_response(redirect(dest))
        # 1-year cookie; SameSite=Lax since dashboard is local-only behind Tailscale.
        resp.set_cookie(vmode.COOKIE, m, max_age=60 * 60 * 24 * 365,
                        httponly=False, samesite="Lax")
        return resp

    @app.route("/")
    def index():
        """Mission Control — primary operator surface."""
        return mission_control()

    @app.route("/overview")
    def overview_view():
        """Today at a glance — filtered by global paper/live/all mode.

        Legacy index page; survives as a Mission Control drill-down for
        operators who want the original dashboard layout.
        """
        conn = get_conn()
        m = vmode.get_mode()
        states = vmode.strategy_states(m)
        clause, params = vmode.in_clause(states)
        strategies = conn.execute(
            f"SELECT * FROM strategies WHERE state {clause} ORDER BY created_at", params,
        ).fetchall()
        tmode = vmode.trade_mode(m)
        open_trades = list_trades(conn, open_only=True, mode=tmode)
        all_trades = list_trades(conn, mode=tmode)
        # Wash-sale + PDT are live-only concepts. In paper mode they're N/A.
        if m == "paper":
            wash = []
            pdt = 0
        else:
            wash = get_wash_sale_exposure(conn, mode="live")
            pdt = get_pdt_count(conn, mode="live")
        month_cost = get_monthly_cost(conn)
        events = get_events(conn, limit=10)
        conn.close()
        return render_template("index.html",
                               strategies=strategies,
                               open_trades=open_trades,
                               all_trades=all_trades,
                               wash_lots=wash,
                               pdt_count=pdt,
                               month_cost=month_cost,
                               events=events)

    @app.route("/desks")
    def desks_view():
        """Section shell for Desk workspaces backed by campaigns."""
        conn = get_conn()
        rows = jcampaigns.list_campaigns(conn)
        desks = []
        for r in rows:
            d = _present_campaign(r)
            d["days"] = jcampaigns.days_active(conn, r["id"])
            d["strategy_count"] = conn.execute(
                "SELECT COUNT(*) FROM strategies WHERE campaign_id=?", (r["id"],)
            ).fetchone()[0]
            d["open_trades"] = conn.execute(
                """SELECT COUNT(*) FROM trades t
                   JOIN strategies s ON s.id = t.strategy_id
                   WHERE s.campaign_id=? AND t.close_price IS NULL""",
                (r["id"],),
            ).fetchone()[0]
            desks.append(d)
        conn.close()
        return render_template("desks.html", desks=desks)

    @app.route("/approvals")
    def approvals_view():
        """Section shell for human decision queues."""
        conn = get_conn()
        proposed = list_strategies(conn, state="PROPOSED")
        candidates = conn.execute(
            """SELECT c.*, s.name AS strategy_name, s.state AS strategy_state
               FROM candidates c JOIN strategies s ON s.id = c.strategy_id
               WHERE c.disposition='pending'
               ORDER BY c.created_at DESC LIMIT 25"""
        ).fetchall()
        review_ok = is_review_current(conn)
        promotion_events = conn.execute(
            """SELECT * FROM events
               WHERE event_type IN ('promotion_blocked', 'promotion_force')
               ORDER BY created_at DESC LIMIT 10"""
        ).fetchall()
        conn.close()
        return render_template(
            "approvals.html",
            proposed=proposed,
            candidates=candidates,
            review_ok=review_ok,
            promotion_events=promotion_events,
        )

    @app.route("/activity")
    def activity_view():
        """Read-only aggregate activity surface; /actions remains scheduler control."""
        conn = get_conn()
        events = get_events(conn, limit=25)
        runs = conn.execute(
            "SELECT * FROM scheduler_runs ORDER BY started_at DESC LIMIT 25"
        ).fetchall()
        recent_candidates = conn.execute(
            "SELECT * FROM candidates ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        recent_trades = list_trades(conn, mode=vmode.trade_mode(vmode.get_mode()))[:10]
        history = []
        for e in events:
            d = dict(e)
            history.append({
                "ts": d.get("created_at"), "kind": "event", "type": d.get("event_type"),
                "severity": d.get("severity") or "info", "source": d.get("source") or "journal",
                "message": d.get("message") or "",
            })
        for r in runs:
            d = dict(r)
            history.append({
                "ts": d.get("started_at"), "kind": "job", "type": d.get("job_id"),
                "severity": "error" if d.get("status") == "error" else "info",
                "source": "supervisor",
                "message": d.get("result_summary") or d.get("error") or f"status={d.get('status')}",
            })
        history.sort(key=lambda x: x["ts"] or "", reverse=True)
        conn.close()
        return render_template(
            "activity.html",
            history=history[:40],
            recent_candidates=recent_candidates,
            recent_trades=recent_trades,
        )

    @app.route("/system")
    def system_view():
        """System section split into Operations, Configuration, and Introspection."""
        from heron.runtime.supervisor import DEFAULT_JOBS

        conn = get_conn()
        recent_events = get_events(conn, limit=10)
        recent_commands = conn.execute(
            "SELECT * FROM scheduler_commands ORDER BY id DESC LIMIT 5"
        ).fetchall()
        job_count = len(DEFAULT_JOBS)
        conn.close()
        return render_template(
            "system.html",
            recent_events=recent_events,
            recent_commands=recent_commands,
            job_count=job_count,
        )

    def mission_control():
        """Inbox / Actions / State — the unified operator surface (C1+C2)."""
        from heron.config import MONTHLY_COST_CEILING
        from heron.strategy.policy import (
            assemble_state, evaluate_policies, current_system_mode,
        )
        from heron.strategy.portfolio import compute_allocations
        from heron.research.audit import contamination_audit
        from heron.runtime.supervisor import DEFAULT_JOBS

        conn = get_conn()
        m = vmode.get_mode()
        tmode = vmode.trade_mode(m)

        # ── Inbox sources ────────────────────────────────────────────────
        proposed = list_strategies(conn, state="PROPOSED")
        pending_candidates = []
        try:
            states = vmode.strategy_states(m)
            clause, params = vmode.in_clause(states)
            rows = conn.execute(
                f"SELECT c.* FROM candidates c JOIN strategies s ON s.id=c.strategy_id "
                f"WHERE c.disposition='pending' AND s.state {clause} "
                f"ORDER BY c.created_at DESC LIMIT 10",
                params,
            ).fetchall()
            pending_candidates = [dict(r) for r in rows]
        except Exception:
            pass

        sys_mode = current_system_mode(conn)
        try:
            pol_state = assemble_state(conn, mode=tmode or "")
            pol_actions = evaluate_policies(pol_state)
        except Exception:
            pol_state, pol_actions = {}, []
        fired_actions = [a for a in pol_actions if a.get("action") not in ("ok", "error")]

        # Monthly review status — operator owes a review if not current.
        try:
            review_ok = is_review_current(conn)
        except Exception:
            review_ok = True

        # Contamination findings — cheap AST walk over strategy modules.
        try:
            contamination = contamination_audit("heron/strategy")
        except Exception:
            contamination = []

        # ── State snapshot ───────────────────────────────────────────────
        open_trades = list_trades(conn, open_only=True, mode=tmode)
        all_trades = list_trades(conn, mode=tmode)
        month_cost = get_monthly_cost(conn) or 0.0
        ceiling = MONTHLY_COST_CEILING or 45.0
        try:
            equity = pol_state.get("equity") or 0.0
            allocs = compute_allocations(conn, equity, mode=tmode) if equity else {}
        except Exception:
            allocs = {}
        events = get_events(conn, limit=8)

        # ── Action shortcuts ─────────────────────────────────────────────
        valid_jobs = {jid for jid, _f, _t, _d in DEFAULT_JOBS}
        immediate_actions = [
            {"job_id": "executor_cycle", "label": "Executor cycle",
             "hint": "Process pending candidates + reconcile."},
            {"job_id": "research_premarket", "label": "Premarket research",
             "hint": "News + classify + propose candidates."},
            {"job_id": "eod_debrief", "label": "EOD debrief",
             "hint": "Daily summary + journal flush."},
            {"job_id": "daily_health", "label": "Health check",
             "hint": "Resilience + SLOs."},
        ]
        immediate_actions = [a for a in immediate_actions if a["job_id"] in valid_jobs]

        conn.close()

        inbox_count = (len(proposed) + len(pending_candidates) +
                       len(fired_actions) + (0 if review_ok else 1) +
                       len(contamination))

        return render_template(
            "mission_control.html",
            proposed=proposed,
            pending_candidates=pending_candidates,
            sys_mode=sys_mode,
            policy_state=pol_state,
            fired_actions=fired_actions,
            review_ok=review_ok,
            contamination=contamination,
            open_trades=open_trades,
            all_trades=all_trades,
            month_cost=month_cost,
            cost_ceiling=ceiling,
            allocations=allocs,
            equity=pol_state.get("equity") or 0.0,
            recent_events=events,
            immediate_actions=immediate_actions,
            inbox_count=inbox_count,
        )

    @app.route("/strategies")
    def strategies_view():
        """Strategy portfolio — filtered by mode."""
        conn = get_conn()
        m = vmode.get_mode()
        states = vmode.strategy_states(m)
        clause, params = vmode.in_clause(states)
        strategies = conn.execute(
            f"""SELECT s.*, c.name AS desk_campaign_name
                FROM strategies s LEFT JOIN campaigns c ON c.id=s.campaign_id
                WHERE s.state {clause} ORDER BY s.created_at""", params,
        ).fetchall()
        strategies = [_present_strategy(s) for s in strategies]
        conn.close()
        return render_template("strategies.html", strategies=strategies)

    @app.route("/strategy/<strategy_id>")
    def strategy_detail(strategy_id):
        """Single strategy detail with trades and state history."""
        from heron.backtest.sweep import SWEEPABLE_AXES
        conn = get_conn()
        strat = get_strategy(conn, strategy_id)
        if not strat:
            conn.close()
            abort(404)
        history = get_state_history(conn, strategy_id)
        trades = list_trades(conn, strategy_id=strategy_id)
        candidates = list_candidates(conn, strategy_id=strategy_id)
        # Sweeps for this strategy.
        sweeps = conn.execute(
            """SELECT sweep_id, COUNT(*) AS n, MIN(created_at) AS started_at,
                      MAX(total_return) AS best_return
               FROM backtest_reports
               WHERE strategy_id=? AND sweep_id IS NOT NULL
               GROUP BY sweep_id ORDER BY started_at DESC LIMIT 10""",
            (strategy_id,),
        ).fetchall()
        sweeps = [dict(r) for r in sweeps]
        # Strategy config keys we know how to sweep.
        try:
            cfg = json.loads(strat["config"]) if strat["config"] else {}
        except (TypeError, json.JSONDecodeError):
            cfg = {}
        sweep_options = [k for k in SWEEPABLE_AXES if k in cfg]
        conn.close()
        return render_template("strategy_detail.html",
                               strategy=strat, history=history,
                               trades=trades, candidates=candidates,
                               sweeps=sweeps, sweep_options=sweep_options,
                               strategy_config=cfg)

    @app.route("/trades")
    def trades_view():
        """Trade log — filtered by global mode."""
        from heron.journal.trades import summarize_trades
        conn = get_conn()
        trades = list_trades(conn, mode=vmode.trade_mode(vmode.get_mode()))
        summary = summarize_trades(trades)
        conn.close()
        return render_template("trades.html", trades=trades, summary=summary)

    @app.route("/candidates")
    def candidates_view():
        """Candidate queue — filtered by global mode (via strategy state)."""
        conn = get_conn()
        disposition = request.args.get("disposition")
        m = vmode.get_mode()
        states = vmode.strategy_states(m)
        clause, params = vmode.in_clause(states)
        # Join candidates to strategies so mode filters through to candidates.
        sql = (
            "SELECT c.* FROM candidates c "
            "JOIN strategies s ON s.id = c.strategy_id "
            f"WHERE s.state {clause}"
        )
        qparams: list[object] = list(params)
        if disposition:
            sql += " AND c.disposition=?"
            qparams.append(disposition)
        sql += " ORDER BY c.created_at DESC"
        candidates = conn.execute(sql, qparams).fetchall()
        conn.close()
        return render_template("candidates.html",
                               candidates=candidates, disposition=disposition)

    @app.route("/health")
    def health_view():
        """Back-compat redirect — Health is now a section of /resilience."""
        return redirect(url_for("resilience_view") + "#health", code=301)

    @app.route("/proposals")
    def proposals_view():
        """Strategy inbox — proposals awaiting operator approval."""
        conn = get_conn()
        proposed = list_strategies(conn, state="PROPOSED")
        recent_retired = conn.execute(
            "SELECT * FROM strategies WHERE state='RETIRED' ORDER BY retired_at DESC LIMIT 10"
        ).fetchall()
        conn.close()
        return render_template("proposals.html",
                               proposed=proposed, recent_retired=recent_retired)

    @app.route("/audits")
    def audits_view():
        """Audit dashboard — trust score + recent audits."""
        from heron.research.audit import compute_trust_score
        from heron.journal.ops import get_audits
        conn = get_conn()
        score = compute_trust_score(conn)
        recent = get_audits(conn, limit=30)
        conn.close()
        return render_template("audits.html", score=score, audits=recent)

    @app.route("/audits/contamination")
    def audits_contamination_view():
        """Static AST scan for PIT-leak patterns in strategy code."""
        import os as _os
        from heron.research.audit import contamination_audit
        target = request.args.get("path") or _os.path.join("heron", "strategy")
        findings = contamination_audit(target)
        return render_template("audits_contamination.html",
                               target=target, findings=findings)

    @app.route("/portfolio")
    def portfolio_view():
        """Per-strategy capital allocations after parity / drawdown / crowding."""
        from heron.config import PORTFOLIO_CONFIG
        from heron.journal.strategies import list_strategies
        from heron.strategy.portfolio import (
            compute_allocations, _parity_factor, _strategy_drawdown, _strategy_tags,
        )
        from heron.strategy.policy import current_system_mode

        mode = request.args.get("mode") or "paper"
        conn = get_conn()
        try:
            equity = float(request.args.get("equity") or 0) or 10000.0
            allocs = compute_allocations(conn, equity, mode=mode)
            states = ("PAPER", "LIVE") if mode == "live" else ("PAPER",)
            rows = [s for s in list_strategies(conn) if s["state"] in states]
            allocations = []
            for s in rows:
                pf = _parity_factor(s, conn)
                dd = _strategy_drawdown(conn, s["id"], mode)
                budget_dollars = float(s["drawdown_budget_pct"] or 0.05) * equity
                df = 1.0 if budget_dollars <= 0 else max(0.0, min(1.0, 1.0 + dd / budget_dollars))
                allocations.append({
                    "id": s["id"], "state": s["state"],
                    "tags": _strategy_tags(s),
                    "base_pct": min(float(s["max_capital_pct"] or 0.15),
                                    float(PORTFOLIO_CONFIG.get("max_per_strategy", 0.30))),
                    "parity_factor": pf,
                    "drawdown_factor": df,
                    "alloc_pct": allocs.get(s["id"], 0.0),
                })
            allocations.sort(key=lambda r: r["alloc_pct"], reverse=True)
            sys_mode = current_system_mode(conn)
        finally:
            conn.close()
        return render_template(
            "portfolio.html",
            mode=mode, equity=equity, allocations=allocations,
            total_pct=sum(allocs.values()),
            max_total=float(PORTFOLIO_CONFIG.get("max_total_exposure", 0.80)),
            system_mode=sys_mode,
            active_path="/portfolio",
        )

    @app.route("/policies")
    def policies_view():
        """Show policy rules, current state evaluation, and system mode."""
        from heron.config import POLICIES
        from heron.strategy.policy import (
            assemble_state, evaluate_policies, current_system_mode,
        )
        mode = request.args.get("mode") or "paper"
        conn = get_conn()
        try:
            state = assemble_state(conn, mode=mode, equity=10000.0)
            actions = evaluate_policies(state)
            fired_ids = {a["id"] for a in actions}
            sys_mode = current_system_mode(conn)
            events = conn.execute(
                "SELECT * FROM events WHERE event_type='system_mode' "
                "ORDER BY created_at DESC LIMIT 25"
            ).fetchall()
        finally:
            conn.close()
        return render_template(
            "policies.html",
            rules=POLICIES, state=state, fired_ids=fired_ids,
            system_mode=sys_mode, events=events,
            active_path="/policies",
        )

    @app.route("/policies/override", methods=["POST"])
    def policies_override():
        """Operator-forced system mode transition (NORMAL/DERISK/SAFE)."""
        from heron.strategy.policy import set_system_mode, VALID_MODES
        new_mode = (request.form.get("mode") or "").strip().upper()
        reason = (request.form.get("reason") or "").strip()
        if new_mode not in VALID_MODES:
            flash(f"Invalid mode: {new_mode}", "error")
            return redirect(url_for("policies_view"))
        if not reason:
            flash("Reason is required for an override.", "error")
            return redirect(url_for("policies_view"))
        conn = get_conn()
        try:
            prior = set_system_mode(conn, new_mode, reason=reason,
                                    operator="dashboard", triggered_by=["operator_override"])
            log_event(conn, "system_mode_override",
                      f"{prior} -> {new_mode}: {reason}",
                      severity="warn", source="dashboard.policies")
        finally:
            conn.close()
        flash(f"System mode set to {new_mode}.", "success")
        return redirect(url_for("policies_view"))

    @app.route("/glossary")
    def glossary_view():
        """Vocabulary reference for HERON's domain terms."""
        return render_template("glossary.html")

    @app.route("/setup", methods=["GET", "POST"])
    def setup_view():
        """First-run setup wizard. Mirrors `heron init` CLI."""
        from heron.runtime.setup import (
            plan_initial_setup, apply_initial_setup, is_already_setup,
            SetupAlreadyDoneError,
        )

        conn = get_conn()
        already = is_already_setup(conn)

        if request.method == "GET":
            conn.close()
            return render_template("setup.html",
                                   already=already, plan=None, applied=None,
                                   form={
                                       "capital": "500",
                                       "campaign_name": DEFAULT_DESK_NAME,
                                       "cadence": "premarket_eod",
                                       "max_capital_pct": "0.15",
                                       "max_positions": "3",
                                       "drawdown_budget_pct": "0.05",
                                   })

        # POST: action = "plan" or "apply"
        action = request.form.get("action", "plan")
        form = {
            "capital": request.form.get("capital", "500"),
            "campaign_name": request.form.get("campaign_name", DEFAULT_DESK_NAME),
            "cadence": request.form.get("cadence", "premarket_eod"),
            "max_capital_pct": request.form.get("max_capital_pct", "0.15"),
            "max_positions": request.form.get("max_positions", "3"),
            "drawdown_budget_pct": request.form.get("drawdown_budget_pct", "0.05"),
        }

        try:
            plan = plan_initial_setup(
                capital_usd=float(form["capital"]),
                campaign_name=form["campaign_name"],
                cadence=form["cadence"],
                max_capital_pct=float(form["max_capital_pct"]),
                max_positions=int(form["max_positions"]),
                drawdown_budget_pct=float(form["drawdown_budget_pct"]),
            )
        except (ValueError, TypeError) as e:
            flash(str(e), "error")
            conn.close()
            return render_template("setup.html",
                                   already=already, plan=None, applied=None, form=form)

        applied = None
        if action == "apply":
            if already:
                flash("Already set up — refusing to re-seed.", "error")
            else:
                try:
                    applied = apply_initial_setup(conn, plan)
                    flash(f"Setup complete: Desk {applied['campaign_id']} "
                          f"with {len(applied['strategy_ids'])} strategies.",
                          "success")
                except SetupAlreadyDoneError as e:
                    flash(str(e), "error")
                except Exception as e:  # noqa: BLE001
                    flash(f"Apply failed: {e}", "error")

        conn.close()
        return render_template("setup.html",
                               already=already or applied is not None,
                               plan=plan, applied=applied, form=form)

    # ── Research Agents page ──────────────────────────────
    # Tracks the state of any running research pass so the UI can poll.
    research_state = {"running": False, "started_at": None,
                      "finished_at": None, "result": None, "error": None}
    research_lock = threading.Lock()

    def _run_research_bg(strategy_id, pass_type, lookback_hours):
        """Background worker that executes one research pass and captures result."""
        from heron.research.orchestrator import ResearchPass
        try:
            with ResearchPass() as rp:
                result = rp.run(strategy_id=strategy_id,
                                pass_type=pass_type,
                                lookback_hours=lookback_hours)
            with research_lock:
                research_state["result"] = result
                research_state["error"] = None
        except Exception as e:
            with research_lock:
                research_state["error"] = str(e)
                research_state["result"] = None
        finally:
            from heron.util import utc_now_iso
            with research_lock:
                research_state["running"] = False
                research_state["finished_at"] = utc_now_iso()

    @app.route("/agents")
    def agents_view():
        """Live view of research agents — LLM calls, outputs, activity feed."""
        conn = get_conn()
        # Recent research events (any source that starts with 'research')
        events = conn.execute(
            "SELECT * FROM events WHERE source = 'research' "
            "OR event_type LIKE 'research_%' ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
        # Latest audits (contain raw LLM outputs)
        audits = get_audits(conn, limit=15)
        # Recent candidates with thesis text
        recent_candidates = conn.execute(
            "SELECT * FROM candidates WHERE thesis IS NOT NULL AND thesis != '' "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        # Today's cost breakdown by model (shows which agent is active)
        today_costs = get_daily_costs(conn)
        # Available strategies for the "run research" form — limited to current mode.
        m = vmode.get_mode()
        states = vmode.strategy_states(m)
        clause, params = vmode.in_clause(states)
        strategies = conn.execute(
            f"SELECT * FROM strategies WHERE state {clause} ORDER BY created_at", params,
        ).fetchall()
        conn.close()
        with research_lock:
            state = dict(research_state)
        return render_template("agents.html",
                               events=events, audits=audits,
                               recent_candidates=recent_candidates,
                               today_costs=today_costs,
                               strategies=strategies,
                               research_state=state)

    @app.route("/agents/status")
    def agents_status():
        """JSON endpoint for HTMX polling — current research state."""
        with research_lock:
            state = dict(research_state)
        return jsonify(state)

    @app.route("/research/run", methods=["POST"])
    def research_run():
        """Kick off a research pass in a background thread."""
        with research_lock:
            if research_state["running"]:
                flash("A research pass is already running.", "warning")
                return redirect(url_for("agents_view"))
            from heron.util import utc_now_iso
            research_state["running"] = True
            research_state["started_at"] = utc_now_iso()
            research_state["finished_at"] = None
            research_state["result"] = None
            research_state["error"] = None

        strategy_id = request.form.get("strategy_id", "pead_v1")
        pass_type = request.form.get("pass_type", "midday")
        try:
            lookback_hours = int(request.form.get("lookback_hours", "6"))
        except ValueError:
            lookback_hours = 6

        t = threading.Thread(
            target=_run_research_bg,
            args=(strategy_id, pass_type, lookback_hours),
            daemon=True,
        )
        t.start()
        flash(f"Research pass started: {pass_type} for {strategy_id}", "success")
        return redirect(url_for("agents_view"))

    @app.route("/candidate/<int:candidate_id>")
    def candidate_detail(candidate_id):
        """Single candidate — full thesis and related audit entries."""
        conn = get_conn()
        cand = get_candidate(conn, candidate_id)
        if not cand:
            conn.close()
            flash(f"Candidate #{candidate_id} not found", "error")
            return redirect(url_for("candidates_view"))
        related_audits = conn.execute(
            "SELECT * FROM audits WHERE candidate_id=? ORDER BY created_at DESC",
            (candidate_id,),
        ).fetchall()
        signal_trace = get_signal_for_candidate(conn, candidate_id)
        conn.close()
        return render_template("candidate_detail.html",
                               c=cand, audits=related_audits,
                               signal_trace=signal_trace)

    @app.route("/backtests")
    def backtests_view():
        """Backtest report browser."""
        from heron.backtest import list_reports
        conn = get_conn()
        reports = list_reports(conn, limit=50)
        conn.close()
        return render_template("backtests.html", reports=reports)

    @app.route("/backtests/<int:report_id>")
    def backtest_detail(report_id):
        """Single backtest report detail with equity / drawdown / overlays."""
        import json as _json
        from heron.backtest import (
            get_report, spy_benchmark_curve, drawdown_curve, find_baseline_report,
        )
        from heron.backtest.regimes import vol_buckets_from_spy, tag_trades, regime_metrics
        from heron.backtest.walkforward import list_walkforward_children
        from heron.data.cache import get_bars
        conn = get_conn()
        report = get_report(conn, report_id)
        if not report:
            conn.close()
            flash(f"Backtest report {report_id} not found", "error")
            return redirect(url_for("backtests_view"))
        metrics = _json.loads(report["metrics_json"])
        trades = _json.loads(report["trades_json"])
        params = _json.loads(report["params_json"])
        equity_curve = metrics.get("equity_curve") or []
        if not equity_curve:
            equity_curve = [
                {"date": report["start_date"], "equity": 0.0},
                {"date": report["end_date"], "equity": 0.0},
            ]
        dd_curve = drawdown_curve(equity_curve)
        initial = equity_curve[0]["equity"] if equity_curve else 0.0
        spy_curve = spy_benchmark_curve(
            conn, report["start_date"], report["end_date"], initial=initial,
        )
        baseline_report = find_baseline_report(
            conn, report["strategy_id"], report["start_date"], report["end_date"],
        )
        baseline_curve = []
        if baseline_report:
            try:
                bm = _json.loads(baseline_report["metrics_json"])
                baseline_curve = bm.get("equity_curve") or []
            except (TypeError, _json.JSONDecodeError):
                baseline_curve = []

        # Regime breakdown — prefer stored value (computed at save time);
        # fall back to live compute for legacy reports.
        regimes = metrics.get("regime_breakdown")
        if (regimes is None or (isinstance(regimes, dict) and regimes.get("available") is False)) and trades:
            spy_bars = get_bars(conn, "SPY", "1Day",
                                start=report["start_date"], end=report["end_date"])
            buckets = vol_buckets_from_spy(spy_bars) if spy_bars else {}
            tagged = tag_trades(trades, buckets)
            regimes = regime_metrics(tagged)

        # Parity verdict — pulled from metrics_json (saved at write time).
        parity = metrics.get("parity")

        # Walk-forward children list if this report has one.
        wf_id = report["walkforward_id"] if "walkforward_id" in report.keys() else None
        wf_children = list_walkforward_children(conn, wf_id) if wf_id else []
        # Drop self if the parent shares its own walkforward_id (filter already in helper).
        is_walkforward_parent = bool(params.get("walkforward")) and wf_id is not None

        conn.close()
        return render_template(
            "backtest_detail.html",
            report=report, metrics=metrics, trades=trades, params=params,
            equity_curve=equity_curve, dd_curve=dd_curve,
            spy_curve=spy_curve, baseline_curve=baseline_curve,
            baseline_report=baseline_report,
            regimes=regimes,
            parity=parity,
            wf_children=wf_children,
            is_walkforward_parent=is_walkforward_parent,
        )

    @app.route("/strategy/<strategy_id>/backtest", methods=["POST"])
    def strategy_backtest(strategy_id):
        """Trigger a backtest from the strategy detail page.

        Synchronous — small windows finish in seconds. Logs an event and
        redirects to the new report's detail page on success.
        """
        from heron.backtest import run_strategy_backtest
        start = request.form.get("start") or None
        end = request.form.get("end") or None
        seeder = request.form.get("seeder") or "synthetic"
        if seeder not in ("synthetic", "real"):
            seeder = "synthetic"
        try:
            seed = int(request.form.get("seed") or 0)
        except ValueError:
            seed = 0
        try:
            equity = float(request.form.get("equity") or 100_000.0)
        except ValueError:
            equity = 100_000.0
        conn = get_conn()
        try:
            result = run_strategy_backtest(
                conn, strategy_id,
                start=start, end=end, seed=seed, initial_equity=equity,
                save=True, seeder=seeder,
            )
        except ValueError as e:
            flash(str(e), "error")
            conn.close()
            return redirect(url_for("strategy_detail", strategy_id=strategy_id))
        log_event(
            conn, "backtest_run",
            f"Backtest #{result['report_id']} for {strategy_id} (seeder={seeder}): "
            f"{result['metrics']['n_trades']} trades, "
            f"{result['metrics']['total_return']:+.2%}",
            severity="info", source="dashboard.backtest",
        )
        conn.close()
        flash(f"Backtest #{result['report_id']} complete (seeder={seeder})", "success")
        return redirect(url_for("backtest_detail", report_id=result["report_id"]))

    @app.route("/strategy/<strategy_id>/walkforward", methods=["POST"])
    def strategy_walkforward(strategy_id):
        """Run a walk-forward backtest from the strategy detail page.

        Synchronous; tests with small windows complete in a few seconds.
        """
        import json as _json
        from heron.backtest.walkforward import run_walkforward
        from heron.backtest.sweep import parse_axes
        from heron.journal.strategies import get_strategy
        start = request.form.get("start") or None
        end = request.form.get("end") or None
        seeder = request.form.get("seeder") or "synthetic"
        if seeder not in ("synthetic", "real"):
            seeder = "synthetic"
        objective = request.form.get("objective") or "sharpe"
        if objective not in ("sharpe", "total_return", "win_rate", "avg_trade_pnl"):
            objective = "sharpe"
        try:
            train = int(request.form.get("train") or 6)
            test = int(request.form.get("test") or 3)
            step = int(request.form.get("step") or 3)
            seed = int(request.form.get("seed") or 0)
            equity = float(request.form.get("equity") or 100_000.0)
        except ValueError:
            flash("Invalid numeric input.", "error")
            return redirect(url_for("strategy_detail", strategy_id=strategy_id))
        if not start or not end:
            flash("Walk-forward requires start and end dates.", "error")
            return redirect(url_for("strategy_detail", strategy_id=strategy_id))

        # Optional axes: textarea/input named "vary" with one spec per line
        # ("stop_mult=1.0,1.5,2.0"). Empty/missing → no fitting.
        vary_raw = (request.form.get("vary") or "").strip()
        axes = None
        conn = get_conn()
        if vary_raw:
            specs = [line.strip() for line in vary_raw.splitlines() if line.strip()]
            s = get_strategy(conn, strategy_id)
            try:
                base_cfg = _json.loads(s["config"]) if s and s["config"] else {}
            except (TypeError, _json.JSONDecodeError):
                base_cfg = {}
            try:
                axes = parse_axes(specs, base_cfg)
            except ValueError as e:
                flash(f"Bad fit axes: {e}", "error")
                conn.close()
                return redirect(url_for("strategy_detail", strategy_id=strategy_id))

        try:
            res = run_walkforward(
                conn, strategy_id,
                start=start, end=end,
                train_months=train, test_months=test, step_months=step,
                seed=seed, initial_equity=equity, seeder=seeder,
                axes=axes, objective=objective,
            )
        except ValueError as e:
            flash(str(e), "error")
            conn.close()
            return redirect(url_for("strategy_detail", strategy_id=strategy_id))
        log_event(
            conn, "backtest_walkforward",
            f"WF {res['walkforward_id']} for {strategy_id}: "
            f"{res['metrics']['n_windows']} windows, "
            f"{res['metrics']['n_trades']} trades, "
            f"{res['metrics']['total_return']:+.2%}"
            + (f" (fit axes={axes}, obj={objective})" if axes else ""),
            severity="info", source="dashboard.walkforward",
        )
        conn.close()
        flash(
            f"Walk-forward complete: {res['metrics']['n_windows']} windows"
            + (" (with fitting)" if axes else "") + ".",
            "success",
        )
        return redirect(url_for("backtest_detail", report_id=res["parent_report_id"]))

    @app.route("/strategy/<strategy_id>/sweep", methods=["POST"])
    def strategy_sweep(strategy_id):
        """Cartesian param sweep posted from the strategy detail page.

        Form fields: start, end, seed, equity, seeder, and one entry per axis
        named `vary_<axis>` containing comma-separated values.
        """
        from heron.backtest.sweep import parse_axes, run_sweep, SWEEPABLE_AXES
        start = request.form.get("start") or None
        end = request.form.get("end") or None
        seeder = request.form.get("seeder") or "synthetic"
        if seeder not in ("synthetic", "real"):
            seeder = "synthetic"
        try:
            seed = int(request.form.get("seed") or 0)
            equity = float(request.form.get("equity") or 100_000.0)
        except ValueError:
            flash("Invalid numeric input.", "error")
            return redirect(url_for("strategy_detail", strategy_id=strategy_id))
        # Build axis specs from form.
        axis_specs = []
        for axis in SWEEPABLE_AXES:
            raw = (request.form.get(f"vary_{axis}") or "").strip()
            if raw:
                axis_specs.append(f"{axis}={raw}")
        if not axis_specs:
            flash("Provide at least one axis to sweep.", "error")
            return redirect(url_for("strategy_detail", strategy_id=strategy_id))

        conn = get_conn()
        strat = get_strategy(conn, strategy_id)
        if not strat:
            conn.close()
            abort(404)
        try:
            base_cfg = json.loads(strat["config"]) if strat["config"] else {}
        except (TypeError, json.JSONDecodeError):
            base_cfg = {}
        try:
            axes = parse_axes(axis_specs, base_cfg)
            res = run_sweep(conn, strategy_id, axes,
                            start=start, end=end, seed=seed,
                            initial_equity=equity, seeder=seeder)
        except ValueError as e:
            flash(str(e), "error")
            conn.close()
            return redirect(url_for("strategy_detail", strategy_id=strategy_id))

        log_event(
            conn, "backtest_sweep",
            f"Sweep {res['sweep_id']} for {strategy_id}: "
            f"{res['n_saved']}/{res['n_combos']} combos saved",
            severity="info", source="dashboard.sweep",
        )
        conn.close()
        flash(f"Sweep complete: {res['n_saved']} combos.", "success")
        return redirect(url_for("backtest_sweep_detail", sweep_id=res["sweep_id"]))

    @app.route("/backtests/sweeps/<sweep_id>")
    def backtest_sweep_detail(sweep_id):
        """Render a sweep matrix: each row = one combo's report + metrics."""
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM backtest_reports WHERE sweep_id=? ORDER BY total_return DESC",
            (sweep_id,),
        ).fetchall()
        if not rows:
            conn.close()
            abort(404)
        reports = []
        all_axes = set()
        for r in rows:
            d = dict(r)
            try:
                params = json.loads(d.get("params_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                params = {}
            try:
                metrics = json.loads(d.get("metrics_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            overrides = params.get("sweep_overrides", {})
            all_axes.update(overrides.keys())
            reports.append({
                "report_id": d["id"],
                "overrides": overrides,
                "total_return": d.get("total_return"),
                "max_drawdown": d.get("max_drawdown"),
                "sharpe": d.get("sharpe"),
                "n_trades": d.get("n_trades"),
                "win_rate": d.get("win_rate"),
                "metrics": metrics,
            })
        # Pick winner (best total_return). Operator can fork from there.
        winner = reports[0] if reports else None
        strategy_id = rows[0]["strategy_id"]
        conn.close()
        return render_template(
            "backtest_sweep.html",
            sweep_id=sweep_id,
            strategy_id=strategy_id,
            reports=reports,
            axes=sorted(all_axes),
            winner=winner,
        )

    @app.route("/backtests/sweeps/<sweep_id>/promote/<int:report_id>", methods=["POST"])
    def backtest_sweep_promote(sweep_id, report_id):
        """Fork the sweep winner into a new PROPOSED strategy with the override applied."""
        from heron.journal.strategies import create_strategy
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM backtest_reports WHERE id=? AND sweep_id=?",
            (report_id, sweep_id),
        ).fetchone()
        if not row:
            conn.close()
            abort(404)
        try:
            params = json.loads(row["params_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            params = {}
        overrides = params.get("sweep_overrides", {})
        if not overrides:
            flash("No overrides recorded; cannot fork.", "error")
            conn.close()
            return redirect(url_for("backtest_sweep_detail", sweep_id=sweep_id))

        parent = get_strategy(conn, row["strategy_id"])
        if not parent:
            conn.close()
            abort(404)
        try:
            base_cfg = json.loads(parent["config"]) if parent["config"] else {}
        except (TypeError, json.JSONDecodeError):
            base_cfg = {}
        merged = dict(base_cfg)
        merged.update(overrides)

        # New strategy id: parent + sweep_id short tag.
        new_id = f"{parent['id']}_swp_{sweep_id[:6]}"
        if get_strategy(conn, new_id):
            flash(f"Strategy {new_id} already exists.", "error")
            conn.close()
            return redirect(url_for("backtest_sweep_detail", sweep_id=sweep_id))

        create_strategy(
            conn,
            id=new_id,
            name=f"{parent['name']} (sweep #{report_id})",
            description=f"Forked from {parent['id']} via sweep {sweep_id}.",
            rationale="Sweep winner overrides: " + ", ".join(
                f"{k}={v}" for k, v in overrides.items()),
            config=merged,
            parent_id=parent["id"],
            template=parent["template"] if "template" in parent.keys() else None,
        )
        log_event(
            conn, "strategy_forked",
            f"Forked {new_id} from {parent['id']} via sweep {sweep_id} report #{report_id}",
            severity="info", source="dashboard.sweep",
        )
        conn.close()
        flash(f"Forked {new_id} as PROPOSED.", "success")
        return redirect(url_for("strategy_detail", strategy_id=new_id))


    @app.route("/data/earnings", methods=["GET"])
    def data_earnings_page():
        """Earnings calendar cache: list, fetch, configure."""
        from heron.data.earnings import get_earnings_events
        from heron.data.cache import get_conn as get_cache_conn, init_db as init_cache_db
        from heron.config import WATCHLIST, FINNHUB_API_KEY

        cache_conn = get_cache_conn()
        init_cache_db(cache_conn)
        try:
            args = request.args
            start = args.get("start") or None
            end = args.get("end") or None
            ticker = (args.get("ticker") or "").upper().strip() or None
            min_surp = args.get("min_surprise", "").strip()
            try:
                min_surp_val = float(min_surp) if min_surp else None
            except ValueError:
                min_surp_val = None
            rows = get_earnings_events(
                cache_conn,
                start=start, end=end,
                tickers=[ticker] if ticker else None,
                min_abs_surprise=min_surp_val,
            )
            stats = {
                "total": len(rows),
                "with_surprise": sum(1 for r in rows if r.get("surprise_pct") is not None),
                "tickers": len({r["ticker"] for r in rows}),
            }
        finally:
            cache_conn.close()

        return render_template(
            "data_earnings.html",
            rows=rows[:500],
            row_count=len(rows),
            truncated=len(rows) > 500,
            stats=stats,
            watchlist=WATCHLIST,
            has_api_key=bool(FINNHUB_API_KEY),
            filters={"start": start or "", "end": end or "",
                     "ticker": ticker or "", "min_surprise": min_surp},
            active_path="/data/earnings",
        )

    @app.route("/data/earnings/fetch", methods=["POST"])
    def data_earnings_fetch_route():
        """Trigger Finnhub fetch for [start, end] across the configured universe."""
        from heron.data.earnings import fetch_and_cache
        from heron.data.cache import get_conn as get_cache_conn, init_db as init_cache_db
        from heron.config import WATCHLIST

        start = request.form.get("start", "").strip()
        end = request.form.get("end", "").strip()
        universe_raw = request.form.get("universe", "").strip()
        if not start or not end:
            flash("start and end are required (YYYY-MM-DD).", "error")
            return redirect(url_for("data_earnings_page"))
        universe = (
            [t.strip().upper() for t in universe_raw.split(",") if t.strip()]
            if universe_raw else list(WATCHLIST)
        )
        cache_conn = get_cache_conn()
        init_cache_db(cache_conn)
        try:
            n = fetch_and_cache(cache_conn, start, end, universe=universe)
        except RuntimeError as e:
            flash(str(e), "error")
            cache_conn.close()
            return redirect(url_for("data_earnings_page"))
        finally:
            cache_conn.close()

        # Log to journal so the action is visible in History.
        conn = get_conn()
        log_event(
            conn, "earnings_fetched",
            f"Cached {n} earnings events {start} → {end} for {len(universe)} tickers",
            severity="info", source="dashboard.data.earnings",
        )
        conn.close()
        flash(f"Cached {n} earnings events.", "success")
        return redirect(url_for("data_earnings_page",
                                start=start, end=end))

    @app.route("/data/universe", methods=["GET"])
    def data_universe_page():
        """Point-in-time universe snapshots — record + browse."""
        from heron.data.cache import get_conn as get_cache_conn, init_db as init_cache_db

        cache_conn = get_cache_conn()
        init_cache_db(cache_conn)
        try:
            dates = cache_conn.execute(
                "SELECT snapshot_date, COUNT(*) AS n, MAX(created_at) AS created_at "
                "FROM universe_snapshots GROUP BY snapshot_date ORDER BY snapshot_date DESC LIMIT 50"
            ).fetchall()
            snapshots = []
            for d in dates:
                tickers = [r[0] for r in cache_conn.execute(
                    "SELECT ticker FROM universe_snapshots WHERE snapshot_date=? ORDER BY ticker",
                    (d["snapshot_date"],),
                ).fetchall()]
                snapshots.append({
                    "snapshot_date": d["snapshot_date"],
                    "n": d["n"],
                    "created_at": d["created_at"],
                    "tickers": tickers,
                })
        finally:
            cache_conn.close()
        return render_template("data_universe.html", snapshots=snapshots,
                               active_path="/data/universe")

    @app.route("/data/universe/snapshot", methods=["POST"])
    def data_universe_snapshot_route():
        """Record a new universe snapshot."""
        from heron.data.cache import get_conn as get_cache_conn, init_db as init_cache_db
        from heron.util import utc_now_iso

        snap_date = request.form.get("snapshot_date", "").strip()
        tickers_raw = request.form.get("tickers", "").strip()
        note = request.form.get("note", "").strip() or None
        if not snap_date or not tickers_raw:
            flash("snapshot_date and tickers are required.", "error")
            return redirect(url_for("data_universe_page"))
        syms = sorted({t.strip().upper() for t in tickers_raw.split(",") if t.strip()})
        if not syms:
            flash("No tickers parsed.", "error")
            return redirect(url_for("data_universe_page"))
        now = utc_now_iso()
        cache_conn = get_cache_conn()
        init_cache_db(cache_conn)
        try:
            cache_conn.executemany(
                """INSERT OR REPLACE INTO universe_snapshots
                   (snapshot_date, ticker, source, note, created_at)
                   VALUES (?, ?, 'manual', ?, ?)""",
                [(snap_date, t, note, now) for t in syms],
            )
            cache_conn.commit()
        finally:
            cache_conn.close()
        conn = get_conn()
        log_event(conn, "universe_snapshot",
                  f"Recorded {len(syms)} tickers as universe at {snap_date}",
                  severity="info", source="dashboard.data.universe")
        conn.close()
        flash(f"Stored {len(syms)} tickers for {snap_date}.", "success")
        return redirect(url_for("data_universe_page"))

    @app.route("/costs")
    def costs_view():
        """Cost tracking (M14) — budget status + per-model breakdown."""
        from heron.research.cost_guard import check_budget
        conn = get_conn()
        budget = check_budget(conn)
        # Daily breakdown for current month
        year_month = budget["year_month"]
        daily = conn.execute(
            """SELECT date, SUM(cost_usd) AS cost, SUM(tokens_in) AS toks_in,
                      SUM(tokens_out) AS toks_out
               FROM cost_tracking WHERE date LIKE ?
               GROUP BY date ORDER BY date DESC""",
            (f"{year_month}%",),
        ).fetchall()
        by_model = conn.execute(
            """SELECT model, SUM(cost_usd) AS cost, SUM(tokens_in) AS toks_in,
                      SUM(tokens_out) AS toks_out, COUNT(*) AS n
               FROM cost_tracking WHERE date LIKE ?
               GROUP BY model ORDER BY cost DESC""",
            (f"{year_month}%",),
        ).fetchall()
        by_strategy = conn.execute(
            """SELECT COALESCE(strategy_id, '(unattributed)') AS strategy_id,
                      SUM(cost_usd) AS cost, COUNT(*) AS n
               FROM cost_tracking WHERE date LIKE ?
               GROUP BY strategy_id ORDER BY cost DESC""",
            (f"{year_month}%",),
        ).fetchall()
        conn.close()
        return render_template("costs.html", budget=budget,
                               daily=daily, by_model=by_model,
                               by_strategy=by_strategy)

    @app.route("/resilience")
    def resilience_view():
        """Resilience + Health — startup audits, shutdowns, secrets, costs, exposure."""
        import json as _json
        from heron.resilience import check_secrets_hygiene
        conn = get_conn()
        startup_rows = conn.execute(
            """SELECT * FROM events WHERE event_type='startup_audit'
               ORDER BY id DESC LIMIT 10"""
        ).fetchall()
        shutdown_rows = conn.execute(
            """SELECT * FROM events WHERE event_type='shutdown_graceful'
               ORDER BY id DESC LIMIT 10"""
        ).fetchall()

        def _parse(row):
            d = dict(row)
            try:
                d["details"] = _json.loads(d["details_json"]) if d.get("details_json") else {}
            except (_json.JSONDecodeError, TypeError):
                d["details"] = {}
            return d

        startups = [_parse(r) for r in startup_rows]
        shutdowns = [_parse(r) for r in shutdown_rows]
        secrets = check_secrets_hygiene()
        # Health section: exposure + cost panels.
        events = get_events(conn, limit=50)
        month_cost = get_monthly_cost(conn)
        daily = get_daily_costs(conn)
        wash = get_wash_sale_exposure(conn, mode="live")
        pdt = get_pdt_count(conn, mode="live")
        conn.close()
        return render_template("resilience.html",
                               startups=startups, shutdowns=shutdowns,
                               secrets=secrets,
                               events=events, month_cost=month_cost,
                               daily_costs=daily, wash_lots=wash,
                               pdt_count=pdt)

    @app.route("/strategy/<strategy_id>/approve", methods=["POST"])
    def approve_strategy(strategy_id):
        """Approve a proposed strategy → PAPER state + create baseline."""
        conn = get_conn()
        reason = request.form.get("reason", "Operator approved")
        try:
            transition_strategy(conn, strategy_id, "PAPER",
                                reason=reason, operator="operator")
            # Auto-create baseline variant
            from heron.strategy.baseline import ensure_baseline
            ensure_baseline(conn, strategy_id)
            flash(f"Strategy {strategy_id} approved → PAPER", "success")
        except ValueError as e:
            flash(str(e), "error")
        conn.close()
        return redirect(url_for("proposals_view"))

    @app.route("/strategy/<strategy_id>/reject", methods=["POST"])
    def reject_strategy(strategy_id):
        """Reject a proposed strategy → RETIRED."""
        conn = get_conn()
        reason = request.form.get("reason", "Operator rejected")
        try:
            transition_strategy(conn, strategy_id, "RETIRED",
                                reason=reason, operator="operator")
            flash(f"Strategy {strategy_id} rejected", "info")
        except ValueError as e:
            flash(str(e), "error")
        conn.close()
        return redirect(url_for("proposals_view"))

    @app.route("/strategy/<strategy_id>/promote", methods=["POST"])
    def promote_strategy(strategy_id):
        """Promote a PAPER strategy → LIVE.

        Two gates (Project-HERON.md §11 + parity invariant):
          1. Monthly review must be filed.
          2. Latest backtest report must show a passing parity verdict.
             Operator can bypass via `force=1` (logged as `promotion_force`).
        """
        from heron.backtest.parity import get_latest_backtest_parity
        from heron.journal.strategies import get_strategy

        conn = get_conn()
        reason = request.form.get("reason", "Operator promoted to live")
        force = request.form.get("force") in ("1", "true", "on")
        strat = get_strategy(conn, strategy_id)
        going_live = bool(strat) and (strat["state"] == "PAPER")
        if not is_review_current(conn):
            log_event(conn, "promotion_blocked",
                      f"Promotion of {strategy_id} blocked: monthly review not filed",
                      severity="warn", source="dashboard.promote")
            flash("Promotion blocked — file this month's review first.", "error")
            conn.close()
            return redirect(url_for("strategy_detail", strategy_id=strategy_id))
        if going_live and not force:
            parity = get_latest_backtest_parity(conn, strategy_id)
            if not parity or not parity.get("available"):
                log_event(conn, "promotion_blocked",
                          f"Promotion of {strategy_id} blocked: no parity verdict on latest backtest",
                          severity="warn", source="dashboard.promote")
                flash("Promotion blocked — run a baseline + reparity the latest backtest first.", "error")
                conn.close()
                return redirect(url_for("strategy_detail", strategy_id=strategy_id))
            if not parity.get("passes"):
                log_event(conn, "promotion_blocked",
                          f"Promotion of {strategy_id} blocked: parity FAIL "
                          f"(CI [{parity.get('ci_lower')}, {parity.get('ci_upper')}], "
                          f"report #{parity.get('report_id')})",
                          severity="warn", source="dashboard.promote")
                flash("Promotion blocked — latest backtest does not beat baseline.", "error")
                conn.close()
                return redirect(url_for("strategy_detail", strategy_id=strategy_id))
        if going_live and force:
            log_event(conn, "promotion_force",
                      f"Operator force-promoted {strategy_id} (parity gate bypassed)",
                      severity="warn", source="dashboard.promote")
        try:
            transition_strategy(conn, strategy_id, "LIVE",
                                reason=reason, operator="operator")
            flash(f"Strategy {strategy_id} promoted → LIVE", "success")
        except ValueError as e:
            flash(str(e), "error")
        conn.close()
        return redirect(url_for("strategy_detail", strategy_id=strategy_id))

    @app.route("/strategy/<strategy_id>/retire", methods=["POST"])
    def retire_strategy(strategy_id):
        """Retire any active strategy."""
        conn = get_conn()
        reason = request.form.get("reason", "Operator retired")
        try:
            transition_strategy(conn, strategy_id, "RETIRED",
                                reason=reason, operator="operator")
            flash(f"Strategy {strategy_id} retired", "info")
        except ValueError as e:
            flash(str(e), "error")
        conn.close()
        return redirect(url_for("strategy_detail", strategy_id=strategy_id))

    @app.route("/candidate/<int:candidate_id>/accept", methods=["POST"])
    def accept_candidate(candidate_id):
        """Accept a pending candidate."""
        conn = get_conn()
        try:
            dispose_candidate(conn, candidate_id, "accepted")
            flash(f"Candidate #{candidate_id} accepted", "success")
        except ValueError as e:
            flash(str(e), "error")
        conn.close()
        return redirect(url_for("candidates_view"))

    @app.route("/candidate/<int:candidate_id>/reject", methods=["POST"])
    def reject_candidate(candidate_id):
        """Reject a pending candidate."""
        conn = get_conn()
        reason = request.form.get("reason", "Operator rejected")
        try:
            dispose_candidate(conn, candidate_id, "rejected", rejection_reason=reason)
            flash(f"Candidate #{candidate_id} rejected", "info")
        except ValueError as e:
            flash(str(e), "error")
        conn.close()
        return redirect(url_for("candidates_view"))

    # ── Desks / Campaign compatibility ─────────────────────────────────────

    @app.route("/campaigns")
    def campaigns_view():
        conn = get_conn()
        rows = jcampaigns.list_campaigns(conn)
        items = []
        for r in rows:
            d = _present_campaign(r)
            d["days"] = jcampaigns.days_active(conn, r["id"])
            d["strategy_count"] = conn.execute(
                "SELECT COUNT(*) FROM strategies WHERE campaign_id=?", (r["id"],)
            ).fetchone()[0]
            items.append(d)
        conn.close()
        return render_template("campaigns.html", campaigns=items)

    def _campaign_new_response():
        if request.method == "POST":
            conn = get_conn()
            cid = (request.form.get("id") or "").strip()
            name = (request.form.get("name") or "").strip()
            if not cid or not name:
                flash("ID and name required.", "error")
                conn.close()
                return redirect(url_for("desk_new"))
            if cid == DEFAULT_DESK_ROUTE_ID:
                flash("Desk ID 'default' is reserved for the PEAD Desk alias.", "error")
                conn.close()
                return redirect(url_for("desk_new"))
            try:
                jcampaigns.create_campaign(
                    conn, cid, name,
                    description=request.form.get("description", ""),
                    mode=request.form.get("mode", "paper"),
                    capital_allocation_usd=float(request.form.get("capital", 500)),
                    paper_window_days=int(request.form.get("paper_window_days", 90)),
                )
                flash(f"Desk {_desk_label(cid)} created (DRAFT).", "success")
                conn.close()
                return redirect(url_for("desk_detail", campaign_id=_desk_route_id(cid)))
            except ValueError as e:
                flash(_desk_error(e), "error")
                conn.close()
            except Exception:
                flash("Could not create Desk. Check the ID and try again.", "error")
                conn.close()
        return render_template("campaign_new.html")

    @app.route("/desk/new", methods=["GET", "POST"])
    def desk_new():
        return _campaign_new_response()

    @app.route("/campaign/new", methods=["GET", "POST"])
    def campaign_new():
        return _campaign_new_response()

    def _campaign_detail_response(campaign_id):
        campaign_id = _resolve_campaign_id(campaign_id)
        conn = get_conn()
        c = jcampaigns.get_campaign(conn, campaign_id)
        if not c:
            conn.close()
            abort(404)
        c = _present_campaign(c)
        strats = jcampaigns.get_campaign_strategies(conn, campaign_id)
        history = jcampaigns.get_state_history(conn, campaign_id)
        days = jcampaigns.days_active(conn, campaign_id)
        signals = list_signals(conn, campaign_id=campaign_id, limit=10)
        # Trades for any strategy in this campaign
        trades = []
        if strats:
            ids = ",".join("?" for _ in strats)
            trades = conn.execute(
                f"SELECT * FROM trades WHERE strategy_id IN ({ids}) ORDER BY created_at DESC LIMIT 50",
                [s["id"] for s in strats],
            ).fetchall()
        conn.close()
        return render_template("campaign_detail.html",
                               campaign=c, strategies=strats, history=history,
                               trades=trades, days=days, signals=signals)

    @app.route("/desk/<campaign_id>")
    def desk_detail(campaign_id):
        return _campaign_detail_response(campaign_id)

    @app.route("/desk/<campaign_id>/signals")
    def desk_signals(campaign_id):
        campaign_id = _resolve_campaign_id(campaign_id)
        conn = get_conn()
        c = jcampaigns.get_campaign(conn, campaign_id)
        if not c:
            conn.close()
            abort(404)
        c = _present_campaign(c)
        signals = list_signals(conn, campaign_id=campaign_id)
        conn.close()
        return render_template("desk_signals.html", campaign=c, signals=signals)

    @app.route("/desks/<campaign_id>")
    def desks_detail_alias(campaign_id):
        return redirect(
            url_for("desk_detail", campaign_id=_desk_route_id(_resolve_campaign_id(campaign_id))),
            code=302,
        )

    @app.route("/campaign/<campaign_id>")
    def campaign_detail(campaign_id):
        return _campaign_detail_response(campaign_id)

    def _campaign_transition_response(campaign_id, action):
        campaign_id = _resolve_campaign_id(campaign_id)
        target = {"start": "ACTIVE", "pause": "PAUSED", "resume": "ACTIVE",
                  "graduate": "GRADUATED", "retire": "RETIRED"}.get(action)
        if not target:
            abort(404)
        conn = get_conn()
        try:
            jcampaigns.transition_campaign(
                conn, campaign_id, target,
                reason=request.form.get("reason", f"Operator {action}"),
                operator="operator",
            )
            flash(f"Desk {_desk_label(campaign_id)} -> {target}", "success")
        except ValueError as e:
            flash(_desk_error(e), "error")
        conn.close()
        return redirect(url_for("desk_detail", campaign_id=_desk_route_id(campaign_id)))

    @app.route("/desk/<campaign_id>/<action>", methods=["POST"])
    def desk_transition(campaign_id, action):
        return _campaign_transition_response(campaign_id, action)

    @app.route("/campaign/<campaign_id>/<action>", methods=["POST"])
    def campaign_transition(campaign_id, action):
        return _campaign_transition_response(campaign_id, action)

    # ── New strategy from template ─────────────────────────────────────────

    @app.route("/strategy/new", methods=["GET", "POST"])
    def strategy_new():
        templates = stemplates.list_templates()
        selected_name = request.values.get("template") or (templates[0].name if templates else None)
        template = stemplates.get_template(selected_name) if selected_name else None

        if request.method == "POST" and request.form.get("submit") == "create":
            conn = get_conn()
            sid = (request.form.get("id") or "").strip()
            name = (request.form.get("name") or "").strip()
            campaign_id = (request.form.get("campaign_id") or "").strip() or None
            campaign_id = _resolve_campaign_id(campaign_id) if campaign_id else None
            if not sid or not name or not template:
                flash("ID, name, and template required.", "error")
                conn.close()
                return redirect(url_for("strategy_new", template=selected_name))
            try:
                overrides = {f.key: request.form.get(f.key) for f in template.param_schema
                             if request.form.get(f.key) not in (None, "")}
                cfg = template.build_config(overrides)
                create_strategy(
                    conn, sid, name,
                    description=request.form.get("description", ""),
                    rationale=request.form.get("rationale", "Operator-authored from template"),
                    config=cfg,
                    template=template.name,
                    campaign_id=campaign_id,
                )
                flash(f"Strategy {sid} created (PROPOSED). Approve in Inbox to start paper trading.", "success")
                conn.close()
                return redirect(url_for("strategy_detail", strategy_id=sid))
            except (ValueError, Exception) as e:
                flash(str(e), "error")
                conn.close()

        conn = get_conn()
        active_campaigns = jcampaigns.list_campaigns(conn, state="ACTIVE")
        draft_campaigns = jcampaigns.list_campaigns(conn, state="DRAFT")
        conn.close()
        return render_template(
            "strategy_new.html",
            templates=templates, template=template,
            campaigns=[_present_campaign(c) for c in list(active_campaigns) + list(draft_campaigns)],
            form=request.form,
        )

    @app.route("/strategy/new/preview", methods=["POST"])
    def strategy_new_preview():
        """HTMX endpoint: show resolved config from current form values."""
        name = request.form.get("template")
        try:
            if not name:
                raise ValueError("Template required")
            t = stemplates.get_template(name)
            overrides = {f.key: request.form.get(f.key) for f in t.param_schema
                         if request.form.get(f.key) not in (None, "")}
            cfg = t.build_config(overrides)
            return render_template("_preview.html", config=cfg, error=None)
        except (KeyError, ValueError) as e:
            return render_template("_preview.html", config=None, error=str(e))

    # ── Actions (formerly Scheduler) ───────────────────────────────────────

    @app.route("/scheduler")
    def scheduler_view():
        """Back-compat redirect; canonical is /actions."""
        return redirect(url_for("actions_view"), code=301)

    @app.route("/actions")
    def actions_view():
        """Operator action surface: Immediate / Scheduled / History."""
        from heron.runtime.supervisor import DEFAULT_JOBS

        conn = get_conn()
        recent = conn.execute(
            "SELECT * FROM scheduler_runs ORDER BY started_at DESC LIMIT 50"
        ).fetchall()
        commands = conn.execute(
            "SELECT * FROM scheduler_commands ORDER BY id DESC LIMIT 20"
        ).fetchall()

        # History filters from query string.
        args = request.args
        event_type_filter = args.get("event_type") or None
        severity_filter = args.get("severity") or None
        kind = args.get("kind") or "all"  # all | jobs | events
        try:
            limit = max(10, min(int(args.get("limit") or 100), 500))
        except ValueError:
            limit = 100

        events_rows = []
        if kind in ("all", "events"):
            events_rows = get_events(conn,
                                     event_type=event_type_filter,
                                     severity=severity_filter,
                                     limit=limit)
        runs_rows = []
        if kind in ("all", "jobs"):
            sql = "SELECT * FROM scheduler_runs"
            params = []
            if severity_filter == "error":
                sql += " WHERE status='error'"
            elif severity_filter and severity_filter != "all":
                sql += " WHERE status=?"
                params.append(severity_filter)
            sql += " ORDER BY started_at DESC LIMIT ?"
            params.append(limit)
            runs_rows = conn.execute(sql, params).fetchall()

        # Merge into one sorted list of dicts.
        history = []
        for e in events_rows:
            d = dict(e)
            history.append({
                "ts": d["created_at"],
                "kind": "event",
                "type": d["event_type"],
                "severity": d.get("severity") or "info",
                "source": d.get("source") or "",
                "message": d.get("message") or "",
            })
        for r in runs_rows:
            d = dict(r)
            history.append({
                "ts": d["started_at"],
                "kind": "job",
                "type": d["job_id"],
                "severity": "error" if d.get("status") == "error" else "info",
                "source": "supervisor",
                "message": (d.get("result_summary") or d.get("error") or
                            f"status={d.get('status')}"),
            })
        history.sort(key=lambda x: x["ts"] or "", reverse=True)
        history = history[:limit]

        # Distinct event types for filter dropdown.
        type_rows = conn.execute(
            "SELECT DISTINCT event_type FROM events ORDER BY event_type"
        ).fetchall()
        known_event_types = [r["event_type"] for r in type_rows]

        jobs = [{"id": jid, "name": desc} for jid, _fn, _trig, desc in DEFAULT_JOBS]
        # Immediate-action shortcuts: subset of jobs the operator runs ad-hoc.
        immediate_actions = [
            {"job_id": "executor_cycle",
             "label": "Run executor cycle",
             "hint": "Process pending candidates + reconcile open positions."},
            {"job_id": "research_premarket",
             "label": "Run premarket research",
             "hint": "News scan + LLM classification + candidate generation."},
            {"job_id": "eod_debrief",
             "label": "Run EOD debrief",
             "hint": "Daily summary, journal flush, Discord alert."},
            {"job_id": "daily_health",
             "label": "Run health check",
             "hint": "Resilience checks, secret rotation, SLO probes."},
            {"job_id": "heartbeat",
             "label": "Send heartbeat",
             "hint": "Manual heartbeat ping (normally hourly)."},
        ]
        # Filter to jobs that exist.
        valid_ids = {j["id"] for j in jobs}
        immediate_actions = [a for a in immediate_actions if a["job_id"] in valid_ids]

        conn.close()
        return render_template(
            "actions.html",
            jobs=jobs, recent=recent, commands=commands,
            history=history,
            immediate_actions=immediate_actions,
            known_event_types=known_event_types,
            filter_event_type=event_type_filter,
            filter_severity=severity_filter,
            filter_kind=kind,
            filter_limit=limit,
        )

    @app.route("/scheduler/<job_id>/<action>", methods=["POST"])
    def scheduler_command(job_id, action):
        from heron.runtime.supervisor import request_command
        conn = get_conn()
        try:
            request_command(conn, job_id, action)
            log_event(
                conn, "operator_action",
                f"Queued {action} for {job_id}",
                severity="info", source="dashboard.actions",
            )
            flash(f"Queued {action} for {job_id}.", "success")
        except ValueError as e:
            flash(str(e), "error")
        conn.close()
        return redirect(url_for("actions_view"))

    @app.route("/actions/<job_id>/<action>", methods=["POST"])
    def actions_command(job_id, action):
        """Canonical action endpoint — same impl as the legacy /scheduler one."""
        return scheduler_command(job_id, action)

    return app

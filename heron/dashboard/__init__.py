"""HERON Dashboard — Flask + HTMX web application.

Local access only (Tailscale VPN boundary). No public exposure.
See Project-HERON.md Section 4.5 for dashboard views.
"""

from flask import Flask, render_template, request, redirect, url_for, abort, flash, jsonify, make_response
from heron.journal import get_journal_conn, init_journal
from heron.journal.strategies import list_strategies, get_strategy, get_state_history, transition_strategy
from heron.journal.candidates import list_candidates, get_candidate, dispose_candidate
from heron.journal.trades import list_trades, get_wash_sale_exposure, get_pdt_count
from heron.journal.ops import get_monthly_cost, get_daily_costs, get_events, get_review, get_audits
from heron.dashboard import mode as vmode

import os
import secrets
import threading


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

    # Overall system state: HALT if cost tripped, WARN if warnings present, else OK.
    if cost_state == "halt":
        system = "halt"
    elif cost_state == "warn" or wash_count > 0:
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
                      "pending_candidates": 0, "pending_strategies": 0, "wash_count": 0}
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
        """Today at a glance — filtered by global paper/live/all mode."""
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

    @app.route("/strategies")
    def strategies_view():
        """Strategy portfolio — filtered by mode."""
        conn = get_conn()
        m = vmode.get_mode()
        states = vmode.strategy_states(m)
        clause, params = vmode.in_clause(states)
        strategies = conn.execute(
            f"SELECT * FROM strategies WHERE state {clause} ORDER BY created_at", params,
        ).fetchall()
        conn.close()
        return render_template("strategies.html", strategies=strategies)

    @app.route("/strategy/<strategy_id>")
    def strategy_detail(strategy_id):
        """Single strategy detail with trades and state history."""
        conn = get_conn()
        strat = get_strategy(conn, strategy_id)
        if not strat:
            conn.close()
            abort(404)
        history = get_state_history(conn, strategy_id)
        trades = list_trades(conn, strategy_id=strategy_id)
        candidates = list_candidates(conn, strategy_id=strategy_id)
        conn.close()
        return render_template("strategy_detail.html",
                               strategy=strat, history=history,
                               trades=trades, candidates=candidates)

    @app.route("/trades")
    def trades_view():
        """Trade log — filtered by global mode."""
        conn = get_conn()
        trades = list_trades(conn, mode=vmode.trade_mode(vmode.get_mode()))
        conn.close()
        return render_template("trades.html", trades=trades)

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
        qparams = list(params)
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
        """System health."""
        conn = get_conn()
        events = get_events(conn, limit=50)
        month_cost = get_monthly_cost(conn)
        daily = get_daily_costs(conn)
        # Health page always shows live exposure — PDT/wash are broker-level facts.
        wash = get_wash_sale_exposure(conn, mode="live")
        pdt = get_pdt_count(conn, mode="live")
        conn.close()
        return render_template("health.html",
                               events=events, month_cost=month_cost,
                               daily_costs=daily, wash_lots=wash,
                               pdt_count=pdt)

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

    @app.route("/glossary")
    def glossary_view():
        """Vocabulary reference for HERON's domain terms."""
        return render_template("glossary.html")

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
        conn.close()
        return render_template("candidate_detail.html",
                               c=cand, audits=related_audits)

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
        """Single backtest report detail."""
        import json as _json
        from heron.backtest import get_report
        conn = get_conn()
        report = get_report(conn, report_id)
        conn.close()
        if not report:
            flash(f"Backtest report {report_id} not found", "error")
            return redirect(url_for("backtests_view"))
        metrics = _json.loads(report["metrics_json"])
        trades = _json.loads(report["trades_json"])
        params = _json.loads(report["params_json"])
        return render_template("backtest_detail.html",
                               report=report, metrics=metrics,
                               trades=trades, params=params)

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
        """Resilience (M15) — startup audits, shutdowns, secrets hygiene."""
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
        conn.close()
        return render_template("resilience.html",
                               startups=startups, shutdowns=shutdowns,
                               secrets=secrets)

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
        """Promote a PAPER strategy → LIVE."""
        conn = get_conn()
        reason = request.form.get("reason", "Operator promoted to live")
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

    return app

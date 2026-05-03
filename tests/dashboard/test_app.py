"""Tests for heron.dashboard Flask app."""

import pytest
from heron.dashboard import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HERON_JOURNAL_DB", str(tmp_path / "test.db"))
    import heron.data.cache as cache_mod
    monkeypatch.setattr(cache_mod, "CACHE_DB", str(tmp_path / "cache.db"))
    # Patch get_journal_conn to use temp DB
    import heron.journal as jmod
    original = jmod.get_journal_conn

    def _tmp_conn():
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr("heron.dashboard.get_journal_conn", _tmp_conn)

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"HERON" in r.data
    # Mission Control is now `/`.
    assert b"Mission Control" in r.data


def test_overview_drilldown(client):
    """Legacy index survives at /overview as a Mission Control drill-down."""
    r = client.get("/overview")
    assert r.status_code == 200


def test_mission_control_inbox_sections(client):
    r = client.get("/")
    assert r.status_code == 200
    # Empty-state DB: Inbox sections still render section titles.
    assert b"Inbox" in r.data
    assert b"Proposals" in r.data
    assert b"Pending candidates" in r.data
    # Action shortcuts present.
    assert b"Executor cycle" in r.data
    # State pane present.
    assert b"System mode" in r.data


def test_shell_has_six_section_nav_and_safety_strip(client):
    r = client.get("/")
    assert r.status_code == 200
    for label in (b"Mission", b"Desks", b"Approvals", b"Activity", b"Portfolio", b"System"):
        assert label in r.data
    for marker in (
        b'data-testid="global-safety-strip"',
        b'data-testid="safety-mode"',
        b'data-testid="safety-pdt"',
        b'data-testid="safety-wash-sale"',
        b'data-testid="safety-cost"',
    ):
        assert marker in r.data


@pytest.mark.parametrize("path, text", [
    ("/desks", b"Desks"),
    ("/approvals", b"Approvals"),
    ("/activity", b"Activity"),
    ("/system", b"Operations"),
])
def test_six_section_routes(client, path, text):
    r = client.get(path)
    assert r.status_code == 200
    assert text in r.data
    assert b"Global Safety Strip" in r.data


def test_system_sub_organization_links_existing_surfaces(client):
    r = client.get("/system")
    assert r.status_code == 200
    for text in (
        b"Operations", b"Configuration", b"Introspection",
        b"Costs", b"Policies", b"Resilience", b"Health", b"Actions",
        b"Agents", b"Audits", b"Setup", b"Data: earnings", b"Data: universe",
    ):
        assert text in r.data


def test_mission_control_shows_proposed_strategy(client):
    """A PROPOSED strategy must show up as a decision card in Mission Control."""
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.strategies import create_strategy
    import os
    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    init_journal(conn)
    create_strategy(conn, "mc_test", "MC Test", template_id="pead",
                    hypothesis="testing mission control surfacing")
    conn.commit()
    conn.close()
    r = client.get("/")
    assert r.status_code == 200
    assert b"mc_test" in r.data
    assert b"Approve" in r.data


def test_strategies(client):
    r = client.get("/strategies")
    assert r.status_code == 200


def test_trades(client):
    r = client.get("/trades")
    assert r.status_code == 200


def test_trades_mode_filter(client):
    r = client.get("/trades?mode=paper")
    assert r.status_code == 200


def test_candidates(client):
    r = client.get("/candidates")
    assert r.status_code == 200


def test_candidates_disposition_filter(client):
    r = client.get("/candidates?disposition=pending")
    assert r.status_code == 200


def test_health(client):
    # /health now redirects to /resilience#health.
    r = client.get("/health")
    assert r.status_code == 301
    assert "/resilience" in r.headers["Location"]


def test_resilience_includes_health(client):
    r = client.get("/resilience")
    assert r.status_code == 200
    # Health section grafted in.
    assert b"Health" in r.data
    assert b"Wash-Sale Lots" in r.data


def test_strategy_detail_404(client):
    r = client.get("/strategy/nonexistent")
    assert r.status_code == 404


def test_campaigns_view(client):
    r = client.get("/campaigns")
    assert r.status_code == 200
    assert b"Desks" in r.data


def test_desks_page_uses_desk_copy(client):
    r = client.get("/desks")
    assert r.status_code == 200
    assert b"Desks" in r.data
    assert b"New desk" in r.data
    assert b"No campaigns yet" not in r.data


def test_legacy_routes_remain_reachable(client):
    import os
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.campaigns import create_campaign

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    init_journal(conn)
    create_campaign(conn, "compat", "Compatibility", mode="paper")
    conn.close()

    expectations = {
        "/": 200,
        "/overview": 200,
        "/desks": 200,
        "/desk/new": 200,
        "/desk/compat": 200,
        "/campaigns": 200,
        "/campaign/new": 200,
        "/campaign/compat": 200,
        "/proposals": 200,
        "/candidates": 200,
        "/trades": 200,
        "/agents": 200,
        "/audits": 200,
        "/backtests": 200,
        "/portfolio": 200,
        "/policies": 200,
        "/costs": 200,
        "/resilience": 200,
        "/health": 301,
        "/setup": 200,
        "/glossary": 200,
        "/actions": 200,
        "/scheduler": 301,
        "/data/earnings": 200,
        "/data/universe": 200,
    }
    for path, status in expectations.items():
        r = client.get(path)
        assert r.status_code == status, path

    assert client.post("/desk/compat/start", follow_redirects=False).status_code == 302
    assert client.post("/campaign/compat/pause", follow_redirects=False).status_code == 302


def test_campaign_new_form(client):
    r = client.get("/campaign/new")
    assert r.status_code == 200
    assert b"New Desk" in r.data


def test_desk_new_form(client):
    r = client.get("/desk/new")
    assert r.status_code == 200
    assert b"New Desk" in r.data


def test_campaign_create_and_detail(client):
    r = client.post("/campaign/new", data={
        "id": "camp_test", "name": "Test", "mode": "paper",
        "capital": "500", "paper_window_days": "90",
        "description": "smoke",
    }, follow_redirects=False)
    assert r.status_code == 302
    r2 = client.get("/campaign/camp_test")
    assert r2.status_code == 200
    assert b"camp_test" in r2.data


def test_desk_create_writes_campaign_row(client):
    import os
    from heron.journal import get_journal_conn

    r = client.post("/desk/new", data={
        "id": "desk_test", "name": "Desk Test", "mode": "paper",
        "capital": "650", "paper_window_days": "90",
        "description": "desk smoke",
    }, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/desk/desk_test")

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    row = conn.execute("SELECT * FROM campaigns WHERE id='desk_test'").fetchone()
    conn.close()
    assert row is not None
    assert row["name"] == "Desk Test"
    assert row["description"] == "desk smoke"


def test_desk_detail_and_action_routes(client):
    client.post("/desk/new", data={
        "id": "desk_t2", "name": "Desk T2", "mode": "paper",
        "capital": "500", "paper_window_days": "90",
    })
    r = client.get("/desk/desk_t2")
    assert r.status_code == 200
    assert b"Desk T2" in r.data
    assert b"Campaign" not in r.data

    alias = client.get("/desks/desk_t2", follow_redirects=False)
    assert alias.status_code == 302
    assert alias.headers["Location"].endswith("/desk/desk_t2")

    action = client.post("/desk/desk_t2/start", follow_redirects=False)
    assert action.status_code == 302
    r2 = client.get("/desk/desk_t2")
    assert b"ACTIVE" in r2.data


def test_candidate_detail_shows_signal_trace(client):
    import os
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.campaigns import create_campaign
    from heron.journal.candidates import create_candidate
    from heron.journal.signals import create_signal, link_signal_candidate
    from heron.journal.strategies import create_strategy

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    init_journal(conn)
    create_campaign(conn, "trace_desk", "Trace Desk", state="ACTIVE")
    create_strategy(conn, "trace_strategy", "Trace Strategy", campaign_id="trace_desk")
    cid = create_candidate(conn, "trace_strategy", "AAPL", thesis="Candidate thesis")
    sid = create_signal(conn, "trace_desk", "research_local", "earnings", "long_bias",
                        "Signal thesis", ticker="AAPL")
    link_signal_candidate(conn, sid, cid, "trace_strategy")
    conn.close()

    r = client.get(f"/candidate/{cid}")
    assert r.status_code == 200
    assert b"Upstream Signal" in r.data
    assert b"Signal thesis" in r.data


def test_candidate_accept_preserves_existing_flow_with_signal(client):
    import os
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.campaigns import create_campaign
    from heron.journal.candidates import create_candidate
    from heron.journal.signals import create_signal, link_signal_candidate
    from heron.journal.strategies import create_strategy

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    init_journal(conn)
    create_campaign(conn, "approve_desk", "Approve Desk", state="ACTIVE")
    create_strategy(conn, "approve_strategy", "Approve Strategy", campaign_id="approve_desk")
    cid = create_candidate(conn, "approve_strategy", "AAPL", thesis="Candidate thesis")
    sid = create_signal(conn, "approve_desk", "research_local", "earnings", "long_bias",
                        "Signal thesis", ticker="AAPL")
    link_signal_candidate(conn, sid, cid, "approve_strategy")
    conn.close()

    r = client.post(f"/candidate/{cid}/accept", follow_redirects=False)
    assert r.status_code == 302

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    row = conn.execute("SELECT disposition FROM candidates WHERE id=?", (cid,)).fetchone()
    conn.close()
    assert row["disposition"] == "accepted"


def test_candidate_detail_legacy_without_signal_renders(client):
    import os
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.candidates import create_candidate
    from heron.journal.strategies import create_strategy

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    init_journal(conn)
    create_strategy(conn, "legacy_strategy", "Legacy Strategy")
    cid = create_candidate(conn, "legacy_strategy", "MSFT", thesis="Legacy thesis")
    conn.close()

    r = client.get(f"/candidate/{cid}")
    assert r.status_code == 200
    assert b"Legacy thesis" in r.data
    assert b"Upstream Signal" not in r.data


def test_desk_signals_route_lists_signals(client):
    import os
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.campaigns import create_campaign
    from heron.journal.signals import create_signal

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    init_journal(conn)
    create_campaign(conn, "signals_desk", "Signals Desk", state="ACTIVE")
    create_signal(conn, "signals_desk", "research_local", "earnings", "long_bias",
                  "AAPL beat", ticker="AAPL")
    conn.close()

    r = client.get("/desk/signals_desk/signals")
    assert r.status_code == 200
    assert b"Signals" in r.data
    assert b"AAPL" in r.data
    assert b"AAPL beat" in r.data


def test_default_paper_presents_as_pead_desk(client):
    import os
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.campaigns import create_campaign

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    init_journal(conn)
    create_campaign(
        conn, "default_paper", "Default Paper Campaign",
        description="Auto-created by migration; holds pre-campaigns strategies.",
        mode="paper", state="ACTIVE",
    )
    conn.close()

    r = client.get("/desks")
    assert r.status_code == 200
    assert b"PEAD Desk" in r.data
    assert b"Default Post-Earnings Drift desk" in r.data
    assert b"default_paper" not in r.data

    detail = client.get("/desk/default")
    assert detail.status_code == 200
    assert b"PEAD Desk" in detail.data
    assert b"default_paper" not in detail.data


def test_strategy_list_uses_default_desk_name(client):
    import os
    from heron.journal import get_journal_conn, init_journal
    from heron.journal.campaigns import create_campaign
    from heron.journal.strategies import create_strategy

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    init_journal(conn)
    create_campaign(conn, "default_paper", "Default Paper Campaign", mode="paper", state="ACTIVE")
    create_strategy(conn, "default_strategy", "Default Strategy", campaign_id="default_paper")
    conn.close()

    r = client.get("/strategies")
    assert r.status_code == 200
    assert b"PEAD Desk" in r.data
    assert b"default_paper" not in r.data


def test_campaign_404(client):
    r = client.get("/campaign/missing")
    assert r.status_code == 404


def test_campaign_state_transition(client):
    client.post("/campaign/new", data={
        "id": "camp_t2", "name": "T2", "mode": "paper",
        "capital": "500", "paper_window_days": "90",
    })
    r = client.post("/campaign/camp_t2/start", follow_redirects=False)
    assert r.status_code == 302
    r = client.get("/campaign/camp_t2")
    assert b"ACTIVE" in r.data


def test_strategy_new_form(client):
    r = client.get("/strategy/new")
    assert r.status_code == 200
    assert b"New Strategy" in r.data
    assert b"PEAD" in r.data or b"pead" in r.data


def test_strategy_new_preview(client):
    r = client.post("/strategy/new/preview", data={
        "template": "pead",
        "surprise_threshold_pct": "7.5",
    })
    assert r.status_code == 200
    assert b"7.5" in r.data or b"surprise_threshold" in r.data


def test_strategy_new_create(client):
    r = client.post("/strategy/new", data={
        "submit": "create",
        "template": "pead",
        "id": "pead_smoke",
        "name": "Smoke PEAD",
        "surprise_threshold_pct": "6.0",
    }, follow_redirects=False)
    assert r.status_code == 302
    r2 = client.get("/strategy/pead_smoke")
    assert r2.status_code == 200


def test_scheduler_view(client):
    # /scheduler now redirects to /actions (canonical).
    r = client.get("/scheduler")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/actions")


def test_actions_view(client):
    r = client.get("/actions")
    assert r.status_code == 200
    assert b"Actions" in r.data
    assert b"research_premarket" in r.data


def test_scheduler_command_queues(client):
    # Legacy /scheduler/<job>/<action> still works and redirects to /actions.
    r = client.post("/scheduler/research_premarket/run_now", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/actions")
    r2 = client.get("/actions")
    assert b"research_premarket" in r2.data


def test_actions_command_queues(client):
    r = client.post("/actions/executor_cycle/run_now", follow_redirects=False)
    assert r.status_code == 302
    r2 = client.get("/actions")
    assert b"executor_cycle" in r2.data


def test_scheduler_and_actions_commands_share_queue(client):
    import os
    from heron.journal import get_journal_conn

    r1 = client.post("/scheduler/research_premarket/run_now", follow_redirects=False)
    r2 = client.post("/actions/research_premarket/pause", follow_redirects=False)
    assert r1.status_code == 302
    assert r2.status_code == 302

    conn = get_journal_conn(os.environ["HERON_JOURNAL_DB"])
    rows = conn.execute(
        "SELECT job_id, action FROM scheduler_commands ORDER BY id"
    ).fetchall()
    conn.close()
    assert [(r["job_id"], r["action"]) for r in rows] == [
        ("research_premarket", "run_now"),
        ("research_premarket", "pause"),
    ]



def test_setup_get(client):
    r = client.get("/setup")
    assert r.status_code == 200
    assert b"First-run setup" in r.data


def test_setup_plan_post(client):
    r = client.post("/setup", data={
        "action": "plan",
        "capital": "750",
        "campaign_name": "My Campaign",
        "cadence": "premarket_eod",
        "max_capital_pct": "0.10",
        "max_positions": "2",
        "drawdown_budget_pct": "0.04",
    })
    assert r.status_code == 200
    assert b"Plan preview" in r.data
    assert b"first_paper" in r.data
    assert b"pead_v1" in r.data


def test_setup_apply_creates(client):
    r = client.post("/setup", data={
        "action": "apply",
        "capital": "500",
        "campaign_name": "First",
        "cadence": "premarket_eod",
        "max_capital_pct": "0.15",
        "max_positions": "3",
        "drawdown_budget_pct": "0.05",
    }, follow_redirects=False)
    assert r.status_code == 200
    # Subsequent apply should be blocked
    r2 = client.post("/setup", data={
        "action": "apply",
        "capital": "500",
        "campaign_name": "Second",
        "cadence": "premarket_eod",
        "max_capital_pct": "0.15",
        "max_positions": "3",
        "drawdown_budget_pct": "0.05",
    })
    assert r2.status_code == 200
    # Already-set-up banner should appear
    assert b"already" in r2.data.lower() or b"refusing" in r2.data.lower() or b"locked" in r2.data.lower()

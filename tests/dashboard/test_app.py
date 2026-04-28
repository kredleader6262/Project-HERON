"""Tests for heron.dashboard Flask app."""

import pytest
from heron.dashboard import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HERON_JOURNAL_DB", str(tmp_path / "test.db"))
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
    assert b"Campaigns" in r.data


def test_campaign_new_form(client):
    r = client.get("/campaign/new")
    assert r.status_code == 200
    assert b"New Campaign" in r.data


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

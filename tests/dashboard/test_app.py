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
    r = client.get("/health")
    assert r.status_code == 200


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
    r = client.get("/scheduler")
    assert r.status_code == 200
    assert b"Scheduler" in r.data
    assert b"research_premarket" in r.data


def test_scheduler_command_queues(client):
    r = client.post("/scheduler/research_premarket/run_now", follow_redirects=False)
    assert r.status_code == 302
    r2 = client.get("/scheduler")
    assert b"research_premarket" in r2.data


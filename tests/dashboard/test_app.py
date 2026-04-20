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

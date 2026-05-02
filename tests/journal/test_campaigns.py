"""Tests for the campaigns journal API + back-compat migration."""

import sqlite3
import pytest

from heron.journal import init_journal, _migrate
from heron.journal.campaigns import (
    create_campaign, get_campaign, list_campaigns,
    transition_campaign, attach_strategy, get_campaign_strategies,
    days_active,
)
from heron.journal.strategies import create_strategy


def test_create_and_get(conn):
    c = create_campaign(conn, "exp_1", "Experiment 1", description="first run")
    assert c["state"] == "DRAFT"
    assert c["mode"] == "paper"
    assert c["paper_window_days"] == 90
    assert c["started_at"] is None
    assert get_campaign(conn, "exp_1")["name"] == "Experiment 1"


def test_active_on_create_sets_started_at(conn):
    c = create_campaign(conn, "exp_1", "E1", state="ACTIVE")
    assert c["started_at"] is not None


def test_transition_validates(conn):
    create_campaign(conn, "exp_1", "E1")
    transition_campaign(conn, "exp_1", "ACTIVE", reason="go")
    assert get_campaign(conn, "exp_1")["started_at"] is not None
    with pytest.raises(ValueError):
        transition_campaign(conn, "exp_1", "DRAFT")  # not allowed
    transition_campaign(conn, "exp_1", "PAUSED")
    transition_campaign(conn, "exp_1", "ACTIVE")
    transition_campaign(conn, "exp_1", "GRADUATED")
    assert get_campaign(conn, "exp_1")["graduated_at"] is not None


def test_attach_strategy(conn):
    create_campaign(conn, "exp_1", "E1", state="ACTIVE")
    create_strategy(conn, "s1", "Strat 1")
    attach_strategy(conn, "exp_1", "s1")
    rows = get_campaign_strategies(conn, "exp_1")
    assert len(rows) == 1
    assert rows[0]["id"] == "s1"


def test_attach_unknown_raises(conn):
    create_campaign(conn, "exp_1", "E1")
    with pytest.raises(ValueError):
        attach_strategy(conn, "exp_1", "missing")
    with pytest.raises(ValueError):
        attach_strategy(conn, "missing", "missing")


def test_list_filters(conn):
    create_campaign(conn, "p1", "P1", mode="paper", state="ACTIVE")
    create_campaign(conn, "l1", "L1", mode="live", state="ACTIVE")
    create_campaign(conn, "p2", "P2", mode="paper")  # DRAFT
    assert {c["id"] for c in list_campaigns(conn, mode="paper")} == {"p1", "p2"}
    assert {c["id"] for c in list_campaigns(conn, state="ACTIVE")} == {"p1", "l1"}


def test_days_active(conn):
    create_campaign(conn, "exp_1", "E1")
    assert days_active(conn, "exp_1") is None
    transition_campaign(conn, "exp_1", "ACTIVE")
    assert days_active(conn, "exp_1") == 0


def test_migration_backfills_orphans(tmp_path):
    """Pre-campaigns DB: strategies exist with no campaign_id; migration should
    create `default_paper` and back-fill them."""
    db = tmp_path / "old.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    # Build the old strategies table shape (no campaign_id, no template)
    c.executescript("""
        CREATE TABLE strategies (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, rationale TEXT,
            state TEXT NOT NULL DEFAULT 'PROPOSED',
            is_baseline INTEGER NOT NULL DEFAULT 0, parent_id TEXT, config TEXT,
            max_capital_pct REAL DEFAULT 0.15, max_positions INTEGER DEFAULT 3,
            drawdown_budget_pct REAL DEFAULT 0.05, min_conviction REAL DEFAULT 0.0,
            min_hold_days INTEGER DEFAULT 2,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            retired_at TEXT, retired_reason TEXT
        );
        INSERT INTO strategies (id, name, state, created_at, updated_at)
        VALUES ('legacy_pead', 'Legacy', 'PAPER', '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00');
    """)
    c.commit()

    init_journal(c)  # creates new tables + runs _migrate

    # campaign_id column added
    cols = {r["name"] for r in c.execute("PRAGMA table_info(strategies)")}
    assert "campaign_id" in cols
    assert "template" in cols

    # default_paper created
    default = c.execute("SELECT * FROM campaigns WHERE id='default_paper'").fetchone()
    assert default is not None
    assert default["state"] == "ACTIVE"
    assert default["mode"] == "paper"

    # legacy strategy back-filled
    s = c.execute("SELECT campaign_id FROM strategies WHERE id='legacy_pead'").fetchone()
    assert s["campaign_id"] == "default_paper"

    # idempotent
    init_journal(c)
    n = c.execute("SELECT COUNT(*) AS n FROM campaigns WHERE id='default_paper'").fetchone()["n"]
    assert n == 1
    c.close()


def test_create_strategy_with_campaign(conn):
    create_campaign(conn, "exp_1", "E1", state="ACTIVE")
    s = create_strategy(conn, "s1", "Strat 1", campaign_id="exp_1", template="pead")
    assert s["campaign_id"] == "exp_1"
    assert s["template"] == "pead"

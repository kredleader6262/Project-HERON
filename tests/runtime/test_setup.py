"""Tests for first-run setup wizard (heron/runtime/setup.py)."""

import sqlite3
import pytest

from heron.journal import init_journal
from heron.runtime.setup import (
    plan_initial_setup, apply_initial_setup, is_already_setup,
    SetupAlreadyDoneError,
)


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_journal(c)
    yield c
    c.close()


def test_plan_validates_inputs():
    with pytest.raises(ValueError):
        plan_initial_setup(capital_usd=0)
    with pytest.raises(ValueError):
        plan_initial_setup(capital_usd=500, cadence="bogus")
    with pytest.raises(ValueError):
        plan_initial_setup(capital_usd=500, max_capital_pct=2.0)
    with pytest.raises(ValueError):
        plan_initial_setup(capital_usd=500, max_positions=0)
    with pytest.raises(ValueError):
        plan_initial_setup(capital_usd=500, drawdown_budget_pct=1.5)


def test_plan_shape():
    plan = plan_initial_setup(capital_usd=500, cadence="premarket_eod")
    assert plan["campaign"]["id"] == "first_paper"
    assert plan["campaign"]["mode"] == "paper"
    assert plan["campaign"]["state"] == "ACTIVE"
    ids = [s["id"] for s in plan["strategies"]]
    assert "pead_v1" in ids and "pead_v1_baseline" in ids
    # Baseline must reference parent
    baseline = next(s for s in plan["strategies"] if s["is_baseline"])
    assert baseline["parent_id"] == "pead_v1"
    assert plan["cadence"]["preset"] == "premarket_eod"
    assert "premarket_research" in plan["cadence"]["jobs"]


def test_apply_creates_campaign_and_strategies(conn):
    plan = plan_initial_setup(capital_usd=500)
    result = apply_initial_setup(conn, plan)
    assert result["campaign_id"] == "first_paper"
    assert set(result["strategy_ids"]) == {"pead_v1", "pead_v1_baseline"}

    # Campaign exists, ACTIVE
    cmp = conn.execute("SELECT * FROM campaigns WHERE id='first_paper'").fetchone()
    assert cmp is not None
    assert cmp["state"] == "ACTIVE"

    # Strategies exist, in PAPER, attached to campaign
    rows = conn.execute(
        "SELECT id, state, campaign_id, is_baseline FROM strategies "
        "WHERE id IN ('pead_v1', 'pead_v1_baseline') ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    for r in rows:
        assert r["state"] == "PAPER"
        assert r["campaign_id"] == "first_paper"
    by_id = {r["id"]: r for r in rows}
    assert by_id["pead_v1_baseline"]["is_baseline"] == 1
    assert by_id["pead_v1"]["is_baseline"] == 0

    # Event logged
    ev = conn.execute(
        "SELECT * FROM events WHERE event_type='initial_setup'"
    ).fetchone()
    assert ev is not None


def test_apply_refuses_on_populated_db(conn):
    plan = plan_initial_setup(capital_usd=500)
    apply_initial_setup(conn, plan)
    with pytest.raises(SetupAlreadyDoneError):
        apply_initial_setup(conn, plan)


def test_is_already_setup(conn):
    assert is_already_setup(conn) is False
    plan = plan_initial_setup(capital_usd=500)
    apply_initial_setup(conn, plan)
    assert is_already_setup(conn) is True


def test_default_paper_alone_does_not_block_setup(conn):
    """Migration creates `default_paper` for legacy DBs; that alone shouldn't block setup."""
    # The conn fixture runs init_journal which may create default_paper if no strategies exist
    # but here there are no strategies so we should still be allowed to set up.
    # Verify setup isn't blocked even if default_paper happened to be created.
    conn.execute(
        """INSERT OR IGNORE INTO campaigns (id, name, mode, state, capital_allocation_usd,
              paper_window_days, created_at, updated_at)
           VALUES ('default_paper', 'Default Paper', 'paper', 'ACTIVE', 500, 90,
                   '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00')"""
    )
    conn.commit()
    assert is_already_setup(conn) is False
    plan = plan_initial_setup(capital_usd=500)
    apply_initial_setup(conn, plan)
    assert is_already_setup(conn) is True

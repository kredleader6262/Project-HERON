"""Tests for strategy CRUD and state machine."""

import pytest
from heron.journal.strategies import (
    create_strategy, get_strategy, list_strategies,
    transition_strategy, get_state_history,
    VALID_STATES, VALID_TRANSITIONS,
)

def test_create_strategy(conn):
    s = create_strategy(conn, "pead_v1", "PEAD Strategy",
                        description="Post-earnings drift", rationale="Academic alpha")
    assert s["id"] == "pead_v1"
    assert s["state"] == "PROPOSED"
    assert s["is_baseline"] == 0


def test_create_baseline(conn):
    create_strategy(conn, "pead_v1", "PEAD LLM")
    s = create_strategy(conn, "pead_v1_base", "PEAD Baseline",
                        is_baseline=True, parent_id="pead_v1")
    assert s["is_baseline"] == 1
    assert s["parent_id"] == "pead_v1"


def test_create_with_limits(conn):
    s = create_strategy(conn, "test", "Test", max_capital_pct=0.10, min_hold_days=5)
    assert s["max_capital_pct"] == 0.10
    assert s["min_hold_days"] == 5


def test_list_strategies(conn):
    create_strategy(conn, "a", "A")
    create_strategy(conn, "b", "B")
    assert len(list_strategies(conn)) == 2
    assert len(list_strategies(conn, state="PROPOSED")) == 2
    assert len(list_strategies(conn, state="LIVE")) == 0


def test_transition_proposed_to_paper(conn):
    create_strategy(conn, "s1", "S1")
    s = transition_strategy(conn, "s1", "PAPER", reason="approved", operator="operator")
    assert s["state"] == "PAPER"


def test_transition_paper_to_live(conn):
    create_strategy(conn, "s1", "S1")
    transition_strategy(conn, "s1", "PAPER", operator="operator")
    s = transition_strategy(conn, "s1", "LIVE", reason="baseline beat", operator="operator")
    assert s["state"] == "LIVE"


def test_transition_to_retired(conn):
    create_strategy(conn, "s1", "S1")
    transition_strategy(conn, "s1", "PAPER", operator="operator")
    s = transition_strategy(conn, "s1", "RETIRED", reason="drawdown breach")
    assert s["state"] == "RETIRED"
    assert s["retired_at"] is not None
    assert s["retired_reason"] == "drawdown breach"


def test_retired_is_reversible(conn):
    create_strategy(conn, "s1", "S1")
    transition_strategy(conn, "s1", "PAPER", operator="operator")
    transition_strategy(conn, "s1", "RETIRED", reason="test")
    s = transition_strategy(conn, "s1", "PROPOSED", reason="operator reactivated", operator="operator")
    assert s["state"] == "PROPOSED"


def test_invalid_transition_raises(conn):
    create_strategy(conn, "s1", "S1")
    with pytest.raises(ValueError, match="Cannot transition"):
        transition_strategy(conn, "s1", "LIVE")  # can't skip PAPER


def test_invalid_state_raises(conn):
    create_strategy(conn, "s1", "S1")
    with pytest.raises(ValueError, match="Invalid state"):
        transition_strategy(conn, "s1", "DELETED")


def test_missing_strategy_raises(conn):
    with pytest.raises(ValueError, match="not found"):
        transition_strategy(conn, "nope", "PAPER")


def test_state_history(conn):
    create_strategy(conn, "s1", "S1")
    transition_strategy(conn, "s1", "PAPER", operator="operator")
    transition_strategy(conn, "s1", "RETIRED", reason="drawdown")
    history = get_state_history(conn, "s1")
    assert len(history) == 3  # created + PAPER + RETIRED
    assert history[0]["from_state"] is None
    assert history[0]["to_state"] == "PROPOSED"
    assert history[1]["to_state"] == "PAPER"
    assert history[2]["to_state"] == "RETIRED"


def test_duplicate_strategy_id_raises(conn):
    create_strategy(conn, "s1", "S1")
    with pytest.raises(Exception):
        create_strategy(conn, "s1", "S1 again")

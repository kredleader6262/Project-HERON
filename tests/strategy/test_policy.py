"""Tests for policy engine + system modes (B2)."""

import pytest

from heron.journal import init_journal, get_journal_conn
from heron.strategy.policy import (
    evaluate_policies, resolve_mode, current_system_mode, set_system_mode,
    derisk_qty, VALID_MODES,
)


@pytest.fixture
def conn(tmp_path):
    c = get_journal_conn(str(tmp_path / "test.db"))
    init_journal(c)
    yield c
    c.close()


# ── Pure rule evaluator ──

def test_no_rules_no_actions():
    assert evaluate_policies({"x": 1}, policies=[]) == []


def test_simple_when_fires():
    rules = [{"id": "dd", "when": "drawdown < -0.05", "then": "derisk",
              "reason": "test"}]
    actions = evaluate_policies({"drawdown": -0.10}, policies=rules)
    assert len(actions) == 1
    assert actions[0]["action"] == "derisk"


def test_when_false_does_not_fire():
    rules = [{"id": "dd", "when": "drawdown < -0.05", "then": "derisk"}]
    assert evaluate_policies({"drawdown": -0.01}, policies=rules) == []


def test_eval_error_reported_as_action():
    rules = [{"id": "broken", "when": "missing_var > 0", "then": "derisk"}]
    actions = evaluate_policies({}, policies=rules)
    assert len(actions) == 1
    assert actions[0]["action"] == "error"


def test_no_builtins_in_eval():
    """No __import__ or open() etc. — restricted namespace."""
    rules = [{"id": "evil", "when": "__import__('os')", "then": "safe_mode"}]
    actions = evaluate_policies({}, policies=rules)
    assert actions[0]["action"] == "error"


# ── Mode resolution ──

def test_resolve_mode_most_restrictive_wins():
    actions = [
        {"id": "a", "action": "derisk"},
        {"id": "b", "action": "safe_mode"},
        {"id": "c", "action": "derisk"},
    ]
    assert resolve_mode(actions) == "SAFE"


def test_resolve_mode_normal_when_no_actions():
    assert resolve_mode([]) == "NORMAL"


def test_resolve_mode_respects_prior_stricter():
    """Operator-set SAFE shouldn't be lifted by rule eval to DERISK."""
    actions = [{"id": "a", "action": "derisk"}]
    assert resolve_mode(actions, prior_mode="SAFE") == "SAFE"


# ── Persistence ──

def test_default_mode_is_normal(conn):
    assert current_system_mode(conn) == "NORMAL"


def test_set_and_read_mode(conn):
    prior = set_system_mode(conn, "DERISK", reason="test", operator="test")
    assert prior == "NORMAL"
    assert current_system_mode(conn) == "DERISK"


def test_set_mode_noop_when_unchanged(conn):
    set_system_mode(conn, "DERISK", reason="t", operator="test")
    set_system_mode(conn, "DERISK", reason="t2", operator="test")
    rows = conn.execute(
        "SELECT * FROM events WHERE event_type='system_mode'"
    ).fetchall()
    assert len(rows) == 1


def test_invalid_mode_raises(conn):
    with pytest.raises(ValueError):
        set_system_mode(conn, "PARANOID", reason="t")


# ── DERISK sizing ──

def test_derisk_qty_scales_in_derisk_mode():
    assert derisk_qty(100, mode_state="DERISK") == 50.0


def test_derisk_qty_unchanged_in_normal():
    assert derisk_qty(100, mode_state="NORMAL") == 100


def test_derisk_qty_unchanged_in_safe():
    """SAFE blocks at the gate; sizing is irrelevant."""
    assert derisk_qty(100, mode_state="SAFE") == 100


# ── pre_trade_checks integration (system_mode gate) ──

def test_safe_mode_blocks_pre_trade_checks(conn):
    """check_system_mode returns ok=False in SAFE."""
    from heron.strategy.risk import check_system_mode
    set_system_mode(conn, "SAFE", reason="t", operator="test")
    r = check_system_mode(conn)
    assert not r.ok
    assert "SAFE" in r.reason


def test_derisk_mode_passes_check_system_mode(conn):
    from heron.strategy.risk import check_system_mode
    set_system_mode(conn, "DERISK", reason="t", operator="test")
    r = check_system_mode(conn)
    assert r.ok


# ── Valid modes invariant ──

def test_valid_modes_constant():
    assert VALID_MODES == ("NORMAL", "DERISK", "SAFE")

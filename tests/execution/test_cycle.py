"""Tests for executor-cycle orchestration."""

from heron.execution.cycle import run_executor_cycle
from heron.journal.strategies import create_strategy, transition_strategy


class Broker:
    def get_account(self):
        return {"equity": 500.0}


def test_unknown_template_skip_is_journaled(conn):
    create_strategy(conn, "broken", "Broken", template="missing")
    transition_strategy(conn, "broken", "PAPER", reason="test")

    summary = run_executor_cycle(conn, mode="paper", broker=Broker())

    assert summary["skipped"] == ["broken: unknown template 'missing'"]
    row = conn.execute(
        "SELECT * FROM events WHERE event_type='strategy_skipped'"
    ).fetchone()
    assert row is not None
    assert row["severity"] == "warn"
    assert row["source"] == "executor_cycle"
    assert row["message"] == "broken: unknown template 'missing'"


def test_strategy_skip_event_is_deduped(conn):
    create_strategy(conn, "custom", "Custom")
    transition_strategy(conn, "custom", "PAPER", reason="test")

    run_executor_cycle(conn, mode="paper", broker=Broker())
    run_executor_cycle(conn, mode="paper", broker=Broker())

    rows = conn.execute(
        "SELECT * FROM events WHERE event_type='strategy_skipped'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["message"] == "custom: missing template"

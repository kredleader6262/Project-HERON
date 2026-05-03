"""Tests for executor-cycle orchestration."""

import pytest

from heron.execution.cycle import run_executor_cycle
from heron.journal.campaigns import create_campaign
from heron.journal.candidates import create_candidate, dispose_candidate
from heron.journal.signals import create_signal, link_signal_candidate
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


@pytest.mark.parametrize("with_signal", [False, True])
def test_accepted_candidate_cycle_ignores_signal_layer(conn, with_signal):
    create_campaign(conn, "cycle_desk", "Cycle Desk", state="ACTIVE")
    create_strategy(conn, "pead_cycle", "PEAD Cycle", campaign_id="cycle_desk", template="pead")
    transition_strategy(conn, "pead_cycle", "PAPER", reason="test")
    cid = create_candidate(conn, "pead_cycle", "AAPL", thesis="accepted", context_json="{}")
    dispose_candidate(conn, cid, "accepted")
    if with_signal:
        sid = create_signal(conn, "cycle_desk", "research_local", "earnings", "long_bias",
                            "AAPL beat", ticker="AAPL")
        link_signal_candidate(conn, sid, cid, "pead_cycle")

    summary = run_executor_cycle(conn, mode="paper", broker=Broker())

    assert summary["errors"] == []
    assert summary["strategies"] == 1

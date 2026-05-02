"""Shared pytest fixtures."""

import pytest

from heron.journal import get_journal_conn, init_journal
from heron.journal.strategies import create_strategy, transition_strategy


@pytest.fixture
def journal_conn_factory(tmp_path):
    conns = []

    def make(name="journal.db", *, strategy_id=None, strategy_name="PEAD",
             strategy_state=None, **strategy_kwargs):
        conn = get_journal_conn(str(tmp_path / name))
        init_journal(conn)
        if strategy_id:
            create_strategy(conn, strategy_id, strategy_name, **strategy_kwargs)
            if strategy_state and strategy_state != "PROPOSED":
                transition_strategy(conn, strategy_id, strategy_state, reason="test fixture")
        conns.append(conn)
        return conn

    yield make

    for conn in conns:
        conn.close()


@pytest.fixture
def conn(journal_conn_factory):
    return journal_conn_factory()


@pytest.fixture
def pead_conn(journal_conn_factory):
    return journal_conn_factory(strategy_id="pead", strategy_name="PEAD")


@pytest.fixture
def s1_conn(journal_conn_factory):
    return journal_conn_factory(strategy_id="s1", strategy_name="test", kind="research_local")


@pytest.fixture
def research_pead_v1_conn(journal_conn_factory):
    return journal_conn_factory(
        strategy_id="pead_v1",
        strategy_name="PEAD Test",
        strategy_state="PAPER",
        max_capital_pct=0.25,
        max_positions=3,
        drawdown_budget_pct=0.08,
        min_hold_days=2,
    )


@pytest.fixture
def baseline_pead_v1_conn(journal_conn_factory):
    return journal_conn_factory(
        strategy_id="pead_v1",
        strategy_name="PEAD v1",
        strategy_state="PAPER",
        max_capital_pct=0.15,
        max_positions=3,
        drawdown_budget_pct=0.05,
        min_hold_days=2,
    )

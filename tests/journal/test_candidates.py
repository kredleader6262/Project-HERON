"""Tests for candidates CRUD."""

import pytest
from heron.journal import get_journal_conn, init_journal
from heron.journal.strategies import create_strategy
from heron.journal.candidates import (
    create_candidate, dispose_candidate, get_candidate, list_candidates,
)


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test_cand.db"
    c = get_journal_conn(str(db))
    init_journal(c)
    create_strategy(c, "pead", "PEAD")
    yield c
    c.close()


def test_create_candidate(conn):
    cid = create_candidate(conn, "pead", "AAPL", source="research_local",
                           local_score=0.8, thesis="Earnings beat")
    row = get_candidate(conn, cid)
    assert row["ticker"] == "AAPL"
    assert row["disposition"] == "pending"
    assert row["local_score"] == 0.8


def test_dispose_accepted(conn):
    cid = create_candidate(conn, "pead", "MSFT")
    dispose_candidate(conn, cid, "accepted")
    row = get_candidate(conn, cid)
    assert row["disposition"] == "accepted"
    assert row["disposed_at"] is not None


def test_dispose_rejected(conn):
    cid = create_candidate(conn, "pead", "GOOGL")
    dispose_candidate(conn, cid, "rejected", rejection_reason="wash sale")
    row = get_candidate(conn, cid)
    assert row["disposition"] == "rejected"
    assert row["rejection_reason"] == "wash sale"


def test_dispose_invalid_raises(conn):
    cid = create_candidate(conn, "pead", "NVDA")
    with pytest.raises(ValueError, match="Invalid disposition"):
        dispose_candidate(conn, cid, "cancelled")


def test_list_candidates_filters(conn):
    create_candidate(conn, "pead", "AAPL")
    cid2 = create_candidate(conn, "pead", "MSFT")
    dispose_candidate(conn, cid2, "rejected")

    assert len(list_candidates(conn, strategy_id="pead")) == 2
    assert len(list_candidates(conn, disposition="pending")) == 1
    assert len(list_candidates(conn, ticker="MSFT")) == 1
    assert len(list_candidates(conn, disposition="rejected")) == 1

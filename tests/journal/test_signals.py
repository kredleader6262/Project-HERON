"""Tests for first-class Signal journal helpers."""

import json

import pytest

from heron.journal import get_journal_conn, init_journal
from heron.journal.campaigns import create_campaign
from heron.journal.candidates import create_candidate
from heron.journal.signals import (
    create_signal, get_signal, list_signals, update_signal,
    link_signal_candidate, get_signal_for_candidate, list_signal_candidates,
)
from heron.journal.strategies import create_strategy


@pytest.fixture
def signal_conn(conn):
    create_campaign(conn, "sig_desk", "Signals Desk", mode="paper", state="ACTIVE")
    create_strategy(conn, "sig_s1", "Signal S1", campaign_id="sig_desk")
    create_strategy(conn, "sig_s2", "Signal S2", campaign_id="sig_desk")
    return conn


def _signal(conn, **kwargs):
    data = {
        "campaign_id": "sig_desk",
        "source": "research_local",
        "signal_type": "earnings",
        "bias": "long_bias",
        "thesis": "AAPL beat with strong guide",
        "ticker": "AAPL",
        "confidence": 0.83,
        "classification": "positive",
        "finding_ref_json": {"article_id": "a1"},
        "evidence_json": {"rationale": "sanitized classifier output"},
        "generated_at": "2026-05-03T12:00:00+00:00",
    }
    data.update(kwargs)
    return create_signal(conn, **data)


def test_signal_schema_idempotent(tmp_path):
    conn = get_journal_conn(str(tmp_path / "journal.db"))
    init_journal(conn)
    init_journal(conn)
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"signals", "signal_candidates"} <= tables
    indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {
        "idx_signals_campaign", "idx_signals_ticker", "idx_signals_type",
        "idx_signals_status", "idx_signal_candidates_signal",
        "idx_signal_candidates_strategy",
    } <= indexes
    conn.close()


def test_signal_migration_adds_tables_to_existing_db(tmp_path):
    conn = get_journal_conn(str(tmp_path / "journal.db"))
    init_journal(conn)
    conn.execute("DROP TABLE signal_candidates")
    conn.execute("DROP TABLE signals")
    conn.commit()
    before = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    init_journal(conn)

    after = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert before <= after
    assert {"signals", "signal_candidates"} <= after
    conn.close()


def test_create_get_update_signal(signal_conn):
    sid = _signal(signal_conn, source="vendor:anything", producing_agent="agent-x", producing_model="model-y")
    row = get_signal(signal_conn, sid)
    assert row["source"] == "vendor:anything"
    assert row["producing_agent"] == "agent-x"
    assert json.loads(row["finding_ref_json"])["article_id"] == "a1"

    updated = update_signal(signal_conn, sid, resolution_status="resolved",
                            outcome_json={"hit": True}, baseline_json={"beat": 0.02})
    assert updated["resolution_status"] == "resolved"
    assert json.loads(updated["outcome_json"])["hit"] is True
    assert json.loads(updated["baseline_json"])["beat"] == 0.02


def test_bias_validation_and_negative_bias_persistence(signal_conn):
    short_id = _signal(signal_conn, bias="short_bias", thesis="Negative guide", ticker="MSFT")
    risk_id = _signal(signal_conn, bias="risk-off", thesis="Macro shock", ticker=None, signal_type="macro")
    assert get_signal(signal_conn, short_id)["bias"] == "short_bias"
    assert get_signal(signal_conn, risk_id)["bias"] == "risk-off"
    with pytest.raises(ValueError, match="Invalid bias"):
        _signal(signal_conn, bias="bearish")


def test_list_signal_filters(signal_conn):
    _signal(signal_conn, ticker="AAPL", signal_type="earnings", bias="long_bias",
            resolution_status="open", expires_at="2026-05-04T00:00:00+00:00")
    _signal(signal_conn, ticker="MSFT", signal_type="analyst", bias="short_bias",
            resolution_status="resolved", expires_at="2026-05-10T00:00:00+00:00")

    assert len(list_signals(signal_conn, campaign_id="sig_desk")) == 2
    assert len(list_signals(signal_conn, ticker="AAPL")) == 1
    assert len(list_signals(signal_conn, signal_type="analyst")) == 1
    assert len(list_signals(signal_conn, status="resolved")) == 1
    assert len(list_signals(signal_conn, expires_before="2026-05-05T00:00:00+00:00")) == 1
    assert len(list_signals(signal_conn, expires_after="2026-05-05T00:00:00+00:00")) == 1


def test_bridge_one_signal_many_candidates(signal_conn):
    sid = _signal(signal_conn)
    c1 = create_candidate(signal_conn, "sig_s1", "AAPL")
    c2 = create_candidate(signal_conn, "sig_s2", "AAPL")

    b1 = link_signal_candidate(signal_conn, sid, c1, "sig_s1")
    b2 = link_signal_candidate(signal_conn, sid, c2, "sig_s2")

    assert b1 != b2
    assert len(list_signal_candidates(signal_conn, signal_id=sid)) == 2
    assert get_signal_for_candidate(signal_conn, c1)["signal_id"] == sid


def test_bridge_candidate_unique_and_idempotent(signal_conn):
    sid = _signal(signal_conn)
    other = _signal(signal_conn, ticker="MSFT")
    cid = create_candidate(signal_conn, "sig_s1", "AAPL")

    bridge_id = link_signal_candidate(signal_conn, sid, cid, "sig_s1")
    assert link_signal_candidate(signal_conn, sid, cid, "sig_s1") == bridge_id
    with pytest.raises(ValueError, match="already linked"):
        link_signal_candidate(signal_conn, other, cid, "sig_s1")


def test_foreign_key_validation(signal_conn):
    with pytest.raises(ValueError, match="Campaign"):
        _signal(signal_conn, campaign_id="missing")
    sid = _signal(signal_conn)
    cid = create_candidate(signal_conn, "sig_s1", "AAPL")
    with pytest.raises(ValueError, match="Signal"):
        link_signal_candidate(signal_conn, 9999, cid, "sig_s1")
    with pytest.raises(ValueError, match="Candidate"):
        link_signal_candidate(signal_conn, sid, 9999, "sig_s1")
    with pytest.raises(ValueError, match="Strategy"):
        link_signal_candidate(signal_conn, sid, cid, "missing")
    with pytest.raises(ValueError, match="belongs to strategy"):
        link_signal_candidate(signal_conn, sid, cid, "sig_s2")
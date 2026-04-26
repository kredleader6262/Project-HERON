"""Tests for M10 — Strategy proposal flow + dashboard approval."""

import json
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from heron.journal import init_journal
from heron.journal.strategies import (
    create_strategy, get_strategy, list_strategies,
    transition_strategy, get_state_history,
)


@pytest.fixture
def db_path(tmp_path):
    db = tmp_path / "test_journal.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_journal(conn)
    conn.close()
    return str(db)


@pytest.fixture
def journal_conn(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    yield c
    try:
        c.close()
    except Exception:
        pass


def _new_conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ── Strategy State Machine ───────────────────────

class TestStrategyLifecycle:

    def test_create_proposed(self, journal_conn):
        s = create_strategy(journal_conn, "test_v1", "Test Strategy",
                            description="A test", rationale="Because testing")
        assert s["state"] == "PROPOSED"
        assert s["name"] == "Test Strategy"

    def test_proposed_to_paper(self, journal_conn):
        create_strategy(journal_conn, "test_v1", "Test")
        s = transition_strategy(journal_conn, "test_v1", "PAPER",
                                reason="Approved", operator="operator")
        assert s["state"] == "PAPER"

    def test_paper_to_live(self, journal_conn):
        create_strategy(journal_conn, "test_v1", "Test")
        transition_strategy(journal_conn, "test_v1", "PAPER")
        s = transition_strategy(journal_conn, "test_v1", "LIVE", reason="Beat test passed")
        assert s["state"] == "LIVE"

    def test_live_to_retired(self, journal_conn):
        create_strategy(journal_conn, "test_v1", "Test")
        transition_strategy(journal_conn, "test_v1", "PAPER")
        transition_strategy(journal_conn, "test_v1", "LIVE")
        s = transition_strategy(journal_conn, "test_v1", "RETIRED", reason="Drawdown breach")
        assert s["state"] == "RETIRED"
        assert s["retired_reason"] == "Drawdown breach"

    def test_retired_to_proposed(self, journal_conn):
        """Reversible retirement."""
        create_strategy(journal_conn, "test_v1", "Test")
        transition_strategy(journal_conn, "test_v1", "RETIRED", reason="Rejected")
        s = transition_strategy(journal_conn, "test_v1", "PROPOSED", reason="Reconsidered")
        assert s["state"] == "PROPOSED"

    def test_invalid_transition(self, journal_conn):
        create_strategy(journal_conn, "test_v1", "Test")
        with pytest.raises(ValueError, match="Cannot transition"):
            transition_strategy(journal_conn, "test_v1", "LIVE")  # PROPOSED → LIVE invalid

    def test_state_history(self, journal_conn):
        create_strategy(journal_conn, "test_v1", "Test")
        transition_strategy(journal_conn, "test_v1", "PAPER", operator="alice")
        transition_strategy(journal_conn, "test_v1", "LIVE", operator="alice")

        history = get_state_history(journal_conn, "test_v1")
        assert len(history) == 3  # created, PAPER, LIVE
        assert history[0]["to_state"] == "PROPOSED"
        assert history[1]["to_state"] == "PAPER"
        assert history[2]["to_state"] == "LIVE"

    def test_list_by_state(self, journal_conn):
        create_strategy(journal_conn, "s1", "Strategy 1")
        create_strategy(journal_conn, "s2", "Strategy 2")
        transition_strategy(journal_conn, "s2", "PAPER")

        proposed = list_strategies(journal_conn, state="PROPOSED")
        paper = list_strategies(journal_conn, state="PAPER")
        assert len(proposed) == 1
        assert len(paper) == 1


# ── Strategy Proposer ────────────────────────────

class TestProposer:

    @patch("heron.research.proposer.call")
    def test_propose_ok(self, mock_call, journal_conn):
        from heron.research.proposer import propose_strategy

        mock_call.return_value = {
            "text": "{}", "parsed": {
                "id": "momentum_v1", "name": "Momentum Strategy",
                "description": "Follow the trend", "rationale": "Momentum works",
                "universe": ["AAPL", "MSFT"],
                "entry_rules": "Buy on breakout", "exit_rules": "Sell on breakdown",
                "max_capital_pct": 0.10, "max_positions": 2,
                "drawdown_budget_pct": 0.04, "min_hold_days": 3,
                "confidence": 0.8,
            },
            "tokens_in": 500, "tokens_out": 300, "cost_usd": 0.01,
            "model": "claude-sonnet-4-20250514", "elapsed_s": 2.0,
        }

        result = propose_strategy(journal_conn)
        assert result["status"] == "ok"
        assert result["strategy_id"] == "momentum_v1"

        s = get_strategy(journal_conn, "momentum_v1")
        assert s is not None
        assert s["state"] == "PROPOSED"
        assert s["name"] == "Momentum Strategy"

    @patch("heron.research.proposer.call")
    def test_propose_low_confidence(self, mock_call, journal_conn):
        from heron.research.proposer import propose_strategy

        mock_call.return_value = {
            "text": "{}", "parsed": {
                "id": "weak_v1", "name": "Weak Strategy",
                "confidence": 0.3,
            },
            "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.001,
            "model": "claude-sonnet-4-20250514", "elapsed_s": 1.0,
        }

        result = propose_strategy(journal_conn)
        assert result["status"] == "low_confidence"

    @patch("heron.research.proposer.call")
    def test_propose_cost_gate(self, mock_call, journal_conn):
        from heron.research.proposer import propose_strategy
        from heron.journal.ops import log_cost

        log_cost(journal_conn, "claude_sonnet", 100000, 50000, 50.0, task="thesis")
        result = propose_strategy(journal_conn)
        assert result["status"] == "cost_halted"
        mock_call.assert_not_called()

    @patch("heron.research.proposer.call")
    def test_propose_duplicate(self, mock_call, journal_conn):
        from heron.research.proposer import propose_strategy

        create_strategy(journal_conn, "existing_v1", "Existing")

        mock_call.return_value = {
            "text": "{}", "parsed": {
                "id": "existing_v1", "name": "Duplicate",
                "confidence": 0.9,
            },
            "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.001,
            "model": "claude-sonnet-4-20250514", "elapsed_s": 1.0,
        }

        result = propose_strategy(journal_conn)
        assert result["status"] == "duplicate"


# ── Dashboard Approval ────────────────────────────

class TestDashboardApproval:

    @pytest.fixture
    def client(self, journal_conn, db_path):
        from heron.dashboard import create_app

        with patch("heron.dashboard.get_journal_conn",
                   side_effect=lambda: _new_conn(db_path)):
            with patch("heron.dashboard.init_journal"):
                app = create_app()
                app.config["TESTING"] = True
                with app.test_client() as c:
                    yield c

    def test_proposals_page(self, client, journal_conn):
        create_strategy(journal_conn, "test_v1", "Test Strategy",
                        rationale="Good idea")
        resp = client.get("/proposals")
        assert resp.status_code == 200
        assert b"Test Strategy" in resp.data

    def test_approve_strategy(self, client, journal_conn):
        create_strategy(journal_conn, "test_v1", "Test Strategy")

        resp = client.post("/strategy/test_v1/approve",
                           data={"reason": "Looks good"},
                           follow_redirects=True)
        assert resp.status_code == 200

        s = get_strategy(journal_conn, "test_v1")
        assert s["state"] == "PAPER"

        # Baseline should have been created
        baseline = get_strategy(journal_conn, "test_v1_baseline")
        assert baseline is not None

    def test_reject_strategy(self, client, journal_conn):
        create_strategy(journal_conn, "test_v1", "Test Strategy")

        resp = client.post("/strategy/test_v1/reject",
                           data={"reason": "Not convinced"},
                           follow_redirects=True)
        assert resp.status_code == 200

        s = get_strategy(journal_conn, "test_v1")
        assert s["state"] == "RETIRED"

    def test_promote_strategy(self, client, journal_conn):
        from heron.journal.ops import create_review, file_review
        from datetime import datetime, timezone
        create_strategy(journal_conn, "test_v1", "Test")
        transition_strategy(journal_conn, "test_v1", "PAPER")
        # Promotion requires a filed monthly review (Project-HERON.md §11).
        ym = datetime.now(timezone.utc).strftime("%Y-%m")
        create_review(journal_conn, ym)
        file_review(journal_conn, ym, "ok", "go")

        resp = client.post("/strategy/test_v1/promote",
                           data={"reason": "Beat test passed"},
                           follow_redirects=True)
        assert resp.status_code == 200

        s = get_strategy(journal_conn, "test_v1")
        assert s["state"] == "LIVE"

    def test_promote_strategy_blocked_without_review(self, client, journal_conn):
        """Promotion must be blocked if this month's review isn't filed."""
        create_strategy(journal_conn, "test_v1", "Test")
        transition_strategy(journal_conn, "test_v1", "PAPER")

        resp = client.post("/strategy/test_v1/promote",
                           data={"reason": "try promote"},
                           follow_redirects=True)
        assert resp.status_code == 200
        s = get_strategy(journal_conn, "test_v1")
        assert s["state"] == "PAPER"

    def test_retire_strategy(self, client, journal_conn):
        create_strategy(journal_conn, "test_v1", "Test")
        transition_strategy(journal_conn, "test_v1", "PAPER")

        resp = client.post("/strategy/test_v1/retire",
                           data={"reason": "Drawdown"},
                           follow_redirects=True)
        assert resp.status_code == 200

        s = get_strategy(journal_conn, "test_v1")
        assert s["state"] == "RETIRED"

    def test_accept_candidate(self, client, journal_conn):
        from heron.journal.candidates import create_candidate, get_candidate

        now = "2026-04-19T00:00:00+00:00"
        journal_conn.execute(
            """INSERT INTO strategies (id, name, state, max_capital_pct, max_positions,
               drawdown_budget_pct, min_hold_days, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("pead_v1", "PEAD", "PAPER", 0.15, 3, 0.05, 2, now, now),
        )
        journal_conn.commit()

        cid = create_candidate(journal_conn, "pead_v1", "AAPL")
        resp = client.post(f"/candidate/{cid}/accept", follow_redirects=True)
        assert resp.status_code == 200

        c = get_candidate(journal_conn, cid)
        assert c["disposition"] == "accepted"

    def test_reject_candidate(self, client, journal_conn):
        from heron.journal.candidates import create_candidate, get_candidate

        now = "2026-04-19T00:00:00+00:00"
        journal_conn.execute(
            """INSERT INTO strategies (id, name, state, max_capital_pct, max_positions,
               drawdown_budget_pct, min_hold_days, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("pead_v1", "PEAD", "PAPER", 0.15, 3, 0.05, 2, now, now),
        )
        journal_conn.commit()

        cid = create_candidate(journal_conn, "pead_v1", "MSFT")
        resp = client.post(f"/candidate/{cid}/reject",
                           data={"reason": "Low quality"},
                           follow_redirects=True)
        assert resp.status_code == 200

        c = get_candidate(journal_conn, cid)
        assert c["disposition"] == "rejected"

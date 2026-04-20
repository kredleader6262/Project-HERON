"""Tests for M8 — Claude API client, thesis writer, escalation logic."""

import json
import sqlite3
from unittest.mock import patch, MagicMock
import random

import pytest

from heron.journal import init_journal
from heron.journal.candidates import create_candidate, get_candidate
from heron.journal.ops import get_monthly_cost, log_cost


# ── Fixtures ──────────────────────────────────────

@pytest.fixture
def journal_conn(tmp_path):
    db = tmp_path / "test_journal.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_journal(conn)
    now = "2026-04-19T00:00:00+00:00"
    conn.execute(
        """INSERT INTO strategies (id, name, state, max_capital_pct, max_positions,
           drawdown_budget_pct, min_hold_days, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("pead_v1", "PEAD Test", "PAPER", 0.25, 3, 0.08, 2, now, now),
    )
    conn.commit()
    return conn


@pytest.fixture
def sample_candidate(journal_conn):
    """Create a sample candidate and return (conn, candidate_id)."""
    context = {
        "sentiment": "positive", "sentiment_score": 0.7,
        "category": "earnings", "relevance_score": 0.8,
    }
    cid = create_candidate(
        journal_conn, "pead_v1", "AAPL", side="buy", source="research_local",
        local_score=0.75, thesis="AAPL: strong earnings beat",
        context_json=json.dumps(context),
    )
    return journal_conn, cid


def _mock_claude_response(parsed_json, tokens_in=200, tokens_out=150, cost=0.005):
    """Create a mock httpx response for Claude Messages API."""
    # Simulate what claude.call() would return after processing
    return {
        "text": json.dumps(parsed_json),
        "parsed": parsed_json,
        "model": "claude-sonnet-4-20250514",
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "elapsed_s": 1.5,
    }


# ── Claude Client ────────────────────────────────

class TestClaudeClient:

    @patch("httpx.post")
    def test_call_json_mode(self, mock_post):
        from heron.research.claude import call

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": '{"answer": 42}'}],
            "usage": {"input_tokens": 50, "output_tokens": 20},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with patch("heron.research.claude.ANTHROPIC_API_KEY", "test-key"):
            result = call("test", json_mode=True)

        assert result["parsed"] == {"answer": 42}
        assert result["tokens_in"] == 50
        assert result["tokens_out"] == 20
        assert result["cost_usd"] > 0

    @patch("httpx.post")
    def test_call_text_mode(self, mock_post):
        from heron.research.claude import call

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "Hello world"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with patch("heron.research.claude.ANTHROPIC_API_KEY", "test-key"):
            result = call("test")

        assert result["text"] == "Hello world"
        assert "parsed" not in result

    def test_call_no_api_key(self):
        from heron.research.claude import call
        with patch("heron.research.claude.ANTHROPIC_API_KEY", ""):
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                call("test")

    @patch("httpx.post")
    def test_call_invalid_json(self, mock_post):
        from heron.research.claude import call

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "not json at all"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with patch("heron.research.claude.ANTHROPIC_API_KEY", "test-key"):
            result = call("test", json_mode=True)

        assert result["parsed"] is None

    @patch("httpx.post")
    def test_cost_calculation_sonnet(self, mock_post):
        from heron.research.claude import call

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with patch("heron.research.claude.ANTHROPIC_API_KEY", "test-key"):
            result = call("test", model="claude-sonnet-4-20250514")

        # Sonnet: 1000 * 3/1M + 500 * 15/1M = 0.003 + 0.0075 = 0.0105
        assert abs(result["cost_usd"] - 0.0105) < 0.001


# ── Thesis Writer ─────────────────────────────────

class TestThesisWriter:

    @patch("heron.research.thesis.call")
    def test_write_thesis_ok(self, mock_call, sample_candidate):
        conn, cid = sample_candidate
        from heron.research.thesis import write_thesis

        mock_call.return_value = _mock_claude_response({
            "conviction": 0.82,
            "thesis": "Strong earnings beat with guidance raise",
            "bull_case": "Revenue acceleration",
            "bear_case": "Valuation stretched",
            "catalysts": ["Q2 earnings", "Product launch"],
            "risks": ["Market rotation", "China exposure"],
            "time_horizon": "weeks",
            "reasoning": "Clear catalyst with positive momentum",
        })

        result = write_thesis(conn, cid)

        assert result["status"] == "ok"
        assert result["conviction"] == 0.82
        assert result["cost_usd"] == 0.005

        # Check candidate was updated
        c = get_candidate(conn, cid)
        assert c["api_score"] == 0.82
        assert "Strong earnings beat" in c["thesis"]

    @patch("heron.research.thesis.call")
    def test_write_thesis_cost_gate(self, mock_call, sample_candidate):
        conn, cid = sample_candidate
        from heron.research.thesis import write_thesis

        # Push cost to ceiling
        log_cost(conn, "claude_sonnet", 100000, 50000, 50.0, task="thesis")

        result = write_thesis(conn, cid)
        assert result["status"] == "cost_halted"
        mock_call.assert_not_called()

    @patch("heron.research.thesis.call")
    def test_write_thesis_parse_error(self, mock_call, sample_candidate):
        conn, cid = sample_candidate
        from heron.research.thesis import write_thesis

        mock_call.return_value = {
            "text": "not json", "parsed": None,
            "tokens_in": 50, "tokens_out": 20, "cost_usd": 0.001,
            "model": "claude-sonnet-4-20250514", "elapsed_s": 1.0,
        }

        result = write_thesis(conn, cid)
        assert result["status"] == "parse_error"

    def test_write_thesis_missing_candidate(self, journal_conn):
        from heron.research.thesis import write_thesis
        result = write_thesis(journal_conn, 9999)
        assert result is None

    @patch("heron.research.thesis.call")
    def test_write_theses_batch(self, mock_call, journal_conn):
        from heron.research.thesis import write_theses_batch

        # Create two candidates
        cid1 = create_candidate(journal_conn, "pead_v1", "AAPL", local_score=0.8,
                                context_json='{"sentiment": "positive", "sentiment_score": 0.6, "category": "earnings"}')
        cid2 = create_candidate(journal_conn, "pead_v1", "MSFT", local_score=0.7,
                                context_json='{"sentiment": "positive", "sentiment_score": 0.5, "category": "analyst"}')

        mock_call.return_value = _mock_claude_response({
            "conviction": 0.75, "thesis": "Good setup",
            "bull_case": "Growth", "bear_case": "Valuation",
            "catalysts": ["Earnings"], "risks": ["Macro"],
            "time_horizon": "days", "reasoning": "Solid",
        })

        results = write_theses_batch(journal_conn, [cid1, cid2])
        assert len(results) == 2
        assert all(r["status"] == "ok" for r in results)

    @patch("heron.research.thesis.call")
    def test_conviction_clamped(self, mock_call, sample_candidate):
        conn, cid = sample_candidate
        from heron.research.thesis import write_thesis

        mock_call.return_value = _mock_claude_response({
            "conviction": 1.5, "thesis": "Over-confident",
            "bull_case": "x", "bear_case": "y",
            "catalysts": [], "risks": [],
            "time_horizon": "days", "reasoning": "test",
        })

        result = write_thesis(conn, cid)
        assert result["conviction"] == 1.0  # Clamped


# ── Escalation Logic ──────────────────────────────

class TestEscalation:

    @patch("heron.research.escalation.write_thesis")
    @patch("heron.research.escalation.call")
    def test_escalate_high_score_gets_thesis(self, mock_call, mock_thesis, journal_conn):
        from heron.research.escalation import escalate_candidates

        cid = create_candidate(journal_conn, "pead_v1", "AAPL", local_score=0.75,
                               context_json='{}')
        mock_thesis.return_value = {"status": "ok", "candidate_id": cid, "conviction": 0.8}

        rng = random.Random(42)  # Fixed seed
        result = escalate_candidates(journal_conn, [cid], rng=rng)

        assert result["status"] == "ok"
        assert result["escalated"] == 1
        mock_thesis.assert_called_once()

    @patch("heron.research.escalation.write_thesis")
    @patch("heron.research.escalation.call")
    def test_escalate_low_score_sampled(self, mock_call, mock_thesis, journal_conn):
        from heron.research.escalation import escalate_candidates

        # Create candidate below threshold
        cid = create_candidate(journal_conn, "pead_v1", "MSFT", local_score=0.4,
                               context_json='{"sentiment": "positive", "sentiment_score": 0.3, "category": "other"}')

        # Use a rigged RNG that always returns < 0.15 (always samples)
        rng = MagicMock()
        rng.random.return_value = 0.05  # < 0.15, so it samples

        mock_call.return_value = _mock_claude_response({
            "agree": True, "conviction": 0.4, "reason": "Marginal"
        }, cost=0.001)

        result = escalate_candidates(journal_conn, [cid], rng=rng)
        assert result["sampled"] == 1

    @patch("heron.research.escalation.write_thesis")
    @patch("heron.research.escalation.call")
    def test_escalate_cost_halted(self, mock_call, mock_thesis, journal_conn):
        from heron.research.escalation import escalate_candidates

        log_cost(journal_conn, "claude_sonnet", 100000, 50000, 50.0, task="thesis")
        cid = create_candidate(journal_conn, "pead_v1", "AAPL", local_score=0.8,
                               context_json='{}')

        result = escalate_candidates(journal_conn, [cid])
        assert result["status"] == "cost_halted"
        mock_thesis.assert_not_called()

    @patch("heron.research.escalation.call")
    def test_audit_divergence_detected(self, mock_call, journal_conn):
        from heron.research.escalation import _audit_sample

        cid = create_candidate(journal_conn, "pead_v1", "TSLA", side="buy",
                               local_score=0.7,
                               context_json='{"sentiment": "positive", "sentiment_score": 0.6}')

        mock_call.return_value = _mock_claude_response({
            "agree": False, "conviction": 0.2, "reason": "Overvalued"
        }, cost=0.001)

        result = _audit_sample(journal_conn, cid)
        assert result is not None
        assert result["divergent"] is True
        assert result["agrees"] is False

        # Check audit was logged
        row = journal_conn.execute(
            "SELECT * FROM audits WHERE candidate_id=?", (cid,)
        ).fetchone()
        assert row is not None
        assert row["audit_type"] == "sampling"
        assert row["divergence"] == 1

    @patch("heron.research.escalation.call")
    def test_audit_agreement(self, mock_call, journal_conn):
        from heron.research.escalation import _audit_sample

        cid = create_candidate(journal_conn, "pead_v1", "GOOG", side="buy",
                               local_score=0.65,
                               context_json='{"sentiment": "positive", "sentiment_score": 0.5}')

        mock_call.return_value = _mock_claude_response({
            "agree": True, "conviction": 0.7, "reason": "Reasonable setup"
        }, cost=0.001)

        result = _audit_sample(journal_conn, cid)
        assert result["divergent"] is False
        assert result["agrees"] is True


# ── Orchestrator Escalation ───────────────────────

class TestOrchestratorEscalation:

    @patch("heron.research.orchestrator.ANTHROPIC_API_KEY", "test-key")
    @patch("heron.research.orchestrator.generate_candidates")
    @patch("heron.research.orchestrator.classify_batch")
    @patch("heron.research.orchestrator.filter_relevant")
    def test_escalation_wired_in(self, mock_filter, mock_classify, mock_gen, journal_conn):
        from heron.research.orchestrator import ResearchPass

        mock_classify.return_value = [{"relevant": True, "relevance_score": 0.8}]
        mock_filter.return_value = [{"relevant": True}]
        mock_gen.return_value = [1, 2]

        feed = MagicMock()
        feed.fetch_watchlist_news.return_value = [{"id": "1", "headline": "Test", "summary": "Test"}]

        with patch("heron.research.escalation.escalate_candidates") as mock_esc:
            mock_esc.return_value = {"status": "ok", "escalated": 1, "sampled": 0}

            rp = ResearchPass(conn=journal_conn, feed=feed)
            result = rp.run(escalate=True)

        assert "escalation" in result
        assert result["escalation"]["status"] == "ok"

    @patch("heron.research.orchestrator.ANTHROPIC_API_KEY", "")
    @patch("heron.research.orchestrator.generate_candidates")
    @patch("heron.research.orchestrator.classify_batch")
    @patch("heron.research.orchestrator.filter_relevant")
    def test_no_escalation_without_api_key(self, mock_filter, mock_classify, mock_gen, journal_conn):
        from heron.research.orchestrator import ResearchPass

        mock_classify.return_value = [{"relevant": True}]
        mock_filter.return_value = [{"relevant": True}]
        mock_gen.return_value = [1]

        feed = MagicMock()
        feed.fetch_watchlist_news.return_value = [{"id": "1", "headline": "Test", "summary": "Test"}]

        rp = ResearchPass(conn=journal_conn, feed=feed)
        result = rp.run(escalate=True)

        assert "escalation" not in result

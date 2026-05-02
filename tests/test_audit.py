"""Tests for M11 — Audit system (post-mortems + trust score)."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from heron.journal.strategies import create_strategy
from heron.journal.candidates import create_candidate
from heron.journal.trades import create_trade, fill_trade, close_trade
from heron.journal.ops import log_audit
from heron.research import audit as audit_mod

def _losing_trade(conn, *, strategy_id="s1", ticker="AAPL", cand_created=None,
                  local_score=0.8):
    """Seed a closed losing trade with its candidate. Returns (trade_id, cand_id)."""
    try:
        create_strategy(conn, strategy_id, name="test", kind="research_local")
    except Exception:
        pass
    cand_id = create_candidate(
        conn, strategy_id, ticker, source="research_local",
        local_score=local_score, final_score=local_score,
        context_json=json.dumps({"news": ["upbeat earnings"]}),
    )
    if cand_created:
        conn.execute("UPDATE candidates SET created_at=? WHERE id=?",
                     (cand_created, cand_id))
        conn.commit()
    trade_id = create_trade(conn, strategy_id, ticker, "buy", "paper", qty=10,
                            candidate_id=cand_id)
    fill_trade(conn, trade_id, fill_price=100.0, fill_qty=10)
    close_trade(conn, trade_id, close_price=95.0, close_reason="stop")
    return trade_id, cand_id


# ── find_losing_trades_needing_postmortem ─────────────────

class TestFindLosingTrades:

    def test_returns_losing_trade_without_audit(self, conn):
        trade_id, _ = _losing_trade(conn)
        rows = audit_mod.find_losing_trades_needing_postmortem(conn)
        assert len(rows) == 1
        assert rows[0]["id"] == trade_id

    def test_skips_if_audit_exists(self, conn):
        trade_id, cand_id = _losing_trade(conn)
        log_audit(conn, "cost_triggered", strategy_id="s1",
                  trade_id=trade_id, candidate_id=cand_id)
        rows = audit_mod.find_losing_trades_needing_postmortem(conn)
        assert rows == []

    def test_skips_winning_trade(self, conn):
        try:
            create_strategy(conn, "s1", name="t", kind="research_local")
        except Exception:
            pass
        tid = create_trade(conn, "s1", "AAPL", "buy", "paper", qty=10)
        fill_trade(conn, tid, 100.0, 10)
        close_trade(conn, tid, 110.0, "target")  # winner
        assert audit_mod.find_losing_trades_needing_postmortem(conn) == []


# ── post_mortem_trade ─────────────────────────────────────

class TestPostMortemTrade:

    def test_memorization_guard_pre_cutoff(self, conn):
        # candidate dated pre-cutoff → skipped, still logs an audit
        trade_id, _ = _losing_trade(conn, cand_created="2020-01-01T00:00:00+00:00")
        trade = conn.execute("SELECT * FROM trades WHERE id=?",
                             (trade_id,)).fetchone()
        r = audit_mod.post_mortem_trade(conn, trade)
        assert r["status"] == "skipped_pre_cutoff"
        row = conn.execute(
            "SELECT * FROM audits WHERE trade_id=?", (trade_id,)
        ).fetchone()
        assert row["audit_type"] == "cost_triggered"
        assert "skipped" in row["notes"]

    @patch("heron.research.audit.call")
    def test_divergent_postmortem_logged(self, mock_call, conn):
        mock_call.return_value = {
            "parsed": {"would_trade": False, "conviction": 0.2, "reason": "red flag"},
            "tokens_in": 100, "tokens_out": 40, "cost_usd": 0.001,
        }
        future = (datetime.now(timezone.utc)).isoformat()
        trade_id, _ = _losing_trade(conn, cand_created=future, local_score=0.85)
        trade = conn.execute("SELECT * FROM trades WHERE id=?",
                             (trade_id,)).fetchone()
        r = audit_mod.post_mortem_trade(conn, trade)
        assert r["status"] == "completed"
        assert r["divergence"] is True
        row = conn.execute(
            "SELECT * FROM audits WHERE trade_id=?", (trade_id,)
        ).fetchone()
        assert row["divergence"] == 1

    @patch("heron.research.audit.call")
    def test_agreeing_postmortem_not_divergent(self, mock_call, conn):
        mock_call.return_value = {
            "parsed": {"would_trade": True, "conviction": 0.75, "reason": "still solid"},
            "tokens_in": 100, "tokens_out": 40, "cost_usd": 0.001,
        }
        future = datetime.now(timezone.utc).isoformat()
        trade_id, _ = _losing_trade(conn, cand_created=future, local_score=0.80)
        trade = conn.execute("SELECT * FROM trades WHERE id=?",
                             (trade_id,)).fetchone()
        r = audit_mod.post_mortem_trade(conn, trade)
        assert r["divergence"] is False


# ── run_pending_post_mortems ──────────────────────────────

class TestRunPendingPostMortems:

    def test_no_pending(self, conn):
        r = audit_mod.run_pending_post_mortems(conn)
        assert r["status"] == "no_pending"
        assert r["completed"] == 0

    @patch("heron.research.audit.call")
    def test_respects_limit(self, mock_call, conn):
        mock_call.return_value = {
            "parsed": {"would_trade": True, "conviction": 0.5},
            "tokens_in": 10, "tokens_out": 10, "cost_usd": 0.0,
        }
        future = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            _losing_trade(conn, cand_created=future, ticker=f"T{i}")
        r = audit_mod.run_pending_post_mortems(conn, limit=2)
        assert r["completed"] == 2

    @patch("heron.research.audit.check_budget")
    def test_cost_halt(self, mock_budget, conn):
        mock_budget.return_value = {
            "status": "tripped", "research_allowed": False,
            "mtd": 9999.0, "reason": "over ceiling",
        }
        _losing_trade(conn)
        r = audit_mod.run_pending_post_mortems(conn)
        assert r["status"] == "cost_halted"


# ── compute_trust_score ───────────────────────────────────

class TestComputeTrustScore:

    def test_under_sampled(self, conn):
        r = audit_mod.compute_trust_score(conn)
        assert r["trust_score"] is None
        assert "under-sampled" in r["warning"]

    def test_score_with_enough_samples(self, conn):
        # 12 sampling audits, 3 divergent → trust = 0.75
        for i in range(12):
            log_audit(conn, "sampling", divergence=(i < 3))
        r = audit_mod.compute_trust_score(conn)
        assert r["sample_size"] == 12
        assert r["trust_score"] == 0.75
        assert r["breakdown"]["sampling"]["n"] == 12
        assert r["breakdown"]["sampling"]["divergent"] == 3

    def test_excludes_skipped_pre_cutoff(self, conn):
        # skipped memorization-guard audits should not dilute the score
        for _ in range(12):
            log_audit(conn, "sampling", divergence=False)
        for _ in range(5):
            log_audit(conn, "cost_triggered", divergence=True,
                      notes="skipped: pre-cutoff (memorization guard)")
        r = audit_mod.compute_trust_score(conn)
        assert r["sample_size"] == 12  # skipped ones filtered out
        assert r["trust_score"] == 1.0

    def test_combines_both_audit_types(self, conn):
        for _ in range(8):
            log_audit(conn, "sampling", divergence=False)
        for _ in range(4):
            log_audit(conn, "cost_triggered", divergence=True)
        r = audit_mod.compute_trust_score(conn)
        assert r["sample_size"] == 12
        # 4 divergent / 12 = 0.333 trust
        assert abs(r["trust_score"] - (1 - 4/12)) < 0.01


# ── after_cutoff helper ───────────────────────────────────

class TestCutoffGuard:

    def test_after_cutoff_true(self):
        assert audit_mod._after_cutoff("2099-01-01T00:00:00+00:00") is True

    def test_before_cutoff_false(self):
        assert audit_mod._after_cutoff("2020-01-01T00:00:00+00:00") is False

    def test_none_returns_false(self):
        assert audit_mod._after_cutoff(None) is False

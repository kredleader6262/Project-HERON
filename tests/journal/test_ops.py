"""Tests for cost tracking, audits, reviews, events."""

import pytest
from heron.journal.ops import (
    log_cost, get_monthly_cost, get_daily_costs,
    log_audit, get_audits,
    create_review, file_review, get_review, is_review_current,
    log_event, get_events,
)


@pytest.fixture
def conn(pead_conn):
    return pead_conn


# ── Cost Tracking ──────────────────────────────────

def test_log_and_get_cost(conn):
    log_cost(conn, "claude_sonnet", 1000, 500, 0.05, strategy_id="pead",
             task="classification", date="2025-01-15")
    log_cost(conn, "qwen_local", 2000, 800, 0.00, date="2025-01-15")
    total = get_monthly_cost(conn, "2025-01")
    assert total == pytest.approx(0.05)


def test_daily_costs(conn):
    log_cost(conn, "claude_sonnet", 1000, 500, 0.03, date="2025-01-15")
    log_cost(conn, "claude_sonnet", 500, 200, 0.02, date="2025-01-15")
    log_cost(conn, "qwen_local", 3000, 1000, 0.00, date="2025-01-15")
    rows = get_daily_costs(conn, "2025-01-15")
    assert len(rows) == 2  # two models
    costs = {r["model"]: r["cost"] for r in rows}
    assert costs["claude_sonnet"] == pytest.approx(0.05)
    assert costs["qwen_local"] == pytest.approx(0.00)


# ── Audits ──────────────────────────────────────────

def test_log_and_get_audit(conn):
    log_audit(conn, "baseline_comparison", strategy_id="pead",
              local_output="bullish", api_output="bearish", divergence=True)
    audits = get_audits(conn, audit_type="baseline_comparison")
    assert len(audits) == 1
    assert audits[0]["divergence"] == 1


def test_audit_no_filter(conn):
    log_audit(conn, "sampling", notes="random check")
    log_audit(conn, "cost_triggered", notes="budget hit")
    assert len(get_audits(conn)) == 2


# ── Reviews ──────────────────────────────────────────

def test_create_and_file_review(conn):
    create_review(conn, "2025-01")
    r = get_review(conn, "2025-01")
    assert r["status"] == "pending"

    file_review(conn, "2025-01", "All good, proceed.", "go")
    r = get_review(conn, "2025-01")
    assert r["status"] == "filed"
    assert r["decision"] == "go"
    assert r["filed_at"] is not None


def test_review_invalid_decision(conn):
    create_review(conn, "2025-02")
    with pytest.raises(ValueError, match="go"):
        file_review(conn, "2025-02", "bad", "maybe")


def test_is_review_current(conn):
    # No review for current month → not current
    assert is_review_current(conn) is False


def test_duplicate_review_ignored(conn):
    create_review(conn, "2025-01")
    create_review(conn, "2025-01")  # INSERT OR IGNORE
    # should not raise


# ── Events ──────────────────────────────────────────

def test_log_and_get_event(conn):
    log_event(conn, "reconciliation_drift", "Position mismatch detected",
              severity="error", source="execution")
    events = get_events(conn, event_type="reconciliation_drift")
    assert len(events) == 1
    assert events[0]["severity"] == "error"


def test_events_filter_severity(conn):
    log_event(conn, "alert", "Stale quote", severity="warn")
    log_event(conn, "halt", "Daily loss reached", severity="critical")
    assert len(get_events(conn, severity="warn")) == 1
    assert len(get_events(conn, severity="critical")) == 1
    assert len(get_events(conn)) == 2

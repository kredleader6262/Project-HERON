"""Tests for M15 — resilience hardening."""

import os
import signal
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from heron.journal import init_journal
from heron.journal.strategies import create_strategy
from heron.journal.trades import create_trade, fill_trade
from heron.resilience.startup_audit import run_startup_audit
from heron.resilience.shutdown import (
    snapshot_state, log_shutdown, install_signal_handlers,
)
from heron.resilience.secrets import (
    check_env_file, check_required_vars, scan_log_for_secrets,
    check_secrets_hygiene,
)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "j.db"))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_journal(c)
    create_strategy(c, "s1", name="test", kind="research_local")
    yield c
    c.close()


class _FakeBroker:
    def __init__(self, positions=None):
        self._positions = positions or []
    def get_positions(self):
        return self._positions


# ── Startup audit ────────────────────────────────

class TestStartupAudit:

    def test_clean_with_no_positions(self, conn):
        r = run_startup_audit(conn, broker=_FakeBroker([]))
        assert r["status"] == "clean"
        assert r["issues"] == []
        assert r["checks"]["reconciliation"]["status"] == "clean"
        assert r["checks"]["stops"]["status"] == "clean"

    def test_skips_reconcile_without_broker(self, conn):
        r = run_startup_audit(conn, broker=None)
        assert r["checks"]["reconciliation"]["status"] == "skipped"

    def test_detects_broker_only_position(self, conn):
        broker = _FakeBroker([{"ticker": "AAPL", "qty": 10}])
        r = run_startup_audit(conn, broker=broker)
        assert r["status"] == "drift"
        assert any("broker but not journal" in i for i in r["issues"])

    def test_detects_qty_mismatch(self, conn):
        # Journal has 5, broker has 10
        tid = create_trade(conn, "s1", "AAPL", "buy", "paper", qty=5,
                           stop_price=95.0)
        fill_trade(conn, tid, fill_price=100.0, fill_qty=5)
        broker = _FakeBroker([{"ticker": "AAPL", "qty": 10}])
        r = run_startup_audit(conn, broker=broker)
        assert r["status"] == "drift"

    def test_detects_unprotected_position(self, conn):
        # Open trade with no stop_price
        tid = create_trade(conn, "s1", "AAPL", "buy", "paper", qty=5)
        fill_trade(conn, tid, fill_price=100.0, fill_qty=5)
        broker = _FakeBroker([{"ticker": "AAPL", "qty": 5}])
        r = run_startup_audit(conn, broker=broker)
        assert r["status"] == "drift"
        assert any("unprotected" in i for i in r["issues"])

    def test_logs_event(self, conn):
        run_startup_audit(conn, broker=_FakeBroker([]))
        row = conn.execute(
            "SELECT * FROM events WHERE event_type='startup_audit' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert "clean" in row["message"]

    def test_pending_work_counted(self, conn):
        r = run_startup_audit(conn, broker=_FakeBroker([]))
        assert "pending_work" in r["checks"]
        assert "open_trades" in r["checks"]["pending_work"]


# ── Shutdown ────────────────────────────────

class TestShutdown:

    def test_snapshot_empty(self, conn):
        s = snapshot_state(conn)
        assert s["open_count"] == 0
        assert s["open_trades"] == []

    def test_snapshot_with_open_trade(self, conn):
        tid = create_trade(conn, "s1", "AAPL", "buy", "paper", qty=5,
                           stop_price=95.0)
        fill_trade(conn, tid, fill_price=100.0, fill_qty=5)
        s = snapshot_state(conn)
        assert s["open_count"] == 1
        assert s["open_trades"][0]["ticker"] == "AAPL"

    def test_log_shutdown_creates_event(self, conn):
        log_shutdown(conn, reason="test")
        row = conn.execute(
            "SELECT * FROM events WHERE event_type='shutdown_graceful' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert "test" in row["message"]

    def test_install_signal_handlers_idempotent(self, conn):
        # Should not raise when called twice
        install_signal_handlers(conn, exit_on_signal=False)
        install_signal_handlers(conn, exit_on_signal=False)


# ── Secrets hygiene ────────────────────────────────

class TestSecrets:

    def test_missing_env_file(self, tmp_path):
        r = check_env_file(str(tmp_path / "nope.env"))
        assert r["status"] == "missing"

    def test_existing_env_file(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("X=1")
        r = check_env_file(str(p))
        assert r["status"] == "ok"

    def test_missing_required_vars(self, monkeypatch):
        for v in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        r = check_required_vars()
        assert r["status"] == "missing"
        assert "ALPACA_API_KEY" in r["missing_required"]

    def test_required_vars_all_set(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "x")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "x")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        r = check_required_vars()
        assert r["status"] == "ok"

    def test_scan_log_missing(self, tmp_path):
        r = scan_log_for_secrets(str(tmp_path / "no.log"))
        assert r["status"] == "skipped"

    def test_scan_log_clean(self, tmp_path):
        p = tmp_path / "app.log"
        p.write_text("nothing sensitive here\njust a log line\n")
        r = scan_log_for_secrets(str(p))
        assert r["status"] == "clean"

    def test_scan_log_detects_anthropic_key(self, tmp_path):
        p = tmp_path / "app.log"
        p.write_text("token=sk-ant-abcdefghijklmnopqrstuvwxyz123456\n")
        r = scan_log_for_secrets(str(p))
        assert r["status"] == "leaked"
        assert len(r["findings"]) >= 1

    def test_check_secrets_hygiene_clean(self, tmp_path, monkeypatch):
        p = tmp_path / ".env"
        p.write_text("X=1")
        monkeypatch.setenv("ALPACA_API_KEY", "x")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "x")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        r = check_secrets_hygiene(env_path=str(p))
        assert r["status"] == "clean"

    def test_check_secrets_hygiene_flags_missing(self, tmp_path, monkeypatch):
        for v in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        r = check_secrets_hygiene(env_path=str(tmp_path / "nope"))
        assert r["status"] == "issues"
        assert len(r["issues"]) >= 1

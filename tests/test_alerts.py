"""Tests for M12 — Discord alerts + EOD debrief."""

import json
import time
from unittest.mock import patch, MagicMock

import pytest

from heron.journal.strategies import create_strategy
from heron.journal.trades import create_trade, fill_trade, close_trade
from heron.journal.ops import log_event, log_cost

@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """Isolate alert-state file per test."""
    p = tmp_path / "alert_state.json"
    monkeypatch.setattr("heron.alerts.discord.ALERT_STATE_FILE", str(p))
    return p


# ── Discord client ────────────────────────────────

class TestDiscordSend:

    def test_no_webhook_returns_no_webhook(self, state_file, monkeypatch):
        monkeypatch.setattr("heron.alerts.discord.DISCORD_WEBHOOK_URL", "")
        from heron.alerts.discord import send
        r = send("debrief", "hello")
        assert r["status"] == "no_webhook"

    @patch("heron.alerts.discord.httpx.post")
    def test_sent_updates_state(self, mock_post, state_file):
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        from heron.alerts.discord import send
        r = send("debrief", "hi", webhook_url="https://x.invalid/hook")
        assert r["status"] == "sent"
        mock_post.assert_called_once()
        body = json.loads(state_file.read_text())
        assert "debrief" in body

    @patch("heron.alerts.discord.httpx.post")
    def test_rate_limited_within_window(self, mock_post, state_file):
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        from heron.alerts.discord import send
        send("debrief", "one", webhook_url="https://x/hook")
        r = send("debrief", "two", webhook_url="https://x/hook")
        assert r["status"] == "rate_limited"
        assert mock_post.call_count == 1  # second was blocked

    @patch("heron.alerts.discord.httpx.post")
    def test_force_bypasses_rate_limit(self, mock_post, state_file):
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        from heron.alerts.discord import send
        send("debrief", "one", webhook_url="https://x/hook")
        r = send("debrief", "two", webhook_url="https://x/hook", force=True)
        assert r["status"] == "sent"
        assert mock_post.call_count == 2

    def test_unknown_category_raises(self, state_file):
        from heron.alerts.discord import send
        with pytest.raises(ValueError):
            send("garbage", "x", webhook_url="https://x/hook")

    @patch("heron.alerts.discord.httpx.post")
    def test_http_error_does_not_update_state(self, mock_post, state_file):
        import httpx
        mock_post.side_effect = httpx.HTTPError("boom")
        from heron.alerts.discord import send
        r = send("debrief", "x", webhook_url="https://x/hook")
        assert r["status"] == "error"
        assert not state_file.exists() or "debrief" not in json.loads(state_file.read_text())

    def test_dry_run_skips_post(self, state_file):
        from heron.alerts.discord import send
        r = send("debrief", "x", webhook_url="https://x/hook", dry_run=True)
        assert r["status"] == "dry_run"
        assert "payload" in r

    @patch("heron.alerts.discord.httpx.post")
    def test_reset_clears_state(self, mock_post, state_file):
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        from heron.alerts.discord import send, reset
        send("debrief", "one", webhook_url="https://x/hook")
        reset("debrief")
        r = send("debrief", "two", webhook_url="https://x/hook")
        assert r["status"] == "sent"

    def test_content_truncated_to_1800(self, state_file):
        from heron.alerts.discord import send
        msg = "a" * 3000
        r = send("debrief", msg, webhook_url="https://x/hook", dry_run=True)
        assert len(r["payload"]["content"]) == 1800


# ── Debrief: gather ────────────────────────────────

def _today_trade(conn, *, strategy_id="s1", ticker="AAPL",
                 entry=100.0, exit=None, close_reason="target"):
    try:
        create_strategy(conn, strategy_id, "Test")
    except Exception:
        pass
    tid = create_trade(conn, strategy_id, ticker, "buy", "paper", qty=10)
    fill_trade(conn, tid, entry, 10)
    if exit is not None:
        close_trade(conn, tid, exit, close_reason)
    return tid


class TestDebriefGather:

    def test_empty_day(self, conn):
        from heron.alerts.debrief import gather
        d = gather(conn)
        assert d["closed_count"] == 0
        assert d["pnl"] == 0
        assert d["winners"] == 0
        assert d["losers"] == 0

    def test_wins_and_losses_counted(self, conn):
        _today_trade(conn, ticker="AAPL", entry=100, exit=110)  # +100
        _today_trade(conn, ticker="MSFT", entry=100, exit=95)   # -50
        from heron.alerts.debrief import gather
        d = gather(conn)
        assert d["closed_count"] == 2
        assert d["winners"] == 1
        assert d["losers"] == 1
        assert d["pnl"] == 50  # +100 + (-50)

    def test_open_positions_counted(self, conn):
        _today_trade(conn, ticker="NVDA", entry=500, exit=None)
        from heron.alerts.debrief import gather
        d = gather(conn)
        assert d["closed_count"] == 0
        assert d["open_count"] == 1

    def test_events_today_collected(self, conn):
        log_event(conn, "test", "something happened")
        from heron.alerts.debrief import gather
        d = gather(conn)
        assert "something happened" in d["events"]


# ── Debrief: run ────────────────────────────────

class TestDebriefRun:

    def test_empty_day_skips_claude(self, conn, state_file):
        from heron.alerts.debrief import run
        # No trades, no events → Claude should not be called
        with patch("heron.alerts.debrief.call") as mock_call:
            result = run(conn, deliver=False)
        mock_call.assert_not_called()
        assert result["prose"]["summary"] == ""
        assert "EOD Debrief" in result["message"]

    @patch("heron.alerts.debrief.call")
    def test_calls_claude_when_trades_exist(self, mock_call, conn, state_file):
        mock_call.return_value = {
            "parsed": {"summary": "Good day.", "flag_for_attention": False},
            "tokens_in": 50, "tokens_out": 20, "cost_usd": 0.01,
        }
        _today_trade(conn, ticker="AAPL", entry=100, exit=105)
        from heron.alerts.debrief import run
        result = run(conn, deliver=False)
        mock_call.assert_called_once()
        assert "Good day." in result["message"]

    @patch("heron.alerts.debrief.call")
    @patch("heron.alerts.debrief.discord_send")
    def test_deliver_posts_to_discord(self, mock_send, mock_call, conn, state_file):
        mock_call.return_value = {
            "parsed": {"summary": "ok", "flag_for_attention": False},
            "tokens_in": 10, "tokens_out": 10, "cost_usd": 0.001,
        }
        mock_send.return_value = {"status": "sent", "category": "debrief"}
        _today_trade(conn, ticker="AAPL", entry=100, exit=105)
        from heron.alerts.debrief import run
        result = run(conn, deliver=True)
        mock_send.assert_called_once()
        assert result["delivery"]["status"] == "sent"

    @patch("heron.alerts.debrief.call")
    def test_claude_failure_falls_back_to_message(self, mock_call, conn, state_file):
        mock_call.side_effect = RuntimeError("api down")
        _today_trade(conn, ticker="AAPL", entry=100, exit=105)
        from heron.alerts.debrief import run
        result = run(conn, deliver=False)
        assert "unavailable" in result["prose"]["summary"]
        assert "EOD Debrief" in result["message"]

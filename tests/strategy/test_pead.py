"""Tests for the PEAD strategy."""

import pytest
from unittest.mock import MagicMock
from heron.strategy.pead import PEADStrategy, PEAD_CONFIG, PEAD_UNIVERSE


@pytest.fixture
def strat():
    return PEADStrategy("pead_test", is_llm_variant=False)


@pytest.fixture
def llm_strat():
    return PEADStrategy("pead_llm", is_llm_variant=True)


# ── screen_candidate ──────────────────────────────

def test_screen_good_candidate(strat):
    ok, reason = strat.screen_candidate({
        "ticker": "AAPL", "surprise_pct": 12.0, "announced_hours_ago": 6
    })
    assert ok
    assert "Qualified" in reason


def test_screen_not_in_universe(strat):
    ok, reason = strat.screen_candidate({
        "ticker": "TSLA", "surprise_pct": 12.0, "announced_hours_ago": 6
    })
    assert not ok
    assert "not in PEAD universe" in reason


def test_screen_below_threshold(strat):
    ok, reason = strat.screen_candidate({
        "ticker": "AAPL", "surprise_pct": 3.0, "announced_hours_ago": 6
    })
    assert not ok
    assert "threshold" in reason


def test_screen_stale_announcement(strat):
    ok, reason = strat.screen_candidate({
        "ticker": "AAPL", "surprise_pct": 12.0, "announced_hours_ago": 48
    })
    assert not ok
    assert "window" in reason


def test_screen_negative_surprise(strat):
    ok, reason = strat.screen_candidate({
        "ticker": "AAPL", "surprise_pct": -8.0, "announced_hours_ago": 6
    })
    assert not ok
    assert "Negative" in reason


def test_screen_llm_veto(llm_strat):
    ok, reason = llm_strat.screen_candidate({
        "ticker": "AAPL", "surprise_pct": 12.0, "announced_hours_ago": 6,
        "llm_veto": True, "veto_reason": "guided down despite beat"
    })
    assert not ok
    assert "veto" in reason.lower()


def test_screen_llm_low_conviction(llm_strat):
    llm_strat.config["min_conviction"] = 0.7
    ok, reason = llm_strat.screen_candidate({
        "ticker": "AAPL", "surprise_pct": 12.0, "announced_hours_ago": 6,
        "conviction": 0.3
    })
    assert not ok
    assert "Conviction" in reason


# ── compute_levels ──────────────────────────────

def test_compute_levels_basic(strat):
    result = strat.compute_levels("AAPL", {"last_close": 180.0, "atr_14": 5.0}, equity=500)
    assert result is not None
    assert result["entry"] == 180.0
    assert result["stop"] == 170.0   # 180 - 2*5
    assert result["target"] == 195.0  # 180 + 3*5
    assert result["qty"] > 0


def test_compute_levels_no_atr(strat):
    result = strat.compute_levels("AAPL", {"last_close": 180.0}, equity=500)
    assert result is None


def test_compute_levels_no_close(strat):
    result = strat.compute_levels("AAPL", {"atr_14": 5.0}, equity=500)
    assert result is None


def test_compute_levels_too_small_atr(strat):
    """Tiny ATR → target too close → fails edge check."""
    result = strat.compute_levels("AAPL", {"last_close": 180.0, "atr_14": 0.01}, equity=500)
    # target = 180.03, net edge = (0.03/180)*10000 - 25 ≈ -23 bps → None
    assert result is None


# ── should_exit ──────────────────────────────────

def _mock_trade(fill=100, stop=90, target=115):
    t = MagicMock()
    t.__getitem__ = lambda self, k: {"fill_price": fill, "stop_price": stop, "target_price": target}.get(k)
    return t


def test_exit_stop_hit(strat):
    trade = _mock_trade(fill=100, stop=90, target=115)
    should, reason, price = strat.should_exit(trade, {"current_price": 88.0})
    assert should
    assert reason == "stop"


def test_exit_target_hit(strat):
    trade = _mock_trade(fill=100, stop=90, target=115)
    should, reason, price = strat.should_exit(trade, {"current_price": 118.0})
    assert should
    assert reason == "target"


def test_exit_time(strat):
    trade = _mock_trade(fill=100, stop=90, target=115)
    should, reason, price = strat.should_exit(trade, {"current_price": 105.0, "days_held": 10})
    assert should
    assert reason == "time_exit"


def test_exit_hold(strat):
    trade = _mock_trade(fill=100, stop=90, target=115)
    should, reason, price = strat.should_exit(trade, {"current_price": 105.0, "days_held": 3})
    assert not should
    assert reason == "hold"


def test_exit_no_price(strat):
    trade = _mock_trade()
    should, reason, price = strat.should_exit(trade, {})
    assert not should


# ── check_min_hold ──────────────────────────────

def test_min_hold_met(strat):
    assert strat.check_min_hold(3) is True


def test_min_hold_not_met(strat):
    assert strat.check_min_hold(1) is False


# ── Config ──────────────────────────────────────

def test_pead_universe():
    assert set(PEAD_UNIVERSE) == {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META"}


def test_pead_config_defaults():
    assert PEAD_CONFIG["surprise_threshold_pct"] == 5.0
    assert PEAD_CONFIG["max_hold_days"] == 10
    assert PEAD_CONFIG["min_hold_days"] == 2
    assert PEAD_CONFIG["max_capital_pct"] == 0.15

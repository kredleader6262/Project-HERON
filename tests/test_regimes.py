"""Tests for regime tagging."""

from heron.backtest.regimes import (
    is_earnings_season, vol_buckets_from_spy, tag_trades, regime_metrics, _percentile,
)


def test_is_earnings_season():
    assert is_earnings_season("2024-01-15") is True
    assert is_earnings_season("2024-04-30") is True
    assert is_earnings_season("2024-07-10") is True
    assert is_earnings_season("2024-10-25") is True
    # Outside the window: early in month or non-quarterly month.
    assert is_earnings_season("2024-01-09") is False
    assert is_earnings_season("2024-02-15") is False
    assert is_earnings_season("2024-12-15") is False
    # Garbage in:
    assert is_earnings_season("not-a-date") is False
    assert is_earnings_season("") is False


def test_percentile_simple():
    vals = [1, 2, 3, 4, 5]
    assert _percentile(vals, 0.0) == 1
    assert _percentile(vals, 1.0) == 5
    assert _percentile(vals, 0.5) == 3


def test_vol_buckets_returns_three_levels():
    # Construct a SPY-shaped series with monotonically increasing volatility.
    bars = []
    base = 100.0
    for i in range(120):
        # Vol grows over time to force distinct buckets.
        kick = (i // 30) * 0.001 + 0.001
        base *= 1 + (kick if i % 2 else -kick)
        bars.append({"ts": f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}T00:00:00Z", "close": base})
    buckets = vol_buckets_from_spy(bars, window=10)
    # All three labels should appear given the increasing-vol construction.
    seen = set(buckets.values())
    assert {"low", "mid", "high"}.issubset(seen)


def test_vol_buckets_handles_empty():
    assert vol_buckets_from_spy([]) == {}


def test_tag_trades_assigns_unknown_when_buckets_missing():
    trades = [{"entry_date": "2024-01-15", "net_pnl": 50.0}]
    tagged = tag_trades(trades, {})
    assert tagged[0]["vol_bucket"] == "unknown"
    assert tagged[0]["earnings_season"] is True


def test_regime_metrics_aggregates_vol_and_season():
    trades = [
        {"entry_date": "2024-01-15", "net_pnl": 100, "vol_bucket": "low", "earnings_season": True},
        {"entry_date": "2024-01-16", "net_pnl": -50, "vol_bucket": "low", "earnings_season": True},
        {"entry_date": "2024-02-10", "net_pnl": 30, "vol_bucket": "high", "earnings_season": False},
    ]
    m = regime_metrics(trades)
    assert m["vol"]["low"]["n_trades"] == 2
    assert m["vol"]["low"]["n_wins"] == 1
    assert m["vol"]["low"]["win_rate"] == 0.5
    assert m["vol"]["low"]["total_pnl"] == 50.0
    assert m["vol"]["high"]["n_trades"] == 1
    assert m["earnings_season"]["yes"]["n_trades"] == 2
    assert m["earnings_season"]["no"]["n_trades"] == 1
    assert m["earnings_season"]["no"]["total_pnl"] == 30.0

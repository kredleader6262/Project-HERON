"""Backtester (M13) — deterministic strategy replay.

See Project-HERON.md Section 12.
"""
from heron.backtest.engine import run_backtest
from heron.backtest.costs import round_trip_cost, slippage_bps
from heron.backtest.report import save_report, list_reports, get_report, reparity_report
from heron.backtest.runner import (
    run_strategy_backtest, spy_benchmark_curve, drawdown_curve, find_baseline_report,
)

__all__ = [
    "run_backtest", "round_trip_cost", "slippage_bps",
    "save_report", "list_reports", "get_report", "reparity_report",
    "run_strategy_backtest", "spy_benchmark_curve", "drawdown_curve",
    "find_baseline_report",
]

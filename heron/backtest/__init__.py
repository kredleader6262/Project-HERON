"""Backtester (M13) — deterministic strategy replay.

See Project-HERON.md Section 12.
"""
from heron.backtest.engine import run_backtest
from heron.backtest.costs import round_trip_cost, slippage_bps
from heron.backtest.report import save_report, list_reports, get_report

__all__ = [
    "run_backtest", "round_trip_cost", "slippage_bps",
    "save_report", "list_reports", "get_report",
]

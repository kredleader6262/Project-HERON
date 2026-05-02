"""Tests for parameter sweep."""

import json

import pytest

from heron.backtest.sweep import (
    SWEEPABLE_AXES, expand_grid, parse_axes, run_sweep,
)
from heron.journal.strategies import create_strategy
from heron.strategy.pead import PEAD_CONFIG

def test_expand_grid_cartesian():
    out = expand_grid({"a": [1, 2], "b": ["x", "y"]})
    assert len(out) == 4
    assert {"a": 1, "b": "x"} in out
    assert {"a": 2, "b": "y"} in out


def test_expand_grid_empty():
    assert expand_grid({}) == [{}]


def test_parse_axes_coerces_types():
    base = dict(PEAD_CONFIG)
    axes = parse_axes(["stop_mult=1.0,1.5,2.0", "max_hold_days=5,10"], base)
    assert axes == {"stop_mult": [1.0, 1.5, 2.0], "max_hold_days": [5, 10]}


def test_parse_axes_rejects_unknown():
    with pytest.raises(ValueError):
        parse_axes(["nonexistent=1,2"], {"foo": 1})


def test_parse_axes_requires_equals():
    with pytest.raises(ValueError):
        parse_axes(["stop_mult"], {"stop_mult": 1.0})


def test_parse_axes_requires_values():
    with pytest.raises(ValueError):
        parse_axes(["stop_mult="], {"stop_mult": 1.0})


def test_sweepable_axes_present_in_pead_config():
    """Every advertised axis must exist in PEAD config."""
    for axis in SWEEPABLE_AXES:
        assert axis in PEAD_CONFIG, f"axis {axis} missing from PEAD_CONFIG"


def test_run_sweep_caps_combos(conn, monkeypatch):
    create_strategy(conn, "swp1", "S", config=dict(PEAD_CONFIG))
    # 11 * 5 = 55 combos > cap of 50.
    axes = {
        "stop_mult": [1.0 + 0.1 * i for i in range(11)],
        "max_hold_days": [3, 5, 7, 10, 14],
    }
    with pytest.raises(ValueError, match="cap is 50"):
        run_sweep(conn, "swp1", axes, start="2024-01-01", end="2024-03-31")


def test_run_sweep_unknown_strategy(conn):
    with pytest.raises(ValueError, match="not found"):
        run_sweep(conn, "ghost", {"stop_mult": [1.0]},
                  start="2024-01-01", end="2024-02-01")

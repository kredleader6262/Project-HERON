"""Tests for the strategy template registry."""

import pytest

from heron.strategy.pead import PEADStrategy
from heron.strategy.templates import (
    TEMPLATES, get_template, list_templates,
    instantiate_from_template, ParamField, StrategyTemplate,
)


def test_pead_registered():
    assert "pead" in TEMPLATES
    t = get_template("pead")
    assert t.cls is PEADStrategy
    assert t.backtest_seeder is not None
    assert any(f.group == "Trigger" for f in t.param_schema)
    assert any(f.group == "Risk" for f in t.param_schema)


def test_unknown_template_raises():
    with pytest.raises(KeyError):
        get_template("does_not_exist")


def test_schema_keys_align_with_default_config():
    """Every schema field must reference a key the default_config knows about,
    otherwise the form will silently fail to round-trip."""
    t = get_template("pead")
    for f in t.param_schema:
        assert f.key in t.default_config, f"schema key {f.key!r} missing from default_config"


def test_build_config_merges_overrides():
    t = get_template("pead")
    cfg = t.build_config({"surprise_threshold_pct": "7.5", "max_positions": "5"})
    assert cfg["surprise_threshold_pct"] == 7.5
    assert cfg["max_positions"] == 5
    # Untouched defaults preserved
    assert cfg["stop_mult"] == 2.0
    assert cfg["universe"] == t.default_config["universe"]


def test_coerce_list_from_csv():
    t = get_template("pead")
    cfg = t.build_config({"universe": "AAPL, MSFT ,GOOGL"})
    assert cfg["universe"] == ["AAPL", "MSFT", "GOOGL"]


def test_coerce_validates_min_max():
    t = get_template("pead")
    with pytest.raises(ValueError):
        t.build_config({"max_capital_pct": "2.0"})  # above max=1.0
    with pytest.raises(ValueError):
        t.build_config({"max_positions": "0"})


def test_coerce_rejects_garbage():
    t = get_template("pead")
    with pytest.raises(ValueError):
        t.build_config({"surprise_threshold_pct": "not a number"})


def test_instantiate_round_trip():
    """instantiate_from_template with no overrides should behave like the
    canonical PEADStrategy() — same screen verdicts on a sample candidate."""
    canonical = PEADStrategy()
    built = instantiate_from_template("pead", "pead_test")

    cand = {"ticker": "AAPL", "surprise_pct": 8.0, "announced_hours_ago": 12}
    assert canonical.screen_candidate(cand) == built.screen_candidate(cand)

    cand_neg = {"ticker": "AAPL", "surprise_pct": -8.0, "announced_hours_ago": 12}
    assert canonical.screen_candidate(cand_neg) == built.screen_candidate(cand_neg)


def test_register_duplicate_raises():
    from heron.strategy.templates import register_template
    t = get_template("pead")
    with pytest.raises(ValueError):
        register_template(StrategyTemplate(
            name="pead", display_name="dup", description="",
            cls=t.cls, default_config={}, param_schema=[],
        ))


def test_list_templates_nonempty():
    ts = list_templates()
    assert any(t.name == "pead" for t in ts)

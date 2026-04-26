"""Strategy template registry.

A template describes a parameterizable strategy that the operator can
instantiate via the dashboard or CLI. The template owns:

- the BaseStrategy subclass that implements the trade logic
- a default config (sensible starting point)
- a typed param schema that drives form rendering and validation
- an optional candidate seeder for inline backtest previews

Templates are *parameterized*, not authored. We never accept user-supplied
code: that would put untrusted logic in the execution hot path.

To add a new template:
    1. Implement a BaseStrategy subclass (deterministic, no LLM calls).
    2. Define a default config dict and a param_schema.
    3. Register via `register_template`.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from heron.strategy.base import BaseStrategy
from heron.strategy.pead import PEADStrategy, PEAD_CONFIG, PEAD_UNIVERSE
from heron.backtest.seeders import synthetic_pead_candidates


@dataclass
class ParamField:
    """One configurable parameter on a template."""
    key: str
    label: str
    type: str                       # "float" | "int" | "bool" | "str" | "list"
    default: Any
    help: str = ""
    group: str = "General"
    min: Optional[float] = None
    max: Optional[float] = None


@dataclass
class StrategyTemplate:
    name: str
    display_name: str
    description: str
    cls: type[BaseStrategy]
    default_config: dict
    param_schema: list[ParamField]
    backtest_seeder: Optional[Callable] = None

    def coerce_value(self, field: ParamField, raw):
        """Coerce a form-string value into the field's declared type."""
        if raw is None or raw == "":
            return field.default
        try:
            if field.type == "float":
                v = float(raw)
            elif field.type == "int":
                v = int(raw)
            elif field.type == "bool":
                v = str(raw).lower() in ("1", "true", "yes", "on")
            elif field.type == "list":
                v = [s.strip() for s in (raw if isinstance(raw, list) else str(raw).split(",")) if s.strip()]
            else:
                v = str(raw)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{field.key}: cannot parse {raw!r} as {field.type}") from e
        if field.min is not None and isinstance(v, (int, float)) and v < field.min:
            raise ValueError(f"{field.key}: {v} below min {field.min}")
        if field.max is not None and isinstance(v, (int, float)) and v > field.max:
            raise ValueError(f"{field.key}: {v} above max {field.max}")
        return v

    def build_config(self, overrides: dict) -> dict:
        """Merge form overrides on top of defaults, coercing each field."""
        cfg = dict(self.default_config)
        for f in self.param_schema:
            if f.key in overrides:
                cfg[f.key] = self.coerce_value(f, overrides[f.key])
        return cfg


# ── Registry ────────────────────────────────────────────────────────────────

TEMPLATES: dict[str, StrategyTemplate] = {}


def register_template(t: StrategyTemplate):
    if t.name in TEMPLATES:
        raise ValueError(f"Template {t.name!r} already registered")
    TEMPLATES[t.name] = t
    return t


def get_template(name: str) -> StrategyTemplate:
    if name not in TEMPLATES:
        raise KeyError(f"Unknown strategy template: {name!r}. Available: {list(TEMPLATES)}")
    return TEMPLATES[name]


def list_templates() -> list[StrategyTemplate]:
    return list(TEMPLATES.values())


def instantiate_from_template(name: str, strategy_id: str,
                              config_overrides: Optional[dict] = None,
                              **kwargs) -> BaseStrategy:
    """Build a ready-to-use strategy instance from a registered template."""
    t = get_template(name)
    config = t.build_config(config_overrides or {})
    return t.cls(strategy_id=strategy_id, config=config, **kwargs)


# ── PEAD template ───────────────────────────────────────────────────────────

_PEAD_SCHEMA = [
    ParamField("universe", "Universe", "list", PEAD_UNIVERSE,
               help="Comma-separated tickers", group="Universe"),

    ParamField("surprise_threshold_pct", "Surprise threshold (%)", "float", 5.0,
               help="Minimum |EPS surprise| to trigger", group="Trigger",
               min=0.0, max=50.0),
    ParamField("surprise_window_hours", "Window (hours)", "int", 24,
               help="Max age of announcement", group="Trigger", min=1, max=168),

    ParamField("atr_period", "ATR period (days)", "int", 14,
               help="Lookback for ATR calc", group="Sizing", min=2, max=60),
    ParamField("stop_mult", "Stop ATR multiplier", "float", 2.0,
               help="Stop = entry − (stop_mult × ATR)", group="Sizing", min=0.5, max=10.0),
    ParamField("target_mult", "Target ATR multiplier", "float", 3.0,
               help="Target = entry + (target_mult × ATR)", group="Sizing", min=0.5, max=10.0),
    ParamField("min_edge_bps", "Minimum edge (bps)", "int", 30,
               help="Refuse trades whose target offers less than this after costs",
               group="Sizing", min=0, max=500),

    ParamField("max_capital_pct", "Max capital per position (%)", "float", 0.15,
               help="0.15 = 15% of equity", group="Risk", min=0.01, max=1.0),
    ParamField("max_positions", "Max concurrent positions", "int", 3,
               group="Risk", min=1, max=20),
    ParamField("drawdown_budget_pct", "Drawdown budget (%)", "float", 0.05,
               help="Auto-retire if exceeded", group="Risk", min=0.01, max=0.5),
    ParamField("min_conviction", "Min LLM conviction", "float", 0.0,
               help="0 disables; baselines use 0", group="Risk", min=0.0, max=1.0),

    ParamField("min_hold_days", "Min hold (days)", "int", 2,
               help="PDT safety floor", group="Hold", min=1, max=30),
    ParamField("max_hold_days", "Max hold (days)", "int", 10,
               help="Time-exit ceiling", group="Hold", min=1, max=60),
]


register_template(StrategyTemplate(
    name="pead",
    display_name="Post-Earnings Announcement Drift",
    description=(
        "Long-only swing strategy on positive earnings surprises. "
        "Deterministic ATR-based stops/targets; LLM may veto via conviction."
    ),
    cls=PEADStrategy,
    default_config=dict(PEAD_CONFIG),
    param_schema=_PEAD_SCHEMA,
    backtest_seeder=synthetic_pead_candidates,
))

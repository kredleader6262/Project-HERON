"""Abstract base for all HERON strategies.

Strategies consume candidates from the Research layer and make deterministic
trade decisions. No LLM calls allowed in any method here.
"""

from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """Base class for all HERON strategies.

    Subclass this and implement the abstract methods. The Strategy runner
    calls them in sequence: screen → compute_levels → should_enter → should_exit.
    """

    def __init__(self, strategy_id, config=None):
        self.strategy_id = strategy_id
        self.config = config or {}

    @property
    def max_capital_pct(self):
        return self.config.get("max_capital_pct", 0.15)

    @property
    def max_positions(self):
        return self.config.get("max_positions", 3)

    @property
    def drawdown_budget_pct(self):
        return self.config.get("drawdown_budget_pct", 0.05)

    @property
    def min_hold_days(self):
        return self.config.get("min_hold_days", 2)

    @abstractmethod
    def screen_candidate(self, candidate, market_data):
        """Decide if a candidate passes strategy-specific filters.

        Returns (accept: bool, reason: str). Called before risk checks.
        """

    @abstractmethod
    def compute_levels(self, ticker, market_data, equity):
        """Compute entry, stop, target prices for a ticker.

        Returns dict: {entry, stop, target, atr, qty} or None if no trade.
        """

    @abstractmethod
    def should_exit(self, trade, market_data):
        """Check if an open trade should be closed.

        Returns (should_close: bool, reason: str, close_price: float).
        Called on every poll cycle for open positions.
        """

    def time_exit_due(self, trade, current_day_count):
        """Check if a trade has exceeded its max hold period.

        Default: exit at day 10 (PEAD spec). Override per strategy.
        """
        max_days = self.config.get("max_hold_days", 10)
        return current_day_count >= max_days

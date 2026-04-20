"""PEAD — Post-Earnings Announcement Drift strategy.

The first and reference strategy for HERON. Deterministic triggers,
ATR-based stops/targets, swing holds (2-10 days).

See Project-HERON.md Section 9 for full specification.

Parameters:
  Universe:       AAPL, MSFT, GOOGL, AMZN, NVDA, META
  Trigger:        Earnings surprise ≥ 5% on consensus EPS, within last 24h
  Entry:          Next session open, market order
  Stop:           2× 14-day ATR below entry
  Target:         3× 14-day ATR above entry, or time-exit day 10
  Position size:  15% of equity, max 3 concurrent
  Min hold:       2 trading days (PDT safety)
"""

from heron.strategy.base import BaseStrategy
from heron.strategy.sizing import size_position, compute_stop_target, minimum_edge_check

PEAD_UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META"]

PEAD_CONFIG = {
    "universe": PEAD_UNIVERSE,
    "surprise_threshold_pct": 5.0,     # minimum earnings surprise %
    "surprise_window_hours": 24,       # must be announced within this window
    "atr_period": 14,                  # days for ATR calculation
    "stop_mult": 2.0,                  # ATR multiplier for stop
    "target_mult": 3.0,               # ATR multiplier for target
    "max_hold_days": 10,               # time-exit
    "max_capital_pct": 0.15,
    "max_positions": 3,
    "drawdown_budget_pct": 0.05,
    "min_conviction": 0.0,            # 0 for deterministic variant
    "min_hold_days": 2,               # PDT safety
    "min_edge_bps": 30,               # minimum expected edge after costs
}


class PEADStrategy(BaseStrategy):
    """Post-Earnings Announcement Drift.

    Deterministic variant: enters every qualifying surprise.
    LLM variant: same rules, but Research layer can veto low-quality beats.
    """

    def __init__(self, strategy_id="pead_v1", config=None, is_llm_variant=True):
        super().__init__(strategy_id, config or dict(PEAD_CONFIG))
        self.is_llm_variant = is_llm_variant

    @property
    def universe(self):
        return self.config.get("universe", PEAD_UNIVERSE)

    def screen_candidate(self, candidate, market_data=None):
        """Screen: ticker in universe, surprise ≥ threshold, within window.

        candidate: dict with keys: ticker, surprise_pct, announced_hours_ago,
                   conviction (optional, from Research layer).
        """
        ticker = candidate.get("ticker", "")
        if ticker not in self.universe:
            return False, f"{ticker} not in PEAD universe"

        surprise = candidate.get("surprise_pct", 0)
        threshold = self.config["surprise_threshold_pct"]
        if abs(surprise) < threshold:
            return False, f"Surprise {surprise:.1f}% below {threshold}% threshold"

        hours_ago = candidate.get("announced_hours_ago", 999)
        window = self.config["surprise_window_hours"]
        if hours_ago > window:
            return False, f"Announced {hours_ago:.0f}h ago, window is {window}h"

        # Direction check: we only go long on positive surprises
        if surprise < 0:
            return False, f"Negative surprise {surprise:.1f}%, PEAD only trades positive"

        # LLM variant: check conviction from Research layer
        if self.is_llm_variant:
            conviction = candidate.get("conviction", 1.0)
            min_conv = self.config.get("min_conviction", 0.0)
            if conviction < min_conv:
                return False, f"Conviction {conviction:.2f} below {min_conv:.2f}"
            # LLM can veto via conviction=0
            if candidate.get("llm_veto"):
                return False, f"LLM veto: {candidate.get('veto_reason', 'low quality beat')}"

        return True, f"Qualified: {ticker} +{surprise:.1f}% surprise"

    def compute_levels(self, ticker, market_data, equity):
        """Compute entry, stop, target from ATR.

        market_data: dict with keys: last_close, atr_14 (14-day ATR).
        Returns dict or None.
        """
        last_close = market_data.get("last_close")
        atr = market_data.get("atr_14")
        if not last_close or not atr:
            return None

        entry = last_close  # market order at open ≈ last close (approximation)

        stop, target = compute_stop_target(
            entry, atr,
            stop_mult=self.config["stop_mult"],
            target_mult=self.config["target_mult"],
        )
        if not stop or not target:
            return None

        # Edge check
        passes, net_bps = minimum_edge_check(
            entry, target,
            cost_bps=25,
            min_edge_bps=self.config.get("min_edge_bps", 30),
        )
        if not passes:
            return None

        qty, risk_dollars, capital_used = size_position(
            equity, entry, stop,
            risk_pct=0.05,
            max_capital_pct=self.config["max_capital_pct"],
        )
        if qty <= 0:
            return None

        return {
            "ticker": ticker,
            "entry": entry,
            "stop": stop,
            "target": target,
            "atr": atr,
            "qty": qty,
            "risk_dollars": risk_dollars,
            "capital_used": capital_used,
            "net_edge_bps": net_bps,
        }

    def should_exit(self, trade, market_data):
        """Check stop, target, or time-exit conditions.

        trade: sqlite Row with fill_price, stop_price, target_price, filled_at, etc.
        market_data: dict with current_price, days_held.
        """
        price = market_data.get("current_price")
        if not price:
            return False, "no price", 0

        stop = trade["stop_price"]
        target = trade["target_price"]

        # Stop hit
        if stop and price <= stop:
            return True, "stop", price

        # Target hit
        if target and price >= target:
            return True, "target", price

        # Time exit
        days_held = market_data.get("days_held", 0)
        if self.time_exit_due(trade, days_held):
            return True, "time_exit", price

        return False, "hold", price

    def check_min_hold(self, days_held):
        """Check if minimum hold period is met before allowing exit."""
        return days_held >= self.min_hold_days

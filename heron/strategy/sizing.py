"""Position sizing.

Sizes positions based on strategy risk budget, equity, ATR-based stops.
Never risks more than the configured per-trade max (default 5% of equity).
See Project-HERON.md Section 4.3, 9.1.
"""


def size_position(equity, entry_price, stop_price, risk_pct=0.05, max_capital_pct=0.15):
    """Compute position size in shares (fractional OK).

    Uses risk-based sizing: qty = (equity * risk_pct) / |entry - stop|
    Capped by max capital allocation: qty <= (equity * max_capital_pct) / entry
    
    Returns (qty, risk_dollars, capital_used).
    """
    if not entry_price or not stop_price or entry_price <= 0:
        return 0, 0, 0

    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share == 0:
        return 0, 0, 0

    risk_budget = equity * risk_pct
    qty_by_risk = risk_budget / risk_per_share

    # Cap by max capital allocation
    max_capital = equity * max_capital_pct
    qty_by_capital = max_capital / entry_price

    qty = min(qty_by_risk, qty_by_capital)
    # Round to reasonable precision (Alpaca supports fractional to 9 decimals)
    qty = round(qty, 4)

    if qty <= 0:
        return 0, 0, 0

    risk_dollars = risk_per_share * qty
    capital_used = entry_price * qty
    return qty, risk_dollars, capital_used


def compute_stop_target(entry_price, atr, stop_mult=2.0, target_mult=3.0):
    """Compute stop-loss and take-profit from ATR multiples.

    PEAD default: stop = 2x ATR below entry, target = 3x ATR above entry.
    Returns (stop_price, target_price).
    """
    if not atr or atr <= 0 or not entry_price:
        return None, None
    stop = round(entry_price - (atr * stop_mult), 2)
    target = round(entry_price + (atr * target_mult), 2)
    return max(stop, 0.01), target  # stop can't go negative


def minimum_edge_check(entry_price, target_price, cost_bps=25, min_edge_bps=30):
    """Check if expected profit exceeds costs + minimum edge.

    Returns (passes: bool, expected_bps: float).
    cost_bps: estimated round-trip friction (IEX spread assumption).
    min_edge_bps: minimum net edge required (default 30 bps per spec).
    """
    if not entry_price or not target_price or entry_price <= 0:
        return False, 0
    gross_bps = ((target_price - entry_price) / entry_price) * 10000
    net_bps = gross_bps - cost_bps
    return net_bps >= min_edge_bps, net_bps

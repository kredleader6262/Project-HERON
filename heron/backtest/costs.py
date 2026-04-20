"""Transaction cost model for backtests.

Alpaca IEX tier: no commission on stock trades.
SEC Section 31 fee: $27.80 per $1M of sell proceeds (2024 rate).
FINRA TAF: $0.000166 per share sold (cap $8.30 per trade) (2024 rate).
Slippage: half the prevailing spread + 5 bps (Section 12).
"""

SEC_FEE_RATE = 27.80 / 1_000_000   # on sell proceeds
FINRA_TAF_PER_SHARE = 0.000166
FINRA_TAF_CAP = 8.30
DEFAULT_SLIPPAGE_BPS = 5.0
DEFAULT_HALF_SPREAD_BPS = 5.0


def sell_fees(qty, price):
    """SEC + FINRA fees on the sell side of a round-trip."""
    proceeds = qty * price
    sec = proceeds * SEC_FEE_RATE
    taf = min(qty * FINRA_TAF_PER_SHARE, FINRA_TAF_CAP)
    return sec + taf


def slippage_bps(half_spread_bps=DEFAULT_HALF_SPREAD_BPS,
                 extra_bps=DEFAULT_SLIPPAGE_BPS):
    """Total one-way slippage in basis points."""
    return half_spread_bps + extra_bps


def apply_slippage(price, side, bps=None):
    """Adjust a fill price for slippage. side='buy' pays up, 'sell' gives up."""
    bps = bps if bps is not None else slippage_bps()
    adj = price * bps / 10_000
    return price + adj if side == "buy" else price - adj


def round_trip_cost(entry_price, exit_price, qty, *, slip_bps=None):
    """Total dollar cost of a round-trip (both legs slipped + sell-side fees)."""
    slip_bps = slip_bps if slip_bps is not None else slippage_bps()
    entry_slipped = apply_slippage(entry_price, "buy", slip_bps)
    exit_slipped = apply_slippage(exit_price, "sell", slip_bps)
    entry_cost = (entry_slipped - entry_price) * qty
    exit_cost = (exit_price - exit_slipped) * qty
    fees = sell_fees(qty, exit_slipped)
    return {
        "entry_fill": entry_slipped,
        "exit_fill": exit_slipped,
        "slippage_dollars": entry_cost + exit_cost,
        "fees_dollars": fees,
        "total_cost": entry_cost + exit_cost + fees,
    }

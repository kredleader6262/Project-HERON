"""Alpaca market data client — OHLCV bars and quotes (IEX tier).

Fetches from Alpaca Data API, caches to SQLite. Returns from cache on repeat calls.
See Project-HERON.md Section 4.1 for IEX constraints.
"""

from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from heron.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, QUOTE_STALE_SECONDS
from heron.data.cache import get_bars, upsert_bars, update_fetch_log

# Map config timeframe strings to alpaca TimeFrame objects
_TF_MAP = {
    "1Min": TimeFrame(1, TimeFrameUnit.Minute),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
    "1Day": TimeFrame(1, TimeFrameUnit.Day),
}


def _client():
    return StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def fetch_bars(conn, ticker, timeframe="1Day", start=None, end=None):
    """Fetch OHLCV bars from Alpaca, cache them, return list of Row objects.

    If the cache already covers the requested [start, end] range we return it.
    If the cache covers part of the range, we fetch only the missing tail
    (Alpaca queries are cheap; we don't bother with mid-range gaps).
    """
    if start is None:
        start = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
    if end is None:
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cached = get_bars(conn, ticker, timeframe, start, end)
    fetch_start = start
    if cached:
        latest_ts = cached[-1]["ts"]            # ISO with timezone
        latest_date = latest_ts[:10]
        end_date = end[:10] if len(end) >= 10 else end
        # Cached range already covers requested end → no refetch.
        if latest_date >= end_date:
            return cached
        # Otherwise fetch from the day after the latest cached bar.
        try:
            fetch_start = (datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))
                           + timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            fetch_start = start  # fall back to a full refetch on weird timestamps

    tf = _TF_MAP.get(timeframe)
    if not tf:
        raise ValueError(f"Unknown timeframe: {timeframe}. Valid: {list(_TF_MAP)}")

    client = _client()
    req = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=tf,
        start=datetime.fromisoformat(fetch_start) if isinstance(fetch_start, str) else fetch_start,
        end=datetime.fromisoformat(end) if isinstance(end, str) else end,
    )
    barset = client.get_stock_bars(req)

    rows = []
    for bar in barset[ticker]:
        rows.append({
            "ticker": ticker,
            "timeframe": timeframe,
            "ts": bar.timestamp.isoformat(),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
            "source": "alpaca_iex",
        })

    if rows:
        upsert_bars(conn, rows)
        update_fetch_log(conn, "alpaca_bars", ticker)

    return get_bars(conn, ticker, timeframe, start, end)


def fetch_bars_bulk(conn, tickers, timeframe="1Day", start=None, end=None):
    """Fetch bars for multiple tickers. Returns dict of ticker -> [Row]."""
    results = {}
    for t in tickers:
        results[t] = fetch_bars(conn, t, timeframe, start, end)
    return results


def fetch_latest_quote(ticker):
    """Get latest quote for a ticker. Returns dict with bid/ask/age_seconds.

    Does NOT cache — quotes are ephemeral.
    """
    client = _client()
    req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
    quotes = client.get_stock_latest_quote(req)
    q = quotes[ticker]

    now = datetime.now(timezone.utc)
    age = (now - q.timestamp).total_seconds() if q.timestamp else float("inf")

    return {
        "ticker": ticker,
        "bid": float(q.bid_price) if q.bid_price else None,
        "ask": float(q.ask_price) if q.ask_price else None,
        "bid_size": float(q.bid_size) if q.bid_size else None,
        "ask_size": float(q.ask_size) if q.ask_size else None,
        "timestamp": q.timestamp.isoformat() if q.timestamp else None,
        "age_seconds": round(age, 1),
        "is_stale": age > QUOTE_STALE_SECONDS,
    }

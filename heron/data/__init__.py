"""Data layer facade. Single entry point for market data and news.

Usage:
    from heron.data import DataFeed
    feed = DataFeed()
    bars = feed.fetch_watchlist_bars()
    news = feed.fetch_watchlist_news()
"""

from datetime import datetime, timedelta, timezone

from heron.config import WATCHLIST, TIMEFRAMES
from heron.data.cache import get_conn, init_db, get_articles
from heron.data.alpaca_market import fetch_bars, fetch_bars_bulk, fetch_latest_quote
from heron.data.alpaca_news import fetch_news
from heron.data.rss_news import fetch_all_rss


class DataFeed:
    def __init__(self, db_path=None):
        self.conn = get_conn(db_path)
        init_db(self.conn)

    def close(self):
        self.conn.close()

    def fetch_watchlist_bars(self, timeframe=None, start=None, end=None):
        """Fetch bars for all watchlist tickers. Returns {ticker: [Row]}."""
        tf = timeframe or TIMEFRAMES[0]
        return fetch_bars_bulk(self.conn, WATCHLIST, tf, start, end)

    def fetch_ticker_bars(self, ticker, timeframe=None, start=None, end=None):
        tf = timeframe or TIMEFRAMES[0]
        return fetch_bars(self.conn, ticker, tf, start, end)

    def fetch_watchlist_news(self, start=None, end=None, limit=50):
        """Fetch news from Alpaca + all RSS sources. Returns merged articles."""
        # Alpaca news (watchlist-filtered)
        fetch_news(self.conn, tickers=WATCHLIST, start=start, end=end, limit=limit)
        # RSS sources (not ticker-filtered at fetch — filtered by ticker extraction)
        fetch_all_rss(self.conn)
        # Return merged from cache
        s = start or (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        e = end or datetime.now(timezone.utc).isoformat()
        if isinstance(s, datetime):
            s = s.isoformat()
        if isinstance(e, datetime):
            e = e.isoformat()
        return get_articles(self.conn, start=s, end=e)

    def get_quote(self, ticker):
        """Latest quote with staleness check. Not cached."""
        return fetch_latest_quote(ticker)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
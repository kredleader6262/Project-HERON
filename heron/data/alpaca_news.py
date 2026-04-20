"""Alpaca News API client. Fetches, sanitizes, deduplicates, caches."""

from datetime import datetime, timedelta, timezone

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from heron.config import ALPACA_API_KEY, ALPACA_SECRET_KEY
from heron.data.cache import upsert_articles, get_articles, update_fetch_log
from heron.data.sanitize import sanitize, sanitize_headline


def _client():
    return NewsClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def fetch_news(conn, tickers=None, start=None, end=None, limit=50):
    """Fetch news from Alpaca, sanitize, cache, return articles.

    Dedup is handled by cache (INSERT OR IGNORE on id).
    """
    if start is None:
        start = datetime.now(timezone.utc) - timedelta(days=1)
    elif isinstance(start, str):
        start = datetime.fromisoformat(start)

    if end is None:
        end = datetime.now(timezone.utc)
    elif isinstance(end, str):
        end = datetime.fromisoformat(end)

    client = _client()
    req = NewsRequest(
        symbols=",".join(tickers) if isinstance(tickers, (list, tuple)) else tickers,
        start=start,
        end=end,
        limit=limit,
        sort="DESC",
    )
    raw_news = client.get_news(req)
    # NewsSet.data is {'news': [News, ...]}
    items = raw_news.data.get("news", []) if hasattr(raw_news, "data") else []

    articles = []
    for item in items:
        article_tickers = list(getattr(item, "symbols", None) or [])
        articles.append({
            "id": f"alpaca:{item.id}",
            "source": "alpaca_news",
            "headline": sanitize_headline(item.headline or ""),
            "summary": sanitize(getattr(item, "summary", "") or ""),
            "body_sanitized": sanitize(getattr(item, "content", "") or ""),
            "tickers": article_tickers,
            "published_at": item.created_at.isoformat() if item.created_at else "",
            "credibility_weight": 0.8,
        })

    if articles:
        upsert_articles(conn, articles)
        update_fetch_log(conn, "alpaca_news")

    # Return from cache (includes any previously-fetched articles in range)
    start_str = start.isoformat() if isinstance(start, datetime) else start
    end_str = end.isoformat() if isinstance(end, datetime) else end
    return get_articles(conn, start=start_str, end=end_str, source="alpaca_news")

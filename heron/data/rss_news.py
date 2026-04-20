"""RSS news fetcher for SEC EDGAR, Fed, Treasury, BLS, etc.

Each feed is parsed, sanitized, tagged with credibility weight, and cached.
All scraped text treated as adversarial. See Project-HERON.md Section 4.1.1.
"""

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from heron.config import NEWS_SOURCES, SEC_USER_AGENT, WATCHLIST
from heron.data.cache import upsert_articles, update_fetch_log
from heron.data.sanitize import sanitize, sanitize_headline

log = logging.getLogger(__name__)

# Precompile ticker matcher — matches whole-word ticker symbols in text
_TICKER_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in WATCHLIST) + r")\b"
)


def _parse_published(entry):
    """Extract published datetime from a feed entry, return ISO string or ''."""
    for field in ("published", "updated", "created"):
        raw = entry.get(field)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except (TypeError, ValueError) as e:
            log.debug(f"Bad RSS date {field!r}={raw!r}: {e}")
    return datetime.now(timezone.utc).isoformat()


def _extract_tickers(text):
    """Find watchlist tickers mentioned in text."""
    if not text:
        return []
    return list(set(_TICKER_RE.findall(text)))


def _make_article_id(source_name, entry):
    """Deterministic article ID for dedup."""
    entry_id = entry.get("id") or entry.get("link") or entry.get("title", "")
    return f"{source_name}:{entry_id}"


def fetch_rss_source(conn, source_cfg):
    """Fetch and cache articles from a single RSS source config dict."""
    name = source_cfg["name"]
    feed_url = source_cfg.get("feed_url")
    if not feed_url:
        return []

    weight = source_cfg.get("weight", 0.5)

    # SEC EDGAR requires User-Agent header
    headers = {}
    if "sec_edgar" in name:
        headers["User-Agent"] = SEC_USER_AGENT

    try:
        resp = httpx.get(feed_url, headers=headers, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        update_fetch_log(conn, name, status=f"error: {e}")
        return []

    feed = feedparser.parse(resp.text)
    articles = []

    for entry in feed.entries:
        headline = sanitize_headline(entry.get("title", ""))
        summary = sanitize(entry.get("summary", ""))
        body = sanitize(entry.get("content", [{}])[0].get("value", "") if entry.get("content") else "")
        combined_text = f"{headline} {summary} {body}"
        tickers = _extract_tickers(combined_text)

        articles.append({
            "id": _make_article_id(name, entry),
            "source": name,
            "headline": headline,
            "summary": summary,
            "body_sanitized": body,
            "tickers": tickers,
            "published_at": _parse_published(entry),
            "credibility_weight": weight,
        })

    if articles:
        upsert_articles(conn, articles)
    update_fetch_log(conn, name, status="ok")
    return articles


def fetch_all_rss(conn):
    """Fetch from all enabled RSS sources. Returns total article count."""
    total = 0
    for src in NEWS_SOURCES:
        if not src.get("enabled", True):
            continue
        if not src.get("feed_url"):
            continue  # Skip non-RSS sources (e.g. alpaca_news)
        articles = fetch_rss_source(conn, src)
        total += len(articles)
    return total

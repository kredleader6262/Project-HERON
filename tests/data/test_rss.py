"""Tests for RSS news fetcher helper functions."""

from datetime import datetime, timezone
from heron.data.rss_news import _extract_tickers, _parse_published, _make_article_id


# --- _extract_tickers ---

def test_extract_tickers_finds_watchlist():
    text = "Apple (AAPL) and Microsoft (MSFT) reported strong earnings"
    result = _extract_tickers(text)
    assert set(result) == {"AAPL", "MSFT"}


def test_extract_tickers_no_match():
    assert _extract_tickers("No tickers mentioned here") == []


def test_extract_tickers_empty():
    assert _extract_tickers("") == []
    assert _extract_tickers(None) == []


def test_extract_tickers_no_partial_match():
    # "SPY" should match, but "SPYING" should not (word boundary)
    text = "SPYING on SPY performance"
    result = _extract_tickers(text)
    assert result == ["SPY"]


def test_extract_tickers_dedup():
    text = "AAPL beats. AAPL again. AAPL everywhere."
    result = _extract_tickers(text)
    assert result == ["AAPL"]


def test_extract_tickers_etfs():
    text = "SPY QQQ IWM DIA all down today"
    result = _extract_tickers(text)
    assert set(result) == {"SPY", "QQQ", "IWM", "DIA"}


# --- _parse_published ---

def test_parse_published_rfc2822():
    entry = {"published": "Sat, 19 Apr 2026 14:30:00 GMT"}
    result = _parse_published(entry)
    assert "2026-04-19" in result


def test_parse_published_falls_back_to_updated():
    entry = {"updated": "Sat, 19 Apr 2026 10:00:00 GMT"}
    result = _parse_published(entry)
    assert "2026-04-19" in result


def test_parse_published_missing_returns_now():
    entry = {}
    result = _parse_published(entry)
    # Should be a valid ISO timestamp from "now"
    dt = datetime.fromisoformat(result)
    assert dt.year >= 2026


def test_parse_published_bad_format_returns_now():
    entry = {"published": "not a date"}
    result = _parse_published(entry)
    dt = datetime.fromisoformat(result)
    assert dt.year >= 2026


# --- _make_article_id ---

def test_make_article_id_uses_entry_id():
    entry = {"id": "https://sec.gov/filing/123", "link": "https://sec.gov/link", "title": "Title"}
    assert _make_article_id("sec_edgar_8k", entry) == "sec_edgar_8k:https://sec.gov/filing/123"


def test_make_article_id_falls_back_to_link():
    entry = {"link": "https://example.com/article", "title": "Title"}
    assert _make_article_id("bls", entry) == "bls:https://example.com/article"


def test_make_article_id_falls_back_to_title():
    entry = {"title": "Fed holds rates"}
    assert _make_article_id("federal_reserve", entry) == "federal_reserve:Fed holds rates"


def test_make_article_id_empty_entry():
    entry = {}
    result = _make_article_id("test", entry)
    assert result == "test:"

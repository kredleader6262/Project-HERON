"""Tests for config loading."""

from heron.config import (
    WATCHLIST, MEGA_CAP, TICKER_FAMILIES, CACHE_DB, QUOTE_STALE_SECONDS,
    NEWS_SOURCES, TIMEFRAMES, MONTHLY_COST_CEILING,
)


def test_watchlist_has_12_tickers():
    assert len(WATCHLIST) == 12


def test_watchlist_mega_cap():
    assert set(MEGA_CAP) == {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META"}


def test_watchlist_includes_etfs():
    for etf in ("SPY", "QQQ", "IWM", "DIA", "XLF", "XLE"):
        assert etf in WATCHLIST


def test_ticker_families_sp500():
    assert set(TICKER_FAMILIES["sp500"]) == {"SPY", "VOO", "IVV"}


def test_ticker_families_nasdaq():
    assert set(TICKER_FAMILIES["nasdaq100"]) == {"QQQ", "QQQM"}


def test_quote_stale_seconds():
    assert QUOTE_STALE_SECONDS == 10


def test_news_sources_present():
    assert len(NEWS_SOURCES) >= 8
    names = [s["name"] for s in NEWS_SOURCES]
    assert "alpaca_news" in names
    assert "sec_edgar_8k" in names
    assert "federal_reserve" in names
    assert "bls" in names


def test_news_source_weights():
    by_name = {s["name"]: s for s in NEWS_SOURCES}
    assert by_name["sec_edgar_8k"]["weight"] == 1.0
    assert by_name["alpaca_news"]["weight"] == 0.8
    assert by_name["seeking_alpha"]["weight"] == 0.4


def test_timeframes_default():
    assert "1Day" in TIMEFRAMES


def test_cost_ceiling():
    assert MONTHLY_COST_CEILING == 45.0


def test_cache_db_path():
    assert str(CACHE_DB).endswith("heron.db")

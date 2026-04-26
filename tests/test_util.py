"""Tests for heron.util timezone helpers."""

from datetime import datetime, timezone

from heron.util import (
    trading_day_ny,
    trading_day_of_iso,
    trading_day_start_utc_iso,
)


def test_trading_day_ny_evening_utc_still_prev_ny_day():
    """23:00 UTC on Apr 21 is 19:00 ET on Apr 21 (DST): same NY day."""
    t = datetime(2026, 4, 21, 23, 0, tzinfo=timezone.utc)
    assert trading_day_ny(t) == "2026-04-21"


def test_trading_day_ny_early_utc_is_prev_ny_day():
    """02:00 UTC on Apr 22 is 22:00 ET on Apr 21 (DST): prior NY day."""
    t = datetime(2026, 4, 22, 2, 0, tzinfo=timezone.utc)
    assert trading_day_ny(t) == "2026-04-21"


def test_trading_day_of_iso_roundtrip():
    # 03:30 UTC on Apr 22 == 23:30 ET Apr 21 (EDT)
    assert trading_day_of_iso("2026-04-22T03:30:00+00:00") == "2026-04-21"
    # 14:00 UTC on Apr 22 == 10:00 ET Apr 22
    assert trading_day_of_iso("2026-04-22T14:00:00+00:00") == "2026-04-22"


def test_trading_day_start_utc_iso_thresholds_trade_created_late_ny():
    """A trade created at 22:00 ET Apr 21 (02:00 UTC Apr 22) belongs to Apr 21,
    so a threshold computed at 10:00 ET Apr 22 should be AFTER that trade."""
    now = datetime(2026, 4, 22, 14, 0, tzinfo=timezone.utc)  # 10:00 ET Apr 22
    threshold = trading_day_start_utc_iso(now)
    trade_created = "2026-04-22T02:00:00+00:00"  # 22:00 ET Apr 21
    assert trade_created < threshold

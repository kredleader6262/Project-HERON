"""Tiny shared utilities."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")


def utc_now_iso():
    """UTC timestamp as ISO-8601 string. Single source of truth for timestamps."""
    return datetime.now(timezone.utc).isoformat()


def utc_today():
    """UTC calendar date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def trading_day_ny(now=None):
    """Current US-equity calendar date (America/New_York) as YYYY-MM-DD.

    Use this — not utc_today — for daily caps, PDT same-day detection, and any
    rule expressed in market-calendar terms. A trade filled at 18:00 ET still
    belongs to that ET trading day even though it's already tomorrow in UTC.
    """
    n = now or datetime.now(timezone.utc)
    return n.astimezone(_NY).strftime("%Y-%m-%d")


def trading_day_of_iso(iso_ts):
    """NY trading day for a stored UTC ISO timestamp (YYYY-MM-DD)."""
    return datetime.fromisoformat(iso_ts).astimezone(_NY).strftime("%Y-%m-%d")


def trading_day_start_utc_iso(now=None):
    """UTC ISO of the start (00:00 ET) of the current NY trading day.

    Use this as a threshold when comparing to stored UTC ISO timestamps:
      `WHERE created_at >= ?` with this value selects rows created since
      the NY day began, regardless of where the UTC date boundary falls.
    """
    n = now or datetime.now(timezone.utc)
    ny_midnight = n.astimezone(_NY).replace(hour=0, minute=0, second=0, microsecond=0)
    return ny_midnight.astimezone(timezone.utc).isoformat()

"""Tiny shared utilities."""

from datetime import datetime, timezone


def utc_now_iso():
    """UTC timestamp as ISO-8601 string. Single source of truth for timestamps."""
    return datetime.now(timezone.utc).isoformat()


def utc_today():
    """UTC calendar date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

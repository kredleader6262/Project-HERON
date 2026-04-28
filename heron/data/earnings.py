"""Earnings calendar + surprise data.

Primary source: Finnhub `/calendar/earnings` (free tier; requires FINNHUB_API_KEY).
Fallback: EDGAR 8-K item 2.02 scraping (TODO — non-trivial EPS extraction).

All fetched events land in the `earnings_events` table. The backtest seeder
reads from cache only — no network in deterministic replay paths.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import json as _json
from datetime import date, datetime, timedelta

from heron.config import FINNHUB_API_KEY
from heron.util import utc_now_iso

_FINNHUB_BASE = "https://finnhub.io/api/v1/calendar/earnings"


def _surprise_pct(actual, estimate):
    if actual is None or estimate is None:
        return None
    try:
        a = float(actual); e = float(estimate)
    except (TypeError, ValueError):
        return None
    if e == 0:
        return None
    return round((a - e) / abs(e) * 100, 2)


def _http_get_json(url, *, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "HERON/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read().decode("utf-8"))


def _chunk_dates(start, end, days=90):
    """Yield (chunk_start, chunk_end) date pairs of length <= days. Inclusive ends."""
    s = date.fromisoformat(start) if isinstance(start, str) else start
    e = date.fromisoformat(end) if isinstance(end, str) else end
    cur = s
    while cur <= e:
        nxt = min(cur + timedelta(days=days - 1), e)
        yield cur.isoformat(), nxt.isoformat()
        cur = nxt + timedelta(days=1)


def fetch_finnhub_earnings(start, end, *, universe=None, api_key=None):
    """Fetch earnings calendar from Finnhub between [start, end] (YYYY-MM-DD).

    Optionally filter to `universe` tickers post-fetch (Finnhub returns global).
    Returns list of normalized event dicts (not yet persisted).
    """
    key = api_key or FINNHUB_API_KEY
    if not key:
        raise RuntimeError("FINNHUB_API_KEY not set; cannot fetch earnings calendar.")

    universe_set = {t.upper() for t in universe} if universe else None
    events = []
    for chunk_start, chunk_end in _chunk_dates(start, end, days=90):
        params = urllib.parse.urlencode({"from": chunk_start, "to": chunk_end, "token": key})
        url = f"{_FINNHUB_BASE}?{params}"
        try:
            payload = _http_get_json(url)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Finnhub HTTP {exc.code} for {chunk_start}..{chunk_end}: {exc.reason}") from exc
        for row in payload.get("earningsCalendar", []) or []:
            ticker = (row.get("symbol") or "").upper()
            if not ticker:
                continue
            if universe_set and ticker not in universe_set:
                continue
            actual = row.get("epsActual")
            est = row.get("epsEstimate")
            events.append({
                "ticker": ticker,
                "event_date": row.get("date"),
                "event_time": (row.get("hour") or "").lower() or None,
                "eps_actual": actual,
                "eps_estimate": est,
                "surprise_pct": _surprise_pct(actual, est),
                "revenue_actual": row.get("revenueActual"),
                "revenue_estimate": row.get("revenueEstimate"),
                "source": "finnhub",
            })
    return events


_VALUE_COLS = ("event_time", "eps_actual", "eps_estimate", "surprise_pct",
               "revenue_actual", "revenue_estimate")


def _values_match(a, b):
    """Two earnings rows are 'the same announcement' when every numeric/categorical
    value matches. Used to decide whether a fetch is a no-op or a restatement."""
    return all(a.get(k) == b.get(k) for k in _VALUE_COLS)


def cache_earnings_events(conn, events, *, as_of=None):
    """Insert events into earnings_events with point-in-time semantics.

    For each (ticker, event_date, source) key:
      - If no current row exists  → INSERT new current row.
      - If current row's values match → no-op (skip; don't bump fetched_at).
      - If values differ            → mark old row `superseded_at=now`, INSERT new current row.

    `as_of` overrides the timestamp stamped on new rows (used for tests and
    deterministic backfill); defaults to `utc_now_iso()`.
    Returns the count of rows inserted (i.e., new + restated, not no-ops).
    """
    if not events:
        return 0
    now = as_of or utc_now_iso()
    inserted = 0
    for e in events:
        cur = conn.execute(
            """SELECT * FROM earnings_events
               WHERE ticker=? AND event_date=? AND source=?
                 AND superseded_at IS NULL
               ORDER BY as_of_ts DESC LIMIT 1""",
            (e["ticker"], e["event_date"], e["source"]),
        ).fetchone()
        if cur is not None and _values_match(dict(cur), e):
            continue  # no change — preserve original as_of_ts
        # The new row needs a strictly greater as_of_ts than any existing
        # version; otherwise the (ticker, event_date, source, as_of_ts) PK
        # collides when caching twice within the same microsecond (e.g.,
        # tests, fast restate retries). String-bumping the suffix keeps
        # ordering correct and the value still ISO-parseable.
        new_as_of = now
        if cur is not None and new_as_of <= cur["as_of_ts"]:
            new_as_of = cur["as_of_ts"] + "_v"  # lexicographically greater
        if cur is not None:
            conn.execute(
                """UPDATE earnings_events SET superseded_at=?
                   WHERE ticker=? AND event_date=? AND source=? AND as_of_ts=?""",
                (new_as_of, e["ticker"], e["event_date"], e["source"], cur["as_of_ts"]),
            )
        conn.execute(
            """INSERT INTO earnings_events
               (ticker, event_date, event_time, eps_actual, eps_estimate, surprise_pct,
                revenue_actual, revenue_estimate, source, fetched_at, as_of_ts, superseded_at)
               VALUES (:ticker, :event_date, :event_time, :eps_actual, :eps_estimate, :surprise_pct,
                       :revenue_actual, :revenue_estimate, :source, :fetched_at, :as_of_ts, NULL)""",
            {**e, "fetched_at": now, "as_of_ts": new_as_of},
        )
        inserted += 1
    conn.commit()
    return inserted


def get_earnings_events(conn, *, start=None, end=None, tickers=None, source=None,
                        min_abs_surprise=None, as_of=None):
    """Read cached earnings events. Returns list of dicts ordered by date, ticker.

    `as_of`: ISO timestamp. When set, return the row that was current at that
    moment per (ticker, event_date, source). When None (default), return only
    rows currently un-superseded.
    """
    args = []
    if as_of is None:
        sql = "SELECT * FROM earnings_events WHERE superseded_at IS NULL"
    else:
        # The row that was current at `as_of`: most recent as_of_ts <= as_of
        # whose superseded_at is either NULL or > as_of.
        sql = ("SELECT * FROM earnings_events WHERE as_of_ts <= ? "
               "AND (superseded_at IS NULL OR superseded_at > ?)")
        args.extend([as_of, as_of])
    if start:
        sql += " AND event_date >= ?"; args.append(start)
    if end:
        sql += " AND event_date <= ?"; args.append(end)
    if tickers:
        placeholders = ",".join("?" * len(tickers))
        sql += f" AND ticker IN ({placeholders})"
        args.extend(t.upper() for t in tickers)
    if source:
        sql += " AND source = ?"; args.append(source)
    if min_abs_surprise is not None:
        sql += " AND surprise_pct IS NOT NULL AND ABS(surprise_pct) >= ?"
        args.append(float(min_abs_surprise))
    sql += " ORDER BY event_date, ticker"
    return [dict(r) for r in conn.execute(sql, args)]


def fetch_and_cache(conn, start, end, *, universe=None, api_key=None):
    """Convenience: fetch from Finnhub and cache. Returns count cached."""
    events = fetch_finnhub_earnings(start, end, universe=universe, api_key=api_key)
    return cache_earnings_events(conn, events)

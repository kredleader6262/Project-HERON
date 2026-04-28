"""Deterministic candidate seeders for backtests.

Two sources:
- `synthetic_pead_candidates`: reproducible fake stream from cached bars.
  Useful for smoke tests, but momentum-correlated by construction.
- `real_pead_candidates`: pulls cached `earnings_events` (e.g. Finnhub) and
  emits one candidate per real surprise. Network-free at replay time —
  only reads the cache.

Seeders MUST be pure functions of their inputs. No wall-clock, no RNG without
a seed, no network calls.
"""

import random
from collections import defaultdict


def synthetic_pead_candidates(bars, *, universe, surprise_threshold=5.0,
                              seed=0, frequency_days=20):
    """Generate fake earnings-surprise candidates from cached bars.

    Every `frequency_days` trading days per ticker, emit a synthetic candidate
    with surprise_pct drawn from a seeded RNG. Useful for smoke-testing PEAD
    without real earnings data.

    Returns sorted list of dicts: {date, ticker, surprise_pct,
    announced_hours_ago, conviction}.
    """
    rng = random.Random(seed)

    by_ticker = defaultdict(list)
    for b in bars:
        if b["ticker"] in universe:
            by_ticker[b["ticker"]].append((b["ts"][:10], b["close"]))
    for t in by_ticker:
        by_ticker[t].sort(key=lambda p: p[0])

    candidates = []
    for ticker in sorted(by_ticker):
        series = by_ticker[ticker]
        for i in range(frequency_days, len(series), frequency_days):
            date = series[i][0]
            prev = series[i - frequency_days][1]
            cur = series[i][1]
            momentum = (cur - prev) / prev * 100 if prev else 0
            noise = rng.uniform(-3, 3)
            surprise = round(momentum + noise, 2)
            if abs(surprise) < surprise_threshold:
                continue
            candidates.append({
                "date": date,
                "ticker": ticker,
                "surprise_pct": surprise,
                "announced_hours_ago": 12,
                "conviction": round(rng.uniform(0.5, 0.9), 2),
            })
    candidates.sort(key=lambda c: (c["date"], c["ticker"]))
    return candidates


def real_pead_candidates(conn, *, universe, start=None, end=None,
                         surprise_threshold=5.0, source=None, as_of=None):
    """Pull real earnings surprises from the `earnings_events` cache.

    `as_of`: when set, return the values that were known at that timestamp
    (PIT). Restated values after `as_of` are ignored. When None, returns
    current values.

    Conviction is a deterministic function of |surprise_pct|:
      conviction = clamp(0.5 + abs(surprise_pct)/40, 0.5, 0.95)
    so a 20% beat => 1.0 -> capped at 0.95; a 5% beat => 0.625.

    `announced_hours_ago` is set from event_time:
      bmo (before market open) -> 6 hours (announced at ~7am, signal at ~1pm)
      amc (after market close) -> 17 hours (announced previous PM, signal next AM)
      else                      -> 12 hours
    """
    from heron.data.earnings import get_earnings_events

    universe_set = {t.upper() for t in universe} if universe else None
    rows = get_earnings_events(
        conn,
        start=start,
        end=end,
        tickers=sorted(universe_set) if universe_set else None,
        source=source,
        min_abs_surprise=surprise_threshold,
        as_of=as_of,
    )
    candidates = []
    for r in rows:
        s = r.get("surprise_pct")
        if s is None:
            continue
        conviction = round(min(0.95, max(0.5, 0.5 + abs(s) / 40.0)), 2)
        et = (r.get("event_time") or "").lower()
        if et == "bmo":
            ann = 6
        elif et == "amc":
            ann = 17
        else:
            ann = 12
        candidates.append({
            "date": r["event_date"],
            "ticker": r["ticker"],
            "surprise_pct": float(s),
            "announced_hours_ago": ann,
            "conviction": conviction,
        })
    candidates.sort(key=lambda c: (c["date"], c["ticker"]))
    return candidates


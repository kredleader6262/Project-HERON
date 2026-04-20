"""Deterministic candidate seeders for backtests.

Real historical earnings-surprise data is out of scope for M13. These seeders
generate reproducible synthetic candidate streams from cached bars, which is
sufficient for validating strategy logic and demonstrating determinism.

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
        # Emit candidate every `frequency_days` bars
        for i in range(frequency_days, len(series), frequency_days):
            date = series[i][0]
            # Mix in price momentum as a pseudo-surprise signal
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

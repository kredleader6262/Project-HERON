"""Regime tagging for backtests.

A regime is a pre-computed market context label assigned to a date:
- vol_bucket: low/mid/high based on rolling 20-day std of SPY daily returns
- earnings_season: True for ±3 weeks around Jan/Apr/Jul/Oct (calendar quarters' US peak)

Pure functions of inputs. SPY bars are pulled from the cache by the caller.
"""

from __future__ import annotations

import math
from collections import defaultdict


def _rolling_std(values, window):
    """Yield rolling sample std dev. None for the first `window-1` items."""
    if window < 2:
        raise ValueError("window must be >= 2")
    out = [None] * len(values)
    for i in range(window - 1, len(values)):
        win = values[i - window + 1:i + 1]
        mean = sum(win) / len(win)
        var = sum((v - mean) ** 2 for v in win) / (len(win) - 1)
        out[i] = math.sqrt(var)
    return out


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def vol_buckets_from_spy(spy_bars, *, window=20):
    """Return {date: 'low'|'mid'|'high'} keyed on YYYY-MM-DD.

    Buckets cut on the 33rd / 67th percentile of rolling 20-day stdev of
    daily log-returns. Dates with insufficient history are omitted.
    """
    if not spy_bars:
        return {}
    bars = sorted(spy_bars, key=lambda b: b["ts"])
    closes = [b["close"] for b in bars]
    rets = [0.0]
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        rets.append(math.log(cur / prev) if prev > 0 and cur > 0 else 0.0)
    stds = _rolling_std(rets, window)

    valid = [s for s in stds if s is not None]
    if not valid:
        return {}
    vsorted = sorted(valid)
    p33 = _percentile(vsorted, 1.0 / 3.0)
    p67 = _percentile(vsorted, 2.0 / 3.0)

    out = {}
    for b, s in zip(bars, stds):
        if s is None:
            continue
        if s < p33:
            bucket = "low"
        elif s < p67:
            bucket = "mid"
        else:
            bucket = "high"
        out[b["ts"][:10]] = bucket
    return out


def is_earnings_season(date_str):
    """True if `date_str` (YYYY-MM-DD) falls in a US earnings-season peak window.

    Approximation: Jan/Apr/Jul/Oct 10–31 inclusive (3 weeks of peak reporting).
    """
    try:
        month = int(date_str[5:7])
        day = int(date_str[8:10])
    except (ValueError, IndexError):
        return False
    if month not in (1, 4, 7, 10):
        return False
    return 10 <= day <= 31


def tag_trades(trades, vol_buckets):
    """Annotate each trade dict with `regime` keys.

    Adds:
      vol_bucket: 'low'|'mid'|'high'|'unknown' (from entry_date)
      earnings_season: bool
    Returns a new list of dicts (does not mutate input).
    """
    out = []
    for t in trades:
        d = t.get("entry_date") or t.get("date") or ""
        d10 = d[:10] if isinstance(d, str) else ""
        tagged = dict(t)
        tagged["vol_bucket"] = vol_buckets.get(d10, "unknown")
        tagged["earnings_season"] = is_earnings_season(d10)
        out.append(tagged)
    return out


def regime_metrics(trades_tagged):
    """Aggregate per-regime metrics (n, win_rate, total_pnl, avg_pnl).

    Two breakdowns:
      'vol': by vol_bucket
      'earnings_season': True/False
    """
    def _bucket():
        return {"n": 0, "n_wins": 0, "total_pnl": 0.0}

    vol_b = defaultdict(_bucket)
    es_b = defaultdict(_bucket)
    for t in trades_tagged:
        pnl = t.get("net_pnl", 0.0) or 0.0
        won = pnl > 0
        for store, key in (
            (vol_b, t.get("vol_bucket", "unknown")),
            (es_b, "yes" if t.get("earnings_season") else "no"),
        ):
            b = store[key]
            b["n"] += 1
            b["n_wins"] += 1 if won else 0
            b["total_pnl"] += pnl

    def _finalize(d):
        out = {}
        for k, b in d.items():
            n = b["n"]
            out[k] = {
                "n_trades": n,
                "n_wins": b["n_wins"],
                "win_rate": round(b["n_wins"] / n, 4) if n else 0.0,
                "total_pnl": round(b["total_pnl"], 2),
                "avg_pnl": round(b["total_pnl"] / n, 2) if n else 0.0,
            }
        return out

    return {"vol": _finalize(vol_b), "earnings_season": _finalize(es_b)}

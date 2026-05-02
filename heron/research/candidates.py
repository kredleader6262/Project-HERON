"""Candidate generator — turns classified news + price data into trade candidates.

This module bridges Research → Strategy. It takes classified articles, enriches
them with price context, scores them, and writes candidates to the journal.

The local model provides classification. Scoring here is deterministic.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from heron.config import WATCHLIST
from heron.journal.candidates import create_candidate, list_candidates
from heron.journal.ops import log_cost
from heron.research.cost_guard import check_budget

log = logging.getLogger(__name__)

# Minimum relevance to even consider
MIN_RELEVANCE = 0.5
# Minimum absolute sentiment to generate a candidate (skip neutral)
MIN_SENTIMENT_ABS = 0.2
# How many hours back to look for duplicate candidates
DEDUP_WINDOW_HOURS = 24


def generate_candidates(conn, classifications, price_data=None, strategy_id=None):
    """Generate trade candidates from classified articles.

    conn: journal DB connection
    classifications: list of dicts from classifier (with relevance, sentiment, tickers)
    price_data: optional dict {ticker: {price, change_pct, volume_ratio}} for context
    strategy_id: which strategy to attribute candidates to

    Returns list of candidate IDs created.
    """
    if not strategy_id:
        strategy_id = "pead_v1"  # default active strategy

    budget = check_budget(conn)
    if not budget["research_allowed"]:
        log.warning(f"Research budget tripped ({budget['reason']}), skipping candidate generation")
        return []

    relevant = [c for c in classifications
                if c.get("relevant") and c.get("relevance_score", 0) >= MIN_RELEVANCE]

    if not relevant:
        log.info("No relevant articles found, no candidates generated")
        return []

    # Dedup: check for existing candidates on same ticker in recent window
    existing = list_candidates(conn, strategy_id=strategy_id)
    recent_tickers = set()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=DEDUP_WINDOW_HOURS)).isoformat()
    for c in existing:
        if c["created_at"] >= cutoff and c["disposition"] == "pending":
            recent_tickers.add(c["ticker"])

    candidate_ids = []
    for cls in relevant:
        tickers = cls.get("tickers", [])
        # Only generate candidates for tickers in our watchlist
        actionable_tickers = [t for t in tickers if t in WATCHLIST]
        if not actionable_tickers:
            continue

        for ticker in actionable_tickers:
            if ticker in recent_tickers:
                log.debug(f"Skipping {ticker} — pending candidate exists within {DEDUP_WINDOW_HOURS}h")
                continue

            score = _compute_score(cls, ticker, price_data)
            if score < MIN_RELEVANCE:
                continue

            side = "buy" if cls.get("sentiment_score", 0) > 0 else "sell"
            thesis = _build_thesis(cls, ticker, price_data)
            context = _build_context(cls, ticker, price_data)

            cid = create_candidate(
                conn, strategy_id, ticker,
                side=side,
                source="research_local",
                local_score=score,
                thesis=thesis,
                context_json=json.dumps(context),
            )
            candidate_ids.append(cid)
            recent_tickers.add(ticker)
            log.info(f"Candidate created: {ticker} ({side}) score={score:.2f} [{cls['category']}]")

    # Log the LLM cost for the classifications that produced candidates
    total_tokens_in = sum(c.get("tokens_in", 0) for c in relevant)
    total_tokens_out = sum(c.get("tokens_out", 0) for c in relevant)
    if total_tokens_in > 0:
        log_cost(conn, "qwen_local", total_tokens_in, total_tokens_out, 0.00,
                 strategy_id=strategy_id, task="classification")

    return candidate_ids


def _compute_score(classification, ticker, price_data=None):
    """Deterministic scoring from classification + price context.

    Weights:
    - relevance_score: 40%
    - abs(sentiment_score): 30%
    - category bonus: 20% (earnings > macro > other)
    - price context bonus: 10% (if available)
    """
    rel = classification.get("relevance_score", 0)
    sent = abs(classification.get("sentiment_score", 0))
    cat = classification.get("category", "other")

    # Skip near-neutral sentiment
    if sent < MIN_SENTIMENT_ABS:
        return 0.0

    category_bonus = {
        "earnings": 1.0,
        "insider": 0.8,
        "macro": 0.6,
        "analyst": 0.5,
        "sector": 0.4,
        "other": 0.2,
    }.get(cat, 0.2)

    price_bonus = 0.0
    if price_data and ticker in price_data:
        pd = price_data[ticker]
        # Bonus if price already moving in sentiment direction
        change = pd.get("change_pct", 0)
        if (change > 0 and classification.get("sentiment_score", 0) > 0) or \
           (change < 0 and classification.get("sentiment_score", 0) < 0):
            price_bonus = min(abs(change) / 5.0, 1.0)  # cap at 5% move
        # Volume spike bonus
        vol_ratio = pd.get("volume_ratio", 1.0)
        if vol_ratio > 1.5:
            price_bonus = min(price_bonus + 0.3, 1.0)

    score = (rel * 0.4) + (sent * 0.3) + (category_bonus * 0.2) + (price_bonus * 0.1)
    return round(score, 3)


def _build_thesis(classification, ticker, price_data=None):
    """Build a short thesis string from classification data."""
    parts = [
        f"{ticker}: {classification.get('rationale', 'no rationale')}",
        f"Sentiment: {classification.get('sentiment', '?')} ({classification.get('sentiment_score', 0):+.2f})",
        f"Category: {classification.get('category', '?')}",
    ]
    if price_data and ticker in price_data:
        pd = price_data[ticker]
        parts.append(f"Price: ${pd.get('price', 0):.2f} ({pd.get('change_pct', 0):+.1f}%)")
    return " | ".join(parts)


def _build_context(classification, ticker, price_data=None):
    """Build context dict stored as JSON on the candidate."""
    ctx = {
        "article_id": classification.get("article_id"),
        "relevance_score": classification.get("relevance_score"),
        "sentiment": classification.get("sentiment"),
        "sentiment_score": classification.get("sentiment_score"),
        "category": classification.get("category"),
        "source": "local_classifier",
    }
    if price_data and ticker in price_data:
        ctx["price_context"] = price_data[ticker]
    return ctx

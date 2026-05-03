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
from heron.journal.signals import create_or_get_signal, link_signal_candidate
from heron.research.cost_guard import check_budget

log = logging.getLogger(__name__)

# Minimum relevance to even consider
MIN_RELEVANCE = 0.5
# Minimum absolute sentiment to generate a candidate (skip neutral)
MIN_SENTIMENT_ABS = 0.2
# How many hours back to look for duplicate candidates
DEDUP_WINDOW_HOURS = 24


def generate_candidates(conn, classifications, price_data=None, strategy_id=None, strategy_ids=None):
    """Generate trade candidates from classified articles.

    conn: journal DB connection
    classifications: list of dicts from classifier (with relevance, sentiment, tickers)
    price_data: optional dict {ticker: {price, change_pct, volume_ratio}} for context
    strategy_id: which strategy to attribute candidates to
    strategy_ids: optional iterable of strategies sharing the same upstream Signals

    Returns list of candidate IDs created.
    """
    strategies = _strategy_ids(strategy_id, strategy_ids)

    budget = check_budget(conn)
    if not budget["research_allowed"]:
        log.warning(f"Research budget tripped ({budget['reason']}), skipping candidate generation")
        return []

    relevant = [c for c in classifications
                if c.get("relevant") and c.get("relevance_score", 0) >= MIN_RELEVANCE]

    if not relevant:
        log.info("No relevant articles found, no candidates generated")
        return []

    # Dedup: check for existing candidates on same ticker in recent window.
    recent_tickers = {sid: set() for sid in strategies}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=DEDUP_WINDOW_HOURS)).isoformat()
    for sid in strategies:
        for c in list_candidates(conn, strategy_id=sid):
            if c["created_at"] >= cutoff and c["disposition"] == "pending":
                recent_tickers[sid].add(c["ticker"])

    candidate_ids = []
    signal_ids = {}
    produced_strategies = set()
    strategy_campaigns = {sid: _campaign_for_strategy(conn, sid) for sid in strategies}
    for cls in relevant:
        tickers = cls.get("tickers", [])
        # Only generate candidates for tickers in our watchlist
        actionable_tickers = [t for t in tickers if t in WATCHLIST]
        if not actionable_tickers:
            continue

        for ticker in actionable_tickers:
            targets = [sid for sid in strategies if ticker not in recent_tickers[sid]]
            if not targets:
                log.debug(f"Skipping {ticker} - pending candidate exists within {DEDUP_WINDOW_HOURS}h")
                continue

            score = _compute_score(cls, ticker, price_data)
            if score < MIN_RELEVANCE:
                continue

            side = "buy" if cls.get("sentiment_score", 0) > 0 else "sell"
            thesis = _build_thesis(cls, ticker, price_data)
            context = _build_context(cls, ticker, price_data)
            signal_type = cls.get("category") or "news"
            bias = _bias_from_classification(cls)

            for campaign_id in {strategy_campaigns[sid] for sid in targets if strategy_campaigns[sid]}:
                key = _signal_key(campaign_id, cls, ticker, signal_type, bias)
                if key in signal_ids:
                    continue
                try:
                    signal_ids[key] = create_or_get_signal(
                        conn,
                        campaign_id=campaign_id,
                        source="research_local",
                        finding_ref_json=_finding_ref(cls, ticker),
                        producing_agent="local_classifier",
                        producing_model="qwen_local",
                        ticker=ticker,
                        signal_type=signal_type,
                        bias=bias,
                        thesis=thesis,
                        confidence=score,
                        classification=cls.get("sentiment"),
                        evidence_json=context,
                    )
                except Exception as e:
                    signal_ids[key] = None
                    log.warning(f"Signal creation failed for {ticker}: {e}")

            for sid in targets:
                cid = create_candidate(
                    conn, sid, ticker,
                    side=side,
                    source="research_local",
                    local_score=score,
                    thesis=thesis,
                    context_json=json.dumps(context),
                )
                candidate_ids.append(cid)
                produced_strategies.add(sid)
                recent_tickers[sid].add(ticker)
                signal_id = signal_ids.get(
                    _signal_key(strategy_campaigns[sid], cls, ticker, signal_type, bias)
                )
                if signal_id:
                    try:
                        link_signal_candidate(conn, signal_id, cid, sid, bridge_source="research")
                    except Exception as e:
                        log.warning(f"Signal link failed for candidate {cid}: {e}")
                log.info(f"Candidate created: {ticker} ({side}) score={score:.2f} [{cls['category']}]")

    # Log the LLM cost for the classifications that produced candidates
    total_tokens_in = sum(c.get("tokens_in", 0) for c in relevant)
    total_tokens_out = sum(c.get("tokens_out", 0) for c in relevant)
    if total_tokens_in > 0:
        if len(produced_strategies) <= 1:
            sid = next(iter(produced_strategies), strategies[0])
            log_cost(conn, "qwen_local", total_tokens_in, total_tokens_out, 0.00,
                     strategy_id=sid, task="classification")
        else:
            log_cost(conn, "qwen_local", total_tokens_in, total_tokens_out, 0.00,
                     strategy_id=None, task="classification")

    return candidate_ids


def _strategy_ids(strategy_id=None, strategy_ids=None):
    if strategy_ids is not None:
        ids = list(strategy_ids)
    elif isinstance(strategy_id, (list, tuple, set)):
        ids = list(strategy_id)
    else:
        ids = [strategy_id or "pead_v1"]
    return [sid for sid in ids if sid]


def _campaign_for_strategy(conn, strategy_id):
    row = conn.execute("SELECT campaign_id FROM strategies WHERE id=?", (strategy_id,)).fetchone()
    return row["campaign_id"] if row else None


def _bias_from_classification(classification):
    sentiment_score = classification.get("sentiment_score", 0) or 0
    sentiment = (classification.get("sentiment") or "").lower()
    category = (classification.get("category") or "").lower()
    if sentiment == "risk-off" or (category == "macro" and sentiment_score < 0):
        return "risk-off"
    if sentiment_score > 0:
        return "long_bias"
    if sentiment_score < 0:
        return "short_bias"
    return "informational"


def _finding_ref(classification, ticker):
    return {
        "article_id": classification.get("article_id"),
        "ticker": ticker,
        "category": classification.get("category"),
    }


def _signal_key(campaign_id, classification, ticker, signal_type, bias):
    return (
        campaign_id,
        json.dumps(_finding_ref(classification, ticker), sort_keys=True),
        ticker,
        signal_type,
        bias,
    )


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

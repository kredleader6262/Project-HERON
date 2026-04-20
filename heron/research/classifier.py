"""News classifier — relevance + sentiment via local LLM.

Input: sanitized news articles from the Data layer.
Output: per-article classification dict with relevance, sentiment, tickers, rationale.

The local model classifies only. It never sizes, risks, or writes final theses.
All input text is pre-sanitized by the Data layer (adversarial input defense).
"""

import json
import logging

from heron.research import generate
from heron.research.progress import Spinner
from heron.data.sanitize import sanitize_headline, sanitize

log = logging.getLogger(__name__)

_CLASSIFY_PROMPT = """You are a financial news classifier. Analyze this headline and summary for a systematic trading system.

HEADLINE: {headline}
SUMMARY: {summary}

Respond with JSON only:
{{
  "relevant": true/false,
  "relevance_score": 0.0-1.0,
  "sentiment": "positive" | "negative" | "neutral",
  "sentiment_score": -1.0 to 1.0,
  "tickers": ["AAPL", ...],
  "category": "earnings" | "macro" | "insider" | "analyst" | "sector" | "other",
  "rationale": "one sentence why"
}}

Rules:
- relevant=true only if it could move a specific stock or sector >0.5% within 48 hours
- sentiment_score: -1.0=very bearish, 0=neutral, +1.0=very bullish
- tickers: only include if directly mentioned or clearly implied
- Be concise in rationale"""

_BATCH_PROMPT = """You are a financial news classifier. Classify each article for a systematic trading system.

ARTICLES:
{articles_block}

For EACH article, respond with a JSON array. Each element:
{{
  "id": "article id",
  "relevant": true/false,
  "relevance_score": 0.0-1.0,
  "sentiment": "positive" | "negative" | "neutral",
  "sentiment_score": -1.0 to 1.0,
  "tickers": ["AAPL", ...],
  "category": "earnings" | "macro" | "insider" | "analyst" | "sector" | "other",
  "rationale": "one sentence"
}}

Respond with JSON: {{"results": [...]}}"""


def classify_article(article):
    """Classify a single news article. Returns classification dict or None on failure."""
    headline = sanitize_headline(article.get("headline", "") if isinstance(article, dict)
                                  else article["headline"])
    summary = sanitize(article.get("summary", "") if isinstance(article, dict)
                       else article["summary"])

    # Truncate to stay within reasonable token budget
    summary = summary[:500]

    prompt = _CLASSIFY_PROMPT.format(headline=headline, summary=summary)
    result = generate(prompt, json_mode=True, temperature=0.1)

    if not result.get("parsed"):
        log.warning(f"Classification failed for: {headline[:60]}")
        return None

    parsed = result["parsed"]
    # Normalize fields
    classification = {
        "article_id": article.get("id", "") if isinstance(article, dict) else article["id"],
        "relevant": bool(parsed.get("relevant", False)),
        "relevance_score": _clamp(float(parsed.get("relevance_score", 0)), 0, 1),
        "sentiment": parsed.get("sentiment", "neutral"),
        "sentiment_score": _clamp(float(parsed.get("sentiment_score", 0)), -1, 1),
        "tickers": parsed.get("tickers", []),
        "category": parsed.get("category", "other"),
        "rationale": str(parsed.get("rationale", ""))[:200],
        "tokens_in": result.get("tokens_in", 0),
        "tokens_out": result.get("tokens_out", 0),
    }
    return classification


def classify_batch(articles, max_per_batch=10):
    """Classify multiple articles in a single LLM call. More token-efficient.

    Falls back to individual classification if batch parsing fails.
    Returns list of classification dicts.
    """
    if not articles:
        return []

    # Chunk into batches
    results = []
    total_batches = (len(articles) + max_per_batch - 1) // max_per_batch
    for i in range(0, len(articles), max_per_batch):
        batch = articles[i:i + max_per_batch]
        batch_num = i // max_per_batch + 1
        label = f"Classifying batch {batch_num}/{total_batches} ({len(batch)} articles)"
        with Spinner(label) as sp:
            batch_results = _classify_one_batch(
                batch,
                on_progress=lambda p: sp.update(f"{p['tokens_out']} tokens"),
            )
        results.extend(batch_results)
    return results


def _classify_one_batch(articles, on_progress=None):
    """Classify a single batch via one LLM call."""
    lines = []
    for a in articles:
        aid = a.get("id", "") if isinstance(a, dict) else a["id"]
        headline = sanitize_headline(a.get("headline", "") if isinstance(a, dict) else a["headline"])
        summary = sanitize(a.get("summary", "") if isinstance(a, dict) else a["summary"])[:300]
        lines.append(f"[{aid}] {headline} — {summary}")

    articles_block = "\n".join(lines)
    prompt = _BATCH_PROMPT.format(articles_block=articles_block)
    result = generate(prompt, json_mode=True, temperature=0.1, on_progress=on_progress)

    parsed = result.get("parsed")
    if not parsed or "results" not in parsed:
        # Fallback: classify individually
        log.warning("Batch classification failed, falling back to individual")
        return [c for c in (classify_article(a) for a in articles) if c]

    classifications = []
    results_list = parsed["results"]
    article_map = {(a.get("id", "") if isinstance(a, dict) else a["id"]): a for a in articles}

    for item in results_list:
        aid = item.get("id", "")
        classifications.append({
            "article_id": aid,
            "relevant": bool(item.get("relevant", False)),
            "relevance_score": _clamp(float(item.get("relevance_score", 0)), 0, 1),
            "sentiment": item.get("sentiment", "neutral"),
            "sentiment_score": _clamp(float(item.get("sentiment_score", 0)), -1, 1),
            "tickers": item.get("tickers", []),
            "category": item.get("category", "other"),
            "rationale": str(item.get("rationale", ""))[:200],
            "tokens_in": result.get("tokens_in", 0) // max(len(results_list), 1),
            "tokens_out": result.get("tokens_out", 0) // max(len(results_list), 1),
        })

    return classifications


def filter_relevant(classifications, min_score=0.5):
    """Filter classifications to only relevant articles above threshold."""
    return [c for c in classifications if c["relevant"] and c["relevance_score"] >= min_score]


def _clamp(val, lo, hi):
    return max(lo, min(hi, val))

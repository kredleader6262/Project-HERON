"""Research orchestrator — runs the pre-market / midday / EOD research passes.

This is the top-level coordinator for the Research layer. It:
1. Fetches recent news via Data layer
2. Classifies via local LLM (Ollama/Qwen)
3. Enriches with price context
4. Generates candidates → Journal
5. Tracks costs

Never in the execution hot path. Called by scheduler or CLI.
"""

import logging
from datetime import datetime, timedelta, timezone

from heron.config import WATCHLIST, ANTHROPIC_API_KEY
from heron.data import DataFeed
from heron.journal import get_journal_conn, init_journal
from heron.journal.ops import log_event, get_monthly_cost
from heron.research.classifier import classify_batch, filter_relevant
from heron.research.candidates import generate_candidates

log = logging.getLogger(__name__)


class ResearchPass:
    """Runs one research pass: fetch → classify → generate candidates."""

    def __init__(self, conn=None, feed=None):
        self._own_conn = conn is None
        self._own_feed = feed is None
        self.conn = conn or get_journal_conn()
        self.feed = feed or DataFeed()
        if self._own_conn:
            init_journal(self.conn)

    def close(self):
        if self._own_feed:
            self.feed.close()
        if self._own_conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def run(self, strategy_id="pead_v1", pass_type="premarket",
            lookback_hours=16, news_limit=50, escalate=True):
        """Execute a full research pass.

        pass_type: "premarket" | "midday" | "eod" (for logging)
        lookback_hours: how far back to fetch news
        news_limit: max articles to fetch

        Returns dict with pass stats.
        """
        log.info(f"Research pass: {pass_type} for {strategy_id}")

        # Cost gate
        from heron.research.cost_guard import check_budget, notify_if_threshold
        budget = check_budget(self.conn)
        if not budget["research_allowed"]:
            msg = f"Research halted: {budget['reason']}"
            log.warning(msg)
            log_event(self.conn, "research_cost_halt", msg,
                      severity="warn", source="research")
            notify_if_threshold(self.conn)
            return {"status": "cost_halted", "month_cost": budget["mtd"],
                    "reason": budget["reason"]}
        if budget["status"] == "warning":
            notify_if_threshold(self.conn)

        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=lookback_hours)).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")

        # 1. Fetch news
        try:
            articles = self.feed.fetch_watchlist_news(start=start, end=end, limit=news_limit)
        except Exception as e:
            log.error(f"News fetch failed: {e}")
            log_event(self.conn, "research_fetch_error", str(e),
                      severity="error", source="research")
            return {"status": "fetch_error", "error": str(e)}

        if not articles:
            log.info("No new articles found")
            return {"status": "no_articles", "articles": 0}

        # Convert Row objects to dicts for classifier
        article_dicts = [dict(a) for a in articles]
        log.info(f"Fetched {len(article_dicts)} articles")

        # 2. Classify
        try:
            classifications = classify_batch(article_dicts)
        except Exception as e:
            log.error(f"Classification failed: {e}")
            log_event(self.conn, "research_classify_error", str(e),
                      severity="error", source="research")
            return {"status": "classify_error", "error": str(e), "articles": len(article_dicts)}

        relevant = filter_relevant(classifications)
        log.info(f"Classified {len(classifications)} articles, {len(relevant)} relevant")

        # 3. Price context
        price_data = self._get_price_context()

        # 4. Generate candidates
        try:
            candidate_ids = generate_candidates(
                self.conn, classifications,
                price_data=price_data,
                strategy_id=strategy_id,
            )
        except Exception as e:
            log.error(f"Candidate generation failed: {e}")
            log_event(self.conn, "research_candidate_error", str(e),
                      severity="error", source="research")
            return {"status": "candidate_error", "error": str(e),
                    "articles": len(article_dicts), "relevant": len(relevant)}

        # Log pass event
        log_event(self.conn, f"research_pass_{pass_type}",
                  f"{len(article_dicts)} articles, {len(relevant)} relevant, "
                  f"{len(candidate_ids)} candidates",
                  severity="info", source="research")

        result = {
            "status": "ok",
            "pass_type": pass_type,
            "articles": len(article_dicts),
            "classified": len(classifications),
            "relevant": len(relevant),
            "candidates": len(candidate_ids),
            "candidate_ids": candidate_ids,
            "month_cost": get_monthly_cost(self.conn),
        }

        # 5. Escalate to Claude if API key configured and candidates exist
        if escalate and candidate_ids and ANTHROPIC_API_KEY:
            try:
                from heron.research.escalation import escalate_candidates
                esc = escalate_candidates(self.conn, candidate_ids, strategy_id=strategy_id)
                result["escalation"] = esc
                log.info(f"Escalation: {esc.get('escalated', 0)} theses, "
                         f"{esc.get('sampled', 0)} audits")
            except Exception as e:
                log.error(f"Escalation failed: {e}")
                result["escalation"] = {"status": "error", "error": str(e)}

        return result

    def _get_price_context(self):
        """Get current price context for watchlist tickers. Best-effort."""
        price_data = {}
        for ticker in WATCHLIST:
            try:
                q = self.feed.get_quote(ticker)
                mid = (q["bid"] + q["ask"]) / 2 if q["bid"] and q["ask"] else 0
                price_data[ticker] = {
                    "price": mid,
                    "bid": q["bid"],
                    "ask": q["ask"],
                    "age_seconds": q["age_seconds"],
                }
            except Exception as e:
                # Quotes fail outside market hours/weekends — not fatal for research
                log.debug(f"Quote fetch failed for {ticker}: {e}")
        return price_data


def run_premarket(strategy_id="pead_v1"):
    """Convenience: run pre-market pass (overnight news, 16h lookback)."""
    with ResearchPass() as rp:
        return rp.run(strategy_id, pass_type="premarket", lookback_hours=16)


def run_midday(strategy_id="pead_v1"):
    """Convenience: run midday pass (6h lookback, breaking news)."""
    with ResearchPass() as rp:
        return rp.run(strategy_id, pass_type="midday", lookback_hours=6)

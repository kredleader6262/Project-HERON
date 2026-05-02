"""Tests for heron.research — Ollama client, classifier, candidate generator, orchestrator."""

import json
from unittest.mock import patch, MagicMock

import pytest


# ── Fixtures ──────────────────────────────────────

@pytest.fixture
def journal_conn(research_pead_v1_conn):
    return research_pead_v1_conn


def _mock_ollama_response(parsed_json, tokens_in=100, tokens_out=50):
    """Create a mock httpx response for Ollama API."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "response": json.dumps(parsed_json),
        "prompt_eval_count": tokens_in,
        "eval_count": tokens_out,
    }
    resp.raise_for_status = MagicMock()
    return resp


# ── Ollama Client ────────────────────────────────

class TestOllamaClient:

    @patch("httpx.post")
    def test_generate_json_mode(self, mock_post):
        from heron.research import generate
        mock_post.return_value = _mock_ollama_response({"answer": 42})

        result = generate("test prompt", json_mode=True, stream=False)

        assert result["parsed"] == {"answer": 42}
        assert result["tokens_in"] == 100
        assert result["tokens_out"] == 50
        assert result["model"]  # should have a model name
        mock_post.assert_called_once()

    @patch("httpx.post")
    def test_generate_text_mode(self, mock_post):
        from heron.research import generate
        resp = MagicMock()
        resp.json.return_value = {
            "response": "Hello world",
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        result = generate("test", json_mode=False, stream=False)

        assert result["text"] == "Hello world"
        assert "parsed" not in result

    @patch("httpx.post")
    def test_generate_invalid_json(self, mock_post):
        from heron.research import generate
        resp = MagicMock()
        resp.json.return_value = {
            "response": "not valid json {{{",
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        result = generate("test", json_mode=True, stream=False)
        assert result["parsed"] is None

    @patch("httpx.get")
    def test_is_available_true(self, mock_get):
        from heron.research import is_available
        resp = MagicMock()
        resp.json.return_value = {"models": [{"name": "qwen2.5:7b-instruct-q4_K_M"}]}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        assert is_available() is True

    @patch("httpx.get")
    def test_is_available_false(self, mock_get):
        import httpx
        from heron.research import is_available
        mock_get.side_effect = httpx.ConnectError("Connection refused")
        assert is_available() is False


# ── Classifier ──────────────────────────────────

class TestClassifier:

    @patch("heron.research.classifier.generate")
    def test_classify_article(self, mock_gen):
        from heron.research.classifier import classify_article
        mock_gen.return_value = {
            "parsed": {
                "relevant": True,
                "relevance_score": 0.85,
                "sentiment": "positive",
                "sentiment_score": 0.7,
                "tickers": ["AAPL"],
                "category": "earnings",
                "rationale": "AAPL beat EPS by 12%",
            },
            "tokens_in": 200,
            "tokens_out": 80,
        }

        article = {
            "id": "test:1",
            "headline": "Apple beats Q3 earnings",
            "summary": "Apple reported strong Q3 results.",
        }
        result = classify_article(article)

        assert result["relevant"] is True
        assert result["relevance_score"] == 0.85
        assert result["sentiment"] == "positive"
        assert result["tickers"] == ["AAPL"]
        assert result["article_id"] == "test:1"

    @patch("heron.research.classifier.generate")
    def test_classify_article_failure(self, mock_gen):
        from heron.research.classifier import classify_article
        mock_gen.return_value = {"parsed": None, "tokens_in": 0, "tokens_out": 0}

        result = classify_article({"id": "x", "headline": "test", "summary": ""})
        assert result is None

    @patch("heron.research.classifier.generate")
    def test_classify_batch(self, mock_gen):
        from heron.research.classifier import classify_batch
        mock_gen.return_value = {
            "parsed": {
                "results": [
                    {"id": "a1", "relevant": True, "relevance_score": 0.9,
                     "sentiment": "positive", "sentiment_score": 0.8,
                     "tickers": ["AAPL"], "category": "earnings", "rationale": "beat"},
                    {"id": "a2", "relevant": False, "relevance_score": 0.2,
                     "sentiment": "neutral", "sentiment_score": 0.0,
                     "tickers": [], "category": "other", "rationale": "routine"},
                ]
            },
            "tokens_in": 500,
            "tokens_out": 200,
        }

        articles = [
            {"id": "a1", "headline": "AAPL beats", "summary": "great quarter"},
            {"id": "a2", "headline": "Market flat", "summary": "nothing happened"},
        ]
        results = classify_batch(articles)
        assert len(results) == 2
        assert results[0]["relevant"] is True
        assert results[1]["relevant"] is False

    def test_filter_relevant(self):
        from heron.research.classifier import filter_relevant
        cls = [
            {"relevant": True, "relevance_score": 0.9},
            {"relevant": True, "relevance_score": 0.3},
            {"relevant": False, "relevance_score": 0.8},
        ]
        filtered = filter_relevant(cls, min_score=0.5)
        assert len(filtered) == 1
        assert filtered[0]["relevance_score"] == 0.9

    def test_clamp(self):
        from heron.research.classifier import _clamp
        assert _clamp(1.5, 0, 1) == 1.0
        assert _clamp(-0.5, 0, 1) == 0.0
        assert _clamp(0.5, 0, 1) == 0.5


# ── Candidate Generator ─────────────────────────

class TestCandidateGenerator:

    def test_generate_candidates_basic(self, journal_conn):
        from heron.research.candidates import generate_candidates

        classifications = [
            {
                "article_id": "test:1",
                "relevant": True,
                "relevance_score": 0.9,
                "sentiment": "positive",
                "sentiment_score": 0.7,
                "tickers": ["AAPL"],
                "category": "earnings",
                "rationale": "AAPL beat EPS",
                "tokens_in": 200,
                "tokens_out": 80,
            },
        ]
        ids = generate_candidates(journal_conn, classifications, strategy_id="pead_v1")
        assert len(ids) == 1

        # Verify in DB
        row = journal_conn.execute("SELECT * FROM candidates WHERE id=?", (ids[0],)).fetchone()
        assert row["ticker"] == "AAPL"
        assert row["source"] == "research_local"
        assert row["disposition"] == "pending"

    def test_generate_candidates_filters_non_watchlist(self, journal_conn):
        from heron.research.candidates import generate_candidates

        classifications = [
            {
                "article_id": "test:2",
                "relevant": True,
                "relevance_score": 0.9,
                "sentiment": "positive",
                "sentiment_score": 0.7,
                "tickers": ["TSLA"],  # Not in watchlist
                "category": "earnings",
                "rationale": "TSLA beat EPS",
                "tokens_in": 100,
                "tokens_out": 50,
            },
        ]
        ids = generate_candidates(journal_conn, classifications, strategy_id="pead_v1")
        assert len(ids) == 0

    def test_generate_candidates_dedup(self, journal_conn):
        from heron.research.candidates import generate_candidates

        cls = [
            {
                "article_id": "test:3",
                "relevant": True,
                "relevance_score": 0.9,
                "sentiment": "positive",
                "sentiment_score": 0.7,
                "tickers": ["AAPL"],
                "category": "earnings",
                "rationale": "beat",
                "tokens_in": 100,
                "tokens_out": 50,
            },
        ]
        # First call creates candidate
        ids1 = generate_candidates(journal_conn, cls, strategy_id="pead_v1")
        assert len(ids1) == 1

        # Second call deduplicates
        ids2 = generate_candidates(journal_conn, cls, strategy_id="pead_v1")
        assert len(ids2) == 0

    def test_generate_candidates_skips_neutral(self, journal_conn):
        from heron.research.candidates import generate_candidates

        cls = [
            {
                "article_id": "test:4",
                "relevant": True,
                "relevance_score": 0.8,
                "sentiment": "neutral",
                "sentiment_score": 0.05,  # Below MIN_SENTIMENT_ABS
                "tickers": ["AAPL"],
                "category": "other",
                "rationale": "routine",
                "tokens_in": 100,
                "tokens_out": 50,
            },
        ]
        ids = generate_candidates(journal_conn, cls, strategy_id="pead_v1")
        assert len(ids) == 0

    def test_generate_candidates_cost_gate(self, journal_conn):
        from heron.research.candidates import generate_candidates
        from heron.journal.ops import log_cost

        # Blow past the cost ceiling
        log_cost(journal_conn, "claude_sonnet", 100000, 50000, 50.00, task="test")

        cls = [
            {
                "article_id": "test:5",
                "relevant": True,
                "relevance_score": 0.9,
                "sentiment": "positive",
                "sentiment_score": 0.8,
                "tickers": ["AAPL"],
                "category": "earnings",
                "rationale": "beat",
                "tokens_in": 100,
                "tokens_out": 50,
            },
        ]
        ids = generate_candidates(journal_conn, cls, strategy_id="pead_v1")
        assert len(ids) == 0  # blocked by cost ceiling

    @patch("heron.research.candidates.check_budget")
    def test_generate_candidates_uses_central_cost_guard(self, mock_budget, journal_conn):
        from heron.research.candidates import generate_candidates

        mock_budget.return_value = {
            "research_allowed": False,
            "reason": "projected $60.00 > ceiling $45.00",
            "mtd": 20.0,
        }
        cls = [{
            "article_id": "test:6",
            "relevant": True,
            "relevance_score": 0.9,
            "sentiment": "positive",
            "sentiment_score": 0.8,
            "tickers": ["AAPL"],
            "category": "earnings",
            "rationale": "beat",
        }]
        assert generate_candidates(journal_conn, cls, strategy_id="pead_v1") == []

    def test_score_computation(self):
        from heron.research.candidates import _compute_score

        cls = {
            "relevance_score": 0.9,
            "sentiment_score": 0.8,
            "category": "earnings",
        }
        score = _compute_score(cls, "AAPL")
        # 0.9*0.4 + 0.8*0.3 + 1.0*0.2 + 0.0*0.1 = 0.36 + 0.24 + 0.20 = 0.80
        assert 0.79 <= score <= 0.81

    def test_score_with_price_context(self):
        from heron.research.candidates import _compute_score

        cls = {
            "relevance_score": 0.9,
            "sentiment_score": 0.8,
            "category": "earnings",
        }
        price_data = {"AAPL": {"change_pct": 3.0, "volume_ratio": 2.0}}
        score = _compute_score(cls, "AAPL", price_data)
        # Should be higher than without price context
        assert score > 0.80


# ── Orchestrator ─────────────────────────────────

class TestOrchestrator:

    @patch("heron.research.orchestrator.classify_batch")
    @patch("heron.research.orchestrator.filter_relevant")
    def test_run_pass_no_articles(self, mock_filter, mock_classify, journal_conn, tmp_path):
        from heron.research.orchestrator import ResearchPass

        # Create a mock feed that returns no articles
        mock_feed = MagicMock()
        mock_feed.fetch_watchlist_news.return_value = []

        rp = ResearchPass(conn=journal_conn, feed=mock_feed)
        result = rp.run(strategy_id="pead_v1")

        assert result["status"] == "no_articles"
        mock_classify.assert_not_called()

    @patch("heron.research.orchestrator.generate_candidates")
    @patch("heron.research.orchestrator.classify_batch")
    def test_run_pass_with_articles(self, mock_classify, mock_gen, journal_conn):
        from heron.research.orchestrator import ResearchPass

        mock_feed = MagicMock()
        mock_feed.fetch_watchlist_news.return_value = [
            {"id": "a1", "headline": "AAPL beats", "summary": "great", "source": "test",
             "tickers": '["AAPL"]', "credibility_weight": 0.8, "published_at": "",
             "body_sanitized": "", "fetched_at": ""},
        ]
        mock_feed.get_quote.side_effect = Exception("market closed")

        mock_classify.return_value = [
            {"article_id": "a1", "relevant": True, "relevance_score": 0.9,
             "sentiment": "positive", "sentiment_score": 0.8, "tickers": ["AAPL"],
             "category": "earnings", "rationale": "beat", "tokens_in": 200, "tokens_out": 80},
        ]
        mock_gen.return_value = [1]

        rp = ResearchPass(conn=journal_conn, feed=mock_feed)
        result = rp.run(strategy_id="pead_v1")

        assert result["status"] == "ok"
        assert result["articles"] == 1
        mock_gen.assert_called_once()

    def test_cost_gate_halts_research(self, journal_conn):
        from heron.research.orchestrator import ResearchPass
        from heron.journal.ops import log_cost

        log_cost(journal_conn, "claude_sonnet", 100000, 50000, 50.00, task="test")

        mock_feed = MagicMock()
        rp = ResearchPass(conn=journal_conn, feed=mock_feed)
        result = rp.run(strategy_id="pead_v1")

        assert result["status"] == "cost_halted"
        mock_feed.fetch_watchlist_news.assert_not_called()

    def test_context_manager(self, journal_conn):
        from heron.research.orchestrator import ResearchPass

        mock_feed = MagicMock()
        with ResearchPass(conn=journal_conn, feed=mock_feed) as rp:
            assert rp.conn is journal_conn

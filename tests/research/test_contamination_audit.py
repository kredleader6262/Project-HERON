"""Tests for contamination static audit (A4)."""

import os
import textwrap

from heron.research.audit import contamination_audit


def test_pead_strategy_clean():
    """The shipped PEAD strategy must pass — it doesn't read PIT data directly."""
    findings = contamination_audit(os.path.join("heron", "strategy", "pead.py"))
    assert findings == [], f"pead.py should be clean, got: {findings}"


def test_strategy_dir_clean():
    """Whole strategy/ directory should pass."""
    findings = contamination_audit(os.path.join("heron", "strategy"))
    assert findings == [], f"strategy/ should be clean, got: {findings}"


def test_seeders_pass(tmp_path):
    """Seeders pass as_of through — should be clean."""
    findings = contamination_audit(os.path.join("heron", "backtest", "seeders.py"))
    bad = [f for f in findings if f["rule"].startswith("missing_as_of")]
    assert bad == [], f"seeders.py should be clean, got: {bad}"


def test_unguarded_get_earnings_events_flagged(tmp_path):
    """Calling get_earnings_events without as_of= must flag."""
    bad = tmp_path / "bad_strategy.py"
    bad.write_text(textwrap.dedent("""
        from heron.data.earnings import get_earnings_events

        def screen(conn):
            rows = get_earnings_events(conn, start="2024-01-01", end="2024-01-31")
            return rows
    """))
    findings = contamination_audit(str(bad))
    assert len(findings) == 1
    assert findings[0]["rule"] == "missing_as_of:get_earnings_events"
    assert findings[0]["severity"] == "error"


def test_guarded_call_passes(tmp_path):
    """Same call with as_of= must NOT flag."""
    good = tmp_path / "good_strategy.py"
    good.write_text(textwrap.dedent("""
        from heron.data.earnings import get_earnings_events

        def screen(conn, as_of):
            return get_earnings_events(conn, start="2024-01-01", end="2024-01-31",
                                       as_of=as_of)
    """))
    assert contamination_audit(str(good)) == []


def test_unguarded_news_calls_flagged(tmp_path):
    """fetch_news / get_articles / fetch_articles all require as_of=."""
    bad = tmp_path / "newsy.py"
    bad.write_text(textwrap.dedent("""
        from heron.data.alpaca_news import fetch_news
        from heron.data.cache import get_articles

        def f(conn):
            fetch_news(conn, tickers=["AAPL"])
            get_articles(conn, ticker="AAPL")
    """))
    findings = contamination_audit(str(bad))
    rules = sorted(f["rule"] for f in findings)
    assert rules == ["missing_as_of:fetch_news", "missing_as_of:get_articles"]


def test_directory_recursion(tmp_path):
    """Directory walk picks up nested .py files."""
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "leaky.py").write_text(
        "from heron.data.earnings import get_earnings_events\n"
        "def f(c): return get_earnings_events(c)\n"
    )
    findings = contamination_audit(str(tmp_path))
    assert len(findings) == 1
    assert findings[0]["line"] == 2


def test_parse_error_reported(tmp_path):
    """Syntax errors are reported as findings, not raised."""
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n    pass\n")
    findings = contamination_audit(str(bad))
    assert len(findings) == 1
    assert findings[0]["rule"] == "parse_error"

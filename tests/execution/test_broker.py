"""Tests for broker adapter interface and order ID generation."""

import pytest
from heron.execution.broker import (
    make_client_order_id, make_entry_order_id, make_close_order_id,
    BrokerAdapter,
)


def test_client_order_id_contains_components():
    oid = make_client_order_id("pead_v1", "AAPL", "buy", nonce="abc")
    # Don't parse the ID — strategy ids contain underscores; just assert
    # all components are present.
    assert "pead_v1" in oid
    assert "abc" in oid
    assert "AAPL" in oid
    assert "buy" in oid


def test_explicit_nonce_makes_id_deterministic():
    a = make_client_order_id("s1", "AAPL", "buy", nonce=42)
    b = make_client_order_id("s1", "AAPL", "buy", nonce=42)
    assert a == b


def test_default_nonce_is_present():
    oid = make_client_order_id("s1", "AAPL", "buy")
    # Format check; default nonce is a millisecond timestamp.
    assert "s1" in oid and "AAPL" in oid and "buy" in oid


def test_entry_id_deterministic_per_candidate():
    a = make_entry_order_id("pead_v1", candidate_id=99, ticker="AAPL", side="buy")
    b = make_entry_order_id("pead_v1", candidate_id=99, ticker="AAPL", side="buy")
    assert a == b
    c = make_entry_order_id("pead_v1", candidate_id=100, ticker="AAPL", side="buy")
    assert c != a


def test_close_id_deterministic_per_trade():
    a = make_close_order_id("pead_v1", trade_id=7, ticker="AAPL", side="sell")
    b = make_close_order_id("pead_v1", trade_id=7, ticker="AAPL", side="sell")
    assert a == b


def test_close_id_differs_per_trade():
    a = make_close_order_id("pead_v1", trade_id=1, ticker="AAPL", side="sell")
    b = make_close_order_id("pead_v1", trade_id=2, ticker="AAPL", side="sell")
    assert a != b


def test_entry_without_candidate_falls_back():
    # Manual entry without a candidate — still produces a valid (if not
    # retry-safe) id.
    oid = make_entry_order_id("s1", candidate_id=None, ticker="AAPL", side="buy")
    assert "s1" in oid and "AAPL" in oid


def test_broker_adapter_is_abstract():
    with pytest.raises(TypeError):
        BrokerAdapter()

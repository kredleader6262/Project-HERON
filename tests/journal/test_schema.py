"""Tests for journal schema initialization."""

import sqlite3

from heron.journal import init_journal

EXPECTED_TABLES = {
    "strategies", "strategy_state_log", "candidates", "trades",
    "wash_sale_lots", "pdt_daytrades", "audits", "cost_tracking",
    "reviews", "events",
}

def test_all_tables_created(conn):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()}
    assert EXPECTED_TABLES.issubset(tables), f"Missing: {EXPECTED_TABLES - tables}"


def test_wal_mode(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_foreign_keys_on(conn):
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_idempotent_init(conn):
    init_journal(conn)
    init_journal(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()}
    assert EXPECTED_TABLES.issubset(tables)


def test_row_factory(conn):
    assert conn.row_factory == sqlite3.Row

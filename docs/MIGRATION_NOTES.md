# Migration Notes

Date: 2026-05-02
Scope: v4 refactor migration plan for Phase 1. This document is documentation-only; no migration has been run by Stage 2.

Canonical constraints:

- Per v4 Decision 5 / `Project-HERON-v4.md` §3.10, Desk reuses the existing `campaigns` substrate in Phase 1. There is no `campaigns` -> `desks` table rename and no `campaign_id` column rename.
- Per v4 Decision 7 / `Project-HERON-v4.md` §3.5, Signal is additive upstream of Candidate. Existing Candidate -> Strategy -> Trade flow remains unchanged.
- SQLite WAL remains the storage model. Migrations are custom idempotent DDL in `heron/journal/__init__.py`, not Alembic.

## Phase 1 Migrations

### Campaign -> Desk Rename

No schema migration is required or allowed in Phase 1.

The operator-facing UI and docs say Desk. The database table stays `campaigns`, the strategy foreign key stays `strategies.campaign_id`, service functions stay in `heron.journal.campaigns`, and CLI commands keep Campaign/Candidate naming. Any Desk route or helper added in Stage 4 is a compatibility wrapper around the existing Campaign substrate.

### Signal Additive Schema

Stage 5 adds first-class Signals upstream of Candidates. Existing Candidate rows remain valid even when they have no Signal link.

Recommended additive DDL:

```sql
CREATE TABLE IF NOT EXISTS signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id          TEXT NOT NULL,
    source              TEXT NOT NULL,
    finding_ref_json    TEXT,
    producing_agent     TEXT,
    producing_model     TEXT,
    ticker              TEXT,
    sector              TEXT,
    asset               TEXT,
    signal_type         TEXT NOT NULL,
    bias                TEXT NOT NULL CHECK (bias IN (
                            'long_bias', 'short_bias', 'informational', 'risk-off'
                         )),
    thesis              TEXT NOT NULL,
    confidence          REAL,
    classification      TEXT,
    evidence_json       TEXT,
    generated_at        TEXT NOT NULL,
    expires_at          TEXT,
    resolution_status   TEXT NOT NULL DEFAULT 'open',
    outcome_json        TEXT,
    baseline_json       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE IF NOT EXISTS signal_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER NOT NULL,
    candidate_id    INTEGER NOT NULL,
    strategy_id     TEXT NOT NULL,
    bridge_source   TEXT NOT NULL DEFAULT 'research',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES candidates(id),
    FOREIGN KEY (strategy_id) REFERENCES strategies(id),
    UNIQUE(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_signals_campaign ON signals(campaign_id, generated_at);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker, generated_at);
CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type, bias);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(resolution_status, expires_at);
CREATE INDEX IF NOT EXISTS idx_signal_candidates_signal ON signal_candidates(signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_candidates_strategy ON signal_candidates(strategy_id);
```

Relationship semantics:

- One Signal can produce zero, one, or many Candidates.
- Each Phase 1 Candidate links to at most one upstream Signal through `UNIQUE(candidate_id)`.
- A legacy Candidate may have no Signal link.
- Baseline-mirrored Candidates should link to the same Signal as the LLM Candidate, using `bridge_source='baseline_mirror'`.
- Candidate approval remains per strategy; linking a Signal never approves or rejects downstream Candidates.

`finding_ref_json` is the Phase 1 bridge to Research Findings. It may point to cached `news_articles`, classifier output, earnings events, or other source records. A dedicated Research Finding table is deferred unless Stage 5 proves transient refs are insufficient.

### Idempotent Migration Path

Stage 5 should add the Signal DDL to the journal initialization path and make it safe to run repeatedly:

1. Add `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` statements to the journal DDL/migration path.
2. Do not alter `campaigns`, `strategies`, `candidates`, or `trades` for the initial Signal bridge.
3. Add service functions in `heron/journal/signals.py` that validate referenced `campaign_id`, `signal_id`, `candidate_id`, and `strategy_id` before linking.
4. Keep existing databases valid with no backfill. Historical Candidates simply have no upstream Signal.
5. For production, back up the WAL database before the first Stage 5 run. If the migration is interrupted, re-running `init_journal()` should complete or no-op safely.
6. If Signal writes need to be disabled after deployment, stop creating/linking Signals in the Research bridge; leave the additive tables in place until a later cleanup decision.

## Phase 2 Migrations Deferred

### Campaigns -> Desks Database Rename

Out of scope for Phase 1. Revisit only when Desk #2 is proposed and there is evidence that internal Campaign naming creates real maintenance risk. A future rename would require route/service/CLI aliases, data migration scripts, FK/index rebuild planning, backup/restore rehearsal, and a compatibility window.

### Multi-Desk Capital Allocator

Out of scope for Phase 1. Revisit when more than one active Desk needs simultaneous capital allocation under the same global account caps. Until then, Desk metrics are computed on demand and strategy-level allocation remains the operative implementation.

### Multi-Desk Signal Router

Out of scope for Phase 1. Revisit when multiple Desks subscribe to overlapping signal types or tickers and a single Signal needs formal routing across Desks. Phase 1 Signals link to Candidates after research generation; they do not route capital or execution rights.

### Shared Signal Approval

Out of scope for Phase 1. Revisit when approving the same upstream Signal separately for many strategy-specific Candidates becomes the operator bottleneck. Until then, Candidate approval remains per strategy so existing gates, audit trails, and executor assumptions stay intact.

### Live Alpaca Adapter

Out of scope for the v4 Desk/Signal refactor. Revisit as a separate hardening project before any live-money adapter is required beyond current paper-mode support. Trigger conditions include a strategy that passes paper-window, baseline-beat, monthly review, and operator readiness gates.

## Test-Suite Migration

`tests/journal/test_schema.py` currently asserts a stale 10-table subset. Stage 7 updates it to assert the actual initialized schema, including the journal tables and the five cache tables after `init_journal()` and cache `init_db()` run against the same temp SQLite database.

The Stage 7 edit should:

- Replace subset-only assertions with explicit expected table sets.
- Include Campaign/Desk tables and operational tables created by current journal initialization.
- Include cache tables: `ohlcv`, `news_articles`, `fetch_log`, `earnings_events`, and `universe_snapshots`.
- Preserve WAL, foreign-key, idempotency, and row-factory assertions.
- Add Signal tables to the expected set after Stage 5 lands.
- If the exact journal count differs from the Stage 0 shorthand of 13 journal tables because scheduler tables are initialized with the journal, assert the observed code reality and document that classification in the test comment rather than hiding it behind subset checks.

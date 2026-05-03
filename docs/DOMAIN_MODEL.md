# Domain Model

Canonical source: [Project-HERON-v4.md](../Project-HERON-v4.md). This doc is the engineer-facing map of the v4 objects and Phase 1 storage reality.

## Hierarchy

```text
Account
  -> Desk
      -> Research Finding
          -> Signal
              -> Candidate
                  -> Strategy
                      -> Run
                          -> Order
                              -> Transaction
```

Every transaction must trace backward:

```text
Transaction <- Order <- Run <- Strategy <- Signal <- Research Finding <- Agent <- Feed <- Desk
```

## Account

Account is the global money and safety boundary. It owns PDT count, wash-sale exposure, monthly cost cap, total exposure cap, daily loss limit, buying power, reconciliation status, broker/API health, live-trading lock, monthly review gate, promotion gate, and local LLM trust aggregate.

These are account-level globals. Per-desk budgets sit inside them.

## Desk

A Desk is the thematic operating unit and v4 operator-facing top-level object. It owns research objective, universe/watchlist, feeds, schedules, agents, signal types, strategy stack, capital budget, risk envelope, feedback reporting, paper/live status, and review cadence.

Phase 1 storage reality:

- Desk equals existing Campaign.
- Table remains `campaigns`.
- Foreign key remains `strategies.campaign_id`.
- Service names remain `create_campaign`, `transition_campaign`, `attach_strategy`, `get_campaign_strategies`, `get_state_history`, `days_active`.
- Compatibility routes `/campaigns`, `/campaign/<id>`, and campaign lifecycle actions remain; canonical dashboard routes are `/desks`, `/desk/new`, `/desk/<id>`, and `/desk/<id>/<action>`.
- `default_paper` is the auto-created default Desk/Campaign that backfills orphan strategies; the dashboard presents it as the PEAD Desk, not as a DB artifact.

Current Campaign fields used by Desk presentation:

- `id`, `name`, `description`.
- `mode`, `state`.
- `capital_allocation_usd`, `paper_window_days`.
- `parent_campaign_id`.
- `started_at`, `graduated_at`, `retired_at`, `retired_reason`.
- `created_at`, `updated_at`.

Lifecycle:

```text
DRAFT -> ACTIVE -> PAUSED -> GRADUATED -> RETIRED
```

## Research Finding

A Research Finding is a raw or processed observation from feeds, agents, market data, filings, or news. Findings may produce Signals.

Phase 1 reality: there is no dedicated Research Finding table today. Findings may be transient classifier output and/or represented by persisted article/cache rows. Stage 5 clarifies the additive bridge.

## Signal

A Signal is a first-class research/market claim. It is upstream of Candidates and can produce zero, one, or many Candidates.

Required fields:

- Source.
- Desk.
- Producing Research Finding.
- Producing agent / model.
- Ticker / sector / asset.
- Signal type.
- Bias: `long_bias`, `short_bias`, `informational`, `risk-off`.
- Thesis.
- Confidence.
- Classification.
- Supporting evidence.
- Timestamp.
- Expiry.
- Consuming strategies.
- Claim resolution status.
- Outcome.
- Baseline comparison where applicable.

Bearish Signals are preserved even while current strategies are long-only. They may be ignored by PEAD but consumed later by risk overlays or future strategy types.

## Candidate

A Candidate is the strategy-specific approval/execution object. The existing `candidates` table remains unchanged in Phase 1.

Current fields:

- `id`, `strategy_id`, `ticker`, `side`, `source`.
- `local_score`, `api_score`, `final_score`.
- `disposition`, `rejection_reason`.
- `thesis`, `context_json`.
- `created_at`, `disposed_at`.

Phase 1 relationship:

```text
Signal -> Candidate(s) -> Strategy
```

One Signal can produce many Candidates, but each Candidate still belongs to one Strategy. Candidate approval remains per-strategy. Shared signal approval is Phase 2.

## Strategy

A Strategy is trade logic inside a Desk. It consumes strategy-specific Candidates derived from Signals.

Defines:

- Signal subscription.
- Entry conditions.
- Exit conditions.
- Position sizing.
- Stop-loss/take-profit methodology.
- Holding period and PDT-safe minimum.
- Risk budget.
- Deterministic baseline variant.
- Paper/live state.
- Promotion criteria.

Current storage remains `strategies`, with `campaign_id` as Desk membership.

Lifecycle:

```text
PROPOSED -> PAPER -> LIVE -> RETIRED
```

## Run

A Run is a scheduled execution attempt of a Strategy. Runs are part of trace lineage and scheduler observability. Current runtime history is represented through scheduler run records and events rather than a dedicated strategy-run table.

## Order

An Order is broker-level intent submitted through Execution. Every order must use deterministic, opaque `client_order_id` values:

- Entries: `make_entry_order_id(strategy, candidate_id, ticker, side)`.
- Exits: `make_close_order_id(strategy, trade_id, ticker, side)`.

Duplicate `client_order_id` responses indicate a prior successful submission and are handled as success.

## Transaction

A Transaction is broker-confirmed money truth: fills, closes, P&L, slippage, and order state recorded in the journal. Transactions roll up to Strategy, Signal, Desk, and Account through Trace Chips and join paths.

## Phase 1 Additive Signal Rule

Signals are additive upstream of Candidates. Do not rewrite the existing Candidate -> Strategy -> Trade chain to make Signals fit. Stage 5 should bridge Signals to Candidates while preserving current approvals, executor assumptions, trade foreign keys, audit paths, and baseline mirroring.

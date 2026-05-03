# Canonical Architecture

Project HERON v4 is a learning-first, operator-gated trading system. It trades through deterministic code, uses LLMs only for research and explanation, and treats the journal as the product. The canonical spec is [Project-HERON-v4.md](../Project-HERON-v4.md); [Project-HERON-v3.md](../Project-HERON-v3.md) is historical reference.

## Object Model in 5 Minutes

The top-level operator object is now the **Desk**. In Phase 1, Desk is the operator-facing name for the existing `campaigns` substrate. Database and CLI names remain `campaigns` and `campaign_id`; dashboard and operator docs use Desk. Do not add a parallel `desks` table in Phase 1.

Hierarchy:

```text
Account
    -> Desk (existing Campaign substrate)
      -> Research Findings
      -> Signals
          -> Candidates
              -> Strategies
                  -> Runs
                      -> Orders
                          -> Transactions
```

A **Signal** is a first-class research/market claim upstream of Candidates. One Signal can produce candidates for many strategies. A **Candidate** remains strategy-specific and approval remains per-strategy in Phase 1. The execution path still flows through existing Strategy, risk, Executor, broker, and journal gates.

## Six-Section Flight Deck

The dashboard target is the **HERON Flight Deck**, organized by operator intent and rendered in the **Executive Signal** visual direction (premium financial-operator desk; dark foundation with layered surfaces; Signal Gold as a sparingly-used brand accent; Heron Teal as a restrained secondary accent; refined typography with tabular numerals for data). Brand phrase: *Insight. Strategy. Advantage.* Full design language and tokens in [UI_INFORMATION_ARCHITECTURE.md](UI_INFORMATION_ARCHITECTURE.md).

The six sections:

1. **Mission** - cockpit with Global Safety Strip, active desks, approvals, live runs, P&L, alerts, health.
2. **Desks** - primary workspace with Overview, Research, Signals, Strategies, Backtests, Runs, Trades, Feedback, Logs, Settings.
3. **Approvals** - human decision queue using Decision Cards.
4. **Activity** - chronological Timeline Spine across events, trades, audits, scheduler runs, approvals, and state changes.
5. **Portfolio** - money truth with attribution by desk, strategy, and signal type.
6. **System** - Operations, Configuration, and Introspection.

The Global Safety Strip is persistent on every page and shows account-level status that cannot decompose by Desk: Trading/View Mode, PDT, wash-sale risk, cost cap, daily loss, exposure, buying power, reconciliation, broker/API health, System Safety Mode, review gate, promotion gate, and LLM trust aggregate.

## Locked Decisions

- Two-level baseline-beat is required: Strategy-level and Desk-level.
- Strategy promotion requires Strategy pass; Desk expansion requires Desk pass; live trading requires both unless auditable operator override.
- Gating windows are 90 market days, not calendar days.
- Signal collision resolution is highest conviction, then signal confidence, strategy hit-rate for signal type, lower exposure, earlier run.
- Feedback is a deterministic report in Phase 1, not an autonomous agent.
- Trust score is `(desk, task, model, score, sample_size, last_updated)` with Mission aggregate and System -> Introspection history.
- Desk reuses Campaigns in Phase 1; no DB-level rename.
- The auto-created `default_paper` campaign presents in the dashboard as the PEAD Desk.
- Desk metrics are computed on demand first; cache only if dashboard latency proves it necessary.
- Bearish/negative Signals are preserved as informational even while current strategies are long-only.
- CLI keeps `campaign` / `candidate` naming in Phase 1.
- Visual direction is Executive Signal: dark, layered, Signal Gold accent, Heron Teal secondary, restrained.

## Safety Invariants

The v3 safety core still rules v4: no LLM in execution hot path, operator-gated risk increases, mandatory wash-sale and PDT/GFV checks, 10-second stale-quote kill switch, deterministic `client_order_id`, cost cap enforced in code, IEX-only assumptions, adversarial-input sanitization, SEC EDGAR User-Agent, memorization-contamination flags, virtual stops for fractional Alpaca orders, and reconciliation drift halting new live entries.
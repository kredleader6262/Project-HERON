# ROADMAP

Milestone tracker for Project HERON. Each milestone is independently demo-able.
See `Project-HERON.md` Section 15 for full descriptions.

| # | Milestone | Status | Notes |
|---|---|---|---|
| 1 | Data layer | ✅ Done | 48 unit tests passing. Integration tests need API keys. |
| 2 | Journal and SQLite schema | ✅ Done | 10 tables, 47 tests, CLI demo (`heron journal demo`). |
| 3 | Strategy framework skeleton | ✅ Done | Base class, risk checks (wash-sale/PDT/exposure/daily-loss), sizing, 33 tests. |
| 4 | Strategy layer (PEAD) | ✅ Done | PEAD strategy with screen/levels/exit, 20 tests. Deterministic + LLM variant support. |
| 5 | Execution layer | ✅ Done | Broker adapter, Alpaca paper adapter, executor with risk checks, virtual stops, reconciliation. 11 tests. |
| 6 | Dashboard v1 | ✅ Done | Flask + HTMX + Tailwind. 6 views: overview, strategies, strategy detail, trades, candidates, health. 8 tests. CLI: `heron dashboard`. |
| 7 | Research layer — local only | ✅ Done | Ollama client, news classifier (batch+single), candidate generator with dedup/scoring/cost gate, orchestrator. 21 tests. CLI: `heron research run/status`. |
| 8 | Research layer — Claude escalation | ✅ Done | Claude API client, thesis writer + conviction, escalation logic (15% sampling + score-based + post-mortem), audit logging. 20 tests. CLI: `heron research thesis`. |
| 9 | Baseline-variant runner | ✅ Done | Deterministic twin creation, candidate mirroring, daily returns, paired equity curves, Section 10.2 bootstrap beat test. 24 tests. CLI: `heron baseline create/beat-test/curves`. |
| 10 | Strategy proposal flow | ✅ Done | Claude proposer (daily limit + cost gate), dashboard approval UI (approve/reject/promote/retire), CLI commands (`heron journal approve/reject/inbox`, `heron research propose`). 19 tests. |
| 11 | Audit system | ✅ Done | Cost-triggered post-mortems (memorization-guard), rolling trust score, dashboard `/audits` view, CLI (`heron audit run/score/list`). 16 tests. |
| 12 | EOD debrief + Discord alerts | ✅ Done | Discord webhook client with per-category rate-limit (1/10min), EOD debrief with Claude prose + P&L/cost aggregation + dashboard link. 17 tests. CLI: `heron debrief`, `heron alert test/send/reset`. |
| 13 | Backtester | ✅ Done | Deterministic replay engine, SEC/FINRA/slippage cost model, report persistence, memorization-contamination flag, synthetic candidate seeder, dashboard `/backtests` + detail view. 15 tests. CLI: `heron backtest run/list`. |
| 14 | Cost controls | ✅ Done | Centralized `cost_guard` (projection + warn/trip states), Discord `cost_warning`/`cost_trip` alerts, research halt (execution continues), `/costs` dashboard + CLI (`heron cost status/notify`). 14 tests. |
| 15 | Resilience hardening | ✅ Done | Startup audit (reconciliation, stop coverage, pending work), graceful shutdown signal handlers, secrets hygiene (env perms, required vars, log leak scan), `/resilience` dashboard + CLI (`heron resilience audit/secrets`). 20 tests. |
| 15.5 | Campaigns + templates + supervisor | ✅ Done | Campaigns as first-class container (DRAFT→ACTIVE→PAUSED→GRADUATED→RETIRED, owns paper-window clock + capital). Strategy templates registry (parameterized PEAD authoring). APScheduler-driven `heron run` supervisor with preflight, heartbeat, dashboard transparency (`/campaigns`, `/scheduler`). 388 tests passing. Unblocks M16. |
| 16 | 90-day paper trading (PEAD) | ⬜ | Mandatory paper window before live graduation |

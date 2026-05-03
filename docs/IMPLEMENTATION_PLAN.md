# Project HERON v4 Refactor Implementation Plan

Date: 2026-05-02
Scope: Stage 2 planning for implementation Stages 3-7. This document is documentation-only and does not authorize application-code changes by itself.

Canonical sources:

- `Project-HERON-v4.md` is the source of truth.
- `docs/ORIENTATION_MEMO.md` supplies actual-code risk areas and current route/schema reality.
- `docs/CANONICAL_ARCHITECTURE.md`, `docs/DOMAIN_MODEL.md`, and `docs/UI_INFORMATION_ARCHITECTURE.md` define the v4 object model, six-section IA, and Executive Signal direction.

## Planning Invariants

- Preserve the v3/v4 safety core: no LLM in execution hot path, operator-gated risk increases, wash-sale/PDT/GFV checks, 10-second stale-quote kill switch, deterministic `client_order_id`, cost cap, IEX assumptions, adversarial-input sanitization, SEC EDGAR User-Agent, memorization flags, fractional-order virtual stops, and reconciliation drift blocking new live entries.
- Phase 1 keeps `campaigns` / `campaign_id` internally. Dashboard and operator docs say Desk; database, service, and CLI names remain Campaign/Candidate unless a later spec amendment says otherwise.
- Signal is additive upstream of Candidate. Existing Candidate -> Strategy -> Trade, approval, executor, audit, trade FK, and baseline mirroring assumptions remain working.
- Existing tests are a constraint. Refactor steps add or update focused tests in the same commit as behavior changes, and the full 518-test suite should keep passing.
- Compatibility aliases are behavior until explicitly retired. In Phase 1, existing public dashboard routes and CLI commands are retained through Stage 7 unless listed otherwise below.

## Stage 3 - UI Shell and Six-Section Navigation

### Goal

Create the v4 six-section dashboard shell while keeping every existing route and workflow reachable.

### Scope

- Update the top-level navigation to Mission, Desks, Approvals, Activity, Portfolio, System in that order.
- Keep `/` as Mission and keep `/overview` as the legacy Mission drill-down.
- Add canonical section routes where missing: `/desks`, `/approvals`, `/activity`, `/system`, plus lightweight System subpages or anchors for Operations, Configuration, and Introspection.
- Keep existing pages behind their current routes and link them from the new section pages rather than merging workflows yet.
- Add the persistent Global Safety Strip to the shared shell with current available data and clearly flagged placeholders where the current journal lacks attribution.
- Preserve `/actions` as the scheduler-control page; Activity is a new aggregate surface and does not replace `/actions`.

### Out Of Scope

- No Campaign -> Desk copy sweep beyond the new nav labels; Stage 4 handles operator-facing terminology.
- No Signal schema or Signal UI; Stage 5 owns it.
- No Executive Signal token/component redesign; Stage 6 owns the visual refactor.
- No route deletion, handler rewrite, or business-logic move.

### Deliverables

- Modify `heron/dashboard/templates/base.html` for six-section nav and Global Safety Strip placement.
- Add or modify templates: `mission_control.html`, `system.html`, `approvals.html`, `activity.html`, and small section cards/partials as needed.
- Modify `heron/dashboard/__init__.py` only for additive GET routes and thin wrappers around existing data queries.
- Update `tests/dashboard/test_app.py` with route smoke tests for all six sections, nav labels, Global Safety Strip presence, and legacy-route compatibility.
- Update README/dashboard docs only if route names in operator instructions become stale.

### Demo Criteria

- Open `/` and see the six-section nav plus a Global Safety Strip.
- Click Mission, Desks, Approvals, Activity, Portfolio, and System; each returns a working page.
- Open legacy paths `/overview`, `/campaigns`, `/proposals`, `/candidates`, `/actions`, `/scheduler`, `/resilience`, `/health`, `/costs`, `/policies`, `/agents`, `/audits`, `/backtests`, `/data/earnings`, and `/data/universe`; each still works or redirects exactly as before.
- System visibly separates Operations, Configuration, and Introspection without hiding Costs, Policies, Resilience/Health, Actions, Agents, Audits, Setup, or Data.

### Test Strategy

- Add dashboard smoke tests for new section routes and nav text.
- Keep existing dashboard tests for `/`, `/overview`, `/campaigns`, `/candidates`, `/actions`, `/scheduler`, `/health`, and `/resilience` passing.
- Add compatibility tests proving `/scheduler` still redirects to `/actions` and `/actions/<job>/<action>` plus `/scheduler/<job>/<action>` still queue the same command.
- Run the full pytest suite after the stage because base-template changes can affect every dashboard page.

### Rollback Strategy

- Revert the additive section routes and `base.html` nav/strip changes in one commit.
- Leave old routes untouched, so rollback restores the pre-stage dashboard shell without DB changes.
- If only the strip data is faulty, gate the strip behind a template include flag and revert that include while keeping six-section nav.

### Dependencies

- Stage 2 docs complete.
- No external inputs required.

### Risk Level

Medium. The change is UI/routing only, but it touches the shared shell and every operator workflow.

### Money-Moving Impact

No order path changes. The strip reads safety state; it must not write policy mode, enqueue jobs, approve candidates, promote strategies, or call execution code.

### Estimated Commits

3-10.

## Stage 4 - Campaign -> Desk UI and Documentation Rename

### Goal

Make Desk the operator-facing object while preserving the Campaign substrate internally.

### Scope

- Change dashboard labels, page titles, empty states, form copy, breadcrumbs, and nav text from Campaign to Desk where the operator sees the concept.
- Present `default_paper` as the default PEAD Desk, not as a migration artifact.
- Add canonical Desk routes if Stage 3 did not fully add them: `/desks`, `/desk/new`, `/desk/<campaign_id>`, and `/desk/<campaign_id>/<action>` as wrappers or redirects around the existing campaign handlers.
- Keep `/campaigns`, `/campaign/new`, `/campaign/<campaign_id>`, and `/campaign/<campaign_id>/<action>` working as compatibility aliases.
- Update engineer-facing docs to describe Campaign/Desk equivalence without implying a database rename.

### Out Of Scope

- No `campaigns` table rename, no `campaign_id` rename, no service-layer rename, and no CLI Desk rename.
- No multi-desk allocator/router/scheduler.
- No Desk metrics table; metrics remain computed on demand per v4 §3.10.
- No Signal schema; Stage 5 owns that.

### Deliverables

- Modify templates: `campaigns.html`, `campaign_detail.html`, `campaign_new.html`, `strategy_new.html`, `setup.html`, and shared cards that expose Campaign language.
- Optionally add Desk wrapper templates only if they avoid duplicating business logic.
- Modify `heron/dashboard/__init__.py` only for additive Desk route wrappers/redirects and presentation helpers.
- Update docs: `README.md`, `docs/CANONICAL_ARCHITECTURE.md`, `docs/DOMAIN_MODEL.md`, `docs/UI_INFORMATION_ARCHITECTURE.md`, and any dashboard-oriented docs whose operator copy says Campaign where Desk is intended.
- Add tests in `tests/dashboard/test_app.py` for `/desks`, `/desk/new`, `/desk/<id>`, legacy `/campaigns` aliases, and `default_paper` presentation.

### Demo Criteria

- Open `/desks` and see Desk language, including the default PEAD Desk if the DB only has `default_paper`.
- Create a new Desk from the UI; the underlying row is still created in `campaigns`.
- Open `/campaigns` and `/campaign/<id>`; both still work or redirect to the corresponding Desk page.
- Create a strategy from a template and attach it to a Desk using the existing `campaign_id` internally.

### Test Strategy

- Extend existing campaign dashboard tests rather than replacing them.
- Add assertions that operator-visible copy says Desk on canonical pages and that legacy routes remain valid.
- Add a focused test for `default_paper` display name/description so the migration artifact does not leak into first-run UX.
- Keep `tests/journal/test_campaigns.py` unchanged except for Stage 7 market-day work; service names remain Campaign.

### Rollback Strategy

- Revert copy/template changes and Desk wrapper routes; no schema migration exists to roll back.
- Because legacy `/campaign*` routes remain canonical-compatible, production can keep operating through old URLs immediately.

### Dependencies

- Stage 3 navigation shell should be present so Desk has a canonical top-level home.

### Risk Level

Medium. Most work is copy/presentation, but naming drift can confuse operators and tests.

### Money-Moving Impact

No direct money-moving changes. Lifecycle buttons still call `transition_campaign()`; no strategy promotion or order logic is altered.

### Estimated Commits

3-10.

## Stage 5 - First-Class Signal Additive Layer

### Goal

Introduce Signals upstream of Candidates without disrupting the existing Candidate -> Strategy -> Trade path.

### Scope

- Add journal schema for `signals` plus a bridge table linking Signals to zero, one, or many Candidates.
- Add `heron/journal/signals.py` service helpers for create/list/get/update/link operations.
- Update research candidate generation so classifier/research output can create a Signal and then create strategy-specific Candidates from it.
- Keep existing `create_candidate()`, `dispose_candidate()`, candidate approval routes, executor cycle, trade FK, audits, and baseline mirroring working.
- Link mirrored baseline Candidates to the same upstream Signal when available, preserving the current duplicate-candidate baseline pattern.
- Decide whether Research Finding remains transient by inspecting the actual research path; Phase 1 default is transient finding refs stored in Signal evidence/finding JSON.
- Preserve bearish/negative-bias Signals as informational even if current PEAD ignores them.

### Out Of Scope

- No shared Signal approval. Candidate approval remains per strategy in Phase 1.
- No multi-desk signal router, multi-desk collision engine, or budget allocator.
- No execution-layer changes and no alternate order path.
- No CLI Signal rename or Candidate rename.
- No DB-level Campaign -> Desk rename.

### Deliverables

- Modify `heron/journal/__init__.py` with additive, idempotent DDL/migration for Signals.
- Add `heron/journal/signals.py`.
- Modify `heron/research/candidates.py` and, if needed, `heron/research/orchestrator.py` to create/link Signals before Candidates.
- Modify `heron/strategy/baseline.py` only enough to preserve Signal linkage for baseline mirrors.
- Add optional dashboard read surfaces for Signals under Desks/Activity if small; defer richer Signal UI to Stage 6 if it risks workflow churn.
- Add tests: `tests/journal/test_signals.py`, focused research bridge tests, baseline mirror linkage tests, and regression tests proving existing candidate acceptance/executor-cycle assumptions still hold.

### Demo Criteria

- Run a research pass in paper mode and see at least one Signal recorded upstream of one or more Candidate rows.
- Open a Candidate detail page and see its upstream Signal trace when present; legacy Candidates without Signals still render.
- Accept/reject a Candidate exactly as before.
- Baseline mirroring still creates a separate baseline Candidate and links it to the same Signal when a Signal exists.

### Test Strategy

- Journal tests for idempotent Signal migration, foreign keys, list filters, expiry/status updates, and bridge cardinality.
- Research tests for one Signal producing multiple strategy-specific Candidates without duplicate pending Candidates for the same strategy/ticker.
- Baseline tests for mirror idempotency and Signal linkage preservation.
- Existing candidate, research, audit, baseline, executor-cycle, and execution tests must still pass.

### Rollback Strategy

- Stop writing Signals by feature flag or config guard if the bridge misbehaves; existing Candidates remain valid and executable.
- Leave additive Signal tables in place if already migrated; they are inert when no code reads them.
- If necessary, revert research bridge writes while keeping schema/service code until a later cleanup migration is planned.

### Dependencies

- Stage 4 terminology should be complete so Signal rows reference Desk/Campaign consistently in UI.
- No external inputs required.

### Risk Level

High. The schema is additive, but Candidate creation feeds the approval and execution pipeline.

### Money-Moving Impact

Indirect only. Stage 5 must not touch `Executor`, broker adapters, order IDs, pre-trade checks, virtual stops, or reconciliation. Conservatism: existing Candidate rows remain the sole executable object; Signals are provenance and grouping upstream.

### Estimated Commits

3-10.

## Stage 6 - Executive Signal Token Refactor and Workflow Surfaces

### Goal

Apply the Executive Signal visual direction through tokens/components first, then update the high-value workflow surfaces.

### Scope

- Define design tokens for color, typography roles, spacing, borders, status emphasis, and chart containers in one dashboard token module or stylesheet.
- Refactor the layout shell, Global Safety Strip, six-section navigation, buttons, cards, tables, badges, forms, and chart containers to use those tokens.
- Apply Mode-Aware Emphasis for PAPER, LIVE, SAFE, and DERISK states.
- Update Approvals with Decision Cards that show the v4 evidence set while reusing existing approve/reject/promote/candidate handlers.
- Update Activity as Timeline Spine over events, scheduler runs, audits, trades, candidates, reviews, and state logs where available.
- Update Portfolio with attribution by Desk, Strategy, and Signal type, using Trace Chips and on-demand joins.
- Update Desk Overview as Desk Control Board with paired LLM-vs-baseline curves and Baseline Ghost.

### Out Of Scope

- No schema rename, no new money-moving action, no shared Signal approval, no multi-desk router/allocator.
- No autonomous Feedback Agent.
- No chart-library migration unless the screenshot pack and implementation review explicitly require it; Chart.js is acceptable if it satisfies the visual target.
- No layout changes that hide safety surfaces under decoration.

### Special Screenshot Dependency

Stage 6 receives an operator-supplied screenshot reference pack produced in a separate design-language session. The pack includes desktop and mobile mockups for Mission, Desk Overview, Approvals, Activity, Portfolio, and System surfaces in the Executive Signal direction. Stage 6 implementation requires a vision-capable model to interpret these screenshots as visual targets for spacing, hierarchy, density, and interaction patterns. The screenshots are the visual source of truth for Stage 6; the six-section IA in `docs/UI_INFORMATION_ARCHITECTURE.md` is the structural source of truth. The two must reconcile: do not abandon the IA to match the mockups, and do not abandon the Executive Signal mockups to preserve a stale layout.

### Deliverables

- Modify or add dashboard style/token assets under `heron/dashboard/templates/` and static assets if the repo adds a static CSS file in this stage.
- Modify templates: `base.html`, `mission_control.html`, `campaign_detail.html` or Desk equivalent, `proposals.html`, `candidates.html`, `actions.html` or Activity equivalent, `portfolio.html`, `resilience.html`, `costs.html`, `policies.html`, `agents.html`, `audits.html`, and reusable card partials.
- Modify `heron/dashboard/__init__.py` only for additional read models needed by Approvals, Activity, Portfolio attribution, Desk Overview, and Trace Chips.
- Add dashboard tests for Decision Card content, Timeline filtering, Trace Chips on Candidates/Trades/Portfolio rows, Desk Control Board data presence, and mode-emphasis class/token output.
- Add screenshot or Playwright/manual verification notes to the PR description for desktop and mobile views.

### Demo Criteria

- Open `/` and see the Executive Signal shell, Global Safety Strip, and six-section nav matching the screenshot pack's hierarchy/density.
- Open a Desk Overview and see paired LLM and Baseline Ghost curves where data exists, with clear empty states otherwise.
- Open Approvals and see Decision Cards for proposed strategies and pending Candidates without changing approve/reject behavior.
- Open Activity and filter a chronological Timeline Spine.
- Open Portfolio and see attribution by Desk/Strategy and Signal type where Signal data exists.
- Resize to a mobile viewport and verify text does not overlap or escape controls.

### Test Strategy

- Keep all existing dashboard route tests passing.
- Add tests for new read models and template output, but avoid brittle pixel-perfect assertions.
- Use browser/screenshot verification for desktop and mobile because this stage is visual and screenshot-driven.
- Run full pytest; if a chart-library migration is chosen, add focused chart data serialization tests.

### Rollback Strategy

- Keep token/component changes isolated so the previous templates can be restored by reverting the stage commits.
- If deeper workflow pages fail, retain the tokenized shell and revert only the page-specific workflow changes.
- No DB migration rollback is needed unless Stage 6 adds optional cache tables, which is out of scope by default.

### Dependencies

- Stages 3-5 complete.
- Operator-supplied screenshot reference pack.
- Vision-capable implementer/model for interpreting the screenshot pack.

### Risk Level

Medium. Visual and dashboard read-model changes are broad, but they should not alter order submission or risk logic.

### Money-Moving Impact

No direct money-moving changes. Approval controls must call the existing handlers and gates; no new submit/promote/override path is introduced.

### Estimated Commits

10+.

## Stage 7 - Cleanup, Divergence Remediation, and Bug Fixes

### Goal

Resolve known v4/code divergences and clean up only the compatibility scaffolding that is proven temporary.

### Scope

- Fix `days_active()` and any paper-window gating that depends on it so the 90-day requirement uses market days, not calendar days.
- Ensure EOD debrief Claude calls log token/cost usage to `cost_tracking`.
- Update schema tests to assert the actual initialized journal/cache schema instead of the stale 10-table subset.
- Audit compatibility aliases introduced during Stages 3-6 and retire only temporary wrappers that have no docs, tests, templates, or operator usage left.
- Document aliases that intentionally survive Phase 1, especially Campaign/Candidate CLI and `/campaign*` dashboard compatibility.

### Out Of Scope

- No DB-level Campaign -> Desk rename.
- No live Alpaca adapter hardening.
- No multi-desk scheduler/router/budget allocator/collision engine.
- No shared Signal approval.
- No broad dashboard redesign beyond bug fixes from Stage 6 review.

### Deliverables

- Modify `heron/journal/campaigns.py` and supporting utilities/tests for market-day `days_active()` or a new clearly named helper if retaining calendar-day UI hints.
- Modify promotion/readiness surfaces only if needed to enforce v4 §10.1 correctly; any change must block on uncertainty rather than allowing early promotion.
- Modify `heron/alerts/debrief.py` and/or `heron/runtime/jobs.py` so debrief Claude cost is persisted through existing `log_cost()` helpers.
- Modify `tests/journal/test_schema.py` to assert the actual journal and cache table sets after initialization.
- Update docs: `Project-HERON-v4.md` Known Divergences, `docs/MIGRATION_NOTES.md`, README/ROADMAP status notes, and any compatibility-alias notes that remain.

### Demo Criteria

- A Desk with fewer than 90 market days cannot be presented as promotion-ready; a Desk at/over the window shows correct readiness.
- Run an EOD debrief dry run with mocked Claude cost and see a `cost_tracking` row for task `debrief`.
- Run schema tests and see them assert the full actual schema rather than a stale subset.
- Legacy routes intentionally retained still work; any retired temporary aliases have docs/tests removed in the same commit.

### Test Strategy

- Add unit tests for market-day counting, including weekends/holidays if a trading-calendar helper is introduced or stubbed.
- Add promotion-gate tests if paper-window gating touches strategy promotion.
- Add debrief cost logging tests using mocked Claude responses and no network.
- Update schema tests to initialize journal and cache schema in a temp DB and compare explicit expected table sets.
- Run the full pytest suite, with special attention to `tests/execution`, `tests/strategy`, `tests/research`, `tests/journal`, and `tests/dashboard`.

### Rollback Strategy

- Revert each divergence fix independently; keep tests paired with their implementation commits.
- If market-day logic miscounts, fail closed by blocking promotion readiness until fixed.
- If debrief cost logging double-counts, disable only the debrief logging path while preserving existing cost guard behavior.

### Dependencies

- Stages 3-6 complete enough that cleanup can distinguish lasting compatibility from temporary scaffolding.

### Risk Level

Medium to High. Most fixes are journal/research/test cleanup, but market-day promotion gating is a risk-increase control.

### Money-Moving Impact

Potentially yes: paper-window readiness and strategy promotion are risk-increase gates. Stage 7 must not weaken promotion gating; it should block early promotion and add regression tests before changing UI/controller behavior.

### Estimated Commits

3-10.

## Compatibility-Alias Inventory

### Dashboard Routes

All existing public routes remain accepted through Stage 7 unless this table names a retirement. "Retire stage: none" means no Phase 1 retirement is planned.

| Current route(s) | v4 home | Alias treatment | Retire stage |
|---|---|---|---|
| `/` | Mission | Remains canonical Mission. | none |
| `/overview` | Mission drill-down | Keep as legacy drill-down. | none |
| `/mode/<m>` | Global shell | Keep unchanged; used by shell mode filter. | none |
| `/campaigns`, `/campaign/new`, `/campaign/<campaign_id>`, `/campaign/<campaign_id>/<action>` | Desks | Stage 4 may add `/desks`, `/desk/new`, `/desk/<id>`, `/desk/<id>/<action>` wrappers; old routes remain. | none |
| `/strategies`, `/strategy/<strategy_id>`, `/strategy/new`, `/strategy/new/preview` | Desks -> Strategies | Keep routes and handlers; relabel surrounding UI only. | none |
| `/strategy/<strategy_id>/approve`, `/reject`, `/promote`, `/retire` | Approvals / Strategy Detail | Keep existing POST handlers and gates; Decision Cards call these. | none |
| `/proposals` | Approvals | Keep as strategy-proposal inbox alias/detail list. | none |
| `/candidates`, `/candidate/<int:candidate_id>`, `/candidate/<int:candidate_id>/accept`, `/candidate/<int:candidate_id>/reject` | Approvals / Activity / Signals trace | Keep Candidate naming and handlers; Signals add provenance only. | none |
| `/trades` | Portfolio / Activity | Keep as trade-log route; Portfolio may link to it. | none |
| `/agents`, `/agents/status`, `/research/run` | System -> Introspection / Activity | Keep; no autonomous agent behavior added. | none |
| `/audits`, `/audits/contamination` | System -> Introspection and Activity | Keep; Activity can aggregate audit events without replacing route. | none |
| `/backtests`, `/backtests/<report_id>`, `/strategy/<id>/backtest`, `/strategy/<id>/walkforward`, `/strategy/<id>/sweep`, `/backtests/sweeps/<sweep_id>`, `/backtests/sweeps/<sweep_id>/promote/<report_id>` | Desks -> Backtests | Keep; no route rename in Phase 1. | none |
| `/data/earnings`, `/data/earnings/fetch`, `/data/universe`, `/data/universe/snapshot` | System -> Configuration / Data | Keep existing data routes. | none |
| `/portfolio` | Portfolio | Remains canonical. | none |
| `/policies`, `/policies/override` | System -> Configuration / Safety | Keep; risky mode override remains explicit. | none |
| `/costs` | System -> Operations | Keep; also summarize in Safety Strip. | none |
| `/resilience` | System -> Operations | Keep; Health remains there. | none |
| `/health` | System -> Operations | Keep existing redirect to `/resilience#health`. | none |
| `/setup` | System -> Configuration | Keep first-run setup route. | none |
| `/glossary` | System / Docs help | Keep. | none |
| `/actions`, `/actions/<job_id>/<action>` | System -> Operations / scheduler controls | Keep as canonical scheduler-control surface. Activity does not replace it. | none |
| `/scheduler`, `/scheduler/<job_id>/<action>` | Legacy scheduler aliases | Keep existing redirects/behavior to `/actions`. | none |

### Service Functions and Modules

| Surface | Existing names to preserve | New/additive names | Retire stage |
|---|---|---|---|
| Campaign/Desk substrate | `create_campaign`, `get_campaign`, `list_campaigns`, `transition_campaign`, `attach_strategy`, `get_campaign_strategies`, `get_state_history`, `days_active`, `campaigns`, `campaign_id` | Optional presentation helpers may say Desk, but database/service names remain Campaign. | none |
| Candidate approvals | `create_candidate`, `dispose_candidate`, `get_candidate`, `list_candidates`, `candidates`, `candidate_id` | Stage 5 adds Signal services and bridge helpers; Candidate helpers stay executable path. | none |
| Strategy lifecycle | `create_strategy`, `transition_strategy`, `get_strategy`, `list_strategies`, strategy state routes | No rename. | none |
| Scheduler/actions | `request_command`, `scheduler_runs`, `scheduler_commands`, `/actions`, `/scheduler` aliases | Activity read model may aggregate but does not replace command queue. | none |
| Baseline machinery | `ensure_baseline`, `mirror_candidate_to_baseline`, `run_beat_test`, `get_equity_curve` | Stage 5 may add Signal bridge linkage for mirrors. | none |

### CLI Commands

CLI keeps existing Campaign/Candidate naming in Phase 1. No Desk/Signal CLI rename is planned for Stages 3-7.

Preserve these command groups and commands: `data today`, `data quote`, `data earnings fetch/list`, `data universe snapshot/list`, `journal demo/status/approve/reject/inbox`, `dashboard`, `init`, `ollama status/start/stop/pull/list`, `research run/status/thesis/propose`, `baseline create/beat-test/curves`, `audit run/score/list/contamination`, `policy status/override/eval`, `alert test/send/reset`, `debrief`, `backtest run/walkforward/sweep/list/reparity`, `cost status/notify`, `resilience audit/secrets`, and `run`.

### Templates

Template filenames may keep legacy object names in Phase 1 if that avoids churn. Operator-facing text changes before filenames.

| Template set | Treatment | Retire stage |
|---|---|---|
| `base.html`, `mission_control.html`, `index.html` | Stage 3 shell/nav/strip; `/overview` keeps `index.html` as legacy drill-down. | none |
| `campaigns.html`, `campaign_detail.html`, `campaign_new.html` | Stage 4 relabels UI to Desk while filenames may remain. | none |
| `strategies.html`, `strategy_detail.html`, `strategy_new.html` | Keep; nested under Desks in nav. | none |
| `proposals.html`, `candidates.html`, `candidate_detail.html`, `_card_proposal.html`, `_card_candidate.html`, `_card_review.html` | Feed Approvals/Decision Cards; existing pages remain. | none |
| `actions.html`, `audits.html`, `audits_contamination.html`, `trades.html` | Feed Activity/System; existing pages remain. | none |
| `portfolio.html` | Stage 6 attribution refactor; route remains canonical. | none |
| `resilience.html`, `costs.html`, `policies.html`, `agents.html`, `setup.html`, `glossary.html`, `data_earnings.html`, `data_universe.html` | Move under System IA without route deletion. | none |
| `backtests.html`, `backtest_detail.html`, `backtest_sweep.html` | Remain backtest surfaces under Desks. | none |

## Money-Moving Code Inventory

| Surface | Files/modules | Planned stage touches | Review discipline |
|---|---|---|---|
| Broker IDs and adapter contract | `heron/execution/broker.py`, `heron/execution/alpaca_adapter.py` | None planned. | Any change requires execution tests for deterministic IDs, duplicate-submit handling, and adapter behavior. Treat 422 duplicate as success. |
| Order entry, retry, fills, virtual stops, reconciliation | `heron/execution/executor.py`, `heron/execution/cycle.py` | None planned. | No alternate order path. Run full `tests/execution` if touched. Preserve stale quote, DERISK, pre-trade checks, and `get_order(client_order_id)` retry discipline. |
| Risk checks and regulatory predicates | `heron/strategy/risk.py`, `heron/journal/trades.py`, `heron/config.py` | None planned except Stage 7 may touch paper-window readiness outside pre-trade checks. | Wash-sale/PDT/GFV and quote freshness fail closed. Run strategy/risk/trades tests if touched. |
| Candidate acceptance feeding execution | `heron/journal/candidates.py`, candidate dashboard routes, `heron/execution/cycle.py` | Stage 5 adds Signal provenance around Candidates. | Candidate remains the executable approval object. Existing accept/reject and executor-cycle tests must pass. |
| Promotion and risk-increase gates | Strategy promote route in `heron/dashboard/__init__.py`, `heron/journal/strategies.py`, `heron/journal/ops.py`, `heron/backtest/parity.py`, `heron/journal/campaigns.py` | Stage 6 UI may render gates; Stage 7 may enforce market-day readiness. | Operator-gated, monthly review and baseline parity remain. Market-day uncertainty blocks promotion, never permits it. |
| System mode and policy overrides | `heron/strategy/policy.py`, `/policies/override`, `policy override/eval` CLI | Stages 3/6 display only. | UI must call existing override path with reason; no one-click hidden mode switch. |
| Runtime jobs and scheduler controls | `heron/runtime/jobs.py`, `heron/runtime/supervisor.py`, `/actions`, `/scheduler`, `heron run` | Stages 3/6 display/aggregate; Stage 7 debrief-cost fix may touch `job_eod_debrief`. | Scheduler command queue remains audited. Executor job still enters through `run_executor_cycle()`. |
| Startup audit and reconciliation blocking | `heron/resilience/startup_audit.py`, `heron/runtime/preflight.py`, `heron/execution/executor.py` | Stage 3/6 display only. | Reconciliation drift remains operator-visible and blocks new live entries. |
| Data/quote freshness and IEX assumptions | `heron/data/alpaca_market.py`, `heron/data/__init__.py`, `heron/strategy/risk.py` | None planned. | Preserve 10-second stale-quote kill switch and IEX cost/liquidity assumptions. |
| Research cost cap | `heron/research/cost_guard.py`, `heron/journal/ops.py`, `heron/alerts/debrief.py` | Stage 7 logs debrief cost. | Cost cap enforcement remains in code. New logging must not double count. |
| Backtests, contamination, baseline comparisons | `heron/backtest/*`, `heron/strategy/baseline.py`, `heron/research/audit.py` | Stage 5 baseline Signal linkage; Stage 6 display; Stage 7 schema/test cleanup. | Pre-cutoff backtests remain flagged, baseline-beat stays paired/bootstrap, no promotion bypass. |

## Risk-and-Test Map

| Orientation risk area | Addressed in stage(s) | Test additions/updates |
|---|---|---|
| UI Nav Restructure | Stage 3, refined in Stage 6 | Six-section route/nav smoke tests, legacy-route compatibility tests, Global Safety Strip render tests. |
| Campaign -> Desk Renaming | Stage 4 | `/desks` and `/campaigns` compatibility tests, operator copy assertions, `default_paper` presentation test. |
| Candidate -> Signal Layering | Stage 5 | Signal schema/service tests, research bridge tests, baseline mirror linkage tests, existing candidate/executor/audit regression tests. |
| Approvals Page Redesign | Stage 3 skeleton, Stage 6 Decision Cards | Tests proving Decision Cards call existing strategy/candidate/review handlers and preserve promotion gates. |
| Activity Timeline Consolidation | Stage 3 skeleton, Stage 6 Timeline Spine | Aggregate read-model ordering/filter tests across `events`, `scheduler_runs`, audits, trades, candidates, reviews, and state logs where available. |
| Portfolio Attribution Rebuild | Stage 6 | Service/read-model tests for Desk/Strategy/Signal attribution, empty states for legacy Candidates without Signals, no execution-path writes. |
| System Section Split | Stage 3, styled in Stage 6 | System Operations/Configuration/Introspection route tests plus old `/resilience`, `/costs`, `/policies`, `/agents`, `/audits`, `/setup`, `/glossary`, `/health` compatibility tests. |
| Anything Money-Moving | Stage 5 indirect, Stage 7 promotion-gate fix if implemented | Full `tests/execution`, `tests/strategy`, and focused promotion/candidate tests. Any order-path change requires extra review and is out of planned scope. |
| Untested Refactor-Touched Areas | All stages | Add dashboard tests with each route/template change; add journal/research tests with Signal schema; add debrief cost tests; update schema test to actual tables. |
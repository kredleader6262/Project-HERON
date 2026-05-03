# Project HERON v4

*Hypothesis-driven Execution with Research, Observation, and Notation*

A learning-first, operator-gated, semi-autonomous algorithmic trading system with an LLM research layer, deterministic execution, and a journal-first operating model.

**Seed:** $500  
**Horizon:** Indefinite  
**Posture:** Learning-First, Operator-Gated  
**Canonical version:** v4  
**Historical reference:** [Project-HERON-v3.md](Project-HERON-v3.md)

v4 replaces v3 as the canonical specification. v3 remains in the repository as historical reference. Most v3 safety constraints remain binding; v4 reframes the top-level operator object from Strategy to Desk and introduces first-class Signals upstream of Candidates.

---

## §0 Changes from v3

v4 is an architectural reframe, not a trading-safety rewrite. The risk posture, cost envelope, wash-sale/PDT discipline, IEX assumptions, PEAD reference strategy, baseline test, and operator gates are inherited from v3 unless this document explicitly narrows or clarifies them.

| v3 | v4 |
|---|---|
| Strategy as top-level organism | Desk as top-level operator-facing concept; Strategy is one mechanism inside it |
| Signals implicit, owned by Strategy | Signals first-class, upstream of Candidates, consumable by multiple Strategies |
| Single-level baseline-beat test per Strategy | Two-level baseline-beat: per Strategy and per Desk |
| Dashboard with backend-object tabs | Six operator-intent sections: Mission, Desks, Approvals, Activity, Portfolio, System |
| Strategy lifecycle as the only lifecycle | Desk lifecycle wraps Strategy lifecycle |
| Feedback as audit reports | Feedback Report in Phase 1; autonomous Feedback Agent deferred to Phase 2 |
| Trust score implicit/global | Trust score per desk, task, and model, with Mission aggregate |
| No named visual language | Executive Signal visual direction |

Preserved from v3, non-negotiable:

- Risk limits, wash-sale tracking, ticker-family map, pre-trade checks, PDT/GFV predicates: v3 §5.
- Monthly cost ceiling and halt-research fallback: v3 §7 and v3 §5.3.
- IEX-only data constraints, $10M ADV / $5 price filters, and 25 bps minimum round-trip cost assumption: v3 §4.1.
- LLM never in the execution hot path: v3 §4.
- Operator-gated state transitions for risk increases: v3 §1.2 and v3 §5.1.
- 90 market days minimum paper period before live promotion: v3 §10.1.
- Memorization-contamination guardrails for pre-cutoff data: v3 §4.2.3, v3 §6, v3 §12, v3 §17.
- Stale-quote kill switch at 10 seconds: v3 §4.1, v3 §4.4.4, v3 §13.1.
- Idempotent `client_order_id` on every order: v3 §4.4.2.
- Cap-and-fallback discipline: v3 §5.3.
- Discord push alerts, Tailscale VPN, SQLite WAL with hourly rsync: v3 §4.5 and v3 §13.
- Reference strategy PEAD: v3 §9.
- Core watchlist: v3 §8.
- News sources and credibility weights: v3 §4.1.1.
- Adversarial-input sanitization and SEC EDGAR User-Agent requirement: v3 §4.1.1.
- Backtester requirements: v3 §12.
- Kill criteria: v3 §14.
- Risk acknowledgments: v3 §17.

---

## §1 Principles

### §1.1 Primary Purpose

HERON is a learning system and engineering portfolio piece that happens to trade real money. It is not a credible path to beat the S&P 500 at a $500 account size. It is a credible way to build a disciplined, inspectable, LLM-annotated trading journal under strict risk limits.

The LLM does research, hypothesis generation, classification, thesis writing, and post-hoc explanation. Deterministic code handles entries, exits, sizing, risk, order submission, and reconciliation. The operator decides when risk increases.

### §1.2 The Three Commitments

**Learning-first.** No external capital is added to the account. The $500 is disposable risk capital. The system value is the journal, calibration data, and engineering experience; P&L is secondary.

**Operator-gated.** Every state transition that increases risk requires explicit operator approval. Agents recommend; the operator decides. No autonomous promotion, capital increase, risk-limit increase, or live deployment.

**Indefinite horizon.** HERON is designed to run for years. A graceful wind-down is acceptable at any time, and the write-up of why it wound down is part of the product.

### §1.3 Baseline Comparison

Every intelligent action must be compared against a dumb baseline. In v4, this happens at two levels:

- **Strategy-level:** each LLM-gated strategy runs beside a deterministic variant and must pass the v3 §10.2 paired bootstrap test over at least 90 market days before promotion.
- **Desk-level:** each Desk compares its combined intelligence layer against a deterministic Desk baseline before expansion.

A Desk can pass while individual strategies fail, or a strategy can pass while the Desk fails. Strategy promotion requires the strategy-level pass. Desk expansion requires the desk-level pass. Live trading requires both unless explicitly overridden by the operator with auditable approval.

### §1.4 v4 Design Principles

1. Every trade must be explainable backward.
2. Every future action must be interruptible forward.
3. Every intelligent action must be compared against a dumb baseline.
4. Every shared signal must have one accountable claim-resolution path.

The cockpit test: HERON succeeds only if the operator can open the app and immediately answer what is running, what is blocked, what needs approval, what made or lost money, why a trade happened, what future action can be stopped, and whether the LLM is beating baseline.

---

## §2 Goals and Non-Goals

### §2.1 Goals

- Operate a learning-first trading system under strict operator control.
- Organize work by Desk: research objective, watchlist, feeds, schedules, agents, signals, strategies, capital budget, risk envelope, feedback, and paper/live status.
- Promote first-class Signals upstream of Candidates so one research claim can feed zero, one, or many strategies.
- Paper-trade every approved strategy in parallel with a deterministic baseline variant.
- Compare LLM-gated behavior against deterministic baselines at Strategy and Desk level.
- Execute trades deterministically, with hard risk limits enforced in code.
- Produce a journal and Flight Deck dashboard that make every decision legible.
- Operate within a $45/month cost ceiling.
- Track and enforce wash-sale exposure, PDT/GFV predicates, stale quotes, and idempotent order submission before any order is sent.

### §2.2 Non-Goals

- Beating the S&P 500 or any benchmark at $500 scale.
- High-frequency or millisecond-sensitive trading.
- Derivatives, leveraged products, or shorting.
- Fully autonomous graduation of desks or strategies to live capital.
- Meaningful monthly income at the $500 scale.
- Day-trading strategies that require PDT exemption or a margin account above $25,000.
- Phase 1 multi-desk routing, shared signal approvals, or a live Alpaca adapter hardening project.

---

## §3 Desk, Signal, and Strategy Lifecycle

### §3.1 Desk

A **Desk** is the top-level operator-facing unit in v4. It is a thematic operating unit that owns a research objective, universe/watchlist, feeds, schedules, agents, signal types, strategy stack, capital budget, risk envelope, feedback reporting, paper/live status, and review cadence.

Phase 1 has one Desk: Post-Earnings Drift / PEAD, backed by the existing `default_paper` campaign. Operator-facing UI and docs say Desk. Internal database names remain `campaigns` and `campaign_id` for Phase 1.

Desk lifecycle:

```text
DRAFT -> ACTIVE -> PAUSED -> GRADUATED -> RETIRED
```

The Desk clock starts on DRAFT -> ACTIVE. Desk state wraps Strategy state: pausing a Desk stops its strategies from submitting new work without rewriting individual strategy state.

### §3.2 Strategy

A **Strategy** is trade logic inside a Desk. It consumes Signals through strategy-specific Candidate rows and defines signal subscription, entry rules, exit rules, sizing, stop-loss/take-profit methodology, minimum holding period, risk budget, deterministic baseline variant, paper/live state, and promotion criteria.

Strategy lifecycle remains:

```text
PROPOSED -> PAPER -> LIVE -> RETIRED
```

The Strategy object contents from v3 §3.1 remain binding: description, rationale, config, risk budget, paper/live/baseline curves, trade log, metrics, wash-sale exposure, state, and state history. v4 adds explicit Desk membership and Signal subscriptions.

### §3.3 Signal

A **Signal** is a first-class research or market claim. It is not a trading directive. It sits upstream of Candidates and can produce zero, one, or many Candidates, one per subscribing strategy.

Required Signal fields:

- Source.
- Desk.
- Producing Research Finding.
- Producing agent and model.
- Ticker, sector, or asset.
- Signal type.
- Bias: `long_bias`, `short_bias`, `informational`, or `risk-off`.
- Thesis.
- Confidence.
- Classification.
- Supporting evidence.
- Timestamp.
- Expiry.
- Consuming strategies, zero to many.
- Claim resolution status.
- Outcome.
- Baseline comparison where applicable.

Bearish and negative-bias Signals are preserved even while current strategies are long-only. Current strategies may ignore `short_bias` Signals; future strategies or risk overlays may consume them.

### §3.4 Research Finding

A **Research Finding** is a raw or processed observation from feeds, agents, market data, filings, or news. Findings may produce Signals.

The orientation memo did not identify a current persisted Research Finding object. Phase 1 may treat findings as transient classifier output that is persisted into news/article/cache tables. Stage 5 clarifies the additive bridge.

### §3.5 Candidate

A **Candidate** remains the strategy-specific approval/execution object. Phase 1 preserves the current one-Candidate-to-one-Strategy relationship. Signals layer on top; they do not replace Candidates.

Phase 1 flow:

```text
Research Finding -> Signal -> Candidate -> Strategy -> Run -> Order -> Transaction
```

Multiple Candidates may share one upstream Signal. Candidate approval remains per strategy in Phase 1. Shared signal approval, where one approval cascades to all subscribed strategies, is deferred to Phase 2.

### §3.6 Run, Order, and Transaction

A **Run** is a scheduled execution attempt by a strategy inside a Desk.

An **Order** is broker-level intent submitted through the Execution layer with deterministic, opaque `client_order_id` values per v3 §4.4.2.

A **Transaction** is the broker-confirmed fill or state change that becomes money truth in the journal.

Every transaction must trace backward:

```text
Transaction <- Order <- Run <- Strategy <- Signal <- Research Finding <- Agent <- Feed <- Desk
```

### §3.7 Signal Collision Resolution

When multiple strategies request the same ticker from the same signal, highest conviction wins, subject to global exposure and per-strategy caps.

Tie-breakers, in order:

1. Higher signal confidence.
2. Higher strategy historical hit-rate for this signal type.
3. Lower current exposure.
4. Earlier-generated run.

Phase 1 is not FIFO and not pro-rata. Losing strategies record a skipped opportunity with reason `signal claimed by higher-ranked strategy`.

### §3.8 Feedback Report

Phase 1 implements a deterministic **Feedback Report**, not an autonomous Feedback Agent.

Allowed in Phase 1:

- Flag for review.
- Recommend backtest.
- Recommend pause.
- Recommend parameter review.
- Update metrics.

Disallowed in Phase 1:

- Modify a strategy.
- Change thresholds.
- Deploy changes.
- Promote to live.
- Alter risk limits.
- Rewrite prompts.

Autonomous Feedback Agent work is Phase 2.

### §3.9 Local LLM Trust Score

The trust score is per desk, per task, and per model. Schema shape:

```text
(desk, task, model, score, sample_size, last_updated)
```

Mission shows an aggregate with a warning when any individual task falls below threshold. Desk pages provide per-desk drilldown. System -> Introspection shows full history.

### §3.10 Repo Reconciliation

The existing `campaigns` substrate becomes the Phase 1 Desk substrate. The orientation memo found a real persisted `campaigns` table, `campaign_state_log`, strategy membership through `strategies.campaign_id`, a service layer (`create_campaign`, `transition_campaign`, `attach_strategy`, `get_campaign_strategies`, `get_state_history`, `days_active`), and dashboard routes (`/campaigns`, `/campaign/<id>`, and lifecycle action routes).

Phase 1 rules:

- No parallel `desks` table.
- Internal database names remain `campaigns` and `campaign_id`.
- Operator-facing UI and docs say Desk.
- Engineering-facing docs may say Campaign/Desk, with glossary equivalence.
- `/campaigns`, `/campaign/<id>`, and lifecycle action routes remain working as aliases or canonical routes throughout the refactor.
- The auto-created `default_paper` campaign backfills orphan strategies. Stage 4 must present it as a default Desk without making it look like an implementation artifact.
- Desk-level metrics are computed on demand from strategies, trades, and backtest reports first. No new metrics tables in Phase 1. Summary tables are a Phase 2 decision only if real dashboard latency requires them.

### §3.11 Phase 1 Scope Limits

Phase 1 does not build:

- Multi-desk scheduler.
- Multi-desk signal router.
- Multi-desk budget allocator.
- Multi-desk feedback orchestration.
- Multi-desk collision engine.
- Live Alpaca adapter.
- Shared signal approval.
- Database-level Campaign -> Desk rename.
- CLI Desk/Signal renames.

Phase 2 picks these up when desk #2 is proposed or when scale demands it.

---

## §4 System Architecture

Five layers with strict interfaces. Each layer can be swapped or rewritten without touching the others. The LLM is never in the execution hot path.

| Layer | Responsibility | Implementation |
|---|---|---|
| Data | Fetch/cache market data, news, filings; sanitize scraped text as adversarial input | Python, Alpaca Data API, curated RSS |
| Research | Run LLM passes, produce findings/signals, propose strategies, generate candidates | Ollama/Qwen local tier plus Claude API |
| Strategy | Consume signals through candidates; compute entries/stops/targets; risk checks | Pure Python, deterministic, backtestable |
| Execution | Submit idempotent orders, manage fills, virtual stops, reconcile | Broker-adapter pattern, paper Alpaca adapter today |
| Journal & Dashboard | Persist every decision; serve HERON Flight Deck | SQLite WAL, Flask, Jinja, HTMX, Tailscale |

### §4.1 Data Layer

HERON uses Alpaca's free IEX-tier market data. IEX covers roughly 2-3% of consolidated US equity volume, so quotes can appear wider than true NBBO and fills may look like unexplained slippage. SIP data at $99/month is out of scope for a $500 seed account.

Strategy implications inherited from v3 §4.1:

- Reject tickers with average daily volume below $10M or price below $5.
- Assume at least 25 bps round-trip cost for same-day strategies.
- Never submit when the last successful quote is older than 10 seconds.

#### §4.1.1 News Sources and Credibility Weights

Every scraped document is treated as adversarial input: stripped, sanitized, and never passed raw into an LLM system prompt. All SEC EDGAR requests must include a User-Agent identifying HERON and contact information.

| Source | Weight | Feed URL / Notes |
|---|---:|---|
| SEC EDGAR 8-K filings | 1.0 | `sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom`; requires User-Agent |
| SEC EDGAR 10-Q / 10-K filings | 1.0 | Same pattern, `type=10-Q` / `type=10-K`; requires User-Agent |
| Federal Reserve press releases | 1.0 | `federalreserve.gov/feeds/press_all.xml` |
| US Treasury press releases | 1.0 | `home.treasury.gov/rss` |
| BLS press releases | 0.9 | `bls.gov/feed/news_release/rss.xml` |
| Reuters Business | 0.8 | Reuters RSS where available; otherwise Alpaca News aggregation |
| Alpaca News API | 0.8 | Free in-stack aggregation including AP/Benzinga breadth |
| SEC EDGAR Form 4 | 0.7 | `type=4`; insider buys/sells on watchlist names |
| Seeking Alpha Market Currents | 0.4 | High volume, variable quality, flagged as aggregator |

### §4.2 Research Layer

The Research layer runs on a schedule, not in the trade execution hot path.

- Pre-market pass, 06:30 ET: overnight news, tradeable developments, Signals/Candidates, occasional strategy proposals.
- Midday refresh, 12:30 ET: re-check top candidates, update conviction, surface breaking news.
- End-of-day debrief, 16:30 ET: review trades, write outcome prose, publish summary to Discord.

#### §4.2.1 Model Strategy

Local tier: Qwen 2.5 7B Instruct via Ollama for classification, deduplication, relevance, sentiment, and low-cost structured tasks. The local model never sizes, risks, or writes final theses.

API tier: Claude Sonnet for shortlist thesis writing, conviction scoring, strategy proposals, end-of-day debrief prose, and monthly review synthesis. Claude Haiku handles cheap batch/audit tasks where appropriate. Model IDs are environment-configurable.

#### §4.2.2 Local vs API Routing

Local tasks:

- News/headline relevance classification.
- Sentiment classification.
- First-pass deduplication.
- Routine summarization where failure is low-cost.

API tasks:

- Shortlist thesis writing and conviction scoring.
- Strategy proposals and deterministic-baseline variant specifications.
- Sampled audit comparisons on post-cutoff data.
- End-of-day debrief prose.
- Monthly review synthesis.

#### §4.2.3 Memorization Warning

Any LLM analysis of pre-cutoff data is contaminated by memorization and must not be treated as out-of-sample evidence. Pre-cutoff backtests are reference-only, excluded from trust-score calculations, and visibly flagged in reports.

### §4.3 Strategy Layer

The Strategy layer is pure Python and deterministic. It consumes Candidates generated from Signals for active strategies and decides whether, when, and how much to trade.

Mandatory responsibilities inherited from v3 §4.3:

- Validate candidate freshness.
- Compute entry, stop, and target from structural levels, not LLM-suggested numbers.
- Size positions based on risk budget, account equity, current exposure, and expected edge after costs.
- Run wash-sale pre-checks before live entries.
- Run PDT/GFV pre-checks before entries requiring same-day exit risk.
- Enforce per-strategy position and concentration limits.
- Decide closes, trims, and holds through rules.

Because the LLM is outside the hot path, a historical day can be replayed and will produce the same result.

### §4.4 Execution Layer

The Execution layer talks to the broker, submits orders, handles fills/rejections/retries, and reconciles journal state against broker truth.

#### §4.4.1 Fractional Share Constraints

At $500, HERON trades fractional shares. Alpaca fractional orders are DAY-only, do not support bracket/OCO, and cannot be replaced. Stop-loss and take-profit logic lives in HERON's polling code, not in Alpaca order types.

#### §4.4.2 Idempotency

Every order carries a deterministic, opaque `client_order_id`.

- Entries use `make_entry_order_id(strategy, candidate_id, ticker, side)`.
- Exits use `make_close_order_id(strategy, trade_id, ticker, side)`.
- On network error, retry, or ambiguous response, query by `client_order_id` before resubmitting.
- HTTP 422 `client_order_id must be unique` means a prior submission succeeded and must be treated as success, not failure.

#### §4.4.3 Reconciliation

Reconciliation runs at market open and close. It compares SQLite state against Alpaca orders, positions, and account state. Any drift must be operator-visible and block new live entries until acknowledged and resolved.

#### §4.4.4 Other Constraints

- Margin account with PDT enforcement is assumed.
- No shorting, options, or leveraged products.
- Actual fill price is recorded; slippage is logged.
- Hard shutdown flattens or protects positions and exits cleanly.
- Never submit if last quote is older than 10 seconds.
- Live Alpaca adapter hardening is separate from the v4 Desk/Signal refactor.

### §4.5 Journal and Dashboard

Every decision at every layer writes to the journal: findings, signals, candidates, approvals, risk checks, orders, fills, closes, audits, costs, reviews, scheduler runs, and operator actions. The journal is the product.

The dashboard is the **HERON Flight Deck**, a Flask/Jinja/HTMX web application accessed locally or over Tailscale. Authentication is the VPN boundary; no public internet exposure.

#### §4.5.1 Six-Section UI

Top-level navigation, in order:

1. **Mission** - operator cockpit. Persistent Global Safety Strip plus active desks, pending approvals, live runs, today's P&L, alerts/exceptions, and system health.
2. **Desks** - primary workspace. Tabs: Overview, Research, Signals, Strategies, Backtests, Runs, Trades, Feedback, Logs, Settings. Desk Overview is the Desk Control Board, headlined by LLM-gated equity curve vs Baseline Ghost deterministic curve. Strategy Detail shows the same paired curves at strategy level.
3. **Approvals** - first-class human decision queue. Decision Cards include requested action, desk, strategy, signal source, LLM confidence, LLM hit-rate on last 50 suggestions, local LLM trust score for desk/task, baseline comparison, 95% CI status if paper, risk impact, PDT impact, wash-sale impact, cost impact, and rollback plan. Actions: Approve, Reject, Edit, Send to Backtest, Paper Trade First, Request More Research, Pause Desk, Mute Signal Type.
4. **Activity** - chronological event log rendered as Timeline Spine. Filter by desk, strategy, signal, ticker, agent, model, status, approval state, and date. Activity is a new aggregate page; existing `/actions` remains a scheduler-control alias and is not replaced.
5. **Portfolio** - money truth with attribution: P&L, holdings, exposure, open orders, closed trades, performance by desk, strategy, and signal type, baseline-vs-LLM contribution, drawdown, risk, wash-sale exposure, and PDT-relevant trades.
6. **System** - explicitly subdivided. Operations: Health, Costs, Schedules, Reconciliation, Broker status, Monthly Review history. Configuration: API Keys, Data Sources, Policies, Risk limits, Model settings. Introspection: Agents, Model logs, Audit logs, Prompt versions, Local LLM trust history.

#### §4.5.2 Account-Level Globals

These cannot decompose by Desk and must live on Mission and the persistent Global Safety Strip:

- PDT counter, rolling 5 business days.
- Wash-sale exposure, cross-desk and ticker-family-aware.
- Monthly cost cap.
- Total exposure cap.
- Daily loss limit.
- Buying power.
- Account reconciliation status.
- Broker/API health.
- Global live-trading lock: NORMAL, SAFE, DERISK.
- Monthly review gate status.
- Promotion gate status.
- Local LLM trust aggregate.

Per-desk budgets sit inside these caps, not beside them.

### §4.6 UI Design Language

The canonical dashboard visual direction is **Executive Signal**: a premium financial-operator desk aesthetic with a dark layered foundation, restrained Signal Gold and Heron Teal accents, refined typography, dense readable data surfaces, and distinct PAPER, LIVE, SAFE, and DERISK emphasis states. The six-section dashboard remains the HERON operator surface; Executive Signal supplies the visual vocabulary. Full named patterns, token guidance, and implementation posture live in [docs/UI_INFORMATION_ARCHITECTURE.md](docs/UI_INFORMATION_ARCHITECTURE.md).

---

## §5 Risk Management

Risk management is inherited from v3 §5 and remains non-negotiable. Risk is enforced at per-strategy, Desk, global, and regulatory levels.

### §5.1 Global Limits

| Rule | Limit | Enforcement |
|---|---:|---|
| Max total exposure across all strategies/desks | 80% of equity | Execution layer rejects new entries |
| Max concurrent positions system-wide | 3; 6 only above $1,500 equity | Execution layer rejects |
| Max single-trade loss | 5% of equity | Hard stop on every trade |
| Max daily loss | 8% of equity | Halt new entries for the day |
| Max daily new entries | 3 | Execution layer rejects beyond cap |
| PDT day-trade count | 3 of 4 FINRA limit over rolling 5 business days | Strategy layer rejects same-day-close entries at cap |
| Wash-sale lookback window | 30 days by ticker family | Strategy layer rejects repurchase of recent losers and substantially identical peers |
| Paper-to-live transition | Operator approval plus baseline-beat | No automatic trigger |
| Monthly review gate | Required written go/no-go | Blocks new promotions if missed |

### §5.2 Per-Strategy and Per-Desk Limits

Each strategy declares its own capital allocation, max concurrent positions, drawdown budget, minimum conviction, and minimum holding period. A Desk owns a capital budget and risk envelope that constrains the strategies inside it. Desk budgets are subordinate to account-level caps.

### §5.3 Cap-and-Fallback Pattern

Every hard cap names a fallback. No orphan limits.

| Cap | Immediate Action | Recovery Condition |
|---|---|---|
| Monthly API cost projected to exceed $45 ceiling | Halt Research layer; Strategy/Execution manage existing work | Operator resets budget or month rolls over |
| Max daily loss reached | Halt new entries for the day | New trading day |
| Max total exposure reached | Reject new entries across all desks | Exposure falls below cap |
| Strategy drawdown budget reached | Auto-retire or pause the strategy | Operator re-approves |
| Desk risk envelope reached | Reject or defer entries inside that Desk | Desk exposure/risk falls below cap |
| PDT limit would be tripped | Reject any entry requiring same-day exit | Rolling window rolls off |
| Wash-sale exposure detected pre-trade | Reject entry and log disallowed-loss amount | 30-day window closes |
| Local model unresponsive | Alert; fall back to rules-only candidate generator | Operator toggles research back on |
| Data feed failure or Alpaca incident | Alert; freeze new entries, manage open positions | Feed recovers and quote age < 10s |
| Reconciliation drift detected | Halt new live entries; alert/operator-visible event | Operator resolves mismatch and reruns audit/acknowledges |

### §5.4 Wash-Sale Tracking

The wash-sale rule is the most important regulatory risk in HERON. Pre-trade wash-sale checks are mandatory and non-negotiable.

HERON defenses inherited from v3 §5.4:

- Ticker-family map in config, reviewed quarterly.
- Live-mode pre-trade check for closed losing lots in the same family within 30 days.
- Paper mode may skip tax/broker wash-sale enforcement, but must still preserve observability where useful.
- Post-trade annotation of every sale at a loss.
- Nightly exposure report and year-end warnings.
- Cross-broker limitation disclosed as operator responsibility.
- Section 475(f) mark-to-market election out of scope.

#### §5.4.1 Initial Ticker-Family Map

- `{SPY, VOO, IVV}` - S&P 500 trio.
- `{QQQ, QQQM}` - Nasdaq-100 pair.
- `{IWM, VTWO}` - Russell 2000 small-cap.
- `{DIA}` - Dow 30.
- `{XLF}`, `{XLE}` - sector ETFs, each its own family.
- AAPL, MSFT, GOOGL, AMZN, NVDA, META - each its own family.

### §5.5 PDT and Settlement Mechanics

Alpaca's default account type is margin, and accounts under $25,000 are subject to FINRA pattern day trading rules. HERON treats three day trades per rolling five business days as the effective limit.

Required predicates:

- Maintain rolling 5-business-day count from the journal.
- Reject entries that would require a fourth same-day close.
- Prefer swing strategies with 2-10 day holds.
- Encapsulate PDT logic so FINRA rule changes can be applied without rewriting the rest of the system.
- Avoid GFVs in any cash-account path by respecting T+1 settlement.

### §5.6 Deliberately Absent

- Multi-tier drawdown escalation with graduated restrictions.
- Automatic conviction-score recalibration from small trade samples.
- Kill switches triggered purely by drawdown percentage.
- Retroactive close-on-slippage rules.
- Shorting, options, or margin beyond PDT constraints.

---

## §6 LLM Audit and Feedback

Audit mechanisms inherited from v3 §6 remain:

- **Baseline comparison:** every LLM-gated strategy and Desk is compared against deterministic baseline behavior.
- **Cost-triggered audit:** every local-model decision leading to a losing trade is flagged for post-mortem.
- **Continuous sampling:** approximately 15% of local-model decisions are escalated to Claude for comparison.
- **Memorization guard:** comparisons use post-cutoff data only; pre-cutoff data is reference-only.

v4 adds the deterministic Feedback Report described in §3.8 and defers autonomous Feedback Agent behavior to Phase 2.

---

## §7 Cost Envelope

Monthly ceiling: **$45**, enforced in code. When projected spend exceeds the ceiling, the Research layer halts new escalations while Strategy and Execution continue managing accepted candidates and open positions.

Allocation inherited from v3 §7:

- Claude API inference: approximately $31.50/month.
- Infrastructure: approximately $9/month.
- Buffer: approximately $4.50/month.

Cost-to-date, projection, per-strategy attribution, and per-desk attribution are visible on the Flight Deck. EOD debrief Claude calls must be charged to `cost_tracking`; current code divergence is documented in §19.

---

## §8 Watchlist

Phase 1 uses the v3 §8 fixed operator-curated watchlist. Dynamic watchlist management returns only after HERON has multiple live desks or strategies and enough operational evidence.

Core watchlist:

- Mega-cap tech: AAPL, MSFT, GOOGL, AMZN, NVDA, META.
- Broad-market ETFs: SPY, QQQ, IWM, DIA.
- Sector ETFs: XLF, XLE.

All names satisfy liquidity filters. ETFs are context and future strategy material; the PEAD reference strategy trades only the six individual stocks.

---

## §9 Reference Strategy: Post-Earnings Announcement Drift (PEAD)

PEAD remains the reference strategy from v3 §9.

| Parameter | Value |
|---|---|
| Universe | AAPL, MSFT, GOOGL, AMZN, NVDA, META |
| Trigger | Earnings surprise >= 5% on consensus EPS, announced within last 24h |
| Entry | Next session open at market order |
| Stop | 2x 14-day ATR below entry price |
| Target | 3x 14-day ATR above entry or time-exit at market close on day 10 |
| Position size | 15% of equity per position; max 3 concurrent |
| Minimum hold | 2 trading days |
| Deterministic variant | Enters every qualifying surprise |
| LLM variant | Same entry/exit rules; LLM may filter using 8-K context and guidance language |

Phase 1 Desk: Post-Earnings Drift / PEAD, backed by the existing `default_paper` campaign unless and until Stage 4 presents it differently.

---

## §10 Graduation and Baseline-Beat Tests

### §10.1 Paper Window

Every strategy runs at least **90 market days** on paper before any live-promotion recommendation. The window is market days for gating purposes. Calendar days may appear only as a clearly labeled UI hint.

Current `days_active()` uses calendar days; this is a known divergence targeted for Stage 7 (§19).

### §10.2 Strategy-Level Baseline-Beat Test

The v3 §10.2 test remains the strategy-level gate: paired bootstrap 95% confidence interval on daily return differences, LLM variant minus deterministic variant, over at least 90 market days. The CI must exclude zero on the positive side.

Strategy promotion requires:

- Strategy-level paired bootstrap pass.
- Paper equity curve net-positive after realistic costs.
- Drawdown within configured budget.
- No operator-intervention-required failures during paper.
- No missed wash-sale violations.
- Operator approval.

### §10.3 Desk-Level Baseline-Beat Test

The Desk-level test compares the Desk's combined intelligence layer against a deterministic Desk baseline. It uses the same paired daily-return framing where practical, aggregating the Desk's strategy stack and closed-trade outcomes.

Desk expansion requires Desk-level pass. Live trading requires both Strategy-level and Desk-level pass unless the operator explicitly overrides with an auditable approval.

### §10.4 Additional Readiness Signals

Not ready:

- Paper performance driven by one or two outliers.
- Drawdown approaching budget.
- Behavior the operator cannot explain from the journal.
- LLM hit-rate or trust score below threshold for the relevant desk/task.
- Reconciliation drift unresolved.

---

## §11 Monthly Review Gate

On the first trading day of each calendar month, the Flight Deck prompts for a written go/no-go review covering system health, per-strategy and per-desk performance, baseline status, cost vs budget, wash-sale exposure, PDT usage, kill criteria, and explicit decision/rationale.

Until filed, no new strategies can be promoted. Existing strategies continue operating normally. Monthly review status appears in the Global Safety Strip. Review history lives under System -> Operations.

---

## §12 Backtesting

Backtester requirements from v3 §12 remain binding:

- Replay historical periods deterministically.
- Include point-in-time universe membership where available.
- Apply news at publication timestamp, not bar open.
- Simulate commission, SEC Section 31 fees, FINRA TAF, and realistic slippage.
- Support synthetic candidate streams from historical news.
- Produce metrics comparable to paper/live dashboard metrics.
- Support parameter sweeps for robustness.
- Flag LLM training-overlap windows as memorization-contaminated.

Every strategy proposal reaching PAPER must have an attached backtest report visible before paper trading begins. Desk-level aggregation is computed from strategy reports and trades on demand in Phase 1.

---

## §13 Infrastructure

### §13.1 Stale-Data Kill Switch

The implemented kill switch rejects orders when the last successful quote is older than 10 seconds. Alpaca status-page incident gating remains a pre-live hardening item until wired into the execution path.

### §13.2 Corporate Actions

Pre-live hardening should poll Alpaca corporate-actions announcements nightly. Splits, reverse splits, dividends, delistings, and mergers must be journaled and surfaced to the operator before affected positions receive new entries.

### §13.3 Secrets and Backup

- API keys live in OS keychain or `.env` with restrictive permissions. Never in repo, never in logs.
- Separate paper and live keys.
- SQLite runs in WAL mode.
- Hourly rsync to cloud storage.
- Timestamps stored in UTC and displayed in America/New_York.
- NTP sync enforced at OS level.

### §13.4 Network and Alerts

- Dashboard binds localhost by default and is exposed through Tailscale VPN when needed.
- No public internet exposure.
- Discord webhook push alerts for time-sensitive events.
- Alerts rate-limited by category.

### §13.5 Technology Stack

The target stack remains locked unless the operator approves a change. Current divergences are documented; existing working code is not torn out just to match the target unless a later stage explicitly scopes that work.

| Layer | Target | Current notes from orientation memo |
|---|---|---|
| Language | Python 3.11+ | Workspace interpreter observed as Python 3.12.4 |
| Local LLM runtime | Ollama | Implemented through HTTP `/api/generate` |
| Local LLM model | `qwen2.5:7b-instruct-q4_K_M` | Env override supported |
| API LLM | Claude Sonnet and Haiku | Model IDs env-configurable |
| Broker | Alpaca, margin/PDT-aware | Paper adapter exists; live adapter hardening deferred |
| Market data | Alpaca Data API, IEX tier | Implemented |
| News | Alpaca News plus curated RSS | Implemented |
| Storage | SQLite WAL | Implemented; 13 journal + 5 cache tables after migrations |
| Trading calendar | `exchange_calendars` or `pandas_market_calendars` target for future calendar work | Current code uses custom `pytz`-based helpers |
| Web framework | Flask + Jinja + HTMX | Implemented; HTMX usage is light |
| Charts | Plotly or lightweight-charts as v3 target unless Stage 6 explicitly chooses otherwise | Current dashboard uses Chart.js |
| Config | YAML plus `.env` | Implemented |
| Scheduling | APScheduler in-process | Implemented |
| Supervisor | systemd or equivalent wrapper | `heron run` supervisor implemented; wrapper is operator choice |
| Remote access | Tailscale VPN | Documented |
| Alerts | Discord webhook | Implemented |

---

## §14 Kill Criteria

Kill criteria from v3 §14 remain binding:

- Ninety paper market days with LLM variant failing baseline-beat -> retire LLM role or strategy.
- Two consecutive months of unread EOD debriefs -> pause all new entries until operator reactivation.
- Monthly costs exceeding $75 for two consecutive months -> disable Research until manual re-enable.
- Any missed wash-sale violation -> immediate halt on all strategies, full journal audit, manual re-enable only.
- Three or more consecutive weeks where HERON maintenance dominates feature work -> stop adding features and consider graceful retirement.
- Flat-to-negative equity after 30+ paper days on every active strategy/Desk -> monthly review must explicitly continue or wind down.

---

## §15 Implementation Roadmap and Stage Boundaries

v3 §15 remains historical context. Current milestone truth is [ROADMAP.md](ROADMAP.md). v4 refactor stages are documentation and architecture first, implementation later.

Stage 1 produces this spec and companion docs only. It does not change application code.

Downstream implementation constraints:

- Preserve compatibility routes unless a later stage explicitly retires them.
- Do not add a parallel Desk table in Phase 1.
- Do not move LLM behavior into Strategy or Execution hot paths.
- Do not bypass existing candidate approval, strategy promotion, review, risk, or broker-idempotency gates.
- Add focused tests before or with refactors that touch dashboard routing, candidate/signal layering, baseline aggregation, or money-moving paths.

---

## §16 Future Scope

Deferred until Phase 1 operational evidence justifies it:

- Autonomous Feedback Agent.
- Multi-desk scheduler/router/budget allocator/collision engine.
- Shared signal approval.
- Database-level Campaign -> Desk rename migration.
- Live Alpaca adapter hardening project.
- Strategy Promoter agent.
- Multi-agent review pipeline.
- Crypto adapter.
- Cross-asset correlation awareness.
- Dynamic watchlist management.

---

## §17 Risk Acknowledgments

The v3 §17 risk acknowledgments remain binding:

- LLM alpha has not been demonstrated net of costs for retail traders.
- Memorization contamination makes pre-cutoff backtests reference-only.
- Small-account economics dominate at $500.
- IEX-only data worsens slippage and quote interpretation.
- Wash-sale rule failure can create severe tax distortion.
- PDT exposure is real under a margin account below $25,000.
- Scraped content can carry prompt injection.
- Qwen 2.5 7B is a classifier, not a numerical decision-maker.
- Operator attention is a core dependency.
- HERON's primary value is learning, engineering, and the journal.

---

## §18 Appendix: Bootstrap Setup

Bootstrap requirements from v3 §18 remain:

1. Install Ollama.
2. Pull `qwen2.5:7b-instruct-q4_K_M` or configured successor.
3. Smoke-test local JSON classification.
4. Optionally benchmark alternative local models and pin exact model tag.
5. Create Alpaca paper account and keys.
6. Create Claude API key with external spend cap.
7. Set up Discord webhook.
8. Install Tailscale.
9. Register a SEC EDGAR User-Agent, for example `HERON-research contact@yourdomain.com`.

---

## §19 Known Divergences

These are documented here so future stages and PRs can cite them. Stage 1 does not fix them.

| Divergence | Target fix stage / disposition |
|---|---|
| `days_active()` uses calendar days; v4 §10.2 gating requires market days | Stage 7 |
| EOD debrief Claude calls do not log to `cost_tracking` | Stage 7 |
| `tests/journal/test_schema.py` asserts a 10-table subset; current schema is 13 journal + 5 cache tables | Stage 7 |
| v3 references a `proposals` table that does not exist; current proposals are `strategies` rows in `PROPOSED` state | Documented; no fix needed |
| v3 stack lists Plotly / lightweight-charts; current dashboard uses Chart.js | Documented; no fix needed unless Stage 6 chooses a chart migration |
| v3 stack lists `exchange_calendars` / `pandas_market_calendars`; current code uses custom `pytz`-based helpers | Documented; dependency change deferred |
| No live Alpaca adapter; only paper adapter exists | Out of v4 scope; separate hardening project |
| HTMX usage is light; v3 implies more pervasive HTMX | Documented; do not over-build HTMX in refactor |
| `default_paper` campaign auto-creation backfills orphan strategies; UI presentation needs care | Stage 4 handles UI presentation |

---

*End of Project HERON v4 canonical spec.*
